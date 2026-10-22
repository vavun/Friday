"""Етап 6: Помилка з буфера обміну -> евристика / LLM -> відкриття документації на моніторі docs без крадіжки фокусу."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import webbrowser
from typing import Any, Callable

from pydantic import BaseModel

from friday.skills.typing_text import ClipboardBackend, PyperclipClipboardBackend
from friday.skills.web import DEFAULT_DOCS_SITES, build_google_search_url
from friday.skills.windows import WindowManager
from friday.util.timing import measure_time

log = logging.getLogger("friday.skills.docs_lookup")

_EXCEPTION_LINE_RE = re.compile(
    r"^\s*([A-Za-z_][A-Za-z0-9_.]*(?:Error|Exception|Warning))(?:\s*:\s*(.*))?$"
)
_INLINE_EXCEPTION_RE = re.compile(
    r"\b([A-Za-z_][A-Za-z0-9_.]*(?:Error|Exception|Warning))(?:\s*:\s*([^\n]+))?"
)


class ParsedTracebackInfo(BaseModel):
    """Результат розбору помилки або traceback (евристикою або LLM)."""

    library: str = "python"
    error: str = ""
    query: str
    used_llm: bool = False


def trim_traceback_text(
    raw_text: str,
    max_tail_lines: int = 40,
    max_chars: int = 3500,
) -> str:
    """Обрізає вміст буфера обміну до першого рядка помилки + останніх ~40 рядків traceback."""
    cleaned = (raw_text or "").strip()
    if not cleaned:
        return ""
    lines = [ln.rstrip() for ln in cleaned.splitlines() if ln.strip()]
    if not lines:
        return ""
    if len(lines) > max_tail_lines + 1:
        selected = [lines[0], *lines[-max_tail_lines:]]
    else:
        selected = lines
    joined = "\n".join(selected)
    if len(joined) > max_chars:
        joined = joined[:200] + "\n...\n" + joined[-(max_chars - 210) :]
    return joined


def detect_library_from_text(text: str) -> str:
    """Визначає цільову бібліотеку документації за ключовими словами у traceback."""
    lower = text.lower()
    if any(k in lower for k in ("fastapi", "starlette", "apirouter", "httpexception", "depends(")):
        return "fastapi"
    if any(
        k in lower
        for k in (
            "pydantic",
            "validationerror",
            "basemodel",
            "field_validator",
            "errors.pydantic.dev",
        )
    ):
        return "pydantic"
    if any(
        k in lower
        for k in (
            "cannot read properties of undefined",
            "referenceerror:",
            "uncaught typeerror",
            "document.queryselector",
            "window.addeventlistener",
        )
    ):
        return "mdn"
    if "python" in lower or "traceback (most recent call last)" in lower or 'file "' in lower:
        return "python"
    return "python"


def parse_traceback_heuristic(raw_text: str) -> ParsedTracebackInfo | None:
    """Швидка детермінована евристика без ШІ для розпізнавання типу помилки та бібліотеки (< 1 мс)."""
    trimmed = trim_traceback_text(raw_text)
    if not trimmed:
        return None

    lines = [ln.strip() for ln in trimmed.splitlines() if ln.strip()]
    library = detect_library_from_text(trimmed)

    # 1. Перевірка специфічного формату Pydantic v2 ValidationError
    if "validationerror" in trimmed.lower() and "pydantic" in trimmed.lower():
        first_err_line = next(
            (ln for ln in reversed(lines) if "validation error" in ln.lower() or "type=" in ln.lower()),
            "ValidationError",
        )
        short_detail = re.sub(r"\s+", " ", first_err_line)[:90]
        return ParsedTracebackInfo(
            library="pydantic",
            error="ValidationError",
            query=f"pydantic ValidationError {short_detail}".strip(),
            used_llm=False,
        )

    # 2. Пошук класичного рядка винятку з кінця traceback (напр. TypeError: ..., ModuleNotFoundError: ...)
    for line in reversed(lines):
        m = _EXCEPTION_LINE_RE.match(line)
        if m:
            err_name = m.group(1).split(".")[-1]
            err_msg = (m.group(2) or "").strip()
            # Прибираємо локальні шляхи в лапках, щоб запит до документації був загальним
            clean_msg = re.sub(r"[A-Za-z]:\\[^\s'\"]+", "", err_msg)
            clean_msg = re.sub(r"\s+", " ", clean_msg).strip()[:85]
            query = f"{library} {err_name} {clean_msg}".strip()
            return ParsedTracebackInfo(
                library=library,
                error=err_name,
                query=query,
                used_llm=False,
            )

    # 3. Якщо виняток згадано всередині рядка
    m_inline = _INLINE_EXCEPTION_RE.search(trimmed)
    if m_inline:
        err_name = m_inline.group(1).split(".")[-1]
        err_msg = (m_inline.group(2) or "").strip()[:70]
        query = f"{library} {err_name} {err_msg}".strip()
        return ParsedTracebackInfo(
            library=library,
            error=err_name,
            query=query,
            used_llm=False,
        )

    return None


class DocsLookupService:
    """Відкриває документацію по помилці з буфера обміну на моніторі docs і повертає фокус у редактор."""

    def __init__(
        self,
        window_manager: WindowManager | None = None,
        clipboard: ClipboardBackend | None = None,
        docs_sites: dict[str, str] | None = None,
        docs_monitor_0based: int = 1,
        open_new_window_fn: Callable[[str], Any] | None = None,
        llm_fallback_fn: Callable[[str], dict[str, Any]] | None = None,
        hwnd_poll_timeout_s: float = 0.8,
        hwnd_poll_interval_s: float = 0.05,
    ) -> None:
        self.window_manager = window_manager or WindowManager()
        self.clipboard: ClipboardBackend = clipboard or PyperclipClipboardBackend()
        self.docs_sites = docs_sites or dict(DEFAULT_DOCS_SITES)
        self.docs_monitor_0based = docs_monitor_0based
        self._open_new_window_fn = open_new_window_fn or (lambda url: webbrowser.open(url, new=1))
        self._llm_fallback_fn = llm_fallback_fn
        self.hwnd_poll_timeout_s = hwnd_poll_timeout_s
        self.hwnd_poll_interval_s = hwnd_poll_interval_s

    def analyze_error_text(self, raw_text: str) -> ParsedTracebackInfo:
        """Аналізує текст помилки: спершу евристика, якщо недостатньо — один виклик LLM."""
        trimmed = trim_traceback_text(raw_text)
        if not trimmed:
            raise ValueError("Буфер обміну порожній — скопіюйте текст помилки або traceback.")

        heuristic = parse_traceback_heuristic(trimmed)
        if heuristic is not None:
            return heuristic

        if self._llm_fallback_fn is not None:
            try:
                raw_dict = self._llm_fallback_fn(trimmed)
                lib = str(raw_dict.get("library", "python")).strip().lower() or "python"
                err = str(raw_dict.get("error", "")).strip()
                q = str(raw_dict.get("query", "")).strip() or f"{lib} {err}".strip()
                return ParsedTracebackInfo(library=lib, error=err, query=q, used_llm=True)
            except Exception as exc:
                log.warning("Не вдалося виконати LLM-аналіз traceback: %s", exc)

        # Безпечний фолбек: беремо перший інформативний рядок без локальних шляхів
        first_line = trimmed.splitlines()[-1].strip()[:90]
        return ParsedTracebackInfo(
            library=detect_library_from_text(trimmed),
            error="Error",
            query=first_line,
            used_llm=False,
        )

    def lookup_sync(self, args: dict[str, Any] | None = None) -> dict[str, Any]:
        """Виконує повний цикл: буфер -> запит -> відкриття на моніторі docs -> повернення фокусу."""
        params = args or {}
        with measure_time("docs.lookup"):
            raw_source = str(params.get("query") or params.get("error") or "").strip()
            if not raw_source or params.get("source") == "clipboard":
                clip_text = self.clipboard.get_text()
                if clip_text.strip():
                    raw_source = clip_text
                elif not raw_source:
                    raise ValueError("Буфер обміну порожній — скопіюйте помилку перед викликом документації.")

            info = self.analyze_error_text(raw_source)
            url = build_google_search_url(info.query, site=info.library, docs_sites=self.docs_sites)

            # 1. Запам'ятовуємо активне вікно редактора та поточний набір відкритих HWND
            previous_fg_hwnd = self.window_manager.backend.get_foreground_window()
            before_hwnds = {w.hwnd for w in self.window_manager.backend.list_visible_windows()}

            # 2. Відкриваємо нове вікно браузера
            self._open_new_window_fn(url)

            # 3. Очікуємо появу нового HWND браузера (або знаходимо поточний браузер)
            target_browser_hwnd: int | None = None
            deadline = time.perf_counter() + self.hwnd_poll_timeout_s
            while time.perf_counter() < deadline:
                after_windows = self.window_manager.backend.list_visible_windows()
                new_windows = [w for w in after_windows if w.hwnd not in before_hwnds]
                if new_windows:
                    target_browser_hwnd = new_windows[0].hwnd
                    break
                if self.hwnd_poll_interval_s > 0:
                    time.sleep(self.hwnd_poll_interval_s)
                else:
                    break

            if target_browser_hwnd is None:
                try:
                    target_browser_hwnd = self.window_manager.find_window("browser")
                except LookupError:
                    target_browser_hwnd = None

            # 4. Переміщуємо вікно документації на монітор docs
            monitors = self.window_manager.backend.get_monitors()
            target_mon_0based = min(max(0, self.docs_monitor_0based), max(0, len(monitors) - 1))
            moved = False
            if target_browser_hwnd is not None and monitors:
                try:
                    self.window_manager.move_to_monitor(
                        target_browser_hwnd,
                        monitor_idx=target_mon_0based,
                        mode="maximize",
                    )
                    moved = True
                except Exception as exc:
                    log.warning("Не вдалося перемістити вікно браузера на монітор docs: %s", exc)

            # 5. Повертаємо фокус у попереднє вікно (редактор), щоб не красти фокус!
            focus_restored = False
            if previous_fg_hwnd and self.window_manager.backend.is_window(previous_fg_hwnd):
                try:
                    focus_res = self.window_manager.focus(previous_fg_hwnd)
                    focus_restored = bool(focus_res.get("focused", False))
                except Exception as exc:
                    log.warning("Не вдалося повернути фокус попередньому вікну: %s", exc)

            log.info(
                "📚 Документація відкрита (lib=%s, err=%s, mon=%d, focus_restored=%s)",
                info.library,
                info.error,
                target_mon_0based,
                focus_restored,
            )
            return {
                "url": url,
                "library": info.library,
                "error": info.error,
                "query": info.query,
                "used_llm": info.used_llm,
                "browser_hwnd": target_browser_hwnd,
                "moved_to_docs_monitor": moved,
                "focus_restored": focus_restored,
                "previous_hwnd": previous_fg_hwnd,
            }


class DocsLookupSkill:
    """Навичка docs.lookup."""

    name = "docs.lookup"

    def __init__(self, service: DocsLookupService | None = None) -> None:
        self.service = service or DocsLookupService()

    async def execute(self, args: dict[str, Any]) -> dict[str, Any]:
        return await asyncio.to_thread(self.service.lookup_sync, args)

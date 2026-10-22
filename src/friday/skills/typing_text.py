"""Вставка тексту в активне вікно через буфер обміну + Ctrl+V зі збереженням і відновленням буфера."""

from __future__ import annotations

import asyncio
import ctypes
import logging
import time
from typing import Any, Callable, Protocol

import pyperclip

from friday.util.timing import measure_time

log = logging.getLogger("friday.skills.typing_text")

VK_CONTROL = 0x11
VK_RCONTROL = 0xA3
VK_V = 0x56
KEYEVENTF_KEYUP = 0x0002


class ClipboardBackend(Protocol):
    """Протокол роботи з системним буфером обміну."""

    def get_text(self) -> str:
        ...

    def set_text(self, value: str) -> None:
        ...


class PyperclipClipboardBackend:
    """Реальний бекенд буфера обміну через pyperclip."""

    def get_text(self) -> str:
        try:
            return str(pyperclip.paste() or "")
        except Exception:
            return ""

    def set_text(self, value: str) -> None:
        pyperclip.copy(value)


def send_win32_ctrl_v() -> None:
    """Надсилає комбінацію клавіш Ctrl+V в активне вікно через Win32 keybd_event."""
    user32 = ctypes.windll.user32
    # На випадок, якщо правий Ctrl (хоткей голосу) ще логічно затиснутий — відпускаємо його
    user32.keybd_event(VK_RCONTROL, 0, KEYEVENTF_KEYUP, 0)
    user32.keybd_event(VK_CONTROL, 0, 0, 0)
    user32.keybd_event(VK_V, 0, 0, 0)
    time.sleep(0.01)
    user32.keybd_event(VK_V, 0, KEYEVENTF_KEYUP, 0)
    user32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)


class TextTyper:
    """Вставляє кириличний або технічний текст через буфер обміну + Ctrl+V та відновлює попередній буфер."""

    def __init__(
        self,
        clipboard: ClipboardBackend | None = None,
        paste_fn: Callable[[], None] | None = None,
        pre_paste_delay_s: float = 0.02,
        restore_delay_s: float = 0.06,
    ) -> None:
        self.clipboard: ClipboardBackend = clipboard or PyperclipClipboardBackend()
        self._paste_fn = paste_fn or send_win32_ctrl_v
        self.pre_paste_delay_s = pre_paste_delay_s
        self.restore_delay_s = restore_delay_s

    def type_text_sync(self, text: str) -> dict[str, Any]:
        """Синхронно копіює текст у буфер, натискає Ctrl+V і відновлює попередній вміст буфера."""
        cleaned = (text or "").strip()
        if not cleaned:
            raise ValueError("Текст для вставки не може бути порожнім")

        with measure_time("text.type"):
            previous_clipboard = self.clipboard.get_text()
            try:
                self.clipboard.set_text(cleaned)
                if self.pre_paste_delay_s > 0:
                    time.sleep(self.pre_paste_delay_s)
                self._paste_fn()
                if self.restore_delay_s > 0:
                    time.sleep(self.restore_delay_s)
            finally:
                try:
                    self.clipboard.set_text(previous_clipboard)
                except Exception as exc:
                    log.warning("Не вдалося відновити попередній вміст буфера обміну: %s", exc)

            log.info("⌨️ Вставлено текст у активне вікно (довжина=%d символів)", len(cleaned))
            return {"status": "typed", "length": len(cleaned)}


class TextTypeSkill:
    """Навичка text.type."""

    name = "text.type"

    def __init__(self, typer: TextTyper | None = None) -> None:
        self.typer = typer or TextTyper()

    async def execute(self, args: dict[str, Any]) -> dict[str, Any]:
        text = str(args.get("text", ""))
        return await asyncio.to_thread(self.typer.type_text_sync, text)

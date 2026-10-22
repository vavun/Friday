"""Комплексні юніт- та інтеграційні тести для Етапів 5, 6, 7, 8 та 9."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from friday.app import AssistantState, FridayApp
from friday.autostart import disable_autostart, enable_autostart, is_autostart_enabled
from friday.config import AgentConfig, AllowedFolderEntry, AppConfig
from friday.hotkeys import HotkeyEvent, HotkeyEventType
from friday.safety import (
    build_confirmation_prompt,
    is_dangerous_command,
    validate_intent_safety,
)
from friday.skills.agent_cli import (
    AgentCliService,
    AgentRunSkill,
    build_agent_cli_command,
    resolve_project_workdir,
)
from friday.skills.base import SkillRegistry
from friday.skills.docs_lookup import (
    DocsLookupService,
    DocsLookupSkill,
    detect_library_from_text,
    parse_traceback_heuristic,
    trim_traceback_text,
)
from friday.skills.telegram import (
    TelegramDialogEntry,
    TelegramSendSkill,
    TelegramService,
    format_telegram_confirm_prompt,
    resolve_contact_fuzzy,
)
from friday.skills.typing_text import TextTyper, TextTypeSkill
from friday.skills.web import WebBrowserService, WebSearchSkill, build_google_search_url
from friday.skills.windows import WindowEntry, WindowManager
from friday.util.monitors import MonitorInfo, Rect
from friday.util.timing import tracker


class _MemoryClipboard:
    """Тестовий буфер обміну в пам'яті."""

    def __init__(self, initial: str = "INITIAL_CLIPBOARD_SECRET") -> None:
        self.value = initial
        self.history: list[str] = []

    def get_text(self) -> str:
        return self.value

    def set_text(self, val: str) -> None:
        self.value = val
        self.history.append(val)


# =====================================================================
# ЕТАП 5: Введення тексту (Ctrl+V) та вебпошук (Google + site:)
# =====================================================================


@pytest.mark.asyncio
async def test_stage5_text_typer_preserves_clipboard_and_pastes_cyrillic() -> None:
    """Перевіряє вставку кирилиці через буфер обміну + Ctrl+V та відновлення попереднього буфера."""
    clip = _MemoryClipboard("попередній текст у буфері")
    pasted_snapshots: list[str] = []

    def fake_paste() -> None:
        pasted_snapshots.append(clip.get_text())

    typer = TextTyper(
        clipboard=clip,
        paste_fn=fake_paste,
        pre_paste_delay_s=0.0,
        restore_delay_s=0.0,
    )
    skill = TextTypeSkill(typer=typer)
    res = await skill.execute({"text": "привіт, зустрінемось о шостій!"})

    assert res["status"] == "typed"
    assert pasted_snapshots == ["привіт, зустрінемось о шостій!"]
    # Попередній вміст буфера обміну обов'язково відновлено
    assert clip.get_text() == "попередній текст у буфері"


@pytest.mark.asyncio
async def test_stage5_web_search_and_docs_sites() -> None:
    """Перевіряє формування URL пошуку з site: та відкриття відомих сайтів."""
    opened_urls: list[str] = []
    svc = WebBrowserService(open_url_fn=lambda u: opened_urls.append(u))
    skill = WebSearchSkill(service=svc)

    url_fastapi = build_google_search_url("як зробити middleware", site="fastapi")
    assert "site%3Afastapi.tiangolo.com" in url_fastapi

    await skill.execute({"query": "BaseModel validator", "site": "pydantic"})
    assert len(opened_urls) == 1
    assert "docs.pydantic.dev" in opened_urls[0]

    svc.open_site_sync("python")
    assert opened_urls[-1] == "https://docs.python.org"


# =====================================================================
# ЕТАП 6: Помилка з буфера -> документація на моніторі docs без крадіжки фокусу
# =====================================================================


class _FakeDocsWin32Backend:
    """Заглушка Win32 для перевірки відкриття нового вікна браузера, перенесення на монітор docs і повернення фокусу."""

    def __init__(self, monitors: list[MonitorInfo]) -> None:
        self.monitors = monitors
        self.foreground_hwnd = 101  # Активний редактор коду (VS Code)
        self.windows: list[WindowEntry] = [
            WindowEntry(
                hwnd=101,
                title="Friday — Visual Studio Code",
                process_name="code.exe",
            )
        ]
        self.moved_calls: list[tuple[int, int, int, int, int]] = []
        self.focused_hwnds: list[int] = []

    def list_visible_windows(self) -> list[WindowEntry]:
        return list(self.windows)

    def get_monitors(self) -> list[MonitorInfo]:
        return list(self.monitors)

    def is_window(self, hwnd: int) -> bool:
        return any(w.hwnd == hwnd for w in self.windows)

    def get_foreground_window(self) -> int:
        return self.foreground_hwnd

    def get_window_rect(self, hwnd: int) -> Rect:
        return Rect(0, 0, 1200, 800)

    def get_frame_margins(self, hwnd: int) -> tuple[int, int, int, int]:
        return (0, 0, 0, 0)

    def get_window_monitor_index(self, hwnd: int, monitors: list[MonitorInfo]) -> int:
        return 0

    def show_window(self, hwnd: int, cmd: int) -> None:
        pass

    def set_window_pos(self, hwnd: int, x: int, y: int, w: int, h: int) -> None:
        self.moved_calls.append((hwnd, x, y, w, h))

    def set_foreground(self, hwnd: int) -> bool:
        self.foreground_hwnd = hwnd
        self.focused_hwnds.append(hwnd)
        return True

    def get_window_at_point(self, x: int, y: int) -> int:
        for w in self.windows:
            r = self.get_window_rect(w.hwnd)
            if r.left <= x <= r.right and r.top <= y <= r.bottom:
                return w.hwnd
        return 0

    def close_window(self, hwnd: int) -> bool:
        self.windows = [w for w in self.windows if w.hwnd != hwnd]
        return True

    def get_window_class(self, hwnd: int) -> str:
        return "Chrome_WidgetWin_1"

    def is_window_elevated(self, hwnd: int) -> bool:
        return False


@pytest.mark.asyncio
async def test_stage6_docs_lookup_heuristic_move_and_focus_preservation() -> None:
    """Перевіряє обрізання traceback, евристику без ШІ, переміщення на монітор docs і повернення фокусу в редактор."""
    long_tb = "\n".join(
        [
            "Traceback (most recent call last):",
            *[f'  File "C:\\app\\module_{i}.py", line {i}, in run' for i in range(60)],
            '  File "C:\\venv\\Lib\\site-packages\\fastapi\\routing.py", line 212, in app',
            "TypeError: object dict can't be used in 'await' expression",
        ]
    )
    trimmed = trim_traceback_text(long_tb, max_tail_lines=40)
    assert len(trimmed.splitlines()) <= 41

    parsed = parse_traceback_heuristic(trimmed)
    assert parsed is not None
    assert parsed.library == "fastapi"
    assert parsed.error == "TypeError"
    assert parsed.used_llm is False
    assert detect_library_from_text("APIRouter instance error in routes") == "fastapi"

    monitors = [
        MonitorInfo(index=0, handle=1, device="M1", is_primary=True, monitor_rect=Rect(0, 0, 1920, 1080), work_area=Rect(0, 0, 1920, 1040)),
        MonitorInfo(index=1, handle=2, device="M2", is_primary=False, monitor_rect=Rect(1920, 0, 3840, 1080), work_area=Rect(1920, 0, 3840, 1040)),
    ]
    backend = _FakeDocsWin32Backend(monitors=monitors)
    wm = WindowManager(backend=backend)
    clip = _MemoryClipboard(long_tb)

    def fake_open_new_browser(url: str) -> None:
        # Імітуємо появу нового вікна браузера (HWND 505), яке тимчасово перехопило фокус
        backend.windows.append(
            WindowEntry(
                hwnd=505,
                title=f"Google Search - {url} - Google Chrome",
                process_name="chrome.exe",
            )
        )
        backend.foreground_hwnd = 505

    svc = DocsLookupService(
        window_manager=wm,
        clipboard=clip,
        docs_monitor_0based=1,
        open_new_window_fn=fake_open_new_browser,
        hwnd_poll_timeout_s=0.1,
        hwnd_poll_interval_s=0.0,
    )
    skill = DocsLookupSkill(service=svc)
    res = await skill.execute({"source": "clipboard"})

    assert res["library"] == "fastapi"
    assert res["error"] == "TypeError"
    assert res["browser_hwnd"] == 505
    assert res["moved_to_docs_monitor"] is True
    assert res["focus_restored"] is True
    # Перевіряємо, що вікно 505 переміщено на другий монітор (x >= 1920), а фокус повернувся у редактор (HWND 101)
    assert any(call[0] == 505 and call[1] >= 1920 for call in backend.moved_calls)
    assert backend.foreground_hwnd == 101


# =====================================================================
# ЕТАП 7: Telegram (нечіткий пошук контакту + кеш діалогів + Confirm Gate)
# =====================================================================


@pytest.mark.asyncio
async def test_stage7_telegram_fuzzy_contact_and_mandatory_confirm_gate() -> None:
    """Перевіряє розв'язання українських імен у давальному відмінку та блокування без підтвердження."""
    dialogs = [
        TelegramDialogEntry(entity_id=1001, title="Олег Коваленко", username="oleg_k"),
        TelegramDialogEntry(entity_id=1002, title="Марина Dev", username="maryna_dev"),
        TelegramDialogEntry(entity_id=1003, title="Робочий Чат", username="work_chat"),
    ]

    matched_oleg = resolve_contact_fuzzy("Олегу", dialogs)
    assert matched_oleg.entity_id == 1001

    matched_maryna = resolve_contact_fuzzy("Марині", dialogs)
    assert matched_maryna.entity_id == 1002

    prompt_str = format_telegram_confirm_prompt("Олегу", "я запізнюсь")
    assert prompt_str == 'Олегу: "я запізнюсь" — відправити? (Enter / Esc)'

    sent_messages: list[tuple[int | str, str]] = []
    fetch_count = 0

    async def fake_fetcher() -> list[TelegramDialogEntry]:
        nonlocal fetch_count
        fetch_count += 1
        return dialogs

    async def fake_sender(entity_id: int | str, text: str) -> None:
        sent_messages.append((entity_id, text))

    tg_service = TelegramService(
        dialogs_fetcher=fake_fetcher,
        message_sender=fake_sender,
    )
    tg_skill = TelegramSendSkill(service=tg_service)

    # 1. Без підтвердження (_confirmed=False) відправка суворо заблокована!
    with pytest.raises(PermissionError, match="Confirm Gate"):
        await tg_skill.execute({"contact": "Олегу", "text": "тест без підтвердження"})
    assert len(sent_messages) == 0

    # 2. З підтвердженням (_confirmed=True) повідомлення надсилається правильному контакту
    res = await tg_skill.execute({"contact": "Олегу", "text": "зустрінемось о 18:00", "_confirmed": True})
    assert res["status"] == "sent"
    assert res["entity_id"] == 1001
    assert sent_messages == [(1001, "зустрінемось о 18:00")]

    # Повторний виклик використовує кеш діалогів (fetch_count залишається 1)
    await tg_skill.execute({"contact": "Марині", "text": "привіт", "_confirmed": True})
    assert fetch_count == 1
    assert len(sent_messages) == 2


# =====================================================================
# ЕТАП 8: Інтеграція Antigravity CLI (реальні прапорці agy, без auto-approve)
# =====================================================================


@pytest.mark.asyncio
async def test_stage8_agent_cli_workdir_flags_and_confirm_gate(tmp_path: Path) -> None:
    """Перевіряє формування команди Antigravity CLI (-i), вибір робочої папки та заборону auto-approve."""
    proj_a = tmp_path / "AlphaService"
    proj_b = tmp_path / "FridayVoice"
    proj_a.mkdir()
    proj_b.mkdir()

    folders = [
        AllowedFolderEntry(label="Alpha", path=str(proj_a), mode="read_write"),
        AllowedFolderEntry(label="FridayVoice", path=str(proj_b), mode="read_write"),
    ]

    # Визначає активний проєкт за заголовком вікна редактора
    resolved_dir = resolve_project_workdir(
        allowed_folders=folders,
        editor_window_title="config.py - FridayVoice - Visual Studio Code",
    )
    assert resolved_dir == proj_b.resolve()

    # Команда використовує реальний прапорець `-i` з `agy --help` і не містить auto-approve
    cmd = build_agent_cli_command("відрефактори файл config.py", executable="agy", interactive=True)
    assert cmd == ["agy", "-i", "відрефактори файл config.py"]

    # Деструктивні промпти блокуються
    with pytest.raises(PermissionError, match="safety.py"):
        build_agent_cli_command("виконай git reset --hard і rm -rf /", executable="agy")

    spawned: list[tuple[list[str], str]] = []
    svc = AgentCliService(
        agent_config=AgentConfig(command="agy", auto_approve=False),
        allowed_folders=folders,
        popen_fn=lambda c, cwd, **kw: spawned.append((c, cwd)),
    )
    skill = AgentRunSkill(service=svc)

    # Без підтвердження запуск заблоковано
    with pytest.raises(PermissionError, match="Confirm Gate"):
        await skill.execute({"prompt": "відрефактори файл config.py"})
    assert len(spawned) == 0

    # Після підтвердження відкриває сесію у робочій папці
    res = await skill.execute({"prompt": "відрефактори файл config.py", "_confirmed": True})
    assert res["status"] == "started"
    assert len(spawned) == 1
    assert spawned[0][0][1] == "-i"


# =====================================================================
# ЕТАП 9: Безпека, ізоляція винятків, автозапуск та профілювання
# =====================================================================


@pytest.mark.asyncio
async def test_stage9_safety_autostart_exception_isolation_and_profiling(tmp_path: Path) -> None:
    """Перевіряє фільтр безпеки, автозапуск через pythonw, стійкість циклу до винятків та таблицю затримок."""
    assert is_dangerous_command("git push origin main --force") is True
    with pytest.raises(PermissionError):
        validate_intent_safety("agent.run", {"prompt": "зроби git reset --hard"})

    assert build_confirmation_prompt("telegram.send", {"contact": "олег", "text": "тест"}).startswith('Олег: "тест"')

    # Тест автозапуску у tmp_path
    startup_dir = tmp_path / "Startup"
    script_file = enable_autostart(startup_dir=startup_dir, project_root=tmp_path)
    assert script_file.exists()
    assert is_autostart_enabled(startup_dir=startup_dir) is True
    assert "pythonw" in script_file.read_text(encoding="utf-8").lower()
    assert disable_autostart(startup_dir=startup_dir) is True
    assert is_autostart_enabled(startup_dir=startup_dir) is False

    # Перевірка, що помилка всередині навички не валить основний цикл FridayApp і повертає у IDLE
    class _FailingSkill:
        name = "web.search"

        async def execute(self, args: dict[str, Any]) -> dict[str, Any]:
            raise RuntimeError("Імітована помилка браузера")

    reg = SkillRegistry()
    reg.register(_FailingSkill())
    app = FridayApp(config=AppConfig(), registry=reg)

    await app._route_and_dispatch("знайди в гуглі тест стійкості")
    assert app.state == AssistantState.IDLE

    # Перевірка таблиці профілювання затримок
    table_str = tracker.format_summary_table()
    assert "Етап" in table_str
    assert "router.fast_path" in table_str

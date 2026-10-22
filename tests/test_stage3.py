"""Юніт-тести для Етапу 3: менеджер вікон, монітори, DWM-рамки, кеш HWND, білий список програм та CLI."""

from __future__ import annotations

import time
from typing import Any

import pytest
import win32con

from friday.dev import build_parser
from friday.router.schema import CommandIntent
from friday.skills.apps import AppLaunchSkill, AppLauncher
from friday.skills.base import SkillRegistry
from friday.skills.windows import (
    WindowEntry,
    WindowFocusSkill,
    WindowManager,
    WindowMoveSkill,
    WindowResizeSkill,
)
from friday.util.monitors import MonitorInfo, Rect, enable_dpi_awareness, list_monitors


class FakeWin32Backend:
    """Ізольований фейковий бекенд Win32 для детермінованого тестування логіки вікон на 2 моніторах."""

    def __init__(self) -> None:
        self.windows: dict[int, WindowEntry] = {
            101: WindowEntry(hwnd=101, title="Google Chrome", process_name="chrome.exe"),
            202: WindowEntry(hwnd=202, title="Friday - Visual Studio Code", process_name="code.exe"),
            303: WindowEntry(hwnd=303, title="Telegram", process_name="telegram.exe"),
        }
        self.valid_hwnds: set[int] = {101, 202, 303}
        self.foreground_hwnd: int = 202
        self.rects: dict[int, Rect] = {
            101: Rect(100, 100, 900, 700),
            202: Rect(0, 0, 1920, 1040),
            303: Rect(200, 200, 1000, 800),
        }
        self.margins: tuple[int, int, int, int] = (7, 0, 7, 7)  # типові DWM-рамки Windows 10/11
        self.monitors: list[MonitorInfo] = [
            MonitorInfo(
                index=0,
                handle=1,
                device="\\\\.\\DISPLAY1",
                is_primary=True,
                monitor_rect=Rect(0, 0, 1920, 1080),
                work_area=Rect(0, 0, 1920, 1040),
            ),
            MonitorInfo(
                index=1,
                handle=2,
                device="\\\\.\\DISPLAY2",
                is_primary=False,
                monitor_rect=Rect(1920, 0, 3840, 1080),
                work_area=Rect(1920, 0, 3840, 1040),
            ),
        ]
        self.show_calls: list[tuple[int, int]] = []
        self.pos_calls: list[tuple[int, int, int, int, int]] = []
        self.enum_count: int = 0

    def is_window(self, hwnd: int) -> bool:
        return hwnd in self.valid_hwnds

    def get_foreground_window(self) -> int:
        return self.foreground_hwnd

    def get_window_class(self, hwnd: int) -> str:
        return "Chrome_WidgetWin_1"

    def is_window_elevated(self, hwnd: int) -> bool:
        return False

    def list_visible_windows(self) -> list[WindowEntry]:
        self.enum_count += 1
        return list(self.windows.values())

    def get_monitors(self) -> list[MonitorInfo]:
        return self.monitors

    def get_window_rect(self, hwnd: int) -> Rect:
        return self.rects[hwnd]

    def get_frame_margins(self, hwnd: int) -> tuple[int, int, int, int]:
        return self.margins

    def get_window_monitor_index(self, hwnd: int, monitors: list[MonitorInfo]) -> int:
        r = self.rects[hwnd]
        return 1 if r.left >= 1920 else 0

    def show_window(self, hwnd: int, cmd: int) -> None:
        self.show_calls.append((hwnd, cmd))

    def set_window_pos(self, hwnd: int, x: int, y: int, w: int, h: int) -> None:
        self.pos_calls.append((hwnd, x, y, w, h))
        self.rects[hwnd] = Rect(x, y, x + w, y + h)

    def set_foreground(self, hwnd: int) -> bool:
        if hwnd not in self.valid_hwnds:
            return False
        self.foreground_hwnd = hwnd
        return True


def test_find_window_and_hwnd_cache_invalidation() -> None:
    """Перевіряє пошук вікна за аліасом, кешування HWND та інвалідацію через IsWindow."""
    backend = FakeWin32Backend()
    wm = WindowManager(backend=backend)

    hwnd1 = wm.find_window("browser")
    assert hwnd1 == 101
    assert backend.enum_count == 1

    # Повторний пошук бере HWND із кешу без EnumWindows
    hwnd2 = wm.find_window("browser")
    assert hwnd2 == 101
    assert backend.enum_count == 1

    # Якщо старе вікно закрилося (IsWindow == False), кеш оновлюється
    backend.valid_hwnds.remove(101)
    backend.windows.pop(101)
    backend.windows[404] = WindowEntry(hwnd=404, title="Brave", process_name="brave.exe")
    backend.valid_hwnds.add(404)

    hwnd3 = wm.find_window("browser")
    assert hwnd3 == 404
    assert backend.enum_count == 2


def test_move_to_second_monitor_maximize_and_latency() -> None:
    """Перевіряє переміщення браузера на 2-й монітор (SW_RESTORE -> SetWindowPos -> SW_MAXIMIZE) за < 100 мс."""
    backend = FakeWin32Backend()
    wm = WindowManager(backend=backend)

    t0 = time.perf_counter()
    hwnd = wm.find_window("browser")
    res = wm.move_to_monitor(hwnd, monitor_idx=2, mode="maximize")
    elapsed_ms = (time.perf_counter() - t0) * 1000.0

    assert elapsed_ms < 100.0
    assert res["monitor"] == 1  # 2-й монітор має 0-based індекс 1
    assert backend.show_calls == [
        (101, win32con.SW_RESTORE),
        (101, win32con.SW_MAXIMIZE),
    ]
    # Перевіряємо, що SetWindowPos переніс вікно у координати другого монітора (>= 1920)
    assert len(backend.pos_calls) == 1
    _, x, y, _w, _h = backend.pos_calls[0]
    assert x >= 1920
    assert y >= 0


def test_snap_left_right_with_dwm_frame_compensation() -> None:
    """Перевіряє снап ліворуч/праворуч з урахуванням невидимих рамок DWM (7px)."""
    backend = FakeWin32Backend()
    wm = WindowManager(backend=backend)

    res = wm.snap(202, "left")
    # Робоча область: (0, 0, 1920, 1040). Половина ширини = 960.
    # З компенсацією рамок (7, 0, 7, 7): x = -7, y = 0, w = 960 + 14 = 974, h = 1040 + 7 = 1047
    assert res["bounds"] == (-7, 0, 974, 1047)


def test_resize_bigger_and_smaller() -> None:
    """Перевіряє збільшення та зменшення вікна відносно work area."""
    backend = FakeWin32Backend()
    wm = WindowManager(backend=backend)

    before = backend.get_window_rect(101)
    wm.resize(101, "bigger")
    after_bigger = backend.get_window_rect(101)
    assert after_bigger.width > before.width
    assert after_bigger.height > before.height

    wm.resize(101, "smaller")
    after_smaller = backend.get_window_rect(101)
    assert after_smaller.width < after_bigger.width


def test_app_launcher_whitelist_enforcement() -> None:
    """Перевіряє, що AppLauncher запускає лише дозволені програми і блокує сторонні."""
    launched: list[tuple[list[str], bool]] = []

    def fake_popen(args: list[str], shell: bool = False) -> Any:
        launched.append((args, shell))

    launcher = AppLauncher(
        allowed_apps={"code": "code", "browser": "chrome"},
        popen_fn=fake_popen,
    )
    res = launcher.launch_sync("хром")
    assert res["app"] == "browser"
    assert launched[0][1] is False  # shell=False обов'язково

    with pytest.raises(PermissionError):
        launcher.launch_sync("calc.exe")


async def test_skill_registry_executes_window_and_app_skills() -> None:
    """Перевіряє інтеграцію навичок вікон і програм через SkillRegistry."""
    backend = FakeWin32Backend()
    wm = WindowManager(backend=backend)
    launcher = AppLauncher(allowed_apps={"telegram": "telegram.exe"}, popen_fn=lambda a, shell=False: None)

    registry = SkillRegistry()
    registry.register(WindowMoveSkill(wm))
    registry.register(WindowResizeSkill(wm))
    registry.register(WindowFocusSkill(wm))
    registry.register(AppLaunchSkill(launcher))

    move_res = await registry.execute(
        CommandIntent(skill="window.move", args={"target": "browser", "monitor": 2, "mode": "maximize"})
    )
    assert move_res["monitor"] == 1

    focus_res = await registry.execute(
        CommandIntent(skill="window.focus", args={"target": "telegram"})
    )
    assert focus_res["focused"] is True
    assert backend.foreground_hwnd == 303

    app_res = await registry.execute(
        CommandIntent(skill="app.launch", args={"name": "телеграм"})
    )
    assert app_res["app"] == "telegram"


def test_real_monitors_and_dev_cli_parser() -> None:
    """Перевіряє роботу реального list_monitors() на цій машині та парсер friday.dev."""
    assert enable_dpi_awareness() is True
    monitors = list_monitors()
    assert len(monitors) >= 1
    assert monitors[0].work_area.width > 0

    parser = build_parser()
    parsed = parser.parse_args(["window", "move", "browser", "2", "maximize"])
    assert parsed.group == "window"
    assert parsed.action == "move"
    assert parsed.target == "browser"
    assert parsed.monitor == 2
    assert parsed.mode == "maximize"

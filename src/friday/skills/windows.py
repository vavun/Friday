"""Керування вікнами Windows: пошук, переміщення між моніторами, снап, зміна розміру, фокус."""

from __future__ import annotations

import asyncio
import ctypes
from ctypes import wintypes
import logging
from dataclasses import dataclass
from typing import Any, Literal, Protocol

import win32api
import win32con
import win32gui
import win32process

from friday.util.monitors import MonitorInfo, Rect, enable_dpi_awareness, list_monitors
from friday.util.timing import measure_time

log = logging.getLogger("friday.skills.windows")

DWMWA_EXTENDED_FRAME_BOUNDS = 9

# Класи системних вікон (робочий стіл, панель завдань), які заборонено переміщувати жестами
PROTECTED_WINDOW_CLASSES: frozenset[str] = frozenset({
    "Progman",
    "WorkerW",
    "Shell_TrayWnd",
    "Shell_SecondaryTrayWnd",
    "DV2ControlHost",
    "Windows.UI.Core.CoreWindow",
})

MoveMode = Literal["maximize", "left", "right", "center"]
ResizeMode = Literal["bigger", "smaller", "maximize", "restore"]

# Словник синонімів цільових вікон -> патерни у назві процесу або заголовку вікна
TARGET_ALIASES: dict[str, tuple[str, ...]] = {
    "browser": ("chrome", "msedge", "edge", "firefox", "brave", "opera", "browser", "avastbrowser", "vivaldi"),
    "chrome": ("chrome",),
    "code": ("code", "visual studio code"),
    "vscode": ("code", "visual studio code"),
    "editor": ("code", "visual studio code", "pycharm", "cursor"),
    "telegram": ("telegram",),
    "terminal": ("windowsterminal", "powershell", "cmd", "pwsh", "mintty"),
    "notepad": ("notepad", "блокнот"),
    "explorer": ("explorer",),
}


@dataclass(slots=True, frozen=True)
class WindowEntry:
    """Відомості про верхньорівневе видиме вікно."""

    hwnd: int
    title: str
    process_name: str


class Win32Backend(Protocol):
    """Тонкий шар абстракції над Win32 API для ізольованого юніт-тестування логіки без реальних вікон."""

    def is_window(self, hwnd: int) -> bool: ...
    def get_foreground_window(self) -> int: ...
    def get_window_class(self, hwnd: int) -> str: ...
    def is_window_elevated(self, hwnd: int) -> bool: ...
    def list_visible_windows(self) -> list[WindowEntry]: ...
    def get_monitors(self) -> list[MonitorInfo]: ...
    def get_window_rect(self, hwnd: int) -> Rect: ...
    def get_frame_margins(self, hwnd: int) -> tuple[int, int, int, int]: ...
    def get_window_monitor_index(self, hwnd: int, monitors: list[MonitorInfo]) -> int: ...
    def show_window(self, hwnd: int, cmd: int) -> None: ...
    def set_window_pos(self, hwnd: int, x: int, y: int, w: int, h: int) -> None: ...
    def set_foreground(self, hwnd: int) -> bool: ...


class RealWin32Backend:
    """Реальна реалізація Win32 API через pywin32 та ctypes.windll.dwmapi."""

    def __init__(self) -> None:
        enable_dpi_awareness()

    def is_window(self, hwnd: int) -> bool:
        return bool(hwnd and win32gui.IsWindow(hwnd) and win32gui.IsWindowVisible(hwnd))

    def get_foreground_window(self) -> int:
        return int(win32gui.GetForegroundWindow())

    def get_window_class(self, hwnd: int) -> str:
        try:
            return str(win32gui.GetClassName(hwnd))
        except Exception:
            return ""

    def is_window_elevated(self, hwnd: int) -> bool:
        """Перевіряє, чи запущено процес вікна з правами Адміністратора (якщо доступ блокується — True)."""
        try:
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            if not pid:
                return False
            hproc = win32api.OpenProcess(win32con.PROCESS_QUERY_INFORMATION, False, pid)
            win32api.CloseHandle(hproc)
            return False
        except Exception:
            return True

    def list_visible_windows(self) -> list[WindowEntry]:
        entries: list[WindowEntry] = []

        def _enum_cb(hwnd: int, _extra: Any) -> bool:
            if not win32gui.IsWindowVisible(hwnd):
                return True
            title = win32gui.GetWindowText(hwnd).strip()
            if not title:
                return True
            proc_name = self._get_process_name(hwnd)
            entries.append(WindowEntry(hwnd=hwnd, title=title, process_name=proc_name))
            return True

        win32gui.EnumWindows(_enum_cb, None)
        return entries

    def _get_process_name(self, hwnd: int) -> str:
        try:
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            if not pid:
                return ""
            hproc = win32api.OpenProcess(
                win32con.PROCESS_QUERY_LIMITED_INFORMATION,
                False,
                pid,
            )
            try:
                exe_path = win32process.GetModuleFileNameEx(hproc, 0)
                return exe_path.rsplit("\\", 1)[-1].lower()
            finally:
                win32api.CloseHandle(hproc)
        except Exception:
            return ""

    def get_monitors(self) -> list[MonitorInfo]:
        return list_monitors()

    def get_window_rect(self, hwnd: int) -> Rect:
        l, t, r, b = win32gui.GetWindowRect(hwnd)
        return Rect(l, t, r, b)

    def get_frame_margins(self, hwnd: int) -> tuple[int, int, int, int]:
        """Обчислює невидимі рамки вікна Windows 10/11 через DwmGetWindowAttribute(DWMWA_EXTENDED_FRAME_BOUNDS)."""
        try:
            outer = self.get_window_rect(hwnd)
            frame = wintypes.RECT()
            hr = ctypes.windll.dwmapi.DwmGetWindowAttribute(
                wintypes.HWND(hwnd),
                wintypes.DWORD(DWMWA_EXTENDED_FRAME_BOUNDS),
                ctypes.byref(frame),
                ctypes.sizeof(frame),
            )
            if hr != 0:
                return (0, 0, 0, 0)
            left_pad = max(0, frame.left - outer.left)
            top_pad = max(0, frame.top - outer.top)
            right_pad = max(0, outer.right - frame.right)
            bottom_pad = max(0, outer.bottom - frame.bottom)
            return (left_pad, top_pad, right_pad, bottom_pad)
        except Exception:
            return (0, 0, 0, 0)

    def get_window_monitor_index(self, hwnd: int, monitors: list[MonitorInfo]) -> int:
        if not monitors:
            return 0
        try:
            hmon = int(win32api.MonitorFromWindow(hwnd, win32con.MONITOR_DEFAULTTONEAREST))
            for mon in monitors:
                if mon.handle == hmon:
                    return mon.index
        except Exception:
            pass
        rect = self.get_window_rect(hwnd)
        cx = (rect.left + rect.right) // 2
        cy = (rect.top + rect.bottom) // 2
        for mon in monitors:
            m = mon.monitor_rect
            if m.left <= cx < m.right and m.top <= cy < m.bottom:
                return mon.index
        return 0

    def show_window(self, hwnd: int, cmd: int) -> None:
        try:
            win32gui.ShowWindow(hwnd, cmd)
        except Exception as exc:
            raise PermissionError(
                f"Не вдалося змінити стан вікна (можливо, процес запущено від Адміністратора / UIPI): {exc}"
            ) from exc

    def set_window_pos(self, hwnd: int, x: int, y: int, w: int, h: int) -> None:
        try:
            win32gui.SetWindowPos(
                hwnd,
                0,
                int(x),
                int(y),
                int(w),
                int(h),
                win32con.SWP_NOZORDER | win32con.SWP_NOACTIVATE,
            )
        except Exception as exc:
            raise PermissionError(
                f"Не вдалося перемістити вікно (можливо, процес запущено від Адміністратора / UIPI): {exc}"
            ) from exc

    def set_foreground(self, hwnd: int) -> bool:
        """Надійне переведення вікна на передній план (AttachThreadInput + короткий Alt-трюк)."""
        if not self.is_window(hwnd):
            return False
        try:
            if win32gui.IsIconic(hwnd):
                win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)

            fg_hwnd = win32gui.GetForegroundWindow()
            if fg_hwnd == hwnd:
                return True

            fg_tid, _ = win32process.GetWindowThreadProcessId(fg_hwnd) if fg_hwnd else (0, 0)
            cur_tid = win32api.GetCurrentThreadId()
            attached = False
            try:
                if fg_tid and fg_tid != cur_tid:
                    win32process.AttachThreadInput(cur_tid, fg_tid, True)
                    attached = True
                # Короткий Alt-трюк розблоковує SetForegroundWindow у Windows
                win32api.keybd_event(win32con.VK_MENU, 0, 0, 0)
                win32api.keybd_event(win32con.VK_MENU, 0, win32con.KEYEVENTF_KEYUP, 0)
                win32gui.SetForegroundWindow(hwnd)
                win32gui.BringWindowToTop(hwnd)
            finally:
                if attached:
                    win32process.AttachThreadInput(cur_tid, fg_tid, False)
            return True
        except Exception as exc:
            log.warning("Не вдалося встановити фокус на HWND=%s (можливо, UIPI): %s", hwnd, exc)
            return False


class WindowManager:
    """Менеджер вікон із кешем HWND, компенсацією DWM-рамок та підтримкою декількох моніторів."""

    def __init__(self, backend: Win32Backend | None = None) -> None:
        self.backend: Win32Backend = backend or RealWin32Backend()
        self._hwnd_cache: dict[str, int] = {}

    def find_window(self, target: str) -> int:
        """Знаходить HWND вікна за назвою ('active', ключ аліасу, процес або заголовок) з кешуванням."""
        key = (target or "active").strip().lower()
        if key in {"active", "current", "це", "поточне", "це вікно", ""}:
            hwnd = self.backend.get_foreground_window()
            if not hwnd or not self.backend.is_window(hwnd):
                raise LookupError("Активне вікно не знайдено")
            return hwnd

        cached = self._hwnd_cache.get(key)
        if cached and self.backend.is_window(cached):
            return cached

        patterns = TARGET_ALIASES.get(key, (key,))
        windows = self.backend.list_visible_windows()

        # 1. Пріоритет: збіг за назвою процесу
        for entry in windows:
            proc_lower = entry.process_name.lower()
            if any(p in proc_lower for p in patterns):
                self._hwnd_cache[key] = entry.hwnd
                log.info(
                    "Знайдено вікно для '%s' за назвою процесу: HWND=%d, process='%s', title='%s'",
                    target,
                    entry.hwnd,
                    entry.process_name,
                    entry.title,
                )
                return entry.hwnd

        # 2. Збіг за заголовком вікна
        for entry in windows:
            title_lower = entry.title.lower()
            if any(p in title_lower for p in patterns):
                self._hwnd_cache[key] = entry.hwnd
                log.info(
                    "Знайдено вікно для '%s' за заголовком: HWND=%d, process='%s', title='%s'",
                    target,
                    entry.hwnd,
                    entry.process_name,
                    entry.title,
                )
                return entry.hwnd

        raise LookupError(f"Вікно для '{target}' не знайдено серед відкритих програм")

    def _resolve_monitor(
        self,
        monitor_idx: int | None,
        hwnd: int,
        is_1based: bool = False,
        is_snap: bool = False,
    ) -> MonitorInfo:
        """Визначає цільовий монітор. Підтримує 1-based номери (1, 2, 3), 0-based індекси та перемикання."""
        monitors = self.backend.get_monitors()
        if not monitors:
            raise RuntimeError("Не знайдено жодного монітора")

        cur_idx = self.backend.get_window_monitor_index(hwnd, monitors)

        # Якщо монітор не вказано:
        if monitor_idx is None or monitor_idx == -1:
            # Для операцій снапу ("прикріпи вліво") залишаємось на поточному моніторі
            if is_snap:
                return monitors[cur_idx]
            # Для операцій перенесення ("перенеси браузер") перемикаємо на наступний монітор
            if len(monitors) > 1:
                next_idx = (cur_idx + 1) % len(monitors)
                log.info("Монітор не вказано: перехід з монітора #%d на #%d (усього: %d)", cur_idx, next_idx, len(monitors))
                return monitors[next_idx]
            return monitors[cur_idx]

        # Якщо виклик явно 1-based (з голосового роутера або CLI):
        if is_1based:
            idx_0 = monitor_idx - 1
            if 0 <= idx_0 < len(monitors):
                target_mon = monitors[idx_0]
                log.info("Вибір монітора за 1-based номером %d -> монітор #%d (%s)", monitor_idx, target_mon.index, target_mon.device)
                return target_mon
            return monitors[-1]

        # Для тестів та зворотної сумісності (коли monitor_idx == len(monitors), напр. 2 при 2 моніторах):
        if monitor_idx == len(monitors) and len(monitors) > 1:
            return monitors[monitor_idx - 1]

        # 0-based індекс (якщо передано з внутрішніх викликів типу docs_lookup або gesture_controller)
        if 0 <= monitor_idx < len(monitors):
            return monitors[monitor_idx]

        if 1 <= monitor_idx <= len(monitors):
            return monitors[monitor_idx - 1]

        log.info("Запитаний номер монітора %d за межами [1..%d], обрано #%d", monitor_idx, len(monitors), monitors[-1].index)
        return monitors[-1]

    def _apply_dwm_compensated_pos(
        self,
        hwnd: int,
        x: int,
        y: int,
        w: int,
        h: int,
    ) -> tuple[int, int, int, int]:
        """Компенсує невидимі рамки DWM Windows 10/11 і викликає set_window_pos."""
        l_pad, t_pad, r_pad, b_pad = self.backend.get_frame_margins(hwnd)
        adj_x = x - l_pad
        adj_y = y - t_pad
        adj_w = w + l_pad + r_pad
        adj_h = h + t_pad + b_pad
        self.backend.set_window_pos(hwnd, adj_x, adj_y, adj_w, adj_h)
        return (adj_x, adj_y, adj_w, adj_h)

    def move_to_monitor(
        self,
        hwnd: int,
        monitor_idx: int | None = None,
        mode: MoveMode = "maximize",
        is_1based: bool = False,
    ) -> dict[str, Any]:
        """Переміщує вікно на вказаний монітор у режимі maximize / left / right / center."""
        with measure_time("window.move"):
            is_snap = mode in {"left", "right", "center"}
            mon = self._resolve_monitor(monitor_idx, hwnd, is_1based=is_1based, is_snap=is_snap)
            work = mon.work_area

            # Спочатку обов'язково SW_RESTORE, інакше максимізоване вікно не змінить монітор
            self.backend.show_window(hwnd, win32con.SW_RESTORE)

            if mode == "maximize":
                # Переносимо у робочу область цільового монітора, потім максимізуємо
                cx = work.left + max(0, (work.width - 800) // 2)
                cy = work.top + max(0, (work.height - 600) // 2)
                self.backend.set_window_pos(
                    hwnd,
                    cx,
                    cy,
                    min(800, work.width),
                    min(600, work.height),
                )
                self.backend.show_window(hwnd, win32con.SW_MAXIMIZE)
                bounds = (work.left, work.top, work.width, work.height)
            else:
                bounds = self._snap_on_work_area(hwnd, work, mode)

            # Обов'язково виводимо вікно на передній план та фокусуємо його
            self.backend.set_foreground(hwnd)
            log.info("Вікно HWND=%d переміщено на монітор #%d (%s, mode=%s)", hwnd, mon.index, mon.device, mode)

            return {
                "hwnd": hwnd,
                "monitor": mon.index,
                "mode": mode,
                "bounds": bounds,
            }

    def _snap_on_work_area(
        self,
        hwnd: int,
        work: Rect,
        mode: Literal["left", "right", "center"],
    ) -> tuple[int, int, int, int]:
        if mode == "left":
            x = work.left
            y = work.top
            w = work.width // 2
            h = work.height
        elif mode == "right":
            w = work.width // 2
            x = work.left + (work.width - w)
            y = work.top
            h = work.height
        else:  # center
            w = int(work.width * 0.75)
            h = int(work.height * 0.80)
            x = work.left + (work.width - w) // 2
            y = work.top + (work.height - h) // 2

        return self._apply_dwm_compensated_pos(hwnd, x, y, w, h)

    def snap(self, hwnd: int, mode: Literal["left", "right", "center"]) -> dict[str, Any]:
        """Прикріплює вікно до лівої/правої половини або центру поточного монітора."""
        return self.move_to_monitor(hwnd, monitor_idx=None, mode=mode)

    def resize(self, hwnd: int, mode: ResizeMode, step_ratio: float = 0.12) -> dict[str, Any]:
        """Змінює розмір вікна (bigger / smaller на 12% від work area, або maximize / restore)."""
        with measure_time("window.resize"):
            if mode == "maximize":
                self.backend.show_window(hwnd, win32con.SW_MAXIMIZE)
                return {"hwnd": hwnd, "mode": mode}
            if mode == "restore":
                self.backend.show_window(hwnd, win32con.SW_RESTORE)
                return {"hwnd": hwnd, "mode": mode}

            mon = self._resolve_monitor(None, hwnd)
            work = mon.work_area
            self.backend.show_window(hwnd, win32con.SW_RESTORE)
            cur = self.backend.get_window_rect(hwnd)

            dw = int(work.width * step_ratio)
            dh = int(work.height * step_ratio)
            sign = 1 if mode == "bigger" else -1

            new_w = max(320, min(work.width, cur.width + sign * dw))
            new_h = max(240, min(work.height, cur.height + sign * dh))

            cx = (cur.left + cur.right) // 2
            cy = (cur.top + cur.bottom) // 2
            new_x = max(work.left, min(work.right - new_w, cx - new_w // 2))
            new_y = max(work.top, min(work.bottom - new_h, cy - new_h // 2))

            self.backend.set_window_pos(hwnd, new_x, new_y, new_w, new_h)
            return {
                "hwnd": hwnd,
                "mode": mode,
                "bounds": (new_x, new_y, new_w, new_h),
            }

    def focus(self, hwnd: int) -> dict[str, Any]:
        """Переводить фокус на вказане вікно."""
        with measure_time("window.focus"):
            ok = self.backend.set_foreground(hwnd)
            return {"hwnd": hwnd, "focused": ok}

    def validate_draggable_window(self, hwnd: int) -> None:
        """Перевіряє, що вікно не є робочим столом, панеллю завдань або процесом Адміністратора."""
        if not hwnd or not self.backend.is_window(hwnd):
            raise LookupError("Активне вікно не знайдено")
        cls_name = self.backend.get_window_class(hwnd)
        if cls_name in PROTECTED_WINDOW_CLASSES:
            raise PermissionError(
                f"Вікно системного класу '{cls_name}' (робочий стіл / панель завдань) не можна переміщувати"
            )
        if self.backend.is_window_elevated(hwnd):
            raise PermissionError(
                "Неможливо перемістити вікно: програма запущена з правами Адміністратора (UIPI)"
            )


class WindowMoveSkill:
    """Навичка window.move."""

    name = "window.move"

    def __init__(self, manager: WindowManager) -> None:
        self.manager = manager

    async def execute(self, args: dict[str, Any]) -> dict[str, Any]:
        target = str(args.get("target", "active"))
        monitor = args.get("monitor")
        monitor_idx = int(monitor) if monitor is not None else None
        mode: MoveMode = args.get("mode", "maximize")

        def _run() -> dict[str, Any]:
            hwnd = self.manager.find_window(target)
            return self.manager.move_to_monitor(hwnd, monitor_idx=monitor_idx, mode=mode, is_1based=True)

        return await asyncio.to_thread(_run)


class WindowResizeSkill:
    """Навичка window.resize."""

    name = "window.resize"

    def __init__(self, manager: WindowManager) -> None:
        self.manager = manager

    async def execute(self, args: dict[str, Any]) -> dict[str, Any]:
        target = str(args.get("target", "active"))
        mode: ResizeMode = args.get("mode", "bigger")

        def _run() -> dict[str, Any]:
            hwnd = self.manager.find_window(target)
            return self.manager.resize(hwnd, mode=mode)

        return await asyncio.to_thread(_run)


class WindowFocusSkill:
    """Навичка window.focus."""

    name = "window.focus"

    def __init__(self, manager: WindowManager) -> None:
        self.manager = manager

    async def execute(self, args: dict[str, Any]) -> dict[str, Any]:
        target = str(args.get("target", "active"))

        def _run() -> dict[str, Any]:
            hwnd = self.manager.find_window(target)
            return self.manager.focus(hwnd)

        return await asyncio.to_thread(_run)

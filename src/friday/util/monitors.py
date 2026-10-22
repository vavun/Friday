"""Робота з моніторами, work area та DPI awareness (Win32 API)."""

from __future__ import annotations

import ctypes
import logging
from dataclasses import dataclass

import win32api
import win32con

log = logging.getLogger("friday.util.monitors")


@dataclass(slots=True, frozen=True)
class Rect:
    """Прямокутна область екрана (пікселі з урахуванням DPI)."""

    left: int
    top: int
    right: int
    bottom: int

    @property
    def width(self) -> int:
        """Ширина області."""
        return max(0, self.right - self.left)

    @property
    def height(self) -> int:
        """Висота області."""
        return max(0, self.bottom - self.top)


@dataclass(slots=True, frozen=True)
class MonitorInfo:
    """Інформація про монітор, включно з робочою областю (work area без панелі завдань)."""

    index: int
    handle: int
    device: str
    is_primary: bool
    monitor_rect: Rect
    work_area: Rect


def enable_dpi_awareness() -> bool:
    """Вмикає Per-Monitor DPI Awareness на старті процесу Windows."""
    try:
        # Per-Monitor V2 DPI Awareness (-4) або شcore SetProcessDpiAwareness(2)
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        return True
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
            return True
        except Exception as exc:
            log.debug("Не вдалося встановити DPI awareness: %s", exc)
            return False


def list_monitors() -> list[MonitorInfo]:
    """Повертає відсортований список моніторів (основний монітор завжди перший, далі зліва направо)."""
    raw_monitors = win32api.EnumDisplayMonitors(None, None)
    parsed: list[tuple[bool, int, int, int, str, Rect, Rect]] = []

    for hmon, _hdc, _rect in raw_monitors:
        info = win32api.GetMonitorInfo(hmon)
        mon_l, mon_t, mon_r, mon_b = info["Monitor"]
        work_l, work_t, work_r, work_b = info["Work"]
        flags = int(info.get("Flags", 0))
        is_primary = bool(flags & win32con.MONITORINFOF_PRIMARY)
        device = str(info.get("Device", ""))

        parsed.append(
            (
                not is_primary,  # False (primary) сортується перед True
                mon_l,
                mon_t,
                int(hmon),
                device,
                Rect(mon_l, mon_t, mon_r, mon_b),
                Rect(work_l, work_t, work_r, work_b),
            )
        )

    parsed.sort(key=lambda item: (item[0], item[1], item[2]))

    result: list[MonitorInfo] = []
    for idx, (not_primary, _l, _t, handle, device, m_rect, w_rect) in enumerate(parsed):
        result.append(
            MonitorInfo(
                index=idx,
                handle=handle,
                device=device,
                is_primary=not not_primary,
                monitor_rect=m_rect,
                work_area=w_rect,
            )
        )
    return result

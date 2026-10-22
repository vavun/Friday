"""Звукові сигнали та toast-сповіщення Windows (winotify + winsound)."""

from __future__ import annotations

import asyncio
import logging
from typing import Callable

log = logging.getLogger("friday.feedback")

try:
    import winsound
except ImportError:  # pragma: no cover
    winsound = None  # type: ignore[assignment]

try:
    from winotify import Notification
except ImportError:  # pragma: no cover
    Notification = None  # type: ignore[assignment]


class FeedbackManager:
    """Керує короткими звуковими індикаторами та спливаючими сповіщеннями (toast)."""

    def __init__(
        self,
        enabled_sound: bool = True,
        enabled_toast: bool = True,
        beep_fn: Callable[[int, int], None] | None = None,
    ) -> None:
        self.enabled_sound = enabled_sound
        self.enabled_toast = enabled_toast
        self._beep_fn = beep_fn or (winsound.Beep if winsound is not None else None)

    def _beep_sync(self, freq_hz: int, duration_ms: int) -> None:
        """Синхронний короткий сигнал."""
        if not self.enabled_sound or self._beep_fn is None:
            return
        try:
            self._beep_fn(freq_hz, duration_ms)
        except Exception as exc:
            log.debug("Не вдалося відтворити звуковий сигнал: %s", exc)

    async def beep_start(self) -> None:
        """Короткий високий сигнал початку запису."""
        await asyncio.to_thread(self._beep_sync, 880, 45)

    async def beep_stop(self) -> None:
        """Короткий нижчий сигнал завершення запису."""
        await asyncio.to_thread(self._beep_sync, 587, 45)

    async def beep_error(self) -> None:
        """Сигнал помилки або скасування."""
        await asyncio.to_thread(self._beep_sync, 330, 90)

    def _toast_sync(self, title: str, message: str) -> None:
        """Синхронний показ Windows Toast через winotify."""
        log.info("🔔 [%s] %s", title, message)
        if not self.enabled_toast or Notification is None:
            return
        try:
            toast = Notification(
                app_id="FRIDAY",
                title=title,
                msg=message,
                duration="short",
            )
            toast.show()
        except Exception as exc:
            log.debug("Не вдалося показати toast-сповіщення: %s", exc)

    async def notify(self, title: str, message: str) -> None:
        """Асинхронно показує toast-сповіщення без блокування event loop."""
        await asyncio.to_thread(self._toast_sync, title, message)

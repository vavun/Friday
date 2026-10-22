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
    """Керує спливаючими сповіщеннями Windows Toast (звукові сигнали вимкнено за замовчуванням)."""

    def __init__(
        self,
        enabled_sound: bool = False,
        enabled_toast: bool = True,
        beep_fn: Callable[[int, int], None] | None = None,
    ) -> None:
        self.enabled_sound = False
        self.enabled_toast = enabled_toast
        self._beep_fn = beep_fn

    def _beep_sync(self, freq_hz: int, duration_ms: int) -> None:
        """Звукові сигнали вимкнено на вимогу користувача."""
        return

    async def beep_start(self) -> None:
        """Вимкнено (без звуку початку запису)."""
        return

    async def beep_stop(self) -> None:
        """Вимкнено (без звуку завершення запису)."""
        return

    async def beep_error(self) -> None:
        """Вимкнено (при помилці надсилається toast-сповіщення замість звуку)."""
        return

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

"""Автомат станів та головна оркестрація асистента FRIDAY."""

from __future__ import annotations

import asyncio
import logging
from enum import StrEnum

from friday.config import AppConfig

log = logging.getLogger("friday.app")


class AssistantState(StrEnum):
    """Стани автомата FRIDAY."""

    IDLE = "IDLE"
    LISTENING = "LISTENING"
    TRANSCRIBING = "TRANSCRIBING"
    ROUTING = "ROUTING"
    CONFIRMING = "CONFIRMING"
    EXECUTING = "EXECUTING"


class FridayApp:
    """Головний клас асистента, що керує станами та життєвим циклом."""

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self._state: AssistantState = AssistantState.IDLE
        self._stop_event = asyncio.Event()

    @property
    def state(self) -> AssistantState:
        """Поточний стан асистента."""
        return self._state

    def transition(self, new_state: AssistantState) -> None:
        """Переводить автомат у новий стан із логуванням."""
        if self._state != new_state:
            log.info("Стан: %s -> %s", self._state.value, new_state.value)
            self._state = new_state

    def stop(self) -> None:
        """Сигналізує про завершення роботи головного циклу."""
        self._stop_event.set()

    async def run(self, once: bool = False) -> None:
        """Запускає головний цикл очікування подій."""
        log.info(
            "FRIDAY запущено (стан=%s, stt=%s, hotkey=%s)",
            self._state.value,
            self.config.stt.backend,
            self.config.hotkeys.voice,
        )
        if once:
            log.info("Режим перевірки (--once): завершення роботи.")
            return

        try:
            await self._stop_event.wait()
        except asyncio.CancelledError:
            log.info("Отримано сигнал скасування.")
        finally:
            self.transition(AssistantState.IDLE)
            log.info("FRIDAY зупинено.")

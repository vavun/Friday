"""Інтерфейс Transcriber для перетворення голосу на текст (Етап 2)."""

from __future__ import annotations

from typing import Protocol


class Transcriber(Protocol):
    """Протокол для всіх STT-бекендів."""

    async def transcribe(self, wav_bytes: bytes) -> str:
        """Перетворює WAV-аудіо (16 кГц, mono, int16) на текст."""
        ...

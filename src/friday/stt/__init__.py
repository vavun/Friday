"""Пакет розпізнавання мовлення (STT) та фабрика створення бекенду."""

from __future__ import annotations

from friday.config import AppConfig
from friday.stt.base import Transcriber
from friday.stt.gemini import GeminiTranscriber
from friday.stt.whisper_local import WhisperLocalTranscriber

__all__ = [
    "Transcriber",
    "GeminiTranscriber",
    "WhisperLocalTranscriber",
    "create_transcriber",
]


def create_transcriber(config: AppConfig) -> Transcriber:
    """Створює потрібний Transcriber згідно з config.stt.backend."""
    if config.stt.backend == "whisper_local":
        return WhisperLocalTranscriber(config=config.stt)
    return GeminiTranscriber(config=config.stt, api_key=config.secrets.gemini_api_key)

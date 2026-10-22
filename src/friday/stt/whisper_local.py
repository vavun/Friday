"""Офлайн-розпізнавання мовлення через faster-whisper (модель small, int8)."""

from __future__ import annotations

import asyncio
import io
import logging
import os
from typing import Any, Callable

import numpy as np

from friday.config import SttConfig
from friday.util.timing import measure_time

os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
log = logging.getLogger("friday.stt.whisper_local")

WHISPER_INITIAL_PROMPT_UK = (
    "Голосові команди українською мовою зі вставками англійських термінів: "
    "FastAPI, pydantic, traceback, git, TypeError, KeyError, ValueError, pytest, "
    "VS Code, Telegram, Chrome."
)


class WhisperLocalTranscriber:
    """Офлайн STT на базі faster-whisper. Завантажує модель лише при виборі цього бекенду."""

    def __init__(
        self,
        config: SttConfig,
        model_size: str = "small",
        compute_type: str = "int8",
        model_factory: Callable[..., Any] | None = None,
    ) -> None:
        self.config = config
        self.model_size = model_size
        self.compute_type = compute_type
        self._model_factory = model_factory
        self._model: Any | None = None

    def _ensure_model_sync(self) -> Any:
        """Завантажує модель faster-whisper (якщо ще не завантажена)."""
        if self._model is not None:
            return self._model

        with measure_time("stt.whisper_load_model"):
            if self._model_factory is not None:
                self._model = self._model_factory(
                    self.model_size,
                    device="cpu",
                    compute_type=self.compute_type,
                )
            else:
                try:
                    from faster_whisper import WhisperModel  # type: ignore[import-not-found]
                except ImportError as exc:
                    raise RuntimeError(
                        "Пакет 'faster-whisper' не встановлено. "
                        "Встановіть через: .\\.venv\\Scripts\\pip.exe install -e '.[whisper]'"
                    ) from exc
                self._model = WhisperModel(
                    self.model_size,
                    device="cpu",
                    compute_type=self.compute_type,
                )
        log.info("Модель faster-whisper (%s, %s) завантажено.", self.model_size, self.compute_type)
        return self._model

    async def warmup(self) -> None:
        """Прогріває модель коротким тихим буфером при старті (якщо обрано whisper_local)."""
        model = await asyncio.to_thread(self._ensure_model_sync)
        silent = np.zeros(8000, dtype=np.float32)

        def _run_warmup() -> None:
            segments, _ = model.transcribe(
                silent,
                language=self.config.language or "uk",
                beam_size=1,
            )
            list(segments)

        with measure_time("stt.whisper_warmup"):
            await asyncio.to_thread(_run_warmup)
        log.info("Модель faster-whisper прогріта і готова до роботи.")

    def _transcribe_sync(self, wav_bytes: bytes) -> str:
        """Синхронна транскрибація WAV-байтів."""
        model = self._ensure_model_sync()
        segments, _info = model.transcribe(
            io.BytesIO(wav_bytes),
            language=self.config.language or "uk",
            initial_prompt=WHISPER_INITIAL_PROMPT_UK,
            beam_size=2,
            vad_filter=True,
        )
        text = " ".join(seg.text.strip() for seg in segments if getattr(seg, "text", "").strip())
        return text.strip()

    async def transcribe(self, wav_bytes: bytes) -> str:
        """Асинхронна обгортка транскрибації через asyncio.to_thread."""
        if not wav_bytes:
            return ""
        with measure_time("stt.whisper_local"):
            transcript = await asyncio.to_thread(self._transcribe_sync, wav_bytes)
        log.info("🗣 Розпізнано (Whisper): %r", transcript)
        return transcript

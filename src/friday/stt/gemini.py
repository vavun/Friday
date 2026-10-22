"""Розпізнавання мовлення через Gemini API (адаптовано з Jarvis Server/app/stt.py)."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from google import genai
from google.genai import types

from friday.config import SttConfig
from friday.util.timing import measure_time

log = logging.getLogger("friday.stt.gemini")

VALID_MODES = {"VERBATIM", "SMART"}

STT_PROMPT_UK = (
    "Транскрибуй аудіо дослівно українською мовою. "
    "Англійські технічні терміни, назви бібліотек, команд і класів "
    "(наприклад: FastAPI, pydantic, traceback, git, TypeError, KeyError, pytest, VS Code) "
    "залишай латиницею. Виводь ТІЛЬКИ розпізнаний текст без жодних коментарів."
)


def _extract_transcript_from_response(resp: Any) -> str:
    """Витягує текст транскрипції з відповіді Gemini (text або audio_transcription)."""
    candidates = getattr(resp, "candidates", None) or []
    if not candidates or not getattr(candidates[0], "content", None):
        log.warning("STT: порожня відповідь від Gemini: %r", resp)
        return ""

    parts = getattr(candidates[0].content, "parts", None) or []
    text = "".join(p.text for p in parts if getattr(p, "text", None)).strip()
    if text:
        return text

    chunks: list[str] = []
    for p in parts:
        t = getattr(p, "audio_transcription", None)
        if not t:
            continue
        if getattr(t, "text", None):
            chunks.append(t.text)
        else:
            chunks.extend(
                w.word for w in (getattr(t, "words", None) or []) if getattr(w, "word", None)
            )
    return " ".join(chunks).strip()


class GeminiTranscriber:
    """Клієнт розпізнавання мовлення через Gemini API з повторним використанням HTTP-клієнта."""

    def __init__(
        self,
        config: SttConfig,
        api_key: str,
        client: genai.Client | None = None,
        timeout_s: float = 10.0,
    ) -> None:
        self.config = config
        self._api_key = api_key
        self._client: genai.Client | None = client
        self.timeout_s = timeout_s

    def _get_client(self) -> genai.Client:
        """Повертає збережений екземпляр genai.Client або створює його один раз."""
        if self._client is None:
            if not self._api_key:
                raise RuntimeError("GEMINI_API_KEY не задано в .env")
            self._client = genai.Client(api_key=self._api_key)
        return self._client

    def _build_generate_config(self) -> types.GenerateContentConfig:
        """Формує конфігурацію для моделей сімейства transcribe або flash."""
        model_lower = self.config.model.lower()
        if "transcribe" in model_lower:
            cfg_kwargs: dict[str, Any] = {
                "language_codes": [self.config.language] if self.config.language else [],
            }
            mode = self.config.mode.strip().upper()
            if mode in VALID_MODES:
                cfg_kwargs["mode"] = mode
            return types.GenerateContentConfig(
                audio_transcription_config=types.AudioTranscriptionConfig(**cfg_kwargs),
            )
        return types.GenerateContentConfig(
            system_instruction=STT_PROMPT_UK,
            temperature=0.0,
        )

    async def transcribe(self, wav_bytes: bytes) -> str:
        """Відправляє WAV-байти в Gemini та повертає розпізнаний текст."""
        if not wav_bytes:
            return ""

        client = self._get_client()
        gen_config = self._build_generate_config()
        audio_part = types.Part.from_bytes(data=wav_bytes, mime_type="audio/wav")

        model_lower = self.config.model.lower()
        contents: list[Any] = (
            [audio_part]
            if "transcribe" in model_lower
            else [audio_part, STT_PROMPT_UK]
        )

        with measure_time("stt.gemini"):
            resp = await asyncio.wait_for(
                client.aio.models.generate_content(
                    model=self.config.model,
                    contents=contents,
                    config=gen_config,
                ),
                timeout=self.timeout_s,
            )

        transcript = _extract_transcript_from_response(resp)
        log.info("🗣 Розпізнано (Gemini): %r", transcript)
        return transcript

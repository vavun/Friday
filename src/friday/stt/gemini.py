"""Fast speech transcription using Gemini API."""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from google import genai
from google.genai import types

from friday.config import SttConfig
from friday.util.timing import measure_time

log = logging.getLogger("friday.stt.gemini")

VALID_MODES = {"VERBATIM", "SMART"}

STT_PROMPT_UK = (
    "Transcribe the spoken audio verbatim into text, preserving Ukrainian or English as spoken. "
    "Keep English technical terms, library names, commands, and code symbols in Latin script. "
    "If the audio is silent or contains only background noise, return nothing. "
    "Output ONLY the exact transcription, without preamble, explanations, quotation marks, or markdown."
)


def _extract_transcript_from_response(resp: Any) -> str:
    """Extract transcript from Gemini response."""
    candidates = getattr(resp, "candidates", None) or []
    if not candidates or not getattr(candidates[0], "content", None):
        log.warning("STT: empty response from Gemini: %r", resp)
        return ""

    parts = getattr(candidates[0].content, "parts", None) or []
    text = "".join(p.text for p in parts if getattr(p, "text", None)).strip()
    if text:
        return re.sub(r"^[\"'\`]+|[\"'\`]+$", "", text).strip()

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
    return re.sub(r"^[\"'\`]+|[\"'\`]+$", "", " ".join(chunks)).strip()


class GeminiTranscriber:
    """Gemini API speech transcriber."""

    def __init__(
        self,
        config: SttConfig,
        api_key: str,
        client: genai.Client | None = None,
        timeout_s: float | None = None,
    ) -> None:
        self.config = config
        self._api_key = api_key
        self._client: genai.Client | None = client
        self.timeout_s = timeout_s if timeout_s is not None else getattr(config, "timeout_s", 20.0)

    def _get_client(self) -> genai.Client:
        """Get or initialize cached genai.Client."""
        if self._client is None:
            if not self._api_key:
                raise RuntimeError("GEMINI_API_KEY is not set in .env")
            self._client = genai.Client(api_key=self._api_key)
        return self._client

    def _build_generate_config(self, model_name: str | None = None) -> types.GenerateContentConfig:
        """Build generation config for transcribe or flash models."""
        target_model = (model_name or self.config.model).lower()
        if "transcribe" in target_model:
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
        """Transcribe WAV audio bytes."""
        if not wav_bytes:
            return ""

        client = self._get_client()
        audio_part = types.Part.from_bytes(data=wav_bytes, mime_type="audio/wav")

        async def _call_model(model_name: str) -> str:
            gen_config = self._build_generate_config(model_name)
            is_transcribe = "transcribe" in model_name.lower()
            contents: list[Any] = [audio_part] if is_transcribe else [audio_part, STT_PROMPT_UK]
            resp = await asyncio.wait_for(
                client.aio.models.generate_content(
                    model=model_name,
                    contents=contents,
                    config=gen_config,
                ),
                timeout=self.timeout_s,
            )
            return _extract_transcript_from_response(resp)

        with measure_time("stt.gemini"):
            try:
                transcript = await _call_model(self.config.model)
            except TimeoutError:
                if self.config.model != "gemini-3.5-flash-lite":
                    log.warning(
                        "STT model %s timed out; retrying with gemini-3.5-flash-lite",
                        self.config.model,
                    )
                    transcript = await _call_model("gemini-3.5-flash-lite")
                else:
                    raise

            if not transcript and self.config.model != "gemini-3.5-flash-lite":
                log.info("STT empty with %s; falling back to gemini-3.5-flash-lite", self.config.model)
                try:
                    transcript = await _call_model("gemini-3.5-flash-lite")
                except Exception as exc:
                    log.warning("Fallback STT failed: %s", exc)

        log.info("🗣 Recognized (Gemini): %r", transcript)
        return transcript

from __future__ import annotations

import io
import logging
import re
import threading
from pathlib import Path
from typing import Protocol

import soundfile as sf

from friday.config import TtsConfig
from friday.tts.sanitizer import sanitize_for_tts

log = logging.getLogger("friday.tts.engine")


class TtsEngine(Protocol):
    async def synthesize_pcm(self, text: str) -> bytes | None: ...
    async def warmup(self) -> None: ...


class EdgeTtsEngine:
    """Online Edge-TTS with fallback support."""

    def __init__(self, config: TtsConfig, fallback: TtsEngine | None = None) -> None:
        self.config = config
        self.fallback = fallback

    async def synthesize_pcm(self, text: str) -> bytes | None:
        clean = sanitize_for_tts(text)
        if not clean:
            return None

        rate_pct = int((self.config.speed - 1.0) * 100)
        rate_str = f"{rate_pct:+d}%"

        voice = self.config.voice
        if re.search(r"[\u0400-\u04FF]", clean):
            voice = getattr(self.config, "voice_uk", "uk-UA-PolinaNeural") or "uk-UA-PolinaNeural"

        try:
            import edge_tts

            comm = edge_tts.Communicate(clean, voice=voice, rate=rate_str)
            chunks: list[bytes] = []
            async for chunk in comm.stream():
                if chunk["type"] == "audio":
                    chunks.append(chunk["data"])

            if not chunks:
                return None

            mp3_bytes = b"".join(chunks)
            samples, _ = sf.read(io.BytesIO(mp3_bytes), dtype="int16")
            return samples.tobytes()
        except Exception as exc:
            log.warning("Edge-TTS error (%s): %s", clean[:30], exc)
            if self.fallback is not None:
                return await self.fallback.synthesize_pcm(clean)
            return None

    async def warmup(self) -> None:
        try:
            await self.synthesize_pcm("Ready.")
            log.info("Edge-TTS ready (%s)", self.config.voice)
        except Exception as exc:
            log.warning("Edge-TTS warmup failed: %s", exc)
            if self.fallback is not None:
                await self.fallback.warmup()


class KokoroTtsEngine:
    """Offline ONNX Kokoro TTS fallback engine."""

    def __init__(self, config: TtsConfig) -> None:
        self.config = config
        self._kokoro = None
        self._lock = threading.Lock()

    def _get_kokoro(self):
        if self._kokoro is None:
            with self._lock:
                if self._kokoro is None:
                    import onnxruntime as ort
                    from kokoro_onnx import Kokoro

                    m_path = Path(self.config.kokoro_model_path)
                    v_path = Path(self.config.kokoro_voices_path)
                    if not m_path.exists() or not v_path.exists():
                        raise FileNotFoundError(f"Kokoro assets missing: {m_path}")

                    opts = ort.SessionOptions()
                    opts.intra_op_num_threads = 2
                    opts.inter_op_num_threads = 1
                    opts.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
                    opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL

                    session = ort.InferenceSession(
                        str(m_path),
                        sess_options=opts,
                        providers=["CPUExecutionProvider"],
                    )
                    self._kokoro = Kokoro.from_session(session, str(v_path))
                    log.info("Kokoro ONNX engine ready (%s)", self.config.kokoro_voice)
        return self._kokoro

    async def synthesize_pcm(self, text: str) -> bytes | None:
        clean = sanitize_for_tts(text)
        if not clean:
            return None

        import asyncio
        import numpy as np

        def _run() -> bytes | None:
            try:
                kokoro = self._get_kokoro()
                samples, _ = kokoro.create(
                    clean,
                    voice=self.config.kokoro_voice,
                    speed=self.config.speed,
                )
                if samples is None or len(samples) == 0:
                    return None
                if samples.dtype == np.int16:
                    return samples.tobytes()
                pcm = (np.clip(samples, -1.0, 1.0) * 32767).astype(np.int16)
                return pcm.tobytes()
            except Exception as exc:
                log.warning("Kokoro synthesis failed: %s", exc)
                return None

        return await asyncio.to_thread(_run)

    async def warmup(self) -> None:
        await self.synthesize_pcm("Ready.")


def create_tts_engine(config: TtsConfig) -> TtsEngine | None:
    """Factory creating configured TTS engine."""
    if not config.enabled or config.engine == "off":
        return None

    kokoro_fallback: KokoroTtsEngine | None = None
    if Path(config.kokoro_model_path).exists() and Path(config.kokoro_voices_path).exists():
        kokoro_fallback = KokoroTtsEngine(config)

    if config.engine == "kokoro":
        return kokoro_fallback or KokoroTtsEngine(config)

    return EdgeTtsEngine(config, fallback=kokoro_fallback)

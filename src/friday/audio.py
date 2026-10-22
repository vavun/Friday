"""Запис звуку з мікрофона (16 кГц, mono, int16), ліміт 15 с, відсікання коротких кліків та збереження у WAV."""

from __future__ import annotations

import io
import logging
import threading
import time
import wave
from pathlib import Path
from typing import Any, Callable

import numpy as np
import sounddevice as sd

from friday.config import AudioConfig, PROJECT_ROOT
from friday.util.timing import measure_time

log = logging.getLogger("friday.audio")

DEFAULT_TEMP_DIR = PROJECT_ROOT / "temp"


def encode_wav_int16(samples: np.ndarray, sample_rate: int = 16000) -> bytes:
    """Кодує 1D або 2D int16 масив семплів у WAV-байти (mono, 16-bit PCM)."""
    flat = np.asarray(samples, dtype=np.int16).reshape(-1)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(flat.tobytes())
    return buf.getvalue()


def normalize_int16(samples: np.ndarray, target_peak: int = 28000) -> np.ndarray:
    """М'яко нормалізує гучність аудіо (якщо сигнал не тиша і занадто тихий)."""
    flat = np.asarray(samples, dtype=np.int16).reshape(-1)
    if flat.size == 0:
        return flat
    peak = int(np.max(np.abs(flat.astype(np.int32))))
    if peak < 300 or peak >= target_peak:
        return flat
    gain = min(target_peak / peak, 4.0)
    scaled = np.clip(flat.astype(np.float32) * gain, -32768, 32767)
    return scaled.astype(np.int16)


class AudioRecorder:
    """Потокобезпечний рекордер мікрофона на базі sounddevice.InputStream (адаптовано з Jarvis voice.py)."""

    def __init__(
        self,
        config: AudioConfig | None = None,
        stream_factory: Callable[..., Any] | None = None,
        temp_dir: Path | None = None,
        save_to_temp: bool = True,
    ) -> None:
        self.config = config or AudioConfig()
        self._stream_factory = stream_factory or sd.InputStream
        self.temp_dir = temp_dir or DEFAULT_TEMP_DIR
        self.save_to_temp = save_to_temp

        self._chunks: list[np.ndarray] = []
        self._samples_collected: int = 0
        self._max_samples: int = self.config.sample_rate * self.config.max_seconds
        self._min_samples: int = int(self.config.sample_rate * (self.config.min_ms / 1000.0))
        self._stream: Any | None = None
        self._lock = threading.Lock()
        self._last_saved_path: Path | None = None

    @property
    def recording(self) -> bool:
        """Повертає True, якщо зараз триває запис."""
        return self._stream is not None

    @property
    def last_saved_path(self) -> Path | None:
        """Шлях до останнього збереженого у temp/ WAV-файлу."""
        return self._last_saved_path

    def _callback(self, indata: np.ndarray, frames: int, time_info: Any, status: Any) -> None:
        """Callback потоку sounddevice: додає чанки до буфера з обмеженням max_seconds."""
        if status:
            log.debug("Статус аудіопотоку: %s", status)
        if self._samples_collected >= self._max_samples:
            return
        chunk = np.asarray(indata, dtype=np.int16).reshape(-1).copy()
        remaining = self._max_samples - self._samples_collected
        if chunk.size > remaining:
            chunk = chunk[:remaining]
        self._chunks.append(chunk)
        self._samples_collected += chunk.size

    def start(self) -> bool:
        """Відкриває InputStream та починає запис. Повертає True, якщо запис стартував."""
        with self._lock:
            if self._stream is not None:
                return False
            self._chunks = []
            self._samples_collected = 0
            with measure_time("audio.start_stream"):
                stream = self._stream_factory(
                    samplerate=self.config.sample_rate,
                    channels=1,
                    dtype="int16",
                    callback=self._callback,
                )
                stream.start()
                self._stream = stream
            log.info(
                "🎙 Запис розпочато (%d Гц, ліміт %d с)",
                self.config.sample_rate,
                self.config.max_seconds,
            )
            return True

    def stop(self) -> bytes | None:
        """Зупиняє потік, перевіряє тривалість (>= min_ms, <= max_seconds) і повертає WAV-байти або None."""
        with self._lock:
            if self._stream is None:
                return None
            stream = self._stream
            self._stream = None
            chunks = self._chunks
            self._chunks = []
            self._samples_collected = 0

        with measure_time("audio.stop_and_encode"):
            try:
                stream.stop()
            finally:
                stream.close()

            if not chunks:
                log.info("Запис порожній — відкинуто.")
                return None

            raw_audio = np.concatenate(chunks)[: self._max_samples]
            duration_ms = (raw_audio.size / self.config.sample_rate) * 1000.0
            if raw_audio.size < self._min_samples:
                log.info(
                    "Запис закороткий (%.0f мс < %d мс) — відкинуто.",
                    duration_ms,
                    self.config.min_ms,
                )
                return None

            normalized = normalize_int16(raw_audio)
            wav_bytes = encode_wav_int16(normalized, sample_rate=self.config.sample_rate)

            if self.save_to_temp:
                self._last_saved_path = self._save_temp_wav(wav_bytes)

            log.info(
                "🎙 Запис завершено: %.2f с (%d байт)",
                duration_ms / 1000.0,
                len(wav_bytes),
            )
            return wav_bytes

    def _save_temp_wav(self, wav_bytes: bytes) -> Path:
        """Зберігає останній запис у temp/ для діагностики та перевірки."""
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        path = self.temp_dir / f"recording_{int(time.time() * 1000)}.wav"
        latest = self.temp_dir / "latest.wav"
        path.write_bytes(wav_bytes)
        latest.write_bytes(wav_bytes)
        return path

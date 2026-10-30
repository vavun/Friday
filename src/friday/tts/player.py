from __future__ import annotations

import asyncio
import io
import logging
import threading
import wave

import numpy as np
import sounddevice as sd

log = logging.getLogger("friday.tts.player")


def generate_chime(
    freqs: list[float],
    duration_s: float = 0.07,
    sample_rate: int = 24000,
    volume: float = 0.12,
) -> bytes:
    """Generate smooth 16-bit mono PCM earcon chime."""
    samples_per_note = int(sample_rate * duration_s / len(freqs))
    t = np.linspace(0, duration_s / len(freqs), samples_per_note, False)
    notes = [np.sin(2 * np.pi * f * t) for f in freqs]
    full = np.concatenate(notes)
    window = np.sin(np.linspace(0, np.pi, len(full)))
    return (full * window * volume * 32767).astype(np.int16).tobytes()


START_CHIME = generate_chime([587.33, 880.0])
STOP_CHIME = generate_chime([880.0, 587.33])


class AudioPlayer:
    """Persistent RawOutputStream for low-latency playback."""

    def __init__(self, sample_rate: int = 24000) -> None:
        self.sample_rate = sample_rate
        self._lock = threading.Lock()
        self._closed = False
        self._stream = sd.RawOutputStream(
            samplerate=sample_rate,
            channels=1,
            dtype="int16",
        )
        self._stream.start()

    def play_audio(self, audio_data: bytes) -> None:
        """Write raw 16-bit PCM or WAV frames to audio stream."""
        if audio_data.startswith(b"RIFF"):
            with wave.open(io.BytesIO(audio_data), "rb") as wf:
                frames = wf.readframes(wf.getnframes())
        else:
            frames = audio_data

        with self._lock:
            if not self._closed:
                self._stream.write(frames)

    def close(self) -> None:
        with self._lock:
            if not self._closed:
                self._closed = True
                self._stream.stop()
                self._stream.close()


def clear_queue(queue: asyncio.Queue) -> None:
    """Drain queue non-blockingly."""
    while not queue.empty():
        try:
            queue.get_nowait()
            queue.task_done()
        except (asyncio.QueueEmpty, ValueError):
            break

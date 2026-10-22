"""Юніт-тести для Етапу 1: гарячі клавіші (hold-to-talk), запис аудіо, ліміти та зворотний зв'язок."""

from __future__ import annotations

import asyncio
import io
import time
import wave
from pathlib import Path
from typing import Any

import numpy as np
from pynput import keyboard

from friday.app import AssistantState, FridayApp
from friday.audio import AudioRecorder, encode_wav_int16, normalize_int16
from friday.config import AppConfig, AudioConfig, HotkeysConfig
from friday.feedback import FeedbackManager
from friday.hotkeys import (
    HotkeyEvent,
    HotkeyEventType,
    HotkeyListener,
    key_to_tokens,
    normalize_key_token,
    parse_combo,
)


class FakeInputStream:
    """Фейковий sounddevice.InputStream для детермінованих тестів без фізичного мікрофона."""

    def __init__(
        self,
        samplerate: int,
        channels: int,
        dtype: str,
        callback: Any,
        chunks_to_push: list[np.ndarray] | None = None,
    ) -> None:
        self.samplerate = samplerate
        self.channels = channels
        self.dtype = dtype
        self.callback = callback
        self.started = False
        self.stopped = False
        self.closed = False
        self._chunks_to_push = chunks_to_push or []

    def start(self) -> None:
        self.started = True
        for chunk in self._chunks_to_push:
            self.callback(chunk, len(chunk), None, None)

    def stop(self) -> None:
        self.stopped = True

    def close(self) -> None:
        self.closed = True


def test_audio_recorder_rejects_short_clips(tmp_path: Path) -> None:
    """Запис коротший за min_ms (300 мс) має відкидатися і повертати None."""
    cfg = AudioConfig(sample_rate=16000, max_seconds=15, min_ms=300)
    # 100 мс = 1600 семплів (< 4800 семплів для 300 мс)
    short_chunk = np.full(1600, 1000, dtype=np.int16)

    recorder = AudioRecorder(
        config=cfg,
        stream_factory=lambda **kw: FakeInputStream(chunks_to_push=[short_chunk], **kw),
        temp_dir=tmp_path,
    )
    assert recorder.start() is True
    assert recorder.recording is True
    wav = recorder.stop()
    assert wav is None
    assert recorder.recording is False


def test_audio_recorder_saves_valid_wav_and_caps_max_seconds(tmp_path: Path) -> None:
    """Запис довший за max_seconds обрізається рівно до ліміту і зберігається у temp/."""
    cfg = AudioConfig(sample_rate=16000, max_seconds=1, min_ms=200)
    # Подаємо 2 секунди аудіо (32000 семплів), має обрізати до 1 секунди (16000 семплів)
    long_chunk = np.full(32000, 2500, dtype=np.int16)

    recorder = AudioRecorder(
        config=cfg,
        stream_factory=lambda **kw: FakeInputStream(chunks_to_push=[long_chunk], **kw),
        temp_dir=tmp_path,
        save_to_temp=True,
    )
    recorder.start()
    wav_bytes = recorder.stop()

    assert wav_bytes is not None
    assert recorder.last_saved_path is not None
    assert recorder.last_saved_path.exists()
    assert (tmp_path / "latest.wav").exists()

    with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
        assert wf.getnchannels() == 1
        assert wf.getsampwidth() == 2
        assert wf.getframerate() == 16000
        assert wf.getnframes() == 16000


def test_normalize_and_encode_wav() -> None:
    """Перевіряє нормалізацію тихого сигналу та кодування у WAV."""
    quiet = np.array([1000, -1000, 500, -500], dtype=np.int16)
    norm = normalize_int16(quiet, target_peak=20000)
    assert int(np.max(np.abs(norm))) == 4000  # максимальне підсилення 4x

    wav = encode_wav_int16(norm, sample_rate=16000)
    assert wav.startswith(b"RIFF")


def test_key_normalization_and_combo_parsing() -> None:
    """Перевіряє канонічні назви клавіш та розбір комбінацій."""
    assert normalize_key_token("right_ctrl") == "ctrl_r"
    assert normalize_key_token("Right Ctrl") == "ctrl_r"
    assert parse_combo("ctrl+alt+d") == frozenset({"ctrl", "alt", "d"})
    assert "ctrl" in key_to_tokens(keyboard.Key.ctrl_r)
    assert "ctrl_r" in key_to_tokens(keyboard.Key.ctrl_r)
    assert "d" in key_to_tokens(keyboard.KeyCode.from_char("d"))
    # Керівний символ Ctrl+D (\x04) також має розпізнаватися як 'd'
    assert "d" in key_to_tokens(keyboard.KeyCode.from_char("\x04"))


async def test_hotkey_listener_emits_press_release_and_combo() -> None:
    """Перевіряє генерацію подій VOICE_PRESS, VOICE_RELEASE та EXPLAIN_CLIPBOARD без polling."""
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[HotkeyEvent] = asyncio.Queue()
    cfg = HotkeysConfig(voice="right_ctrl", explain_clipboard="ctrl+alt+d", cancel="esc")

    listener = HotkeyListener(config=cfg, loop=loop, queue=queue)

    # Утримання та відпускання Right Ctrl (повторний press при утриманні ігнорується)
    listener.on_press(keyboard.Key.ctrl_r)
    listener.on_press(keyboard.Key.ctrl_r)
    listener.on_release(keyboard.Key.ctrl_r)

    # Комбінація Ctrl+Alt+D
    listener.on_press(keyboard.Key.ctrl_l)
    listener.on_press(keyboard.Key.alt_l)
    listener.on_press(keyboard.KeyCode.from_char("d"))
    listener.on_release(keyboard.KeyCode.from_char("d"))
    listener.on_release(keyboard.Key.alt_l)
    listener.on_release(keyboard.Key.ctrl_l)

    await asyncio.sleep(0.01)
    events = []
    while not queue.empty():
        events.append(queue.get_nowait().type)

    assert events == [
        HotkeyEventType.VOICE_PRESS,
        HotkeyEventType.VOICE_RELEASE,
        HotkeyEventType.EXPLAIN_CLIPBOARD,
    ]


async def test_hold_to_talk_pipeline_and_latency(tmp_path: Path) -> None:
    """Перевіряє повний цикл Hold-to-Talk у FridayApp та бюджет затримки < 100 мс."""
    cfg = AppConfig()
    cfg.audio.tail_ms = 20
    cfg.audio.min_ms = 100

    chunk = np.full(8000, 3000, dtype=np.int16)  # 500 мс аудіо
    recorder = AudioRecorder(
        config=cfg.audio,
        stream_factory=lambda **kw: FakeInputStream(chunks_to_push=[chunk], **kw),
        temp_dir=tmp_path,
    )
    beeps: list[tuple[int, int]] = []
    feedback = FeedbackManager(
        enabled_sound=True,
        enabled_toast=False,
        beep_fn=lambda f, d: beeps.append((f, d)),
    )

    class DummyTranscriber:
        async def transcribe(self, wav_bytes: bytes) -> str:
            return ""

    app = FridayApp(
        config=cfg,
        recorder=recorder,
        feedback=feedback,
        transcriber=DummyTranscriber(),
    )

    t0 = time.perf_counter()
    await app.handle_event(HotkeyEvent(type=HotkeyEventType.VOICE_PRESS, timestamp=t0))
    press_latency_ms = (time.perf_counter() - t0) * 1000.0

    assert app.state == AssistantState.LISTENING
    assert press_latency_ms < 100.0  # ціль з розділу 11: < 100 мс

    await app.handle_event(HotkeyEvent(type=HotkeyEventType.VOICE_RELEASE))
    await asyncio.sleep(0.02)

    assert app.state == AssistantState.IDLE
    assert app.last_audio is not None
    assert (tmp_path / "latest.wav").exists()
    assert len(beeps) == 0

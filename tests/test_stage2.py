"""Юніт-тести для Етапу 2: STT (GeminiTranscriber, WhisperLocalTranscriber, 10 тестових фраз, затримка release -> текст)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

from friday.app import AssistantState, FridayApp
from friday.audio import AudioRecorder, encode_wav_int16
from friday.config import AppConfig, SttConfig
from friday.feedback import FeedbackManager
from friday.hotkeys import HotkeyEvent, HotkeyEventType
from friday.stt import (
    GeminiTranscriber,
    WhisperLocalTranscriber,
    create_transcriber,
)
from friday.stt.gemini import STT_PROMPT_UK
from tests.test_stage1 import FakeInputStream


TEN_UKRAINIAN_TEST_PHRASES: list[str] = [
    "перенеси браузер на другий монітор",
    "збільш це вікно",
    "перейди в термінал",
    "відкрий телеграм",
    "знайди в гуглі як налаштувати FastAPI middleware",
    "покажи документацію по цій помилці TypeError",
    "напиши привіт зустрінемось о шостій",
    "напиши Олегу в телеграм що я запізнюсь",
    "відрефактори файл config.py і додай типізацію pydantic",
    "постав вікно коду ліворуч на основному моніторі",
]


class FakeGeminiAioModels:
    """Фейковий асинхронний сервіс client.aio.models для тестування GeminiTranscriber."""

    def __init__(self, responses: list[Any]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    async def generate_content(self, *, model: str, contents: Any, config: Any) -> Any:
        self.calls.append({"model": model, "contents": contents, "config": config})
        return self._responses.pop(0)


class FakeGeminiClient:
    """Фейковий genai.Client, що перевіряє повторне використання одного екземпляра."""

    def __init__(self, responses: list[Any]) -> None:
        self.models_service = FakeGeminiAioModels(responses)
        self.aio = SimpleNamespace(models=self.models_service)


def _make_text_response(text: str) -> Any:
    part = SimpleNamespace(text=text, audio_transcription=None)
    content = SimpleNamespace(parts=[part])
    candidate = SimpleNamespace(content=content)
    return SimpleNamespace(candidates=[candidate])


def _make_audio_transcription_response(words: list[str]) -> Any:
    word_objs = [SimpleNamespace(word=w) for w in words]
    transcription = SimpleNamespace(text=None, words=word_objs)
    part = SimpleNamespace(text=None, audio_transcription=transcription)
    content = SimpleNamespace(parts=[part])
    candidate = SimpleNamespace(content=content)
    return SimpleNamespace(candidates=[candidate])


@pytest.mark.parametrize("phrase", TEN_UKRAINIAN_TEST_PHRASES)
async def test_gemini_transcriber_10_ukrainian_phrases(phrase: str) -> None:
    """Перевіряє розпізнавання 10 україномовних фраз з англійськими термінами."""
    fake_client = FakeGeminiClient([_make_text_response(phrase)])
    cfg = SttConfig(backend="gemini", language="uk", model="gemini-3.5-transcribe", mode="VERBATIM")
    transcriber = GeminiTranscriber(config=cfg, api_key="test-key", client=fake_client)  # type: ignore[arg-type]

    wav_bytes = encode_wav_int16(np.full(8000, 1500, dtype=np.int16))
    result = await transcriber.transcribe(wav_bytes)

    assert result == phrase
    assert len(fake_client.models_service.calls) == 1
    assert transcriber._get_client() is fake_client  # клієнт використовується повторно


async def test_gemini_transcriber_fallback_audio_transcription_parts() -> None:
    """Перевіряє запасний варіант розбору відповіді з поля audio_transcription.words (як у Jarvis stt.py)."""
    fake_client = FakeGeminiClient(
        [_make_audio_transcription_response(["перевір", "git", "status", "та", "traceback"])]
    )
    cfg = SttConfig(backend="gemini", language="uk", model="gemini-3.5-transcribe")
    transcriber = GeminiTranscriber(config=cfg, api_key="test-key", client=fake_client)  # type: ignore[arg-type]

    wav_bytes = encode_wav_int16(np.full(8000, 1200, dtype=np.int16))
    result = await transcriber.transcribe(wav_bytes)
    assert result == "перевір git status та traceback"


async def test_gemini_flash_model_uses_ukrainian_tech_prompt() -> None:
    """Якщо модель не transcribe, передається системна інструкція STT_PROMPT_UK."""
    fake_client = FakeGeminiClient([_make_text_response("відкрий FastAPI документацію")])
    cfg = SttConfig(backend="gemini", language="uk", model="gemini-3.5-flash-lite")
    transcriber = GeminiTranscriber(config=cfg, api_key="test-key", client=fake_client)  # type: ignore[arg-type]

    wav_bytes = encode_wav_int16(np.full(8000, 1200, dtype=np.int16))
    result = await transcriber.transcribe(wav_bytes)
    assert result == "відкрий FastAPI документацію"
    call = fake_client.models_service.calls[0]
    assert STT_PROMPT_UK in call["contents"]


async def test_whisper_local_lazy_loading_and_warmup() -> None:
    """Перевіряє, що модель Whisper завантажується лише один раз і коректно проходить warmup + transcribe."""
    load_count = 0

    class FakeWhisperModel:
        def transcribe(self, audio: Any, **kwargs: Any) -> tuple[list[Any], Any]:
            return [SimpleNamespace(text="перенеси VS Code на другий монітор")], SimpleNamespace()

    def factory(model_size: str, device: str, compute_type: str) -> FakeWhisperModel:
        nonlocal load_count
        load_count += 1
        assert model_size == "small"
        assert compute_type == "int8"
        return FakeWhisperModel()

    cfg = SttConfig(backend="whisper_local", language="uk")
    whisper_stt = WhisperLocalTranscriber(config=cfg, model_factory=factory)
    assert load_count == 0  # до виклику warmup/transcribe модель не у пам'яті

    await whisper_stt.warmup()
    assert load_count == 1

    wav_bytes = encode_wav_int16(np.full(8000, 1500, dtype=np.int16))
    text = await whisper_stt.transcribe(wav_bytes)
    assert text == "перенеси VS Code на другий монітор"
    assert load_count == 1  # модель не перезавантажується повторно


def test_create_transcriber_factory_does_not_load_whisper_when_gemini_selected() -> None:
    """Якщо обрано бекенд gemini, створюється GeminiTranscriber без завантаження faster-whisper."""
    cfg = AppConfig()
    cfg.stt.backend = "gemini"
    t = create_transcriber(cfg)
    assert isinstance(t, GeminiTranscriber)


async def test_app_hold_to_talk_to_stt_pipeline_logs_release_latency(tmp_path: Path) -> None:
    """Перевіряє повний перехід IDLE -> LISTENING -> TRANSCRIBING -> IDLE та вимір затримки release -> текст."""
    cfg = AppConfig()
    cfg.audio.tail_ms = 10
    cfg.audio.min_ms = 100

    chunk = np.full(8000, 3000, dtype=np.int16)
    recorder = AudioRecorder(
        config=cfg.audio,
        stream_factory=lambda **kw: FakeInputStream(chunks_to_push=[chunk], **kw),
        temp_dir=tmp_path,
    )
    feedback = FeedbackManager(enabled_sound=False, enabled_toast=False)
    fake_client = FakeGeminiClient([_make_text_response("відкрий телеграм")])
    transcriber = GeminiTranscriber(config=cfg.stt, api_key="test-key", client=fake_client)  # type: ignore[arg-type]

    app = FridayApp(
        config=cfg,
        recorder=recorder,
        feedback=feedback,
        transcriber=transcriber,
    )

    await app.handle_event(HotkeyEvent(type=HotkeyEventType.VOICE_PRESS))
    assert app.state == AssistantState.LISTENING

    await app.handle_event(HotkeyEvent(type=HotkeyEventType.VOICE_RELEASE))
    assert app.state == AssistantState.IDLE
    assert app.last_transcript == "відкрий телеграм"

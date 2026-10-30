from __future__ import annotations

import pytest

from friday.config import TtsConfig
from friday.tts.engine import create_tts_engine
from friday.tts.player import generate_chime
from friday.tts.sanitizer import sanitize_for_tts, split_sentences


def test_sanitize_for_tts() -> None:
    text = "Here is `$100` code: ```python\nprint(1)\n``` and [Docs](https://example.com/api)!"
    cleaned = sanitize_for_tts(text)
    assert "100 dollars" in cleaned
    assert "print(1)" not in cleaned
    assert "Docs" in cleaned
    assert "https://" not in cleaned


def test_split_sentences() -> None:
    text = "Starting the app. Please wait a moment! All systems ready."
    sentences = split_sentences(text)
    assert len(sentences) == 3
    assert sentences[0] == "Starting the app."


def test_generate_chime() -> None:
    chime = generate_chime([440.0, 880.0], duration_s=0.05, sample_rate=24000)
    assert isinstance(chime, bytes)
    assert len(chime) > 0


def test_create_tts_engine_config() -> None:
    cfg = TtsConfig(enabled=True, engine="edge", voice="en-US-AriaNeural")
    engine = create_tts_engine(cfg)
    assert engine is not None
    assert engine.config.voice == "en-US-AriaNeural"

    disabled_cfg = TtsConfig(enabled=False)
    assert create_tts_engine(disabled_cfg) is None

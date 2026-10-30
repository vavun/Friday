import asyncio

import pytest

from friday.app import AssistantState, FridayApp, build_spoken_confirmation
from friday.config import AppConfig, TtsConfig
from friday.hotkeys import HotkeyEvent, HotkeyEventType
from friday.router import CommandIntent
from friday.skills.base import SkillRegistry
from friday.tts.engine import create_tts_engine
from friday.tts.player import generate_chime
from friday.tts.sanitizer import sanitize_for_tts, split_sentences


class MockRecorder:
    def __init__(self) -> None:
        self.recording = False
        self.last_saved_path = None

    def start(self) -> bool:
        self.recording = True
        return True

    def stop(self) -> bytes | None:
        self.recording = False
        return b""


class MockTtsEngine:
    def __init__(self) -> None:
        self.spoken: list[str] = []

    async def synthesize_pcm(self, text: str) -> bytes | None:
        self.spoken.append(text)
        return b"pcm_" + text.encode()

    async def warmup(self) -> None:
        pass


class MockAudioPlayer:
    def __init__(self) -> None:
        self.played: list[bytes] = []
        self.aborted = False
        self.closed = False

    def play_audio(self, audio_data: bytes) -> None:
        self.played.append(audio_data)

    def abort(self) -> None:
        self.aborted = True

    def close(self) -> None:
        self.closed = True


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


def test_spoken_confirmation_prompts() -> None:
    assert build_spoken_confirmation("telegram.send", {"contact": "Alex"}) == "Send message to Alex?"
    assert build_spoken_confirmation("agent.run", {}) == "Start agent session?"
    assert build_spoken_confirmation("skill.create", {"name": "test"}) == "Create and run skill test?"
    assert build_spoken_confirmation("pipeline.save", {"name": "pipe"}) == "Save pipeline pipe?"
    assert build_spoken_confirmation("custom.action", {}) == "Confirm custom.action?"


@pytest.mark.asyncio
async def test_app_speak_and_playback_loop() -> None:
    tts = MockTtsEngine()
    player = MockAudioPlayer()
    app = FridayApp(config=AppConfig(), tts_engine=tts, audio_player=player)

    await app.speak("Hello Friday")
    assert tts.spoken == ["Hello Friday"]
    assert not app.audio_queue.empty()

    await app.audio_queue.put(None)
    await app._playback_loop()
    assert player.played == [b"pcm_Hello Friday"]


@pytest.mark.asyncio
async def test_app_barge_in_clears_queue_and_aborts_player() -> None:
    tts = MockTtsEngine()
    player = MockAudioPlayer()
    recorder = MockRecorder()
    app = FridayApp(config=AppConfig(), recorder=recorder, tts_engine=tts, audio_player=player)

    await app.audio_queue.put(b"chunk1")
    await app.audio_queue.put(b"chunk2")
    assert app.audio_queue.qsize() == 2

    await app.handle_event(HotkeyEvent(type=HotkeyEventType.VOICE_PRESS, timestamp=0.0))
    assert app.audio_queue.empty()
    assert player.aborted is True
    assert app.state == AssistantState.LISTENING


@pytest.mark.asyncio
async def test_app_voice_confirm_and_cancel_feedback() -> None:
    tts = MockTtsEngine()
    player = MockAudioPlayer()
    app = FridayApp(config=AppConfig(), tts_engine=tts, audio_player=player)

    class _DummyRouter:
        async def route(self, _text: str) -> CommandIntent:
            return CommandIntent(skill="telegram.send", args={"contact": "Alex", "text": "Hi"}, needs_confirm=True)

    app.router = _DummyRouter()  # type: ignore[assignment]
    await app._route_and_dispatch("send telegram")
    assert app.state == AssistantState.CONFIRMING

    await asyncio.sleep(0.02)
    assert "Send message to Alex?" in tts.spoken

    # Esc cancel feedback
    await app.handle_event(HotkeyEvent(type=HotkeyEventType.CANCEL))
    assert app.state == AssistantState.IDLE
    await asyncio.sleep(0.02)
    assert "Cancelled." in tts.spoken


@pytest.mark.asyncio
async def test_app_voice_clarify_and_error_feedback() -> None:
    tts = MockTtsEngine()
    player = MockAudioPlayer()
    app = FridayApp(config=AppConfig(), tts_engine=tts, audio_player=player)

    class _ClarifyRouter:
        async def route(self, _text: str) -> CommandIntent:
            return CommandIntent(skill="clarify", args={"question": "Which monitor?"})

    app.router = _ClarifyRouter()  # type: ignore[assignment]
    await app._route_and_dispatch("move window")
    await asyncio.sleep(0.02)
    assert "Which monitor?" in tts.spoken

    class _FailingSkill:
        name = "text.type"

        async def execute(self, _args: dict) -> None:
            raise RuntimeError("Skill failed")

    reg = SkillRegistry()
    reg.register(_FailingSkill())
    app.registry = reg
    await app._execute_intent(CommandIntent(skill="text.type", args={}))
    await asyncio.sleep(0.02)
    assert any("text.type" in s for s in tts.spoken)

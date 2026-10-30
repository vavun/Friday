"""Tests for bidirectional Jarvis <-> FRIDAY bridge and routing."""

from __future__ import annotations

from typing import Any

from friday.app import FridayApp
from friday.config import AppConfig
from friday.feedback import FeedbackManager
from friday.router.rules import match_fast_rule


class FakeTtsEngine:
    def __init__(self) -> None:
        self.spoken: list[str] = []

    async def synthesize_pcm(self, text: str) -> bytes | None:
        self.spoken.append(text)
        return b"\x00\x00" * 160


class FakeAudioPlayer:
    def __init__(self) -> None:
        self.played: list[bytes] = []

    def play_audio(self, data: bytes) -> None:
        self.played.append(data)

    def abort(self) -> None:
        pass

    def close(self) -> None:
        pass


def test_fast_rule_routes_jarvis_requests() -> None:
    intent1 = match_fast_rule("Ask Jarvis what notes are in my vault")
    assert intent1 is not None
    assert intent1.skill == "assistant.jarvis"
    assert intent1.args["discuss"] is False

    intent2 = match_fast_rule("Поговори з Джарвісом про архітектуру проєкту")
    assert intent2 is not None
    assert intent2.skill == "assistant.jarvis"
    assert intent2.args["discuss"] is True


def test_fast_rule_ignores_incidental_jarvis_mentions() -> None:
    intent = match_fast_rule("відкрий папку jarvis")
    assert intent is None or intent.skill != "assistant.jarvis"


async def test_peer_request_with_risky_skill_requires_confirmation() -> None:
    from friday.app import AssistantState
    from friday.router.schema import CommandIntent

    cfg = AppConfig()
    feedback = FeedbackManager(enabled_sound=False, enabled_toast=False)
    app = FridayApp(config=cfg, feedback=feedback, tts_engine=FakeTtsEngine(), audio_player=FakeAudioPlayer())  # type: ignore[arg-type]
    executed: list[Any] = []

    class SpySkill:
        name = "telegram.send"

        async def execute(self, args: dict[str, Any]) -> Any:
            executed.append(args)
            return {}

    app.registry.register(SpySkill())  # type: ignore[arg-type]
    intent = CommandIntent(skill="telegram.send", args={"contact": "oleg", "text": "hi"})
    summary = await app.execute_intent_from_peer(intent)

    assert executed == []
    assert app.state == AssistantState.CONFIRMING
    assert "confirm" in summary.lower()


async def test_jarvis_offline_fallback_uses_male_voice() -> None:
    cfg = AppConfig()
    feedback = FeedbackManager(enabled_sound=False, enabled_toast=False)
    app = FridayApp(config=cfg, feedback=feedback, tts_engine=FakeTtsEngine(), audio_player=FakeAudioPlayer())  # type: ignore[arg-type]

    def offline_post(url: str, payload: dict[str, Any], timeout_s: float = 45.0) -> dict[str, Any]:
        raise ConnectionRefusedError("offline")

    async def fake_fallback(msg: str) -> str:
        return "I hear you, Friday."

    synthesized: list[str] = []

    async def fake_synth(self: Any, text: str) -> bytes:
        synthesized.append(f"{self.config.voice}:{text}")
        return b"\x00\x00" * 10

    app.bridge._http_post = offline_post
    app.bridge._generate_jarvis_fallback_reply = fake_fallback  # type: ignore[method-assign]
    from friday.tts.engine import EdgeTtsEngine

    orig = EdgeTtsEngine.synthesize_pcm
    EdgeTtsEngine.synthesize_pcm = fake_synth  # type: ignore[method-assign]
    try:
        res = await app.bridge.call_jarvis({"message": "Ask Jarvis to say hi"})
    finally:
        EdgeTtsEngine.synthesize_pcm = orig  # type: ignore[method-assign]

    assert res["jarvis_reply"] == "I hear you, Friday."
    assert any(s.startswith(f"{cfg.bridge.jarvis_voice}:") for s in synthesized)


async def test_friday_calls_jarvis_over_bridge() -> None:
    cfg = AppConfig()
    tts = FakeTtsEngine()
    player = FakeAudioPlayer()
    feedback = FeedbackManager(enabled_sound=False, enabled_toast=False)
    app = FridayApp(config=cfg, feedback=feedback, tts_engine=tts, audio_player=player)  # type: ignore[arg-type]

    sent_payloads: list[dict[str, Any]] = []

    def fake_post(url: str, payload: dict[str, Any], timeout_s: float = 45.0) -> dict[str, Any]:
        sent_payloads.append({"url": url, "payload": payload})
        return {"ok": True, "reply": "Sure Friday, I checked the vault.", "spoken": True}

    app.bridge._http_post = fake_post
    res = await app.bridge.call_jarvis(
        {"message": "Ask Jarvis to check my vault notes", "discuss": False}
    )

    assert res["status"] == "ok"
    assert "Sure Friday" in res["jarvis_reply"]
    assert len(sent_payloads) == 1
    assert sent_payloads[0]["payload"]["sender"] == "friday"
    assert any("Jarvis" in s for s in tts.spoken)


async def test_friday_handles_incoming_request_from_jarvis() -> None:
    cfg = AppConfig()
    tts = FakeTtsEngine()
    player = FakeAudioPlayer()
    feedback = FeedbackManager(enabled_sound=False, enabled_toast=False)
    app = FridayApp(config=cfg, feedback=feedback, tts_engine=tts, audio_player=player)  # type: ignore[arg-type]

    async def fake_reply(msg: str) -> str:
        return "I am ready to help you, Jarvis."

    app.bridge._generate_friday_reply_to_jarvis = fake_reply  # type: ignore[method-assign]
    res = await app.bridge.handle_incoming_from_jarvis(
        {"sender": "jarvis", "message": "How are systems looking, Friday?", "speak": True}
    )

    assert res["ok"] is True
    assert "Jarvis" in res["reply"]
    assert any("Jarvis" in s for s in tts.spoken)


async def test_friday_bridge_http_server_roundtrip() -> None:
    cfg = AppConfig()
    cfg.bridge.friday_port = 18766
    tts = FakeTtsEngine()
    player = FakeAudioPlayer()
    feedback = FeedbackManager(enabled_sound=False, enabled_toast=False)
    app = FridayApp(config=cfg, feedback=feedback, tts_engine=tts, audio_player=player)  # type: ignore[arg-type]

    async def fake_reply(msg: str) -> str:
        return "Hello Jarvis, systems are online."

    app.bridge._generate_friday_reply_to_jarvis = fake_reply  # type: ignore[method-assign]
    await app.bridge.start()
    try:
        from friday.bridge import _http_post_json
        import asyncio

        resp = await asyncio.to_thread(
            _http_post_json,
            "http://127.0.0.1:18766/api/assist",
            {"sender": "jarvis", "message": "Status check", "speak": True},
            5.0,
        )
        assert resp["ok"] is True
        assert "Hello Jarvis" in resp["reply"]
    finally:
        await app.bridge.stop()


def test_is_dialogue_concluded_heuristics() -> None:
    from friday.bridge import is_dialogue_concluded

    assert is_dialogue_concluded("That covers everything, thank you!") is True
    assert is_dialogue_concluded("Sounds like a plan. Let's proceed.") is True
    assert is_dialogue_concluded("We are all set then.") is True
    assert is_dialogue_concluded("Nothing more to add on my end.") is True
    assert is_dialogue_concluded("We're good to go.") is True
    assert is_dialogue_concluded("That wraps it up.") is True
    assert is_dialogue_concluded("Talk to you soon.") is True
    assert is_dialogue_concluded("Have a great rest of your day!") is True
    assert is_dialogue_concluded("[DONE]") is True

    # Questions must NOT be treated as concluded even with trigger words
    assert is_dialogue_concluded("Does that sound like a plan to you?") is False
    assert is_dialogue_concluded("Are we all set?") is False
    assert is_dialogue_concluded("Sounds like a plan. What do you think?") is False
    assert is_dialogue_concluded("What do you think about the schema?") is False
    assert is_dialogue_concluded("I checked the vault and found 3 notes.") is False


async def test_continuous_dialogue_stops_when_concluded() -> None:
    cfg = AppConfig()
    cfg.bridge.max_discussion_turns = 10
    tts = FakeTtsEngine()
    player = FakeAudioPlayer()
    feedback = FeedbackManager(enabled_sound=False, enabled_toast=False)
    app = FridayApp(config=cfg, feedback=feedback, tts_engine=tts, audio_player=player)  # type: ignore[arg-type]

    jarvis_replies = [
        "I suggest using SQLite FTS5 for fast note indexing.",
        "That covers everything, Friday. We're all set.",
    ]
    turn_counter = 0

    def mock_post(url: str, payload: dict[str, Any], timeout_s: float = 45.0) -> dict[str, Any]:
        nonlocal turn_counter
        reply = jarvis_replies[min(turn_counter, len(jarvis_replies) - 1)]
        turn_counter += 1
        return {"ok": True, "reply": reply, "spoken": True}

    async def mock_friday_turn(dialogue_history: list[dict[str, str]], raw_topic: str, turn: int, max_turns: int) -> str:
        return "Sounds great Jarvis, does it support snippet extraction?"

    app.bridge._http_post = mock_post
    app.bridge._generate_friday_dialogue_turn = mock_friday_turn  # type: ignore[method-assign]

    res = await app.bridge.call_jarvis(
        {"message": "Discuss note search architecture with Jarvis", "discuss": True}
    )

    assert res["status"] == "ok"
    assert res["turns_completed"] == 2
    assert "covers everything" in res["history"][-1]["content"]


async def test_friday_bridge_handles_cancel_endpoint() -> None:
    cfg = AppConfig()
    cfg.bridge.friday_port = 18767
    tts = FakeTtsEngine()
    player = FakeAudioPlayer()
    feedback = FeedbackManager(enabled_sound=False, enabled_toast=False)
    app = FridayApp(config=cfg, feedback=feedback, tts_engine=tts, audio_player=player)  # type: ignore[arg-type]

    app.bridge._dialogue_active = True
    await app.bridge.start()
    try:
        from friday.bridge import _http_post_json
        import asyncio

        resp = await asyncio.to_thread(
            _http_post_json,
            "http://127.0.0.1:18767/api/cancel",
            {"source": "jarvis"},
            5.0,
        )
        assert resp["status"] == "cancelled"
        assert resp["assistant"] == "friday"
        assert app.bridge._dialogue_cancelled is True
        assert app.bridge._dialogue_active is False
    finally:
        await app.bridge.stop()

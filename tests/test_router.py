"""Тести Етапу 4 — Роутер (Fast path rules + Smart path LLM parser + корпус із 35 україномовних фраз)."""

from __future__ import annotations

import json
import time
from typing import Any

import pytest

from friday.app import AssistantState, FridayApp
from friday.config import AppConfig
from friday.hotkeys import HotkeyEvent, HotkeyEventType
from friday.router import (
    CommandIntent,
    CommandRouter,
    LlmCommandParser,
    match_fast_rule,
)
from friday.skills.base import SkillRegistry


# Корпус із 35 тестових фраз українською мовою (Fast path + Smart path)
TEST_CORPUS_35_PHRASES: list[tuple[str, str, dict[str, Any], str]] = [
    # --- Fast path (30 фраз = 85.7% >= 80%) ---
    ("перенеси браузер на другий монітор", "window.move", {"target": "browser", "monitor": 2, "mode": "maximize"}, "fast"),
    ("перекинь телеграм на перший монітор", "window.move", {"target": "telegram", "monitor": 1, "mode": "maximize"}, "fast"),
    ("відправ редактор на третій монітор", "window.move", {"target": "code", "monitor": 3, "mode": "maximize"}, "fast"),
    ("перемісти термінал на інший монітор", "window.move", {"target": "terminal", "monitor": 2, "mode": "maximize"}, "fast"),
    ("перенеси хром на сусідній монітор ліворуч", "window.move", {"target": "browser", "monitor": 2, "mode": "left"}, "fast"),
    ("постав вікно коду ліворуч на основному моніторі", "window.move", {"target": "code", "monitor": 1, "mode": "left"}, "fast"),
    ("прикріпи браузер праворуч на другому моніторі", "window.move", {"target": "browser", "monitor": 2, "mode": "right"}, "fast"),
    ("розмісти телеграм по центру", "window.move", {"target": "telegram", "monitor": 1, "mode": "center"}, "fast"),
    ("постав це вікно вправо", "window.move", {"target": "active", "monitor": 1, "mode": "right"}, "fast"),
    ("збільш це вікно", "window.resize", {"target": "active", "mode": "bigger"}, "fast"),
    ("зменш вікно браузера", "window.resize", {"target": "browser", "mode": "smaller"}, "fast"),
    ("розгорни редактор", "window.resize", {"target": "code", "mode": "maximize"}, "fast"),
    ("віднови це вікно", "window.resize", {"target": "active", "mode": "restore"}, "fast"),
    ("максимізуй термінал", "window.resize", {"target": "terminal", "mode": "maximize"}, "fast"),
    ("перейди в термінал", "window.focus", {"target": "terminal"}, "fast"),
    ("переключись на браузер", "window.focus", {"target": "browser"}, "fast"),
    ("сфокусуй телеграм", "window.focus", {"target": "telegram"}, "fast"),
    ("активуй вікно редактора", "window.focus", {"target": "code"}, "fast"),
    ("відкрий телеграм", "app.launch", {"name": "telegram"}, "fast"),
    ("запусти браузер", "app.launch", {"name": "browser"}, "fast"),
    ("увімкни вс код", "app.launch", {"name": "code"}, "fast"),
    ("режим жестів", "gesture.drag", {"action": "start"}, "fast"),
    ("вимкни жести", "gesture.drag", {"action": "stop"}, "fast"),
    ("знайди в гуглі як налаштувати FastAPI middleware", "web.search", {"query": "як налаштувати FastAPI middleware", "site": "fastapi"}, "fast"),
    ("загугли документацію pydantic BaseModel", "web.search", {"query": "документацію pydantic BaseModel", "site": "pydantic"}, "fast"),
    ("пошукай в інтернеті asyncio gather python", "web.search", {"query": "asyncio gather python", "site": "python"}, "fast"),
    ("покажи документацію по цій помилці TypeError", "docs.lookup", {"source": "clipboard", "error": "TypeError"}, "fast"),
    ("поясни помилку з буфера обміну", "docs.lookup", {"source": "clipboard"}, "fast"),
    ("напиши: привіт, зустрінемось о шостій", "text.type", {"text": "привіт, зустрінемось о шостій"}, "fast"),
    ("напиши Олегу в телеграм що я запізнюсь на 10 хвилин", "telegram.send", {"contact": "олегу", "text": "я запізнюсь на 10 хвилин"}, "fast"),
    # --- Smart path (5 фраз = 14.3% для складних / вільних формулювань) ---
    ("слухай, мені треба глянути що там у хромові на другому екрані", "window.move", {"target": "browser", "monitor": 2, "mode": "maximize"}, "smart"),
    ("чи не міг би ти зробити поточне віконце трохи компактнішим", "window.resize", {"target": "active", "mode": "smaller"}, "smart"),
    ("мені потрібна свіжа інфа про WebSockets у MDN", "web.search", {"query": "WebSockets", "site": "mdn"}, "smart"),
    ("будь ласка, маякни Марині у телеграмі що реліз готовий", "telegram.send", {"contact": "Марина", "text": "реліз готовий"}, "smart"),
    ("подивись у проект і запропонуй архітектуру кешування для роутера", "agent.run", {"prompt": "запропонуй архітектуру кешування для роутера"}, "smart"),
]


@pytest.mark.asyncio
async def test_router_corpus_35_phrases_and_fast_path_ratio() -> None:
    """Перевіряє корпус із 35 фраз: >= 80% через fast path, валідація схеми та швидкість < 150 мс."""
    config = AppConfig(apps={"code": "code", "telegram": "telegram.exe", "browser": "chrome.exe"})

    smart_mock_map: dict[str, dict[str, Any]] = {
        phrase: {
            "skill": exp_skill,
            "args": exp_args,
            "confidence": 0.91,
            "needs_confirm": exp_skill in {"telegram.send", "agent.run"},
        }
        for phrase, exp_skill, exp_args, exp_source in TEST_CORPUS_35_PHRASES
        if exp_source == "smart"
    }

    def fake_llm_generate(text: str, _sys_prompt: str) -> str:
        payload = smart_mock_map[text]
        return json.dumps(payload, ensure_ascii=False)

    llm_parser = LlmCommandParser(config=config, generate_fn=fake_llm_generate)
    router = CommandRouter(config=config, llm_parser=llm_parser)

    fast_count = 0
    fast_durations_ms: list[float] = []

    for phrase, expected_skill, expected_args, expected_source in TEST_CORPUS_35_PHRASES:
        t0 = time.perf_counter()
        intent = await router.route(phrase)
        dt_ms = (time.perf_counter() - t0) * 1000.0

        assert isinstance(intent, CommandIntent)
        assert router.last_route_source == expected_source, f"Phrase '{phrase}' routed via {router.last_route_source}"
        assert intent.skill == expected_skill, f"Phrase '{phrase}' -> {intent.skill} != {expected_skill}"
        for k, v in expected_args.items():
            assert intent.args.get(k) == v, f"Phrase '{phrase}' arg '{k}': {intent.args.get(k)} != {v}"

        if intent.skill in {"telegram.send", "agent.run"}:
            assert intent.needs_confirm is True

        if router.last_route_source == "fast":
            fast_count += 1
            fast_durations_ms.append(dt_ms)

    total = len(TEST_CORPUS_35_PHRASES)
    fast_ratio = fast_count / total
    avg_fast_ms = sum(fast_durations_ms) / len(fast_durations_ms)
    max_fast_ms = max(fast_durations_ms)

    assert total >= 30
    assert fast_ratio >= 0.80, f"Fast path ratio {fast_ratio:.2%} < 80%"
    assert max_fast_ms < 150.0, f"Max fast path duration {max_fast_ms:.2f} ms >= 150 ms"
    assert avg_fast_ms < 10.0


@pytest.mark.asyncio
async def test_llm_parser_retry_on_invalid_json_and_low_confidence_clarify() -> None:
    """Перевіряє 1 ретрай при невалідному JSON, відсікання confidence < 0.7 та білий список програм."""
    config = AppConfig(apps={"code": "code", "browser": "chrome"})

    # 1. Перша відповідь бита, друга — валідний JSON у markdown-блоці
    calls = 0

    def flaky_generate(_text: str, _sys: str) -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            return "NOT A JSON AT ALL {"
        return '```json\n{"skill": "window.focus", "args": {"target": "browser"}, "confidence": 0.88, "needs_confirm": false}\n```'

    parser = LlmCommandParser(config=config, generate_fn=flaky_generate)
    intent = await parser.parse("покажи хром")
    assert calls == 2
    assert intent.skill == "window.focus"
    assert intent.args["target"] == "browser"

    # 2. Якщо обидві спроби невалідні -> повертає clarify
    bad_parser = LlmCommandParser(config=config, generate_fn=lambda _t, _s: "{broken_json")
    fallback = await bad_parser.parse("щось незрозуміле")
    assert fallback.skill == "clarify"
    assert fallback.confidence == 0.0

    # 3. Якщо confidence < 0.7 -> перетворюється на clarify
    low_conf_parser = LlmCommandParser(
        config=config,
        generate_fn=lambda _t, _s: json.dumps(
            {"skill": "window.move", "args": {"target": "code", "monitor": 2}, "confidence": 0.55, "needs_confirm": False}
        ),
    )
    low_intent = await low_conf_parser.parse("перекинь кудись")
    assert low_intent.skill == "clarify"

    # 4. Якщо програма у app.launch не з білого списку -> перетворюється на clarify
    unlisted_app_parser = LlmCommandParser(
        config=config,
        generate_fn=lambda _t, _s: json.dumps(
            {"skill": "app.launch", "args": {"name": "photoshop"}, "confidence": 0.95, "needs_confirm": False}
        ),
    )
    unlisted_intent = await unlisted_app_parser.parse("відкрий фотошоп")
    assert unlisted_intent.skill == "clarify"


@pytest.mark.asyncio
async def test_app_routing_and_confirm_gate_execution() -> None:
    """Перевіряє повний цикл у FridayApp: ROUTING -> CONFIRMING -> Enter (CONFIRM) -> EXECUTING -> IDLE."""
    config = AppConfig()
    executed_calls: list[dict[str, Any]] = []

    class _DummyTelegramSkill:
        name = "telegram.send"

        async def execute(self, args: dict[str, Any]) -> dict[str, Any]:
            executed_calls.append(args)
            return {"status": "sent"}

    registry = SkillRegistry()
    registry.register(_DummyTelegramSkill())

    app = FridayApp(config=config, registry=registry)

    # Маршрутизуємо команду, яка потребує підтвердження (telegram.send)
    await app._route_and_dispatch("напиши Олегу в телеграм що я буду о сьомій")
    assert app.state == AssistantState.CONFIRMING
    assert len(executed_calls) == 0

    # Натискання Enter (CONFIRM) виконує відкладену дію і повертає у IDLE
    await app.handle_event(HotkeyEvent(type=HotkeyEventType.CONFIRM))
    assert app.state == AssistantState.IDLE
    assert len(executed_calls) == 1
    assert executed_calls[0]["contact"] == "олегу"
    assert executed_calls[0]["text"] == "я буду о сьомій"

    # Тепер перевіримо скасування по Esc (CANCEL) у стані CONFIRMING
    await app._route_and_dispatch("напиши Олегу в телеграм що відбій")
    assert app.state == AssistantState.CONFIRMING
    await app.handle_event(HotkeyEvent(type=HotkeyEventType.CANCEL))
    assert app.state == AssistantState.IDLE
    assert len(executed_calls) == 1  # не виконано!


def test_fast_rule_refactor_agent_command() -> None:
    """Перевіряє детерміноване розпізнавання кодерського запиту до Antigravity CLI."""
    intent = match_fast_rule("відрефактори файл config.py і додай типізацію pydantic")
    assert intent is not None
    assert intent.skill == "agent.run"
    assert intent.needs_confirm is True

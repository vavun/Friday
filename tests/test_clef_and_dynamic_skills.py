"""Тести інтеграції Experiential Labs Clef (tool calling & pipelines) та динамічних навичок FRIDAY."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from friday.app import AssistantState, FridayApp
from friday.config import AppConfig, SecretsConfig
from friday.hotkeys import HotkeyEvent, HotkeyEventType
from friday.launcher.services.app_scanner import ScannedApp
from friday.launcher.services.config_store import ConfigStore
from friday.launcher.services.key_check import verify_explabs_api_key
from friday.router import ClefToolPlanner, CommandIntent, CommandRouter, LlmCommandParser, match_fast_rule
from friday.skills.apps import AppLauncher, AppLaunchSkill
from friday.skills.base import SkillRegistry
from friday.skills.dynamic import (
    AppDiscoverSkill,
    AppDiscoveryService,
    DynamicSkillManager,
    PipelineSaveSkill,
    SkillCreateSkill,
    SkillRunSkill,
)


def _make_fake_openai_response(
    tool_calls: list[tuple[str, dict[str, Any]]] | None = None,
    content: str | None = None,
) -> Any:
    """Створює імітацію відповіді OpenAI chat.completions.create для моделі Clef."""
    tc_objs = None
    if tool_calls:
        tc_objs = [
            SimpleNamespace(
                id=f"call_{idx}",
                type="function",
                function=SimpleNamespace(
                    name=fn_name,
                    arguments=json.dumps(fn_args, ensure_ascii=False),
                ),
            )
            for idx, (fn_name, fn_args) in enumerate(tool_calls)
        ]
    msg = SimpleNamespace(content=content, tool_calls=tc_objs)
    return SimpleNamespace(choices=[SimpleNamespace(message=msg)])


def test_clef_tool_planner_single_and_multi_step_tool_calls() -> None:
    """Перевіряє, що ClefToolPlanner перетворює один або кілька tool_calls у CommandIntent та steps."""
    captured_calls: list[dict[str, Any]] = []

    class _FakeCompletions:
        def create(self, **kwargs: Any) -> Any:
            captured_calls.append(kwargs)
            user_msg = kwargs["messages"][-1]["content"]
            if "спотіфай" in user_msg:
                return _make_fake_openai_response(
                    tool_calls=[
                        (
                            "app_discover",
                            {
                                "name": "спотіфай",
                                "candidates": ["spotify", "spotify.exe"],
                                "synonyms": ["спотіфай", "спотік"],
                                "launch": True,
                            },
                        ),
                        (
                            "window_move",
                            {"target": "spotify", "monitor": 2, "mode": "right"},
                        ),
                    ]
                )
            return _make_fake_openai_response(
                tool_calls=[("window_focus", {"target": "browser"})]
            )

    fake_client = SimpleNamespace(chat=SimpleNamespace(completions=_FakeCompletions()))
    config = AppConfig(secrets=SecretsConfig(explabs_api_key="explabs_test_key_1234"))
    planner = ClefToolPlanner(config=config, openai_client=fake_client)

    # 1. Одиночний виклик інструменту
    single = planner.plan_sync("покажи браузер")
    assert isinstance(single, CommandIntent)
    assert single.skill == "window.focus"
    assert single.args["target"] == "browser"
    assert len(single.steps) == 0
    assert captured_calls[-1]["model"] == "clef"
    assert len(captured_calls[-1]["tools"]) >= 10

    # 2. Мульти-кроковий пайплайн із кількох tool_calls (app_discover -> window_move)
    multi = planner.plan_sync("відкрий спотіфай і перенеси його праворуч на другий монітор")
    assert isinstance(multi, CommandIntent)
    assert len(multi.steps) == 2
    assert multi.steps[0].skill == "app.discover"
    assert multi.steps[0].args["name"] == "спотіфай"
    assert multi.steps[1].skill == "window.move"
    assert multi.steps[1].args["monitor"] == 2
    assert multi.steps[1].args["mode"] == "right"


@pytest.mark.asyncio
async def test_llm_parser_clef_primary_and_gemini_fallback() -> None:
    """Перевіряє роботу Clef як основної моделі та автоматичний фолбек на Gemini при помилці Clef."""
    config = AppConfig(
        apps={"code": "code", "browser": "chrome"},
        secrets=SecretsConfig(explabs_api_key="explabs_key", gemini_api_key="gemini_key"),
    )

    class _FailingClefClient:
        class chat:
            class completions:
                @staticmethod
                def create(**_kwargs: Any) -> Any:
                    raise ConnectionError("Clef endpoint temporarily unreachable")

    class _FakeGeminiModels:
        @staticmethod
        def generate_content(**_kwargs: Any) -> Any:
            return SimpleNamespace(
                text=json.dumps(
                    {
                        "skill": "window.focus",
                        "args": {"target": "code"},
                        "confidence": 0.92,
                        "needs_confirm": False,
                    }
                )
            )

    fake_gemini = SimpleNamespace(models=_FakeGeminiModels())
    clef_planner = ClefToolPlanner(config=config, openai_client=_FailingClefClient())
    parser = LlmCommandParser(config=config, client=fake_gemini, clef_planner=clef_planner)

    intent = await parser.parse("перемкни увагу на редактор коду")
    assert intent.skill == "window.focus"
    assert intent.args["target"] == "code"


@pytest.mark.asyncio
async def test_app_discover_saves_to_config_and_enables_fast_path(tmp_path: Path) -> None:
    """Перевіряє авто-пошук нової програми, збереження у config.local.toml та миттєвий Fast Path."""
    base_toml = tmp_path / "config.toml"
    local_toml = tmp_path / "config.local.toml"
    base_toml.write_text('[apps]\ncode = "code"\n', encoding="utf-8")

    fake_exe = tmp_path / "Spotify.exe"
    fake_exe.write_bytes(b"MZ_FAKE_SPOTIFY")

    store = ConfigStore(base_path=base_toml, local_path=local_toml)
    cfg = store.load_merged()

    launched_cmds: list[list[str]] = []
    launcher = AppLauncher(
        cfg.apps,
        popen_fn=lambda cmd, **_kw: launched_cmds.append(cmd),
        custom_synonyms=cfg.app_synonyms,
    )

    scanned_list = [
        ScannedApp(
            voice_name="spotify",
            display_name="Spotify Music",
            exe_path=str(fake_exe),
        )
    ]
    discovery = AppDiscoveryService(
        config=cfg,
        app_launcher=launcher,
        config_store=store,
        scanner_fn=lambda: scanned_list,
        which_fn=lambda _x: None,
    )
    discover_skill = AppDiscoverSkill(discovery, launcher)

    # Виконуємо відкриття нової програми "спотіфай", якої ще не було у [apps]
    res = await discover_skill.execute(
        {"name": "спотіфай", "synonyms": ["спотіфай", "музика"], "launch": True}
    )
    assert res["status"] == "launched"
    assert res["discovered"] is True
    assert res["app"] == "spotify"
    assert len(launched_cmds) == 1
    assert str(fake_exe) in launched_cmds[0][0]

    # Перевіряємо, що програма та її синонім збереглися у config.local.toml
    reloaded = store.load_merged()
    assert "spotify" in reloaded.apps
    assert reloaded.app_synonyms.get("спотіфай") == "spotify"
    assert reloaded.app_synonyms.get("музика") == "spotify"

    # Тепер голосова команда "відкрий спотіфай" розпізнається детермінованим Fast Path без LLM!
    fast_hit = match_fast_rule(
        "відкрий спотіфай",
        custom_apps=reloaded.apps,
        custom_synonyms=reloaded.app_synonyms,
    )
    assert fast_hit is not None
    assert fast_hit.skill == "app.launch"
    assert fast_hit.args["name"] == "spotify"


@pytest.mark.asyncio
async def test_pipeline_save_and_custom_skill_confirm_gate(tmp_path: Path) -> None:
    """Перевіряє збереження пайплайнів (pipeline.save) та створення навичок (skill.create) через Confirm Gate."""
    base_toml = tmp_path / "config.toml"
    local_toml = tmp_path / "config.local.toml"
    base_toml.write_text('[apps]\ncode = "code"\n', encoding="utf-8")

    store = ConfigStore(base_path=base_toml, local_path=local_toml)
    cfg = store.load_merged()

    executed_log: list[tuple[str, dict[str, Any]]] = []

    class _DummySkill:
        def __init__(self, name: str) -> None:
            self.name = name

        async def execute(self, args: dict[str, Any]) -> dict[str, Any]:
            executed_log.append((self.name, dict(args)))
            return {"status": "ok"}

    reg = SkillRegistry()
    reg.register(_DummySkill("app.launch"))
    reg.register(_DummySkill("window.move"))

    popens: list[list[str]] = []
    dyn_mgr = DynamicSkillManager(
        config=cfg,
        registry=reg,
        config_store=store,
        popen_fn=lambda cmd, **_kw: popens.append(cmd),
    )
    reg.register(PipelineSaveSkill(dyn_mgr))
    reg.register(SkillCreateSkill(dyn_mgr))
    reg.register(SkillRunSkill(dyn_mgr))

    # 1. Збереження та негайне виконання нового пайплайну
    pipe_skill = reg.get("pipeline.save")
    assert pipe_skill is not None
    await pipe_skill.execute(
        {
            "trigger": "робочий простір",
            "steps": [
                {"skill": "app.launch", "args": {"name": "code"}},
                {"skill": "window.move", "args": {"target": "code", "monitor": 2, "mode": "maximize"}},
            ],
            "run_now": True,
        }
    )
    assert len(executed_log) == 2
    assert "робочий простір" in store.load_merged().pipelines

    # Повторний голосовий виклик "запусти робочий простір" іде через Fast Path!
    fast_pipe = match_fast_rule(
        "запусти робочий простір",
        saved_pipelines=cfg.pipelines,
        saved_skills=cfg.custom_skills,
    )
    assert fast_pipe is not None
    assert len(fast_pipe.steps) == 2
    assert fast_pipe.steps[0].skill == "app.launch"
    assert fast_pipe.steps[1].skill == "window.move"

    # 2. Створення нової користувацької навички (skill.create) у FridayApp вимагає Enter / Esc підтвердження
    app = FridayApp(config=cfg, registry=reg)
    create_intent = CommandIntent(
        skill="skill.create",
        args={
            "name": "пінг сервера",
            "trigger": "перевір пінг",
            "action_type": "command",
            "command": "ping.exe 8.8.8.8 -n 1",
            "run_now": True,
        },
        confidence=0.95,
        needs_confirm=True,
    )
    app.router.route = lambda _t: _async_return(create_intent)  # type: ignore[method-assign]

    await app._route_and_dispatch("створи навичку перевір пінг")
    assert app.state == AssistantState.CONFIRMING
    assert len(popens) == 0

    # Підтверджуємо натисканням Enter
    await app.handle_event(HotkeyEvent(type=HotkeyEventType.CONFIRM))
    assert app.state == AssistantState.IDLE
    assert len(popens) == 1
    assert "пінг сервера" in store.load_merged().custom_skills

    # Наступного разу фраза "перевір пінг" миттєво розпізнається у Fast Path -> skill.run
    fast_custom = match_fast_rule(
        "перевір пінг",
        saved_pipelines=cfg.pipelines,
        saved_skills=cfg.custom_skills,
    )
    assert fast_custom is not None
    assert fast_custom.skill == "skill.run"
    assert fast_custom.args["name"] == "пінг сервера"


async def _async_return(val: Any) -> Any:
    return val


def test_verify_explabs_api_key_scenarios() -> None:
    """Перевіряє валідацію ключа EXPLABS_API_KEY (порожній, валідний, 401 та офлайн)."""
    assert verify_explabs_api_key("").ok is False

    class _FakeClient:
        def __init__(self, mode: str) -> None:
            self.chat = SimpleNamespace(
                completions=SimpleNamespace(create=lambda **_kw: self._call(mode))
            )

        @staticmethod
        def _call(mode: str) -> Any:
            if mode == "ok":
                return _make_fake_openai_response(content="ok")
            if mode == "401":
                raise RuntimeError("Error code: 401 - invalid_api_key")
            raise ConnectionError("Connection timed out")

    assert verify_explabs_api_key("exp_ok", client_factory=lambda _k: _FakeClient("ok")).ok is True
    res_401 = verify_explabs_api_key("exp_bad", client_factory=lambda _k: _FakeClient("401"))
    assert res_401.ok is False and res_401.status == "invalid_key"
    res_off = verify_explabs_api_key("exp_off", client_factory=lambda _k: _FakeClient("off"))
    assert res_off.ok is False and res_off.status == "offline"


@pytest.mark.asyncio
async def test_clef_system_prompt_pipeline_execute_and_flat_step_normalization() -> None:
    """Перевіряє, що Clef отримує власний tool-calling промпт, підтримує pipeline_execute, плоскі кроки та протягування цілей."""
    captured_messages: list[list[dict[str, Any]]] = []

    class _FakeClefCompletions:
        @staticmethod
        def create(**kwargs: Any) -> Any:
            captured_messages.append(kwargs["messages"])
            # Модель викликає pipeline_execute із плоскими кроками (без 'args') та займенником 'його'
            return _make_fake_openai_response(
                tool_calls=[
                    (
                        "pipeline_execute",
                        {
                            "steps": [
                                {
                                    "skill": "app_discover",
                                    "name": "спотіфай",
                                    "candidates": ["spotify", "spotify.exe"],
                                },
                                {
                                    "skill": "window_move",
                                    "target": "його",
                                    "monitor": "2",
                                    "direction": "left",
                                },
                            ]
                        },
                    )
                ]
            )

    fake_client = SimpleNamespace(chat=SimpleNamespace(completions=_FakeClefCompletions()))
    config = AppConfig(
        apps={"code": "code"},
        pipelines={"ранковий старт": [{"skill": "app.launch", "args": {"name": "code"}}]},
        secrets=SecretsConfig(explabs_api_key="explabs_key"),
    )
    planner = ClefToolPlanner(config=config, openai_client=fake_client)
    parser = LlmCommandParser(config=config, clef_planner=planner)

    intent = await parser.parse("відкрий спотіфай і перенеси його ліворуч на другий монітор")
    # Перевіряємо, що Clef отримав власний промпт зі списком збережених пайплайнів, а не JSON-only промпт Gemini
    sys_content = captured_messages[0][0]["content"]
    assert "модель Clef" in sys_content
    assert "ранковий старт" in sys_content

    assert len(intent.steps) == 2
    assert intent.steps[0].skill == "app.discover"
    assert intent.steps[0].args["name"] == "спотіфай"
    assert intent.steps[1].skill == "window.move"
    # Займенник 'його' автоматично замінено на канонічну ціль 'spotify' з першого кроку
    assert intent.steps[1].args["target"] == "spotify"
    assert intent.steps[1].args["monitor"] == 2
    assert intent.steps[1].args["mode"] == "left"


def test_fast_path_pipeline_save_and_pronoun_propagation() -> None:
    """Перевіряє детерміноване створення пайплайну (pipeline.save) та протягування займенників у Fast Path."""
    # 1. Протягування займенника 'його' у складеній команді Fast Path
    compound = match_fast_rule("запусти браузер а потім перенеси його на другий монітор")
    assert compound is not None
    assert len(compound.steps) == 2
    assert compound.steps[0].skill == "app.launch"
    assert compound.steps[1].skill == "window.move"
    assert compound.steps[1].args["target"] == "browser"

    # 2. Детерміноване збереження пайплайну через Fast Path без виклику LLM
    save_intent = match_fast_rule(
        "збережи пайплайн ранковий старт: відкрий браузер і перенеси його на другий монітор праворуч"
    )
    assert save_intent is not None
    assert save_intent.skill == "pipeline.save"
    assert save_intent.args["trigger"] == "ранковий старт"
    steps = save_intent.args["steps"]
    assert len(steps) == 2
    assert steps[0]["skill"] == "app.launch"
    assert steps[0]["args"]["name"] == "browser"
    assert steps[1]["skill"] == "window.move"
    assert steps[1]["args"]["target"] == "browser"
    assert steps[1]["args"]["monitor"] == 2
    assert steps[1]["args"]["mode"] == "right"


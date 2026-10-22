"""Юніт-тести для Етапу 0: конфігурація, таймінги, автомат станів, безпека."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from pydantic import ValidationError

from friday.app import AssistantState, FridayApp
from friday.config import AgentConfig, AppConfig, load_config, setup_logging
from friday.router.schema import CommandIntent
from friday.safety import is_dangerous_command
from friday.util.timing import TimingTracker


def test_load_default_config(tmp_path: Path) -> None:
    """Перевіряє завантаження конфігурації з TOML-файлу."""
    toml_file = tmp_path / "config.toml"
    toml_file.write_text(
        """
[hotkeys]
voice = "right_ctrl"

[audio]
sample_rate = 16000
max_seconds = 15

[apps]
code = "code"
""",
        encoding="utf-8",
    )
    env_file = tmp_path / ".env"
    env_file.write_text("GEMINI_API_KEY=test-secret-key\n", encoding="utf-8")

    cfg = load_config(config_path=toml_file, env_path=env_file)
    assert isinstance(cfg, AppConfig)
    assert cfg.hotkeys.voice == "right_ctrl"
    assert cfg.audio.max_seconds == 15
    assert cfg.apps["code"] == "code"
    assert "test-secret-key" not in repr(cfg.secrets)


def test_agent_auto_approve_forbidden() -> None:
    """Перевіряє, що auto_approve=True блокується валідатором."""
    with pytest.raises(ValidationError):
        AgentConfig(command="antigravity", auto_approve=True)


def test_timing_tracker_measures_duration() -> None:
    """Перевіряє контекстний менеджер вимірювання затримок."""
    tracker = TimingTracker()
    with tracker.measure("test_step") as rec:
        time.sleep(0.01)

    assert rec.stage == "test_step"
    assert rec.elapsed_ms >= 5.0
    assert len(tracker.records) == 1


def test_command_intent_schema_validation() -> None:
    """Перевіряє схему команд роутера."""
    cmd = CommandIntent(
        skill="window.move",
        args={"target": "browser", "monitor": 2, "mode": "maximize"},
        confidence=0.95,
        needs_confirm=False,
    )
    assert cmd.skill == "window.move"

    with pytest.raises(ValidationError):
        CommandIntent(skill="unknown.skill", args={})  # type: ignore[arg-type]


def test_safety_dangerous_patterns() -> None:
    """Перевіряє виявлення заборонених деструктивних команд."""
    assert is_dangerous_command("git reset --hard HEAD~1")
    assert is_dangerous_command("rm -rf /")
    assert is_dangerous_command("rmdir /s C:\\temp")
    assert not is_dangerous_command("pytest tests/")


async def test_app_run_once_and_state_transitions() -> None:
    """Перевіряє життєвий цикл FridayApp у режимі once."""
    cfg = AppConfig()
    app = FridayApp(config=cfg)
    assert app.state == AssistantState.IDLE
    app.transition(AssistantState.LISTENING)
    assert app.state == AssistantState.LISTENING
    await app.run(once=True)


def test_setup_logging_creates_file(tmp_path: Path) -> None:
    """Перевіряє створення лог-файлу."""
    log_file = setup_logging(log_dir=tmp_path)
    assert log_file.exists()

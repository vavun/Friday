"""Завантаження, валідація конфігурації (config.toml + .env) та налаштування логування."""

from __future__ import annotations

import logging
import os
import tomllib
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from pydantic import BaseModel, Field, field_validator


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config.toml"
DEFAULT_LOG_DIR = PROJECT_ROOT / "logs"


class HotkeysConfig(BaseModel):
    """Налаштування глобальних гарячих клавіш."""

    voice: str = "right_ctrl"
    explain_clipboard: str = "ctrl+alt+d"
    cancel: str = "esc"


class AudioConfig(BaseModel):
    """Параметри запису звуку."""

    sample_rate: int = Field(default=16000, ge=8000, le=48000)
    max_seconds: int = Field(default=15, ge=1, le=60)
    tail_ms: int = Field(default=200, ge=0, le=2000)
    min_ms: int = Field(default=300, ge=50, le=2000)


class SttConfig(BaseModel):
    """Параметри розпізнавання мовлення (STT)."""

    backend: Literal["gemini", "whisper_local"] = "gemini"
    language: str = "uk"
    model: str = "gemini-3.5-transcribe"
    mode: str = "VERBATIM"


class LlmConfig(BaseModel):
    """Параметри LLM-парсера команд."""

    model: str = "gemini-3.5-flash-lite"
    timeout_s: float = Field(default=5.0, gt=0.5, le=30.0)


class MonitorsConfig(BaseModel):
    """Індекси моніторів за призначенням."""

    primary: int = Field(default=0, ge=0)
    docs: int = Field(default=1, ge=0)


class AgentConfig(BaseModel):
    """Налаштування інтеграції з Antigravity CLI."""

    command: str = "antigravity"
    auto_approve: bool = False

    @field_validator("auto_approve")
    @classmethod
    def _forbid_auto_approve(cls, v: bool) -> bool:
        """Гарантує, що auto_approve завжди вимкнений для безпеки."""
        if v:
            raise ValueError("auto_approve має бути ЗАВЖДИ false при голосовому керуванні")
        return False


class SecretsConfig(BaseModel):
    """Секретні ключі, завантажені з .env (не логуються)."""

    gemini_api_key: str = ""
    telegram_api_id: str = ""
    telegram_api_hash: str = ""

    def __repr__(self) -> str:
        return "SecretsConfig(gemini_api_key='***', telegram_api_id='***', telegram_api_hash='***')"


class AppConfig(BaseModel):
    """Головна схема конфігурації асистента FRIDAY."""

    hotkeys: HotkeysConfig = Field(default_factory=HotkeysConfig)
    audio: AudioConfig = Field(default_factory=AudioConfig)
    stt: SttConfig = Field(default_factory=SttConfig)
    llm: LlmConfig = Field(default_factory=LlmConfig)
    monitors: MonitorsConfig = Field(default_factory=MonitorsConfig)
    apps: dict[str, str] = Field(default_factory=dict)
    docs_sites: dict[str, str] = Field(default_factory=dict)
    agent: AgentConfig = Field(default_factory=AgentConfig)
    secrets: SecretsConfig = Field(default_factory=SecretsConfig)


def load_config(
    config_path: Path | None = None,
    env_path: Path | None = None,
) -> AppConfig:
    """Читає config.toml та .env і повертає провалідований AppConfig."""
    load_dotenv(dotenv_path=env_path or (PROJECT_ROOT / ".env"), override=False)

    path = config_path or DEFAULT_CONFIG_PATH
    raw_data: dict = {}
    if path.exists():
        with path.open("rb") as f:
            raw_data = tomllib.load(f)

    secrets = SecretsConfig(
        gemini_api_key=os.getenv("GEMINI_API_KEY", ""),
        telegram_api_id=os.getenv("TELEGRAM_API_ID", ""),
        telegram_api_hash=os.getenv("TELEGRAM_API_HASH", ""),
    )
    raw_data["secrets"] = secrets
    return AppConfig.model_validate(raw_data)


def setup_logging(log_dir: Path | None = None, level: int = logging.INFO) -> Path:
    """Налаштовує логування у консоль та ротований файл."""
    target_dir = log_dir or DEFAULT_LOG_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    log_file = target_dir / "friday.log"

    fmt = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    root = logging.getLogger("friday")
    root.setLevel(level)
    root.handlers.clear()
    root.propagate = False

    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=2 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setFormatter(fmt)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(fmt)

    root.addHandler(file_handler)
    root.addHandler(console_handler)
    return log_file

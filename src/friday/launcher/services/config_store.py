"""Атомарне читання та запис config.local.toml із валідацією через Pydantic-схеми ядра."""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any

import tomli_w
from pydantic import ValidationError

from friday.config import (
    DEFAULT_CONFIG_PATH,
    DEFAULT_LOCAL_CONFIG_PATH,
    AppConfig,
    deep_merge_dicts,
    load_config,
)


class ConfigValidationError(ValueError):
    """Помилка валідації конфігу із зазначенням конкретного некоректного поля."""

    def __init__(self, field_path: str, message: str) -> None:
        self.field_path = field_path
        self.message = message
        super().__init__(f"Некоректне поле '{field_path}': {message}")


def _clean_none_for_toml(data: Any) -> Any:
    """Рекурсивно прибирає ключі зі значенням None, оскільки стандарт TOML не має типу null."""
    if isinstance(data, dict):
        return {k: _clean_none_for_toml(v) for k, v in data.items() if v is not None}
    if isinstance(data, list):
        return [_clean_none_for_toml(v) for v in data if v is not None]
    return data


class ConfigStore:
    """Керує базовим config.toml та користувацьким config.local.toml (атомарний запис)."""

    def __init__(
        self,
        base_path: Path | None = None,
        local_path: Path | None = None,
        env_path: Path | None = None,
    ) -> None:
        self.base_path = base_path or DEFAULT_CONFIG_PATH
        self.local_path = local_path or DEFAULT_LOCAL_CONFIG_PATH
        self.env_path = env_path

    def read_base_dict(self) -> dict[str, Any]:
        """Читає базовий config.toml як словник."""
        if not self.base_path.exists():
            return {}
        with self.base_path.open("rb") as f:
            return tomllib.load(f)

    def read_local_dict(self) -> dict[str, Any]:
        """Читає особистий config.local.toml як словник."""
        if not self.local_path.exists():
            return {}
        with self.local_path.open("rb") as f:
            return tomllib.load(f)

    def load_merged(self) -> AppConfig:
        """Завантажує злиту та провалідовану конфігурацію AppConfig."""
        return load_config(
            config_path=self.base_path,
            env_path=self.env_path,
            local_config_path=self.local_path,
        )

    def save_local_overrides(self, overrides: dict[str, Any]) -> AppConfig:
        """Валідує накладені зміни через AppConfig і атомарно записує у config.local.toml."""
        base_dict = self.read_base_dict()
        current_local = self.read_local_dict()
        updated_local = deep_merge_dicts(current_local, overrides)
        candidate_merged = deep_merge_dicts(base_dict, updated_local)

        try:
            validated = AppConfig.model_validate(candidate_merged)
        except ValidationError as exc:
            first = exc.errors()[0]
            loc = ".".join(str(x) for x in first.get("loc", ())) or "root"
            msg = str(first.get("msg", "Помилка валідації"))
            raise ConfigValidationError(field_path=loc, message=msg) from exc

        clean_local = _clean_none_for_toml(updated_local)
        toml_text = tomli_w.dumps(clean_local)

        self.local_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.local_path.with_suffix(".toml.tmp")
        tmp_path.write_text(toml_text, encoding="utf-8", newline="\n")
        os.replace(tmp_path, self.local_path)
        return validated

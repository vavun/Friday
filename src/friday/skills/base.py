"""Базовий інтерфейс навички (Skill) та реєстр навичок."""

from __future__ import annotations

from typing import Any, Protocol


class Skill(Protocol):
    """Протокол виконання навички."""

    name: str

    async def execute(self, args: dict[str, Any]) -> dict[str, Any]:
        """Виконує навичку з переданими аргументами."""
        ...

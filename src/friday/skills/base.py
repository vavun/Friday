"""Базовий інтерфейс навички (Skill) та реєстр навичок."""

from __future__ import annotations

import logging
from typing import Any, Protocol

from friday.router.schema import CommandIntent

log = logging.getLogger("friday.skills")


class Skill(Protocol):
    """Протокол виконання навички."""

    name: str

    async def execute(self, args: dict[str, Any]) -> dict[str, Any]:
        """Виконує навичку з переданими аргументами."""
        ...


class SkillRegistry:
    """Реєстр та виконавець зареєстрованих навичок."""

    def __init__(self) -> None:
        self._skills: dict[str, Skill] = {}

    def register(self, skill: Skill) -> None:
        """Реєструє навичку за її іменем."""
        self._skills[skill.name] = skill

    def get(self, name: str) -> Skill | None:
        """Повертає навичку за назвою або None."""
        return self._skills.get(name)

    @property
    def names(self) -> list[str]:
        """Перелік зареєстрованих імен навичок."""
        return sorted(self._skills.keys())

    async def execute(self, intent: CommandIntent) -> dict[str, Any]:
        """Виконує команду за об'єктом CommandIntent."""
        skill = self._skills.get(intent.skill)
        if skill is None:
            raise KeyError(f"Навичку '{intent.skill}' не зареєстровано")
        return await skill.execute(intent.args)

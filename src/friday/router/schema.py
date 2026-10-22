"""Pydantic-схеми команд (контракт між роутером і навичками)."""

from __future__ import annotations

from typing import Any, Literal
from pydantic import BaseModel, Field


SkillName = Literal[
    "window.move",
    "window.resize",
    "window.focus",
    "app.launch",
    "web.search",
    "docs.lookup",
    "text.type",
    "telegram.send",
    "agent.run",
    "clarify",
]


class CommandIntent(BaseModel):
    """Структурована команда, яку повертають fast-rules або LLM-парсер."""

    skill: SkillName
    args: dict[str, Any] = Field(default_factory=dict)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    needs_confirm: bool = False

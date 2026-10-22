"""Пакет маршрутизації голосових команд: Fast path (rules) -> Smart path (LLM JSON)."""

from __future__ import annotations

import logging
from typing import Literal

from friday.config import AppConfig
from friday.router.llm_parser import LlmCommandParser
from friday.router.rules import match_fast_rule, normalize_command_text
from friday.router.schema import CommandIntent, SkillName
from friday.util.timing import measure_time

log = logging.getLogger("friday.router")

_NOISE_TOKENS: frozenset[str] = frozenset({"а", "е", "ее", "ем", "гм", "мм", "угу", "ну", "ой", "та"})


class CommandRouter:
    """Комбінований роутер: спершу детерміновані правила (Fast path), потім LLM-парсер (Smart path)."""

    def __init__(
        self,
        config: AppConfig,
        llm_parser: LlmCommandParser | None = None,
    ) -> None:
        self.config = config
        self.llm_parser = llm_parser or LlmCommandParser(config=config)
        self.last_route_source: Literal["fast", "smart", "noise"] = "fast"

    async def route(self, text: str) -> CommandIntent:
        """Маршрутизує розпізнаний текст у валідний CommandIntent."""
        norm = normalize_command_text(text)
        if len(norm) < 2 or norm in _NOISE_TOKENS:
            self.last_route_source = "noise"
            return CommandIntent(
                skill="clarify",
                args={"question": "Фраза занадто коротка або схожа на шум. Повторіть команду."},
                confidence=0.0,
            )

        with measure_time("router.fast_path"):
            fast_intent = match_fast_rule(
                text,
                custom_apps=self.config.apps,
                custom_synonyms=self.config.app_synonyms,
            )

        if fast_intent is not None:
            self.last_route_source = "fast"
            log.info(
                "⚡ Fast path -> skill=%s args=%s (conf=%.2f)",
                fast_intent.skill,
                fast_intent.args,
                fast_intent.confidence,
            )
            return fast_intent

        self.last_route_source = "smart"
        smart_intent = await self.llm_parser.parse(text)
        log.info(
            "🧠 Smart path -> skill=%s args=%s (conf=%.2f)",
            smart_intent.skill,
            smart_intent.args,
            smart_intent.confidence,
        )
        return smart_intent


__all__ = [
    "CommandIntent",
    "CommandRouter",
    "LlmCommandParser",
    "SkillName",
    "match_fast_rule",
    "normalize_command_text",
]

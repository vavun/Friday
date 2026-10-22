"""Smart path: LLM-парсер голосових команд на базі Gemini Flash із жорсткою JSON-схемою."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, Callable

from friday.config import AppConfig
from friday.router.rules import resolve_target_name
from friday.router.schema import CommandIntent
from friday.skills.apps import APP_NAME_SYNONYMS
from friday.util.timing import measure_time

log = logging.getLogger("friday.router.llm")

SYSTEM_PROMPT_TEMPLATE = """Ти — парсер голосових команд для асистента на Windows. Користувач говорить українською.
Перетвори команду на ОДИН JSON-об'єкт за схемою {{ "skill": "...", "args": {{...}}, "confidence": 0.0..1.0, "needs_confirm": true|false }}.
Відповідай ТІЛЬКИ JSON, без пояснень і без markdown.
Допустимі skill: window.move, window.resize, window.focus, gesture.drag, app.launch, web.search,
docs.lookup, text.type, telegram.send, agent.run, file.create, file.open, clarify.
Якщо команда неоднозначна — skill = "clarify" з полем args.question.
Дії, що надсилають повідомлення або змінюють файли, мають needs_confirm = true.
Назви програм бери лише з переданого списку: {APPS_LIST}."""

_CODE_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


def extract_json_text(raw: str) -> str:
    """Очищує відповідь моделі від випадкових markdown-огорток ```json ... ```."""
    cleaned = (raw or "").strip()
    cleaned = _CODE_FENCE_RE.sub("", cleaned).strip()
    return cleaned


class LlmCommandParser:
    """Перетворює вільний україномовний текст на валідний CommandIntent через Gemini Flash."""

    def __init__(
        self,
        config: AppConfig,
        client: Any | None = None,
        generate_fn: Callable[[str, str], str] | None = None,
        confidence_threshold: float = 0.7,
    ) -> None:
        self.config = config
        self._client = client
        self._generate_fn = generate_fn
        self.confidence_threshold = confidence_threshold

    def _get_client(self) -> Any:
        """Ліниво ініціалізує та повторно використовує один екземпляр genai.Client."""
        if self._client is None:
            from google import genai

            api_key = self.config.secrets.gemini_api_key
            if not api_key:
                raise RuntimeError("GEMINI_API_KEY не налаштовано у .env")
            self._client = genai.Client(api_key=api_key)
        return self._client

    def build_system_prompt(self) -> str:
        """Формує системний промпт із актуальним білим списком програм."""
        apps_keys = sorted(set(self.config.apps.keys()) | {"browser", "telegram", "code"})
        return SYSTEM_PROMPT_TEMPLATE.format(APPS_LIST=", ".join(apps_keys))

    def _call_model_sync(self, text: str, system_prompt: str) -> str:
        """Синхронний виклик Gemini Flash з temperature=0 та коротким лімітом токенів."""
        if self._generate_fn is not None:
            return self._generate_fn(text, system_prompt)

        from google.genai import types

        client = self._get_client()
        response = client.models.generate_content(
            model=self.config.llm.model,
            contents=text,
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                temperature=0.0,
                max_output_tokens=200,
                response_mime_type="application/json",
            ),
        )
        return str(getattr(response, "text", "") or "")

    def _postprocess_intent(self, intent: CommandIntent, raw_text: str) -> CommandIntent:
        """Застосовує політики безпеки: поріг confidence >= 0.7, needs_confirm та білий список програм."""
        if intent.skill != "clarify" and intent.confidence < self.confidence_threshold:
            log.info(
                "LLM confidence %.2f < %.2f -> перехід у clarify",
                intent.confidence,
                self.confidence_threshold,
            )
            return CommandIntent(
                skill="clarify",
                args={"question": f"Команда нечітка («{raw_text}»). Уточніть, що саме потрібно зробити?"},
                confidence=intent.confidence,
                needs_confirm=False,
            )

        if intent.skill in {"telegram.send", "agent.run"}:
            intent.needs_confirm = True

        if intent.skill == "app.launch":
            raw_name = str(intent.args.get("name", "")).strip()
            resolved = resolve_target_name(raw_name, self.config.app_synonyms)
            allowed = (
                {k.lower() for k in self.config.apps}
                | set(APP_NAME_SYNONYMS.values())
                | {"browser", "telegram", "code"}
            )
            if resolved not in allowed:
                return CommandIntent(
                    skill="clarify",
                    args={"question": f"Програма «{raw_name}» відсутня у білому списку дозволених програм."},
                    confidence=intent.confidence,
                    needs_confirm=False,
                )
            intent.args["name"] = resolved

        return intent

    async def parse(self, text: str) -> CommandIntent:
        """Асинхронно парсить текст у CommandIntent із таймаутом та одним ретраєм при невалідному JSON."""
        cleaned_text = (text or "").strip()
        if not cleaned_text:
            return CommandIntent(
                skill="clarify",
                args={"question": "Команда порожня. Повторіть, будь ласка."},
                confidence=0.0,
            )

        system_prompt = self.build_system_prompt()
        timeout_s = float(self.config.llm.timeout_s)

        with measure_time("router.llm_parse"):
            for attempt in range(2):  # Перша спроба + 1 ретрай при невалідному JSON/схемі
                try:
                    raw_output = await asyncio.wait_for(
                        asyncio.to_thread(self._call_model_sync, cleaned_text, system_prompt),
                        timeout=timeout_s,
                    )
                    json_str = extract_json_text(raw_output)
                    payload = json.loads(json_str)
                    intent = CommandIntent.model_validate(payload)
                    return self._postprocess_intent(intent, cleaned_text)
                except asyncio.TimeoutError:
                    log.warning("Таймаут LLM-парсера (%.1f с) на спробі %d", timeout_s, attempt + 1)
                    break
                except Exception as exc:
                    log.warning(
                        "Невалідний вивід LLM на спробі %d/2 (%s): %s",
                        attempt + 1,
                        type(exc).__name__,
                        exc,
                    )
                    if attempt == 0:
                        continue

        return CommandIntent(
            skill="clarify",
            args={"question": "Не вдалося однозначно розпізнати команду. Повторіть, будь ласка."},
            confidence=0.0,
            needs_confirm=False,
        )

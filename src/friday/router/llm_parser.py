"""Smart path: LLM-парсер голосових команд та планувальник пайплайнів на базі Clef (Experiential Labs) із фолбеком на Gemini Flash."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, Callable

from friday.config import AppConfig
from friday.router.clef_client import (
    ClefToolPlanner,
    normalize_skill_args,
    normalize_skill_name,
    normalize_step_dict,
    propagate_pipeline_targets,
    step_requires_confirm,
)
from friday.router.rules import resolve_target_name
from friday.router.schema import CommandIntent
from friday.skills.apps import APP_NAME_SYNONYMS
from friday.util.timing import measure_time

log = logging.getLogger("friday.router.llm")

SYSTEM_PROMPT_TEMPLATE = """You are the command parser and pipeline planner for the FRIDAY voice assistant on Windows.
The user speaks in Ukrainian or English, but FRIDAY ALWAYS RESPONDS AND ASKS QUESTIONS STRICTLY IN ENGLISH.
Convert the command into ONE JSON object according to the schema:
{{ "skill": "...", "args": {{...}}, "confidence": 0.0..1.0, "needs_confirm": true|false, "steps": [...] }}.
Respond with ONLY JSON, without explanations and without markdown.
Allowed skills: window.move, window.resize, window.focus, gesture.drag, app.launch, app.discover, web.search,
docs.lookup, text.type, telegram.send, agent.run, file.create, file.open, pipeline.save, skill.create, skill.run, clarify.
If the command consists of multiple actions — provide the list of steps in the "steps" field: [{{"skill": "...", "args": {{...}}}}].
In window.move / window.resize / window.focus steps after launching an app, set args.target to the canonical English name of that app.
If the user asks to launch an app not in ({APPS_LIST}) — use "app.discover" (if it's a real app) or "clarify".
If the user asks to save a new pipeline/routine — use "pipeline.save".
If the user asks to create a new custom skill — use "skill.create" with needs_confirm = true.
If the command is ambiguous, conversational, a greeting, or requires clarification — set skill = "clarify" with field args.question ALWAYS STRICTLY IN ENGLISH (e.g. "Hello! How can I help you?", "Which monitor would you like me to move this to?").
Actions that send messages, run agents, or create skills must have needs_confirm = true."""

_CODE_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)
_FENCED_BLOCK_RE = re.compile(r"```(?:json)?\s*([\s\S]*?)\s*```", re.IGNORECASE)


def extract_json_text(raw: str) -> str:
    """Очищує відповідь моделі від markdown-огорток або витягує перший JSON-об'єкт/масив із тексту."""
    cleaned = (raw or "").strip()
    if not cleaned:
        return ""

    fenced = _FENCED_BLOCK_RE.search(cleaned)
    if fenced:
        cleaned = fenced.group(1).strip()
    else:
        cleaned = _CODE_FENCE_RE.sub("", cleaned).strip()

    if cleaned.startswith(("{", "[")):
        return cleaned

    # Якщо модель додала пояснювальний текст навколо JSON — витягуємо зовнішні дужки
    obj_start = cleaned.find("{")
    arr_start = cleaned.find("[")
    if obj_start != -1 and (arr_start == -1 or obj_start < arr_start):
        obj_end = cleaned.rfind("}")
        if obj_end > obj_start:
            return cleaned[obj_start : obj_end + 1].strip()
    elif arr_start != -1:
        arr_end = cleaned.rfind("]")
        if arr_end > arr_start:
            return cleaned[arr_start : arr_end + 1].strip()

    return cleaned


def _normalize_payload_dict(payload: Any) -> dict[str, Any]:
    """Нормалізує імена навичок, плоскі кроки, масиви кроків та скорочений формат {'steps': [...]}."""
    if isinstance(payload, list):
        payload = {"steps": payload}
    if not isinstance(payload, dict):
        raise ValueError("JSON-відповідь LLM має бути об'єктом або списком кроків")

    data = dict(payload)

    # Якщо модель повернула pipeline.execute / pipeline_execute на верхньому рівні — розгортаємо його кроки
    top_skill_raw = str(data.get("skill") or data.get("tool") or "").strip()
    if top_skill_raw and normalize_skill_name(top_skill_raw) == "pipeline.execute":
        nested_steps = (
            data.get("steps")
            or (data.get("args", {}) if isinstance(data.get("args"), dict) else {}).get("steps")
        )
        if isinstance(nested_steps, list) and nested_steps:
            data = {
                "steps": nested_steps,
                "confidence": data.get("confidence", 0.95),
            }

    if "steps" in data and isinstance(data["steps"], list) and data["steps"]:
        norm_steps: list[dict[str, Any]] = []
        for st in data["steps"]:
            if isinstance(st, dict):
                norm_steps.append(normalize_step_dict(st))
        data["steps"] = norm_steps
        if ("skill" not in data or normalize_skill_name(str(data.get("skill", ""))) == "pipeline.execute") and norm_steps:
            data["skill"] = norm_steps[0].get("skill", "clarify")
            data["args"] = dict(norm_steps[0].get("args", {}))

    if "skill" in data or "tool" in data or "function" in data:
        norm_top = normalize_step_dict(data)
        data["skill"] = norm_top["skill"]
        data["args"] = norm_top["args"]

    return data


class LlmCommandParser:
    """Перетворює вільний україномовний текст на валідний CommandIntent через Clef (primary) або Gemini Flash (fallback)."""

    def __init__(
        self,
        config: AppConfig,
        client: Any | None = None,
        generate_fn: Callable[[str, str], str] | None = None,
        confidence_threshold: float = 0.7,
        clef_planner: ClefToolPlanner | None = None,
        app_discovery: Any | None = None,
    ) -> None:
        self.config = config
        self._client = client
        self._generate_fn = generate_fn
        self.confidence_threshold = confidence_threshold
        self.clef_planner = clef_planner or ClefToolPlanner(config=config)
        self.app_discovery = app_discovery

    def _get_client(self) -> Any:
        """Ліниво ініціалізує та повторно використовує один екземпляр genai.Client для Gemini."""
        if self._client is None:
            from google import genai

            api_key = self.config.secrets.gemini_api_key
            if not api_key:
                raise RuntimeError("GEMINI_API_KEY не налаштовано у .env")
            self._client = genai.Client(api_key=api_key)
        return self._client

    def build_system_prompt(self) -> str:
        """Формує системний промпт для JSON-режиму Gemini із актуальним білим списком програм."""
        apps_keys = sorted(set(self.config.apps.keys()) | {"browser", "telegram", "code"})
        return SYSTEM_PROMPT_TEMPLATE.format(APPS_LIST=", ".join(apps_keys))

    def _call_gemini_sync(self, text: str, system_prompt: str) -> str:
        """Синхронний виклик Gemini Flash з temperature=0 та коротким лімітом токенів."""
        from google.genai import types

        client = self._get_client()
        response = client.models.generate_content(
            model=self.config.llm.model,
            contents=text,
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                temperature=0.0,
                max_output_tokens=450,
                response_mime_type="application/json",
            ),
        )
        return str(getattr(response, "text", "") or "")

    def _has_gemini_fallback(self) -> bool:
        return bool((self.config.secrets.gemini_api_key or "").strip() or self._client is not None)

    def _call_model_sync(self, text: str, system_prompt: str, force_gemini: bool = False) -> CommandIntent | str:
        """Викликає Clef як основну модель (з власним tool-calling промптом) або переключається на Gemini."""
        if self._generate_fn is not None:
            return self._generate_fn(text, system_prompt)

        use_clef = (
            not force_gemini
            and self.config.llm.backend == "clef"
            and (self.clef_planner.has_api_key() or self.clef_planner._client is not None)
        )
        if use_clef:
            try:
                # Використовуємо спеціалізований системний промпт ClefToolPlanner (з пайплайнами та tool calling)
                return self.clef_planner.plan_sync(text)
            except Exception as exc:
                log.warning(
                    "Помилка виклику Clef API (%s) — перемикання на резервний Gemini: %s",
                    type(exc).__name__,
                    exc,
                )
                if not self._has_gemini_fallback():
                    raise

        return self._call_gemini_sync(text, system_prompt)

    def _resolve_or_discover_app_step(self, raw_name: str, confidence: float) -> tuple[str, dict[str, Any]] | None:
        """Перевіряє програму за білим списком або сервісом авто-пошуку; повертає (skill, args) або None."""
        cleaned = (raw_name or "").strip()
        if not cleaned:
            return None
        resolved = resolve_target_name(cleaned, self.config.app_synonyms)
        allowed = (
            {k.lower() for k in self.config.apps}
            | set(APP_NAME_SYNONYMS.values())
            | {"browser", "telegram", "code"}
        )
        if resolved in allowed:
            return ("app.launch", {"name": resolved})

        if self.app_discovery is not None:
            found = self.app_discovery.find_executable(cleaned)
            if found is not None:
                return ("app.discover", {"name": cleaned, "launch": True})

        return None

    def _postprocess_single_step(self, intent: CommandIntent, raw_text: str) -> CommandIntent:
        """Перевіряє один крок на політики безпеки, підтвердження, цільові вікна та авто-пошук програм."""
        intent.args = normalize_skill_args(intent.skill, intent.args)
        if step_requires_confirm(intent.skill, intent.args):
            intent.needs_confirm = True

        if intent.skill == "app.launch":
            raw_name = str(intent.args.get("name", "")).strip()
            resolved_step = self._resolve_or_discover_app_step(raw_name, intent.confidence)
            if resolved_step is None:
                return CommandIntent(
                    skill="clarify",
                    args={"question": f"Application '{raw_name}' is not in the allowed apps whitelist."},
                    confidence=intent.confidence,
                    needs_confirm=False,
                )
            sk_name, sk_args = resolved_step
            if sk_name == "app.discover":
                return CommandIntent(
                    skill="app.discover",
                    args=sk_args,
                    confidence=intent.confidence,
                    needs_confirm=False,
                )
            intent.args["name"] = sk_args["name"]

        elif intent.skill in ("window.move", "window.resize", "window.focus"):
            raw_target = str(intent.args.get("target", "active")).strip()
            if raw_target and raw_target.lower() not in {k.lower() for k in self.config.apps}:
                intent.args["target"] = resolve_target_name(raw_target, self.config.app_synonyms)

        elif intent.skill in ("pipeline.save", "skill.create") and isinstance(intent.args.get("steps"), list):
            norm_nested: list[dict[str, Any]] = []
            for st in intent.args["steps"]:
                if not isinstance(st, dict):
                    continue
                st_norm = normalize_step_dict(st)
                if st_norm["skill"] == "app.launch":
                    r_name = str(st_norm["args"].get("name", "")).strip()
                    res_app = self._resolve_or_discover_app_step(r_name, intent.confidence)
                    if res_app is not None:
                        st_norm["skill"], st_norm["args"] = res_app
                elif st_norm["skill"] in ("window.move", "window.resize", "window.focus"):
                    r_t = str(st_norm["args"].get("target", "active")).strip()
                    if r_t and r_t.lower() not in {k.lower() for k in self.config.apps}:
                        st_norm["args"]["target"] = resolve_target_name(r_t, self.config.app_synonyms)
                norm_nested.append(st_norm)
            intent.args["steps"] = norm_nested
            if step_requires_confirm(intent.skill, intent.args):
                intent.needs_confirm = True

        return intent

    def _postprocess_intent(self, intent: CommandIntent, raw_text: str) -> CommandIntent:
        """Застосовує політики безпеки: поріг confidence >= 0.7, needs_confirm, пайплайни та білий список програм."""
        if intent.skill != "clarify" and intent.confidence < self.confidence_threshold:
            log.info(
                "LLM confidence %.2f < %.2f -> перехід у clarify",
                intent.confidence,
                self.confidence_threshold,
            )
            return CommandIntent(
                skill="clarify",
                args={"question": f"Command is ambiguous ('{raw_text}'). Could you please clarify?"},
                confidence=intent.confidence,
                needs_confirm=False,
            )

        if intent.steps:
            propagated_input = propagate_pipeline_targets(list(intent.steps))
            processed_steps: list[CommandIntent] = []
            for st in propagated_input:
                pst = self._postprocess_single_step(st, raw_text)
                if pst.skill == "clarify":
                    return pst
                processed_steps.append(pst)
            processed_steps = propagate_pipeline_targets(processed_steps)
            first = processed_steps[0]
            return CommandIntent(
                skill=first.skill,
                args=first.args,
                confidence=min(s.confidence for s in processed_steps),
                needs_confirm=any(s.needs_confirm for s in processed_steps),
                steps=processed_steps,
            )

        return self._postprocess_single_step(intent, raw_text)

    async def parse(self, text: str) -> CommandIntent:
        """Асинхронно парсить текст у CommandIntent із таймаутом та одним ретраєм при невалідному JSON."""
        cleaned_text = (text or "").strip()
        if not cleaned_text:
            return CommandIntent(
                skill="clarify",
                args={"question": "Command is empty. Please speak again."},
                confidence=0.0,
            )

        system_prompt = self.build_system_prompt()
        timeout_s = float(self.config.llm.timeout_s)

        with measure_time("router.llm_parse"):
            for attempt in range(2):  # Перша спроба + 1 ретрай (із перемиканням на Gemini за потреби)
                try:
                    force_gemini = attempt == 1 and self._generate_fn is None and self._has_gemini_fallback()
                    raw_output = await asyncio.wait_for(
                        asyncio.to_thread(self._call_model_sync, cleaned_text, system_prompt, force_gemini),
                        timeout=timeout_s,
                    )
                    if isinstance(raw_output, CommandIntent):
                        return self._postprocess_intent(raw_output, cleaned_text)

                    json_str = extract_json_text(raw_output)
                    payload = _normalize_payload_dict(json.loads(json_str))
                    intent = CommandIntent.model_validate(payload)
                    return self._postprocess_intent(intent, cleaned_text)
                except asyncio.TimeoutError:
                    log.warning("Таймаут LLM-парсера (%.1f с) на спробі %d", timeout_s, attempt + 1)
                    if attempt == 0 and self._generate_fn is None and self._has_gemini_fallback():
                        continue
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
            args={"question": "Could not recognize the command. Please repeat."},
            confidence=0.0,
            needs_confirm=False,
        )

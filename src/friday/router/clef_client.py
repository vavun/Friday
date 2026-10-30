"""Клієнт та планувальник викликів інструментів (Tool Calling & Pipelines) на базі моделі Experiential Labs Clef."""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

from friday.config import AppConfig
from friday.router.schema import CommandIntent

log = logging.getLogger("friday.router.clef")

# Відображення імен інструментів OpenAI (без крапок) -> канонічні SkillName у FRIDAY
TOOL_NAME_TO_SKILL: dict[str, str] = {
    "window_move": "window.move",
    "window_resize": "window.resize",
    "window_focus": "window.focus",
    "gesture_drag": "gesture.drag",
    "app_launch": "app.launch",
    "app_discover": "app.discover",
    "file_open": "file.open",
    "file_create": "file.create",
    "web_search": "web.search",
    "docs_lookup": "docs.lookup",
    "text_type": "text.type",
    "telegram_send": "telegram.send",
    "agent_run": "agent.run",
    "pipeline_save": "pipeline.save",
    "pipeline_execute": "pipeline.execute",
    "pipeline_run": "pipeline.execute",
    "skill_create": "skill.create",
    "skill_run": "skill.run",
    "assistant_jarvis": "assistant.jarvis",
    "ask_jarvis": "assistant.jarvis",
    "clarify": "clarify",
}

SKILL_TO_TOOL_NAME: dict[str, str] = {
    v: k for k, v in TOOL_NAME_TO_SKILL.items() if v != "pipeline.execute"
}

RISKY_SKILLS: frozenset[str] = frozenset({"telegram.send", "agent.run", "skill.create"})

_PRONOUN_TARGETS: frozenset[str] = frozenset({
    "active",
    "його",
    "її",
    "це",
    "це вікно",
    "поточне вікно",
    "активне вікно",
    "вікно",
    "",
})

_STEP_META_KEYS: frozenset[str] = frozenset({
    "skill",
    "tool",
    "function",
    "args",
    "arguments",
    "parameters",
    "params",
    "confidence",
    "needs_confirm",
    "steps",
})

_CODE_FENCE_STRIP_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


def normalize_skill_name(raw_name: str) -> str:
    """Перетворює ім'я інструменту (app_launch або app.launch) на канонічне ім'я навички FRIDAY."""
    cleaned = (raw_name or "").strip().lower().replace("-", "_")
    if cleaned in TOOL_NAME_TO_SKILL:
        return TOOL_NAME_TO_SKILL[cleaned]
    dot_replaced = cleaned.replace(".", "_")
    if dot_replaced in TOOL_NAME_TO_SKILL:
        return TOOL_NAME_TO_SKILL[dot_replaced]
    return (raw_name or "").strip()


def _safe_json_loads(raw: Any) -> Any:
    """Безпечно розбирає JSON-рядок (навіть якщо він обгорнутий у markdown-блок)."""
    if not isinstance(raw, str):
        return raw
    cleaned = _CODE_FENCE_STRIP_RE.sub("", raw.strip()).strip()
    if not cleaned:
        return {}
    try:
        return json.loads(cleaned)
    except Exception:
        # Спроба знайти перший JSON-об'єкт або масив у рядку
        for start_ch, end_ch in (("{", "}"), ("[", "]")):
            s_idx = cleaned.find(start_ch)
            e_idx = cleaned.rfind(end_ch)
            if s_idx != -1 and e_idx > s_idx:
                try:
                    return json.loads(cleaned[s_idx : e_idx + 1])
                except Exception:
                    pass
        return {}


def normalize_skill_args(skill_name: str, raw_args: dict[str, Any]) -> dict[str, Any]:
    """Нормалізує типові аліаси параметрів та вкладені JSON-рядки для конкретної навички."""
    args = dict(raw_args or {})

    # Розпаковуємо вкладені списки/словники, якщо модель передала їх JSON-рядком
    for list_key in ("steps", "candidates", "synonyms"):
        if isinstance(args.get(list_key), str):
            parsed_val = _safe_json_loads(args[list_key])
            if isinstance(parsed_val, list):
                args[list_key] = parsed_val

    if skill_name in ("app.launch", "app.discover"):
        if "name" not in args:
            for alt in ("app", "target", "application", "program", "query"):
                if alt in args and args[alt]:
                    args["name"] = args.pop(alt)
                    break

    elif skill_name in ("window.move", "window.resize", "window.focus"):
        if "target" not in args:
            for alt in ("app", "window", "name", "application"):
                if alt in args and args[alt]:
                    args["target"] = args.pop(alt)
                    break
        if "target" not in args or not str(args.get("target", "")).strip():
            args["target"] = "active"
        if skill_name == "window.move":
            if "monitor" in args and isinstance(args["monitor"], str) and args["monitor"].strip().isdigit():
                args["monitor"] = int(args["monitor"].strip())
            if "mode" not in args and "direction" in args:
                args["mode"] = args.pop("direction")
        elif skill_name == "window.resize":
            if "mode" not in args:
                for alt in ("action", "size", "direction"):
                    if alt in args and args[alt]:
                        args["mode"] = args.pop(alt)
                        break

    elif skill_name == "file.create":
        if "name" not in args:
            for alt in ("filename", "file", "folder", "path", "target"):
                if alt in args and args[alt]:
                    args["name"] = args.pop(alt)
                    break
        if "location" not in args:
            for alt in ("directory", "dir", "folder_location", "where"):
                if alt in args and args[alt]:
                    args["location"] = args.pop(alt)
                    break
        if "initial_content" not in args and "content" in args:
            args["initial_content"] = args.pop("content")
        if "item_type" not in args:
            args["item_type"] = "file"

    elif skill_name == "file.open":
        if "target" not in args:
            for alt in ("name", "path", "file", "folder", "filename"):
                if alt in args and args[alt]:
                    args["target"] = args.pop(alt)
                    break
        if "location" not in args:
            for alt in ("directory", "dir", "where"):
                if alt in args and args[alt]:
                    args["location"] = args.pop(alt)
                    break

    elif skill_name == "web.search":
        if "query" not in args:
            for alt in ("q", "text", "search", "open_site", "site", "url"):
                if alt in args and args[alt]:
                    args["query"] = args[alt]
                    break

    elif skill_name == "text.type":
        if "text" not in args:
            for alt in ("content", "message", "value", "input"):
                if alt in args and args[alt]:
                    args["text"] = args.pop(alt)
                    break

    elif skill_name == "telegram.send":
        if "contact" not in args:
            for alt in ("to", "recipient", "user", "username", "chat"):
                if alt in args and args[alt]:
                    args["contact"] = args.pop(alt)
                    break
        if "text" not in args:
            for alt in ("message", "content", "body"):
                if alt in args and args[alt]:
                    args["text"] = args.pop(alt)
                    break

    elif skill_name == "agent.run":
        if "prompt" not in args:
            for alt in ("task", "instruction", "query", "text", "command"):
                if alt in args and args[alt]:
                    args["prompt"] = args.pop(alt)
                    break

    elif skill_name == "gesture.drag":
        if "action" not in args:
            for alt in ("mode", "state"):
                if alt in args and args[alt]:
                    args["action"] = args.pop(alt)
                    break
        if "action" not in args:
            args["action"] = "start"

    elif skill_name == "pipeline.save":
        if "trigger" not in args and "name" in args:
            args["trigger"] = args["name"]
        if isinstance(args.get("steps"), list):
            args["steps"] = [
                normalize_step_dict(st) for st in args["steps"] if isinstance(st, dict)
            ]

    elif skill_name == "skill.create":
        if "name" not in args and "trigger" in args:
            args["name"] = args["trigger"]
        if "trigger" not in args and "name" in args:
            args["trigger"] = args["name"]
        if isinstance(args.get("steps"), list):
            args["steps"] = [
                normalize_step_dict(st) for st in args["steps"] if isinstance(st, dict)
            ]
        if "action_type" not in args:
            if isinstance(args.get("steps"), list) and args["steps"]:
                args["action_type"] = "steps"
            elif args.get("url"):
                args["action_type"] = "url"
            else:
                args["action_type"] = "command"

    elif skill_name == "skill.run":
        if "name" not in args:
            for alt in ("trigger", "pipeline", "skill_name", "target"):
                if alt in args and args[alt]:
                    args["name"] = args.pop(alt)
                    break

    elif skill_name == "assistant.jarvis":
        if "message" not in args:
            for alt in ("prompt", "query", "text", "task", "question"):
                if alt in args and args[alt]:
                    args["message"] = args.pop(alt)
                    break

    return args


def normalize_step_dict(step: dict[str, Any]) -> dict[str, Any]:
    """Нормалізує один крок пайплайну (підтримує вкладені args, плоскі поля та OpenAI function-формат)."""
    raw = dict(step or {})
    fn_block = raw.get("function") if isinstance(raw.get("function"), dict) else None

    raw_skill = (
        raw.get("skill")
        or raw.get("tool")
        or (fn_block.get("name") if fn_block else None)
        or (raw.get("name") if "skill" not in raw and ("args" in raw or "arguments" in raw or "parameters" in raw) else None)
        or ""
    )
    skill_name = normalize_skill_name(str(raw_skill))

    raw_args: Any = None
    for k in ("args", "arguments", "parameters", "params"):
        if k in raw and raw[k] is not None:
            raw_args = raw[k]
            break
    if raw_args is None and fn_block is not None:
        raw_args = fn_block.get("arguments") or fn_block.get("parameters")

    if isinstance(raw_args, str):
        parsed = _safe_json_loads(raw_args)
        args_dict = dict(parsed) if isinstance(parsed, dict) else {}
    elif isinstance(raw_args, dict):
        args_dict = dict(raw_args)
    else:
        args_dict = {}

    # Якщо модель передала аргументи плоским словником на рівні кроку (напр. {"skill": "window.move", "target": "code", "monitor": 2})
    for k, v in raw.items():
        if k in _STEP_META_KEYS:
            continue
        if k == "name" and "skill" not in raw and "tool" not in raw:
            continue
        if k not in args_dict and v is not None:
            args_dict[k] = v

    norm_args = normalize_skill_args(skill_name, args_dict)
    out: dict[str, Any] = {"skill": skill_name, "args": norm_args}
    if "confidence" in raw:
        out["confidence"] = raw["confidence"]
    if "needs_confirm" in raw:
        out["needs_confirm"] = raw["needs_confirm"]
    return out


def propagate_pipeline_targets(steps: list[CommandIntent]) -> list[CommandIntent]:
    """Протягує контекст цільового вікна між послідовними кроками пайплайну (launch/discover -> window.*)."""
    if len(steps) <= 1:
        return steps

    last_target: str | None = None
    for st in steps:
        if st.skill == "app.launch":
            app_name = str(st.args.get("name", "")).strip()
            if app_name:
                last_target = app_name
        elif st.skill == "app.discover":
            cands = st.args.get("candidates")
            if isinstance(cands, list) and cands:
                first_cand = str(cands[0]).strip().lower()
                if first_cand.endswith(".exe"):
                    first_cand = first_cand[:-4]
                last_target = first_cand or str(st.args.get("name", "")).strip()
            else:
                last_target = str(st.args.get("name", "")).strip()
        elif st.skill == "web.search":
            last_target = "browser"
        elif st.skill in ("file.open", "file.create"):
            app_hint = str(st.args.get("app") or "").strip()
            if app_hint:
                last_target = app_hint
        elif st.skill in ("window.move", "window.resize", "window.focus"):
            cur_target = str(st.args.get("target", "active")).strip().lower()
            if cur_target in _PRONOUN_TARGETS and last_target:
                st.args["target"] = last_target
            elif cur_target not in _PRONOUN_TARGETS:
                last_target = str(st.args.get("target", "")).strip()

    return steps


def step_requires_confirm(skill_name: str, args: dict[str, Any]) -> bool:
    """Перевіряє, чи крок (або його вкладені кроки у pipeline.save / skill.create) потребує підтвердження."""
    if skill_name in RISKY_SKILLS:
        return True
    if skill_name in ("pipeline.save", "pipeline.execute", "skill.create"):
        nested = args.get("steps")
        if isinstance(nested, list):
            for st in nested:
                if isinstance(st, dict):
                    st_skill = normalize_skill_name(str(st.get("skill", "")))
                    st_args = st.get("args") if isinstance(st.get("args"), dict) else {}
                    if step_requires_confirm(st_skill, st_args):
                        return True
    return False


def build_clef_tools_schema(
    allowed_apps: list[str],
    saved_pipelines: list[str],
    saved_skills: list[str],
) -> list[dict[str, Any]]:
    """Формує список OpenAI-сумісних інструментів (tools) для моделі Clef."""
    apps_desc = ", ".join(allowed_apps) if allowed_apps else "browser, telegram, code"
    pipelines_desc = ", ".join(saved_pipelines) if saved_pipelines else "(ще немає)"
    skills_desc = ", ".join(saved_skills) if saved_skills else "(ще немає)"

    return [
        {
            "type": "function",
            "function": {
                "name": "app_launch",
                "description": f"Запустити програму з поточного білого списку дозволених ({apps_desc}).",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Канонічна назва програми з білого списку"},
                    },
                    "required": ["name"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "app_discover",
                "description": (
                    "Знайти нову програму, якої ще немає у білому списку, у меню Пуск/Реєстрі/PATH, "
                    "автоматично зберегти її у навички (config.local.toml) разом із голосовими синонімами та запустити."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Назва програми, яку просить відкрити користувач"},
                        "candidates": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Можливі англійські назви або імена .exe (наприклад ['spotify', 'spotify.exe'])",
                        },
                        "synonyms": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Голосові синоніми українською/англійською для збереження у [app_synonyms]",
                        },
                        "launch": {"type": "boolean", "default": True},
                    },
                    "required": ["name"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "window_move",
                "description": "Перемістити або прикріпити вікно на вказаний монітор чи сторону екрана.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "target": {
                            "type": "string",
                            "description": "Канонічна назва вікна/програми (browser, code, telegram, spotify, notepad тощо) або 'active'",
                        },
                        "monitor": {"type": "integer", "description": "Номер монітора (1, 2, 3)"},
                        "mode": {"type": "string", "enum": ["left", "right", "center", "maximize"]},
                    },
                    "required": ["target"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "window_resize",
                "description": "Змінити розмір вікна (більше, менше, на весь екран, відновити).",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "target": {"type": "string", "description": "Назва вікна/програми або 'active'"},
                        "mode": {"type": "string", "enum": ["bigger", "smaller", "maximize", "restore"]},
                    },
                    "required": ["target", "mode"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "window_focus",
                "description": "Перемкнути фокус на вказане вікно або програму.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "target": {"type": "string", "description": "Назва вікна/програми"},
                    },
                    "required": ["target"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "gesture_drag",
                "description": "Увімкнути або вимкнути режим керування вікнами жестами руки через камеру.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {"type": "string", "enum": ["start", "stop"]},
                    },
                    "required": ["action"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "file_create",
                "description": "Створити новий файл або папку у дозволеній директорії (робочий стіл, завантаження, документи, проєкт).",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Ім'я файлу або папки"},
                        "item_type": {"type": "string", "enum": ["file", "folder"]},
                        "file_kind": {
                            "type": "string",
                            "description": "Тип файлу: python, text, markdown, json, html, js, css, bat",
                        },
                        "location": {
                            "type": "string",
                            "description": "Цільова папка (робочий стіл, завантаження, документи, проєкт або назва підпапки)",
                        },
                        "initial_content": {"type": "string", "description": "Початковий вміст файлу (необов'язково)"},
                        "open_after": {"type": "boolean", "description": "Відкрити одразу після створення"},
                        "app": {"type": "string", "description": "Програма для відкриття (code, notepad, explorer)"},
                    },
                    "required": ["name", "item_type"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "file_open",
                "description": "Відкрити існуючий файл або папку у Провіднику чи вказаній програмі.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "target": {"type": "string", "description": "Назва файлу або папки"},
                        "location": {"type": "string", "description": "Де шукати (робочий стіл, завантаження, документи, проєкт)"},
                        "prefer_dir": {"type": "boolean", "description": "True якщо шукаємо саме папку"},
                        "app": {"type": "string", "description": "Програма для відкриття (code, notepad, explorer)"},
                    },
                    "required": ["target"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "web_search",
                "description": "Пошук у Google або пряме відкриття сайту/документації.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Пошуковий запит або назва сайту"},
                        "site": {"type": "string", "description": "Ключ сайту документації (python, fastapi, pydantic, mdn)"},
                        "open_site": {"type": "string", "description": "Пряме відкриття сайту (youtube, github, google, chatgpt, gmail)"},
                    },
                    "required": ["query"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "docs_lookup",
                "description": "Відкрити документацію за помилкою або трейсбеком із буфера обміну на моніторі документації.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "source": {"type": "string", "default": "clipboard"},
                        "error": {"type": "string", "description": "Назва класу помилки (наприклад TypeError, KeyError)"},
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "text_type",
                "description": "Ввести/надрукувати текст у активне поле вводу.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string"},
                    },
                    "required": ["text"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "telegram_send",
                "description": "Надіслати повідомлення контакту в Telegram (вимагатиме підтвердження Enter/Esc).",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "contact": {"type": "string"},
                        "text": {"type": "string"},
                    },
                    "required": ["contact", "text"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "agent_run",
                "description": "Запустити кодерського агента Antigravity CLI для рефакторингу чи генерації коду.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "prompt": {"type": "string"},
                    },
                    "required": ["prompt"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "pipeline_execute",
                "description": (
                    "Виконати одноразовий багатокроковий пайплайн (послідовність дій) прямо зараз без збереження у конфіг. "
                    "Використовуй або цей інструмент зі списком steps, або виклич кілька інструментів підряд."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "steps": {
                            "type": "array",
                            "description": "Впорядкований список кроків пайплайну [{skill, args}]",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "skill": {"type": "string", "description": "Ім'я навички (напр. app.launch, window.move, file.create)"},
                                    "args": {"type": "object", "description": "Аргументи кроку"},
                                },
                                "required": ["skill", "args"],
                            },
                        },
                    },
                    "required": ["steps"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "pipeline_save",
                "description": (
                    "Створити та зберегти новий багатокроковий пайплайн (макрос із послідовності навичок) "
                    "за голосовим тригером у config.local.toml і одразу виконати його."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "trigger": {"type": "string", "description": "Голосовий тригер українською для збереження"},
                        "steps": {
                            "type": "array",
                            "description": "Список кроків пайплайну [{skill, args}]",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "skill": {"type": "string"},
                                    "args": {"type": "object"},
                                },
                                "required": ["skill", "args"],
                            },
                        },
                        "run_now": {"type": "boolean", "default": True},
                    },
                    "required": ["trigger", "steps"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "skill_create",
                "description": (
                    "Створити та зберегти нову користувацьку функцію/навичку у навички FRIDAY "
                    "(потребує підтвердження Enter/Esc перед збереженням і запуском)."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Коротка назва нової навички"},
                        "trigger": {"type": "string", "description": "Голосова фраза для виклику навички"},
                        "action_type": {"type": "string", "enum": ["command", "url", "steps"]},
                        "command": {"type": "string", "description": "Безпечна команда або виконуваний файл"},
                        "url": {"type": "string", "description": "URL для відкриття, якщо action_type='url'"},
                        "steps": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "skill": {"type": "string"},
                                    "args": {"type": "object"},
                                },
                            },
                        },
                        "description": {"type": "string"},
                        "run_now": {"type": "boolean", "default": True},
                    },
                    "required": ["name", "trigger", "action_type"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "skill_run",
                "description": f"Запустити раніше збережений пайплайн ({pipelines_desc}) або навичку ({skills_desc}).",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Назва або тригер збереженої навички/пайплайну"},
                    },
                    "required": ["name"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "clarify",
                "description": "Попросити користувача уточнити неоднозначну команду.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "question": {"type": "string"},
                    },
                    "required": ["question"],
                },
            },
        },
    ]


class ClefToolPlanner:
    """Планувальник викликів навичок і пайплайнів через Experiential Labs API (model='clef')."""

    def __init__(
        self,
        config: AppConfig,
        openai_client: Any | None = None,
    ) -> None:
        self.config = config
        self._client = openai_client
        self._tools_cache_key: tuple[Any, ...] | None = None
        self._tools_cache: list[dict[str, Any]] | None = None
        self._prompt_cache_key: tuple[Any, ...] | None = None
        self._prompt_cache: str | None = None

    def has_api_key(self) -> bool:
        """Перевіряє наявність EXPLABS_API_KEY у конфігурації або змінних середовища."""
        key = (self.config.secrets.explabs_api_key or os.environ.get("EXPLABS_API_KEY", "")).strip()
        return bool(key)

    def _get_client(self) -> Any:
        """Ліниво ініціалізує OpenAI-клієнт для https://api.experientiallabs.ai/v1."""
        if self._client is None:
            from openai import OpenAI

            api_key = (self.config.secrets.explabs_api_key or os.environ.get("EXPLABS_API_KEY", "")).strip()
            if not api_key:
                raise RuntimeError("EXPLABS_API_KEY не налаштовано у .env або середовищі")

            has_gemini_fallback = bool((self.config.secrets.gemini_api_key or "").strip())
            base_timeout = float(self.config.llm.timeout_s)
            # Якщо є резервний Gemini, обмежуємо таймаут Clef, щоб встигнути переключитися на фолбек до загального таймауту
            req_timeout = min(base_timeout * 0.7, 8.5) if has_gemini_fallback and base_timeout > 4.0 else base_timeout

            self._client = OpenAI(
                base_url=self.config.llm.clef_base_url,
                api_key=api_key,
                timeout=req_timeout,
                max_retries=1,
            )
        return self._client

    def _get_tools_schema(self) -> list[dict[str, Any]]:
        """Повертає кешовану схему інструментів Clef (перебудовує лише при зміні конфігу)."""
        apps_t = tuple(sorted(self.config.apps.keys()))
        pipes_t = tuple(sorted(self.config.pipelines.keys()))
        skills_t = tuple(sorted(self.config.custom_skills.keys()))
        key = (apps_t, pipes_t, skills_t)
        if self._tools_cache_key != key or self._tools_cache is None:
            self._tools_cache_key = key
            self._tools_cache = build_clef_tools_schema(
                allowed_apps=list(apps_t),
                saved_pipelines=list(pipes_t),
                saved_skills=list(skills_t),
            )
        return self._tools_cache

    def build_system_prompt(self) -> str:
        """Формує системну інструкцію для моделі Clef з підтримкою tool calling, пайплайнів та авто-пошуку."""
        apps_keys = tuple(sorted(set(self.config.apps.keys()) | {"browser", "telegram", "code"}))
        syn_items = tuple(sorted((k, v) for k, v in self.config.app_synonyms.items()))
        pipelines_keys = tuple(sorted(self.config.pipelines.keys()))
        skills_keys = tuple(sorted(self.config.custom_skills.keys()))
        folders_labels = tuple(sorted(f.label for f in self.config.paths.allowed_folders))
        docs_keys = tuple(sorted(self.config.docs_sites.keys()))
        mon_pair = (self.config.monitors.primary + 1, self.config.monitors.docs + 1)

        cache_key = (apps_keys, syn_items, pipelines_keys, skills_keys, folders_labels, docs_keys, mon_pair)
        if self._prompt_cache_key == cache_key and self._prompt_cache is not None:
            return self._prompt_cache

        syn_str = ", ".join(f"{k}->{v}" for k, v in syn_items[:30]) if syn_items else "стандартні"
        folders_str = ", ".join(["робочий стіл", "завантаження", "документи", "проєкт", *folders_labels])
        docs_str = ", ".join(docs_keys) if docs_keys else "python, fastapi, pydantic, mdn"

        prompt = (
            "Ти — інтелектуальне ядро та планувальник пайплайнів голосового асистента FRIDAY для Windows (модель Clef). "
            "Користувач віддає команди українською або англійською, але FRIDAY ЗАВЖДИ ВІДПОВІДАЄ ТА ЗАПИТУЄ ВИКЛЮЧНО АНГЛІЙСЬКОЮ МОВОЮ (ALL RESPONSES AND QUESTIONS MUST BE STRICTLY IN ENGLISH).\n"
            "Правила маршрутизації та генерації пайплайнів:\n"
            f"1. Дозволені програми у [apps]: {', '.join(apps_keys)} (синоніми: {syn_str}).\n"
            "2. Якщо користувач просить відкрити/запустити програму, якої ще немає у [apps] — "
            "викликай `app_discover` з `name`, англійськими `candidates` (напр. ['spotify', 'spotify.exe']) "
            "та українськими `synonyms` (напр. ['спотіфай', 'музика']), щоб FRIDAY знайшла її, зберегла у навички та запустила.\n"
            "3. ГЕНЕРАЦІЯ ПАЙПЛАЙНІВ (багатокрокових команд):\n"
            "   - Якщо запит містить послідовність дій (напр. відкрити програму -> створити файл -> перемістити вікно, "
            "або розкласти дві програми ліворуч і праворуч на екрані) — виклич усі потрібні інструменти по черзі в одному запиті "
            "(multiple tool_calls) АБО виклич `pipeline_execute` зі списком `steps` у точному порядку виконання.\n"
            "   - У кроках `window_move`, `window_resize`, `window_focus`, які йдуть після `app_launch` або `app_discover`, "
            "завжди вказуй у `target` канонічну англійську назву щойно відкритої програми (напр. 'code', 'browser', 'spotify', 'notepad'), "
            "а не займенник 'його'.\n"
            "   - Якщо користувач просить розташувати дві програми поруч (split-screen: ліворуч і праворуч) — "
            "згенеруй кроки для обох вікон (`mode='left'` для першого та `mode='right'` для другого).\n"
            f"   - Монітори (1-based): основний = {mon_pair[0]}, документація/другий = {mon_pair[1]}.\n"
            f"   - Доступні директорії для файлів: {folders_str}. Сайти документації: {docs_str}.\n"
            "4. Якщо користувач просить запам'ятати/зберегти новий сценарій чи пайплайн за голосовою фразою — викликай `pipeline_save`.\n"
            "5. Якщо користувач просить створити нову кастомну функцію/команду — викликай `skill_create`.\n"
            f"6. Збережені пайплайни: {', '.join(pipelines_keys) or 'немає'}. "
            f"Збережені навички: {', '.join(skills_keys) or 'немає'}. Для їх запуску викликай `skill_run`.\n"
            "Якщо відповідаєш текстом без tool_calls — відповідай ТІЛЬКИ валідним JSON за схемою CommandIntent."
        )
        self._prompt_cache_key = cache_key
        self._prompt_cache = prompt
        return prompt

    def _build_single_intent(self, skill_name: str, parsed_args: dict[str, Any], confidence: float = 0.95) -> CommandIntent:
        """Створює один CommandIntent із нормалізованими аргументами та прапорцем needs_confirm."""
        norm_skill = normalize_skill_name(skill_name)
        norm_args = normalize_skill_args(norm_skill, parsed_args)
        needs_confirm = step_requires_confirm(norm_skill, norm_args)
        return CommandIntent(
            skill=norm_skill,  # type: ignore[arg-type]
            args=norm_args,
            confidence=confidence,
            needs_confirm=needs_confirm,
        )

    def _parse_tool_calls_to_intent(self, tool_calls: list[Any]) -> CommandIntent:
        """Перетворює один або декілька tool_calls з відповіді Clef на CommandIntent (або ланцюжок steps)."""
        intents: list[CommandIntent] = []
        for tc in tool_calls:
            fn = getattr(tc, "function", None)
            if fn is None and isinstance(tc, dict):
                fn = tc.get("function", {})
            raw_name = getattr(fn, "name", None) or (fn.get("name") if isinstance(fn, dict) else "")
            raw_args = getattr(fn, "arguments", None) or (fn.get("arguments") if isinstance(fn, dict) else "{}")

            skill_name = normalize_skill_name(str(raw_name))
            if isinstance(raw_args, str):
                loaded = _safe_json_loads(raw_args)
                parsed_args = dict(loaded) if isinstance(loaded, dict) else {}
            elif isinstance(raw_args, dict):
                parsed_args = dict(raw_args)
            else:
                parsed_args = {}

            # Якщо модель викликала pipeline_execute (або передала steps у skill_run без імені), розгортаємо кроки
            if skill_name == "pipeline.execute" or (
                skill_name == "skill.run" and not parsed_args.get("name") and isinstance(parsed_args.get("steps"), list)
            ):
                raw_steps = parsed_args.get("steps")
                if isinstance(raw_steps, str):
                    raw_steps = _safe_json_loads(raw_steps)
                if isinstance(raw_steps, list):
                    for st in raw_steps:
                        if isinstance(st, dict):
                            norm_st = normalize_step_dict(st)
                            intents.append(
                                self._build_single_intent(
                                    norm_st["skill"],
                                    norm_st["args"],
                                    confidence=float(norm_st.get("confidence", 0.95)),
                                )
                            )
                continue

            intents.append(self._build_single_intent(skill_name, parsed_args))

        if not intents:
            return CommandIntent(
                skill="clarify",
                args={"question": "Could not determine command steps. Please repeat."},
                confidence=0.0,
            )

        intents = propagate_pipeline_targets(intents)

        if len(intents) == 1:
            return intents[0]

        first = intents[0]
        return CommandIntent(
            skill=first.skill,
            args=first.args,
            confidence=min(i.confidence for i in intents),
            needs_confirm=any(i.needs_confirm for i in intents),
            steps=intents,
        )

    def plan_sync(self, text: str, system_prompt: str | None = None) -> CommandIntent | str:
        """Виконує синхронний запит до Clef з інструментами; повертає CommandIntent або сирий JSON-рядок."""
        client = self._get_client()
        sys_msg = system_prompt or self.build_system_prompt()
        tools = self._get_tools_schema()

        response = client.chat.completions.create(
            model=self.config.llm.clef_model,
            messages=[
                {"role": "system", "content": sys_msg},
                {"role": "user", "content": text},
            ],
            tools=tools,
            tool_choice="auto",
            temperature=0.0,
        )
        choices = getattr(response, "choices", None)
        if not choices:
            return ""
        msg = choices[0].message
        tool_calls = getattr(msg, "tool_calls", None)
        if tool_calls:
            return self._parse_tool_calls_to_intent(list(tool_calls))

        return str(getattr(msg, "content", "") or "")

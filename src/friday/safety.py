"""Перевірка безпеки команд, список заборонених патернів і доступ до дозволених папок."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Literal, Sequence

from friday.config import AllowedFolderEntry, SafetyConfig

SECRET_NAMES: frozenset[str] = frozenset({".env", "id_rsa", "id_ed25519", "credentials.json"})
SECRET_SUFFIXES: frozenset[str] = frozenset({".pem", ".key", ".pfx", ".p12", ".session"})

DANGEROUS_PATTERNS: list[str] = [
    r"\brm\s+-rf\b",
    r"\brmdir\s+/s\b",
    r"\bdel\s+(/f|/q|\*)\b",
    r"\bformat\s+[a-zA-Z]:",
    r"\breg\s+(add|delete)\b",
    r"\bgit\s+reset\s+--hard\b",
    r"\bgit\s+push\s+[^\n]*--force\b",
    r"\bmkfs\b",
    r"\bdd\s+if=",
    r"\bshutdown\b",
    r"\breboot\b",
]

DANGEROUS_RE = re.compile("|".join(DANGEROUS_PATTERNS), re.IGNORECASE)


def is_dangerous_command(text: str) -> bool:
    """Перевіряє, чи містить команда небезпечні деструктивні патерни."""
    return bool(DANGEROUS_RE.search(text))


def check_path_allowed(
    target_path: str | Path,
    allowed_folders: Sequence[AllowedFolderEntry],
    require_write: bool = False,
) -> Path:
    """Загальний перевіряльник шляху для файлових навичок ядра.

    Переконується, що шлях не є секретним файлом і лежить усередині однієї з папок
    списку `[paths].allowed_folders` з відповідним режимом (`read_only` або `read_write`).
    """
    resolved = Path(os.path.expandvars(str(target_path))).expanduser().resolve()

    if resolved.name.lower() in SECRET_NAMES or resolved.suffix.lower() in SECRET_SUFFIXES:
        raise PermissionError(f"Доступ до секретного файлу заблоковано: {resolved.name}")

    if not allowed_folders:
        raise PermissionError("Список дозволених папок [paths].allowed_folders порожній")

    matched_mode: Literal["read_only", "read_write"] | None = None
    for entry in allowed_folders:
        folder_root = Path(os.path.expandvars(entry.path)).expanduser().resolve()
        if resolved == folder_root or resolved.is_relative_to(folder_root):
            matched_mode = entry.mode
            if matched_mode == "read_write":
                break

    if matched_mode is None:
        raise PermissionError(f"Шлях '{resolved}' знаходиться поза дозволеними папками")

    if require_write and matched_mode != "read_write":
        raise PermissionError(f"Папка для '{resolved}' відкрита лише для читання (read_only)")

    return resolved


def _contains_dangerous_value(obj: Any) -> tuple[bool, str]:
    """Рекурсивно перевіряє рядки, списки та словники аргументів на наявність небезпечних патернів."""
    if isinstance(obj, str):
        if is_dangerous_command(obj):
            return (True, obj)
    elif isinstance(obj, dict):
        for k, v in obj.items():
            bad, sample = _contains_dangerous_value(v)
            if bad:
                return (True, f"{k}={sample}")
    elif isinstance(obj, (list, tuple)):
        for item in obj:
            bad, sample = _contains_dangerous_value(item)
            if bad:
                return (True, sample)
    return (False, "")


def validate_intent_safety(skill: str, args: dict[str, Any]) -> None:
    """Перевіряє аргументи команди на наявність заборонених деструктивних патернів перед виконанням."""
    if skill in {"agent.run", "app.launch", "app.discover", "pipeline.save", "skill.create", "skill.run"}:
        bad, detail = _contains_dangerous_value(args)
        if bad:
            raise PermissionError(
                f"Дію '{skill}' заблоковано safety.py: виявлено небезпечний патерн ({detail})."
            )


def should_require_confirmation(
    skill: str,
    needs_confirm_flag: bool,
    safety_config: SafetyConfig | None = None,
) -> bool:
    """Визначає, чи потрібне підтвердження (Confirm Gate) для навички."""
    cfg = safety_config or SafetyConfig()
    if skill == "telegram.send":
        return True if cfg.confirm_messages else needs_confirm_flag
    if skill in {"agent.run", "skill.create"}:
        return True if cfg.confirm_file_changes else needs_confirm_flag
    return needs_confirm_flag


def build_confirmation_prompt(skill: str, args: dict[str, Any]) -> str:
    """Формує зрозумілий рядок підтвердження (Enter / Esc) для користувача."""
    if skill == "telegram.send":
        contact = str(args.get("contact", "")).strip().capitalize()
        text = str(args.get("text", "")).strip()
        return f'{contact}: "{text}" — відправити? (Enter / Esc)'
    if skill == "agent.run":
        prompt = str(args.get("prompt", "")).strip()[:80]
        return f'Antigravity CLI: "{prompt}" — запустити сесію? (Enter / Esc)'
    if skill == "skill.create":
        name = str(args.get("name") or args.get("trigger") or "нова навичка").strip()
        action_type = str(args.get("action_type", "command")).strip()
        cmd = str(args.get("command") or args.get("description") or "").strip()[:70]
        return f'Зберегти та виконати нову навичку "{name}" [{action_type}: {cmd}]? (Enter / Esc)'
    if skill == "pipeline.save":
        name = str(args.get("name") or args.get("trigger") or "пайплайн").strip()
        steps_count = len(args.get("steps", [])) if isinstance(args.get("steps"), list) else 0
        return f'Зберегти пайплайн "{name}" ({steps_count} кроків)? (Enter / Esc)'
    return f"{skill}: {args} — виконати? (Enter / Esc)"

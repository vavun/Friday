"""Перевірка безпеки команд, список заборонених патернів і доступ до дозволених папок."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Literal, Sequence

from friday.config import AllowedFolderEntry

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

"""Перевірка безпеки команд, список заборонених патернів і confirm gate (похідне з Jarvis omni.py + handlers.py)."""

from __future__ import annotations

import re

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

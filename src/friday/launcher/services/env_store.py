"""Безпечне читання, маскування та атомарний запис ключів у .env без пошкодження сторонніх рядків."""

from __future__ import annotations

import os
import re
from pathlib import Path

from friday.config import PROJECT_ROOT


DEFAULT_ENV_PATH = PROJECT_ROOT / ".env"
DEFAULT_GITIGNORE_PATH = PROJECT_ROOT / ".gitignore"

_KEY_LINE_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)\s*$")


def mask_secret(value: str) -> str:
    """Маскує секретний ключ, залишаючи лише останні 4 символи (наприклад, '••••••••abcd')."""
    cleaned = (value or "").strip()
    if not cleaned:
        return ""
    if len(cleaned) <= 4:
        return "•" * len(cleaned)
    return "••••••••" + cleaned[-4:]


def ensure_env_in_gitignore(gitignore_path: Path | None = None) -> tuple[bool, bool]:
    """Перевіряє, чи .env є в .gitignore; якщо ні — додає його.

    Повертає кортеж (is_present_now, was_added_by_us).
    """
    path = gitignore_path or DEFAULT_GITIGNORE_PATH
    if path.exists():
        text = path.read_text(encoding="utf-8")
        lines = [ln.strip() for ln in text.splitlines()]
        if ".env" in lines or "/.env" in lines:
            return (True, False)
        new_text = text.rstrip("\r\n") + "\n.env\n"
        path.write_text(new_text, encoding="utf-8", newline="\n")
        return (True, True)

    path.write_text("# Секрети\n.env\n", encoding="utf-8", newline="\n")
    return (True, True)


class EnvStore:
    """Керує файлом .env зі збереженням коментарів та порядку рядків."""

    def __init__(
        self,
        env_path: Path | None = None,
        gitignore_path: Path | None = None,
    ) -> None:
        self.env_path = env_path or DEFAULT_ENV_PATH
        self.gitignore_path = gitignore_path or DEFAULT_GITIGNORE_PATH

    def read_all(self) -> dict[str, str]:
        """Читає всі пари KEY=VALUE з .env без логування їх значень."""
        if not self.env_path.exists():
            return {}
        result: dict[str, str] = {}
        for raw_line in self.env_path.read_text(encoding="utf-8").splitlines():
            stripped = raw_line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            match = _KEY_LINE_RE.match(raw_line)
            if not match:
                continue
            key, val = match.group(1), match.group(2).strip()
            if len(val) >= 2 and val[0] == val[-1] and val[0] in {"'", '"'}:
                val = val[1:-1]
            result[key] = val
        return result

    def get_key(self, key: str) -> str:
        """Повертає значення ключа з .env (або порожній рядок)."""
        return self.read_all().get(key, "")

    def get_masked(self, key: str) -> str:
        """Повертає замасковане значення ключа (лише останні 4 символи)."""
        return mask_secret(self.get_key(key))

    def update_keys(self, updates: dict[str, str]) -> bool:
        """Оновлює лише вказані ключі у .env, зберігаючи коментарі та всі інші рядки.

        Повертає True, якщо довелося автоматично додати .env у .gitignore.
        """
        _, was_added_to_gitignore = ensure_env_in_gitignore(self.gitignore_path)

        existing_lines: list[str] = []
        if self.env_path.exists():
            existing_lines = self.env_path.read_text(encoding="utf-8").splitlines()

        remaining = {k: v.strip() for k, v in updates.items()}
        new_lines: list[str] = []

        for line in existing_lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                new_lines.append(line)
                continue
            match = _KEY_LINE_RE.match(line)
            if match and match.group(1) in remaining:
                k = match.group(1)
                val = remaining.pop(k)
                new_lines.append(f"{k}={val}")
            else:
                new_lines.append(line)

        for k, val in remaining.items():
            new_lines.append(f"{k}={val}")

        content = "\n".join(new_lines) + "\n"
        self.env_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.env_path.with_suffix(".env.tmp")
        tmp_path.write_text(content, encoding="utf-8", newline="\n")
        os.replace(tmp_path, self.env_path)
        return was_added_to_gitignore

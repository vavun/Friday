"""Запуск програм виключно за білим списком із config.toml."""

from __future__ import annotations

import asyncio
import logging
import os
import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable

from friday.util.timing import measure_time

log = logging.getLogger("friday.skills.apps")

APP_NAME_SYNONYMS: dict[str, str] = {
    "телеграм": "telegram",
    "telegram": "telegram",
    "тг": "telegram",
    "браузер": "browser",
    "хром": "browser",
    "chrome": "browser",
    "browser": "browser",
    "код": "code",
    "вс код": "code",
    "vscode": "code",
    "vs code": "code",
    "редактор": "code",
    "code": "code",
}


class AppLauncher:
    """Запускає програми тільки з білого списку [apps] у config.toml (без довільного shell)."""

    def __init__(
        self,
        allowed_apps: dict[str, str],
        popen_fn: Callable[..., Any] | None = None,
    ) -> None:
        self.allowed_apps = {k.lower(): v for k, v in allowed_apps.items()}
        self._popen_fn = popen_fn or subprocess.Popen

    def resolve_app_key(self, raw_name: str) -> str:
        """Нормалізує назву програми до ключа білого списку або піднімає PermissionError."""
        cleaned = (raw_name or "").strip().lower()
        canonical = APP_NAME_SYNONYMS.get(cleaned, cleaned)
        if canonical not in self.allowed_apps:
            raise PermissionError(
                f"Програму '{raw_name}' не знайдено у білому списку [apps] config.toml "
                f"(дозволені: {sorted(self.allowed_apps.keys())})"
            )
        return canonical

    def launch_sync(self, name: str) -> dict[str, Any]:
        """Синхронно запускає дозволену програму без shell=True."""
        with measure_time("app.launch"):
            key = self.resolve_app_key(name)
            raw_cmd = os.path.expandvars(self.allowed_apps[key]).strip()

            if Path(raw_cmd).exists():
                cmd_args = [raw_cmd]
            else:
                parts = shlex.split(raw_cmd, posix=False)
                exe = parts[0].strip('"')
                resolved = shutil.which(exe) or exe
                cmd_args = [resolved, *parts[1:]]

            self._popen_fn(cmd_args, shell=False)
            log.info("🚀 Запущено програму з білого списку: %s -> %s", key, cmd_args[0])
            return {"app": key, "command": cmd_args[0], "status": "launched"}


class AppLaunchSkill:
    """Навичка app.launch."""

    name = "app.launch"

    def __init__(self, launcher: AppLauncher) -> None:
        self.launcher = launcher

    async def execute(self, args: dict[str, Any]) -> dict[str, Any]:
        name = str(args.get("name", ""))
        return await asyncio.to_thread(self.launcher.launch_sync, name)

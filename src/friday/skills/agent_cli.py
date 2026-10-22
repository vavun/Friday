"""Етап 8: Адаптер інтеграції з Antigravity CLI (agy / antigravity) без auto-approve та з перевіркою безпеки."""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable, Sequence

from friday.config import AgentConfig, AllowedFolderEntry, PROJECT_ROOT
from friday.safety import is_dangerous_command
from friday.skills.windows import WindowManager
from friday.util.timing import measure_time

log = logging.getLogger("friday.skills.agent_cli")

# Реальні прапорці з виводу `agy --help`:
#   -i / --prompt-interactive : Run an initial prompt interactively and continue the session
#   -p / --print              : Run a single prompt non-interactively and print the response
#   --dangerously-skip-permissions : ЗАБОРОНЕНО використовувати!
FORBIDDEN_CLI_FLAGS: frozenset[str] = frozenset(
    {
        "--dangerously-skip-permissions",
        "--auto-approve",
        "--yes",
        "-y",
    }
)


def resolve_agent_executable(configured_cmd: str = "antigravity") -> str:
    """Знаходить виконуваний файл Antigravity CLI (підтримує як 'antigravity', так і 'agy')."""
    cleaned = (configured_cmd or "antigravity").strip()
    hit = shutil.which(cleaned)
    if hit:
        return hit
    if cleaned.lower() == "antigravity":
        agy_hit = shutil.which("agy")
        if agy_hit:
            return agy_hit
    return cleaned


def resolve_project_workdir(
    allowed_folders: Sequence[AllowedFolderEntry] | None = None,
    editor_window_title: str = "",
    fallback_dir: Path | None = None,
) -> Path:
    """Визначає робочу директорію активного проєкту за заголовком вікна редактора або з config.paths."""
    folders = list(allowed_folders or [])
    title_lower = (editor_window_title or "").lower()

    if title_lower and folders:
        for entry in folders:
            p = Path(os.path.expandvars(entry.path)).expanduser()
            if (
                entry.label.lower() in title_lower
                or p.name.lower() in title_lower
            ) and p.exists():
                return p.resolve()

    # Пріоритет: перша існуюча папка з правами read_write, потім будь-яка дозволена
    for entry in folders:
        if entry.mode == "read_write":
            p = Path(os.path.expandvars(entry.path)).expanduser()
            if p.exists() and p.is_dir():
                return p.resolve()

    for entry in folders:
        p = Path(os.path.expandvars(entry.path)).expanduser()
        if p.exists() and p.is_dir():
            return p.resolve()

    return (fallback_dir or PROJECT_ROOT).resolve()


def build_agent_cli_command(
    prompt: str,
    executable: str,
    interactive: bool = True,
) -> list[str]:
    """Формує безпечний список аргументів для запуску сесії Antigravity CLI (без shell=True і без auto-approve)."""
    cleaned_prompt = (prompt or "").strip()
    if not cleaned_prompt:
        raise ValueError("Промпт для Antigravity CLI не може бути порожнім")

    if is_dangerous_command(cleaned_prompt):
        raise PermissionError(
            f"Промпт заблоковано фільтром безпеки safety.py (виявлено небезпечний патерн): {cleaned_prompt}"
        )

    flag = "-i" if interactive else "-p"
    cmd = [executable, flag, cleaned_prompt]

    for token in cmd:
        if token.lower() in FORBIDDEN_CLI_FLAGS:
            raise PermissionError(f"Заборонений прапорець auto-approve у команді: {token}")

    return cmd


class AgentCliService:
    """Запускає інтерактивну сесію Antigravity CLI у робочій папці проєкту після ручного підтвердження."""

    def __init__(
        self,
        agent_config: AgentConfig | None = None,
        allowed_folders: Sequence[AllowedFolderEntry] | None = None,
        window_manager: WindowManager | None = None,
        popen_fn: Callable[..., Any] | None = None,
    ) -> None:
        self.agent_config = agent_config or AgentConfig()
        if self.agent_config.auto_approve:
            raise ValueError("auto_approve категорично заборонений для AgentCliService")
        self.allowed_folders = list(allowed_folders or [])
        self.window_manager = window_manager
        self._popen_fn = popen_fn or subprocess.Popen

    def run_session_sync(
        self,
        prompt: str,
        confirmed: bool = False,
        interactive: bool = True,
    ) -> dict[str, Any]:
        """Синхронно відкриває сесію Antigravity CLI (тільки при confirmed=True)."""
        if not confirmed:
            raise PermissionError(
                "Запуск Antigravity CLI вимагає обов'язкового ручного підтвердження (Confirm Gate)."
            )

        with measure_time("agent.run"):
            editor_title = ""
            if self.window_manager is not None:
                try:
                    code_hwnd = self.window_manager.find_window("code")
                    for w in self.window_manager.backend.list_visible_windows():
                        if w.hwnd == code_hwnd:
                            editor_title = w.title
                            break
                except Exception:
                    pass

            workdir = resolve_project_workdir(
                allowed_folders=self.allowed_folders,
                editor_window_title=editor_title,
            )
            exe = resolve_agent_executable(self.agent_config.command)
            cmd = build_agent_cli_command(prompt=prompt, executable=exe, interactive=interactive)

            creationflags = 0
            if os.name == "nt" and interactive:
                creationflags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0x00000010)

            self._popen_fn(
                cmd,
                cwd=str(workdir),
                shell=False,
                creationflags=creationflags,
            )
            log.info("🤖 Відкрито сесію Antigravity CLI у %s: %s", workdir, cmd[0])
            return {
                "status": "started",
                "command": cmd,
                "workdir": str(workdir),
                "interactive": interactive,
            }


class AgentRunSkill:
    """Навичка agent.run."""

    name = "agent.run"

    def __init__(self, service: AgentCliService | None = None) -> None:
        self.service = service or AgentCliService()

    async def execute(self, args: dict[str, Any]) -> dict[str, Any]:
        prompt = str(args.get("prompt", "")).strip()
        confirmed = bool(args.get("_confirmed", False))
        interactive = bool(args.get("interactive", True))
        return await asyncio.to_thread(
            self.service.run_session_sync,
            prompt,
            confirmed,
            interactive,
        )

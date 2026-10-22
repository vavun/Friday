"""Керування автозапуском FRIDAY у фоновому режимі без консолі (pythonw) через папку Startup Windows."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from friday.config import PROJECT_ROOT

AUTOSTART_FILENAME = "FridayAssistantAutostart.cmd"


def default_startup_dir() -> Path:
    """Повертає шлях до папки автозапуску поточного користувача Windows (shell:startup)."""
    appdata = os.environ.get("APPDATA", "")
    if appdata:
        return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
    return Path.home() / "AppData" / "Roaming" / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def resolve_pythonw_executable(project_root: Path | None = None) -> str:
    """Знаходить шлях до pythonw.exe у .venv або поточному інтерпретаторі Python."""
    root = project_root or PROJECT_ROOT
    venv_pythonw = root / ".venv" / "Scripts" / "pythonw.exe"
    if venv_pythonw.exists():
        return str(venv_pythonw)

    current_exe = Path(sys.executable)
    sibling_pythonw = current_exe.with_name("pythonw.exe")
    if sibling_pythonw.exists():
        return str(sibling_pythonw)

    return str(current_exe)


def build_autostart_script(
    project_root: Path | None = None,
    target_module: str = "friday.launcher",
    minimized: bool = True,
) -> str:
    """Формує вміст стартового .cmd-файлу для запуску без консольного вікна через pythonw.exe."""
    root = (project_root or PROJECT_ROOT).resolve()
    pythonw = resolve_pythonw_executable(root)
    flag = " --minimized" if minimized and target_module == "friday.launcher" else ""
    return (
        "@echo off\n"
        f'cd /d "{root}"\n'
        f'set "PYTHONPATH={root / "src"}"\n'
        f'start "" "{pythonw}" -m {target_module}{flag}\n'
    )


def enable_autostart(
    startup_dir: Path | None = None,
    project_root: Path | None = None,
    target_module: str = "friday.launcher",
    minimized: bool = True,
) -> Path:
    """Створює файл автозапуску у папці Startup Windows."""
    target_dir = startup_dir or default_startup_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    script_path = target_dir / AUTOSTART_FILENAME
    content = build_autostart_script(
        project_root=project_root,
        target_module=target_module,
        minimized=minimized,
    )
    script_path.write_text(content, encoding="utf-8")
    return script_path


def disable_autostart(startup_dir: Path | None = None) -> bool:
    """Видаляє файл автозапуску з папки Startup Windows."""
    target_dir = startup_dir or default_startup_dir()
    script_path = target_dir / AUTOSTART_FILENAME
    if script_path.exists():
        script_path.unlink()
        return True
    return False


def is_autostart_enabled(startup_dir: Path | None = None) -> bool:
    """Перевіряє, чи увімкнено автозапуск FRIDAY."""
    target_dir = startup_dir or default_startup_dir()
    return (target_dir / AUTOSTART_FILENAME).exists()

"""Правила безпеки та валідації шляхів для сторінки 'Дозволені папки' (Folders)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True, frozen=True)
class PathValidationResult:
    """Результат перевірки папки перед додаванням у білий список [paths].allowed_folders."""

    ok: bool
    normalized_path: str
    exists: bool
    error: str = ""
    warning: str = ""


_BROAD_FOLDER_NAMES: frozenset[str] = frozenset(
    {
        "desktop",
        "робочий стіл",
        "downloads",
        "завантаження",
        "documents",
        "документи",
        "onedrive",
    }
)


def _is_same_or_parent(candidate: Path, target: Path) -> bool:
    """Перевіряє, чи candidate збігається з target або є його предком/нащадком за потреби."""
    try:
        candidate.relative_to(target)
        return True
    except ValueError:
        return False


def validate_folder_path(
    raw_path: str | Path,
    home_dir: Path | None = None,
    require_exists: bool = False,
) -> PathValidationResult:
    """Нормалізує шлях та перевіряє його на заборонені системні/кореневі директорії.

    Заборонено:
    - Корінь диска (C:\\, D:\\ тощо)
    - Системні каталоги (Windows, Program Files, Program Files (x86), ProgramData, System Volume Information, $Recycle.Bin)
    - Усю домашню директорію користувача (C:\\Users\\<User>) або каталог C:\\Users
    - Кореневу папку AppData

    Попередження (warning):
    - Дуже широкі папки профілю (Desktop, Downloads, Documents, OneDrive) або папки першого рівня від кореня диска з великою кількістю елементів.
    """
    raw_str = str(raw_path or "").strip().strip('"').strip("'")
    if not raw_str:
        return PathValidationResult(
            ok=False,
            normalized_path="",
            exists=False,
            error="Шлях до папки не може бути порожнім.",
        )

    expanded = os.path.expandvars(os.path.expanduser(raw_str))
    try:
        resolved = Path(expanded).resolve()
    except Exception as exc:
        return PathValidationResult(
            ok=False,
            normalized_path=raw_str,
            exists=False,
            error=f"Некоректний синтаксис шляху: {exc}",
        )

    norm_str = str(resolved)
    exists = resolved.exists() and resolved.is_dir()

    # 1. Заборона кореня диска (наприклад C:\)
    if resolved.parent == resolved:
        return PathValidationResult(
            ok=False,
            normalized_path=norm_str,
            exists=exists,
            error="Заборонено додавати корінь диска цілком (оберіть конкретну робочу папку).",
        )

    drive_root = Path(resolved.anchor) if resolved.anchor else Path("C:\\")
    user_home = (home_dir or Path.home()).resolve()
    users_root = user_home.parent

    # 2. Заборона всієї домашньої директорії, C:\Users або кореня AppData
    if resolved in {user_home, users_root, user_home / "AppData"}:
        return PathValidationResult(
            ok=False,
            normalized_path=norm_str,
            exists=exists,
            error="Заборонено надавати доступ до всієї домашньої директорії користувача або кореня AppData.",
        )

    # 3. Заборона системних каталогів Windows
    system_dirs: list[Path] = [
        drive_root / "Windows",
        drive_root / "Program Files",
        drive_root / "Program Files (x86)",
        drive_root / "ProgramData",
        drive_root / "System Volume Information",
        drive_root / "$Recycle.Bin",
    ]
    for env_var in ("SystemRoot", "windir", "ProgramFiles", "ProgramFiles(x86)", "ProgramData"):
        val = os.environ.get(env_var)
        if val:
            try:
                system_dirs.append(Path(val).resolve())
            except Exception:
                pass

    for sys_dir in system_dirs:
        try:
            sys_resolved = sys_dir.resolve()
        except Exception:
            sys_resolved = sys_dir
        if _is_same_or_parent(resolved, sys_resolved):
            return PathValidationResult(
                ok=False,
                normalized_path=norm_str,
                exists=exists,
                error=f"Заборонено додавати системний каталог ({sys_resolved.name}).",
            )

    if require_exists and not exists:
        return PathValidationResult(
            ok=False,
            normalized_path=norm_str,
            exists=False,
            error="Вказана папка не існує на диску.",
        )

    # 4. Попередження про дуже широкі папки
    warning = ""
    folder_name_lower = resolved.name.lower()
    if resolved.parent == user_home and folder_name_lower in _BROAD_FOLDER_NAMES:
        warning = (
            f"Увага: папка «{resolved.name}» є широким каталогом профілю. "
            "Рекомендується обирати вужчу підпапку конкретного проєкту."
        )
    elif resolved.parent == drive_root:
        warning = (
            f"Увага: папка «{resolved.name}» розташована безпосередньо в корені диска. "
            "Переконайтеся, що вона не містить сторонніх приватних файлів."
        )

    return PathValidationResult(
        ok=True,
        normalized_path=norm_str,
        exists=exists,
        warning=warning,
    )


def check_folder_status(path_str: str) -> tuple[bool, str]:
    """Перевіряє доступність папки для відображення в таблиці UI."""
    try:
        p = Path(os.path.expandvars(os.path.expanduser(path_str))).resolve()
        if p.exists() and p.is_dir():
            return (True, "Існує")
        return (False, "Недоступна")
    except Exception:
        return (False, "Недоступна")

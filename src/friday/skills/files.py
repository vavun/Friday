"""Розумні детерміновані операції з файлами та папками без виклику LLM: відкриття, створення, запуск у програмі."""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from friday.config import PROJECT_ROOT, AllowedFolderEntry
from friday.skills.apps import APP_NAME_SYNONYMS, AppLauncher
from friday.util.timing import measure_time

log = logging.getLogger("friday.skills.files")

FILE_KIND_EXTENSIONS: dict[str, str] = {
    "python": ".py",
    "пайтон": ".py",
    "пітон": ".py",
    "py": ".py",
    "text": ".txt",
    "текстовий": ".txt",
    "текст": ".txt",
    "txt": ".txt",
    "markdown": ".md",
    "маркдаун": ".md",
    "md": ".md",
    "json": ".json",
    "джейсон": ".json",
    "html": ".html",
    "js": ".js",
    "javascript": ".js",
    "css": ".css",
    " bat": ".bat",
    "батник": ".bat",
}


def get_known_user_dirs(
    allowed_folders: list[AllowedFolderEntry] | None = None,
) -> dict[str, Path]:
    """Повертає словник відомих папок користувача (Робочий стіл, Завантаження, Документи, Проєкт, allowed_folders)."""
    home = Path.home()
    dirs: dict[str, Path] = {}

    # Робочий стіл (враховуємо OneDrive\Робочий стіл та стандартний Desktop)
    desktop_candidates = [
        home / "OneDrive" / "Робочий стіл",
        home / "OneDrive" / "Desktop",
        home / "Desktop",
        home / "Робочий стіл",
    ]
    desktop_dir = next((p for p in desktop_candidates if p.exists()), home / "Desktop")
    for alias in (
        "desktop",
        "десктоп",
        "робочий стіл",
        "робочому столі",
        "робочого столу",
        "стіл",
    ):
        dirs[alias] = desktop_dir

    # Завантаження
    dl_candidates = [home / "Downloads", home / "Завантаження"]
    dl_dir = next((p for p in dl_candidates if p.exists()), home / "Downloads")
    for alias in (
        "downloads",
        "download",
        "даунлоадс",
        "завантаження",
        "завантаженнях",
        "завантажень",
    ):
        dirs[alias] = dl_dir

    # Документи
    doc_candidates = [
        home / "OneDrive" / "Документи",
        home / "OneDrive" / "Documents",
        home / "Documents",
        home / "Документи",
    ]
    doc_dir = next((p for p in doc_candidates if p.exists()), home / "Documents")
    for alias in ("documents", "docs", "документи", "документах", "документів"):
        dirs[alias] = doc_dir

    # Поточний проєкт
    for alias in ("project", "проєкт", "проект", "проєкті", "проекті", "friday", "пятниця", "п'ятниця"):
        dirs[alias] = PROJECT_ROOT

    # Дозволені папки з конфігу
    for entry in allowed_folders or []:
        p = Path(os.path.expandvars(os.path.expanduser(entry.path)))
        if p.exists():
            dirs[entry.label.strip().lower()] = p
            dirs[p.name.strip().lower()] = p

    return dirs


class FileSystemService:
    """Сервіс швидкого пошуку, створення та відкриття файлів/папок без залучення LLM."""

    def __init__(
        self,
        allowed_folders: list[AllowedFolderEntry] | None = None,
        app_launcher: AppLauncher | None = None,
    ) -> None:
        self.allowed_folders = allowed_folders or []
        self.app_launcher = app_launcher or AppLauncher({})
        self.last_created_or_opened: Path | None = None

    def resolve_directory(self, location_hint: str | None) -> Path:
        """Визначає цільову папку за текстовою підказкою (наприклад, 'робочий стіл' або 'завантаження')."""
        known = get_known_user_dirs(self.allowed_folders)
        if not location_hint:
            if self.allowed_folders:
                first = Path(os.path.expandvars(os.path.expanduser(self.allowed_folders[0].path)))
                if first.exists():
                    return first
            return known["desktop"]

        hint = location_hint.strip().lower().strip("\"'")
        if hint in known:
            return known[hint]

        for key, path in known.items():
            if key in hint or hint in key:
                return path

        direct = Path(os.path.expandvars(os.path.expanduser(location_hint.strip("\"'"))))
        if direct.exists() and direct.is_dir():
            return direct

        # Пошук підпапки з такою назвою у базових директоріях
        search_roots = list({known["desktop"], known["downloads"], known["documents"], PROJECT_ROOT})
        for entry in self.allowed_folders:
            ep = Path(os.path.expandvars(os.path.expanduser(entry.path)))
            if ep.exists():
                search_roots.append(ep)

        for root in search_roots:
            candidate = root / location_hint.strip("\"'")
            if candidate.exists() and candidate.is_dir():
                return candidate

        return known["desktop"]

    def find_target_path(
        self,
        target_name: str,
        location_hint: str | None = None,
        prefer_dir: bool | None = None,
    ) -> Path | None:
        """Знаходить існуючу папку або файл за назвою або псевдонімом."""
        raw = (target_name or "").strip().strip("\"'")
        if not raw:
            return None

        known = get_known_user_dirs(self.allowed_folders)
        low = raw.lower()
        if low in known and not location_hint:
            return known[low]

        direct = Path(os.path.expandvars(os.path.expanduser(raw)))
        if direct.exists():
            return direct.resolve()

        search_roots: list[Path] = []
        if location_hint:
            search_roots.append(self.resolve_directory(location_hint))
        for root in (known["desktop"], known["downloads"], known["documents"], PROJECT_ROOT):
            if root not in search_roots and root.exists():
                search_roots.append(root)
        for entry in self.allowed_folders:
            ep = Path(os.path.expandvars(os.path.expanduser(entry.path)))
            if ep.exists() and ep not in search_roots:
                search_roots.append(ep)

        # 1. Точний збіг імені або імені без розширення на першому рівні
        for root in search_roots:
            exact = root / raw
            if exact.exists():
                if prefer_dir is True and not exact.is_dir():
                    continue
                if prefer_dir is False and not exact.is_file():
                    continue
                return exact.resolve()

            try:
                for child in root.iterdir():
                    c_name = child.name.lower()
                    c_stem = child.stem.lower()
                    if low in (c_name, c_stem):
                        if prefer_dir is True and not child.is_dir():
                            continue
                        if prefer_dir is False and not child.is_file():
                            continue
                        return child.resolve()
            except Exception:
                continue

        # 2. Нечіткий збіг підрядка на першому рівні
        for root in search_roots:
            try:
                for child in root.iterdir():
                    if low in child.name.lower():
                        if prefer_dir is True and not child.is_dir():
                            continue
                        if prefer_dir is False and not child.is_file():
                            continue
                        return child.resolve()
            except Exception:
                continue

        return None

    def create_item(
        self,
        name: str,
        item_type: str = "file",
        location_hint: str | None = None,
        file_kind: str | None = None,
        initial_content: str = "",
        open_after: bool = False,
        open_with_app: str | None = None,
    ) -> Path:
        """Створює файл або папку у визначеній директорії та за потреби відкриває її."""
        clean_name = (name or "").strip().strip("\"'")
        if not clean_name:
            clean_name = "Нова папка" if item_type == "folder" else "note.txt"

        base_dir = self.resolve_directory(location_hint)
        base_dir.mkdir(parents=True, exist_ok=True)

        if item_type == "folder":
            target_path = (base_dir / clean_name).resolve()
            target_path.mkdir(parents=True, exist_ok=True)
            log.info("📁 Створено папку: %s", target_path)
        else:
            if "." not in Path(clean_name).name:
                ext = ".txt"
                if file_kind:
                    ext = FILE_KIND_EXTENSIONS.get(file_kind.lower().strip(), ".txt")
                clean_name = f"{clean_name}{ext}"
            target_path = (base_dir / clean_name).resolve()
            target_path.parent.mkdir(parents=True, exist_ok=True)
            if not target_path.exists() or initial_content:
                target_path.write_text(initial_content, encoding="utf-8")
            log.info("📄 Створено файл: %s", target_path)

        self.last_created_or_opened = target_path
        if open_after or open_with_app:
            self.open_path(target_path, app_name=open_with_app)
        return target_path

    def open_path(self, target_path: Path, app_name: str | None = None) -> str:
        """Відкриває файл або папку у вказаній програмі або за замовчуванням у Windows."""
        self.last_created_or_opened = target_path
        if not app_name:
            if hasattr(os, "startfile"):
                os.startfile(str(target_path))  # type: ignore[attr-defined]
            else:
                subprocess.Popen(["explorer", str(target_path)], shell=False)
            log.info("📂 Відкрито шлях за замовчуванням: %s", target_path)
            return str(target_path)

        app_low = app_name.strip().lower()
        # Блокнот / Notepad
        if app_low in ("notepad", "блокнот", "блокноті", "текстовий редактор"):
            subprocess.Popen(["notepad.exe", str(target_path)], shell=False)
            log.info("📝 Відкрито %s у Notepad", target_path)
            return f"notepad.exe {target_path}"

        # VS Code / редактор коду
        if app_low in ("code", "vscode", "vs code", "код", "коді", "вс код", "редактор", "редакторі"):
            code_exe = shutil.which("code") or shutil.which("code.cmd") or "code"
            if code_exe == "code" and "code" in self.app_launcher.whitelist:
                code_exe = self.app_launcher.whitelist["code"]
            subprocess.Popen([code_exe, str(target_path)], shell=False)
            log.info("💻 Відкрито %s у VS Code", target_path)
            return f"{code_exe} {target_path}"

        # Провідник Windows
        if app_low in ("explorer", "провідник", "провіднику"):
            if target_path.is_file():
                subprocess.Popen(["explorer.exe", f"/select,{target_path}"], shell=False)
            else:
                subprocess.Popen(["explorer.exe", str(target_path)], shell=False)
            return f"explorer.exe {target_path}"

        # Будь-яка інша програма з білого списку [apps]
        canonical = self.app_launcher.resolve_app_key(app_low) or APP_NAME_SYNONYMS.get(app_low, app_low)
        if canonical in self.app_launcher.whitelist:
            raw_cmd = os.path.expandvars(self.app_launcher.whitelist[canonical].strip().strip('"'))
            subprocess.Popen([raw_cmd, str(target_path)], shell=False)
            log.info("🚀 Відкрито %s у програмі %s", target_path, canonical)
            return f"{raw_cmd} {target_path}"

        if hasattr(os, "startfile"):
            os.startfile(str(target_path))  # type: ignore[attr-defined]
        return str(target_path)


class FileCreateSkill:
    """Навичка file.create: детерміноване створення файлу або папки та необов'язкове відкриття."""

    name = "file.create"

    def __init__(self, service: FileSystemService) -> None:
        self.service = service

    async def execute(self, args: dict[str, Any]) -> dict[str, Any]:
        item_type = str(args.get("item_type", "file"))
        name = str(args.get("name", "")).strip()
        location = args.get("location")
        file_kind = args.get("file_kind")
        open_after = bool(args.get("open_after", False))
        open_with_app = args.get("app")

        with measure_time("file.create"):
            created = await asyncio.to_thread(
                self.service.create_item,
                name=name,
                item_type=item_type,
                location_hint=str(location) if location else None,
                file_kind=str(file_kind) if file_kind else None,
                open_after=open_after,
                open_with_app=str(open_with_app) if open_with_app else None,
            )
        return {
            "ok": True,
            "status": "created",
            "name": created.name,
            "path": str(created),
        }


class FileOpenSkill:
    """Навичка file.open: відкриття папки або файлу у Провіднику чи вказаній програмі."""

    name = "file.open"

    def __init__(self, service: FileSystemService) -> None:
        self.service = service

    async def execute(self, args: dict[str, Any]) -> dict[str, Any]:
        target = str(args.get("target", "")).strip()
        location = args.get("location")
        app_name = args.get("app")
        prefer_dir = args.get("prefer_dir")

        with measure_time("file.open"):
            found = await asyncio.to_thread(
                self.service.find_target_path,
                target_name=target,
                location_hint=str(location) if location else None,
                prefer_dir=prefer_dir if isinstance(prefer_dir, bool) else None,
            )
            if found is None and target.lower() in ("останній", "його", "її", "цей файл", "цю папку"):
                found = self.service.last_created_or_opened

            if found is None:
                raise ValueError(f"Не вдалося знайти файл або папку «{target}».")

            cmd = await asyncio.to_thread(
                self.service.open_path,
                found,
                str(app_name) if app_name else None,
            )
        return {
            "ok": True,
            "status": "opened",
            "name": found.name,
            "path": str(found),
            "command": cmd,
        }

"""Тести для операцій із файлами/папками (file.create, file.open), складених промптів та сканера застосунків."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from PySide6.QtWidgets import QApplication

from friday.app import FridayApp
from friday.config import AllowedFolderEntry, AppConfig
from friday.launcher.pages.apps import AppsPage
from friday.launcher.services.app_scanner import (
    ScannedApp,
    default_app_shortcut_dirs,
    scan_registry_apps,
    scan_start_menu_apps,
)
from friday.router.rules import match_fast_rule, split_compound_commands
from friday.router.schema import CommandIntent
from friday.skills.apps import AppLauncher
from friday.skills.files import FileCreateSkill, FileOpenSkill, FileSystemService


def test_split_compound_commands() -> None:
    """Перевіряє розбиття складних голосових запитів на самостійні кроки."""
    t1 = "відкрий ютуб перенеси на другий монітор і прикріпи праворуч"
    segs1 = split_compound_commands(t1)
    assert len(segs1) == 3
    assert "відкрий ютуб" in segs1[0]
    assert "перенеси на другий монітор" in segs1[1]
    assert "прикріпи праворуч" in segs1[2]

    t2 = "створи файл app.py на робочому столі і відкрий в блокноті"
    segs2 = split_compound_commands(t2)
    assert len(segs2) == 2
    assert "створи файл app.py" in segs2[0]
    assert "відкрий в блокноті" in segs2[1]

    t3 = "запусти браузер а потім перенеси його на перший монітор"
    segs3 = split_compound_commands(t3)
    assert len(segs3) == 2
    assert "запусти браузер" in segs3[0]
    assert "перенеси його на перший монітор" in segs3[1]


def test_match_fast_rule_compound_intent() -> None:
    """Перевіряє, що роутер формує CommandIntent зі списком steps для складеного промпту."""
    text = "відкрий ютуб перенеси на другий монітор і прикріпи вправо"
    intent = match_fast_rule(text)
    assert intent is not None
    assert len(intent.steps) == 3
    assert intent.steps[0].skill == "web.search"
    assert intent.steps[0].args.get("open_site") == "youtube"
    assert intent.steps[1].skill == "window.move"
    assert intent.steps[1].args.get("monitor") == 2
    assert intent.steps[2].skill == "window.move"
    assert intent.steps[2].args.get("mode") == "right"


def test_match_fast_rule_file_create_and_open() -> None:
    """Перевіряє детерміноване розпізнавання створення файлів, папок та їх відкриття."""
    # 1. Створення файлу
    i1 = match_fast_rule("створи файл script.py на робочому столі")
    assert i1 is not None
    assert i1.skill == "file.create"
    assert i1.args.get("name") == "script.py"
    assert i1.args.get("location") == "робочому столі"
    assert i1.args.get("item_type") == "file"

    # 2. Створення папки
    i2 = match_fast_rule("створи папку мої_документи")
    assert i2 is not None
    assert i2.skill == "file.create"
    assert i2.args.get("item_type") == "folder"
    assert i2.args.get("name") == "мої_документи"

    # 3. Відкриття файлу у певній програмі
    i3 = match_fast_rule("відкрий script.py в блокноті")
    assert i3 is not None
    assert i3.skill == "file.open"
    assert i3.args.get("target") == "script.py"
    assert i3.args.get("app") == "блокноті"

    # 4. Відкриття папки
    i4 = match_fast_rule("відкрий папку завантаження")
    assert i4 is not None
    assert i4.skill == "file.open"
    assert i4.args.get("target") == "завантаження"
    assert i4.args.get("prefer_dir") is True


def test_file_system_service_create_and_open(tmp_path: Path) -> None:
    """Перевіряє безпосереднє створення та пошук файлів/папок на файловій системі."""
    allowed = [AllowedFolderEntry(path=str(tmp_path), label="тест_папка", recursive=True)]
    service = FileSystemService(allowed_folders=allowed)

    # 1. Створення папки
    created_dir = service.create_item("my_subfolder", item_type="folder", location_hint="тест_папка")
    assert created_dir.exists() and created_dir.is_dir()

    # 2. Створення файлу з типом python
    created_file = service.create_item(
        "test_code",
        item_type="file",
        location_hint="тест_папка",
        file_kind="python",
        initial_content="print('hello')",
    )
    assert created_file.exists() and created_file.is_file()
    assert created_file.suffix == ".py"
    assert created_file.read_text(encoding="utf-8") == "print('hello')"

    # 3. Пошук файлу за назвою
    found = service.find_target_path("test_code.py", location_hint="тест_папка")
    assert found is not None
    assert found.resolve() == created_file.resolve()

    # 4. Пошук створеної папки
    found_dir = service.find_target_path("my_subfolder", location_hint="тест_папка", prefer_dir=True)
    assert found_dir is not None
    assert found_dir.is_dir()

    # 5. Створення bat-файлу (перевірка виправленого ключа "bat")
    created_bat = service.create_item("run_task", item_type="file", location_hint="тест_папка", file_kind="bat")
    assert created_bat.suffix == ".bat"

    # 6. Відкриття файлу у дозволеній програмі з білого списку (без AttributeError: whitelist)
    launcher = AppLauncher({"custom_editor": "custom_editor.exe --flag"})
    assert hasattr(launcher, "whitelist")
    assert "custom_editor" in launcher.whitelist

    service_with_apps = FileSystemService(allowed_folders=allowed, app_launcher=launcher)
    with patch("subprocess.Popen") as mock_popen:
        cmd_result = service_with_apps.open_path(created_file, app_name="custom_editor")
        assert "custom_editor.exe" in cmd_result
        mock_popen.assert_called_once()
        call_args = mock_popen.call_args[0][0]
        assert "custom_editor.exe" in call_args[0]
        assert str(created_file) in call_args[-1]


@pytest.mark.asyncio
async def test_friday_app_compound_execution(tmp_path: Path) -> None:
    """Перевіряє, що FridayApp послідовно виконує кожен крок ланцюжка steps."""
    cfg = AppConfig()
    app = FridayApp(config=cfg)

    executed_skills: list[str] = []

    mock_skill1 = MagicMock()
    mock_skill1.name = "app.launch"
    async def exec1(args):
        executed_skills.append("app.launch")
        return {"status": "ok"}
    mock_skill1.execute = exec1

    mock_skill2 = MagicMock()
    mock_skill2.name = "window.move"
    async def exec2(args):
        executed_skills.append("window.move")
        return {"status": "ok"}
    mock_skill2.execute = exec2

    app.registry.register(mock_skill1)
    app.registry.register(mock_skill2)

    compound_intent = CommandIntent(
        skill="app.launch",
        args={"name": "browser"},
        steps=[
            CommandIntent(skill="app.launch", args={"name": "browser"}),
            CommandIntent(skill="window.move", args={"target": "active", "monitor": 2, "mode": "maximize"}),
        ],
    )

    await app._execute_intent(compound_intent)
    assert executed_skills == ["app.launch", "window.move"]


def test_app_scanner_registry_and_shortcuts() -> None:
    """Перевіряє, що сканер ярликів та реєстру повертає структуровані об'єкти ScannedApp."""
    dirs = default_app_shortcut_dirs()
    assert len(dirs) > 0

    reg_apps = scan_registry_apps()
    assert isinstance(reg_apps, list)

    all_apps = scan_start_menu_apps(require_target_exists=True, include_registry=True)
    assert len(all_apps) > 0
    first = all_apps[0]
    assert isinstance(first, ScannedApp)
    assert first.voice_name
    assert first.display_name
    assert first.exe_path


def test_apps_page_search_and_selection() -> None:
    """Перевіряє фільтрацію, вибір усіх чекбоксів та відсутність обрізання тексту в AppsPage."""
    app = QApplication.instance()
    if app is None:
        app = QApplication([])

    mock_apps = [
        ScannedApp(voice_name="code", display_name="Visual Studio Code", exe_path=r"C:\Apps\Code.exe"),
        ScannedApp(voice_name="telegram", display_name="Telegram Desktop", exe_path=r"C:\Apps\Telegram.exe"),
        ScannedApp(voice_name="vlc", display_name="VLC Media Player", exe_path=r"C:\Apps\vlc.exe"),
    ]

    page = AppsPage(scanner_fn=lambda: mock_apps)

    page.populate_scan_results(mock_apps)
    assert len(page.scan_checkboxes) == 3

    # Перевіряємо кнопку Вибрати всі
    page.select_all_scanned(True)
    assert all(chk.isChecked() for chk, _ in page.scan_checkboxes)
    assert "3" in page.btn_import_checked.text()

    # Перевіряємо кнопку Зняти вибір
    page.select_all_scanned(False)
    assert not any(chk.isChecked() for chk, _ in page.scan_checkboxes)
    assert not page.btn_import_checked.isEnabled()

    # Перевіряємо швидкий пошук/фільтр
    page.search_filter.setText("telegram")
    visible = [chk for chk, _ in page.scan_checkboxes if not chk.isHidden()]
    assert len(visible) == 1
    assert "Telegram" in visible[0].text()

    page.search_filter.setText("неіснуюча_програма_12345")
    visible_none = [chk for chk, _ in page.scan_checkboxes if not chk.isHidden()]
    assert len(visible_none) == 0

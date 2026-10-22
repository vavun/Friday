"""Юніт- та GUI-тести для Під-етапу L3: правила шляхів (path_rules.py), парсер .lnk (app_scanner.py) та сторінки Folders + Apps."""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

from friday.launcher.app import LauncherWindow
from friday.launcher.services.app_scanner import (
    ScannedApp,
    build_stub_lnk_bytes,
    parse_lnk_file,
    scan_start_menu_apps,
    validate_app_executable,
)
from friday.launcher.services.config_store import ConfigStore
from friday.launcher.services.env_store import EnvStore
from friday.launcher.services.path_rules import check_folder_status, validate_folder_path
from friday.launcher.theme import build_cyberpunk_qss, load_bundled_fonts
from friday.skills.apps import AppLauncher


@pytest.fixture(scope="session")
def qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    assert isinstance(app, QApplication)
    fonts = load_bundled_fonts()
    app.setStyleSheet(build_cyberpunk_qss(fonts["heading"], fonts["mono"]))
    return app


def test_path_rules_forbids_root_system_and_home_and_warns_broad(tmp_path: Path) -> None:
    """Перевіряє заборону кореня диска, системних папок, всієї домашньої директорії та попередження про широкі папки."""
    fake_home = tmp_path / "Users" / "TestUser"
    fake_appdata = fake_home / "AppData"
    fake_downloads = fake_home / "Downloads"
    narrow_project = fake_home / "Projects" / "FridayRepo"

    for p in (fake_appdata, fake_downloads, narrow_project):
        p.mkdir(parents=True, exist_ok=True)

    # 1. Корінь диска заборонено
    res_root = validate_folder_path("C:\\", home_dir=fake_home)
    assert res_root.ok is False
    assert "корінь диска" in res_root.error.lower()

    # 2. Системні каталоги заборонено
    for sys_path in ("C:\\Windows", "C:\\Windows\\System32", "C:\\Program Files", "C:\\ProgramData"):
        res_sys = validate_folder_path(sys_path, home_dir=fake_home)
        assert res_sys.ok is False
        assert "системний каталог" in res_sys.error.lower()

    # 3. Уся домашня директорія та AppData заборонені
    res_home = validate_folder_path(fake_home, home_dir=fake_home)
    assert res_home.ok is False
    assert "домашньої директорії" in res_home.error.lower()

    res_appdata = validate_folder_path(fake_appdata, home_dir=fake_home)
    assert res_appdata.ok is False

    # 4. Широка папка профілю (Downloads) дозволена, але повертає попередження
    res_broad = validate_folder_path(fake_downloads, home_dir=fake_home, require_exists=True)
    assert res_broad.ok is True
    assert res_broad.warning != ""

    # 5. Вузька папка конкретного проєкту — OK без попереджень
    res_narrow = validate_folder_path(narrow_project, home_dir=fake_home, require_exists=True)
    assert res_narrow.ok is True
    assert res_narrow.warning == ""
    assert res_narrow.exists is True

    assert check_folder_status(str(narrow_project)) == (True, "Існує")
    assert check_folder_status(str(tmp_path / "missing_dir")) == (False, "Недоступна")


def test_app_scanner_lnk_parser_on_stubs_and_uninstaller_filter(tmp_path: Path) -> None:
    """Перевіряє розбір бінарних заглушок .lnk, фільтрацію деінсталяторів та валідатор виконуваних файлів."""
    bin_dir = tmp_path / "ProgramsBin"
    start_menu = tmp_path / "StartMenu"
    bin_dir.mkdir()
    start_menu.mkdir()

    fake_tg_exe = bin_dir / "Telegram.exe"
    fake_tg_exe.write_bytes(b"MZ_FAKE_EXE")
    fake_unins_exe = bin_dir / "unins000.exe"
    fake_unins_exe.write_bytes(b"MZ_FAKE_UNINS")

    tg_lnk = start_menu / "Telegram Desktop.lnk"
    tg_lnk.write_bytes(build_stub_lnk_bytes(str(fake_tg_exe), "--workdir C:\\Temp"))

    unins_lnk = start_menu / "Uninstall Telegram.lnk"
    unins_lnk.write_bytes(build_stub_lnk_bytes(str(fake_unins_exe)))

    broken_lnk = start_menu / "Broken Shortcut.lnk"
    broken_lnk.write_bytes(b"NOT_A_VALID_LNK_HEADER")

    parsed = parse_lnk_file(tg_lnk, require_target_exists=True)
    assert parsed is not None
    assert parsed.voice_name == "telegram desktop"
    assert Path(parsed.exe_path) == fake_tg_exe
    assert parsed.arguments == "--workdir C:\\Temp"

    scanned = scan_start_menu_apps([start_menu], require_target_exists=True)
    assert len(scanned) == 1
    assert scanned[0].voice_name == "telegram desktop"

    # Перевірка валідатора виконуваного файлу
    ok_exe, cmd_str, err = validate_app_executable(str(fake_tg_exe), "--minimized", require_exists=True)
    assert ok_exe is True
    assert err == ""
    assert "--minimized" in cmd_str

    txt_file = bin_dir / "notes.txt"
    txt_file.write_text("hello", encoding="utf-8")
    ok_txt, _, err_txt = validate_app_executable(str(txt_file), require_exists=True)
    assert ok_txt is False
    assert ".txt" in err_txt


def test_folders_and_apps_pages_gui_workflow(qapp: QApplication, tmp_path: Path) -> None:
    """Перевіряє додавання/видалення папок і програм через GUI-сторінки FoldersPage та AppsPage."""
    base_toml = tmp_path / "config.toml"
    local_toml = tmp_path / "config.local.toml"
    env_file = tmp_path / ".env"

    base_toml.write_text("[hotkeys]\nvoice = 'right_ctrl'\n", encoding="utf-8")
    env_file.write_text("", encoding="utf-8")

    store = ConfigStore(base_path=base_toml, local_path=local_toml, env_path=env_file)
    env_store = EnvStore(env_path=env_file, gitignore_path=tmp_path / ".gitignore")
    win = LauncherWindow(config_store=store, env_store=env_store, enable_tray=False)
    win.show()
    qapp.processEvents()

    # --- 1. Тест сторінки FoldersPage ---
    win.switch_page("folders")
    qapp.processEvents()

    # Спроба додати заборонений системний каталог C:\Windows
    win.folders_page.input_label.setText("System")
    win.folders_page.input_path.setText("C:\\Windows")
    assert win.folders_page.add_folder_from_inputs() is False

    # Додавання валідної папки проєкту
    proj_dir = tmp_path / "Workspace" / "MyService"
    proj_dir.mkdir(parents=True)
    win.folders_page.input_label.setText("MyService")
    win.folders_page.input_path.setText(str(proj_dir))
    win.folders_page.combo_mode.setCurrentIndex(1)  # read_write
    assert win.folders_page.add_folder_from_inputs() is True

    cfg_after_folder = store.load_merged()
    assert len(cfg_after_folder.paths.allowed_folders) == 1
    assert cfg_after_folder.paths.allowed_folders[0].label == "MyService"
    assert cfg_after_folder.paths.allowed_folders[0].mode == "read_write"

    # Видалення папки
    win.folders_page.remove_folder(str(proj_dir.resolve()))
    assert len(store.load_merged().paths.allowed_folders) == 0

    # --- 2. Тест сторінки AppsPage ---
    win.switch_page("apps")
    qapp.processEvents()

    fake_app_exe = tmp_path / "obs64.exe"
    fake_app_exe.write_bytes(b"MZ_FAKE_OBS")

    win.apps_page.input_voice_name.setText("obs")
    win.apps_page.input_synonyms.setText("обс, стрім")
    win.apps_page.input_exe_path.setText(str(fake_app_exe))
    win.apps_page.input_args.setText("--startrecording")
    assert win.apps_page.add_app_from_inputs() is True

    cfg_after_app = store.load_merged()
    assert "obs" in cfg_after_app.apps
    assert "--startrecording" in cfg_after_app.apps["obs"]
    assert cfg_after_app.app_synonyms.get("обс") == "obs"
    assert cfg_after_app.app_synonyms.get("стрім") == "obs"

    # Перевіряємо, що AppLauncher ядра знаходить програму за користувацьким синонімом
    launcher = AppLauncher(cfg_after_app.apps, custom_synonyms=cfg_after_app.app_synonyms)
    assert launcher.resolve_app_key("стрім") == "obs"

    # Тест імпорту зі сканера меню Пуск із чекбоксами
    scanned_exe = tmp_path / "discord.exe"
    scanned_exe.write_bytes(b"MZ_FAKE_DISCORD")
    win.apps_page.populate_scan_results(
        [
            ScannedApp(
                voice_name="discord",
                display_name="Discord",
                exe_path=str(scanned_exe),
            )
        ]
    )
    assert len(win.apps_page.scan_checkboxes) == 1
    win.apps_page.scan_checkboxes[0][0].setChecked(True)
    assert win.apps_page.import_checked_scanned_apps() == 1
    assert "discord" in store.load_merged().apps

    win.quit_application()

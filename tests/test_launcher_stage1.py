"""Юніт- та GUI smoke-тести для Під-етапу L1: каркас лаунчера, config.local.toml, правила шляхів, тема та навігація."""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

from friday.config import AllowedFolderEntry, load_config
from friday.launcher.app import LauncherWindow
from friday.launcher.services.config_store import ConfigStore, ConfigValidationError
from friday.launcher.theme import build_cyberpunk_qss, load_bundled_fonts
from friday.safety import check_path_allowed


@pytest.fixture(scope="session")
def qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    assert isinstance(app, QApplication)
    fonts = load_bundled_fonts()
    app.setStyleSheet(build_cyberpunk_qss(fonts["heading"], fonts["mono"]))
    return app


def test_config_store_atomic_roundtrip_and_overlay(tmp_path: Path) -> None:
    """Перевіряє накладання config.local.toml поверх config.toml та валідацію полів."""
    base_toml = tmp_path / "config.toml"
    local_toml = tmp_path / "config.local.toml"
    env_file = tmp_path / ".env"

    base_toml.write_text(
        """
[hotkeys]
voice = "right_ctrl"

[monitors]
primary = 0
docs = 1
""",
        encoding="utf-8",
    )
    env_file.write_text("", encoding="utf-8")

    store = ConfigStore(base_path=base_toml, local_path=local_toml, env_path=env_file)
    merged = store.save_local_overrides(
        {
            "monitors": {"docs": 2},
            "apps": {"browser": "chrome.exe"},
        }
    )
    assert local_toml.exists()
    assert merged.monitors.primary == 0
    assert merged.monitors.docs == 2
    assert merged.apps["browser"] == "chrome.exe"

    # Перевірка, що load_config ядра автоматично підхоплює local_config_path
    reloaded = load_config(config_path=base_toml, env_path=env_file, local_config_path=local_toml)
    assert reloaded.monitors.docs == 2

    # Спроба зберегти невалідне поле (auto_approve=True) піднімає ConfigValidationError із назвою поля
    with pytest.raises(ConfigValidationError) as exc_info:
        store.save_local_overrides({"agent": {"auto_approve": True}})
    assert "agent.auto_approve" in exc_info.value.field_path


def test_core_check_path_allowed(tmp_path: Path) -> None:
    """Перевіряє загальний перевіряльник шляху check_path_allowed для [paths].allowed_folders."""
    ro_dir = tmp_path / "docs_ro"
    rw_dir = tmp_path / "proj_rw"
    outside_dir = tmp_path / "outside"
    ro_dir.mkdir()
    rw_dir.mkdir()
    outside_dir.mkdir()

    folders = [
        AllowedFolderEntry(label="Docs", path=str(ro_dir), mode="read_only"),
        AllowedFolderEntry(label="Project", path=str(rw_dir), mode="read_write"),
    ]

    # Читання дозволено в обох
    assert check_path_allowed(ro_dir / "readme.md", folders, require_write=False) == (ro_dir / "readme.md").resolve()
    assert check_path_allowed(rw_dir / "main.py", folders, require_write=True) == (rw_dir / "main.py").resolve()

    # Запис у read_only заборонено
    with pytest.raises(PermissionError, match="read_only"):
        check_path_allowed(ro_dir / "readme.md", folders, require_write=True)

    # Доступ поза білим списком заборонено
    with pytest.raises(PermissionError, match="поза дозволеними"):
        check_path_allowed(outside_dir / "file.txt", folders)

    # Доступ до .env заборонено навіть усередині дозволеної папки
    with pytest.raises(PermissionError, match="секретного файлу"):
        check_path_allowed(rw_dir / ".env", folders)


def test_launcher_gui_smoke_navigation_and_orb_idle(qapp: QApplication, tmp_path: Path) -> None:
    """Smoke-тест GUI: запуск, перехід по всіх 9 сторінках, зупинка анімації у прихованому стані."""
    base_toml = tmp_path / "config.toml"
    local_toml = tmp_path / "config.local.toml"
    base_toml.write_text("[hotkeys]\nvoice = 'right_ctrl'\n", encoding="utf-8")

    store = ConfigStore(base_path=base_toml, local_path=local_toml, env_path=tmp_path / ".env")
    win = LauncherWindow(config_store=store, enable_tray=False)
    win.show()
    qapp.processEvents()

    # Перевіряємо перехід по всіх 9 сторінках
    for page_id, _ in LauncherWindow.PAGE_ORDER:
        win.switch_page(page_id)
        qapp.processEvents()
        assert win.current_page_id == page_id
        assert win.nav_buttons[page_id].property("active") == "true"

    # Повертаємось на Dashboard і вмикаємо статус running -> орба анімується
    win.switch_page("dashboard")
    orb = win.dashboard_page.orb
    orb.set_status("running")
    qapp.processEvents()
    assert orb.is_animating is True

    # Приховуємо в трей -> таймер анімації орби миттєво вимикається (~0% CPU)
    win.hide_to_tray()
    qapp.processEvents()
    assert orb.is_animating is False

    win.quit_application()

"""Юніт- та GUI-тести для Під-етапу L2: EnvStore (.env), перевірка ключа Gemini, CoreProcessManager та інтеграція Keys + Dashboard."""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any

import pytest
from PySide6.QtWidgets import QApplication, QPushButton

from friday.launcher.app import LauncherWindow
from friday.launcher.services.config_store import ConfigStore
from friday.launcher.services.env_store import EnvStore, ensure_env_in_gitignore, mask_secret
from friday.launcher.services.key_check import verify_gemini_api_key
from friday.launcher.services.process_manager import (
    CoreProcessManager,
    CoreProcessStatus,
    sanitize_log_line,
)
from friday.launcher.theme import build_cyberpunk_qss, load_bundled_fonts


@pytest.fixture(scope="session")
def qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    assert isinstance(app, QApplication)
    fonts = load_bundled_fonts()
    app.setStyleSheet(build_cyberpunk_qss(fonts["heading"], fonts["mono"]))
    return app


def test_env_store_preserves_comments_and_masks_secret(tmp_path: Path) -> None:
    """Перевіряє запис у .env без пошкодження сторонніх рядків, автоперевірку .gitignore та маскування."""
    env_file = tmp_path / ".env"
    gitignore_file = tmp_path / ".gitignore"

    env_file.write_text(
        "# Початковий коментар\n"
        "CUSTOM_SETTING=preserve_me\n"
        "GEMINI_API_KEY=old_key_0000\n"
        "\n"
        "# Кінцевий коментар\n",
        encoding="utf-8",
    )
    gitignore_file.write_text("__pycache__/\n", encoding="utf-8")

    store = EnvStore(env_path=env_file, gitignore_path=gitignore_file)
    added_to_gitignore = store.update_keys(
        {
            "GEMINI_API_KEY": "AIzaSySuperSecretKey9876",
            "TELEGRAM_API_ID": "1234567",
        }
    )

    assert added_to_gitignore is True
    assert ".env" in gitignore_file.read_text(encoding="utf-8").splitlines()
    assert ensure_env_in_gitignore(gitignore_file) == (True, False)

    raw_content = env_file.read_text(encoding="utf-8")
    assert "# Початковий коментар" in raw_content
    assert "CUSTOM_SETTING=preserve_me" in raw_content
    assert "# Кінцевий коментар" in raw_content
    assert "GEMINI_API_KEY=AIzaSySuperSecretKey9876" in raw_content
    assert "TELEGRAM_API_ID=1234567" in raw_content

    masked = store.get_masked("GEMINI_API_KEY")
    assert masked.endswith("9876")
    assert "AIzaSySuperSecret" not in masked
    assert mask_secret("1234") == "••••"


def test_verify_gemini_api_key_scenarios() -> None:
    """Перевіряє сценарії verify_gemini_api_key: порожній, валідний, невалідний (401) та офлайн."""
    res_empty = verify_gemini_api_key("   ")
    assert res_empty.ok is False
    assert res_empty.status == "invalid_key"

    class _FakeModels:
        def __init__(self, mode: str) -> None:
            self.mode = mode

        def generate_content(self, **kwargs: Any) -> Any:
            if self.mode == "ok":
                return object()
            if self.mode == "401":
                raise RuntimeError("401 API_KEY_INVALID: API key not valid")
            raise ConnectionError("Failed to establish a new connection: [Errno 11001] getaddrinfo failed")

    class _FakeClient:
        def __init__(self, mode: str) -> None:
            self.models = _FakeModels(mode)

    res_ok = verify_gemini_api_key("AIzaValidKey123456", client_factory=lambda _k: _FakeClient("ok"))
    assert res_ok.ok is True
    assert res_ok.status == "valid"

    res_invalid = verify_gemini_api_key("AIzaBadKey123456", client_factory=lambda _k: _FakeClient("401"))
    assert res_invalid.ok is False
    assert res_invalid.status == "invalid_key"

    res_offline = verify_gemini_api_key("AIzaOfflineKey1234", client_factory=lambda _k: _FakeClient("offline"))
    assert res_offline.ok is False
    assert res_offline.status == "offline"


def test_core_process_manager_lifecycle_and_sanitization(tmp_path: Path) -> None:
    """Перевіряє запуск, маскування секретів у stdout, зупинку та виявлення аварійного завершення."""
    secret = "AIzaSecretTokenForTest5566"
    assert secret not in sanitize_log_line(f"Loaded key={secret}", [secret])
    assert "5566" in sanitize_log_line(f"Loaded key={secret}", [secret])

    statuses: list[CoreProcessStatus] = []
    cmd_ok = [
        sys.executable,
        "-u",
        "-c",
        f"import time; print('booting with {secret}', flush=True); time.sleep(15)",
    ]
    mgr = CoreProcessManager(
        command=cmd_ok,
        cwd=str(tmp_path),
        on_status_change=lambda st, _det: statuses.append(st),
    )
    mgr.set_redacted_secrets([secret])

    assert mgr.start() is True
    # Повторний виклик start() при живому процесі не створює дублікат
    assert mgr.start() is True

    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline and (mgr.status != CoreProcessStatus.RUNNING or not mgr.output_lines):
        time.sleep(0.05)

    assert mgr.status == CoreProcessStatus.RUNNING
    assert any("5566" in line and secret not in line for line in mgr.output_lines)

    assert mgr.stop(timeout_s=1.5) is True
    assert mgr.status == CoreProcessStatus.STOPPED

    # Перевірка аварійного падіння дочірнього процесу (код виходу != 0)
    cmd_crash = [sys.executable, "-c", "import sys; sys.exit(3)"]
    crash_mgr = CoreProcessManager(command=cmd_crash, cwd=str(tmp_path))
    assert crash_mgr.start() is True

    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline and crash_mgr.status != CoreProcessStatus.ERROR:
        time.sleep(0.05)

    assert crash_mgr.status == CoreProcessStatus.ERROR
    assert "3" in crash_mgr.last_error


def test_launcher_keys_and_dashboard_gui_integration(qapp: QApplication, tmp_path: Path) -> None:
    """Перевіряє інтеграцію Dashboard (Preflight 'Виправити') та сторінки Keys у головному вікні."""
    base_toml = tmp_path / "config.toml"
    local_toml = tmp_path / "config.local.toml"
    env_file = tmp_path / ".env"
    gitignore_file = tmp_path / ".gitignore"

    base_toml.write_text("[hotkeys]\nvoice = 'right_ctrl'\n[apps]\ncalc = 'calc.exe'\n", encoding="utf-8")
    env_file.write_text("", encoding="utf-8")
    gitignore_file.write_text(".env\n", encoding="utf-8")

    config_store = ConfigStore(base_path=base_toml, local_path=local_toml, env_path=env_file)
    env_store = EnvStore(env_path=env_file, gitignore_path=gitignore_file)

    win = LauncherWindow(config_store=config_store, env_store=env_store, enable_tray=False)
    win.show()
    qapp.processEvents()

    # На старті ключ відсутній -> на Dashboard є кнопка "Виправити", яка веде на сторінку "keys"
    fix_buttons = [
        btn
        for btn in win.dashboard_page.preflight_rows_container.findChildren(QPushButton)
        if btn.text() == "Виправити"
    ]
    assert len(fix_buttons) >= 1
    fix_buttons[0].click()
    qapp.processEvents()
    assert win.current_page_id == "keys"

    # Вводимо та зберігаємо ключ на сторінці Keys
    full_key = "AIzaSyTestLauncherKeyABCD"
    win.keys_page.input_gemini.setText(full_key)
    win.keys_page.save_gemini_key()
    qapp.processEvents()

    # Поле очищено, а у підписі відображаються лише останні 4 символи
    assert win.keys_page.input_gemini.text() == ""
    assert "ABCD" in win.keys_page.lbl_gemini_saved.text()
    assert "AIzaSyTestLauncherKey" not in win.keys_page.lbl_gemini_saved.text()

    win.quit_application()

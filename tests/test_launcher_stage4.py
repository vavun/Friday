"""Тести Під-етапу L4 лаунчера: Голос/Хоткеї, Монітори, Безпека, Логи та Налаштування."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QApplication

from friday.autostart import AUTOSTART_FILENAME, is_autostart_enabled
from friday.launcher.app import LauncherWindow
from friday.launcher.pages.logs import LogsPage
from friday.launcher.pages.monitors import MonitorsPage
from friday.launcher.pages.safety import SafetyPage
from friday.launcher.pages.settings import SettingsPage
from friday.launcher.pages.voice import VoicePage, detect_hotkey_conflicts
from friday.launcher.services.config_store import ConfigStore
from friday.launcher.services.env_store import EnvStore
from friday.util.monitors import MonitorInfo, Rect


def _ensure_qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    assert isinstance(app, QApplication)
    return app


def _make_store(tmp_path: Path) -> ConfigStore:
    base_cfg = tmp_path / "config.toml"
    base_cfg.write_text(
        '[hotkeys]\nvoice = "right_ctrl"\nexplain_clipboard = "ctrl+alt+d"\ngesture = "ctrl+alt+g"\ncancel = "esc"\n'
        '[monitors]\nprimary = 0\ndocs = 1\n'
        '[safety]\nconfirm_messages = true\nconfirm_file_changes = true\n',
        encoding="utf-8",
    )
    return ConfigStore(
        base_path=base_cfg,
        local_path=tmp_path / "config.local.toml",
        env_path=tmp_path / ".env",
    )


def test_detect_hotkey_conflicts() -> None:
    """Перевіряє виявлення дублікатів гарячих клавіш та системних комбінацій Windows."""
    ok_issues = detect_hotkey_conflicts(
        {
            "voice": "right_ctrl",
            "explain_clipboard": "ctrl+alt+d",
            "gesture": "ctrl+alt+g",
            "cancel": "esc",
        }
    )
    assert ok_issues == []

    dup_issues = detect_hotkey_conflicts(
        {
            "voice": "ctrl+alt+d",
            "explain_clipboard": "ctrl+alt+d",
            "gesture": "alt+tab",
            "cancel": "esc",
        }
    )
    assert len(dup_issues) >= 2
    assert any("Конфлікт:" in msg for msg in dup_issues)
    assert any("системну" in msg for msg in dup_issues)


def test_voice_page_saves_stt_hotkeys_and_blocks_conflicts(tmp_path: Path) -> None:
    """VoicePage зберігає STT/хоткеї у config.local.toml і блокує збереження при дублікатах."""
    _ensure_qapp()
    store = _make_store(tmp_path)
    page = VoicePage(config_store=store)

    # Дублікат хоткеїв не повинен перезаписувати конфіг
    page.input_hk_voice.setText("ctrl+alt+d")
    page.input_hk_docs.setText("ctrl+alt+d")
    saved = page.save_voice_settings()
    assert saved is False
    assert not store.local_path.exists()

    # Валідні хоткеї та локальний Whisper
    page.input_hk_voice.setText("right_ctrl")
    page.input_hk_docs.setText("ctrl+shift+d")
    idx_whisper = page.combo_backend.findData("whisper_local")
    page.combo_backend.setCurrentIndex(idx_whisper)
    saved2 = page.save_voice_settings()
    assert saved2 is True

    merged = store.load_merged()
    assert merged.hotkeys.explain_clipboard == "ctrl+shift+d"
    assert merged.stt.backend == "whisper_local"


def test_monitors_page_identify_and_save_docs_role(tmp_path: Path) -> None:
    """MonitorsPage показує список моніторів, зберігає роль docs та відкриває оверлей ідентифікації."""
    _ensure_qapp()
    store = _make_store(tmp_path)
    fake_monitors = [
        MonitorInfo(
            index=0,
            handle=1001,
            device="DISPLAY1",
            is_primary=True,
            monitor_rect=Rect(0, 0, 1920, 1080),
            work_area=Rect(0, 0, 1920, 1040),
        ),
        MonitorInfo(
            index=1,
            handle=1002,
            device="DISPLAY2",
            is_primary=False,
            monitor_rect=Rect(1920, 0, 3840, 1080),
            work_area=Rect(1920, 0, 3840, 1040),
        ),
    ]
    page = MonitorsPage(config_store=store, monitors_provider=lambda: fake_monitors)
    assert len(page.get_current_monitors()) == 2

    idx_mon0 = page.combo_docs_monitor.findData(0)
    page.combo_docs_monitor.setCurrentIndex(idx_mon0)
    page.save_monitors_config()

    merged = store.load_merged()
    assert merged.monitors.docs == 0

    count = page.identify_monitors(duration_ms=0)
    assert count == 2
    assert len(page._active_overlays) == 2
    page.close_identify_overlays()
    assert len(page._active_overlays) == 0

    # Симуляція реального кліку на кнопку QPushButton (передає checked=False у слот)
    page.btn_identify.click()
    assert len(page._active_overlays) == 2
    assert page._active_overlays[0]._close_timer is not None
    assert page._active_overlays[0]._close_timer.isActive() is True
    page.close_identify_overlays()


def test_safety_page_locks_auto_approve_and_saves_flags(tmp_path: Path) -> None:
    """SafetyPage тримає auto_approve заблокованим (False) і зберігає прапорці підтвердження."""
    _ensure_qapp()
    store = _make_store(tmp_path)
    page = SafetyPage(config_store=store)

    assert page.chk_auto_approve.isChecked() is False
    assert page.chk_auto_approve.isEnabled() is False
    assert "DANGEROUS_PATTERNS" in page.readonly_viewer.toPlainText()

    page.chk_confirm_messages.setChecked(False)
    page.chk_confirm_files.setChecked(True)
    page.save_safety_settings()

    merged = store.load_merged()
    assert merged.safety.confirm_messages is False
    assert merged.safety.confirm_file_changes is True
    assert merged.agent.auto_approve is False


def test_logs_page_redacts_secrets_and_filters_by_level(tmp_path: Path) -> None:
    """LogsPage маскує секрети у файлі та потоці і фільтрує рядки за рівнем (ERROR/WARNING/INFO)."""
    _ensure_qapp()
    log_file = tmp_path / "friday.log"
    secret = "AIzaSySecretKey999888777"
    log_file.write_text(
        f"2026-10-22 12:00:00 | INFO    | friday | Started with key={secret}\n"
        "2026-10-22 12:00:01 | ERROR   | friday | Audio stream failed\n",
        encoding="utf-8",
    )

    page = LogsPage(log_file_path=log_file, secrets_provider=lambda: [secret])
    page.reload_from_file()

    full_text = page.log_viewer.toPlainText()
    assert secret not in full_text
    assert "••••••••8777" in full_text
    assert "Audio stream failed" in full_text

    # Фільтр лише ERROR
    idx_err = page.combo_level.findData("ERROR")
    page.combo_level.setCurrentIndex(idx_err)
    err_text = page.log_viewer.toPlainText()
    assert "Audio stream failed" in err_text
    assert "Started with key=" not in err_text


def test_settings_page_autostart_and_reduce_animations(tmp_path: Path) -> None:
    """SettingsPage створює/видаляє скрипт автозапуску у Startup та перемикає reduce_animations."""
    _ensure_qapp()
    store = _make_store(tmp_path)
    startup_dir = tmp_path / "Startup"
    anim_states: list[bool] = []

    page = SettingsPage(
        config_store=store,
        startup_dir=startup_dir,
        on_reduce_animations_changed=lambda val: anim_states.append(val),
    )

    page.chk_autostart.setChecked(True)
    page.chk_start_minimized.setChecked(True)
    page.chk_reduce_animations.setChecked(True)
    idx_debug = page.combo_log_level.findData("DEBUG")
    page.combo_log_level.setCurrentIndex(idx_debug)
    page.save_settings()

    assert is_autostart_enabled(startup_dir) is True
    assert (startup_dir / AUTOSTART_FILENAME).exists()
    assert anim_states[-1] is True

    merged = store.load_merged()
    assert merged.ui.autostart is True
    assert merged.ui.start_minimized is True
    assert merged.ui.reduce_animations is True
    assert merged.ui.log_level == "DEBUG"

    # Вимкнення автозапуску
    page.chk_autostart.setChecked(False)
    page.save_settings()
    assert is_autostart_enabled(startup_dir) is False


def test_launcher_window_integrates_all_nine_pages(tmp_path: Path) -> None:
    """LauncherWindow успішно ініціалізує всі 9 функціональних сторінок L1–L4 та передає логи у LogsPage."""
    _ensure_qapp()
    store = _make_store(tmp_path)
    env = EnvStore(env_path=tmp_path / ".env")
    win = LauncherWindow(config_store=store, env_store=env, enable_tray=False)

    for page_id, _ in LauncherWindow.PAGE_ORDER:
        win.switch_page(page_id)
        assert win.current_page_id == page_id

    win._on_reduce_animations_changed(True)
    assert win.dashboard_page.orb._reduce_animations is True
    win.close()

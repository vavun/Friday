"""Сторінка 09 // Налаштування лаунчера ([ui], автозапуск, трей, анімації)."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from friday.autostart import (
    AUTOSTART_FILENAME,
    default_startup_dir,
    disable_autostart,
    enable_autostart,
    is_autostart_enabled,
)
from friday.launcher.pages import BasePage, make_cyber_card
from friday.launcher.services.config_store import ConfigStore


class SettingsPage(BasePage):
    """Налаштування автозапуску з Windows, згортання в трей, зменшення анімацій та рівня логів."""

    def __init__(
        self,
        config_store: ConfigStore | None = None,
        startup_dir: Path | None = None,
        on_settings_saved: Callable[[], None] | None = None,
        on_reduce_animations_changed: Callable[[bool], None] | None = None,
        on_close_to_tray_changed: Callable[[bool], None] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(
            page_id="settings",
            title="09 // НАЛАШТУВАННЯ СИСТЕМИ",
            subtitle="Автозапуск з Windows, поведінка системного трею, продуктивність UI та мова.",
            parent=parent,
        )
        self.config_store = config_store or ConfigStore()
        self.startup_dir = startup_dir or default_startup_dir()
        self.on_settings_saved = on_settings_saved
        self.on_reduce_animations_changed = on_reduce_animations_changed
        self.on_close_to_tray_changed = on_close_to_tray_changed

        # 1. Картка автозапуску та трею
        boot_card, boot_layout = make_cyber_card("АВТОЗАПУСК ТА СИСТЕМНИЙ ТРЕЙ")

        self.chk_autostart = QCheckBox(
            "Автозапуск із Windows (через shell:startup -> pythonw.exe --minimized)"
        )
        boot_layout.addWidget(self.chk_autostart)

        autostart_note = QLabel(
            f"Механізм: ярлик/скрипт {AUTOSTART_FILENAME} у папці Startup поточного користувача. "
            "Не потребує прав Адміністратора (на відміну від Task Scheduler) і не відкриває чорне вікно консолі."
        )
        autostart_note.setObjectName("PageSubtitle")
        autostart_note.setWordWrap(True)
        boot_layout.addWidget(autostart_note)

        self.chk_start_minimized = QCheckBox(
            "Запускати лаунчер згорнутим у системний трей (--minimized)"
        )
        boot_layout.addWidget(self.chk_start_minimized)

        self.chk_close_to_tray = QCheckBox(
            "Згортати у системний трей при натисканні хрестика закриття вікна"
        )
        self.chk_close_to_tray.setChecked(True)
        self.chk_close_to_tray.toggled.connect(self._handle_close_to_tray_toggled)
        boot_layout.addWidget(self.chk_close_to_tray)

        self.root_layout.addWidget(boot_card)

        # 2. Картка продуктивності UI, логів та мови
        ui_card, ui_layout = make_cyber_card("ПРОДУКТИВНІСТЬ UI, ЛОГУВАННЯ ТА МОВА")

        self.chk_reduce_animations = QCheckBox(
            "Зменшити анімації (вимикає пульсацію неонового ядра на Головній сторінці)"
        )
        self.chk_reduce_animations.toggled.connect(self._handle_reduce_animations_toggled)
        ui_layout.addWidget(self.chk_reduce_animations)

        row_combos = QHBoxLayout()

        col_log = QVBoxLayout()
        lbl_log = QLabel("Рівень деталізації логів (log_level):")
        self.combo_log_level = QComboBox()
        for lvl in ("DEBUG", "INFO", "WARNING", "ERROR"):
            self.combo_log_level.addItem(lvl, lvl)
        col_log.addWidget(lbl_log)
        col_log.addWidget(self.combo_log_level)
        row_combos.addLayout(col_log, stretch=1)

        col_lang = QVBoxLayout()
        lbl_lang = QLabel("Мова інтерфейсу (language):")
        self.combo_language = QComboBox()
        self.combo_language.addItem("Українська (uk)", "uk")
        self.combo_language.addItem("English (en)", "en")
        col_lang.addWidget(lbl_lang)
        col_lang.addWidget(self.combo_language)
        row_combos.addLayout(col_lang, stretch=1)

        ui_layout.addLayout(row_combos)
        self.root_layout.addWidget(ui_card)

        # 3. Кнопка збереження
        act_row = QHBoxLayout()
        self.btn_save = QPushButton("ЗБЕРЕГТИ НАЛАШТУВАННЯ")
        self.btn_save.setObjectName("PrimaryButton")
        self.btn_save.clicked.connect(self.save_settings)
        act_row.addWidget(self.btn_save)

        self.lbl_status = QLabel("")
        self.lbl_status.setObjectName("PageSubtitle")
        act_row.addWidget(self.lbl_status, stretch=1)

        self.root_layout.addLayout(act_row)
        self.root_layout.addStretch()

        self.reload_from_config()

    def _handle_reduce_animations_toggled(self, checked: bool) -> None:
        if self.on_reduce_animations_changed is not None:
            self.on_reduce_animations_changed(bool(checked))

    def _handle_close_to_tray_toggled(self, checked: bool) -> None:
        if self.on_close_to_tray_changed is not None:
            self.on_close_to_tray_changed(bool(checked))

    def reload_from_config(self) -> None:
        """Завантажує секцію [ui] та перевіряє фактичну наявність файлу автозапуску."""
        cfg = self.config_store.load_merged()
        autostart_active = bool(cfg.ui.autostart) or is_autostart_enabled(self.startup_dir)
        self.chk_autostart.setChecked(autostart_active)
        self.chk_start_minimized.setChecked(bool(cfg.ui.start_minimized))
        self.chk_reduce_animations.setChecked(bool(cfg.ui.reduce_animations))

        idx_log = self.combo_log_level.findData(cfg.ui.log_level)
        if idx_log >= 0:
            self.combo_log_level.setCurrentIndex(idx_log)

        idx_lang = self.combo_language.findData(cfg.ui.language)
        if idx_lang >= 0:
            self.combo_language.setCurrentIndex(idx_lang)

    def save_settings(self) -> None:
        """Зберігає секцію [ui] у config.local.toml та синхронізує скрипт у папці Startup."""
        autostart_val = bool(self.chk_autostart.isChecked())
        minimized_val = bool(self.chk_start_minimized.isChecked())
        reduce_anim_val = bool(self.chk_reduce_animations.isChecked())
        log_level_val = str(self.combo_log_level.currentData() or "INFO")
        lang_val = str(self.combo_language.currentData() or "uk")

        if autostart_val:
            enable_autostart(
                startup_dir=self.startup_dir,
                target_module="friday.launcher",
                minimized=minimized_val or True,
            )
        else:
            disable_autostart(startup_dir=self.startup_dir)

        self.config_store.save_local_overrides(
            {
                "ui": {
                    "language": lang_val,
                    "autostart": autostart_val,
                    "start_minimized": minimized_val,
                    "reduce_animations": reduce_anim_val,
                    "log_level": log_level_val,
                }
            }
        )
        self.lbl_status.setText("Налаштування системи збережено у config.local.toml.")
        self.lbl_status.setStyleSheet("color: #22C55E; font-weight: 700;")

        if self.on_reduce_animations_changed is not None:
            self.on_reduce_animations_changed(reduce_anim_val)
        if self.on_settings_saved is not None:
            self.on_settings_saved()

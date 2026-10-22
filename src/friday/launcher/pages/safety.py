"""Сторінка 07 // Безпека ([safety], заблокований auto_approve та перегляд DANGEROUS_PATTERNS із safety.py)."""

from __future__ import annotations

from typing import Callable

from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QWidget,
)

from friday.launcher.pages import BasePage, make_cyber_card
from friday.launcher.services.config_store import ConfigStore
from friday.launcher.theme import CyberPalette
from friday.safety import DANGEROUS_PATTERNS, SECRET_NAMES, SECRET_SUFFIXES


class SafetyPage(BasePage):
    """Налаштування Confirm Gate та перегляд заблокованих команд із safety.py."""

    def __init__(
        self,
        config_store: ConfigStore | None = None,
        on_safety_changed: Callable[[], None] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(
            page_id="safety",
            title="07 // БЕЗПЕКА (CONFIRM GATE)",
            subtitle=(
                "Обов'язкове підтвердження для незворотних дій, блокування auto_approve "
                "та незмінний список заборонених деструктивних патернів із safety.py."
            ),
            parent=parent,
        )
        self.config_store = config_store or ConfigStore()
        self._on_safety_changed = on_safety_changed
        cfg = self.config_store.load_merged()

        # --- Картка 1: Перемикачі Confirm Gate та заблокований auto_approve ---
        gate_card, gate_layout = make_cyber_card(
            "ПІДТВЕРДЖЕННЯ ЧУТЛИВИХ ДІЙ ([SAFETY] & [AGENT])",
            "При голосовому керуванні всі незворотні дії вимагають підтвердження (Enter / Esc).",
        )

        self.chk_confirm_messages = QCheckBox(
            "Вимагати підтвердження перед надсиланням повідомлень у Telegram (confirm_messages)"
        )
        self.chk_confirm_messages.setChecked(cfg.safety.confirm_messages)

        self.chk_confirm_files = QCheckBox(
            "Вимагати підтвердження перед зміною файлів / запуском Antigravity CLI (confirm_file_changes)"
        )
        self.chk_confirm_files.setChecked(cfg.safety.confirm_file_changes)

        self.chk_auto_approve = QCheckBox(
            "Автоматичне схвалення агентських змін без запиту (agent.auto_approve) — ЗАБЛОКОВАНО: ВИМКНЕНО"
        )
        self.chk_auto_approve.setChecked(False)
        self.chk_auto_approve.setEnabled(False)
        self.chk_auto_approve.setStyleSheet(f"color: {CyberPalette.ERR_RED}; font-weight: bold;")

        gate_layout.addWidget(self.chk_confirm_messages)
        gate_layout.addWidget(self.chk_confirm_files)
        gate_layout.addWidget(self.chk_auto_approve)

        btn_row = QHBoxLayout()
        self.btn_save = QPushButton("Зберегти політики безпеки")
        self.btn_save.setObjectName("PrimaryButton")
        self.btn_save.clicked.connect(self.save_safety_settings)
        btn_row.addWidget(self.btn_save)
        btn_row.addStretch()
        gate_layout.addLayout(btn_row)

        self.lbl_status = QLabel("")
        self.lbl_status.setObjectName("MutedText")
        gate_layout.addWidget(self.lbl_status)

        self.root_layout.addWidget(gate_card)

        # --- Картка 2: Перегляд заборонених патернів із safety.py (лише читання) ---
        rules_card, rules_layout = make_cyber_card(
            "ЗАБОРОНЕНІ ПАТЕРНИ ТА ЗАХИЩЕНІ СЕКРЕТНІ ФАЙЛИ (ЛИШЕ ЧИТАННЯ)",
            "Цей перелік жорстко закодований у friday/safety.py і не може бути вимкнений через конфіг.",
        )
        self.readonly_viewer = QPlainTextEdit()
        self.readonly_viewer.setObjectName("LogViewer")
        self.readonly_viewer.setReadOnly(True)
        self.readonly_viewer.setMinimumHeight(210)

        lines = [
            "# Заборонені регулярні вирази деструктивних команд (DANGEROUS_PATTERNS):",
            *[f"  • {pat}" for pat in DANGEROUS_PATTERNS],
            "",
            "# Захищені імена секретних файлів (SECRET_NAMES):",
            f"  • {', '.join(sorted(SECRET_NAMES))}",
            "",
            "# Захищені розширення ключів і сесій (SECRET_SUFFIXES):",
            f"  • {', '.join(sorted(SECRET_SUFFIXES))}",
        ]
        self.readonly_viewer.setPlainText("\n".join(lines))
        rules_layout.addWidget(self.readonly_viewer)

        self.root_layout.addWidget(rules_card)
        self.root_layout.addStretch()

    def save_safety_settings(self) -> None:
        """Зберігає перемикачі [safety] у config.local.toml (auto_approve залишається False)."""
        self.config_store.save_local_overrides(
            {
                "safety": {
                    "confirm_messages": bool(self.chk_confirm_messages.isChecked()),
                    "confirm_file_changes": bool(self.chk_confirm_files.isChecked()),
                }
            }
        )
        self.lbl_status.setText("● Політики безпеки збережено у config.local.toml.")
        self.lbl_status.setStyleSheet(f"color: {CyberPalette.OK_GREEN}; font-weight: bold;")
        if self._on_safety_changed is not None:
            self._on_safety_changed()

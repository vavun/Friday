"""Сторінка 01 // Dashboard (Панель керування)."""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from friday.launcher.pages import BasePage, NeonOrbWidget, make_cyber_card
from friday.launcher.theme import apply_neon_glow


class DashboardPage(BasePage):
    """Головна панель: статус ядра, START/STOP/RESTART, неонова орба, підсумок та preflight."""

    def __init__(
        self,
        on_navigate: Callable[[str], None] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(
            page_id="dashboard",
            title="01 // ПАНЕЛЬ КЕРУВАННЯ",
            subtitle="Керування процесом FRIDAY, зведення конфігурації та передстартова діагностика (Preflight).",
            parent=parent,
        )
        self._on_navigate = on_navigate

        hero_card, hero_layout = make_cyber_card("ЯДРО АСИСТЕНТА FRIDAY")
        hero_row = QHBoxLayout()
        hero_row.setSpacing(20)

        self.orb = NeonOrbWidget(self)
        hero_row.addWidget(self.orb, alignment=Qt.AlignmentFlag.AlignVCenter)

        info_col = QVBoxLayout()
        self.status_badge = QLabel("СТАТУС: ЗУПИНЕНО")
        self.status_badge.setObjectName("SectionTitle")
        self.summary_label = QLabel(
            "Хоткей голосу: right_ctrl  |  Документація: ctrl+alt+d  |  Жести: ctrl+alt+g"
        )
        self.summary_label.setObjectName("MutedText")
        info_col.addWidget(self.status_badge)
        info_col.addWidget(self.summary_label)

        btn_row = QHBoxLayout()
        self.btn_start = QPushButton("ЗАПУСТИТИ FRIDAY")
        self.btn_start.setObjectName("PrimaryButton")
        apply_neon_glow(self.btn_start)

        self.btn_stop = QPushButton("ЗУПИНИТИ")
        self.btn_stop.setObjectName("DangerButton")
        self.btn_restart = QPushButton("ПЕРЕЗАПУСТИТИ")

        btn_row.addWidget(self.btn_start)
        btn_row.addWidget(self.btn_stop)
        btn_row.addWidget(self.btn_restart)
        btn_row.addStretch()
        info_col.addLayout(btn_row)

        hero_row.addLayout(info_col, stretch=1)
        hero_layout.addLayout(hero_row)
        self.root_layout.addWidget(hero_card)

        preflight_card, self.preflight_layout = make_cyber_card(
            "ПЕРЕВІРКА ГОТОВНОСТІ (PREFLIGHT)",
            "Перевірка ключа Gemini API, мікрофона, дозволених папок і програм.",
        )
        self.preflight_status_label = QLabel("Готовність буде оновлено автоматично.")
        self.preflight_layout.addWidget(self.preflight_status_label)
        self.root_layout.addWidget(preflight_card)
        self.root_layout.addStretch()

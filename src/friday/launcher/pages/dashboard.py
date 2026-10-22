"""Сторінка 01 // Dashboard (Панель керування) із живим статусом, підсумком та Preflight-діагностикою."""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from friday.config import AppConfig
from friday.launcher.pages import BasePage, NeonOrbWidget, make_cyber_card
from friday.launcher.services.preflight import PreflightItem
from friday.launcher.theme import CyberPalette, apply_neon_glow


class DashboardPage(BasePage):
    """Головна панель: статус ядра, START/STOP/RESTART, неонова орба, підсумок та preflight."""

    def __init__(
        self,
        on_navigate: Callable[[str], None] | None = None,
        on_start_requested: Callable[[], None] | None = None,
        on_stop_requested: Callable[[], None] | None = None,
        on_restart_requested: Callable[[], None] | None = None,
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
        hero_row.setSpacing(22)

        self.orb = NeonOrbWidget(self)
        hero_row.addWidget(self.orb, alignment=Qt.AlignmentFlag.AlignVCenter)

        info_col = QVBoxLayout()
        self.status_badge = QLabel("СТАТУС: ЗУПИНЕНО")
        self.status_badge.setObjectName("SectionTitle")

        self.error_detail_label = QLabel("")
        self.error_detail_label.setStyleSheet(f"color: {CyberPalette.ERR_RED};")
        self.error_detail_label.setVisible(False)

        self.summary_label = QLabel()
        self.summary_label.setObjectName("MutedText")
        self.summary_label.setWordWrap(True)

        info_col.addWidget(self.status_badge)
        info_col.addWidget(self.error_detail_label)
        info_col.addWidget(self.summary_label)

        btn_row = QHBoxLayout()
        self.btn_start = QPushButton("ЗАПУСТИТИ FRIDAY")
        self.btn_start.setObjectName("PrimaryButton")
        apply_neon_glow(self.btn_start)

        self.btn_stop = QPushButton("ЗУПИНИТИ")
        self.btn_stop.setObjectName("DangerButton")
        self.btn_restart = QPushButton("ПЕРЕЗАПУСТИТИ")

        if on_start_requested is not None:
            self.btn_start.clicked.connect(on_start_requested)
        if on_stop_requested is not None:
            self.btn_stop.clicked.connect(on_stop_requested)
        if on_restart_requested is not None:
            self.btn_restart.clicked.connect(on_restart_requested)

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
            "Кожна виявлена проблема має кнопку швидкого переходу до відповідного розділу налаштувань.",
        )
        self.preflight_rows_container = QWidget()
        self.preflight_rows_layout = QVBoxLayout(self.preflight_rows_container)
        self.preflight_rows_layout.setContentsMargins(0, 4, 0, 0)
        self.preflight_rows_layout.setSpacing(8)
        self.preflight_layout.addWidget(self.preflight_rows_container)

        self.root_layout.addWidget(preflight_card)
        self.root_layout.addStretch()

        self.update_process_status("stopped", "")

    def update_summary(self, config: AppConfig) -> None:
        """Оновлює зведення активних хоткеїв, STT-бекенда, дозволених папок і програм."""
        hk = config.hotkeys
        self.summary_label.setText(
            f"Голос: [{hk.voice}]  •  Документація: [{hk.explain_clipboard}]  •  "
            f"Жести: [{hk.gesture}]  •  Скасування: [{hk.cancel}]\n"
            f"STT-бекенд: {config.stt.backend.upper()} ({config.stt.model})  |  "
            f"Дозволених папок: {len(config.paths.allowed_folders)}  |  "
            f"Дозволених програм: {len(config.apps)}"
        )

    def update_process_status(self, status: str, detail: str = "") -> None:
        """Оновлює орбу, бейдж статусу та доступність кнопок START/STOP/RESTART."""
        self.orb.set_status(status)
        labels = {
            "stopped": ("СТАТУС: ЗУПИНЕНО", CyberPalette.TEXT_MUTED),
            "starting": ("СТАТУС: ЗАПУСКАЄТЬСЯ...", CyberPalette.CYAN),
            "running": ("СТАТУС: ПРАЦЮЄ (ONLINE)", CyberPalette.OK_GREEN),
            "error": ("СТАТУС: ПОМИЛКА ПРОЦЕСУ", CyberPalette.ERR_RED),
        }
        text, color = labels.get(status, ("СТАТУС: НЕВІДОМО", CyberPalette.TEXT_MAIN))
        self.status_badge.setText(text)
        self.status_badge.setStyleSheet(f"color: {color}; font-weight: bold;")

        if status == "error" and detail:
            self.error_detail_label.setText(detail)
            self.error_detail_label.setVisible(True)
        else:
            self.error_detail_label.clear()
            self.error_detail_label.setVisible(False)

        is_active = status in {"running", "starting"}
        self.btn_start.setEnabled(not is_active)
        self.btn_stop.setEnabled(is_active)

    def update_preflight(self, items: list[PreflightItem]) -> None:
        """Перебудовує список пунктів Preflight із кнопкою 'Виправити' для кожної проблеми."""
        while self.preflight_rows_layout.count():
            child = self.preflight_rows_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

        for item in items:
            row_frame = QFrame()
            row_layout = QHBoxLayout(row_frame)
            row_layout.setContentsMargins(8, 6, 8, 6)

            badge = QLabel("● OK" if item.ok else "▲ УВАГА")
            badge_color = CyberPalette.OK_GREEN if item.ok else CyberPalette.ERR_RED
            badge.setStyleSheet(f"color: {badge_color}; font-weight: bold; min-width: 72px;")

            title_lbl = QLabel(f"{item.title}:")
            title_lbl.setStyleSheet("font-weight: 600; min-width: 170px;")

            detail_lbl = QLabel(item.detail)
            detail_lbl.setObjectName("MutedText")

            row_layout.addWidget(badge)
            row_layout.addWidget(title_lbl)
            row_layout.addWidget(detail_lbl, stretch=1)

            if not item.ok and self._on_navigate is not None:
                fix_btn = QPushButton("Виправити")
                fix_btn.clicked.connect(
                    lambda _checked=False, page_id=item.fix_page_id: self._on_navigate(page_id)
                )
                row_layout.addWidget(fix_btn)

            self.preflight_rows_layout.addWidget(row_frame)

"""Сторінка 06 // Монітори ([monitors]): список дисплеїв, неонові банери ідентифікації та вибір монітора документації."""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from friday.launcher.pages import BasePage, make_cyber_card
from friday.launcher.services.config_store import ConfigStore
from friday.launcher.theme import CyberPalette
from friday.util.monitors import MonitorInfo, list_monitors


class MonitorIdentifyOverlay(QWidget):
    """Напівпрозорий неоновий оверлей із великим номером монітора, що автоматично зникає."""

    def __init__(
        self,
        display_number: int,
        device_name: str,
        x: int,
        y: int,
        auto_close_ms: int = 1800,
    ) -> None:
        super().__init__(
            None,
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_QuitOnClose, False)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setFixedSize(260, 180)
        self.move(x, y)
        self.setStyleSheet(
            f"""
            QWidget {{
                background-color: {CyberPalette.BG_PANEL};
                border: 2px solid {CyberPalette.CYAN};
                border-radius: 12px;
            }}
            """
        )
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        num_lbl = QLabel(str(display_number))
        num_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        num_lbl.setStyleSheet(
            f"color: {CyberPalette.CYAN}; font-size: 72px; font-weight: bold; border: none;"
        )

        sub_lbl = QLabel(f"МОНІТОР #{display_number} ({device_name})")
        sub_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sub_lbl.setStyleSheet(
            f"color: {CyberPalette.TEXT_MAIN}; font-size: 13px; border: none;"
        )

        layout.addWidget(num_lbl)
        layout.addWidget(sub_lbl)

        self._close_timer: QTimer | None = None
        if not isinstance(auto_close_ms, bool) and auto_close_ms > 0:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(self.close)
            timer.start(int(auto_close_ms))
            self._close_timer = timer

    def mousePressEvent(self, event: object) -> None:  # noqa: ARG002
        self.close()


class MonitorsPage(BasePage):
    """Перегляд підключених моніторів, ідентифікація номерів на екранах та вибір монітора документації."""

    def __init__(
        self,
        config_store: ConfigStore | None = None,
        monitors_provider: Callable[[], list[MonitorInfo]] | None = None,
        on_monitors_changed: Callable[[], None] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(
            page_id="monitors",
            title="06 // МОНІТОРИ ТА РОЗКЛАДКА",
            subtitle=(
                "Перегляд підключених дисплеїв, візуальна ідентифікація номерів на екранах "
                "та вибір цільового монітора для відкриття документації (docs.lookup)."
            ),
            parent=parent,
        )
        self.config_store = config_store or ConfigStore()
        self._monitors_provider = monitors_provider or list_monitors
        self._on_monitors_changed = on_monitors_changed
        self._active_overlays: list[MonitorIdentifyOverlay] = []

        # --- Картка 1: Підключені монітори та Ідентифікація ---
        list_card, list_layout = make_cyber_card(
            "ПІДКЛЮЧЕНІ ДИСПЛЕЇ СИСТЕМИ",
            "Натисніть «Ідентифікувати монітори», щоб коротко показати великий неоновий номер на кожному екрані.",
        )

        action_row = QHBoxLayout()
        self.btn_identify = QPushButton("Ідентифікувати монітори")
        self.btn_identify.setObjectName("PrimaryButton")
        self.btn_identify.clicked.connect(lambda _checked=False: self.identify_monitors(1800))

        self.btn_refresh = QPushButton("Оновити список")
        self.btn_refresh.clicked.connect(lambda _checked=False: self.refresh_monitors_ui())

        action_row.addWidget(self.btn_identify)
        action_row.addWidget(self.btn_refresh)
        action_row.addStretch()
        list_layout.addLayout(action_row)

        self.monitors_rows_container = QWidget()
        self.monitors_rows_layout = QVBoxLayout(self.monitors_rows_container)
        self.monitors_rows_layout.setContentsMargins(0, 6, 0, 0)
        self.monitors_rows_layout.setSpacing(8)
        list_layout.addWidget(self.monitors_rows_container)
        self.root_layout.addWidget(list_card)

        # --- Картка 2: Призначення моніторів ([monitors]) ---
        assign_card, assign_layout = make_cyber_card(
            "ПРИЗНАЧЕННЯ МОНІТОРА ДЛЯ ДОКУМЕНТАЦІЇ ([MONITORS])",
            "На цей монітор FRIDAY автоматично переноситиме вікно браузера при натисканні Ctrl+Alt+D (explain_clipboard).",
        )
        assign_row = QHBoxLayout()
        self.combo_docs_monitor = QComboBox()
        self.btn_save_monitors = QPushButton("Зберегти вибір монітора")
        self.btn_save_monitors.setObjectName("PrimaryButton")
        self.btn_save_monitors.clicked.connect(self.save_monitors_config)

        assign_row.addWidget(QLabel("Монітор для документації (docs):"))
        assign_row.addWidget(self.combo_docs_monitor, stretch=1)
        assign_row.addWidget(self.btn_save_monitors)
        assign_layout.addLayout(assign_row)

        self.lbl_status = QLabel("")
        self.lbl_status.setObjectName("MutedText")
        assign_layout.addWidget(self.lbl_status)

        self.root_layout.addWidget(assign_card)
        self.root_layout.addStretch()

        self.refresh_monitors_ui()

    def get_current_monitors(self) -> list[MonitorInfo]:
        try:
            return self._monitors_provider()
        except Exception:
            return []

    def refresh_monitors_ui(self) -> None:
        """Перебудовує картки підключених моніторів і випадаючий список вибору."""
        while self.monitors_rows_layout.count():
            child = self.monitors_rows_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

        cfg = self.config_store.load_merged()
        monitors = self.get_current_monitors()

        self.combo_docs_monitor.clear()
        for mon in monitors:
            num = mon.index + 1
            role = "ОСНОВНИЙ" if mon.is_primary else "ДОДАТКОВИЙ"
            w = mon.work_area
            row = QFrame()
            r_layout = QHBoxLayout(row)
            r_layout.setContentsMargins(8, 6, 8, 6)

            badge = QLabel(f"МОНІТОР #{num}")
            badge.setStyleSheet(f"color: {CyberPalette.CYAN}; font-weight: bold; min-width: 115px;")

            role_lbl = QLabel(role)
            role_color = CyberPalette.OK_GREEN if mon.is_primary else CyberPalette.TEXT_MUTED
            role_lbl.setStyleSheet(f"color: {role_color}; font-weight: bold; min-width: 115px;")

            coords_lbl = QLabel(
                f"{mon.device}  |  Робоча область: {w.width}×{w.height} px  (X={w.left}, Y={w.top})"
            )
            coords_lbl.setObjectName("MutedText")

            r_layout.addWidget(badge)
            r_layout.addWidget(role_lbl)
            r_layout.addWidget(coords_lbl, stretch=1)
            self.monitors_rows_layout.addWidget(row)

            self.combo_docs_monitor.addItem(
                f"Монітор #{num} ({mon.device} — {w.width}×{w.height}, {role})",
                mon.index,
            )
            if mon.index == cfg.monitors.docs:
                self.combo_docs_monitor.setCurrentIndex(self.combo_docs_monitor.count() - 1)

        if self.combo_docs_monitor.count() == 0:
            self.combo_docs_monitor.addItem("Монітор #1 (індекс 0)", 0)
            self.combo_docs_monitor.addItem("Монітор #2 (індекс 1)", 1)
            self.combo_docs_monitor.setCurrentIndex(min(1, cfg.monitors.docs))

    def identify_monitors(self, duration_ms: int = 1800) -> int:
        """Коротко показує великий номер на кожному підключеному моніторі."""
        if isinstance(duration_ms, bool):
            duration_ms = 1800

        self.close_identify_overlays()

        monitors = self.get_current_monitors()
        for mon in monitors:
            w = mon.work_area
            cx = w.left + max(0, (w.width - 260) // 2)
            cy = w.top + max(0, (w.height - 180) // 2)
            overlay = MonitorIdentifyOverlay(
                mon.index + 1,
                mon.device,
                cx,
                cy,
                auto_close_ms=duration_ms,
            )
            overlay.show()
            self._active_overlays.append(overlay)

        if duration_ms > 0:
            QTimer.singleShot(duration_ms, self.close_identify_overlays)
        return len(self._active_overlays)

    def close_identify_overlays(self) -> None:
        for ov in list(self._active_overlays):
            try:
                ov.close()
            except Exception:
                pass
        self._active_overlays.clear()

    def hideEvent(self, event: object) -> None:
        self.close_identify_overlays()
        super().hideEvent(event)  # type: ignore[arg-type]

    def save_monitors_config(self) -> None:
        """Зберігає вибраний індекс монітора для документації у config.local.toml."""
        idx_data = self.combo_docs_monitor.currentData()
        docs_idx = int(idx_data) if idx_data is not None else 0
        self.config_store.save_local_overrides({"monitors": {"docs": docs_idx}})
        self.lbl_status.setText(
            f"● Монітор для документації збережено: Монітор #{docs_idx + 1} (індекс {docs_idx})."
        )
        self.lbl_status.setStyleSheet(f"color: {CyberPalette.OK_GREEN}; font-weight: bold;")
        if self._on_monitors_changed is not None:
            self._on_monitors_changed()

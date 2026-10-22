"""Сторінка 08 // Журнал подій (живий перегляд logs/friday.log)."""

from __future__ import annotations

from PySide6.QtWidgets import QWidget

from friday.launcher.pages import BasePage, make_cyber_card


class LogsPage(BasePage):
    """Живий перегляд логів friday.log та виводу дочірнього процесу з фільтром за рівнем."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(
            page_id="logs",
            title="08 // ЖУРНАЛ ПОДІЙ (LOGS)",
            subtitle="Моніторинг роботи ядра в реальному часі (JetBrains Mono).",
            parent=parent,
        )
        card, _ = make_cyber_card("СИСТЕМНИЙ ТЕРМІНАЛ ЛОГІВ")
        self.root_layout.addWidget(card)
        self.root_layout.addStretch()

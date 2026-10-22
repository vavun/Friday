"""Сторінка 07 // Безпека ([safety], перегляд заборонених патернів)."""

from __future__ import annotations

from PySide6.QtWidgets import QWidget

from friday.launcher.pages import BasePage, make_cyber_card


class SafetyPage(BasePage):
    """Налаштування Confirm Gate та перегляд заблокованих команд із safety.py."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(
            page_id="safety",
            title="07 // БЕЗПЕКА (CONFIRM GATE)",
            subtitle="Обов'язкове підтвердження чутливих дій та перелік заблокованих shell-патернів.",
            parent=parent,
        )
        card, _ = make_cyber_card("ПОЛІТИКИ БЕЗПЕКИ")
        self.root_layout.addWidget(card)
        self.root_layout.addStretch()

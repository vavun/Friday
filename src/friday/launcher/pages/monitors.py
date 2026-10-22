"""Сторінка 06 // Монітори ([monitors], ідентифікація екранів)."""

from __future__ import annotations

from PySide6.QtWidgets import QWidget

from friday.launcher.pages import BasePage, make_cyber_card


class MonitorsPage(BasePage):
    """Перегляд підключених моніторів, ідентифікація номерів на екранах та вибір монітора документації."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(
            page_id="monitors",
            title="06 // МОНІТОРИ ТА РОЗКЛАДКА",
            subtitle="Візуальна ідентифікація моніторів та призначення екрана для документації.",
            parent=parent,
        )
        card, _ = make_cyber_card("КОНФІГУРАЦІЯ ДИСПЛЕЇВ")
        self.root_layout.addWidget(card)
        self.root_layout.addStretch()

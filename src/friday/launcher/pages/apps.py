"""Сторінка 04 // Програми ([apps] білий список + сканер меню Пуск)."""

from __future__ import annotations

from PySide6.QtWidgets import QWidget

from friday.launcher.pages import BasePage, make_cyber_card


class AppsPage(BasePage):
    """Керування білим списком програм для голосового запуску."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(
            page_id="apps",
            title="04 // ПРОГРАМИ (БІЛИЙ СПИСОК)",
            subtitle="Голосові назви програм, шляхи до .exe/.lnk та сканування меню Пуск.",
            parent=parent,
        )
        card, _ = make_cyber_card("ДОЗВОЛЕНІ ПРОГРАМИ")
        self.root_layout.addWidget(card)
        self.root_layout.addStretch()

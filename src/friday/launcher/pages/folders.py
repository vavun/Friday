"""Сторінка 03 // Дозволені папки ([paths].allowed_folders)."""

from __future__ import annotations

from PySide6.QtWidgets import QWidget

from friday.launcher.pages import BasePage, make_cyber_card


class FoldersPage(BasePage):
    """Керування білим списком папок із перевіркою безпеки шляхів (path_rules)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(
            page_id="folders",
            title="03 // ДОЗВОЛЕНІ ПАПКИ",
            subtitle="Папки, до яких FRIDAY має доступ на читання або читання й запис.",
            parent=parent,
        )
        card, _ = make_cyber_card("БІЛИЙ СПИСОК ДИРЕКТОРІЙ")
        self.root_layout.addWidget(card)
        self.root_layout.addStretch()

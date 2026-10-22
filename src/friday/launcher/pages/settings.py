"""Сторінка 09 // Налаштування лаунчера ([ui], автозапуск, трей, анімації)."""

from __future__ import annotations

from PySide6.QtWidgets import QWidget

from friday.launcher.pages import BasePage, make_cyber_card


class SettingsPage(BasePage):
    """Налаштування автозапуску з Windows, згортання в трей, зменшення анімацій та рівня логів."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(
            page_id="settings",
            title="09 // НАЛАШТУВАННЯ СИСТЕМИ",
            subtitle="Автозапуск, поведінка системного трею, продуктивність UI та мова.",
            parent=parent,
        )
        card, _ = make_cyber_card("ПАРАМЕТРИ ЛАУНЧЕРА")
        self.root_layout.addWidget(card)
        self.root_layout.addStretch()

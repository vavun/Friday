"""Сторінка 02 // Ключі API (Gemini & Telegram)."""

from __future__ import annotations

from PySide6.QtWidgets import QWidget

from friday.launcher.pages import BasePage, make_cyber_card


class KeysPage(BasePage):
    """Керування секретними ключами (.env): GEMINI_API_KEY та TELEGRAM_API_ID / HASH."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(
            page_id="keys",
            title="02 // КЛЮЧІ API (.ENV)",
            subtitle="Безпечне збереження ключів у .env без потрапляння в код, логи чи Git.",
            parent=parent,
        )
        card, _ = make_cyber_card("GEMINI API KEY & TELEGRAM API")
        self.root_layout.addWidget(card)
        self.root_layout.addStretch()

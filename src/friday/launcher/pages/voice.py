"""Сторінка 05 // Голос і гарячі клавіші ([hotkeys], [audio], [stt])."""

from __future__ import annotations

from PySide6.QtWidgets import QWidget

from friday.launcher.pages import BasePage, make_cyber_card


class VoicePage(BasePage):
    """Налаштування гарячих клавіш, мікрофона та STT-бекенда."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(
            page_id="voice",
            title="05 // ГОЛОС І ГАРЯЧІ КЛАВІШІ",
            subtitle="Захоплення клавіш, вибір мікрофона, рівень сигналу та STT-бекенд (Gemini / Whisper).",
            parent=parent,
        )
        card, _ = make_cyber_card("ПАРАМЕТРИ АУДІО ТА STT")
        self.root_layout.addWidget(card)
        self.root_layout.addStretch()

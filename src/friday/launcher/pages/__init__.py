"""Базові віджети сторінок та анімована неонова орба статусу (<= 30 FPS, 0% CPU у прихованому стані)."""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, Qt, QTimer
from PySide6.QtGui import QColor, QHideEvent, QPainter, QPaintEvent, QPen, QRadialGradient, QShowEvent
from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout, QWidget

from friday.launcher.theme import CyberPalette


class NeonOrbWidget(QWidget):
    """Індикатор-'орба', що плавно пульсує (<= 24 FPS) коли Friday працює і повністю зупиняє таймер у трей-режимі."""

    def __init__(self, parent: QWidget | None = None, reduce_animations: bool = False) -> None:
        super().__init__(parent)
        self.setFixedSize(116, 116)
        self._status: str = "stopped"  # stopped | starting | running | error
        self._reduce_animations: bool = reduce_animations
        self._phase: float = 0.0

        self._timer = QTimer(self)
        self._timer.setInterval(42)  # ~24 FPS (< 30 FPS згідно з ТЗ)
        self._timer.timeout.connect(self._tick)

    @property
    def is_animating(self) -> bool:
        return self._timer.isActive()

    def set_reduce_animations(self, reduce: bool) -> None:
        self._reduce_animations = reduce
        self._update_timer_state()
        self.update()

    def set_status(self, status: str) -> None:
        self._status = status
        self._update_timer_state()
        self.update()

    def _update_timer_state(self) -> None:
        should_run = (
            self.isVisible()
            and not self._reduce_animations
            and self._status in {"running", "starting"}
        )
        if should_run and not self._timer.isActive():
            self._timer.start()
        elif not should_run and self._timer.isActive():
            self._timer.stop()

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        self._update_timer_state()

    def hideEvent(self, event: QHideEvent) -> None:
        super().hideEvent(event)
        self._timer.stop()

    def _tick(self) -> None:
        self._phase = (self._phase + 0.14) % (2.0 * math.pi)
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        w, h = self.width(), self.height()
        cx, cy = w / 2.0, h / 2.0

        if self._status == "running":
            primary = QColor(CyberPalette.CYAN)
            core = QColor(CyberPalette.OK_GREEN)
        elif self._status == "starting":
            primary = QColor(CyberPalette.ELECTRIC_BLUE)
            core = QColor(CyberPalette.CYAN)
        elif self._status == "error":
            primary = QColor(CyberPalette.ERR_RED)
            core = QColor(CyberPalette.ERR_RED)
        else:
            primary = QColor(CyberPalette.BORDER_DIM)
            core = QColor(CyberPalette.ELECTRIC_BLUE)

        pulse = 0.5 * (1.0 + math.sin(self._phase)) if self._timer.isActive() else 0.25
        outer_r = 38.0 + pulse * 12.0

        grad = QRadialGradient(QPointF(cx, cy), outer_r)
        c_glow = QColor(primary)
        c_glow.setAlpha(int(95 + 80 * pulse))
        c_edge = QColor(primary)
        c_edge.setAlpha(0)
        grad.setColorAt(0.0, core)
        grad.setColorAt(0.45, c_glow)
        grad.setColorAt(1.0, c_edge)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(grad)
        painter.drawEllipse(QPointF(cx, cy), outer_r, outer_r)

        pen = QPen(primary, 2.0)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(QPointF(cx, cy), 30.0, 30.0)
        painter.end()


def make_cyber_card(title: str, subtitle: str = "") -> tuple[QFrame, QVBoxLayout]:
    """Створює стилізовану напівпрозору картку з неоновою лівою гранню."""
    card = QFrame()
    card.setObjectName("CyberCard")
    layout = QVBoxLayout(card)
    layout.setContentsMargins(16, 14, 16, 14)
    layout.setSpacing(8)

    lbl_title = QLabel(title)
    lbl_title.setObjectName("SectionTitle")
    layout.addWidget(lbl_title)

    if subtitle:
        lbl_sub = QLabel(subtitle)
        lbl_sub.setObjectName("MutedText")
        lbl_sub.setWordWrap(True)
        layout.addWidget(lbl_sub)

    return card, layout


class BasePage(QWidget):
    """Базова сторінка лаунчера з заголовком у техно-шрифті."""

    def __init__(self, page_id: str, title: str, subtitle: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.page_id = page_id
        self.root_layout = QVBoxLayout(self)
        self.root_layout.setContentsMargins(24, 20, 24, 20)
        self.root_layout.setSpacing(14)

        self.title_label = QLabel(title)
        self.title_label.setObjectName("PageTitle")
        self.root_layout.addWidget(self.title_label)

        if subtitle:
            self.subtitle_label = QLabel(subtitle)
            self.subtitle_label.setObjectName("MutedText")
            self.subtitle_label.setWordWrap(True)
            self.root_layout.addWidget(self.subtitle_label)

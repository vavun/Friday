"""Неонова кіберпанк-тема Friday Launcher (QSS, палітра, локальні шрифти та ефекти світіння)."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QColor, QFontDatabase, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QGraphicsDropShadowEffect, QWidget

ASSETS_DIR = Path(__file__).resolve().parent / "assets"
FONTS_DIR = ASSETS_DIR / "fonts"


class CyberPalette:
    """Кольорова палітра неонового кіберпанку (блакитний / ціан)."""

    BG_DEEP = "#05080F"
    BG_PANEL = "#0A0F1C"
    BG_CARD = "#0E1628"
    BG_INPUT = "#080D18"
    BORDER_DIM = "#182946"
    CYAN = "#00E5FF"
    ELECTRIC_BLUE = "#2979FF"
    TEXT_MAIN = "#E6F4FF"
    TEXT_MUTED = "#7F9BBF"
    OK_GREEN = "#00E676"
    ERR_RED = "#FF3D57"
    WARN_AMBER = "#FFB300"


def load_bundled_fonts() -> dict[str, str]:
    """Завантажує локальні шрифти Rajdhani (заголовки) та JetBrains Mono (логи) з assets/fonts."""
    families: dict[str, str] = {
        "heading": "Segoe UI",
        "mono": "Consolas",
    }
    if not FONTS_DIR.exists():
        return families

    for ttf_path in sorted(FONTS_DIR.glob("*.ttf")):
        font_id = QFontDatabase.addApplicationFont(str(ttf_path))
        if font_id >= 0:
            loaded = QFontDatabase.applicationFontFamilies(font_id)
            if loaded:
                fam = loaded[0]
                if "rajdhani" in fam.lower() or "orbitron" in fam.lower():
                    families["heading"] = fam
                elif "jetbrains" in fam.lower() or "mono" in fam.lower():
                    families["mono"] = fam
    return families


def apply_neon_glow(
    widget: QWidget,
    color_hex: str = CyberPalette.CYAN,
    blur_radius: float = 18.0,
) -> QGraphicsDropShadowEffect:
    """Додає неонове світіння (QGraphicsDropShadowEffect) до віджета."""
    effect = QGraphicsDropShadowEffect(widget)
    color = QColor(color_hex)
    color.setAlpha(140)
    effect.setColor(color)
    effect.setOffset(0.0, 0.0)
    effect.setBlurRadius(blur_radius)
    widget.setGraphicsEffect(effect)
    return effect


def create_neon_orb_icon(size: int = 64, active: bool = False) -> QIcon:
    """Генерує векторну неонову іконку для вікна та системного трею без зовнішніх зображень."""
    pixmap = QPixmap(size, size)
    pixmap.fill(QColor(0, 0, 0, 0))
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

    outer = QColor(CyberPalette.CYAN if active else CyberPalette.ELECTRIC_BLUE)
    outer.setAlpha(90)
    painter.setBrush(outer)
    painter.setPen(QColor(CyberPalette.CYAN))
    margin = size // 8
    painter.drawEllipse(margin, margin, size - 2 * margin, size - 2 * margin)

    inner = QColor(CyberPalette.OK_GREEN if active else CyberPalette.CYAN)
    painter.setBrush(inner)
    core_m = size // 3
    painter.drawEllipse(core_m, core_m, size - 2 * core_m, size - 2 * core_m)
    painter.end()
    return QIcon(pixmap)


def build_cyberpunk_qss(heading_font: str = "Rajdhani", mono_font: str = "JetBrains Mono") -> str:
    """Повертає QSS-таблицю стилів для всього застосунку."""
    p = CyberPalette
    return f"""
    QMainWindow, QWidget#RootContainer {{
        background-color: {p.BG_DEEP};
        color: {p.TEXT_MAIN};
        font-family: "Segoe UI", sans-serif;
        font-size: 13px;
    }}

    QFrame#Sidebar {{
        background-color: {p.BG_PANEL};
        border-right: 1px solid {p.BORDER_DIM};
    }}

    QLabel#BrandTitle {{
        font-family: "{heading_font}", "Segoe UI";
        font-size: 24px;
        font-weight: bold;
        color: {p.CYAN};
        letter-spacing: 2px;
    }}

    QLabel#BrandSubtitle {{
        color: {p.TEXT_MUTED};
        font-size: 11px;
        letter-spacing: 1px;
    }}

    QLabel#PageTitle {{
        font-family: "{heading_font}", "Segoe UI";
        font-size: 22px;
        font-weight: bold;
        color: {p.CYAN};
        letter-spacing: 1px;
    }}

    QLabel#SectionTitle {{
        font-family: "{heading_font}", "Segoe UI";
        font-size: 16px;
        font-weight: bold;
        color: {p.TEXT_MAIN};
    }}

    QLabel#MutedText {{
        color: {p.TEXT_MUTED};
    }}

    QFrame#CyberCard {{
        background-color: {p.BG_CARD};
        border: 1px solid {p.BORDER_DIM};
        border-left: 3px solid {p.ELECTRIC_BLUE};
        border-radius: 4px;
    }}

    QFrame#CyberCard:hover {{
        border: 1px solid {p.CYAN};
        border-left: 3px solid {p.CYAN};
    }}

    QPushButton#NavButton {{
        text-align: left;
        padding: 10px 16px;
        background-color: transparent;
        color: {p.TEXT_MUTED};
        border: none;
        border-left: 3px solid transparent;
        font-family: "{heading_font}", "Segoe UI";
        font-size: 15px;
        font-weight: 600;
    }}

    QPushButton#NavButton:hover {{
        color: {p.TEXT_MAIN};
        background-color: #101A30;
        border-left: 3px solid {p.ELECTRIC_BLUE};
    }}

    QPushButton#NavButton[active="true"] {{
        color: {p.CYAN};
        background-color: #12203A;
        border-left: 3px solid {p.CYAN};
    }}

    QPushButton {{
        background-color: #101B33;
        color: {p.TEXT_MAIN};
        border: 1px solid {p.ELECTRIC_BLUE};
        border-radius: 3px;
        padding: 7px 14px;
        font-weight: 600;
    }}

    QPushButton:hover {{
        background-color: #16294D;
        border: 1px solid {p.CYAN};
        color: {p.CYAN};
    }}

    QPushButton:pressed {{
        background-color: {p.ELECTRIC_BLUE};
        color: {p.BG_DEEP};
    }}

    QPushButton#PrimaryButton {{
        background-color: #092638;
        border: 1px solid {p.CYAN};
        color: {p.CYAN};
        font-family: "{heading_font}", "Segoe UI";
        font-size: 15px;
        letter-spacing: 1px;
    }}

    QPushButton#PrimaryButton:hover {{
        background-color: {p.CYAN};
        color: {p.BG_DEEP};
    }}

    QPushButton#DangerButton {{
        background-color: #260D16;
        border: 1px solid {p.ERR_RED};
        color: {p.ERR_RED};
    }}

    QPushButton#DangerButton:hover {{
        background-color: {p.ERR_RED};
        color: #FFFFFF;
    }}

    QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QPlainTextEdit {{
        background-color: {p.BG_INPUT};
        color: {p.TEXT_MAIN};
        border: 1px solid {p.BORDER_DIM};
        border-radius: 3px;
        padding: 6px 10px;
        selection-background-color: {p.ELECTRIC_BLUE};
    }}

    QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus, QPlainTextEdit:focus {{
        border: 1px solid {p.CYAN};
    }}

    QPlainTextEdit#LogViewer {{
        font-family: "{mono_font}", "Consolas", monospace;
        font-size: 12px;
        background-color: #04070D;
        border: 1px solid {p.BORDER_DIM};
    }}

    QScrollBar:vertical {{
        background-color: #060A14;
        width: 10px;
        margin: 2px 0 2px 0;
        border-radius: 4px;
    }}

    QScrollBar::handle:vertical {{
        background-color: {p.BORDER_DIM};
        min-height: 24px;
        border-radius: 4px;
    }}

    QScrollBar::handle:vertical:hover {{
        background-color: {p.CYAN};
    }}

    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
        height: 0px;
    }}

    QScrollBar:horizontal {{
        background-color: #060A14;
        height: 10px;
        margin: 0 2px 0 2px;
        border-radius: 4px;
    }}

    QScrollBar::handle:horizontal {{
        background-color: {p.BORDER_DIM};
        min-width: 24px;
        border-radius: 4px;
    }}

    QScrollBar::handle:horizontal:hover {{
        background-color: {p.CYAN};
    }}

    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
        width: 0px;
    }}

    QCheckBox {{
        color: {p.TEXT_MAIN};
        spacing: 8px;
        min-height: 26px;
        font-size: 13px;
    }}

    QCheckBox::indicator {{
        width: 18px;
        height: 18px;
        border: 1px solid {p.BORDER_DIM};
        background-color: {p.BG_INPUT};
        border-radius: 3px;
    }}

    QCheckBox::indicator:hover {{
        border: 1px solid {p.CYAN};
    }}

    QCheckBox::indicator:checked {{
        background-color: #0A3245;
        border: 1px solid {p.CYAN};
    }}
    """

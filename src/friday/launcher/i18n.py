"""Словник рядків інтерфейсу Friday Launcher (українська за замовчуванням, готовий до англійської)."""

from __future__ import annotations

STRINGS: dict[str, dict[str, str]] = {
    "uk": {
        "app.title": "FRIDAY // NEON CONTROL DECK",
        "app.subtitle": "ЛОКАЛЬНИЙ ГОЛОСОВИЙ ТА ЖЕСТОВИЙ АСИСТЕНТ",
        "nav.dashboard": "01 // ПАНЕЛЬ КЕРУВАННЯ",
        "nav.keys": "02 // КЛЮЧІ API",
        "nav.folders": "03 // ДОЗВОЛЕНІ ПАПКИ",
        "nav.apps": "04 // ПРОГРАМИ",
        "nav.voice": "05 // ГОЛОС І КЛАВІШІ",
        "nav.monitors": "06 // МОНІТОРИ",
        "nav.safety": "07 // БЕЗПЕКА",
        "nav.logs": "08 // ЖУРНАЛ ПОДІЙ",
        "nav.settings": "09 // НАЛАШТУВАННЯ",
        "status.stopped": "ЗУПИНЕНО",
        "status.starting": "ЗАПУСКАЄТЬСЯ",
        "status.running": "ПРАЦЮЄ",
        "status.error": "ПОМИЛКА",
        "btn.start": "ЗАПУСТИТИ FRIDAY",
        "btn.stop": "ЗУПИНИТИ",
        "btn.restart": "ПЕРЕЗАПУСТИТИ",
        "btn.fix": "Виправити",
        "tray.open": "Відкрити Friday Launcher",
        "tray.start": "Запустити Friday",
        "tray.stop": "Зупинити Friday",
        "tray.quit": "Вийти",
    },
    "en": {
        "app.title": "FRIDAY // NEON CONTROL DECK",
        "app.subtitle": "LOCAL VOICE & GESTURE ASSISTANT",
        "nav.dashboard": "01 // DASHBOARD",
        "nav.keys": "02 // API KEYS",
        "nav.folders": "03 // ALLOWED FOLDERS",
        "nav.apps": "04 // APPLICATIONS",
        "nav.voice": "05 // VOICE & HOTKEYS",
        "nav.monitors": "06 // MONITORS",
        "nav.safety": "07 // SAFETY GATE",
        "nav.logs": "08 // SYSTEM LOGS",
        "nav.settings": "09 // SETTINGS",
        "status.stopped": "STOPPED",
        "status.starting": "STARTING",
        "status.running": "ONLINE",
        "status.error": "ERROR",
        "btn.start": "START FRIDAY",
        "btn.stop": "STOP",
        "btn.restart": "RESTART",
        "btn.fix": "Fix",
        "tray.open": "Open Friday Launcher",
        "tray.start": "Start Friday",
        "tray.stop": "Stop Friday",
        "tray.quit": "Quit",
    },
}


def tr(key: str, lang: str = "uk") -> str:
    """Повертає локалізований рядок за ключем."""
    table = STRINGS.get(lang, STRINGS["uk"])
    return table.get(key, STRINGS["uk"].get(key, key))

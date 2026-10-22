"""Передстартова діагностика (Preflight) для Dashboard без імпорту важких моделей у пам'ять."""

from __future__ import annotations

import importlib.util
from dataclasses import dataclass

from friday.config import AppConfig
from friday.launcher.services.env_store import EnvStore


@dataclass(slots=True, frozen=True)
class PreflightItem:
    """Один пункт перевірки готовності з цільовою сторінкою для кнопки 'Виправити'."""

    id: str
    title: str
    ok: bool
    detail: str
    fix_page_id: str


def check_core_dependencies() -> tuple[bool, str]:
    """Перевіряє наявність базових бібліотек ядра через find_spec (без їх імпорту)."""
    required = ("pydantic", "pynput", "sounddevice", "numpy", "google.genai", "win32gui")
    missing = [pkg for pkg in required if importlib.util.find_spec(pkg) is None]
    if missing:
        return (False, f"Відсутні пакети: {', '.join(missing)}")
    return (True, "Усі базові залежності встановлено")


def check_microphone_present() -> tuple[bool, str]:
    """Швидко перевіряє наявність вхідного аудіопристрою через WinMM без ініціалізації важких аудіо-потоків."""
    if importlib.util.find_spec("sounddevice") is None:
        return (False, "Модуль sounddevice не встановлено")
    try:
        import ctypes

        if hasattr(ctypes, "windll") and hasattr(ctypes.windll, "winmm"):
            count = int(ctypes.windll.winmm.waveInGetNumDevs())
            if count <= 0:
                return (False, "Не знайдено жодного мікрофона у системі (WinMM)")
            return (True, f"Знайдено вхідних аудіопристроїв: {count}")
    except Exception:
        pass

    try:
        import sounddevice as sd

        devices = sd.query_devices()
        inputs = [d for d in devices if int(d.get("max_input_channels", 0)) > 0]
        if not inputs:
            return (False, "Не знайдено жодного мікрофона у системі")
        return (True, f"Знайдено вхідних аудіопристроїв: {len(inputs)}")
    except Exception as exc:
        return (False, f"Не вдалося опитати мікрофон: {exc}")


def run_preflight_checks(
    config: AppConfig,
    env_store: EnvStore,
    mic_checker: tuple[bool, str] | None = None,
) -> list[PreflightItem]:
    """Формує повний список пунктів готовності для Dashboard."""
    items: list[PreflightItem] = []

    # 1. Ключ Gemini API (якщо обрано бекенд gemini або для розумного роутера)
    gemini_key = env_store.get_key("GEMINI_API_KEY")
    has_key = len(gemini_key.strip()) >= 10
    items.append(
        PreflightItem(
            id="gemini_key",
            title="Ключ Gemini API",
            ok=has_key,
            detail=f"Встановлено ({env_store.get_masked('GEMINI_API_KEY')})"
            if has_key
            else "GEMINI_API_KEY не задано у .env",
            fix_page_id="keys",
        )
    )

    # 2. Мікрофон
    mic_ok, mic_detail = mic_checker if mic_checker is not None else check_microphone_present()
    items.append(
        PreflightItem(
            id="microphone",
            title="Мікрофон (Аудіовхід)",
            ok=mic_ok,
            detail=mic_detail,
            fix_page_id="voice",
        )
    )

    # 3. Залежності
    deps_ok, deps_detail = check_core_dependencies()
    items.append(
        PreflightItem(
            id="dependencies",
            title="Залежності Python",
            ok=deps_ok,
            detail=deps_detail,
            fix_page_id="settings",
        )
    )

    # 4. Дозволені програми та папки
    has_apps = len(config.apps) > 0
    items.append(
        PreflightItem(
            id="apps",
            title="Білий список програм",
            ok=has_apps,
            detail=f"Дозволено програм: {len(config.apps)} | Папок: {len(config.paths.allowed_folders)}",
            fix_page_id="apps",
        )
    )
    return items

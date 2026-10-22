"""Глобальні гарячі клавіші (pynput) із підтримкою hold-to-talk та комбінацій."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Callable

from pynput import keyboard

from friday.config import HotkeysConfig

log = logging.getLogger("friday.hotkeys")


class HotkeyEventType(StrEnum):
    """Типи подій від клавіатурного слухача."""

    VOICE_PRESS = "VOICE_PRESS"
    VOICE_RELEASE = "VOICE_RELEASE"
    EXPLAIN_CLIPBOARD = "EXPLAIN_CLIPBOARD"
    GESTURE_PRESS = "GESTURE_PRESS"
    GESTURE_RELEASE = "GESTURE_RELEASE"
    CANCEL = "CANCEL"
    CONFIRM = "CONFIRM"


@dataclass(slots=True)
class HotkeyEvent:
    """Подія гарячої клавіші з часовою міткою perf_counter для точного вимірювання затримки."""

    type: HotkeyEventType
    timestamp: float = field(default_factory=time.perf_counter)


_KEY_ALIASES: dict[str, str] = {
    "right_ctrl": "ctrl_r",
    "right ctrl": "ctrl_r",
    "rctrl": "ctrl_r",
    "ctrl_r": "ctrl_r",
    "left_ctrl": "ctrl_l",
    "left ctrl": "ctrl_l",
    "lctrl": "ctrl_l",
    "ctrl_l": "ctrl_l",
    "ctrl": "ctrl",
    "control": "ctrl",
    "right_alt": "alt_r",
    "right alt": "alt_r",
    "alt_gr": "alt_gr",
    "left_alt": "alt_l",
    "left alt": "alt_l",
    "alt": "alt",
    "right_shift": "shift_r",
    "left_shift": "shift_l",
    "shift": "shift",
    "esc": "esc",
    "escape": "esc",
    "enter": "enter",
    "return": "enter",
    "space": "space",
}


def normalize_key_token(token: str) -> str:
    """Нормалізує текстову назву клавіші до внутрішнього канонічного токена."""
    cleaned = token.strip().lower()
    return _KEY_ALIASES.get(cleaned, cleaned)


def parse_combo(combo_str: str) -> frozenset[str]:
    """Розбирає рядок комбінації (наприклад, 'ctrl+alt+d') на множину канонічних токенів."""
    parts = [normalize_key_token(p) for p in combo_str.split("+") if p.strip()]
    return frozenset(parts)


def key_to_tokens(key: keyboard.Key | keyboard.KeyCode | Any) -> set[str]:
    """Перетворює об'єкт клавіші pynput на множину канонічних токенів (включно з груповими ctrl/alt/shift)."""
    tokens: set[str] = set()
    if isinstance(key, keyboard.Key):
        name = (key.name or "").lower()
        tokens.add(name)
        if name in {"ctrl_l", "ctrl_r"}:
            tokens.add("ctrl")
        elif name in {"alt_l", "alt_r", "alt_gr"}:
            tokens.add("alt")
        elif name in {"shift_l", "shift_r"}:
            tokens.add("shift")
    elif isinstance(key, keyboard.KeyCode):
        if key.char:
            ch = key.char.lower()
            tokens.add(ch)
            # При утриманні Ctrl символи a..z можуть приходити як керівні коди \x01..\x1a
            if len(key.char) == 1 and 1 <= ord(key.char) <= 26:
                tokens.add(chr(ord("a") + ord(key.char) - 1))
        if key.vk is not None and 65 <= key.vk <= 90:
            tokens.add(chr(key.vk).lower())
    elif isinstance(key, str):
        tokens.add(normalize_key_token(key))
    return tokens


class HotkeyListener:
    """Слухач клавіатури в окремому потоці, що передає події у asyncio.Queue без polling."""

    def __init__(
        self,
        config: HotkeysConfig,
        loop: asyncio.AbstractEventLoop,
        queue: asyncio.Queue[HotkeyEvent],
        listener_factory: Callable[..., Any] | None = None,
    ) -> None:
        self.config = config
        self._loop = loop
        self._queue = queue
        self._listener_factory = listener_factory or keyboard.Listener

        self._voice_token = normalize_key_token(config.voice)
        self._cancel_token = normalize_key_token(config.cancel)
        self._explain_combo = parse_combo(config.explain_clipboard)
        self._gesture_combo = parse_combo(config.gesture)

        self._pressed_tokens: set[str] = set()
        self._voice_held: bool = False
        self._explain_latched: bool = False
        self._gesture_latched: bool = False
        self._listener: Any | None = None

    def _emit(self, event_type: HotkeyEventType) -> None:
        """Потокобезпечно кладе подію в asyncio.Queue."""
        event = HotkeyEvent(type=event_type, timestamp=time.perf_counter())
        self._loop.call_soon_threadsafe(self._enqueue_nowait, event)

    def _enqueue_nowait(self, event: HotkeyEvent) -> None:
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            log.warning("Черга подій гарячих клавіш переповнена, відкинуто: %s", event.type)

    def on_press(self, key: Any) -> None:
        """Обробник натискання клавіші (викликається з потоку pynput)."""
        tokens = key_to_tokens(key)
        self._pressed_tokens.update(tokens)

        if self._voice_token in tokens and not self._voice_held:
            self._voice_held = True
            self._emit(HotkeyEventType.VOICE_PRESS)
            return

        if self._cancel_token in tokens:
            self._emit(HotkeyEventType.CANCEL)
            return

        if "enter" in tokens:
            self._emit(HotkeyEventType.CONFIRM)
            return

        if (
            self._explain_combo
            and self._explain_combo.issubset(self._pressed_tokens)
            and not self._explain_latched
        ):
            self._explain_latched = True
            self._emit(HotkeyEventType.EXPLAIN_CLIPBOARD)

        if (
            self._gesture_combo
            and self._gesture_combo.issubset(self._pressed_tokens)
            and not self._gesture_latched
        ):
            self._gesture_latched = True
            self._emit(HotkeyEventType.GESTURE_PRESS)

    def on_release(self, key: Any) -> None:
        """Обробник відпускання клавіші (викликається з потоку pynput)."""
        tokens = key_to_tokens(key)
        self._pressed_tokens.difference_update(tokens)

        if self._voice_token in tokens and self._voice_held:
            self._voice_held = False
            self._emit(HotkeyEventType.VOICE_RELEASE)

        if not self._explain_combo.issubset(self._pressed_tokens):
            self._explain_latched = False

        if self._gesture_latched and not self._gesture_combo.issubset(self._pressed_tokens):
            self._gesture_latched = False
            self._emit(HotkeyEventType.GESTURE_RELEASE)

    def start(self) -> None:
        """Запускає фоновий потік слухача pynput."""
        if self._listener is not None:
            return
        self._listener = self._listener_factory(
            on_press=self.on_press,
            on_release=self.on_release,
        )
        self._listener.start()
        log.info(
            "Слухач клавіш активний: hold=[%s], explain=[%s], cancel=[%s]",
            self.config.voice,
            self.config.explain_clipboard,
            self.config.cancel,
        )

    def stop(self) -> None:
        """Зупиняє фоновий потік слухача."""
        if self._listener is not None:
            self._listener.stop()
            self._listener = None
            log.info("Слухач клавіш зупинено.")

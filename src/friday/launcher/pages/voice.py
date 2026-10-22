"""Сторінка 05 // Голос і гарячі клавіш ([hotkeys], [audio], [stt]) із захопленням хоткеїв, перевіркою конфліктів і тестом мікрофона."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from typing import Callable

from PySide6.QtCore import QEvent, Qt, QThread, Signal
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from friday.launcher.pages import BasePage, make_cyber_card
from friday.launcher.services.config_store import ConfigStore
from friday.launcher.theme import CyberPalette

RESERVED_SYSTEM_SHORTCUTS: frozenset[str] = frozenset(
    {
        "ctrl+c",
        "ctrl+v",
        "ctrl+x",
        "ctrl+z",
        "alt+f4",
        "alt+tab",
        "ctrl+alt+del",
    }
)


def detect_hotkey_conflicts(hotkeys: dict[str, str]) -> list[str]:
    """Виявляє дублікати між гарячими клавішами FRIDAY або перетин зі стандартними комбінаціями ОС."""
    warnings: list[str] = []
    seen: dict[str, str] = {}
    for role, combo in hotkeys.items():
        norm = (combo or "").strip().lower()
        if not norm:
            warnings.append(f"Гаряча клавіша для «{role}» порожня.")
            continue
        if norm in seen:
            warnings.append(
                f"Конфлікт: комбінація «{norm}» одночасно призначена для «{seen[norm]}» та «{role}»."
            )
        else:
            seen[norm] = role
        if norm in RESERVED_SYSTEM_SHORTCUTS:
            warnings.append(
                f"Увага: комбінація «{norm}» ({role}) перекриває системну комбінацію Windows."
            )
    return warnings


def list_winmm_input_devices() -> list[tuple[int | None, str]]:
    """Отримує список мікрофонів через WinMM API без завантаження важких аудіо-бібліотек у процес лаунчера."""
    devices: list[tuple[int | None, str]] = [(None, "Системний мікрофон за замовчуванням")]
    try:
        winmm = ctypes.windll.winmm
        count = int(winmm.waveInGetNumDevs())

        class WAVEINCAPSW(ctypes.Structure):
            _fields_ = [
                ("wMid", wintypes.WORD),
                ("wPid", wintypes.WORD),
                ("vDriverVersion", wintypes.UINT),
                ("szPname", wintypes.WCHAR * 32),
                ("dwFormats", wintypes.DWORD),
                ("wChannels", wintypes.WORD),
                ("wReserved1", wintypes.WORD),
            ]

        for idx in range(count):
            caps = WAVEINCAPSW()
            if winmm.waveInGetDevCapsW(idx, ctypes.byref(caps), ctypes.sizeof(caps)) == 0:
                name = caps.szPname.strip() or f"Мікрофон #{idx}"
                devices.append((idx, f"[{idx}] {name}"))
            else:
                devices.append((idx, f"[{idx}] Аудіовхід #{idx}"))
    except Exception:
        pass
    return devices


class HotkeyCaptureEdit(QLineEdit):
    """Спеціальне поле для захоплення гарячої клавіші натисканням на клавіатурі."""

    hotkey_captured = Signal(str)

    def __init__(self, initial: str = "", parent: QWidget | None = None) -> None:
        super().__init__(initial, parent)
        self._capturing = False
        self.setReadOnly(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Клікніть і натисніть потрібну клавішу або комбінацію")

    def mousePressEvent(self, event: object) -> None:
        self._capturing = True
        self.setPlaceholderText("Натисніть клавішу...")
        self.selectAll()
        super().mousePressEvent(event)  # type: ignore[arg-type]

    def event(self, event: QEvent) -> bool:
        if self._capturing and event.type() == QEvent.Type.KeyPress:
            key_event: QKeyEvent = event  # type: ignore[assignment]
            if key_event.key() in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab):
                self.keyPressEvent(key_event)
                return True
        return super().event(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if not self._capturing:
            super().keyPressEvent(event)
            return

        key = event.key()
        mods = event.modifiers()

        # Одиночні модифікатори (наприклад Right Ctrl / Left Ctrl для hold-to-talk)
        if key == Qt.Key.Key_Control:
            # Qt nativeVirtualKey або nativeScanCode дозволяє відрізнити правий Ctrl (extended scan code)
            scan = event.nativeScanCode()
            combo = "right_ctrl" if scan in (285, 0x11D) else "ctrl"
            self._finish_capture(combo)
            return
        if key in (Qt.Key.Key_Shift, Qt.Key.Key_Alt, Qt.Key.Key_Meta):
            return

        parts: list[str] = []
        if mods & Qt.KeyboardModifier.ControlModifier:
            parts.append("ctrl")
        if mods & Qt.KeyboardModifier.AltModifier:
            parts.append("alt")
        if mods & Qt.KeyboardModifier.ShiftModifier:
            parts.append("shift")

        key_map: dict[int, str] = {
            int(Qt.Key.Key_Escape): "esc",
            int(Qt.Key.Key_Space): "space",
            int(Qt.Key.Key_Return): "enter",
            int(Qt.Key.Key_Enter): "enter",
            int(Qt.Key.Key_Tab): "tab",
        }
        if key in key_map:
            parts.append(key_map[key])
        elif Qt.Key.Key_F1 <= key <= Qt.Key.Key_F12:
            parts.append(f"f{int(key) - int(Qt.Key.Key_F1) + 1}")
        elif Qt.Key.Key_A <= key <= Qt.Key.Key_Z:
            parts.append(chr(ord("a") + (int(key) - int(Qt.Key.Key_A))))
        elif Qt.Key.Key_0 <= key <= Qt.Key.Key_9:
            parts.append(chr(ord("0") + (int(key) - int(Qt.Key.Key_0))))
        else:
            ch = event.text().strip().lower()
            if ch and ch.isprintable() and ch.isascii():
                parts.append(ch)
            else:
                seq_str = Qt.Key(key).name.removeprefix("Key_").lower()
                parts.append(seq_str)

        combo_str = "+".join(parts)
        if combo_str:
            self._finish_capture(combo_str)

    def _finish_capture(self, combo: str) -> None:
        self._capturing = False
        self.setText(combo)
        self.hotkey_captured.emit(combo)


class MicLevelTestWorker(QThread):
    """Фоновий потік короткого тесту мікрофона з вимірюванням пікового рівня сигналу (0..100%)."""

    level_updated = Signal(int, str)

    def __init__(
        self,
        device_index: int | None = None,
        tester_fn: Callable[[int | None], tuple[int, str]] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.device_index = device_index
        self._tester_fn = tester_fn

    def run(self) -> None:
        if self._tester_fn is not None:
            lvl, msg = self._tester_fn(self.device_index)
            self.level_updated.emit(lvl, msg)
            return

        try:
            import numpy as np
            import sounddevice as sd

            frames = sd.rec(
                int(0.6 * 16000),
                samplerate=16000,
                channels=1,
                dtype="int16",
                device=self.device_index,
            )
            sd.wait()
            peak = int(np.max(np.abs(frames))) if frames.size else 0
            percent = min(100, max(5, int((peak / 12000.0) * 100))) if peak > 30 else 3
            self.level_updated.emit(
                percent,
                f"Тест завершено успішно (пікова амплітуда: {peak}, рівень ~{percent}%).",
            )
        except Exception as exc:
            self.level_updated.emit(0, f"Не вдалося записати тестовий сигнал: {exc}")


class VoicePage(BasePage):
    """Налаштування гарячих клавіш, мікрофона та STT-бекенда."""

    def __init__(
        self,
        config_store: ConfigStore | None = None,
        on_voice_changed: Callable[[], None] | None = None,
        mic_tester_fn: Callable[[int | None], tuple[int, str]] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(
            page_id="voice",
            title="05 // ГОЛОС І ГАРЯЧІ КЛАВІШІ",
            subtitle="Захоплення клавіш із перевіркою конфліктів, вибір мікрофона, індикатор сигналу та STT-бекенд.",
            parent=parent,
        )
        self.config_store = config_store or ConfigStore()
        self._on_voice_changed = on_voice_changed
        self._mic_tester_fn = mic_tester_fn
        self._mic_worker: MicLevelTestWorker | None = None

        cfg = self.config_store.load_merged()

        # --- Картка 1: Гарячі клавіші ---
        hk_card, hk_layout = make_cyber_card(
            "ГЛОБАЛЬНІ ГАРЯЧІ КЛАВІШІ ([HOTKEYS])",
            "Клікніть у поле та натисніть клавішу або комбінацію (або введіть назву вручну).",
        )

        self.input_hk_voice = HotkeyCaptureEdit(cfg.hotkeys.voice)
        self.input_hk_docs = HotkeyCaptureEdit(cfg.hotkeys.explain_clipboard)
        self.input_hk_gesture = HotkeyCaptureEdit(cfg.hotkeys.gesture)
        self.input_hk_cancel = HotkeyCaptureEdit(cfg.hotkeys.cancel)

        for edit in (
            self.input_hk_voice,
            self.input_hk_docs,
            self.input_hk_gesture,
            self.input_hk_cancel,
        ):
            edit.hotkey_captured.connect(lambda _v: self.validate_hotkeys_ui())
            edit.textChanged.connect(lambda _v: self.validate_hotkeys_ui())

        hk_rows = [
            ("Утримання для голосу (voice):", self.input_hk_voice),
            ("Помилка з буфера → Документація (explain_clipboard):", self.input_hk_docs),
            ("Режим жестів камери (gesture):", self.input_hk_gesture),
            ("Скасування дії / запису (cancel):", self.input_hk_cancel),
        ]
        for label_text, widget in hk_rows:
            r = QHBoxLayout()
            lbl = QLabel(label_text)
            lbl.setMinimumWidth(340)
            r.addWidget(lbl)
            r.addWidget(widget, stretch=1)
            hk_layout.addLayout(r)

        self.lbl_conflict_warning = QLabel("")
        self.lbl_conflict_warning.setWordWrap(True)
        hk_layout.addWidget(self.lbl_conflict_warning)
        self.root_layout.addWidget(hk_card)

        # --- Картка 2: Мікрофон та рівень сигналу ---
        mic_card, mic_layout = make_cyber_card(
            "МІКРОФОН ТА ТЕСТ ЗАПИСУ ([AUDIO])",
            "Оберіть вхідний аудіопристрій та перевірте рівень сигналу.",
        )
        mic_row = QHBoxLayout()
        self.combo_mic = QComboBox()
        for dev_idx, dev_name in list_winmm_input_devices():
            self.combo_mic.addItem(dev_name, dev_idx)
            if dev_idx == cfg.audio.device_index:
                self.combo_mic.setCurrentIndex(self.combo_mic.count() - 1)

        self.btn_test_mic = QPushButton("Тест запису")
        self.btn_test_mic.clicked.connect(self.run_mic_test)

        mic_row.addWidget(QLabel("Пристрій:"))
        mic_row.addWidget(self.combo_mic, stretch=1)
        mic_row.addWidget(self.btn_test_mic)
        mic_layout.addLayout(mic_row)

        level_row = QHBoxLayout()
        self.level_bar = QProgressBar()
        self.level_bar.setRange(0, 100)
        self.level_bar.setValue(0)
        level_row.addWidget(QLabel("Рівень сигналу:"))
        level_row.addWidget(self.level_bar, stretch=1)
        mic_layout.addLayout(level_row)

        self.lbl_mic_status = QLabel("Натисніть «Тест запису» для перевірки рівня сигналу мікрофона.")
        self.lbl_mic_status.setObjectName("MutedText")
        mic_layout.addWidget(self.lbl_mic_status)
        self.root_layout.addWidget(mic_card)

        # --- Картка 3: STT-бекенд, мова та ліміт тривалості ---
        stt_card, stt_layout = make_cyber_card(
            "РОЗПІЗНАВАННЯ МОВЛЕННЯ ([STT])",
            "Вибір двигуна STT (хмарний Gemini або локальний Faster-Whisper), мови та ліміту запису.",
        )
        stt_row = QHBoxLayout()
        self.combo_backend = QComboBox()
        self.combo_backend.addItem("Gemini Audio API (gemini)", "gemini")
        self.combo_backend.addItem("Faster-Whisper Офлайн (whisper_local)", "whisper_local")
        self.combo_backend.setCurrentIndex(0 if cfg.stt.backend == "gemini" else 1)

        self.combo_language = QComboBox()
        self.combo_language.addItem("Українська (uk)", "uk")
        self.combo_language.addItem("English (en)", "en")
        self.combo_language.setCurrentIndex(0 if cfg.stt.language == "uk" else 1)

        self.spin_max_seconds = QSpinBox()
        self.spin_max_seconds.setRange(1, 60)
        self.spin_max_seconds.setValue(cfg.audio.max_seconds)
        self.spin_max_seconds.setSuffix(" с")

        self.btn_save = QPushButton("Зберегти налаштування голосу")
        self.btn_save.setObjectName("PrimaryButton")
        self.btn_save.clicked.connect(self.save_voice_settings)

        stt_row.addWidget(QLabel("Бекенд:"))
        stt_row.addWidget(self.combo_backend)
        stt_row.addWidget(QLabel("Мова:"))
        stt_row.addWidget(self.combo_language)
        stt_row.addWidget(QLabel("Ліміт запису:"))
        stt_row.addWidget(self.spin_max_seconds)
        stt_row.addWidget(self.btn_save)
        stt_layout.addLayout(stt_row)

        self.lbl_save_status = QLabel("")
        self.lbl_save_status.setObjectName("MutedText")
        stt_layout.addWidget(self.lbl_save_status)

        self.root_layout.addWidget(stt_card)
        self.root_layout.addStretch()

        self.validate_hotkeys_ui()

    def current_hotkeys_dict(self) -> dict[str, str]:
        return {
            "voice": self.input_hk_voice.text().strip().lower(),
            "explain_clipboard": self.input_hk_docs.text().strip().lower(),
            "gesture": self.input_hk_gesture.text().strip().lower(),
            "cancel": self.input_hk_cancel.text().strip().lower(),
        }

    def validate_hotkeys_ui(self) -> list[str]:
        """Перевіряє конфлікти гарячих клавіш і оновлює попередження на сторінці."""
        conflicts = detect_hotkey_conflicts(self.current_hotkeys_dict())
        if conflicts:
            self.lbl_conflict_warning.setText("⚠ " + " | ".join(conflicts))
            self.lbl_conflict_warning.setStyleSheet(f"color: {CyberPalette.ERR_RED}; font-weight: bold;")
        else:
            self.lbl_conflict_warning.setText("● Конфліктів гарячих клавіш не виявлено.")
            self.lbl_conflict_warning.setStyleSheet(f"color: {CyberPalette.OK_GREEN};")
        return conflicts

    def run_mic_test(self) -> None:
        self.btn_test_mic.setEnabled(False)
        self.lbl_mic_status.setText("Запис тестового фрагмента (0.6 с)...")
        dev_idx = self.combo_mic.currentData()
        worker = MicLevelTestWorker(
            device_index=dev_idx,
            tester_fn=self._mic_tester_fn,
            parent=self,
        )
        worker.level_updated.connect(self._on_mic_test_done)
        self._mic_worker = worker
        worker.start()

    def _on_mic_test_done(self, level: int, msg: str) -> None:
        self.btn_test_mic.setEnabled(True)
        self.level_bar.setValue(level)
        self.lbl_mic_status.setText(msg)

    def save_voice_settings(self) -> bool:
        """Зберігає [hotkeys], [audio] та [stt] у config.local.toml."""
        conflicts = self.validate_hotkeys_ui()
        if any("Конфлікт:" in c or "порожня" in c for c in conflicts):
            self.lbl_save_status.setText("Виправте конфлікти гарячих клавіш перед збереженням.")
            self.lbl_save_status.setStyleSheet(f"color: {CyberPalette.ERR_RED}; font-weight: bold;")
            return False

        hk = self.current_hotkeys_dict()
        dev_idx = self.combo_mic.currentData()
        audio_patch: dict[str, int | None] = {"max_seconds": int(self.spin_max_seconds.value())}
        if dev_idx is not None:
            audio_patch["device_index"] = int(dev_idx)

        stt_patch = {
            "backend": str(self.combo_backend.currentData() or "gemini"),
            "language": str(self.combo_language.currentData() or "uk"),
        }

        self.config_store.save_local_overrides(
            {
                "hotkeys": hk,
                "audio": audio_patch,
                "stt": stt_patch,
            }
        )
        self.lbl_save_status.setText("● Налаштування голосу, мікрофона та гарячих клавіш збережено.")
        self.lbl_save_status.setStyleSheet(f"color: {CyberPalette.OK_GREEN}; font-weight: bold;")
        if self._on_voice_changed is not None:
            self._on_voice_changed()
        return True

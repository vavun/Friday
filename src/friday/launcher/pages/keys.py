"""Сторінка 02 // Ключі API (Gemini & Telegram) з маскуванням, тимчасовим показом та онлайн-перевіркою."""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QThread, QTimer, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from friday.launcher.pages import BasePage, make_cyber_card
from friday.launcher.services.env_store import EnvStore, mask_secret
from friday.launcher.services.key_check import KeyCheckResult, verify_gemini_api_key
from friday.launcher.theme import CyberPalette


class KeyCheckWorker(QThread):
    """Фоновий потік перевірки ключа Gemini API, щоб ніколи не блокувати GUI."""

    finished_check = Signal(object)  # KeyCheckResult

    def __init__(
        self,
        api_key: str,
        checker_fn: Callable[[str], KeyCheckResult] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._api_key = api_key
        self._checker_fn = checker_fn or verify_gemini_api_key

    def run(self) -> None:
        res = self._checker_fn(self._api_key)
        self.finished_check.emit(res)


class KeysPage(BasePage):
    """Керування секретними ключами (.env): GEMINI_API_KEY та TELEGRAM_API_ID / HASH."""

    def __init__(
        self,
        env_store: EnvStore | None = None,
        on_keys_saved: Callable[[], None] | None = None,
        checker_fn: Callable[[str], KeyCheckResult] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(
            page_id="keys",
            title="02 // КЛЮЧІ API (.ENV)",
            subtitle=(
                "Ключі записуються виключно у файл .env в корені проєкту і ніколи не потрапляють у логи чи Git. "
                "Після збереження відображаються лише останні 4 символи."
            ),
            parent=parent,
        )
        self.env_store = env_store or EnvStore()
        self._on_keys_saved = on_keys_saved
        self._checker_fn = checker_fn
        self._worker: KeyCheckWorker | None = None

        # --- Картка 1: Gemini API Key ---
        gemini_card, g_layout = make_cyber_card(
            "GEMINI API KEY (ОБОВ'ЯЗКОВО ДЛЯ STT / SMART ROUTER)",
            "Використовується для розпізнавання українського мовлення та швидкого JSON-роутера.",
        )

        self.lbl_gemini_saved = QLabel()
        self.lbl_gemini_saved.setObjectName("MutedText")
        g_layout.addWidget(self.lbl_gemini_saved)

        input_row = QHBoxLayout()
        self.input_gemini = QLineEdit()
        self.input_gemini.setPlaceholderText("Вставте новий GEMINI_API_KEY (AIza...)")
        self.input_gemini.setEchoMode(QLineEdit.EchoMode.Password)

        self.btn_reveal = QPushButton("Показати (3 с)")
        self.btn_reveal.clicked.connect(self._reveal_temporarily)

        self.btn_save_gemini = QPushButton("Зберегти у .env")
        self.btn_save_gemini.setObjectName("PrimaryButton")
        self.btn_save_gemini.clicked.connect(self.save_gemini_key)

        self.btn_check_gemini = QPushButton("Перевірити ключ")
        self.btn_check_gemini.clicked.connect(self.check_gemini_key)

        input_row.addWidget(self.input_gemini, stretch=1)
        input_row.addWidget(self.btn_reveal)
        input_row.addWidget(self.btn_save_gemini)
        input_row.addWidget(self.btn_check_gemini)
        g_layout.addLayout(input_row)

        self.lbl_gemini_status = QLabel("Статус перевірки: очікування")
        self.lbl_gemini_status.setObjectName("MutedText")
        g_layout.addWidget(self.lbl_gemini_status)

        self.root_layout.addWidget(gemini_card)

        # --- Картка 2: Telegram API (Необов'язково) ---
        tg_card, tg_layout = make_cyber_card(
            "TELEGRAM API (НЕОБОВ'ЯЗКОВО — ЕТАП 7)",
            "Потрібно лише для голосової відправки повідомлень через Telethon (my.telegram.org).",
        )
        self.lbl_tg_saved = QLabel()
        self.lbl_tg_saved.setObjectName("MutedText")
        tg_layout.addWidget(self.lbl_tg_saved)

        tg_row = QHBoxLayout()
        self.input_tg_id = QLineEdit()
        self.input_tg_id.setPlaceholderText("TELEGRAM_API_ID")
        self.input_tg_hash = QLineEdit()
        self.input_tg_hash.setPlaceholderText("TELEGRAM_API_HASH")
        self.input_tg_hash.setEchoMode(QLineEdit.EchoMode.Password)

        self.btn_save_tg = QPushButton("Зберегти Telegram")
        self.btn_save_tg.clicked.connect(self.save_telegram_keys)

        tg_row.addWidget(self.input_tg_id)
        tg_row.addWidget(self.input_tg_hash, stretch=1)
        tg_row.addWidget(self.btn_save_tg)
        tg_layout.addLayout(tg_row)

        self.root_layout.addWidget(tg_card)
        self.root_layout.addStretch()

        self.refresh_masked_state()

    def refresh_masked_state(self) -> None:
        """Оновлює підписи збережених ключів (показує лише останні 4 символи)."""
        g_masked = self.env_store.get_masked("GEMINI_API_KEY")
        if g_masked:
            self.lbl_gemini_saved.setText(f"Збережений у .env ключ: {g_masked}")
        else:
            self.lbl_gemini_saved.setText("Ключ GEMINI_API_KEY ще не збережено у .env")

        tg_id_masked = self.env_store.get_masked("TELEGRAM_API_ID")
        tg_hash_masked = self.env_store.get_masked("TELEGRAM_API_HASH")
        if tg_id_masked or tg_hash_masked:
            self.lbl_tg_saved.setText(
                f"Збережено: API_ID={tg_id_masked or '—'}, API_HASH={tg_hash_masked or '—'}"
            )
        else:
            self.lbl_tg_saved.setText("Ключі Telegram не налаштовані (необов'язково).")

    def _reveal_temporarily(self) -> None:
        """Тимчасово показує введений у поле ключ на 3 секунди і знову ховає за зірочками."""
        self.input_gemini.setEchoMode(QLineEdit.EchoMode.Normal)
        QTimer.singleShot(
            3000,
            lambda: self.input_gemini.setEchoMode(QLineEdit.EchoMode.Password),
        )

    def save_gemini_key(self) -> None:
        """Зберігає GEMINI_API_KEY у .env, очищує поле вводу і залишає лише маску останніх 4 символів."""
        raw_key = self.input_gemini.text().strip()
        if not raw_key:
            self._set_status_text("Введіть непорожній ключ перед збереженням.", ok=False)
            return

        added_gitignore = self.env_store.update_keys({"GEMINI_API_KEY": raw_key})
        self.input_gemini.clear()
        self.input_gemini.setEchoMode(QLineEdit.EchoMode.Password)
        self.refresh_masked_state()

        msg = f"Ключ збережено у .env ({mask_secret(raw_key)})."
        if added_gitignore:
            msg += " УВАГА: файл .env було автоматично додано у .gitignore!"
        self._set_status_text(msg, ok=True)
        if self._on_keys_saved is not None:
            self._on_keys_saved()

    def save_telegram_keys(self) -> None:
        updates: dict[str, str] = {}
        if self.input_tg_id.text().strip():
            updates["TELEGRAM_API_ID"] = self.input_tg_id.text().strip()
        if self.input_tg_hash.text().strip():
            updates["TELEGRAM_API_HASH"] = self.input_tg_hash.text().strip()
        if not updates:
            return
        self.env_store.update_keys(updates)
        self.input_tg_id.clear()
        self.input_tg_hash.clear()
        self.refresh_masked_state()
        if self._on_keys_saved is not None:
            self._on_keys_saved()

    def check_gemini_key(self) -> None:
        """Запускає перевірку введеного або збереженого у .env ключа в окремому потоці."""
        candidate = self.input_gemini.text().strip() or self.env_store.get_key("GEMINI_API_KEY")
        self.btn_check_gemini.setEnabled(False)
        self._set_status_text("Перевірка з'єднання з Gemini API...", ok=None)

        worker = KeyCheckWorker(candidate, checker_fn=self._checker_fn, parent=self)
        worker.finished_check.connect(self._on_check_finished)
        self._worker = worker
        worker.start()

    def _on_check_finished(self, result: KeyCheckResult) -> None:
        self.btn_check_gemini.setEnabled(True)
        self._set_status_text(result.message, ok=result.ok)

    def _set_status_text(self, message: str, ok: bool | None) -> None:
        self.lbl_gemini_status.setText(message)
        if ok is True:
            self.lbl_gemini_status.setStyleSheet(f"color: {CyberPalette.OK_GREEN}; font-weight: bold;")
        elif ok is False:
            self.lbl_gemini_status.setStyleSheet(f"color: {CyberPalette.ERR_RED}; font-weight: bold;")
        else:
            self.lbl_gemini_status.setStyleSheet(f"color: {CyberPalette.CYAN};")

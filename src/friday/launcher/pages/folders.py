"""Сторінка 03 // Дозволені папки ([paths].allowed_folders) з валідацією path_rules та живим статусом."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from friday.config import AllowedFolderEntry
from friday.launcher.pages import BasePage, make_cyber_card
from friday.launcher.services.config_store import ConfigStore
from friday.launcher.services.path_rules import check_folder_status, validate_folder_path
from friday.launcher.theme import CyberPalette


class FoldersPage(BasePage):
    """Керування білим списком папок із перевіркою безпеки шляхів (path_rules)."""

    def __init__(
        self,
        config_store: ConfigStore | None = None,
        on_folders_changed: Callable[[], None] | None = None,
        home_dir: Path | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(
            page_id="folders",
            title="03 // ДОЗВОЛЕНІ ПАПКИ",
            subtitle=(
                "Файлові навички FRIDAY мають доступ виключно до папок із цього білого списку. "
                "Корінь диска, системні каталоги Windows та вся домашня папка заборонені."
            ),
            parent=parent,
        )
        self.config_store = config_store or ConfigStore()
        self._on_folders_changed = on_folders_changed
        self._home_dir = home_dir

        # --- Картка 1: Додавання нової папки ---
        add_card, add_layout = make_cyber_card(
            "ДОДАТИ ПАПКУ ДО БІЛОГО СПИСКУ",
            "Вкажіть зрозумілу мітку, оберіть директорію та режим доступу (лише читання або читання і запис).",
        )

        form_row = QHBoxLayout()
        self.input_label = QLineEdit()
        self.input_label.setPlaceholderText("Мітка (наприклад: Проєкт Friday)")
        self.input_label.setMinimumWidth(180)

        self.input_path = QLineEdit()
        self.input_path.setPlaceholderText("Шлях до папки (C:\\Projects\\MyRepo)")

        self.btn_browse = QPushButton("Обрати папку...")
        self.btn_browse.clicked.connect(self._browse_folder)

        self.combo_mode = QComboBox()
        self.combo_mode.addItem("Лише читання (read_only)", "read_only")
        self.combo_mode.addItem("Читання і запис (read_write)", "read_write")

        self.btn_add = QPushButton("Додати папку")
        self.btn_add.setObjectName("PrimaryButton")
        self.btn_add.clicked.connect(self.add_folder_from_inputs)

        form_row.addWidget(self.input_label)
        form_row.addWidget(self.input_path, stretch=1)
        form_row.addWidget(self.btn_browse)
        form_row.addWidget(self.combo_mode)
        form_row.addWidget(self.btn_add)
        add_layout.addLayout(form_row)

        self.lbl_feedback = QLabel("")
        self.lbl_feedback.setWordWrap(True)
        self.lbl_feedback.setObjectName("MutedText")
        add_layout.addWidget(self.lbl_feedback)

        self.root_layout.addWidget(add_card)

        # --- Картка 2: Список дозволених папок ---
        list_card, list_layout = make_cyber_card(
            "БІЛИЙ СПИСОК ДИРЕКТОРІЙ ([PATHS] ALLOWED_FOLDERS)",
            "Кожна папка перевіряється на існування на диску в реальному часі.",
        )
        self.rows_container = QWidget()
        self.rows_layout = QVBoxLayout(self.rows_container)
        self.rows_layout.setContentsMargins(0, 4, 0, 0)
        self.rows_layout.setSpacing(8)
        list_layout.addWidget(self.rows_container)

        self.root_layout.addWidget(list_card)
        self.root_layout.addStretch()

        self.refresh_folders_list()

    def _browse_folder(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "Оберіть папку для білого списку FRIDAY")
        if chosen:
            self.input_path.setText(chosen)
            if not self.input_label.text().strip():
                self.input_label.setText(Path(chosen).name or "Папка")

    def add_folder_from_inputs(self) -> bool:
        """Валідує введений шлях через path_rules та зберігає у config.local.toml."""
        raw_path = self.input_path.text().strip()
        raw_label = self.input_label.text().strip()
        mode_val = str(self.combo_mode.currentData() or "read_only")

        validation = validate_folder_path(raw_path, home_dir=self._home_dir, require_exists=True)
        if not validation.ok:
            self._set_feedback(validation.error, kind="error")
            return False

        label = raw_label or Path(validation.normalized_path).name or "Папка"
        cfg = self.config_store.load_merged()
        entries: list[dict[str, str]] = []
        updated_existing = False

        for existing in cfg.paths.allowed_folders:
            if existing.path.lower() == validation.normalized_path.lower():
                entries.append(
                    {
                        "label": label,
                        "path": validation.normalized_path,
                        "mode": mode_val,
                    }
                )
                updated_existing = True
            else:
                entries.append(existing.model_dump())

        if not updated_existing:
            entries.append(
                {
                    "label": label,
                    "path": validation.normalized_path,
                    "mode": mode_val,
                }
            )

        self.config_store.save_local_overrides({"paths": {"allowed_folders": entries}})
        self.input_label.clear()
        self.input_path.clear()
        self.refresh_folders_list()

        msg = f"Папку «{label}» ({validation.normalized_path}) збережено у config.local.toml."
        if validation.warning:
            self._set_feedback(f"{msg} {validation.warning}", kind="warning")
        else:
            self._set_feedback(msg, kind="ok")

        if self._on_folders_changed is not None:
            self._on_folders_changed()
        return True

    def remove_folder(self, target_path: str) -> None:
        """Видаляє папку зі списку allowed_folders і зберігає зміни."""
        cfg = self.config_store.load_merged()
        kept = [
            entry.model_dump()
            for entry in cfg.paths.allowed_folders
            if entry.path.lower() != target_path.lower()
        ]
        self.config_store.save_local_overrides({"paths": {"allowed_folders": kept}})
        self.refresh_folders_list()
        self._set_feedback(f"Папку видалено зі списку: {target_path}", kind="info")
        if self._on_folders_changed is not None:
            self._on_folders_changed()

    def refresh_folders_list(self) -> None:
        """Перебудовує список дозволених папок зі статусом (Існує / Недоступна)."""
        while self.rows_layout.count():
            child = self.rows_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

        cfg = self.config_store.load_merged()
        folders: list[AllowedFolderEntry] = cfg.paths.allowed_folders

        if not folders:
            empty_lbl = QLabel("Список порожній. Додайте хоча б одну робочу папку проєкту вище.")
            empty_lbl.setObjectName("MutedText")
            self.rows_layout.addWidget(empty_lbl)
            return

        for entry in folders:
            row_frame = QFrame()
            row_layout = QHBoxLayout(row_frame)
            row_layout.setContentsMargins(8, 6, 8, 6)

            exists, status_text = check_folder_status(entry.path)
            status_lbl = QLabel(f"● {status_text}" if exists else f"▲ {status_text}")
            status_color = CyberPalette.OK_GREEN if exists else CyberPalette.ERR_RED
            status_lbl.setStyleSheet(f"color: {status_color}; font-weight: bold; min-width: 110px;")

            label_lbl = QLabel(entry.label)
            label_lbl.setStyleSheet("font-weight: bold; min-width: 150px;")

            path_lbl = QLabel(entry.path)
            path_lbl.setObjectName("MutedText")

            mode_text = "ЧИТАННЯ + ЗАПИС" if entry.mode == "read_write" else "ЛИШЕ ЧИТАННЯ"
            mode_color = CyberPalette.CYAN if entry.mode == "read_write" else CyberPalette.TEXT_MUTED
            mode_lbl = QLabel(mode_text)
            mode_lbl.setStyleSheet(f"color: {mode_color}; font-weight: 600; min-width: 135px;")

            btn_del = QPushButton("Видалити")
            btn_del.setObjectName("DangerButton")
            btn_del.clicked.connect(
                lambda _checked=False, p=entry.path: self.remove_folder(p)
            )

            row_layout.addWidget(status_lbl)
            row_layout.addWidget(label_lbl)
            row_layout.addWidget(path_lbl, stretch=1)
            row_layout.addWidget(mode_lbl)
            row_layout.addWidget(btn_del)
            self.rows_layout.addWidget(row_frame)

    def _set_feedback(self, message: str, kind: str = "info") -> None:
        self.lbl_feedback.setText(message)
        if kind == "ok":
            self.lbl_feedback.setStyleSheet(f"color: {CyberPalette.OK_GREEN}; font-weight: bold;")
        elif kind == "error":
            self.lbl_feedback.setStyleSheet(f"color: {CyberPalette.ERR_RED}; font-weight: bold;")
        elif kind == "warning":
            self.lbl_feedback.setStyleSheet(f"color: {CyberPalette.CYAN}; font-weight: bold;")
        else:
            self.lbl_feedback.setStyleSheet(f"color: {CyberPalette.TEXT_MUTED};")

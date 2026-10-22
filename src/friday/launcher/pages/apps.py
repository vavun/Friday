"""Сторінка 04 // Програми ([apps] білий список + синоніми + сканер меню Пуск .lnk із чекбоксами)."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from friday.launcher.pages import BasePage, make_cyber_card
from friday.launcher.services.app_scanner import (
    ScannedApp,
    normalize_voice_name,
    scan_start_menu_apps,
    split_command_and_args,
    validate_app_executable,
)
from friday.launcher.services.config_store import ConfigStore
from friday.launcher.theme import CyberPalette
from friday.skills.apps import APP_NAME_SYNONYMS


class StartMenuScanWorker(QThread):
    """Фоновий потік сканування ярликів .lnk у меню Пуск без блокування GUI."""

    finished_scan = Signal(object)  # list[ScannedApp]

    def __init__(
        self,
        scanner_fn: Callable[[], list[ScannedApp]] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._scanner_fn = scanner_fn or scan_start_menu_apps

    def run(self) -> None:
        items = self._scanner_fn()
        self.finished_scan.emit(items)


class AppsPage(BasePage):
    """Керування білим списком програм [apps], синонімами [app_synonyms] та сканером меню Пуск."""

    def __init__(
        self,
        config_store: ConfigStore | None = None,
        on_apps_changed: Callable[[], None] | None = None,
        scanner_fn: Callable[[], list[ScannedApp]] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(
            page_id="apps",
            title="04 // ПРОГРАМИ (БІЛИЙ СПИСОК)",
            subtitle=(
                "FRIDAY запускає виключно програми з білого списку [apps] без shell=True. "
                "Додавайте програми вручну або скануйте ярлики меню Пуск (.lnk)."
            ),
            parent=parent,
        )
        self.config_store = config_store or ConfigStore()
        self._on_apps_changed = on_apps_changed
        self._scanner_fn = scanner_fn
        self._scan_worker: StartMenuScanWorker | None = None
        self.scan_checkboxes: list[tuple[QCheckBox, ScannedApp]] = []

        # --- Картка 1: Ручне додавання ---
        manual_card, manual_layout = make_cyber_card(
            "РУЧНЕ ДОДАВАННЯ ПРОГРАМИ ТА ГОЛОСОВИХ СИНОНІМІВ",
            "Вкажіть основну голосову назву, синоніми через кому (наприклад: телеграм, тг), шлях до .exe/.lnk та аргументи.",
        )

        row1 = QHBoxLayout()
        self.input_voice_name = QLineEdit()
        self.input_voice_name.setPlaceholderText("Голосова назва (напр. telegram)")
        self.input_synonyms = QLineEdit()
        self.input_synonyms.setPlaceholderText("Синоніми через кому (напр. телеграм, тг)")
        row1.addWidget(self.input_voice_name, stretch=1)
        row1.addWidget(self.input_synonyms, stretch=1)
        manual_layout.addLayout(row1)

        row2 = QHBoxLayout()
        self.input_exe_path = QLineEdit()
        self.input_exe_path.setPlaceholderText("Шлях до виконуваного файлу (.exe або .lnk)")
        self.btn_browse_exe = QPushButton("Обрати файл...")
        self.btn_browse_exe.clicked.connect(self._browse_executable)

        self.input_args = QLineEdit()
        self.input_args.setPlaceholderText("Необов'язкові аргументи (напр. --incognito)")

        self.btn_add_app = QPushButton("Додати у [apps]")
        self.btn_add_app.setObjectName("PrimaryButton")
        self.btn_add_app.clicked.connect(self.add_app_from_inputs)

        row2.addWidget(self.input_exe_path, stretch=2)
        row2.addWidget(self.btn_browse_exe)
        row2.addWidget(self.input_args, stretch=1)
        row2.addWidget(self.btn_add_app)
        manual_layout.addLayout(row2)

        self.lbl_feedback = QLabel("")
        self.lbl_feedback.setWordWrap(True)
        self.lbl_feedback.setObjectName("MutedText")
        manual_layout.addWidget(self.lbl_feedback)

        self.root_layout.addWidget(manual_card)

        # --- Картка 2: Сканер меню Пуск (.lnk) ---
        scan_card, scan_layout = make_cyber_card(
            "СКАНЕР МЕНЮ ПУСК (.LNK)",
            "Автоматичний розбір ярликів меню Пуск Windows із можливістю обрати потрібні програми чекбоксами.",
        )
        scan_btn_row = QHBoxLayout()
        self.btn_scan = QPushButton("Сканувати меню Пуск")
        self.btn_scan.clicked.connect(self.start_scan)

        self.btn_import_checked = QPushButton("Додати вибрані програми")
        self.btn_import_checked.setObjectName("PrimaryButton")
        self.btn_import_checked.setEnabled(False)
        self.btn_import_checked.clicked.connect(self.import_checked_scanned_apps)

        self.lbl_scan_status = QLabel("Натисніть «Сканувати меню Пуск», щоб знайти встановлені програми.")
        self.lbl_scan_status.setObjectName("MutedText")

        scan_btn_row.addWidget(self.btn_scan)
        scan_btn_row.addWidget(self.btn_import_checked)
        scan_btn_row.addWidget(self.lbl_scan_status, stretch=1)
        scan_layout.addLayout(scan_btn_row)

        self.scan_results_container = QWidget()
        self.scan_results_layout = QVBoxLayout(self.scan_results_container)
        self.scan_results_layout.setContentsMargins(0, 4, 0, 0)
        self.scan_results_layout.setSpacing(4)
        scan_layout.addWidget(self.scan_results_container)

        self.root_layout.addWidget(scan_card)

        # --- Картка 3: Поточний білий список [apps] ---
        list_card, list_layout = make_cyber_card(
            "БІЛИЙ СПИСОК ДОЗВОЛЕНИХ ПРОГРАМ ([APPS])",
            "Зміни зберігаються у config.local.toml без модифікації базового config.toml.",
        )
        self.apps_rows_container = QWidget()
        self.apps_rows_layout = QVBoxLayout(self.apps_rows_container)
        self.apps_rows_layout.setContentsMargins(0, 4, 0, 0)
        self.apps_rows_layout.setSpacing(8)
        list_layout.addWidget(self.apps_rows_container)

        self.root_layout.addWidget(list_card)
        self.root_layout.addStretch()

        self.refresh_apps_list()

    def _browse_executable(self) -> None:
        chosen, _ = QFileDialog.getOpenFileName(
            self,
            "Оберіть виконуваний файл програми або ярлик",
            "",
            "Виконувані файли (*.exe *.lnk *.bat *.cmd *.com)",
        )
        if chosen:
            self.input_exe_path.setText(chosen)
            if not self.input_voice_name.text().strip():
                self.input_voice_name.setText(normalize_voice_name(Path(chosen).stem))

    def add_app_from_inputs(self) -> bool:
        """Валідує шлях до виконуваного файлу та зберігає програму й синоніми у config.local.toml."""
        raw_voice = self.input_voice_name.text().strip()
        voice_key = normalize_voice_name(raw_voice) if raw_voice else ""
        if not voice_key:
            self._set_feedback("Введіть голосову назву програми (наприклад: telegram).", kind="error")
            return False

        ok, formatted_cmd, err = validate_app_executable(
            self.input_exe_path.text(),
            self.input_args.text(),
            require_exists=True,
        )
        if not ok:
            self._set_feedback(err, kind="error")
            return False

        cfg = self.config_store.load_merged()
        apps_dict = dict(cfg.apps)
        apps_dict[voice_key] = formatted_cmd

        synonyms_dict = dict(cfg.app_synonyms)
        raw_syns = self.input_synonyms.text().strip()
        if raw_syns:
            for token in raw_syns.split(","):
                syn = token.strip().lower()
                if syn:
                    synonyms_dict[syn] = voice_key

        self.config_store.save_local_overrides(
            {
                "apps": apps_dict,
                "app_synonyms": synonyms_dict,
            }
        )

        self.input_voice_name.clear()
        self.input_synonyms.clear()
        self.input_exe_path.clear()
        self.input_args.clear()
        self.refresh_apps_list()
        self._set_feedback(f"Програму «{voice_key}» збережено у [apps].", kind="ok")
        if self._on_apps_changed is not None:
            self._on_apps_changed()
        return True

    def start_scan(self) -> None:
        """Запускає сканування меню Пуск у фоновому потоці."""
        self.btn_scan.setEnabled(False)
        self.lbl_scan_status.setText("Сканування ярликів меню Пуск...")
        worker = StartMenuScanWorker(scanner_fn=self._scanner_fn, parent=self)
        worker.finished_scan.connect(self._on_scan_finished)
        self._scan_worker = worker
        worker.start()

    def _on_scan_finished(self, items: list[ScannedApp]) -> None:
        self.btn_scan.setEnabled(True)
        self.populate_scan_results(items)

    def populate_scan_results(self, items: list[ScannedApp]) -> None:
        """Відображає знайдені у меню Пуск програми з чекбоксами."""
        while self.scan_results_layout.count():
            child = self.scan_results_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()
        self.scan_checkboxes.clear()

        if not items:
            self.lbl_scan_status.setText("Нових ярликів .lnk у меню Пуск не знайдено.")
            self.btn_import_checked.setEnabled(False)
            return

        self.lbl_scan_status.setText(f"Знайдено програм у меню Пуск: {len(items)}. Позначте потрібні:")
        for app_item in items[:35]:
            chk = QCheckBox(f"{app_item.display_name}  [{app_item.voice_name}]  →  {app_item.exe_path}")
            self.scan_checkboxes.append((chk, app_item))
            self.scan_results_layout.addWidget(chk)

        self.btn_import_checked.setEnabled(True)

    def import_checked_scanned_apps(self) -> int:
        """Додає всі відмічені чекбоксами програми зі сканера у [apps]."""
        selected = [app_item for chk, app_item in self.scan_checkboxes if chk.isChecked()]
        if not selected:
            self._set_feedback("Позначте хоча б одну програму у списку сканування.", kind="error")
            return 0

        cfg = self.config_store.load_merged()
        apps_dict = dict(cfg.apps)
        added = 0
        for item in selected:
            ok, formatted_cmd, _ = validate_app_executable(
                item.exe_path,
                item.arguments,
                require_exists=False,
            )
            if ok:
                apps_dict[item.voice_name] = formatted_cmd
                added += 1

        self.config_store.save_local_overrides({"apps": apps_dict})
        self.refresh_apps_list()
        self._set_feedback(f"Імпортовано програм із меню Пуск: {added}.", kind="ok")
        if self._on_apps_changed is not None:
            self._on_apps_changed()
        return added

    def remove_app(self, app_key: str) -> None:
        """Видаляє програму та пов'язані з нею користувацькі синоніми з config.local.toml."""
        cfg = self.config_store.load_merged()
        base_dict = self.config_store.read_base_dict()
        base_apps = base_dict.get("apps", {}) if isinstance(base_dict.get("apps"), dict) else {}

        apps_dict = {k: v for k, v in cfg.apps.items() if k.lower() != app_key.lower()}
        for bk in base_apps:
            if bk.lower() == app_key.lower():
                apps_dict[bk] = ""

        syns_dict = {
            syn: target
            for syn, target in cfg.app_synonyms.items()
            if target.lower() != app_key.lower()
        }
        self.config_store.save_local_overrides({"apps": apps_dict, "app_synonyms": syns_dict})
        self.refresh_apps_list()
        self._set_feedback(f"Програму «{app_key}» видалено з білого списку.", kind="info")
        if self._on_apps_changed is not None:
            self._on_apps_changed()

    def refresh_apps_list(self) -> None:
        """Перебудовує список дозволених програм із показом синонімів та перевіркою шляху."""
        while self.apps_rows_layout.count():
            child = self.apps_rows_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

        cfg = self.config_store.load_merged()
        merged_synonyms: dict[str, str] = dict(APP_NAME_SYNONYMS)
        merged_synonyms.update(cfg.app_synonyms)

        if not cfg.apps:
            empty_lbl = QLabel("Білий список програм порожній.")
            empty_lbl.setObjectName("MutedText")
            self.apps_rows_layout.addWidget(empty_lbl)
            return

        for key, raw_cmd in sorted(cfg.apps.items()):
            row_frame = QFrame()
            row_layout = QHBoxLayout(row_frame)
            row_layout.setContentsMargins(8, 6, 8, 6)

            exe_part, args_part = split_command_and_args(raw_cmd)
            ok_exe, _, _ = validate_app_executable(exe_part, args_part, require_exists=True)

            badge = QLabel("● OK" if ok_exe else "▲ Не знайдено")
            badge_color = CyberPalette.OK_GREEN if ok_exe else CyberPalette.ERR_RED
            badge.setStyleSheet(f"color: {badge_color}; font-weight: bold; min-width: 105px;")

            syns_for_key = sorted(
                syn for syn, target in merged_synonyms.items() if target == key and syn != key
            )
            syn_suffix = f" ({', '.join(syns_for_key)})" if syns_for_key else ""
            name_lbl = QLabel(f"{key}{syn_suffix}")
            name_lbl.setStyleSheet("font-weight: bold; min-width: 190px;")

            cmd_lbl = QLabel(raw_cmd)
            cmd_lbl.setObjectName("MutedText")

            btn_del = QPushButton("Видалити")
            btn_del.setObjectName("DangerButton")
            btn_del.clicked.connect(lambda _checked=False, k=key: self.remove_app(k))

            row_layout.addWidget(badge)
            row_layout.addWidget(name_lbl)
            row_layout.addWidget(cmd_lbl, stretch=1)
            row_layout.addWidget(btn_del)
            self.apps_rows_layout.addWidget(row_frame)

    def _set_feedback(self, message: str, kind: str = "info") -> None:
        self.lbl_feedback.setText(message)
        if kind == "ok":
            self.lbl_feedback.setStyleSheet(f"color: {CyberPalette.OK_GREEN}; font-weight: bold;")
        elif kind == "error":
            self.lbl_feedback.setStyleSheet(f"color: {CyberPalette.ERR_RED}; font-weight: bold;")
        else:
            self.lbl_feedback.setStyleSheet(f"color: {CyberPalette.TEXT_MUTED};")

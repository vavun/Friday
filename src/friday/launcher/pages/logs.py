"""Сторінка 08 // Журнал подій (живий перегляд logs/friday.log із фільтром за рівнем та маскуванням секретів)."""

from __future__ import annotations

import os
from collections import deque
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QFileSystemWatcher
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QWidget,
)

from friday.config import DEFAULT_LOG_DIR
from friday.launcher.pages import BasePage, make_cyber_card
from friday.launcher.services.process_manager import sanitize_log_line


class LogsPage(BasePage):
    """Живий перегляд логів logs/friday.log та виводу дочірнього процесу з фільтром за рівнем."""

    def __init__(
        self,
        log_file_path: Path | None = None,
        secrets_provider: Callable[[], list[str]] | None = None,
        open_folder_fn: Callable[[Path], None] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(
            page_id="logs",
            title="08 // ЖУРНАЛ ПОДІЙ (LOGS)",
            subtitle=(
                "Живий перегляд logs/friday.log та потоку виводу ядра (шрифт JetBrains Mono). "
                "Ключі API та вміст буфера обміну автоматично маскуються."
            ),
            parent=parent,
            scrollable=False,
        )
        self.log_file_path = log_file_path or (DEFAULT_LOG_DIR / "friday.log")
        self._secrets_provider = secrets_provider or (lambda: [])
        self._open_folder_fn = open_folder_fn
        self._lines_buffer: deque[str] = deque(maxlen=400)

        card, layout = make_cyber_card(
            "СИСТЕМНИЙ ТЕРМІНАЛ ЛОГІВ",
            f"Файл журналу: {self.log_file_path}",
        )

        controls_row = QHBoxLayout()
        self.combo_level = QComboBox()
        for lvl_label, lvl_code in (
            ("Усі рівні (ALL)", "ALL"),
            (" лише INFO+", "INFO"),
            (" лише WARNING+", "WARNING"),
            (" лише ERROR", "ERROR"),
            (" DEBUG", "DEBUG"),
        ):
            self.combo_level.addItem(lvl_label, lvl_code)
        self.combo_level.currentIndexChanged.connect(lambda _idx: self.render_filtered_logs())

        self.btn_reload = QPushButton("Оновити з файлу")
        self.btn_reload.clicked.connect(self.reload_from_file)

        self.btn_open_dir = QPushButton("Відкрити папку логів")
        self.btn_open_dir.setObjectName("PrimaryButton")
        self.btn_open_dir.clicked.connect(self.open_logs_folder)

        controls_row.addWidget(QLabel("Фільтр рівня:"))
        controls_row.addWidget(self.combo_level)
        controls_row.addWidget(self.btn_reload)
        controls_row.addWidget(self.btn_open_dir)
        controls_row.addStretch()
        layout.addLayout(controls_row)

        self.log_viewer = QPlainTextEdit()
        self.log_viewer.setObjectName("LogViewer")
        self.log_viewer.setReadOnly(True)
        self.log_viewer.setMinimumHeight(340)
        layout.addWidget(self.log_viewer)

        self.root_layout.addWidget(card)

        self._watcher = QFileSystemWatcher(self)
        self._watcher.fileChanged.connect(lambda _p: self.reload_from_file())
        self.reload_from_file()

    def reload_from_file(self) -> None:
        """Читає останні рядки з logs/friday.log, маскує секрети та оновлює панель."""
        self.log_file_path.parent.mkdir(parents=True, exist_ok=True)
        if self.log_file_path.exists():
            if str(self.log_file_path) not in self._watcher.files():
                self._watcher.addPath(str(self.log_file_path))
            try:
                raw_lines = self.log_file_path.read_text(encoding="utf-8", errors="replace").splitlines()
            except Exception:
                raw_lines = []
            secrets = self._secrets_provider()
            self._lines_buffer.clear()
            for ln in raw_lines[-350:]:
                self._lines_buffer.append(sanitize_log_line(ln, secrets))
        self.render_filtered_logs()

    def append_live_line(self, line: str) -> None:
        """Додає новий рядок із stdout/stderr дочірнього процесу ядра."""
        secrets = self._secrets_provider()
        clean = sanitize_log_line(line, secrets)
        self._lines_buffer.append(clean)
        if self._matches_filter(clean):
            self.log_viewer.appendPlainText(clean)

    def _matches_filter(self, line: str) -> bool:
        selected = str(self.combo_level.currentData() or "ALL")
        if selected == "ALL":
            return True
        upper = line.upper()
        if selected == "ERROR":
            return "ERROR" in upper or "CRITICAL" in upper
        if selected == "WARNING":
            return any(tok in upper for tok in ("WARNING", "WARN", "ERROR", "CRITICAL"))
        if selected == "INFO":
            return any(tok in upper for tok in ("INFO", "WARNING", "WARN", "ERROR", "CRITICAL"))
        return selected in upper

    def render_filtered_logs(self) -> None:
        """Відображає у QPlainTextEdit лише рядки, що відповідають обраному фільтру рівня."""
        filtered = [ln for ln in self._lines_buffer if self._matches_filter(ln)]
        if not filtered:
            self.log_viewer.setPlainText("Логи за обраним фільтром відсутні.")
            return
        self.log_viewer.setPlainText("\n".join(filtered))
        scrollbar = self.log_viewer.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def open_logs_folder(self) -> Path:
        """Відкриває директорію logs/ у Провіднику Windows."""
        folder = self.log_file_path.parent.resolve()
        folder.mkdir(parents=True, exist_ok=True)
        if self._open_folder_fn is not None:
            self._open_folder_fn(folder)
        elif hasattr(os, "startfile"):
            try:
                os.startfile(str(folder))  # type: ignore[attr-defined]
            except Exception:
                pass
        return folder

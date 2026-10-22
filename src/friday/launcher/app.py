"""Головне вікно Friday Launcher, навігація між 9 сторінками, системний трей та single-instance lock."""

from __future__ import annotations

import argparse
import ctypes
from typing import Any

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QAction, QCloseEvent
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QPushButton,
    QStackedWidget,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from friday.launcher.i18n import tr
from friday.launcher.pages import BasePage
from friday.launcher.pages.apps import AppsPage
from friday.launcher.pages.dashboard import DashboardPage
from friday.launcher.pages.folders import FoldersPage
from friday.launcher.pages.keys import KeysPage
from friday.launcher.pages.logs import LogsPage
from friday.launcher.pages.monitors import MonitorsPage
from friday.launcher.pages.safety import SafetyPage
from friday.launcher.pages.settings import SettingsPage
from friday.launcher.pages.voice import VoicePage
from friday.launcher.services.config_store import ConfigStore
from friday.launcher.services.env_store import EnvStore
from friday.launcher.services.preflight import run_preflight_checks
from friday.launcher.services.process_manager import CoreProcessManager, CoreProcessStatus
from friday.launcher.theme import (
    build_cyberpunk_qss,
    create_neon_orb_icon,
    load_bundled_fonts,
)


class _ProcessSignalBridge(QObject):
    """Місток сигналів Qt для потокобезпечного оновлення GUI з потоків CoreProcessManager."""

    status_changed = Signal(str, str)
    log_received = Signal(str)


class SingleInstanceMutex:
    """Системний м'ютекс Windows для захисту від подвійного запуску лаунчера."""

    def __init__(self, name: str = "Local\\FridayLauncherSingleInstanceMutex") -> None:
        self.name = name
        self._handle: Any = None
        self.already_running: bool = False

    def acquire(self) -> bool:
        try:
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.CreateMutexW(None, False, self.name)
            last_err = kernel32.GetLastError()
            self._handle = handle
            # ERROR_ALREADY_EXISTS = 183
            self.already_running = last_err == 183
            return not self.already_running
        except Exception:
            return True

    def release(self) -> None:
        if self._handle:
            try:
                ctypes.windll.kernel32.CloseHandle(self._handle)
            except Exception:
                pass
            self._handle = None


class LauncherWindow(QMainWindow):
    """Головне неонове вікно Friday Launcher із лівою панеллю навігації та підтримкою трею."""

    PAGE_ORDER: tuple[tuple[str, str], ...] = (
        ("dashboard", "nav.dashboard"),
        ("keys", "nav.keys"),
        ("folders", "nav.folders"),
        ("apps", "nav.apps"),
        ("voice", "nav.voice"),
        ("monitors", "nav.monitors"),
        ("safety", "nav.safety"),
        ("logs", "nav.logs"),
        ("settings", "nav.settings"),
    )

    def __init__(
        self,
        config_store: ConfigStore | None = None,
        env_store: EnvStore | None = None,
        process_manager: CoreProcessManager | None = None,
        enable_tray: bool = True,
    ) -> None:
        super().__init__()
        self.config_store = config_store or ConfigStore()
        self.env_store = env_store or EnvStore(env_path=self.config_store.env_path)
        self.app_config = self.config_store.load_merged()
        self.lang = self.app_config.ui.language
        self._force_quit = False
        self._minimize_to_tray_on_close = False

        self._proc_bridge = _ProcessSignalBridge(self)
        self._proc_bridge.status_changed.connect(self._on_core_status_changed)

        if process_manager is not None:
            self.process_manager = process_manager
        else:
            self.process_manager = CoreProcessManager(
                on_status_change=lambda st, det: self._proc_bridge.status_changed.emit(str(st), det),
                on_log_line=lambda line: self._proc_bridge.log_received.emit(line),
            )

        self.setWindowTitle(tr("app.title", self.lang))
        self.setMinimumSize(940, 620)
        self.resize(1040, 680)
        self.setWindowIcon(create_neon_orb_icon(64, active=False))

        root = QWidget(self)
        root.setObjectName("RootContainer")
        self.setCentralWidget(root)

        main_layout = QHBoxLayout(root)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # Ліве меню навігації
        self.sidebar = QFrame(root)
        self.sidebar.setObjectName("Sidebar")
        self.sidebar.setFixedWidth(245)
        side_layout = QVBoxLayout(self.sidebar)
        side_layout.setContentsMargins(16, 20, 16, 16)
        side_layout.setSpacing(6)

        brand = QLabel("F.R.I.D.A.Y.")
        brand.setObjectName("BrandTitle")
        sub = QLabel(tr("app.subtitle", self.lang))
        sub.setObjectName("BrandSubtitle")
        sub.setWordWrap(True)
        side_layout.addWidget(brand)
        side_layout.addWidget(sub)
        side_layout.addSpacing(12)

        self.stack = QStackedWidget(root)
        self.pages: dict[str, BasePage] = {}
        self.nav_buttons: dict[str, QPushButton] = {}

        self._init_pages()

        for page_id, nav_key in self.PAGE_ORDER:
            btn = QPushButton(tr(nav_key, self.lang))
            btn.setObjectName("NavButton")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _checked=False, pid=page_id: self.switch_page(pid))
            self.nav_buttons[page_id] = btn
            side_layout.addWidget(btn)

        side_layout.addStretch()

        self.btn_hide_tray = QPushButton("ЗГОРНУТИ В ТРЕЙ")
        self.btn_hide_tray.clicked.connect(self.hide_to_tray)
        side_layout.addWidget(self.btn_hide_tray)

        main_layout.addWidget(self.sidebar)
        main_layout.addWidget(self.stack, stretch=1)

        self.tray_icon: QSystemTrayIcon | None = None
        self.act_tray_start: QAction | None = None
        self.act_tray_stop: QAction | None = None
        if enable_tray and QSystemTrayIcon.isSystemTrayAvailable():
            self._init_tray()

        self.refresh_dashboard_state()
        self.switch_page("dashboard")

    def _init_pages(self) -> None:
        self.dashboard_page = DashboardPage(
            on_navigate=self.switch_page,
            on_start_requested=self.start_core,
            on_stop_requested=self.stop_core,
            on_restart_requested=self.restart_core,
            parent=self,
        )
        self.keys_page = KeysPage(
            env_store=self.env_store,
            on_keys_saved=self.refresh_dashboard_state,
            parent=self,
        )
        self.folders_page = FoldersPage(parent=self)
        self.apps_page = AppsPage(parent=self)
        self.voice_page = VoicePage(parent=self)
        self.monitors_page = MonitorsPage(parent=self)
        self.safety_page = SafetyPage(parent=self)
        self.logs_page = LogsPage(parent=self)
        self.settings_page = SettingsPage(parent=self)

        all_pages: list[BasePage] = [
            self.dashboard_page,
            self.keys_page,
            self.folders_page,
            self.apps_page,
            self.voice_page,
            self.monitors_page,
            self.safety_page,
            self.logs_page,
            self.settings_page,
        ]
        for page in all_pages:
            self.pages[page.page_id] = page
            self.stack.addWidget(page)

    def _init_tray(self) -> None:
        tray = QSystemTrayIcon(create_neon_orb_icon(64, active=False), self)
        tray.setToolTip("FRIDAY Launcher")
        menu = QMenu()

        act_open = QAction(tr("tray.open", self.lang), self)
        act_open.triggered.connect(self.restore_from_tray)
        menu.addAction(act_open)

        self.act_tray_start = QAction(tr("tray.start", self.lang), self)
        self.act_tray_start.triggered.connect(self.start_core)
        menu.addAction(self.act_tray_start)

        self.act_tray_stop = QAction(tr("tray.stop", self.lang), self)
        self.act_tray_stop.triggered.connect(self.stop_core)
        self.act_tray_stop.setEnabled(False)
        menu.addAction(self.act_tray_stop)

        menu.addSeparator()
        act_quit = QAction(tr("tray.quit", self.lang), self)
        act_quit.triggered.connect(self.quit_application)
        menu.addAction(act_quit)

        tray.setContextMenu(menu)
        tray.activated.connect(self._on_tray_activated)
        tray.show()
        self.tray_icon = tray

    def refresh_dashboard_state(self) -> None:
        """Оновлює зведення конфігурації, Preflight-діагностику та список секретів для маскування логів."""
        self.app_config = self.config_store.load_merged()
        self.dashboard_page.update_summary(self.app_config)
        items = run_preflight_checks(self.app_config, self.env_store)
        self.dashboard_page.update_preflight(items)
        self.process_manager.set_redacted_secrets(
            [
                self.env_store.get_key("GEMINI_API_KEY"),
                self.env_store.get_key("TELEGRAM_API_HASH"),
            ]
        )

    def start_core(self) -> None:
        """Запускає дочірній процес ядра FRIDAY."""
        self.refresh_dashboard_state()
        self.process_manager.start()

    def stop_core(self) -> None:
        """Зупиняє дочірній процес ядра FRIDAY."""
        self.process_manager.stop()

    def restart_core(self) -> None:
        """Перезапускає дочірній процес ядра FRIDAY."""
        self.refresh_dashboard_state()
        self.process_manager.restart()

    def _on_core_status_changed(self, status: str, detail: str) -> None:
        self.dashboard_page.update_process_status(status, detail)
        is_active = status in {CoreProcessStatus.RUNNING, CoreProcessStatus.STARTING}
        self.setWindowIcon(create_neon_orb_icon(64, active=is_active))
        if self.tray_icon is not None:
            self.tray_icon.setIcon(create_neon_orb_icon(64, active=is_active))
        if self.act_tray_start is not None:
            self.act_tray_start.setEnabled(not is_active)
        if self.act_tray_stop is not None:
            self.act_tray_stop.setEnabled(is_active)

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self.restore_from_tray()

    def switch_page(self, page_id: str) -> None:
        """Перемикає активну сторінку в QStackedWidget і оновлює підсвічування кнопки в меню."""
        if page_id not in self.pages:
            raise KeyError(f"Сторінку '{page_id}' не знайдено")

        if page_id == "dashboard":
            self.refresh_dashboard_state()

        self.stack.setCurrentWidget(self.pages[page_id])
        for pid, btn in self.nav_buttons.items():
            btn.setProperty("active", "true" if pid == page_id else "false")
            btn.style().unpolish(btn)
            btn.style().polish(btn)

    @property
    def current_page_id(self) -> str:
        widget = self.stack.currentWidget()
        if isinstance(widget, BasePage):
            return widget.page_id
        return ""

    def hide_to_tray(self) -> None:
        """Приховує вікно лаунчера в трей (усі анімації автоматично зупиняються через hideEvent -> ~0% CPU)."""
        self.hide()

    def restore_from_tray(self) -> None:
        """Відновлює головне вікно з трею."""
        self.showNormal()
        self.activateWindow()
        self.raise_()

    def quit_application(self) -> None:
        """Повністю закриває лаунчер і гарантовано зупиняє дочірній процес ядра."""
        self._force_quit = True
        self.process_manager.stop(timeout_s=1.0)
        if self.tray_icon is not None:
            self.tray_icon.hide()
        self.close()

    def closeEvent(self, event: QCloseEvent) -> None:
        if not self._force_quit and self._minimize_to_tray_on_close and self.tray_icon is not None:
            event.ignore()
            self.hide_to_tray()
            return
        if self.tray_icon is not None:
            self.tray_icon.hide()
        super().closeEvent(event)


def create_launcher_app(argv: list[str] | None = None) -> tuple[QApplication, LauncherWindow]:
    """Ініціалізує QApplication, локальні техно-шрифти, QSS-тему та головне вікно."""
    app = QApplication.instance()
    if app is None:
        app = QApplication(argv or [])
    assert isinstance(app, QApplication)

    fonts = load_bundled_fonts()
    app.setStyleSheet(build_cyberpunk_qss(heading_font=fonts["heading"], mono_font=fonts["mono"]))

    window = LauncherWindow()
    return app, window


def run_launcher(argv: list[str] | None = None) -> int:
    """Точка запуску Friday Launcher з підтримкою прапорця --minimized та single-instance захисту."""
    parser = argparse.ArgumentParser(description="FRIDAY Neon Cyberpunk Launcher")
    parser.add_argument("--minimized", action="store_true", help="Запустити згорнутим у трей")
    args, qt_args = parser.parse_known_args(argv)

    mutex = SingleInstanceMutex()
    if not mutex.acquire():
        return 0

    try:
        app, window = create_launcher_app(qt_args)
        if args.minimized or window.app_config.ui.start_minimized:
            window.hide_to_tray()
        else:
            window.show()
        return app.exec()
    finally:
        mutex.release()

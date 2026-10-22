"""Головне вікно Friday Launcher, навігація між 9 сторінками, системний трей та single-instance lock."""

from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
from typing import TYPE_CHECKING, Any

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
from friday.launcher.pages.dashboard import DashboardPage
from friday.launcher.services.config_store import ConfigStore
from friday.launcher.services.env_store import EnvStore
from friday.launcher.services.preflight import run_preflight_checks
from friday.launcher.services.process_manager import CoreProcessManager, CoreProcessStatus
from friday.launcher.theme import (
    build_cyberpunk_qss,
    create_neon_orb_icon,
    load_bundled_fonts,
)

if TYPE_CHECKING:
    from friday.launcher.pages.apps import AppsPage
    from friday.launcher.pages.folders import FoldersPage
    from friday.launcher.pages.keys import KeysPage
    from friday.launcher.pages.logs import LogsPage
    from friday.launcher.pages.monitors import MonitorsPage
    from friday.launcher.pages.safety import SafetyPage
    from friday.launcher.pages.settings import SettingsPage
    from friday.launcher.pages.voice import VoicePage


class _ProcessSignalBridge(QObject):
    """Місток сигналів Qt для потокобезпечного оновлення GUI з потоків CoreProcessManager."""

    status_changed = Signal(str, str)
    log_received = Signal(str)


RESTORE_WINDOW_MSG_ID: int = 0
try:
    RESTORE_WINDOW_MSG_ID = int(
        ctypes.windll.user32.RegisterWindowMessageW("FridayLauncherRestoreWindowMessage")
    )
except Exception:
    RESTORE_WINDOW_MSG_ID = 0


class SingleInstanceMutex:
    """Системний м'ютекс Windows із автоматичним розгортанням існуючого вікна з трею при повторному запуску."""

    def __init__(self, name: str = "Local\\FridayLauncherSingleInstanceMutex") -> None:
        self.name = name
        self._handle: Any = None
        self.already_running: bool = False

    def acquire(self) -> bool:
        try:
            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            ctypes.set_last_error(0)
            handle = k32.CreateMutexW(None, False, self.name)
            last_err = ctypes.get_last_error()
            self._handle = handle
            # ERROR_ALREADY_EXISTS = 183
            self.already_running = last_err == 183
            if self.already_running and RESTORE_WINDOW_MSG_ID:
                # HWND_BROADCAST = 0xFFFF — миттєво будить згорнуте у трей вікно без навантаження на CPU
                ctypes.windll.user32.PostMessageW(0xFFFF, RESTORE_WINDOW_MSG_ID, 0, 0)
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

        self.switch_page("dashboard")

    def _init_pages(self) -> None:
        self._valid_page_ids: set[str] = {pid for pid, _ in self.PAGE_ORDER}
        self._live_log_buffer: list[str] = []
        self._proc_bridge.log_received.connect(self._on_process_log_line)

        self.dashboard_page = DashboardPage(
            on_navigate=self.switch_page,
            on_start_requested=self.start_core,
            on_stop_requested=self.stop_core,
            on_restart_requested=self.restart_core,
            parent=self,
        )
        self.pages["dashboard"] = self.dashboard_page
        self.stack.addWidget(self.dashboard_page)

    def _on_process_log_line(self, line: str) -> None:
        self._live_log_buffer.append(line)
        if len(self._live_log_buffer) > 300:
            self._live_log_buffer = self._live_log_buffer[-300:]
        page = self.pages.get("logs")
        if page is not None and hasattr(page, "append_live_line"):
            page.append_live_line(line)  # type: ignore[attr-defined]

    def _ensure_page(self, page_id: str) -> BasePage:
        """Ліниво ініціалізує сторінки лаунчера при першому переході (для холодного старту вікна < 1 с)."""
        if page_id not in self._valid_page_ids:
            raise KeyError(f"Сторінку '{page_id}' не знайдено")
        if page_id in self.pages:
            return self.pages[page_id]

        if page_id == "keys":
            from friday.launcher.pages.keys import KeysPage

            page: BasePage = KeysPage(
                env_store=self.env_store,
                on_keys_saved=self.refresh_dashboard_state,
                parent=self,
            )
        elif page_id == "folders":
            from friday.launcher.pages.folders import FoldersPage

            page = FoldersPage(
                config_store=self.config_store,
                on_folders_changed=self.refresh_dashboard_state,
                parent=self,
            )
        elif page_id == "apps":
            from friday.launcher.pages.apps import AppsPage

            page = AppsPage(
                config_store=self.config_store,
                on_apps_changed=self.refresh_dashboard_state,
                parent=self,
            )
        elif page_id == "voice":
            from friday.launcher.pages.voice import VoicePage

            page = VoicePage(
                config_store=self.config_store,
                on_voice_changed=self.refresh_dashboard_state,
                parent=self,
            )
        elif page_id == "monitors":
            from friday.launcher.pages.monitors import MonitorsPage

            page = MonitorsPage(
                config_store=self.config_store,
                on_monitors_changed=self.refresh_dashboard_state,
                parent=self,
            )
        elif page_id == "safety":
            from friday.launcher.pages.safety import SafetyPage

            page = SafetyPage(
                config_store=self.config_store,
                on_safety_changed=self.refresh_dashboard_state,
                parent=self,
            )
        elif page_id == "logs":
            from friday.launcher.pages.logs import LogsPage

            logs_pg = LogsPage(
                secrets_provider=lambda: [
                    self.env_store.get_key("GEMINI_API_KEY"),
                    self.env_store.get_key("TELEGRAM_API_HASH"),
                ],
                parent=self,
            )
            for ln in self._live_log_buffer:
                logs_pg.append_live_line(ln)
            page = logs_pg
        elif page_id == "settings":
            from friday.launcher.pages.settings import SettingsPage

            page = SettingsPage(
                config_store=self.config_store,
                on_settings_saved=self.refresh_dashboard_state,
                on_reduce_animations_changed=self._on_reduce_animations_changed,
                on_close_to_tray_changed=self._on_close_to_tray_changed,
                parent=self,
            )
        else:
            raise KeyError(f"Сторінку '{page_id}' не знайдено")

        self.pages[page_id] = page
        self.stack.addWidget(page)
        return page

    @property
    def keys_page(self) -> Any:
        return self._ensure_page("keys")

    @property
    def folders_page(self) -> Any:
        return self._ensure_page("folders")

    @property
    def apps_page(self) -> Any:
        return self._ensure_page("apps")

    @property
    def voice_page(self) -> Any:
        return self._ensure_page("voice")

    @property
    def monitors_page(self) -> Any:
        return self._ensure_page("monitors")

    @property
    def safety_page(self) -> Any:
        return self._ensure_page("safety")

    @property
    def logs_page(self) -> Any:
        return self._ensure_page("logs")

    @property
    def settings_page(self) -> Any:
        return self._ensure_page("settings")

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
        self.dashboard_page.orb.set_reduce_animations(self.app_config.ui.reduce_animations)
        items = run_preflight_checks(self.app_config, self.env_store)
        self.dashboard_page.update_preflight(items)
        secrets_list = [
            self.env_store.get_key("GEMINI_API_KEY"),
            self.env_store.get_key("TELEGRAM_API_HASH"),
        ]
        self.process_manager.set_redacted_secrets(secrets_list)

    def _on_reduce_animations_changed(self, reduce_anim: bool) -> None:
        self.dashboard_page.orb.set_reduce_animations(reduce_anim)

    def _on_close_to_tray_changed(self, enabled: bool) -> None:
        self._minimize_to_tray_on_close = bool(enabled)

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
        target_page = self._ensure_page(page_id)

        if page_id == "dashboard":
            self.refresh_dashboard_state()

        self.stack.setCurrentWidget(target_page)
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
        """Відновлює головне вікно з трею та виводить його на передній план Windows."""
        self.setWindowState(
            (self.windowState() & ~Qt.WindowState.WindowMinimized) | Qt.WindowState.WindowActive
        )
        self.showNormal()
        self.show()
        self.raise_()
        self.activateWindow()
        try:
            hwnd = int(self.winId())
            if hwnd:
                ctypes.windll.user32.ShowWindow(hwnd, 9)  # SW_RESTORE
                ctypes.windll.user32.SetForegroundWindow(hwnd)
        except Exception:
            pass

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

    def nativeEvent(self, eventType: Any, message: Any) -> Any:
        """Обробляє системне повідомлення пробудження (коли користувач повторно запускає лаунчер)."""
        if RESTORE_WINDOW_MSG_ID and message:
            try:
                msg = wintypes.MSG.from_address(int(message))
                if msg.message == RESTORE_WINDOW_MSG_ID:
                    self.restore_from_tray()
            except Exception:
                pass
        return super().nativeEvent(eventType, message)


def create_launcher_app(argv: list[str] | None = None) -> tuple[QApplication, LauncherWindow]:
    """Ініціалізує QApplication, локальні техно-шрифти, QSS-тему та головне вікно."""
    app = QApplication.instance()
    if app is None:
        app = QApplication(argv or [])
    assert isinstance(app, QApplication)

    fonts = load_bundled_fonts()
    window = LauncherWindow()
    window.setStyleSheet(build_cyberpunk_qss(heading_font=fonts["heading"], mono_font=fonts["mono"]))
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
            window.restore_from_tray()
        return app.exec()
    finally:
        mutex.release()

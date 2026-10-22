"""Контролер жестів (drag / swipe), обмеження SetWindowPos до ~30 Гц, повернення вікна по Esc та навичка gesture.drag."""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable

import win32con

from friday.config import GestureConfig
from friday.gesture.camera import CameraStream
from friday.gesture.gestures import (
    GestureState,
    GestureStateMachine,
    HandObservation,
    find_monitor_at_point,
    find_neighbor_monitor,
    map_normalized_to_virtual_desktop,
)
from friday.gesture.hand_tracker import HandTracker
from friday.gesture.smoothing import OneEuroFilter2D
from friday.skills.windows import WindowManager
from friday.util.monitors import MonitorInfo, Rect

log = logging.getLogger("friday.gesture.controller")


@dataclass(slots=True)
class DragSession:
    """Зберігає початковий стан активного вікна на момент старту щипка для перетягування або відкату."""

    hwnd: int
    initial_rect: Rect
    initial_monitor_idx: int


class GestureWindowMover:
    """Зв'язує вихід автомата жестів (HOVER / DRAGGING / DROP) із WindowManager без дублювання Win32-коду."""

    def __init__(
        self,
        config: GestureConfig,
        window_manager: WindowManager,
        max_setpos_hz: float = 30.0,
    ) -> None:
        self.config = config
        self.wm = window_manager
        self.fsm = GestureStateMachine(config=config)
        self.smoother = OneEuroFilter2D(min_cutoff=1.2, beta=0.8, d_cutoff=1.0)
        self._min_setpos_interval_s = 1.0 / max(1.0, max_setpos_hz)
        self._last_setpos_at: float = 0.0
        self._session: DragSession | None = None
        self._monitors_cache: list[MonitorInfo] = []
        self.last_hand_to_window_ms: float = 0.0

    @property
    def active_session(self) -> DragSession | None:
        return self._session

    def refresh_monitors(self) -> list[MonitorInfo]:
        """Оновлює кеш списку моніторів із WindowManager."""
        self._monitors_cache = self.wm.backend.get_monitors()
        return self._monitors_cache

    def _begin_drag_session(self) -> DragSession:
        """Фіксує активне вікно на початку щипка та перевіряє його безпеку."""
        hwnd = self.wm.find_window("active")
        self.wm.validate_draggable_window(hwnd)
        monitors = self._monitors_cache or self.refresh_monitors()
        rect = self.wm.backend.get_window_rect(hwnd)
        mon_idx = self.wm.backend.get_window_monitor_index(hwnd, monitors)

        if self.config.mode == "drag":
            # Знімаємо максимізацію перед плавним перетягуванням
            self.wm.backend.show_window(hwnd, win32con.SW_RESTORE)

        session = DragSession(hwnd=hwnd, initial_rect=rect, initial_monitor_idx=mon_idx)
        self._session = session
        log.info("✋ Початок жесту для HWND=%s (монітор #%d)", hwnd, mon_idx)
        return session

    def process_observation(self, obs: HandObservation | None) -> dict[str, Any]:
        """Обробляє одне спостереження руки й за потреби переміщує вікно."""
        t0 = time.perf_counter()
        prev_state = self.fsm.state
        step = self.fsm.update(obs)

        if step.state == GestureState.IDLE or step.cursor_norm is None:
            self.smoother.reset()
            return {"state": step.state.value}

        ts = obs.timestamp if obs is not None else t0
        smooth_x, smooth_y = self.smoother.filter(step.cursor_norm[0], step.cursor_norm[1], ts)
        monitors = self._monitors_cache or self.refresh_monitors()
        screen_x, screen_y = map_normalized_to_virtual_desktop(smooth_x, smooth_y, monitors)

        # Перехід у DRAGGING: фіксуємо вікно
        if step.state == GestureState.DRAGGING and prev_state != GestureState.DRAGGING:
            self._begin_drag_session()

        # Режим SWIPE: різкий рух під час щипка переносить вікно на сусідній монітор
        if step.state == GestureState.DRAGGING and self.config.mode == "swipe" and step.swipe_direction:
            session = self._session or self._begin_drag_session()
            target_mon = find_neighbor_monitor(
                session.initial_monitor_idx,
                step.swipe_direction,
                monitors,
            )
            res = self.wm.move_to_monitor(session.hwnd, monitor_idx=target_mon.index, mode="maximize")
            self._session = DragSession(
                hwnd=session.hwnd,
                initial_rect=session.initial_rect,
                initial_monitor_idx=target_mon.index,
            )
            self.last_hand_to_window_ms = (time.perf_counter() - t0) * 1000.0
            log.info(
                "👋 Swipe [%s] -> монітор #%d (⏱ %.2f мс)",
                step.swipe_direction,
                target_mon.index,
                self.last_hand_to_window_ms,
            )
            return {
                "state": step.state.value,
                "action": "swipe_move",
                "direction": step.swipe_direction,
                "monitor": target_mon.index,
                "result": res,
            }

        # Режим DRAG: плавне слідування вікна за рукою (обмежено до ~30 Гц)
        if step.state == GestureState.DRAGGING and self.config.mode == "drag" and self._session is not None:
            now = time.perf_counter()
            if (now - self._last_setpos_at) >= self._min_setpos_interval_s:
                w = max(400, min(1200, self._session.initial_rect.width))
                h = max(300, min(800, self._session.initial_rect.height))
                win_x = screen_x - w // 2
                win_y = max(0, screen_y - 40)
                self.wm.backend.set_window_pos(self._session.hwnd, win_x, win_y, w, h)
                self._last_setpos_at = now
                self.last_hand_to_window_ms = (time.perf_counter() - t0) * 1000.0
                return {
                    "state": step.state.value,
                    "action": "drag_pos",
                    "screen_pos": (screen_x, screen_y),
                }

        # Завершення щипка (DROP) у режимі drag: прилипання вікна до монітора під рукою
        if step.state == GestureState.DROP and self._session is not None:
            session = self._session
            self._session = None
            if self.config.mode == "drag":
                target_mon = find_monitor_at_point(screen_x, screen_y, monitors)
                res = self.wm.move_to_monitor(session.hwnd, monitor_idx=target_mon.index, mode="maximize")
                self.last_hand_to_window_ms = (time.perf_counter() - t0) * 1000.0
                log.info(
                    "✋ Drop HWND=%s на монітор #%d (⏱ %.2f мс)",
                    session.hwnd,
                    target_mon.index,
                    self.last_hand_to_window_ms,
                )
                return {
                    "state": step.state.value,
                    "action": "drop_snap",
                    "monitor": target_mon.index,
                    "result": res,
                }

        return {"state": step.state.value, "screen_pos": (screen_x, screen_y)}

    def cancel_and_restore(self) -> dict[str, Any] | None:
        """Скасовує поточне перетягування (по Esc або хоткею) і повертає вікно на початкову позицію."""
        session = self._session
        self._session = None
        self.fsm.reset()
        self.smoother.reset()
        if session is None:
            return None

        r = session.initial_rect
        self.wm.backend.show_window(session.hwnd, win32con.SW_RESTORE)
        self.wm.backend.set_window_pos(session.hwnd, r.left, r.top, r.width, r.height)
        log.info("↩ Перетягування скасовано: вікно HWND=%s повернуто на %s", session.hwnd, r)
        return {"hwnd": session.hwnd, "restored_rect": r}


class GestureRuntimeController:
    """Керує фоновим потоком камери + MediaPipe: вмикається лише за запитом і сам вимикається по тайм-ауту."""

    def __init__(
        self,
        config: GestureConfig,
        window_manager: WindowManager,
        camera_factory: Callable[[], CameraStream] | None = None,
        tracker_factory: Callable[[], HandTracker] | None = None,
    ) -> None:
        self.config = config
        self.wm = window_manager
        self.mover = GestureWindowMover(config=config, window_manager=window_manager)
        self._camera_factory = camera_factory or (
            lambda: CameraStream(camera_index=config.camera_index, fps=config.fps)
        )
        self._tracker_factory = tracker_factory or (
            lambda: HandTracker(min_confidence=config.min_confidence)
        )

        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self.last_camera_release_ms: float = 0.0

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> bool:
        """Запускає фоновий потік відстеження жестів."""
        if not self.config.enabled:
            log.info("Режим жестів вимкнено у config.toml")
            return False

        with self._lock:
            if self.is_running:
                return True
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._worker_loop,
                name="friday-gesture-worker",
                daemon=True,
            )
            self._thread.start()
            return True

    def stop(self, cancel_drag: bool = False) -> float:
        """Зупиняє потік, звільняє камеру (< 1 с) і за потреби повертає вікно на початкове місце."""
        t0 = time.perf_counter()
        self._stop_event.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=0.95)
        self._thread = None
        if cancel_drag:
            self.mover.cancel_and_restore()
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        log.info("🛑 Режим жестів зупинено за %.2f мс", elapsed_ms)
        return elapsed_ms

    def _worker_loop(self) -> None:
        camera = self._camera_factory()
        tracker = self._tracker_factory()
        if not camera.open():
            return

        self.mover.refresh_monitors()
        frame_interval = 1.0 / max(10, self.config.fps)
        last_hand_seen_at = time.perf_counter()

        try:
            while not self._stop_event.is_set():
                loop_t0 = time.perf_counter()
                rgb = camera.read_rgb()
                if rgb is not None:
                    obs = tracker.detect(rgb, timestamp_s=loop_t0)
                    if obs is not None:
                        last_hand_seen_at = loop_t0
                    try:
                        self.mover.process_observation(obs)
                    except PermissionError as exc:
                        log.warning("Обмеження безпеки вікна: %s", exc)
                        self.mover.fsm.reset()
                    except Exception as exc:
                        log.debug("Помилка обробки кадру жестів: %s", exc)

                if (loop_t0 - last_hand_seen_at) >= self.config.idle_timeout_s:
                    log.info(
                        "⏳ Рука не виявлена протягом %.1f с — автоматичне вимкнення камери.",
                        self.config.idle_timeout_s,
                    )
                    break

                elapsed = time.perf_counter() - loop_t0
                sleep_s = max(0.001, frame_interval - elapsed)
                self._stop_event.wait(timeout=sleep_s)
        finally:
            tracker.close()
            self.last_camera_release_ms = camera.close()


class GestureDragSkill:
    """Навичка gesture.drag для запуску або зупинки режиму жестів через роутер."""

    name = "gesture.drag"

    def __init__(self, controller: GestureRuntimeController) -> None:
        self.controller = controller

    async def execute(self, args: dict[str, Any]) -> dict[str, Any]:
        action = str(args.get("action", "toggle")).lower()
        if action == "stop" or (action == "toggle" and self.controller.is_running):
            stop_ms = await asyncio.to_thread(self.controller.stop, False)
            return {"status": "stopped", "stop_ms": stop_ms}

        started = await asyncio.to_thread(self.controller.start)
        return {"status": "started" if started else "disabled", "mode": self.controller.config.mode}

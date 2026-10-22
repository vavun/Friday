"""Контролер жестів (drag / swipe), обмеження SetWindowPos до ~30 Гц, повернення вікна по Esc та навичка gesture.drag."""

from __future__ import annotations

import asyncio
import logging
import math
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
    PinchDetector,
    compute_pinch_midpoint,
    compute_pinch_ratio,
    compute_virtual_desktop_bounds,
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
    """Зберігає початковий та поточний стан активного вікна для перетягування, зміни розміру або відкату."""

    hwnd: int
    initial_rect: Rect
    initial_monitor_idx: int
    current_width: int | None = None
    current_height: int | None = None


class GestureWindowMover:
    """Зв'язує вихід автомата жестів (HOVER / DRAGGING / DROP / CLOSED) із WindowManager без дублювання Win32-коду."""

    def __init__(
        self,
        config: GestureConfig,
        window_manager: WindowManager,
        max_setpos_hz: float = 30.0,
    ) -> None:
        self.config = config
        self.wm = window_manager
        self.fsm = GestureStateMachine(config=config)
        self.pinch2 = PinchDetector(pinch_on=config.pinch_on, pinch_off=config.pinch_off)
        self.smoother = OneEuroFilter2D(min_cutoff=1.2, beta=0.8, d_cutoff=1.0)
        self.smoother2 = OneEuroFilter2D(min_cutoff=1.2, beta=0.8, d_cutoff=1.0)
        self._two_hand_resizing: bool = False
        self._initial_hands_dist: float | None = None
        self._initial_resize_rect: Rect | None = None
        self._min_setpos_interval_s = 1.0 / max(1.0, max_setpos_hz)
        self._last_setpos_at: float = 0.0
        self._session: DragSession | None = None
        self._monitors_cache: list[MonitorInfo] = []
        self.last_hand_to_window_ms: float = 0.0

    @property
    def active_session(self) -> DragSession | None:
        return self._session

    def reset(self) -> None:
        """Повне скидання сесії перетягування та внутрішніх автоматів."""
        self._session = None
        self.fsm.reset()
        self.pinch2.reset()
        self.smoother.reset()
        self.smoother2.reset()
        self._two_hand_resizing = False
        self._initial_hands_dist = None
        self._initial_resize_rect = None

    def refresh_monitors(self) -> list[MonitorInfo]:
        """Оновлює кеш списку моніторів із WindowManager."""
        self._monitors_cache = self.wm.backend.get_monitors()
        return self._monitors_cache

    def _begin_drag_session(self, point: tuple[int, int] | None = None) -> DragSession:
        """Фіксує вікно під рукою (або активне) на початку щипка та перевіряє його безпеку."""
        if point is not None:
            hwnd = self.wm.find_window_at_point(point[0], point[1], fallback_to_active=True)
        else:
            hwnd = self.wm.find_window("active")

        self.wm.validate_draggable_window(hwnd)

        # Переводимо фокус на цільове вікно, щоб воно піднялося над іншими
        self.wm.backend.set_foreground(hwnd)

        monitors = self._monitors_cache or self.refresh_monitors()
        rect = self.wm.backend.get_window_rect(hwnd)
        mon_idx = self.wm.backend.get_window_monitor_index(hwnd, monitors)

        if self.config.mode == "drag":
            # Знімаємо максимізацію перед плавним перетягуванням
            self.wm.backend.show_window(hwnd, win32con.SW_RESTORE)

        session = DragSession(hwnd=hwnd, initial_rect=rect, initial_monitor_idx=mon_idx)
        self._session = session
        log.info("✋ Початок жесту для HWND=%s (монітор #%d, point=%s)", hwnd, mon_idx, point)
        return session

    def process_observation(self, obs: HandObservation | None) -> dict[str, Any]:
        """Обробляє одне спостереження руки й за потреби переміщує або масштабує вікно."""
        t0 = time.perf_counter()
        prev_state = self.fsm.state
        step = self.fsm.update(obs)

        # Закриття вікна жестом кулака (fist gesture)
        if step.state == GestureState.CLOSED or step.is_fist:
            hwnd = self._session.hwnd if self._session is not None else None
            self.reset()
            if hwnd is not None:
                try:
                    self.wm.close_window(hwnd)
                    log.info("✊ Вікно HWND=%s закрито жестом кулака", hwnd)
                    return {"state": GestureState.CLOSED.value, "action": "window_close", "hwnd": hwnd}
                except Exception as exc:
                    log.warning("Не вдалося закрити вікно HWND=%s: %s", hwnd, exc)
                    return {"state": GestureState.CLOSED.value, "error": str(exc)}
            return {"state": GestureState.CLOSED.value}

        if step.state == GestureState.IDLE or step.cursor_norm is None:
            self.smoother.reset()
            self.smoother2.reset()
            self.pinch2.reset()
            self._two_hand_resizing = False
            self._initial_hands_dist = None
            self._initial_resize_rect = None
            if self._session is not None:
                log.info("Скидання сесії перетягування для HWND=%s при вході в IDLE", self._session.hwnd)
                self._session = None
            return {"state": step.state.value}

        ts = obs.timestamp if obs is not None else t0
        smooth_x, smooth_y = self.smoother.filter(step.cursor_norm[0], step.cursor_norm[1], ts)
        monitors = self._monitors_cache or self.refresh_monitors()
        screen_x, screen_y = map_normalized_to_virtual_desktop(smooth_x, smooth_y, monitors)

        # Перехід у DRAGGING або відсутність активної сесії: фіксуємо вікно у точці руки
        if step.state == GestureState.DRAGGING and (self._session is None or prev_state != GestureState.DRAGGING):
            try:
                self._begin_drag_session((screen_x, screen_y))
            except Exception as exc:
                log.warning("Не вдалося почати перетягування у точці (%d, %d): %s", screen_x, screen_y, exc)
                self.reset()
                return {"state": self.fsm.state.value, "error": str(exc)}

        # Режим SWIPE: різкий рух під час щипка переносить вікно на сусідній монітор
        if step.state == GestureState.DRAGGING and self.config.mode == "swipe" and step.swipe_direction:
            session = self._session
            if session is None:
                try:
                    session = self._begin_drag_session((screen_x, screen_y))
                except Exception as exc:
                    log.warning("Не вдалося зафіксувати вікно для свайпу: %s", exc)
                    self.reset()
                    return {"state": step.state.value, "error": str(exc)}
            target_mon = find_neighbor_monitor(
                session.initial_monitor_idx,
                step.swipe_direction,
                monitors,
            )
            try:
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
            except Exception as exc:
                log.warning("Помилка переміщення swipe: %s", exc)
                return {"state": step.state.value, "error": str(exc)}

        # Режим DRAG: плавне слідування вікна за рукою або дворучний динамічний ресайз (до ~30 Гц)
        if step.state == GestureState.DRAGGING and self.config.mode == "drag" and self._session is not None:
            now = ts
            if (now - self._last_setpos_at) >= self._min_setpos_interval_s:
                bounds = compute_virtual_desktop_bounds(monitors)

                # Перевіряємо наявність та щипок другої руки для динамічного масштабування
                is_second_pinched = False
                if obs is not None and obs.landmarks_second is not None:
                    ratio2 = compute_pinch_ratio(obs.landmarks_second)
                    is_second_pinched = self.pinch2.update(ratio2)
                else:
                    self.pinch2.reset()

                if is_second_pinched and obs is not None and obs.landmarks_second is not None:
                    # Дворучний жест: масштабування вікна за відстанню між щипками
                    p2_norm = compute_pinch_midpoint(obs.landmarks_second, mirror=self.config.mirror)
                    s2_x, s2_y = self.smoother2.filter(p2_norm[0], p2_norm[1], ts)
                    p2_screen = map_normalized_to_virtual_desktop(s2_x, s2_y, monitors)

                    current_dist = math.hypot(s2_x - smooth_x, s2_y - smooth_y)
                    if (
                        not self._two_hand_resizing
                        or self._initial_hands_dist is None
                        or self._initial_resize_rect is None
                    ):
                        self._two_hand_resizing = True
                        self._initial_hands_dist = max(0.04, current_dist)
                        self._initial_resize_rect = self.wm.backend.get_window_rect(self._session.hwnd)

                    scale = current_dist / self._initial_hands_dist
                    scale = max(0.2, min(4.0, scale))

                    base_w = (
                        self._initial_resize_rect.width
                        if (self._initial_resize_rect and self._initial_resize_rect.width > 50)
                        else 800
                    )
                    base_h = (
                        self._initial_resize_rect.height
                        if (self._initial_resize_rect and self._initial_resize_rect.height > 50)
                        else 600
                    )

                    new_w = max(320, min(bounds.width, int(base_w * scale)))
                    new_h = max(200, min(bounds.height, int(base_h * scale)))
                    self._session.current_width = new_w
                    self._session.current_height = new_h

                    mid_x = (screen_x + p2_screen[0]) // 2
                    mid_y = (screen_y + p2_screen[1]) // 2

                    win_x = max(bounds.left - new_w + 100, min(bounds.right - 100, mid_x - new_w // 2))
                    win_y = max(bounds.top, min(bounds.bottom - 100, mid_y - new_h // 2))

                    try:
                        self.wm.backend.set_window_pos(self._session.hwnd, win_x, win_y, new_w, new_h)
                        self._last_setpos_at = now
                    except Exception as exc:
                        log.debug("Пропуск кадру ресайзу для HWND=%s: %s", self._session.hwnd, exc)
                        if not self.wm.backend.is_window(self._session.hwnd):
                            log.warning("Вікно HWND=%s закрито або недоступне", self._session.hwnd)
                            self.reset()
                            return {"state": "IDLE", "error": "window_lost"}

                    self.last_hand_to_window_ms = (time.perf_counter() - t0) * 1000.0
                    return {
                        "state": step.state.value,
                        "action": "resize_pos",
                        "screen_pos": (mid_x, mid_y),
                        "size": (new_w, new_h),
                        "scale": scale,
                    }

                # Звичайне перетягування однією рукою
                if self._two_hand_resizing:
                    self._two_hand_resizing = False
                    self._initial_hands_dist = None
                    self._initial_resize_rect = None
                    self.smoother2.reset()

                w = self._session.current_width or max(400, min(1200, self._session.initial_rect.width))
                h = self._session.current_height or max(300, min(800, self._session.initial_rect.height))
                win_x = max(bounds.left - w + 100, min(bounds.right - 100, screen_x - w // 2))
                win_y = max(bounds.top, min(bounds.bottom - 100, screen_y - 40))
                try:
                    self.wm.backend.set_window_pos(self._session.hwnd, win_x, win_y, w, h)
                    self._last_setpos_at = now
                except Exception as exc:
                    log.debug("Пропуск кадру переміщення для HWND=%s: %s", self._session.hwnd, exc)
                    if not self.wm.backend.is_window(self._session.hwnd):
                        log.warning("Вікно HWND=%s закрито або недоступне", self._session.hwnd)
                        self.reset()
                        return {"state": "IDLE", "error": "window_lost"}
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
                try:
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
                except Exception as exc:
                    log.warning("Помилка move_to_monitor при Drop для HWND=%s: %s", session.hwnd, exc)
                    return {
                        "state": step.state.value,
                        "action": "drop_failed",
                        "error": str(exc),
                    }

        return {"state": step.state.value, "screen_pos": (screen_x, screen_y)}

    def cancel_and_restore(self) -> dict[str, Any] | None:
        """Скасовує поточне перетягування (по Esc або хоткею) і повертає вікно на початкову позицію."""
        session = self._session
        self._session = None
        self.fsm.reset()
        self.pinch2.reset()
        self.smoother.reset()
        self.smoother2.reset()
        self._two_hand_resizing = False
        self._initial_hands_dist = None
        self._initial_resize_rect = None
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
                    if obs is not None or self.mover.active_session is not None:
                        last_hand_seen_at = loop_t0
                    try:
                        self.mover.process_observation(obs)
                    except (PermissionError, LookupError) as exc:
                        log.warning("Неможливо захопити вікно жестом: %s", exc)
                        self.mover.reset()
                    except Exception as exc:
                        log.debug("Помилка обробки кадру жестів: %s", exc)
                else:
                    try:
                        self.mover.process_observation(None)
                    except Exception as exc:
                        log.debug("Помилка обробки порожнього кадру камери: %s", exc)

                if (loop_t0 - last_hand_seen_at) >= self.config.idle_timeout_s:
                    log.info(
                        "⏳ Рука не виявлена протягом %.1f с — автоматичне вимкнення камери.",
                        self.config.idle_timeout_s,
                    )
                    break

                elapsed = time.perf_counter() - loop_t0
                sleep_s = max(0.001, frame_interval - elapsed)
                self._stop_event.wait(timeout=sleep_s)
        except Exception as loop_err:
            log.error("Помилка робочого циклу жестів: %s", loop_err, exc_info=True)
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

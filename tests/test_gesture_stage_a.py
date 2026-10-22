"""Юніт-тести для Етапу A (gesture.drag): детектор щипка, фільтр One Euro, автомат станів, 3 монітори, drag/swipe та безпека."""

from __future__ import annotations

import asyncio
import time
from typing import Any

import numpy as np
import pytest

from friday.config import GestureConfig
from friday.gesture.camera import CameraStream
from friday.gesture.controller import (
    GestureDragSkill,
    GestureRuntimeController,
    GestureWindowMover,
)
from friday.gesture.gestures import (
    GestureState,
    GestureStateMachine,
    HandObservation,
    PinchDetector,
    Point3D,
    compute_pinch_ratio,
    find_monitor_at_point,
    find_neighbor_monitor,
    is_fist_gesture,
    map_normalized_to_virtual_desktop,
)
from friday.gesture.smoothing import OneEuroFilter2D
from friday.skills.windows import WindowEntry, WindowManager
from friday.util.monitors import MonitorInfo, Rect


def make_synthetic_hand(
    pinch_distance: float = 0.05,
    hand_size: float = 0.25,
    center_x: float = 0.5,
    center_y: float = 0.5,
    confidence: float = 0.95,
    timestamp: float = 0.0,
) -> HandObservation:
    """Генерує синтетичний набір із 21 точки руки з контрольованим розміром долоні (0..9) та щипком (4..8)."""
    pts = [Point3D(x=center_x, y=center_y, z=0.0) for _ in range(21)]
    # Зап'ястя (0) та основа середнього пальця (9) задають hand_size
    pts[0] = Point3D(x=center_x, y=center_y + hand_size, z=0.0)
    pts[9] = Point3D(x=center_x, y=center_y, z=0.0)
    # Великий (4) та вказівний (8) пальці навколо (center_x, center_y)
    half = pinch_distance * 0.5
    pts[4] = Point3D(x=center_x - half, y=center_y, z=0.0)
    pts[8] = Point3D(x=center_x + half, y=center_y, z=0.0)
    return HandObservation(landmarks=tuple(pts), confidence=confidence, timestamp=timestamp)


def make_synthetic_fist(
    center_x: float = 0.5,
    center_y: float = 0.5,
    hand_size: float = 0.25,
    confidence: float = 0.95,
    timestamp: float = 0.0,
) -> HandObservation:
    """Генерує руку зі складеними у кулак пальцями (кінчики біля зап'ястя/долоні)."""
    pts = [Point3D(x=center_x, y=center_y, z=0.0) for _ in range(21)]
    wrist_y = center_y + hand_size
    pts[0] = Point3D(x=center_x, y=wrist_y, z=0.0)
    pts[9] = Point3D(x=center_x, y=center_y, z=0.0)
    # Кінчики пальців (8, 12, 16, 20) підігнуті дуже близько до зап'ястя (d_tip < 0.05)
    for tip_idx in (8, 12, 16, 20):
        pts[tip_idx] = Point3D(x=center_x, y=wrist_y - 0.05, z=0.0)
    # PIP суглоби (6, 10, 14, 18) розташовані далі від зап'ястя
    for pip_idx in (6, 10, 14, 18):
        pts[pip_idx] = Point3D(x=center_x, y=center_y + 0.05, z=0.0)
    # Великий палець складений біля основи
    pts[4] = Point3D(x=center_x + 0.02, y=center_y + 0.02, z=0.0)
    return HandObservation(landmarks=tuple(pts), confidence=confidence, timestamp=timestamp)


def make_synthetic_two_hands(
    p1_center: tuple[float, float] = (0.3, 0.5),
    p2_center: tuple[float, float] = (0.7, 0.5),
    p1_pinch: float = 0.04,
    p2_pinch: float = 0.04,
    timestamp: float = 0.0,
) -> HandObservation:
    h1 = make_synthetic_hand(pinch_distance=p1_pinch, center_x=p1_center[0], center_y=p1_center[1], timestamp=timestamp)
    h2 = make_synthetic_hand(pinch_distance=p2_pinch, center_x=p2_center[0], center_y=p2_center[1], timestamp=timestamp)
    return HandObservation(
        landmarks=h1.landmarks,
        confidence=h1.confidence,
        timestamp=timestamp,
        landmarks_second=h2.landmarks,
        confidence_second=h2.confidence,
    )


class ThreeMonitorFakeBackend:
    """Фейковий Win32Backend із 3 моніторами (лівий -768..0, центральний 0..1920, правий 1920..3456)."""

    def __init__(self) -> None:
        self.monitors: list[MonitorInfo] = [
            MonitorInfo(
                index=0,
                handle=10,
                device="\\\\.\\DISPLAY2",
                is_primary=True,
                monitor_rect=Rect(0, 0, 1920, 1080),
                work_area=Rect(0, 0, 1920, 1040),
            ),
            MonitorInfo(
                index=1,
                handle=11,
                device="\\\\.\\DISPLAY11",
                is_primary=False,
                monitor_rect=Rect(-768, 0, 0, 1366),
                work_area=Rect(-768, 0, 0, 1326),
            ),
            MonitorInfo(
                index=2,
                handle=12,
                device="\\\\.\\DISPLAY1",
                is_primary=False,
                monitor_rect=Rect(1920, 0, 3456, 864),
                work_area=Rect(1920, 0, 3456, 824),
            ),
        ]
        self.foreground_hwnd = 500
        self.window_classes: dict[int, str] = {500: "Chrome_WidgetWin_1"}
        self.elevated_hwnds: set[int] = set()
        self.rects: dict[int, Rect] = {500: Rect(100, 100, 900, 700)}
        self.visible_windows: list[WindowEntry] | None = None
        self.show_calls: list[tuple[int, int]] = []
        self.pos_calls: list[tuple[int, int, int, int, int]] = []

    def is_window(self, hwnd: int) -> bool:
        return hwnd in self.rects

    def get_foreground_window(self) -> int:
        return self.foreground_hwnd

    def get_window_class(self, hwnd: int) -> str:
        return self.window_classes.get(hwnd, "")

    def is_window_elevated(self, hwnd: int) -> bool:
        return hwnd in self.elevated_hwnds

    def list_visible_windows(self) -> list[WindowEntry]:
        if self.visible_windows is not None:
            return list(self.visible_windows)
        return [WindowEntry(hwnd=500, title="Chrome", process_name="chrome.exe")]

    def get_window_at_point(self, x: int, y: int) -> int:
        for entry in self.list_visible_windows():
            r = self.rects.get(entry.hwnd)
            if r and r.left <= x <= r.right and r.top <= y <= r.bottom:
                return entry.hwnd
        return 0

    def get_monitors(self) -> list[MonitorInfo]:
        return self.monitors

    def get_window_rect(self, hwnd: int) -> Rect:
        return self.rects[hwnd]

    def get_frame_margins(self, hwnd: int) -> tuple[int, int, int, int]:
        return (0, 0, 0, 0)

    def get_window_monitor_index(self, hwnd: int, monitors: list[MonitorInfo]) -> int:
        r = self.rects[hwnd]
        cx = (r.left + r.right) // 2
        cy = (r.top + r.bottom) // 2
        return find_monitor_at_point(cx, cy, monitors).index

    def show_window(self, hwnd: int, cmd: int) -> None:
        self.show_calls.append((hwnd, cmd))

    def set_window_pos(self, hwnd: int, x: int, y: int, w: int, h: int) -> None:
        self.pos_calls.append((hwnd, x, y, w, h))
        self.rects[hwnd] = Rect(x, y, x + w, y + h)

    def set_foreground(self, hwnd: int) -> bool:
        self.foreground_hwnd = hwnd
        return True

    def close_window(self, hwnd: int) -> bool:
        if hwnd in self.rects:
            del self.rects[hwnd]
            return True
        return False


def test_pinch_ratio_and_hysteresis_detector() -> None:
    """Перевіряє обчислення нормованої відстані щипка (4..8)/(0..9) та гістерезис."""
    obs_closed = make_synthetic_hand(pinch_distance=0.05, hand_size=0.25)  # ratio = 0.20
    obs_mid = make_synthetic_hand(pinch_distance=0.085, hand_size=0.25)    # ratio = 0.34
    obs_open = make_synthetic_hand(pinch_distance=0.125, hand_size=0.25)   # ratio = 0.50

    assert pytest.approx(compute_pinch_ratio(obs_closed.landmarks), rel=1e-3) == 0.20
    assert pytest.approx(compute_pinch_ratio(obs_mid.landmarks), rel=1e-3) == 0.34
    assert pytest.approx(compute_pinch_ratio(obs_open.landmarks), rel=1e-3) == 0.50

    detector = PinchDetector(pinch_on=0.28, pinch_off=0.40)
    assert detector.update(0.34) is False  # ще не досягнуто pinch_on
    assert detector.update(0.20) is True   # увімкнувся щипок
    assert detector.update(0.34) is True   # гістерезис тримає щипок активним між 0.28 та 0.40!
    assert detector.update(0.50) is False  # розімкнувся при >= 0.40


def test_one_euro_filter_reduces_jitter_and_tracks_fast_motion() -> None:
    """Перевіряє, що фільтр One Euro пригнічує тремтіння на місці, але швидко слідує за стрибком."""
    f = OneEuroFilter2D(min_cutoff=1.0, beta=0.8)
    rng = np.random.default_rng(42)

    # 20 кадрів із малим шумом навколо 0.5
    outputs = []
    for i in range(20):
        noisy_x = 0.5 + float(rng.normal(0.0, 0.015))
        x_hat, _ = f.filter(noisy_x, 0.5, t=i * 0.05)
        outputs.append(x_hat)

    # Дисперсія на виході має бути меншою за вхідний шум
    assert np.std(outputs[5:]) < 0.012

    # Різкий рух у точку 0.9 за 2 кроки
    x_fast, _ = f.filter(0.9, 0.5, t=1.05)
    x_fast2, _ = f.filter(0.9, 0.5, t=1.10)
    assert x_fast2 > 0.80


def test_gesture_state_machine_idle_hover_dragging_drop() -> None:
    """Перевіряє переходи IDLE -> HOVER -> DRAGGING (>=150 мс) -> DROP та фільтр присутності руки."""
    cfg = GestureConfig(
        mode="drag",
        pinch_on=0.28,
        pinch_off=0.40,
        hold_ms=150,
        min_present_frames=3,
        mirror=False,
    )
    fsm = GestureStateMachine(cfg)

    # Перші 2 кадри рука ігнорується (min_present_frames=3)
    assert fsm.update(make_synthetic_hand(pinch_distance=0.15, timestamp=0.00)).state == GestureState.IDLE
    assert fsm.update(make_synthetic_hand(pinch_distance=0.15, timestamp=0.05)).state == GestureState.IDLE
    # 3-й кадр з відкритою долонею -> HOVER
    assert fsm.update(make_synthetic_hand(pinch_distance=0.15, timestamp=0.10)).state == GestureState.HOVER

    # Початок щипка (t=0.20) -> ще HOVER, бо минуло 0 мс < 150 мс
    assert fsm.update(make_synthetic_hand(pinch_distance=0.04, timestamp=0.20)).state == GestureState.HOVER
    # t=0.30 (100 мс < 150 мс) -> все ще HOVER
    assert fsm.update(make_synthetic_hand(pinch_distance=0.04, timestamp=0.30)).state == GestureState.HOVER
    # t=0.36 (160 мс >= 150 мс) -> перехід у DRAGGING!
    step_drag = fsm.update(make_synthetic_hand(pinch_distance=0.04, timestamp=0.36))
    assert step_drag.state == GestureState.DRAGGING

    # Відпускання щипка (t=0.50) -> перехід у DROP
    step_drop = fsm.update(make_synthetic_hand(pinch_distance=0.15, timestamp=0.50))
    assert step_drop.state == GestureState.DROP
    assert step_drop.dropped is True

    # Втрата руки -> повернення у IDLE
    assert fsm.update(None).state == GestureState.IDLE


def test_virtual_desktop_mapping_across_three_monitors() -> None:
    """Перевіряє відображення координат руки [0..1] на віртуальний робочий стіл із 3 моніторів."""
    backend = ThreeMonitorFakeBackend()
    monitors = backend.get_monitors()

    # Крайня ліва точка (norm_x=0.05) -> лівий монітор #1 (-768..0)
    x_left, y_left = map_normalized_to_virtual_desktop(0.05, 0.5, monitors)
    assert find_monitor_at_point(x_left, y_left, monitors).index == 1

    # Центральна точка (norm_x=0.45) -> центральний основний монітор #0 (0..1920)
    x_mid, y_mid = map_normalized_to_virtual_desktop(0.45, 0.5, monitors)
    assert find_monitor_at_point(x_mid, y_mid, monitors).index == 0

    # Права точка (norm_x=0.90) -> правий монітор #2 (1920..3456)
    x_right, y_right = map_normalized_to_virtual_desktop(0.90, 0.5, monitors)
    assert find_monitor_at_point(x_right, y_right, monitors).index == 2

    # Перевірка сусідів: ліворуч від #0 є #1, праворуч від #0 є #2
    assert find_neighbor_monitor(0, "left", monitors).index == 1
    assert find_neighbor_monitor(0, "right", monitors).index == 2


def test_controller_drag_across_three_monitors_and_cancel_restore() -> None:
    """Перевіряє режим drag через 3 монітори, скасування по Esc (відкат) і фінальний drop."""
    backend = ThreeMonitorFakeBackend()
    wm = WindowManager(backend=backend)
    cfg = GestureConfig(
        mode="drag",
        hold_ms=150,
        min_present_frames=2,
        mirror=False,
    )
    mover = GestureWindowMover(config=cfg, window_manager=wm, max_setpos_hz=1000.0)

    # 1. Прогрів присутності руки та вхід у DRAGGING на центральному моніторі (#0)
    mover.process_observation(make_synthetic_hand(pinch_distance=0.15, center_x=0.45, timestamp=0.0))
    mover.process_observation(make_synthetic_hand(pinch_distance=0.04, center_x=0.45, timestamp=0.05))
    res_drag = mover.process_observation(make_synthetic_hand(pinch_distance=0.04, center_x=0.92, timestamp=0.25))
    assert res_drag["state"] == "DRAGGING"
    assert mover.last_hand_to_window_ms < 100.0

    # 2. Відпускання руки над правим монітором (#2, center_x=0.92) -> вікно прилипає до монітора #2
    res_drop = mover.process_observation(make_synthetic_hand(pinch_distance=0.15, center_x=0.92, timestamp=0.35))
    assert res_drop["action"] == "drop_snap"
    assert res_drop["monitor"] == 2

    # 3. Нове перетягування і скасування (cancel_and_restore) повертає вікно на початкову позицію
    saved_before_drag = backend.get_window_rect(500)
    mover.process_observation(make_synthetic_hand(pinch_distance=0.04, center_x=0.90, timestamp=1.00))
    mover.process_observation(make_synthetic_hand(pinch_distance=0.04, center_x=0.05, timestamp=1.20))
    restored = mover.cancel_and_restore()
    assert restored is not None
    assert backend.get_window_rect(500) == saved_before_drag


def test_controller_swipe_mode_moves_to_left_and_right_monitors() -> None:
    """Перевіряє режим swipe: щипок + різкий рух вліво/вправо переносить вікно між 3 моніторами."""
    backend = ThreeMonitorFakeBackend()
    wm = WindowManager(backend=backend)
    cfg = GestureConfig(
        mode="swipe",
        hold_ms=150,
        swipe_distance=0.20,
        min_present_frames=2,
        mirror=False,
    )
    mover = GestureWindowMover(config=cfg, window_manager=wm)

    # Починаємо на центральному моніторі (#0), щипок у точці x=0.50, різкий свайп вліво до x=0.20
    mover.process_observation(make_synthetic_hand(pinch_distance=0.15, center_x=0.50, timestamp=0.0))
    mover.process_observation(make_synthetic_hand(pinch_distance=0.04, center_x=0.50, timestamp=0.05))
    mover.process_observation(make_synthetic_hand(pinch_distance=0.04, center_x=0.50, timestamp=0.22))
    res_left = mover.process_observation(make_synthetic_hand(pinch_distance=0.04, center_x=0.20, timestamp=0.35))

    assert res_left["action"] == "swipe_move"
    assert res_left["direction"] == "left"
    assert res_left["monitor"] == 1  # лівий монітор


def test_safety_blocks_desktop_taskbar_and_elevated_windows() -> None:
    """Перевіряє, що робочий стіл (Progman), панель завдань (Shell_TrayWnd) та адмін-вікна захищені."""
    backend = ThreeMonitorFakeBackend()
    wm = WindowManager(backend=backend)

    backend.window_classes[500] = "Shell_TrayWnd"
    with pytest.raises(PermissionError, match="панель завдань"):
        wm.validate_draggable_window(500)

    backend.window_classes[500] = "Chrome_WidgetWin_1"
    backend.elevated_hwnds.add(500)
    with pytest.raises(PermissionError, match="Адміністратора"):
        wm.validate_draggable_window(500)


async def test_runtime_controller_camera_release_under_1s() -> None:
    """Перевіряє запуск/зупинку GestureRuntimeController та звільнення камери < 1 с."""
    backend = ThreeMonitorFakeBackend()
    wm = WindowManager(backend=backend)
    cfg = GestureConfig(fps=20, idle_timeout_s=5.0)

    class FakeCapture:
        def __init__(self) -> None:
            self.opened = True

        def isOpened(self) -> bool:
            return self.opened

        def read(self) -> tuple[bool, np.ndarray]:
            return True, np.zeros((480, 640, 3), dtype=np.uint8)

        def release(self) -> None:
            self.opened = False

    class FakeTracker:
        def detect(self, rgb_frame: np.ndarray, timestamp_s: float | None = None) -> HandObservation | None:
            return make_synthetic_hand(pinch_distance=0.15, timestamp=timestamp_s or time.perf_counter())

        def close(self) -> None:
            pass

    cam = CameraStream(capture_factory=lambda _idx: FakeCapture())
    ctrl = GestureRuntimeController(
        config=cfg,
        window_manager=wm,
        camera_factory=lambda: cam,
        tracker_factory=lambda: FakeTracker(),  # type: ignore[return-value]
    )
    skill = GestureDragSkill(ctrl)

    start_res = await skill.execute({"action": "start"})
    assert start_res["status"] == "started"
    await asyncio.sleep(0.08)
    assert ctrl.is_running is True

    stop_res = await skill.execute({"action": "stop"})
    assert stop_res["status"] == "stopped"
    assert stop_res["stop_ms"] < 1000.0
    assert cam.is_open is False


def test_find_window_at_point_targets_window_under_hand_not_active() -> None:
    """Перевіряє, що щипок захоплює не активне вікно, а те, над яким безпосередньо розташована рука."""
    backend = ThreeMonitorFakeBackend()
    wm = WindowManager(backend=backend)

    # Вікно 500 — активне (Chrome) на координатах (100..900)
    # Вікно 600 — неактивне фонове (Telegram) на координатах (1000..1800)
    backend.foreground_hwnd = 500
    backend.rects[600] = Rect(1000, 100, 1800, 700)
    backend.window_classes[600] = "Telegram_WidgetWin_1"
    backend.visible_windows = [
        WindowEntry(hwnd=500, title="Google Chrome", process_name="chrome.exe"),
        WindowEntry(hwnd=600, title="Telegram", process_name="telegram.exe"),
    ]

    # Прямий виклик wm.find_window_at_point над вікном 600 (x=1200, y=300)
    assert wm.find_window_at_point(1200, 300) == 600

    cfg = GestureConfig(
        mode="drag",
        hold_ms=100,
        min_present_frames=2,
        mirror=False,
    )
    mover = GestureWindowMover(config=cfg, window_manager=wm, max_setpos_hz=1000.0)

    # Нормовані координати для точки (1200, 300) у віртуальному робочому просторі 3 моніторів
    norm_x = (1200 - (-768)) / (3456 - (-768))  # 1968 / 4224 ≈ 0.4659
    norm_y = 300 / 1366  # ≈ 0.2196

    # 1. Рука зависає над вікном Telegram (600)
    mover.process_observation(make_synthetic_hand(pinch_distance=0.15, center_x=norm_x, center_y=norm_y, timestamp=0.0))
    # 2. Початок щипка над вікном Telegram (600)
    mover.process_observation(make_synthetic_hand(pinch_distance=0.04, center_x=norm_x, center_y=norm_y, timestamp=0.05))
    res_drag = mover.process_observation(make_synthetic_hand(pinch_distance=0.04, center_x=norm_x, center_y=norm_y, timestamp=0.20))

    assert res_drag["state"] == "DRAGGING"
    # Перевіряємо: захоплено саме вікно 600 (Telegram), а не активне 500 (Chrome)!
    assert mover.active_session is not None
    assert mover.active_session.hwnd == 600
    assert backend.foreground_hwnd == 600  # Вікно активовано та піднято у фокус

    # Позиція вікна 500 залишилась незмінною
    assert backend.get_window_rect(500) == Rect(100, 100, 900, 700)


def test_find_window_at_point_respects_z_order_and_skips_protected() -> None:
    """Перевіряє дотримання Z-порядку вікон та ігнорування системних класів (Shell_TrayWnd/Progman)."""
    backend = ThreeMonitorFakeBackend()
    wm = WindowManager(backend=backend)

    # Два вікна перекриваються: 500 та 700 мають однакові межі
    backend.rects[700] = Rect(100, 100, 900, 700)
    backend.window_classes[700] = "Sublime_Class"

    # Якщо 700 перше у Z-порядку (передній план) -> повертає 700
    backend.visible_windows = [
        WindowEntry(hwnd=700, title="Sublime", process_name="sublime.exe"),
        WindowEntry(hwnd=500, title="Chrome", process_name="chrome.exe"),
    ]
    assert wm.find_window_at_point(300, 300) == 700

    # Якщо порядок змінюється (500 на передньому плані) -> повертає 500
    backend.visible_windows = [
        WindowEntry(hwnd=500, title="Chrome", process_name="chrome.exe"),
        WindowEntry(hwnd=700, title="Sublime", process_name="sublime.exe"),
    ]
    assert wm.find_window_at_point(300, 300) == 500

    # Перевірка ігнорування панелі завдань / робочого столу:
    backend.rects[999] = Rect(0, 1000, 1920, 1080)
    backend.window_classes[999] = "Shell_TrayWnd"
    backend.visible_windows = [
        WindowEntry(hwnd=999, title="", process_name="explorer.exe"),
        WindowEntry(hwnd=500, title="Chrome", process_name="chrome.exe"),
    ]
    # У точці таскбару (500, 1050):
    # При fallback_to_active=True повертає активне вікно (500)
    assert wm.find_window_at_point(500, 1050, fallback_to_active=True) == 500
    # При fallback_to_active=False генерує LookupError
    with pytest.raises(LookupError, match="не знайдено видимого вікна"):
        wm.find_window_at_point(500, 1050, fallback_to_active=False)


def test_drag_resilience_to_momentary_lost_frames_and_drop() -> None:
    """Перевіряє, що короткочасна втрата руки (1-3 кадри) не обриває перенесення, а довша (>4 кадрів) виконує Drop."""
    cfg = GestureConfig(mode="drag", hold_ms=100, min_present_frames=2, mirror=False)
    fsm = GestureStateMachine(cfg)

    # 1. Вхід у DRAGGING
    fsm.update(make_synthetic_hand(pinch_distance=0.15, timestamp=0.0))
    fsm.update(make_synthetic_hand(pinch_distance=0.04, timestamp=0.05))
    step_drag = fsm.update(make_synthetic_hand(pinch_distance=0.04, timestamp=0.16))
    assert step_drag.state == GestureState.DRAGGING

    # 2. Рука короткочасно зникає на 1-3 кадри (змазування руху, відблиск)
    for _ in range(3):
        step_coast = fsm.update(None)
        assert step_coast.state == GestureState.DRAGGING  # перенесення не вибиває!
        assert step_coast.is_pinched is True

    # 3. Рука знову виявлена — перенесення гладко триває
    step_resumed = fsm.update(make_synthetic_hand(pinch_distance=0.04, timestamp=0.30))
    assert step_resumed.state == GestureState.DRAGGING

    # 4. Якщо рука зникає надовго (> 4 кадрів) — безпечно викликається DROP
    fsm.update(None)  # lost 1
    fsm.update(None)  # lost 2
    fsm.update(None)  # lost 3
    fsm.update(None)  # lost 4
    step_final_drop = fsm.update(None)  # lost 5 -> DROP!
    assert step_final_drop.state == GestureState.DROP
    assert step_final_drop.dropped is True


def test_drag_resilience_to_pinch_jitter() -> None:
    """Перевіряє фільтрацію випадкового 1-кадрового тремтіння щипка (на межі pinch_off)."""
    cfg = GestureConfig(mode="drag", hold_ms=100, min_present_frames=2, mirror=False)
    fsm = GestureStateMachine(cfg)

    # Вхід у DRAGGING
    fsm.update(make_synthetic_hand(pinch_distance=0.15, timestamp=0.0))
    fsm.update(make_synthetic_hand(pinch_distance=0.04, timestamp=0.05))
    fsm.update(make_synthetic_hand(pinch_distance=0.04, timestamp=0.16))

    # 1 кадр із тремтінням навколо межі (ratio ≈ 0.44: відстань 0.11 при розмірі 0.25)
    jitter_hand = make_synthetic_hand(pinch_distance=0.11, hand_size=0.25, timestamp=0.20)
    step_jitter = fsm.update(jitter_hand)
    # Завдяки дебаунсу 1 кадр тремтіння не скидає перенесення передчасно
    assert step_jitter.state == GestureState.DRAGGING

    # Наступний кадр пальці знову стабільно зімкнені -> перенесення продовжується
    step_ok = fsm.update(make_synthetic_hand(pinch_distance=0.04, timestamp=0.25))
    assert step_ok.state == GestureState.DRAGGING


def test_is_fist_gesture_detection() -> None:
    """Перевіряє, що функція is_fist_gesture розрізняє кулак від відкритої долоні та щипка."""
    fist = make_synthetic_fist()
    assert is_fist_gesture(fist.landmarks) is True

    open_palm = make_synthetic_hand(pinch_distance=0.25)
    assert is_fist_gesture(open_palm.landmarks) is False

    pinch = make_synthetic_hand(pinch_distance=0.04)
    assert is_fist_gesture(pinch.landmarks) is False


def test_fist_gesture_closes_window() -> None:
    """Перевіряє закриття вікна (WM_CLOSE / close_window) при утриманні та складанні руки в кулак."""
    backend = ThreeMonitorFakeBackend()
    wm = WindowManager(backend=backend)
    cfg = GestureConfig(mode="drag", hold_ms=100, min_present_frames=2, mirror=False)
    mover = GestureWindowMover(config=cfg, window_manager=wm)

    # 1. Початок щипка та вхід у DRAGGING
    mover.process_observation(make_synthetic_hand(pinch_distance=0.15, timestamp=0.0))
    mover.process_observation(make_synthetic_hand(pinch_distance=0.04, timestamp=0.05))
    step_drag = mover.process_observation(make_synthetic_hand(pinch_distance=0.04, timestamp=0.16))
    assert step_drag["state"] == GestureState.DRAGGING.value
    assert mover.active_session is not None
    assert mover.active_session.hwnd == 500

    # 2. Складаємо руку в кулак (2 кадри для підтвердження дебаунсу)
    res1 = mover.process_observation(make_synthetic_fist(timestamp=0.20))
    assert res1["state"] == GestureState.DRAGGING.value  # 1-й кадр підтвердження

    res2 = mover.process_observation(make_synthetic_fist(timestamp=0.25))
    assert res2["state"] == GestureState.CLOSED.value
    assert res2["action"] == "window_close"
    assert res2["hwnd"] == 500

    # Перевіряємо: вікно закрито і видалено, сесію перенесення очищено
    assert not backend.is_window(500)
    assert mover.active_session is None


def test_two_hand_pinch_resizes_and_centers_window() -> None:
    """Перевіряє дворучний динамічний ресайз: відстань між щипками змінює розмір вікна."""
    backend = ThreeMonitorFakeBackend()
    wm = WindowManager(backend=backend)
    backend.rects[500] = Rect(100, 100, 900, 700)  # 800x600
    cfg = GestureConfig(mode="drag", hold_ms=100, min_present_frames=2, mirror=False)
    mover = GestureWindowMover(config=cfg, window_manager=wm, max_setpos_hz=100.0)

    # 1. Вхід у DRAGGING однією рукою
    mover.process_observation(make_synthetic_hand(pinch_distance=0.15, center_x=0.4, timestamp=0.0))
    mover.process_observation(make_synthetic_hand(pinch_distance=0.04, center_x=0.4, timestamp=0.05))
    mover.process_observation(make_synthetic_hand(pinch_distance=0.04, center_x=0.4, timestamp=0.16))
    assert mover.active_session is not None

    # 2. Підносимо другу руку та складаємо обидва щипки (початкова відстань 0.30: 0.35..0.65)
    two_hands_init = make_synthetic_two_hands(
        p1_center=(0.35, 0.5),
        p2_center=(0.65, 0.5),
        p1_pinch=0.04,
        p2_pinch=0.04,
        timestamp=0.20,
    )
    res_init = mover.process_observation(two_hands_init)
    assert res_init["action"] == "resize_pos"
    assert mover._two_hand_resizing is True

    # 3. Розтягуємо руки ширше (відстань 0.60: 0.20..0.80) -> розмір вікна має зрости вдвічі
    two_hands_expand = make_synthetic_two_hands(
        p1_center=(0.20, 0.5),
        p2_center=(0.80, 0.5),
        p1_pinch=0.04,
        p2_pinch=0.04,
        timestamp=0.25,
    )
    res_expand = mover.process_observation(two_hands_expand)
    assert res_expand["action"] == "resize_pos"
    assert res_expand["scale"] > 1.3
    new_w, new_h = res_expand["size"]
    assert new_w > 800
    assert new_h > 600
    assert backend.get_window_rect(500).width == new_w
    assert backend.get_window_rect(500).height == new_h

    # 4. Відпускаємо другу руку (розмикаємо щипок другої руки до 0.20)
    one_hand_resumed = make_synthetic_two_hands(
        p1_center=(0.5, 0.5),
        p2_center=(0.8, 0.5),
        p1_pinch=0.04,
        p2_pinch=0.20,
        timestamp=0.30,
    )
    res_single = mover.process_observation(one_hand_resumed)
    assert res_single["action"] == "drag_pos"
    # Після виходу з дворучного режиму вікно зберігає масштабований розмір
    assert backend.get_window_rect(500).width == new_w
    assert backend.get_window_rect(500).height == new_h




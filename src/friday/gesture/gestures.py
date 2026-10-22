"""Детектор щипка з гістерезисом, автомат станів жестів та відображення координат на монітори."""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal, Sequence

from friday.config import GestureConfig
from friday.util.monitors import MonitorInfo, Rect


@dataclass(slots=True, frozen=True)
class Point3D:
    """Нормалізована 3D-точка ключового суглоба руки MediaPipe (x, y у [0..1])."""

    x: float
    y: float
    z: float = 0.0


@dataclass(slots=True, frozen=True)
class HandObservation:
    """Результат розпізнавання руки в одному кадрі (21 ключова точка + впевненість)."""

    landmarks: tuple[Point3D, ...]
    confidence: float
    timestamp: float


class GestureState(StrEnum):
    """Стани автомата жестів."""

    IDLE = "IDLE"
    HOVER = "HOVER"
    DRAGGING = "DRAGGING"
    DROP = "DROP"


@dataclass(slots=True, frozen=True)
class GestureStepResult:
    """Вихід одного кроку обробки кадру автоматом жестів."""

    state: GestureState
    pinch_ratio: float | None = None
    is_pinched: bool = False
    cursor_norm: tuple[float, float] | None = None
    swipe_direction: Literal["left", "right"] | None = None
    dropped: bool = False


def compute_pinch_ratio(landmarks: Sequence[Point3D]) -> float:
    """Обчислює відношення відстані між кінчиками великого (4) і вказівного (8) пальців
    до базового розміру долоні (від зап'ястя 0 до основи середнього пальця 9)."""
    if len(landmarks) < 10:
        raise ValueError("Очікується щонайменше 10 точок руки (індекси 0..9)")

    wrist = landmarks[0]
    middle_mcp = landmarks[9]
    thumb_tip = landmarks[4]
    index_tip = landmarks[8]

    hand_size = math.hypot(middle_mcp.x - wrist.x, middle_mcp.y - wrist.y)
    if hand_size < 1e-5:
        return 999.0

    pinch_dist = math.hypot(thumb_tip.x - index_tip.x, thumb_tip.y - index_tip.y)
    return pinch_dist / hand_size


def compute_pinch_midpoint(landmarks: Sequence[Point3D], mirror: bool = True) -> tuple[float, float]:
    """Повертає нормалізовані координати (x, y) середини щипка (між точками 4 та 8) з урахуванням дзеркальності."""
    thumb_tip = landmarks[4]
    index_tip = landmarks[8]
    raw_x = 0.5 * (thumb_tip.x + index_tip.x)
    raw_y = 0.5 * (thumb_tip.y + index_tip.y)
    x = (1.0 - raw_x) if mirror else raw_x
    return (max(0.0, min(1.0, x)), max(0.0, min(1.0, raw_y)))


class PinchDetector:
    """Детектор щипка з подвійним порогом (гістерезис pinch_on / pinch_off)."""

    def __init__(self, pinch_on: float = 0.28, pinch_off: float = 0.40) -> None:
        if pinch_on >= pinch_off:
            raise ValueError("pinch_on має бути меншим за pinch_off для гістерезису")
        self.pinch_on = pinch_on
        self.pinch_off = pinch_off
        self._active = False

    @property
    def active(self) -> bool:
        return self._active

    def reset(self) -> None:
        self._active = False

    def update(self, ratio: float) -> bool:
        """Оновлює стан щипка за новим значенням відношення відстані."""
        if not self._active and ratio <= self.pinch_on:
            self._active = True
        elif self._active and ratio >= self.pinch_off:
            self._active = False
        return self._active


def compute_virtual_desktop_bounds(monitors: Sequence[MonitorInfo]) -> Rect:
    """Обчислює охоплюючий прямокутник віртуального робочого столу всіх моніторів."""
    if not monitors:
        raise ValueError("Список моніторів порожній")
    left = min(m.work_area.left for m in monitors)
    top = min(m.work_area.top for m in monitors)
    right = max(m.work_area.right for m in monitors)
    bottom = max(m.work_area.bottom for m in monitors)
    return Rect(left=left, top=top, right=right, bottom=bottom)


def map_normalized_to_virtual_desktop(
    norm_x: float,
    norm_y: float,
    monitors: Sequence[MonitorInfo],
) -> tuple[int, int]:
    """Відображає нормалізовані координати руки [0..1] на піксельні координати віртуального робочого столу."""
    bounds = compute_virtual_desktop_bounds(monitors)
    clamped_x = max(0.0, min(1.0, norm_x))
    clamped_y = max(0.0, min(1.0, norm_y))
    px = int(round(bounds.left + clamped_x * max(1, bounds.width - 1)))
    py = int(round(bounds.top + clamped_y * max(1, bounds.height - 1)))
    return (px, py)


def find_monitor_at_point(x: int, y: int, monitors: Sequence[MonitorInfo]) -> MonitorInfo:
    """Знаходить монітор, над яким знаходиться точка (x, y), або найближчий за центром."""
    if not monitors:
        raise ValueError("Список моніторів порожній")

    for mon in monitors:
        r = mon.monitor_rect
        if r.left <= x < r.right and r.top <= y < r.bottom:
            return mon

    # Якщо точка потрапила у «мертву зону» між моніторами різної висоти — беремо найближчий
    def _dist_sq(m: MonitorInfo) -> float:
        cx = (m.monitor_rect.left + m.monitor_rect.right) * 0.5
        cy = (m.monitor_rect.top + m.monitor_rect.bottom) * 0.5
        return (x - cx) ** 2 + (y - cy) ** 2

    return min(monitors, key=_dist_sq)


def find_neighbor_monitor(
    current_monitor_index: int,
    direction: Literal["left", "right"],
    monitors: Sequence[MonitorInfo],
) -> MonitorInfo:
    """Знаходить сусідній монітор ліворуч або праворуч за фізичною координатою X."""
    if not monitors:
        raise ValueError("Список моніторів порожній")

    ordered = sorted(monitors, key=lambda m: (m.monitor_rect.left, m.index))
    pos = next(
        (i for i, m in enumerate(ordered) if m.index == current_monitor_index),
        0,
    )
    if direction == "left":
        target_pos = max(0, pos - 1)
    else:
        target_pos = min(len(ordered) - 1, pos + 1)
    return ordered[target_pos]


class GestureStateMachine:
    """Автомат станів жестів: IDLE -> HOVER -> DRAGGING -> DROP."""

    def __init__(self, config: GestureConfig) -> None:
        self.config = config
        self.pinch = PinchDetector(pinch_on=config.pinch_on, pinch_off=config.pinch_off)
        self.state: GestureState = GestureState.IDLE
        self._present_frames: int = 0
        self._pinch_started_at: float | None = None
        self._drag_origin_norm: tuple[float, float] | None = None
        self._drag_started_at: float | None = None
        self._swipe_fired: bool = False

    def reset(self) -> None:
        """Повне скидання стану автомата."""
        self.pinch.reset()
        self.state = GestureState.IDLE
        self._present_frames = 0
        self._pinch_started_at = None
        self._drag_origin_norm = None
        self._drag_started_at = None
        self._swipe_fired = False

    def update(self, obs: HandObservation | None) -> GestureStepResult:
        """Обробляє одне спостереження руки (або None, якщо рука відсутня в кадрі)."""
        if obs is None or obs.confidence < self.config.min_confidence:
            self.reset()
            return GestureStepResult(state=GestureState.IDLE)

        self._present_frames += 1
        if self._present_frames < self.config.min_present_frames:
            return GestureStepResult(state=GestureState.IDLE)

        ratio = compute_pinch_ratio(obs.landmarks)
        is_pinched = self.pinch.update(ratio)
        cursor = compute_pinch_midpoint(obs.landmarks, mirror=self.config.mirror)

        if self.state == GestureState.DROP:
            self.state = GestureState.HOVER

        if not is_pinched:
            self._pinch_started_at = None
            if self.state == GestureState.DRAGGING:
                self.state = GestureState.DROP
                self._drag_origin_norm = None
                self._drag_started_at = None
                self._swipe_fired = False
                return GestureStepResult(
                    state=GestureState.DROP,
                    pinch_ratio=ratio,
                    is_pinched=False,
                    cursor_norm=cursor,
                    dropped=True,
                )
            self.state = GestureState.HOVER
            return GestureStepResult(
                state=GestureState.HOVER,
                pinch_ratio=ratio,
                is_pinched=False,
                cursor_norm=cursor,
            )

        # Щипок активний
        if self._pinch_started_at is None:
            self._pinch_started_at = obs.timestamp

        held_ms = (obs.timestamp - self._pinch_started_at) * 1000.0
        if self.state != GestureState.DRAGGING:
            if held_ms >= self.config.hold_ms:
                self.state = GestureState.DRAGGING
                self._drag_origin_norm = cursor
                self._drag_started_at = obs.timestamp
                self._swipe_fired = False
            else:
                self.state = GestureState.HOVER
                return GestureStepResult(
                    state=GestureState.HOVER,
                    pinch_ratio=ratio,
                    is_pinched=True,
                    cursor_norm=cursor,
                )

        # Перебуваємо у стані DRAGGING
        swipe_dir: Literal["left", "right"] | None = None
        if (
            self.config.mode == "swipe"
            and not self._swipe_fired
            and self._drag_origin_norm is not None
            and self._drag_started_at is not None
        ):
            elapsed_s = obs.timestamp - self._drag_started_at
            dx = cursor[0] - self._drag_origin_norm[0]
            if 0.0 < elapsed_s <= 1.0 and abs(dx) >= self.config.swipe_distance:
                swipe_dir = "right" if dx > 0 else "left"
                self._swipe_fired = True

        return GestureStepResult(
            state=GestureState.DRAGGING,
            pinch_ratio=ratio,
            is_pinched=True,
            cursor_norm=cursor,
            swipe_direction=swipe_dir,
        )

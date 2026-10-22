"""Захоплення кадрів із веб-камери (640x480, 15–20 FPS) без збереження відео на диск."""

from __future__ import annotations

import logging
import time
from typing import Any, Callable

import numpy as np

log = logging.getLogger("friday.gesture.camera")


class CameraStream:
    """Керує життєвим циклом веб-камери OpenCV (відкриття лише за вимогою, миттєве звільнення)."""

    def __init__(
        self,
        camera_index: int = 0,
        width: int = 640,
        height: int = 480,
        fps: int = 20,
        capture_factory: Callable[[int], Any] | None = None,
    ) -> None:
        self.camera_index = camera_index
        self.width = width
        self.height = height
        self.fps = fps
        self._capture_factory = capture_factory
        self._cap: Any | None = None

    @property
    def is_open(self) -> bool:
        """Повертає True, якщо камера зараз відкрита."""
        return self._cap is not None and bool(self._cap.isOpened())

    def open(self) -> bool:
        """Відкриває камеру з параметрами 640x480 та заданим FPS."""
        if self.is_open:
            return True

        if self._capture_factory is not None:
            cap = self._capture_factory(self.camera_index)
        else:
            import cv2

            # CAP_DSHOW на Windows пришвидшує відкриття та звільнення камери (< 200 мс)
            cap = cv2.VideoCapture(self.camera_index, cv2.CAP_DSHOW)
            if not cap.isOpened():
                cap = cv2.VideoCapture(self.camera_index)

            cap.set(cv2.CAP_PROP_FRAME_WIDTH, float(self.width))
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, float(self.height))
            cap.set(cv2.CAP_PROP_FPS, float(self.fps))
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

        if not cap.isOpened():
            cap.release()
            log.warning("Не вдалося відкрити камеру #%d", self.camera_index)
            return False

        self._cap = cap
        log.info("📷 Камеру #%d відкрито (%dx%d @ %d FPS)", self.camera_index, self.width, self.height, self.fps)
        return True

    def read_rgb(self) -> np.ndarray | None:
        """Читає один кадр у пам'ять і конвертує його з BGR у RGB (жодного запису на диск)."""
        if self._cap is None:
            return None
        ok, frame = self._cap.read()
        if not ok or frame is None:
            return None

        if frame.ndim == 3 and frame.shape[2] == 3:
            # Конвертація BGR -> RGB через зріз numpy без зайвого виклику cv2.cvtColor
            return np.ascontiguousarray(frame[:, :, ::-1])
        return frame

    def close(self) -> float:
        """Звільняє камеру і повертає час звільнення в мілісекундах."""
        t0 = time.perf_counter()
        if self._cap is not None:
            try:
                self._cap.release()
            finally:
                self._cap = None
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        log.info("📷 Камеру звільнено за %.2f мс", elapsed_ms)
        return elapsed_ms

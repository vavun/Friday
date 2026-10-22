"""Утиліти для вимірювання затримок етапів обробки команди."""

from __future__ import annotations

import logging
import time
from collections import deque
from dataclasses import dataclass
from typing import Iterator
from contextlib import contextmanager

log = logging.getLogger("friday.timing")


@dataclass(slots=True)
class StageTiming:
    """Результат вимірювання тривалості одного етапу."""

    stage: str
    elapsed_ms: float


class TimingTracker:
    """Зберігає історію вимірювань затримок та надає контекстний менеджер."""

    def __init__(self, max_records: int = 200) -> None:
        self._records: deque[StageTiming] = deque(maxlen=max_records)

    @property
    def records(self) -> list[StageTiming]:
        """Повертає список усіх збережених вимірювань."""
        return list(self._records)

    @contextmanager
    def measure(self, stage: str) -> Iterator[StageTiming]:
        """Контекстний менеджер, що вимірює час виконання блоку в мілісекундах."""
        record = StageTiming(stage=stage, elapsed_ms=0.0)
        start = time.perf_counter()
        try:
            yield record
        finally:
            record.elapsed_ms = (time.perf_counter() - start) * 1000.0
            self._records.append(record)
            log.info("⏱ Етап [%s] виконано за %.2f мс", stage, record.elapsed_ms)

    def clear(self) -> None:
        """Очищає збережені записи."""
        self._records.clear()


tracker = TimingTracker()
measure_time = tracker.measure

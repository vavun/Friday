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

    def summary_by_stage(self) -> dict[str, dict[str, float]]:
        """Групує вимірювання за назвою етапу та обчислює count, avg_ms, max_ms."""
        grouped: dict[str, list[float]] = {}
        for rec in self._records:
            grouped.setdefault(rec.stage, []).append(rec.elapsed_ms)

        out: dict[str, dict[str, float]] = {}
        for stage, vals in grouped.items():
            out[stage] = {
                "count": float(len(vals)),
                "avg_ms": sum(vals) / len(vals),
                "min_ms": min(vals),
                "max_ms": max(vals),
            }
        return out

    def format_summary_table(self) -> str:
        """Формує текстову таблицю затримок по всіх етапах для профілювання (Етап 9)."""
        stats = self.summary_by_stage()
        if not stats:
            return "Немає записаних вимірювань затримок."
        lines = [
            f"{'Етап':<28} | {'К-сть':>5} | {'Сер. (мс)':>10} | {'Макс. (мс)':>10}",
            "-" * 62,
        ]
        for stage, item in sorted(stats.items()):
            lines.append(
                f"{stage:<28} | {int(item['count']):>5} | {item['avg_ms']:>10.2f} | {item['max_ms']:>10.2f}"
            )
        return "\n".join(lines)


tracker = TimingTracker()
measure_time = tracker.measure

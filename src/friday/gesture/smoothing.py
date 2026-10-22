"""Згладжування координат руки фільтром One Euro Filter (1€ Filter)."""

from __future__ import annotations

import math


def _smoothing_factor(dt: float, cutoff: float) -> float:
    """Обчислює коефіцієнт експоненційного згладжування alpha для заданого dt і частоти зрізу."""
    r = 2.0 * math.pi * cutoff * dt
    return r / (r + 1.0)


class OneEuroFilter1D:
    """Одновимірний адаптивний фільтр низьких частот One Euro (Casiez et al., CHI 2012)."""

    def __init__(
        self,
        min_cutoff: float = 1.2,
        beta: float = 0.8,
        d_cutoff: float = 1.0,
    ) -> None:
        self.min_cutoff = min_cutoff
        self.beta = beta
        self.d_cutoff = d_cutoff
        self._x_prev: float | None = None
        self._dx_prev: float = 0.0
        self._t_prev: float | None = None

    def reset(self) -> None:
        """Скидає стан фільтра (при втраті руки)."""
        self._x_prev = None
        self._dx_prev = 0.0
        self._t_prev = None

    def filter(self, x: float, t: float) -> float:
        """Фільтрує значення x у момент часу t (секунди)."""
        if self._t_prev is None or self._x_prev is None:
            self._x_prev = x
            self._dx_prev = 0.0
            self._t_prev = t
            return x

        dt = t - self._t_prev
        if dt <= 1e-6:
            return self._x_prev

        # Похідна (швидкість зміни сигналу)
        a_d = _smoothing_factor(dt, self.d_cutoff)
        dx = (x - self._x_prev) / dt
        dx_hat = a_d * dx + (1.0 - a_d) * self._dx_prev

        # Адаптивна частота зрізу: повільний рух -> сильне згладжування, швидкий рух -> мінімальний лаг
        cutoff = self.min_cutoff + self.beta * abs(dx_hat)
        a = _smoothing_factor(dt, cutoff)
        x_hat = a * x + (1.0 - a) * self._x_prev

        self._x_prev = x_hat
        self._dx_prev = dx_hat
        self._t_prev = t
        return x_hat


class OneEuroFilter2D:
    """Двовимірний фільтр One Euro для нормалізованих або екранних координат (x, y)."""

    def __init__(
        self,
        min_cutoff: float = 1.2,
        beta: float = 0.8,
        d_cutoff: float = 1.0,
    ) -> None:
        self._fx = OneEuroFilter1D(min_cutoff=min_cutoff, beta=beta, d_cutoff=d_cutoff)
        self._fy = OneEuroFilter1D(min_cutoff=min_cutoff, beta=beta, d_cutoff=d_cutoff)

    def reset(self) -> None:
        """Скидає обидві осі фільтра."""
        self._fx.reset()
        self._fy.reset()

    def filter(self, x: float, y: float, t: float) -> tuple[float, float]:
        """Повертає згладжені координати (x_hat, y_hat)."""
        return (self._fx.filter(x, t), self._fy.filter(y, t))

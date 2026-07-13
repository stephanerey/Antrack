"""Position-derived antenna rate estimation."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Deque


@dataclass
class PositionRateEstimator:
    """Estimate angular rate from a short position history."""

    window_s: float = 2.0
    min_dt_s: float = 0.25
    smoothing_alpha: float = 0.35
    _history: Deque[tuple[float, float, float]] = field(default_factory=deque)
    _az_unwrapped: float | None = None
    _last_az: float | None = None
    _az_rate: float = 0.0
    _el_rate: float = 0.0

    def add(self, timestamp_s: float, az_deg: float | None, el_deg: float | None) -> tuple[float, float]:
        if az_deg is None or el_deg is None:
            return self._az_rate, self._el_rate

        az = float(az_deg)
        el = float(el_deg)
        if self._az_unwrapped is None or self._last_az is None:
            self._az_unwrapped = az
        else:
            delta = ((az - self._last_az + 180.0) % 360.0) - 180.0
            self._az_unwrapped += delta
        self._last_az = az

        now = float(timestamp_s)
        self._history.append((now, float(self._az_unwrapped), el))
        cutoff = now - max(0.25, float(self.window_s))
        while len(self._history) > 2 and self._history[0][0] < cutoff:
            self._history.popleft()

        raw_az_rate, raw_el_rate = self._fit_rates()
        if raw_az_rate is not None:
            self._az_rate = self._smooth(self._az_rate, raw_az_rate)
        if raw_el_rate is not None:
            self._el_rate = self._smooth(self._el_rate, raw_el_rate)
        return self._az_rate, self._el_rate

    def reset(self) -> None:
        self._history.clear()
        self._az_unwrapped = None
        self._last_az = None
        self._az_rate = 0.0
        self._el_rate = 0.0

    def _fit_rates(self) -> tuple[float | None, float | None]:
        if len(self._history) < 2:
            return None, None
        t0 = self._history[0][0]
        samples = [(t - t0, az, el) for t, az, el in self._history]
        duration = samples[-1][0] - samples[0][0]
        if duration < max(0.05, float(self.min_dt_s)):
            return None, None

        mean_t = sum(t for t, _az, _el in samples) / len(samples)
        denom = sum((t - mean_t) ** 2 for t, _az, _el in samples)
        if denom <= 1e-9:
            return None, None

        mean_az = sum(az for _t, az, _el in samples) / len(samples)
        mean_el = sum(el for _t, _az, el in samples) / len(samples)
        az_rate = sum((t - mean_t) * (az - mean_az) for t, az, _el in samples) / denom
        el_rate = sum((t - mean_t) * (el - mean_el) for t, _az, el in samples) / denom
        return az_rate, el_rate

    def _smooth(self, previous: float, current: float) -> float:
        alpha = min(1.0, max(0.0, float(self.smoothing_alpha)))
        return (alpha * float(current)) + ((1.0 - alpha) * float(previous))

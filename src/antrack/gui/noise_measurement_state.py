"""State helpers for the live noise measurement tab."""

from __future__ import annotations

import math
import time
from collections import deque
from typing import Deque


DEFAULT_WINDOW_OPTIONS_S = (10.0, 30.0, 60.0)
DEFAULT_WINDOW_S = 30.0
DEFAULT_HISTORY_RETENTION_S = 150.0


class NoiseMeasurementState:
    """Track live absolute and relative noise measurements with bounded history."""

    def __init__(
        self,
        *,
        window_options_s: tuple[float, ...] = DEFAULT_WINDOW_OPTIONS_S,
        default_window_s: float = DEFAULT_WINDOW_S,
        history_retention_s: float = DEFAULT_HISTORY_RETENTION_S,
    ) -> None:
        options = tuple(float(max(1.0, value)) for value in window_options_s) or DEFAULT_WINDOW_OPTIONS_S
        self.window_options_s = options
        self.window_index = 0
        for index, value in enumerate(options):
            if math.isclose(value, float(default_window_s), rel_tol=0.0, abs_tol=1e-9):
                self.window_index = index
                break
        self.history_retention_s = float(max(history_retention_s, max(options) + 60.0))
        self.relative_mode = False
        self.current_absolute_db: float | None = None
        self.reference_absolute_db: float | None = None
        self._history: Deque[tuple[float, float]] = deque()
        self._last_history_point: tuple[float, float] | None = None

    @property
    def current_window_s(self) -> float:
        return float(self.window_options_s[self.window_index])

    @property
    def has_reference(self) -> bool:
        return self.reference_absolute_db is not None and math.isfinite(float(self.reference_absolute_db))

    @property
    def relative_db(self) -> float | None:
        if self.current_absolute_db is None or not self.has_reference:
            return None
        relative_db = float(self.current_absolute_db) - float(self.reference_absolute_db)
        return relative_db if math.isfinite(relative_db) else None

    def update_absolute(self, value_db: float | None, *, timestamp_s: float | None = None) -> bool:
        if value_db is None:
            return False
        try:
            value = float(value_db)
        except Exception:
            return False
        if not math.isfinite(value):
            return False
        timestamp = float(timestamp_s) if timestamp_s is not None else float(time.time())
        if not math.isfinite(timestamp):
            timestamp = float(time.time())
        self.current_absolute_db = value
        return True

    def clear_current(self) -> None:
        self.current_absolute_db = None

    def append_history_point(self, *, timestamp_s: float | None = None) -> bool:
        if self.current_absolute_db is None or not math.isfinite(float(self.current_absolute_db)):
            return False
        timestamp = float(timestamp_s) if timestamp_s is not None else float(time.time())
        if not math.isfinite(timestamp):
            timestamp = float(time.time())
        point = (timestamp, float(self.current_absolute_db))
        last_point = self._last_history_point
        if last_point is not None:
            delta_t = point[0] - last_point[0]
            delta_v = abs(point[1] - last_point[1])
            if delta_t < 0.095 and delta_v < 1e-6:
                self._prune_history(now_s=timestamp)
                return False
        self._history.append(point)
        self._last_history_point = point
        self._prune_history(now_s=timestamp)
        return True

    def set_relative_mode(self, enabled: bool) -> bool:
        if not enabled:
            self.relative_mode = False
            return True
        if self.current_absolute_db is None or not math.isfinite(float(self.current_absolute_db)):
            self.relative_mode = False
            return False
        self.reference_absolute_db = float(self.current_absolute_db)
        self.relative_mode = True
        return True

    def cycle_window(self) -> float:
        self.window_index = (self.window_index + 1) % len(self.window_options_s)
        return self.current_window_s

    def clear_history(self) -> None:
        self._history.clear()
        self._last_history_point = None

    def plot_series(self, *, now_s: float | None = None) -> tuple[list[float], list[float]]:
        if not self._history:
            return [], []
        now_value = float(now_s) if now_s is not None else float(self._history[-1][0])
        cutoff = now_value - self.current_window_s
        xs: list[float] = []
        ys: list[float] = []
        for timestamp, absolute_db in self._history:
            if timestamp < cutoff:
                continue
            value = absolute_db
            if self.relative_mode and self.has_reference:
                value = absolute_db - float(self.reference_absolute_db)
            if not math.isfinite(value):
                continue
            xs.append(float(timestamp))
            ys.append(float(value))
        return xs, ys

    def recent_absolute_range(self) -> tuple[float, float] | None:
        finite_values = [value for _timestamp, value in self._history if math.isfinite(value)]
        if not finite_values:
            return None
        return float(min(finite_values)), float(max(finite_values))

    def _prune_history(self, *, now_s: float) -> None:
        cutoff = float(now_s) - self.history_retention_s
        while self._history and self._history[0][0] < cutoff:
            self._history.popleft()
        if not self._history:
            self._last_history_point = None

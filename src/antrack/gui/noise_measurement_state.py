"""State helpers for the live noise measurement tab."""

from __future__ import annotations

import math
import time
from collections import deque
from typing import Deque


DEFAULT_WINDOW_OPTIONS_S = (30.0, 60.0, 600.0, 3600.0, 86400.0)
DEFAULT_WINDOW_S = 30.0
DEFAULT_HISTORY_RETENTION_S = 86460.0
DEFAULT_MAX_HISTORY_POINTS = 100_000
DEFAULT_MAX_PLOT_POINTS = 5_000


class NoiseMeasurementState:
    """Track live absolute and relative noise measurements with bounded history."""

    def __init__(
        self,
        *,
        window_options_s: tuple[float, ...] = DEFAULT_WINDOW_OPTIONS_S,
        default_window_s: float = DEFAULT_WINDOW_S,
        history_retention_s: float = DEFAULT_HISTORY_RETENTION_S,
        max_history_points: int = DEFAULT_MAX_HISTORY_POINTS,
        max_plot_points: int = DEFAULT_MAX_PLOT_POINTS,
    ) -> None:
        options = tuple(float(max(1.0, value)) for value in window_options_s) or DEFAULT_WINDOW_OPTIONS_S
        self.window_options_s = options
        self.window_index = 0
        for index, value in enumerate(options):
            if math.isclose(value, float(default_window_s), rel_tol=0.0, abs_tol=1e-9):
                self.window_index = index
                break
        self.history_retention_s = float(max(history_retention_s, max(options) + 60.0))
        self.max_history_points = max(100, int(max_history_points))
        self.max_plot_points = max(100, int(max_plot_points))
        self.relative_mode = False
        self.current_absolute_db: float | None = None
        self.reference_absolute_db: float | None = None
        self._history: Deque[tuple[float, float]] = deque()
        self._last_history_point: tuple[float, float] | None = None
        self.reset_statistics()

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

    def update_absolute(
        self,
        value_db: float | None,
        *,
        timestamp_s: float | None = None,
        record_statistics: bool = True,
    ) -> bool:
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
        if record_statistics:
            self.record_statistics(value, timestamp_s=timestamp)
        return True

    def record_statistics(self, value_db: float, *, timestamp_s: float | None = None) -> bool:
        try:
            value = float(value_db)
        except (TypeError, ValueError):
            return False
        if not math.isfinite(value):
            return False
        timestamp = float(timestamp_s) if timestamp_s is not None else float(time.time())
        if not math.isfinite(timestamp):
            timestamp = float(time.time())
        self._statistics_count += 1
        self._statistics_sum += value
        if self._statistics_min is None or value < self._statistics_min:
            self._statistics_min = value
            self._statistics_min_timestamp = timestamp
        if self._statistics_max is None or value > self._statistics_max:
            self._statistics_max = value
            self._statistics_max_timestamp = timestamp
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
        self._compress_history_if_needed()
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

    def reset_statistics(self) -> None:
        self._statistics_count = 0
        self._statistics_sum = 0.0
        self._statistics_min: float | None = None
        self._statistics_max: float | None = None
        self._statistics_min_timestamp: float | None = None
        self._statistics_max_timestamp: float | None = None

    def statistics(self) -> dict[str, float | int | None]:
        if self._statistics_count <= 0:
            return {
                "count": 0,
                "min": None,
                "mean": None,
                "max": None,
                "min_timestamp": None,
                "max_timestamp": None,
            }
        offset = 0.0
        if self.relative_mode and self.has_reference:
            offset = float(self.reference_absolute_db)
        return {
            "count": self._statistics_count,
            "min": float(self._statistics_min) - offset,
            "mean": (self._statistics_sum / self._statistics_count) - offset,
            "max": float(self._statistics_max) - offset,
            "min_timestamp": self._statistics_min_timestamp,
            "max_timestamp": self._statistics_max_timestamp,
        }

    @staticmethod
    def valid_y_range(y_min: object, y_max: object) -> bool:
        try:
            minimum = float(y_min)
            maximum = float(y_max)
        except (TypeError, ValueError):
            return False
        return math.isfinite(minimum) and math.isfinite(maximum) and minimum < maximum

    def plot_series(self, *, now_s: float | None = None) -> tuple[list[float], list[float]]:
        if not self._history:
            return [], []
        now_value = float(now_s) if now_s is not None else float(self._history[-1][0])
        cutoff = now_value - self.current_window_s
        visible: list[tuple[float, float]] = []
        for timestamp, absolute_db in reversed(self._history):
            if timestamp < cutoff:
                break
            value = absolute_db
            if self.relative_mode and self.has_reference:
                value = absolute_db - float(self.reference_absolute_db)
            if not math.isfinite(value):
                continue
            visible.append((float(timestamp), float(value)))
        visible.reverse()
        if len(visible) > self.max_plot_points:
            stride = int(math.ceil(len(visible) / self.max_plot_points))
            decimated = visible[::stride]
            if decimated[-1] != visible[-1]:
                decimated.append(visible[-1])
            visible = decimated
        xs = [point[0] for point in visible]
        ys = [point[1] for point in visible]
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

    def _compress_history_if_needed(self) -> None:
        if len(self._history) <= self.max_history_points:
            return
        points = list(self._history)
        older_count = max(2, (len(points) * 3) // 4)
        if older_count % 2:
            older_count -= 1
        compressed: list[tuple[float, float]] = []
        for index in range(0, older_count, 2):
            first = points[index]
            second = points[index + 1]
            compressed.append(((first[0] + second[0]) * 0.5, (first[1] + second[1]) * 0.5))
        compressed.extend(points[older_count:])
        self._history = deque(compressed)
        self._last_history_point = self._history[-1] if self._history else None

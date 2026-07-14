"""Common scan sample and result helpers."""

from __future__ import annotations

import math
import time
from collections import deque
from statistics import median
from typing import Iterable


def _as_float(value, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return float(default)
    if not math.isfinite(result):
        return float(default)
    return result


def angular_error_deg(az_error_deg: float, el_error_deg: float, center_el_deg: float = 0.0) -> float:
    """Return the small-angle pointing error projected on the sky."""
    cross_el_error = float(az_error_deg) * math.cos(math.radians(float(center_el_deg)))
    return float(math.hypot(cross_el_error, float(el_error_deg)))


class ScanEtaEstimator:
    """Estimate remaining scan time from observed point-completion intervals."""

    def __init__(self, *, window_size: int = 20) -> None:
        self.window_size = max(3, int(window_size))
        self.reset()

    def reset(self, *, started_monotonic_s: float | None = None) -> None:
        self.started_monotonic_s = started_monotonic_s
        self.last_point_monotonic_s: float | None = None
        self.point_intervals_s = deque(maxlen=self.window_size)

    def point_completed(
        self,
        *,
        current: int,
        total: int,
        monotonic_s: float | None = None,
        wall_time_s: float | None = None,
    ) -> dict[str, float | int | None]:
        now = float(time.monotonic() if monotonic_s is None else monotonic_s)
        wall_now = float(time.time() if wall_time_s is None else wall_time_s)
        if self.started_monotonic_s is None:
            self.started_monotonic_s = now
        if self.last_point_monotonic_s is not None:
            interval = now - self.last_point_monotonic_s
            if math.isfinite(interval) and interval >= 0.0:
                self.point_intervals_s.append(interval)
        self.last_point_monotonic_s = now
        elapsed = max(0.0, now - float(self.started_monotonic_s))
        remaining_points = max(0, int(total) - int(current))
        point_duration = float(median(self.point_intervals_s)) if self.point_intervals_s else None
        remaining = None if point_duration is None else point_duration * remaining_points
        return {
            "current": int(current),
            "total": int(total),
            "elapsed_s": elapsed,
            "point_duration_s": point_duration,
            "remaining_s": remaining,
            "estimated_end_s": None if remaining is None else wall_now + remaining,
        }


def make_scan_sample(
    point: dict,
    value: float,
    *,
    theoretical_az_deg: float,
    theoretical_el_deg: float,
    timestamp: float | None = None,
) -> dict:
    """Build the common sample shape consumed by scan UI and future analysis."""
    az = _as_float(point.get("az"), theoretical_az_deg)
    el = _as_float(point.get("el"), theoretical_el_deg)
    theoretical_az = _as_float(point.get("theoretical_az", theoretical_az_deg), theoretical_az_deg)
    theoretical_el = _as_float(point.get("theoretical_el", theoretical_el_deg), theoretical_el_deg)
    offset_az = az - theoretical_az
    offset_el = el - theoretical_el

    sample = dict(point)
    sample.update(
        {
            "az": az,
            "el": el,
            "value": float(value),
            "timestamp": float(time.time() if timestamp is None else timestamp),
            "phase": point.get("phase", "main"),
            "axis": point.get("axis"),
            "theoretical_az": theoretical_az,
            "theoretical_el": theoretical_el,
            "offset_az": offset_az,
            "offset_el": offset_el,
            "offset_az_deg": offset_az,
            "offset_el_deg": offset_el,
        }
    )
    return sample


def make_peak_estimate(
    point: dict,
    *,
    method: str = "best_sample",
    confidence: float = 1.0,
    theoretical_az_deg: float | None = None,
    theoretical_el_deg: float | None = None,
) -> dict:
    """Represent an estimated noise peak using a stable, UI-friendly dict."""
    theoretical_az = _as_float(theoretical_az_deg, point.get("theoretical_az", point.get("az", 0.0)))
    theoretical_el = _as_float(theoretical_el_deg, point.get("theoretical_el", point.get("el", 0.0)))
    az = _as_float(point.get("az"), theoretical_az)
    el = _as_float(point.get("el"), theoretical_el)
    az_error = az - theoretical_az
    el_error = el - theoretical_el
    cross_el_error = az_error * math.cos(math.radians(theoretical_el))
    total_error = angular_error_deg(az_error, el_error, theoretical_el)
    return {
        "az": az,
        "el": el,
        "value": _as_float(point.get("value"), 0.0),
        "timestamp": _as_float(point.get("timestamp"), time.time()),
        "method": str(method),
        "confidence": float(max(0.0, min(1.0, confidence))),
        "theoretical_az": theoretical_az,
        "theoretical_el": theoretical_el,
        "az_error_deg": az_error,
        "el_error_deg": el_error,
        "cross_el_error_deg": cross_el_error,
        "total_pointing_error_deg": total_error,
        "angular_error_deg": total_error,
    }


def make_scan_result(
    *,
    strategy: str,
    samples: Iterable[dict],
    center_az_deg: float,
    center_el_deg: float,
    best_point: dict | None = None,
    peak_estimate: dict | None = None,
) -> dict:
    """Build the common scan result while preserving legacy keys."""
    sample_list = list(samples)
    if best_point is None:
        best_point = max(sample_list, key=lambda point: float(point["value"]))
    if peak_estimate is None:
        peak_estimate = make_peak_estimate(
            best_point,
            theoretical_az_deg=best_point.get("theoretical_az", center_az_deg),
            theoretical_el_deg=best_point.get("theoretical_el", center_el_deg),
        )

    az_offset = float(peak_estimate["az_error_deg"])
    el_offset = float(peak_estimate["el_error_deg"])
    return {
        "strategy": str(strategy),
        "samples": sample_list,
        "best_point": best_point,
        "peak_estimate": peak_estimate,
        "estimated_peak": peak_estimate,
        "center_az_deg": float(center_az_deg),
        "center_el_deg": float(center_el_deg),
        "az_offset_deg": az_offset,
        "el_offset_deg": el_offset,
        "error_trace": [peak_estimate],
    }


def scan_error_series(error_trace: Iterable[dict]) -> dict[str, list[float]]:
    """Convert an error trace to plot-ready series."""
    x_values: list[float] = []
    az_errors: list[float] = []
    el_errors: list[float] = []
    angular_errors: list[float] = []

    for index, point in enumerate(error_trace):
        x_values.append(float(index))
        az_error = _as_float(point.get("az_error_deg"), 0.0)
        el_error = _as_float(point.get("el_error_deg"), 0.0)
        az_errors.append(az_error)
        el_errors.append(el_error)
        center_el = _as_float(point.get("theoretical_el"), 0.0)
        angular_errors.append(
            _as_float(point.get("angular_error_deg"), angular_error_deg(az_error, el_error, center_el))
        )

    return {
        "x": x_values,
        "az_error_deg": az_errors,
        "el_error_deg": el_errors,
        "angular_error_deg": angular_errors,
    }

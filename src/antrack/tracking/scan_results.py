"""Common scan sample and result helpers."""

from __future__ import annotations

import math
import time
from typing import Iterable


def _as_float(value, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return float(default)
    if not math.isfinite(result):
        return float(default)
    return result


def angular_error_deg(az_error_deg: float, el_error_deg: float) -> float:
    """Return the 2D angular error magnitude in degrees."""
    return float(math.hypot(float(az_error_deg), float(el_error_deg)))


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
        "angular_error_deg": angular_error_deg(az_error, el_error),
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
            theoretical_az_deg=center_az_deg,
            theoretical_el_deg=center_el_deg,
        )

    az_offset = float(peak_estimate["az"]) - float(center_az_deg)
    el_offset = float(peak_estimate["el"]) - float(center_el_deg)
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

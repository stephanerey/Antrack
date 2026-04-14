"""Cross-scan helpers."""

from __future__ import annotations

from typing import Dict, List

import numpy as np


def _axis_points(center_deg: float, span_deg: float, step_deg: float) -> list[float]:
    span_deg = float(abs(span_deg))
    step_deg = float(max(1e-6, abs(step_deg)))
    half_span = span_deg / 2.0
    count = max(1, int(round(span_deg / step_deg)))
    values = np.linspace(center_deg - half_span, center_deg + half_span, count + 1)
    return [float(value) for value in values]


def generate_cross_points(
    center_az_deg: float,
    center_el_deg: float,
    span_deg: float,
    step_deg: float,
) -> Dict[str, List[dict]]:
    """Generate orthogonal 1D cuts around the requested center."""
    az_points = [
        {"axis": "az", "az": float(az_deg), "el": float(center_el_deg), "phase": "cross_az"}
        for az_deg in _axis_points(center_az_deg, span_deg, step_deg)
    ]
    el_points = [
        {"axis": "el", "az": float(center_az_deg), "el": float(el_deg), "phase": "cross_el"}
        for el_deg in _axis_points(center_el_deg, span_deg, step_deg)
    ]
    return {"azimuth": az_points, "elevation": el_points}


def estimate_cross_offset(az_curve: List[dict], el_curve: List[dict], center_az_deg: float, center_el_deg: float) -> dict:
    """Estimate the best offset directly from measured azimuth and elevation cuts."""
    if not az_curve or not el_curve:
        return {"az_offset_deg": 0.0, "el_offset_deg": 0.0}

    best_az = max(az_curve, key=lambda point: float(point.get("value", float("-inf"))))
    best_el = max(el_curve, key=lambda point: float(point.get("value", float("-inf"))))
    return {
        "az_offset_deg": float(best_az["az"]) - float(center_az_deg),
        "el_offset_deg": float(best_el["el"]) - float(center_el_deg),
        "best_az_point": best_az,
        "best_el_point": best_el,
    }

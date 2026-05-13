"""Noise peak estimators for measured scan samples."""

from __future__ import annotations

import math
from collections.abc import Iterable

import numpy as np

from antrack.tracking.scan_results import make_peak_estimate


def _finite_float(value, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return float(default)
    return result if math.isfinite(result) else float(default)


def _rounded_key(value: float) -> float:
    return round(float(value), 9)


def _corner_map(samples: Iterable[dict]) -> dict[tuple[float, float], dict]:
    points = {}
    for sample in samples:
        az = _finite_float(sample.get("az"), float("nan"))
        el = _finite_float(sample.get("el"), float("nan"))
        value = _finite_float(sample.get("value"), float("nan"))
        if not (math.isfinite(az) and math.isfinite(el) and math.isfinite(value)):
            continue
        key = (_rounded_key(az), _rounded_key(el))
        if key not in points or value > _finite_float(points[key].get("value"), float("-inf")):
            points[key] = sample
    return points


def find_best_four_point_cell(samples: Iterable[dict]) -> dict | None:
    """Return the best complete adjacent 4-point cell from a grid-like sample set."""
    points = _corner_map(samples)
    if not points:
        return None
    az_values = sorted({key[0] for key in points})
    el_values = sorted({key[1] for key in points})
    best_cell = None
    best_score = float("-inf")

    for az_index in range(len(az_values) - 1):
        for el_index in range(len(el_values) - 1):
            x0 = az_values[az_index]
            x1 = az_values[az_index + 1]
            y0 = el_values[el_index]
            y1 = el_values[el_index + 1]
            keys = [(x0, y0), (x1, y0), (x0, y1), (x1, y1)]
            if any(key not in points for key in keys):
                continue
            corners = [points[key] for key in keys]
            values = np.asarray([corner["value"] for corner in corners], dtype=np.float64)
            score = float(np.max(values) + np.mean(values) * 1e-3)
            if score > best_score:
                best_score = score
                best_cell = {
                    "az_min": float(x0),
                    "az_max": float(x1),
                    "el_min": float(y0),
                    "el_max": float(y1),
                    "corners": {
                        "bottom_left": points[(x0, y0)],
                        "bottom_right": points[(x1, y0)],
                        "top_left": points[(x0, y1)],
                        "top_right": points[(x1, y1)],
                    },
                }
    return best_cell


def estimate_four_point_divergence_peak(samples: Iterable[dict]) -> dict | None:
    """Estimate an in-cell peak from the best measured 4-point cell.

    With only four corners this is intentionally a confidence-scored estimate,
    not proof that a true maximum exists inside the cell. The estimator uses
    linear-domain weights from the measured dB values and exposes gradient and
    divergence-like diagnostics for later strategy decisions.
    """
    cell = find_best_four_point_cell(samples)
    if cell is None:
        return None

    corners = cell["corners"]
    ordered = [
        corners["bottom_left"],
        corners["bottom_right"],
        corners["top_left"],
        corners["top_right"],
    ]
    values = np.asarray([corner["value"] for corner in ordered], dtype=np.float64)
    max_value = float(np.max(values))
    weights = np.power(10.0, (values - max_value) / 10.0)
    weight_sum = float(np.sum(weights))
    if weight_sum <= 0.0 or not math.isfinite(weight_sum):
        weights = np.ones_like(values)
        weight_sum = float(weights.size)

    az_values = np.asarray([corner["az"] for corner in ordered], dtype=np.float64)
    el_values = np.asarray([corner["el"] for corner in ordered], dtype=np.float64)
    peak_az = float(np.sum(az_values * weights) / weight_sum)
    peak_el = float(np.sum(el_values * weights) / weight_sum)

    left_avg = float((values[0] + values[2]) * 0.5)
    right_avg = float((values[1] + values[3]) * 0.5)
    bottom_avg = float((values[0] + values[1]) * 0.5)
    top_avg = float((values[2] + values[3]) * 0.5)
    gradient_az = right_avg - left_avg
    gradient_el = top_avg - bottom_avg

    az_center = (float(cell["az_min"]) + float(cell["az_max"])) * 0.5
    el_center = (float(cell["el_min"]) + float(cell["el_max"])) * 0.5
    half_az = max(1e-12, (float(cell["az_max"]) - float(cell["az_min"])) * 0.5)
    half_el = max(1e-12, (float(cell["el_max"]) - float(cell["el_min"])) * 0.5)
    normalized_radius = max(abs((peak_az - az_center) / half_az), abs((peak_el - el_center) / half_el))
    interior_score = float(max(0.0, min(1.0, 1.0 - normalized_radius)))
    value_span = float(np.max(values) - np.min(values))
    dynamic_score = float(value_span / (value_span + 3.0)) if value_span > 0.0 else 0.0
    balance_score = float((1.0 - (np.max(weights) / weight_sum)) * (4.0 / 3.0))
    confidence = float(max(0.0, min(1.0, 0.25 + 0.75 * dynamic_score * max(interior_score, balance_score))))

    theoretical_az = float(np.mean([_finite_float(corner.get("theoretical_az"), az_center) for corner in ordered]))
    theoretical_el = float(np.mean([_finite_float(corner.get("theoretical_el"), el_center) for corner in ordered]))
    peak = make_peak_estimate(
        {
            "az": peak_az,
            "el": peak_el,
            "value": max_value,
            "timestamp": max(_finite_float(corner.get("timestamp"), 0.0) for corner in ordered),
        },
        method="four_point_divergence",
        confidence=confidence,
        theoretical_az_deg=theoretical_az,
        theoretical_el_deg=theoretical_el,
    )
    peak.update(
        {
            "cell": cell,
            "gradient_az_db": gradient_az,
            "gradient_el_db": gradient_el,
            "divergence_score": interior_score,
            "value_span_db": value_span,
        }
    )
    return peak

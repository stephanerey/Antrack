"""Grid scan point generators."""

from __future__ import annotations

from typing import List

import numpy as np


def _axis_points(center_deg: float, span_deg: float, step_deg: float) -> list[float]:
    span_deg = float(abs(span_deg))
    step_deg = float(max(1e-6, abs(step_deg)))
    half_span = span_deg / 2.0
    count = max(1, int(round(span_deg / step_deg)))
    values = np.linspace(center_deg - half_span, center_deg + half_span, count + 1)
    return [float(value) for value in values]


def generate_grid_points(
    center_az_deg: float,
    center_el_deg: float,
    span_az_deg: float,
    span_el_deg: float,
    step_deg: float,
    *,
    order: str = "zigzag",
    phase: str = "main",
) -> List[dict]:
    """Generate a 2D raster scan around the requested center."""
    az_values = _axis_points(center_az_deg, span_az_deg, step_deg)
    el_values = _axis_points(center_el_deg, span_el_deg, step_deg)
    points: list[dict] = []
    normalized_order = str(order or "zigzag").strip().lower()
    for row_index, el_deg in enumerate(el_values):
        row_az = list(az_values)
        if normalized_order in {"zigzag", "serpentine", "serpentin"} and row_index % 2 == 1:
            row_az.reverse()
        for col_index, az_deg in enumerate(row_az):
            points.append(
                {
                    "az": float(az_deg),
                    "el": float(el_deg),
                    "phase": phase,
                    "row": int(row_index),
                    "col": int(col_index),
                }
            )
    return points


def generate_two_pass_grid_points(
    center_az_deg: float,
    center_el_deg: float,
    coarse_span_deg: float,
    coarse_step_deg: float,
    fine_span_deg: float,
    fine_step_deg: float,
    *,
    order: str = "zigzag",
) -> list[dict]:
    """Generate a coarse grid followed by a fine grid centered on the same location."""
    coarse = generate_grid_points(
        center_az_deg,
        center_el_deg,
        coarse_span_deg,
        coarse_span_deg,
        coarse_step_deg,
        order=order,
        phase="coarse",
    )
    fine = generate_grid_points(
        center_az_deg,
        center_el_deg,
        fine_span_deg,
        fine_span_deg,
        fine_step_deg,
        order=order,
        phase="fine",
    )
    return coarse + fine

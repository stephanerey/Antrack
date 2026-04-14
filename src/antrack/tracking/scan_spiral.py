"""Spiral scan helpers."""

from __future__ import annotations

import math
from typing import List

import numpy as np


def generate_spiral_points(
    center_az_deg: float,
    center_el_deg: float,
    span_deg: float,
    radial_step_deg: float,
    *,
    turns: int | None = None,
    points_per_turn: int = 36,
) -> List[dict]:
    """Generate an Archimedean spiral centered on the requested point."""
    radius_max = float(abs(span_deg)) / 2.0
    radial_step_deg = float(max(1e-6, abs(radial_step_deg)))
    turns = int(turns or max(1, int(math.ceil(radius_max / radial_step_deg))))
    points_per_turn = int(max(12, points_per_turn))
    total_points = max(points_per_turn, turns * points_per_turn + 1)
    points: list[dict] = []
    for index in range(total_points):
        t = 2.0 * math.pi * turns * index / float(max(1, total_points - 1))
        radius = min(radius_max, radial_step_deg * t / (2.0 * math.pi))
        az = float(center_az_deg + radius * math.cos(t))
        el = float(center_el_deg + radius * math.sin(t))
        points.append({"az": az, "el": el, "phase": "spiral", "radius": radius, "theta": t})
    return points


def spiral_samples_to_grid(samples: List[dict], step_deg: float) -> dict:
    """Project irregular spiral samples onto a regular grid with mean aggregation."""
    if not samples:
        return {"az_values": np.array([]), "el_values": np.array([]), "grid": np.array([[]], dtype=np.float32)}

    step_deg = float(max(1e-6, abs(step_deg)))
    az = np.asarray([point["az"] for point in samples], dtype=np.float64)
    el = np.asarray([point["el"] for point in samples], dtype=np.float64)
    values = np.asarray([point.get("value", np.nan) for point in samples], dtype=np.float64)

    az_values = np.arange(np.min(az), np.max(az) + step_deg * 0.5, step_deg, dtype=np.float64)
    el_values = np.arange(np.min(el), np.max(el) + step_deg * 0.5, step_deg, dtype=np.float64)
    grid = np.full((len(el_values), len(az_values)), np.nan, dtype=np.float32)
    counts = np.zeros_like(grid, dtype=np.int32)

    az_idx = np.clip(np.round((az - az_values[0]) / step_deg).astype(int), 0, len(az_values) - 1)
    el_idx = np.clip(np.round((el - el_values[0]) / step_deg).astype(int), 0, len(el_values) - 1)
    for idx, value in enumerate(values):
        row = el_idx[idx]
        col = az_idx[idx]
        if np.isnan(value):
            continue
        if np.isnan(grid[row, col]):
            grid[row, col] = float(value)
        else:
            grid[row, col] = float(grid[row, col] + value)
        counts[row, col] += 1

    nonzero = counts > 0
    grid[nonzero] = (grid[nonzero] / counts[nonzero]).astype(np.float32, copy=False)
    return {"az_values": az_values, "el_values": el_values, "grid": grid}

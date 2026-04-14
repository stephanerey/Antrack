"""Lightweight filter helpers used by the SDR backend."""

from __future__ import annotations

import numpy as np


def apply_ema(
    values: np.ndarray,
    previous: np.ndarray | None,
    *,
    alpha: float,
) -> np.ndarray:
    """Apply an exponential moving average over a spectrum trace."""
    current = np.asarray(values, dtype=np.float32)
    alpha = float(np.clip(alpha, 0.01, 1.0))
    if previous is None:
        return current.copy()
    prev = np.asarray(previous, dtype=np.float32)
    if prev.shape != current.shape:
        return current.copy()
    return (alpha * current + (1.0 - alpha) * prev).astype(np.float32, copy=False)

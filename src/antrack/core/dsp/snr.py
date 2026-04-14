"""SNR helpers for SDR display and scan workflows."""

from __future__ import annotations

import math
from typing import Literal

import numpy as np


def compute_snr(
    spectrum_db: np.ndarray,
    mode: Literal["relative", "absolute"] = "relative",
    noise_floor_ref_db: float | None = None,
) -> float:
    """Compute a relative or absolute SNR in dB from a spectrum trace."""
    spectrum = np.asarray(spectrum_db, dtype=np.float32)
    if spectrum.size == 0:
        return float("nan")

    finite = spectrum[np.isfinite(spectrum)]
    if finite.size == 0:
        return float("nan")

    peak_db = float(np.max(finite))
    normalized_mode = str(mode or "relative").strip().lower()
    if normalized_mode == "absolute":
        if noise_floor_ref_db is None or not math.isfinite(float(noise_floor_ref_db)):
            return float("nan")
        return peak_db - float(noise_floor_ref_db)

    noise_floor_db = float(np.median(finite))
    return peak_db - noise_floor_db

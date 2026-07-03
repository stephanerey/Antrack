"""SNR and band-power helpers for SDR display and scan workflows."""

from __future__ import annotations

import math
from typing import Literal

import numpy as np

_POWER_EPSILON = 1e-24


def db_to_linear_power(values_db: np.ndarray | float) -> np.ndarray:
    """Convert dB power values to linear power."""
    values = np.asarray(values_db, dtype=np.float64)
    linear = np.zeros_like(values, dtype=np.float64)
    finite = np.isfinite(values)
    linear[finite] = np.power(10.0, values[finite] / 10.0)
    return linear


def linear_power_to_db(values_linear: np.ndarray | float) -> np.ndarray | float:
    """Convert linear power values to dB while keeping zero-safe floors."""
    values = np.asarray(values_linear, dtype=np.float64)
    safe = np.maximum(values, _POWER_EPSILON)
    converted = 10.0 * np.log10(safe)
    if converted.ndim == 0:
        return float(converted)
    return converted


def bin_width_to_density_offset_db(bin_width_hz: float) -> float:
    """Return the dB offset between dB/bin and dB/Hz for a given bin width."""
    width_hz = float(max(_POWER_EPSILON, abs(float(bin_width_hz))))
    return float(10.0 * math.log10(width_hz))


def convert_db_per_bin_to_db_per_hz(values_db: np.ndarray | float, bin_width_hz: float) -> np.ndarray | float:
    """Convert power values from dB/bin to dB/Hz."""
    return np.asarray(values_db, dtype=np.float64) - bin_width_to_density_offset_db(bin_width_hz)


def average_power_spectrum_db(traces_db: np.ndarray, axis: int = 0) -> np.ndarray:
    """Average dB spectra in the linear domain and return the result in dB."""
    traces = np.asarray(traces_db, dtype=np.float64)
    if traces.size == 0:
        return np.asarray([], dtype=np.float32)
    mean_linear = np.mean(db_to_linear_power(traces), axis=axis)
    return np.asarray(linear_power_to_db(mean_linear), dtype=np.float32)


def compute_band_power_metrics(
    spectrum_db: np.ndarray,
    *,
    bin_width_hz: float,
    bandwidth_hz: float | None = None,
) -> dict[str, float]:
    """Return integrated, per-bin, and density band-power metrics for a spectrum slice."""
    spectrum = np.asarray(spectrum_db, dtype=np.float64)
    finite = spectrum[np.isfinite(spectrum)]
    if finite.size == 0:
        return {
            "integrated_db": float("nan"),
            "per_bin_db": float("nan"),
            "per_hz_db": float("nan"),
            "bin_count": 0.0,
        }

    linear = db_to_linear_power(finite)
    per_bin_db = float(linear_power_to_db(np.mean(linear)))
    per_hz_db = float(per_bin_db - bin_width_to_density_offset_db(bin_width_hz))
    effective_bandwidth_hz = float(
        max(
            abs(float(bandwidth_hz)) if bandwidth_hz is not None else float(finite.size) * float(bin_width_hz),
            1.0,
        )
    )
    return {
        "integrated_db": float(per_hz_db + (10.0 * math.log10(effective_bandwidth_hz))),
        "per_bin_db": per_bin_db,
        "per_hz_db": per_hz_db,
        "bin_count": float(finite.size),
    }


def compute_trace_band_power_metrics(
    traces_db: np.ndarray,
    freqs_hz: np.ndarray,
    *,
    center_hz: float,
    bandwidth_hz: float,
    bin_width_hz: float | None = None,
) -> dict[str, float]:
    """Return band-power metrics for one or more traces around a center frequency."""
    freqs = np.asarray(freqs_hz, dtype=np.float64)
    traces = np.asarray(traces_db, dtype=np.float64)
    if freqs.ndim != 1 or freqs.size == 0:
        return {
            "integrated_db": float("nan"),
            "per_bin_db": float("nan"),
            "per_hz_db": float("nan"),
            "bin_count": 0.0,
        }

    if traces.ndim == 1:
        traces = traces.reshape(1, -1)
    if traces.ndim != 2 or traces.size == 0 or traces.shape[1] != freqs.shape[0]:
        return {
            "integrated_db": float("nan"),
            "per_bin_db": float("nan"),
            "per_hz_db": float("nan"),
            "bin_count": 0.0,
        }

    if bin_width_hz is None or not math.isfinite(float(bin_width_hz)) or float(bin_width_hz) <= 0.0:
        if freqs.size > 1:
            finite_diffs = np.abs(np.diff(freqs))
            finite_diffs = finite_diffs[np.isfinite(finite_diffs) & (finite_diffs > 0.0)]
            bin_width_hz = float(np.median(finite_diffs)) if finite_diffs.size else 1.0
        else:
            bin_width_hz = 1.0
    else:
        bin_width_hz = float(bin_width_hz)

    half_bw = float(max(1.0, abs(float(bandwidth_hz)))) * 0.5
    center_hz = float(center_hz)
    mask = (freqs >= center_hz - half_bw) & (freqs <= center_hz + half_bw)
    if not np.any(mask):
        nearest = int(np.argmin(np.abs(freqs - center_hz)))
        mask = np.zeros_like(freqs, dtype=bool)
        mask[nearest] = True

    band_traces = traces[:, mask]
    if band_traces.size == 0:
        return {
            "integrated_db": float("nan"),
            "per_bin_db": float("nan"),
            "per_hz_db": float("nan"),
            "bin_count": 0.0,
        }

    averaged_band = np.atleast_1d(average_power_spectrum_db(band_traces, axis=0))
    effective_bandwidth_hz = float(max(bin_width_hz, int(np.count_nonzero(mask)) * bin_width_hz))
    return compute_band_power_metrics(
        averaged_band,
        bin_width_hz=bin_width_hz,
        bandwidth_hz=effective_bandwidth_hz,
    )


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

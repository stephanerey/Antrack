"""Spectrum and FFT related helpers."""

from __future__ import annotations

import numpy as np

_POWER_DB_FLOOR = 1e-24

WINDOW_ENBW_FACTORS = {
    "rectangular": 1.0,
    "hann": 1.5,
    "blackman": 1.73,
}


def select_fft_size(sample_rate_hz: float, buffer_size: int) -> int:
    """Select a display FFT size without overshooting the available IQ block."""
    sample_rate_hz = float(max(1.0, sample_rate_hz))
    buffer_size = int(max(1024, buffer_size))
    target_rbw_hz = 40.0 if sample_rate_hz <= 4_000_000.0 else 180.0
    max_fft = 262_144 if sample_rate_hz <= 4_000_000.0 else 131_072
    n_target = int(2 ** np.ceil(np.log2(sample_rate_hz / target_rbw_hz)))
    n_target = max(2048, n_target)
    n_target = min(max_fft, max(buffer_size, n_target))
    return int(max(1024, 2 ** int(np.floor(np.log2(max(2, n_target))))))


def fft_max_for_sample_rate(sample_rate_hz: float, buffer_size: int) -> int:
    """Return the largest practical display FFT size for the current sample rate."""
    sample_rate_hz = float(max(1.0, sample_rate_hz))
    _ = int(max(1024, buffer_size))
    if sample_rate_hz <= 2_000_000.0:
        max_fft = 8_388_608
    elif sample_rate_hz <= 4_000_000.0:
        max_fft = 4_194_304
    else:
        max_fft = 2_097_152
    return int(max_fft)


def make_window(length: int, window_type: str = "blackman") -> np.ndarray:
    """Return the requested FFT window as float32."""
    length = int(max(1, length))
    if length == 1:
        return np.ones(1, dtype=np.float32)
    normalized = str(window_type or "blackman").strip().lower()
    if normalized == "rectangular":
        return np.ones(length, dtype=np.float32)
    if normalized == "hann":
        return np.hanning(length).astype(np.float32, copy=False)
    return np.blackman(length).astype(np.float32, copy=False)


def blackman_window(length: int) -> np.ndarray:
    """Backward-compatible Blackman window helper."""
    return make_window(length, "blackman")


def compute_power_spectrum_db(
    iq_data: np.ndarray,
    fft_size: int,
    window: np.ndarray | None = None,
    window_power: float | None = None,
) -> np.ndarray:
    """Compute a centered power spectrum in dB for the provided IQ samples."""
    fft_size = int(max(8, fft_size))
    samples = np.asarray(iq_data, dtype=np.complex64)
    if samples.size > fft_size:
        samples = samples[-fft_size:]
    sample_count = int(max(1, samples.size))

    if window is None or int(np.asarray(window).size) != sample_count:
        window = blackman_window(sample_count)
        window_power = None
    else:
        window = np.asarray(window, dtype=np.float32)
    if window_power is None:
        coherent_gain = float(np.sum(window))
        window_power = coherent_gain * coherent_gain
    if window_power <= 1e-24:
        window_power = 1.0

    windowed = samples * window
    if sample_count < fft_size:
        windowed = np.pad(windowed, (0, fft_size - sample_count), mode="constant")
    fft_data = np.fft.fft(windowed, fft_size)
    power_linear = (np.abs(fft_data) ** 2).astype(np.float32, copy=False)
    power_linear = power_linear / max(1.0, window_power)
    power_db = 10.0 * np.log10(power_linear + _POWER_DB_FLOOR)
    return np.fft.fftshift(power_db).astype(np.float32, copy=False)


def frequency_axis(n_bins: int, sample_rate_hz: float, center_frequency_hz: float) -> np.ndarray:
    """Return the frequency axis aligned with a centered FFT spectrum."""
    freqs = np.fft.fftshift(np.fft.fftfreq(int(n_bins), 1.0 / float(sample_rate_hz))).astype(np.float64)
    freqs += float(center_frequency_hz)
    return freqs

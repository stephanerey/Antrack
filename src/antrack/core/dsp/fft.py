"""Spectrum and FFT related helpers."""

from __future__ import annotations

import numpy as np


def select_fft_size(sample_rate_hz: float, buffer_size: int) -> int:
    """Select a display FFT size without overshooting the available IQ block."""
    sample_rate_hz = float(max(1.0, sample_rate_hz))
    buffer_size = int(max(1024, buffer_size))
    target_rbw_hz = 40.0 if sample_rate_hz <= 4_000_000.0 else 180.0
    max_fft = 65_536 if sample_rate_hz <= 4_000_000.0 else 32_768
    n_target = int(2 ** np.ceil(np.log2(sample_rate_hz / target_rbw_hz)))
    n_target = max(2048, n_target)
    n_target = min(buffer_size, n_target, max_fft)
    return int(max(1024, 2 ** int(np.floor(np.log2(max(2, n_target))))))


def fft_max_for_sample_rate(sample_rate_hz: float, buffer_size: int) -> int:
    """Return the largest practical display FFT size for the current sample rate."""
    sample_rate_hz = float(max(1.0, sample_rate_hz))
    buffer_size = int(max(1024, buffer_size))
    max_fft = 65_536 if sample_rate_hz <= 4_000_000.0 else 32_768
    return int(min(buffer_size, max_fft))


def blackman_window(length: int) -> np.ndarray:
    """Return a Blackman window as float32."""
    length = int(max(1, length))
    if length == 1:
        return np.ones(1, dtype=np.float32)
    return np.blackman(length).astype(np.float32, copy=False)


def compute_power_spectrum_db(
    iq_data: np.ndarray,
    fft_size: int,
    window: np.ndarray | None = None,
    window_power: float | None = None,
) -> np.ndarray:
    """Compute a centered power spectrum in dB for the provided IQ samples."""
    fft_size = int(max(8, fft_size))
    samples = np.asarray(iq_data, dtype=np.complex64)
    if samples.size < fft_size:
        samples = np.pad(samples, (0, fft_size - samples.size), mode="constant")
    elif samples.size > fft_size:
        samples = samples[:fft_size]

    window = blackman_window(fft_size) if window is None else np.asarray(window, dtype=np.float32)
    if window_power is None:
        window_power = float(np.sum(window * window))
    if window_power <= 1e-12:
        window_power = 1.0

    fft_data = np.fft.fft(samples * window, fft_size)
    power_linear = (np.abs(fft_data) ** 2).astype(np.float32, copy=False)
    power_linear = power_linear / max(1.0, window_power * float(fft_size))
    power_db = 10.0 * np.log10(power_linear + 1e-12)
    return np.fft.fftshift(power_db).astype(np.float32, copy=False)


def frequency_axis(n_bins: int, sample_rate_hz: float, center_frequency_hz: float) -> np.ndarray:
    """Return the frequency axis aligned with a centered FFT spectrum."""
    freqs = np.fft.fftshift(np.fft.fftfreq(int(n_bins), 1.0 / float(sample_rate_hz))).astype(np.float64)
    freqs += float(center_frequency_hz)
    return freqs

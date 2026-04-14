import math

import numpy as np

from antrack.core.dsp.snr import compute_snr
from antrack.core.instruments.sdr_client import SdrClient


def _dummy_client() -> SdrClient:
    client = SdrClient(
        settings={
            "SDR": {
                "sample_rate_hz": 2_000_000,
                "center_freq_hz": 1_420_000_000,
                "buffer_size": 8192,
                "fft_fps": 10,
                "spectrum_trace_alpha": 0.5,
            }
        }
    )
    client.mode = "dummy"
    client._sdr = None
    return client


def test_compute_snr_relative_and_absolute():
    spectrum = np.array([-110.0, -100.0, -87.0, -99.0, -101.0], dtype=np.float32)
    assert compute_snr(spectrum, "relative") == 13.0
    assert compute_snr(spectrum, "absolute", -105.0) == 18.0


def test_sdr_client_compute_spectrum_returns_finite_trace():
    client = _dummy_client()
    iq = client._generate_dummy_iq_block()
    spectrum = client.compute_spectrum(iq)
    assert spectrum.ndim == 1
    assert spectrum.size == client.fft_size
    assert np.isfinite(spectrum).all()


def test_measure_band_power_prefers_signal_band_in_dummy_mode():
    client = _dummy_client()
    in_band_db = client.measure_band_power(120_000.0, 40_000.0, 0.05)
    off_band_db = client.measure_band_power(700_000.0, 40_000.0, 0.05)
    assert math.isfinite(in_band_db)
    assert math.isfinite(off_band_db)
    assert in_band_db > off_band_db


def test_measure_band_power_is_reasonably_stable_in_dummy_mode():
    client = _dummy_client()
    first = client.measure_band_power(120_000.0, 40_000.0, 0.05)
    second = client.measure_band_power(120_000.0, 40_000.0, 0.05)
    assert abs(first - second) < 6.0

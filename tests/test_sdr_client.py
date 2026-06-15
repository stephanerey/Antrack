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


class RecordingThreadManager:
    def __init__(self):
        self.started = []
        self.stopped = []
        self.threads = {}

    def start_thread(self, name, func, *args, **kwargs):
        self.started.append(name)
        return None

    def stop_thread(self, name):
        self.stopped.append(name)

    def get_worker(self, _name):
        return None


class FakeFuture:
    def __init__(self, done: bool):
        self._done = done
        self._callbacks = []

    def done(self):
        return self._done

    def add_done_callback(self, callback):
        self._callbacks.append(callback)


class RecordingExecutorThreadManager(RecordingThreadManager):
    def __init__(self):
        super().__init__()
        self.submissions = []

    def submit_task(self, func, *args, **kwargs):
        self.submissions.append((func, args, kwargs))
        return FakeFuture(done=False)


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


def test_cache_iq_block_wakes_scheduler_without_rendering_immediately():
    client = _dummy_client()
    client._spectrum_wakeup.clear()

    client._cache_iq_block(client._generate_dummy_iq_block())

    assert client._spectrum_wakeup.is_set() is True


def test_schedule_spectrum_publish_reports_pending_when_future_in_flight():
    thread_manager = RecordingExecutorThreadManager()
    client = SdrClient(
        settings={"SDR": {"sample_rate_hz": 2_000_000, "buffer_size": 8192, "fft_size": 2048}},
        thread_manager=thread_manager,
    )
    client.mode = "dummy"
    client._cache_iq_block(client._generate_dummy_iq_block())

    first = client._schedule_spectrum_publish(timestamp=1.0)
    second = client._schedule_spectrum_publish(timestamp=2.0)

    assert first == "submitted"
    assert second == "pending"
    assert len(thread_manager.submissions) == 1


def test_sdr_client_cpu_optimized_caps_fft_runtime_settings():
    client = SdrClient(
        settings={
            "PERFORMANCE": {
                "cpu_optimized": True,
                "fft_fps": 5.0,
                "plot_refresh_fps": 4.0,
                "max_fft_size": 2048,
            },
            "SDR": {
                "sample_rate_hz": 2_000_000,
                "buffer_size": 8192,
                "fft_fps": 20.0,
                "fft_size": 65536,
                "plot_refresh_fps": 20.0,
            },
        }
    )

    assert client.cpu_optimized is True
    assert client.fft_fps == 5.0
    assert client.plot_refresh_fps == 4.0
    assert client.fft_size == 2048


def test_sdr_client_start_uses_single_worker_thread():
    thread_manager = RecordingThreadManager()
    client = SdrClient(settings={"SDR": {}}, thread_manager=thread_manager)
    client.mode = "dummy"

    client.start()
    client.stop()

    assert thread_manager.started == ["SdrStream"]
    assert thread_manager.stopped == ["SdrStream"]


def test_measure_band_power_stays_close_in_cpu_optimized_mode():
    baseline = _dummy_client()
    optimized = SdrClient(
        settings={
            "PERFORMANCE": {
                "cpu_optimized": True,
                "fft_fps": 5.0,
                "plot_refresh_fps": 4.0,
                "max_fft_size": 2048,
            },
            "SDR": {
                "sample_rate_hz": 2_000_000,
                "center_freq_hz": 1_420_000_000,
                "buffer_size": 8192,
                "fft_fps": 10,
                "fft_size": 65536,
                "spectrum_trace_alpha": 0.5,
            },
        }
    )
    optimized.mode = "dummy"
    optimized._sdr = None

    baseline_power = baseline.measure_band_power(120_000.0, 40_000.0, 0.05)
    optimized_power = optimized.measure_band_power(120_000.0, 40_000.0, 0.05)

    assert abs(baseline_power - optimized_power) < 8.0

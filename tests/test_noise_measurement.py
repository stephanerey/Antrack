from antrack.core.dsp.snr import compute_trace_band_power_metrics
from antrack.gui.noise_measurement_state import NoiseMeasurementState


def test_compute_trace_band_power_metrics_averages_band_in_linear_domain():
    traces = [
        [-100.0, -90.0, -80.0, -90.0, -100.0],
        [-100.0, -88.0, -78.0, -88.0, -100.0],
    ]
    freqs = [99.0, 100.0, 101.0, 102.0, 103.0]

    metrics = compute_trace_band_power_metrics(
        traces,
        freqs,
        center_hz=101.0,
        bandwidth_hz=3.0,
        bin_width_hz=1.0,
    )

    assert metrics["bin_count"] == 3.0
    assert round(metrics["per_bin_db"], 3) == -82.865
    assert round(metrics["integrated_db"], 3) == -78.094


def test_noise_measurement_state_captures_reference_and_cycles_windows():
    state = NoiseMeasurementState()

    assert state.current_window_s == 30.0
    assert state.set_relative_mode(True) is False

    assert state.update_absolute(-92.5, timestamp_s=100.0) is True
    assert state.set_relative_mode(True) is True
    assert state.reference_absolute_db == -92.5
    assert state.relative_mode is True
    assert state.append_history_point(timestamp_s=100.0) is True

    state.update_absolute(-89.0, timestamp_s=110.0)
    assert state.append_history_point(timestamp_s=110.0) is True
    xs, ys = state.plot_series(now_s=110.0)

    assert xs == [100.0, 110.0]
    assert ys == [0.0, 3.5]
    assert state.relative_db == 3.5
    assert state.cycle_window() == 60.0
    assert state.cycle_window() == 10.0


def test_noise_measurement_state_prunes_old_history_and_clears():
    state = NoiseMeasurementState(history_retention_s=120.0)

    state.update_absolute(-95.0, timestamp_s=0.0)
    state.append_history_point(timestamp_s=0.0)
    state.update_absolute(-90.0, timestamp_s=130.0)
    state.append_history_point(timestamp_s=130.0)
    xs, ys = state.plot_series(now_s=130.0)

    assert xs == [130.0]
    assert ys == [-90.0]

    state.clear_history()
    xs, ys = state.plot_series(now_s=130.0)
    assert xs == []
    assert ys == []


def test_noise_measurement_state_ignores_invalid_updates_and_preserves_last_value():
    state = NoiseMeasurementState()

    assert state.update_absolute(-91.0, timestamp_s=10.0) is True
    state.append_history_point(timestamp_s=10.0)

    assert state.update_absolute(None, timestamp_s=11.0) is False
    assert state.current_absolute_db == -91.0

    assert state.update_absolute(float("nan"), timestamp_s=12.0) is False
    assert state.current_absolute_db == -91.0


def test_noise_measurement_state_deduplicates_fast_identical_history_points():
    state = NoiseMeasurementState()

    assert state.update_absolute(-90.0, timestamp_s=100.0) is True
    assert state.append_history_point(timestamp_s=100.0) is True
    assert state.append_history_point(timestamp_s=100.05) is False
    assert state.append_history_point(timestamp_s=100.2) is True

    xs, ys = state.plot_series(now_s=101.0)
    assert xs == [100.0, 100.2]
    assert ys == [-90.0, -90.0]

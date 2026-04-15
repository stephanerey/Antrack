import math

import numpy as np

from antrack.core.data_storage import DataStorage
from antrack.core.dsp.snr import average_power_spectrum_db, compute_band_power_metrics


def test_compute_band_power_metrics_reports_integrated_and_per_bin_values():
    metrics = compute_band_power_metrics(np.array([-100.0, -100.0], dtype=np.float32))
    assert metrics["bin_count"] == 2.0
    assert math.isclose(metrics["per_bin_db"], -100.0, abs_tol=1e-6)
    assert math.isclose(metrics["integrated_db"], -96.98970004336019, rel_tol=1e-6)


def test_average_power_spectrum_db_uses_linear_domain_average():
    traces = np.array([[-90.0, -90.0], [-100.0, -100.0]], dtype=np.float32)
    averaged = average_power_spectrum_db(traces, axis=0)
    expected = 10.0 * np.log10((10.0 ** (-9.0) + 10.0 ** (-10.0)) / 2.0)
    assert averaged.shape == (2,)
    assert np.allclose(averaged, expected, rtol=1e-6, atol=1e-6)


def test_waterfall_stride_averages_lines_in_linear_power_and_rebuilds_history():
    storage = DataStorage(max_history_size=8, waterfall_max_bins=8, waterfall_time_stride=1)
    x = np.array([1.0, 2.0], dtype=np.float64)
    storage.update({"x": x, "y": np.array([-90.0, -90.0], dtype=np.float32)})
    storage.update({"x": x, "y": np.array([-100.0, -100.0], dtype=np.float32)})
    assert storage.waterfall_history is not None
    assert storage.waterfall_history.history_size == 2

    storage.set_waterfall_time_stride(2)

    assert storage.waterfall_history is not None
    assert storage.waterfall_history.history_size == 1
    averaged_line = storage.waterfall_history.get_recent(1)[0]
    expected = 10.0 * np.log10((10.0 ** (-9.0) + 10.0 ** (-10.0)) / 2.0)
    assert np.allclose(averaged_line, np.array([expected, expected], dtype=np.float32), rtol=1e-6, atol=1e-6)

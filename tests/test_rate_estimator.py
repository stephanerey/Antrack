import pytest

from antrack.core.antenna.rate_estimator import PositionRateEstimator


def test_position_rate_estimator_fits_slow_motion_over_window():
    estimator = PositionRateEstimator(window_s=3.0, min_dt_s=0.1, smoothing_alpha=1.0)

    estimator.add(0.0, 10.0, 20.0)
    estimator.add(1.0, 10.01, 20.02)
    az_rate, el_rate = estimator.add(2.0, 10.02, 20.04)

    assert az_rate == pytest.approx(0.01)
    assert el_rate == pytest.approx(0.02)


def test_position_rate_estimator_unwraps_azimuth_across_zero():
    estimator = PositionRateEstimator(window_s=3.0, min_dt_s=0.1, smoothing_alpha=1.0)

    estimator.add(0.0, 359.99, 10.0)
    az_rate, _el_rate = estimator.add(1.0, 0.01, 10.0)

    assert az_rate == pytest.approx(0.02)

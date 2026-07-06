import pytest

from antrack.core.axis.axis_protocol import deg_to_raw, raw_az_to_deg, raw_el_to_deg


def test_raw_azimuth_conversion():
    assert raw_az_to_deg(0) == pytest.approx(0.0)
    assert raw_az_to_deg(65535) == pytest.approx(360.0)
    assert raw_az_to_deg(32768) == pytest.approx(180.0027, abs=0.01)


def test_raw_elevation_conversion_wraps_signed_range():
    assert raw_el_to_deg(0) == pytest.approx(0.0)
    assert raw_el_to_deg(16384) == pytest.approx(90.0, abs=0.01)
    assert raw_el_to_deg(32768) == pytest.approx(-180.0, abs=0.01)
    assert raw_el_to_deg(49152) == pytest.approx(-90.0, abs=0.01)


def test_degree_roundtrip_stays_within_one_raw_step():
    for degrees in (0.0, 45.0, 90.0, 179.5, 270.0, 359.0):
        raw = deg_to_raw(degrees)
        assert raw_az_to_deg(raw) == pytest.approx(degrees % 360.0, abs=0.02)

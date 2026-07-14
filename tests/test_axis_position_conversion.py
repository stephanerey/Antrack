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


@pytest.mark.parametrize(
    ("raw", "expected_degrees"),
    [
        (0x0000, 0.0),
        (0x0001, 360.0 / 65535.0),
        (0xFFFF, -360.0 / 65535.0),
        (0xFFC0, -64.0 * 360.0 / 65535.0),
        (0x8000, -32768.0 * 360.0 / 65535.0),
        (0x7FFF, 32767.0 * 360.0 / 65535.0),
    ],
)
def test_raw_elevation_decodes_signed_16_bit_limits(raw, expected_degrees):
    assert raw_el_to_deg(raw) == pytest.approx(expected_degrees)


def test_raw_elevation_accepts_signed_tcp_payload_without_modulo():
    assert raw_el_to_deg(-64) == pytest.approx(-64.0 * 360.0 / 65535.0)


def test_degree_roundtrip_stays_within_one_raw_step():
    for degrees in (0.0, 45.0, 90.0, 179.5, 270.0, 359.0):
        raw = deg_to_raw(degrees)
        assert raw_az_to_deg(raw) == pytest.approx(degrees % 360.0, abs=0.02)

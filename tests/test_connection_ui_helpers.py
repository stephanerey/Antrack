from antrack.core.antenna.config import load_antenna_connection_config
from antrack.core.antenna.types import AntennaTelemetry
from antrack.gui.connection_ui import (
    axis_reference_valid,
    compute_axis_reference_indicator,
    format_antenna_endpoint_summary,
    format_axis_index_status,
    format_axis_index_tooltip,
)


def test_format_axis_index_status_for_axis_driver():
    assert format_axis_index_status("axis_driver", 0) == "NOT REF"
    assert format_axis_index_status("axis_driver", 1) == "REF"
    assert format_axis_index_status("axis_driver", 2) == "TRIG"
    assert format_axis_index_status("axis_driver", None) == "UNKNOWN"


def test_format_axis_index_status_returns_na_for_other_modes():
    assert format_axis_index_status("axis_server", 1) == "N/A"
    assert format_axis_index_status("pst_rotator", 2) == "N/A"


def test_axis_reference_valid_is_limited_to_axis_driver():
    assert axis_reference_valid("axis_driver", 1, 2) is True
    assert axis_reference_valid("axis_driver", 1, 0) is False
    assert axis_reference_valid("axis_server", 1, 2) is None


def test_axis_reference_indicator_latches_after_reference_acquisition():
    state, latched = compute_axis_reference_indicator("axis_driver", 0, False)
    assert state == "NOT REF"
    assert latched is False

    state, latched = compute_axis_reference_indicator("axis_driver", 2, latched)
    assert state == "TRIG"
    assert latched is True

    state, latched = compute_axis_reference_indicator("axis_driver", 1, latched)
    assert state == "REF"
    assert latched is True

    state, latched = compute_axis_reference_indicator("axis_driver", 2, latched)
    assert state == "PASSING"
    assert latched is True

    state, latched = compute_axis_reference_indicator("axis_driver", 0, latched)
    assert state == "REF"
    assert latched is True

    state, latched = compute_axis_reference_indicator("axis_driver", None, latched)
    assert state == "REF"
    assert latched is True


def test_axis_reference_indicator_keeps_blue_flash_after_trigger():
    state, latched = compute_axis_reference_indicator("axis_driver", 0, True, flash_active=True)
    assert state == "PASSING"
    assert latched is True


def test_axis_reference_indicator_reset_clears_latch():
    state, latched = compute_axis_reference_indicator("axis_driver", 1, False)
    assert state == "REF"
    assert latched is True

    state, latched = compute_axis_reference_indicator("axis_driver", 0, False)
    assert state == "NOT REF"
    assert latched is False


def test_format_axis_index_tooltip_matches_led_state():
    assert format_axis_index_tooltip("AZ", "axis_driver", 1, True) == "AZ index: referenced, raw=1"
    assert format_axis_index_tooltip("AZ", "axis_driver", 0, False) == "AZ index: not referenced, raw=0"
    assert format_axis_index_tooltip("EL", "axis_driver", 2, True, passing=True) == "EL index: referenced, raw=2, passing index"
    assert format_axis_index_tooltip("EL", "axis_driver", None, True) == "EL index: referenced, raw=unknown"
    assert format_axis_index_tooltip("EL", "pst_rotator", None) == "EL index: N/A"


def test_format_antenna_endpoint_summary_for_axis_server():
    config = load_antenna_connection_config(
        {
            "ANTENNA_CONNECTION": {"mode": "axis_server"},
            "AXIS_SERVER": {"ip_address": "192.168.1.48", "port": 10000},
        }
    )

    assert format_antenna_endpoint_summary(config, "axis_server") == "192.168.1.48:10000"


def test_format_antenna_endpoint_summary_for_axis_driver():
    config = load_antenna_connection_config(
        {
            "ANTENNA_CONNECTION": {"mode": "axis_driver"},
            "AXIS_DRIVER": {"comport": "COM11", "baudrate": 38400},
        }
    )

    assert format_antenna_endpoint_summary(config, "axis_driver") == "COM11 @ 38400"


def test_format_antenna_endpoint_summary_for_pst_rotator():
    config = load_antenna_connection_config(
        {
            "ANTENNA_CONNECTION": {"mode": "pst_rotator"},
            "PST_ROTATOR": {"host": "127.0.0.1", "udp_port": 12000},
        }
    )

    assert format_antenna_endpoint_summary(config, "pst_rotator") == "127.0.0.1 12000"


def test_antenna_telemetry_to_dict_includes_index_fields():
    telemetry = AntennaTelemetry(index_az=1, index_el=2)

    payload = telemetry.to_dict()

    assert payload["index_az"] == 1
    assert payload["index_el"] == 2

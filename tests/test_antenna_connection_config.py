import pytest

from antrack.core.antenna.config import AntennaConfigError, load_antenna_connection_config


def test_missing_antenna_connection_defaults_to_axis_server():
    config = load_antenna_connection_config({"AXIS_SERVER": {"ip_address": "192.168.1.48", "port": 10000}})
    assert config.mode.value == "axis_server"
    assert config.axis_server.host == "192.168.1.48"
    assert config.axis_server.port == 10000


def test_axis_driver_settings_are_parsed():
    config = load_antenna_connection_config(
        {
            "ANTENNA_CONNECTION": {"mode": "axis_driver"},
            "AXIS_DRIVER": {
                "comport": "COM7",
                "baudrate": 38400,
                "az_slave_address": 10,
                "el_slave_address": 20,
            },
        }
    )
    assert config.mode.value == "axis_driver"
    assert config.axis_driver.comport == "COM7"
    assert config.axis_driver.az_slave_address == 10
    assert config.axis_driver.el_slave_address == 20
    assert config.axis_driver.position_interval_s == 0.5
    assert config.axis_driver.status_interval_s == 2.0


def test_pst_rotator_settings_are_parsed():
    config = load_antenna_connection_config(
        {
            "ANTENNA_CONNECTION": {"mode": "pst_rotator"},
            "PST_ROTATOR": {
                "host": "127.0.0.1",
                "udp_port": 12000,
                "response_port": 12001,
            },
        }
    )
    assert config.mode.value == "pst_rotator"
    assert config.pst_rotator.host == "127.0.0.1"
    assert config.pst_rotator.udp_port == 12000
    assert config.pst_rotator.response_port == 12001


def test_unknown_mode_raises_config_error():
    with pytest.raises(AntennaConfigError):
        load_antenna_connection_config({"ANTENNA_CONNECTION": {"mode": "unknown"}})

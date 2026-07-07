import asyncio
import time

import pytest

from antrack.core.antenna.config import AxisDriverConnectionConfig
from antrack.core.antenna.types import AntennaConnectionState
from antrack.core.axis.axis_driver_backend import AxisDriverBackend
from antrack.core.axis.axis_driver_constants import COMMAND_REGISTER, COMMAND_TRIGGER_REGISTER, MOTION_STATE_REGISTER, RAW_POSITION_REGISTER, RELEASE_REGISTER, SPEED_REGISTER
from antrack.core.axis.modbus_rtu import append_crc, build_fc03_request, build_fc06_request


def _fc03_response(slave: int, value: int) -> bytes:
    return append_crc(bytes((slave, 0x03, 0x02, (value >> 8) & 0xFF, value & 0xFF)))


def _fc03_block_response(slave: int, values: list[int]) -> bytes:
    payload = bytes((slave, 0x03, 2 * len(values)))
    for value in values:
        payload += bytes(((value >> 8) & 0xFF, value & 0xFF))
    return append_crc(payload)


class FakeSerial:
    def __init__(self, responses):
        self.responses = responses
        self.writes = []
        self.last_request = b""
        self.pending_response = b""
        self.is_open = True

    def write(self, data: bytes) -> int:
        self.writes.append(data)
        self.last_request = data
        self.pending_response = self.responses.get(data, b"")
        return len(data)

    def read(self, size: int) -> bytes:
        chunk = self.pending_response[:size]
        self.pending_response = self.pending_response[size:]
        return chunk

    def reset_input_buffer(self) -> None:
        self.pending_response = b""

    def close(self) -> None:
        self.is_open = False


def _driver_responses():
    responses = {
        build_fc03_request(10, RELEASE_REGISTER, 1): _fc03_response(10, 150),
        build_fc03_request(20, RELEASE_REGISTER, 1): _fc03_response(20, 151),
        build_fc03_request(10, RAW_POSITION_REGISTER, 1): _fc03_response(10, 32768),
        build_fc03_request(20, RAW_POSITION_REGISTER, 1): _fc03_response(20, 16384),
        build_fc03_request(10, MOTION_STATE_REGISTER, 7): _fc03_block_response(10, [30, 0, 32768, 1, 150, 2, 0]),
        build_fc03_request(20, MOTION_STATE_REGISTER, 7): _fc03_block_response(20, [30, 0, 16384, 1, 151, 2, 0]),
    }
    for slave in (10, 20):
        for register, value in ((101, 30), (104, 1), (106, 2), (107, 0)):
            responses[build_fc03_request(slave, register, 1)] = _fc03_response(slave, value)
    for request in (
        build_fc06_request(10, SPEED_REGISTER, 25),
        build_fc06_request(10, COMMAND_TRIGGER_REGISTER, 1),
        build_fc06_request(10, COMMAND_REGISTER, 100),
        build_fc06_request(20, COMMAND_REGISTER, 100),
        build_fc06_request(10, COMMAND_REGISTER, 10),
        build_fc06_request(20, COMMAND_TRIGGER_REGISTER, 1),
    ):
        responses[request] = request
    return responses


def _backend_and_serial():
    fake_serial = FakeSerial(_driver_responses())
    config = AxisDriverConnectionConfig(
        comport="COM7",
        legacy_accept_short_fc6_response=False,
    )
    backend = AxisDriverBackend(config, serial_factory=lambda **_kwargs: fake_serial)
    return backend, fake_serial


def _backend_and_serial_with_responses(responses):
    fake_serial = FakeSerial(responses)
    config = AxisDriverConnectionConfig(
        comport="COM7",
        legacy_accept_short_fc6_response=False,
    )
    backend = AxisDriverBackend(config, serial_factory=lambda **_kwargs: fake_serial)
    return backend, fake_serial


def test_axis_driver_connect_reads_versions_and_position():
    backend, fake_serial = _backend_and_serial()

    asyncio.run(backend.connect())

    assert backend.is_connected()
    assert backend.versions.server_version == "AxisDriver"
    assert backend.versions.driver_version_az == "1.50"
    assert backend.telemetry.az_raw == 32768
    assert backend.telemetry.el_raw == 16384
    assert fake_serial.is_open is True


def test_axis_driver_disconnect_closes_serial():
    backend, fake_serial = _backend_and_serial()
    asyncio.run(backend.connect())

    asyncio.run(backend.disconnect())

    assert fake_serial.is_open is False
    assert backend.get_connection_state() == AntennaConnectionState.DISCONNECTED


def test_axis_driver_set_speed_and_move_emit_expected_frames():
    backend, fake_serial = _backend_and_serial()
    asyncio.run(backend.connect())
    fake_serial.writes.clear()

    asyncio.run(backend.set_az_speed(25))
    asyncio.run(backend.move_cw())

    assert fake_serial.writes == [
        build_fc06_request(10, SPEED_REGISTER, 25),
        build_fc06_request(10, COMMAND_TRIGGER_REGISTER, 1),
        build_fc06_request(10, COMMAND_REGISTER, 100),
        build_fc06_request(10, COMMAND_TRIGGER_REGISTER, 1),
    ]


def test_axis_driver_timeout_sets_degraded_state():
    backend, fake_serial = _backend_and_serial()
    asyncio.run(backend.connect())
    fake_serial.responses.clear()

    for _ in range(2):
        try:
            asyncio.run(backend.get_position())
        except Exception:
            pass

    assert backend.get_connection_state() == AntennaConnectionState.CONNECTED

    try:
        asyncio.run(backend.get_position())
    except Exception:
        pass

    assert backend.get_connection_state() == AntennaConnectionState.DEGRADED


def test_axis_driver_connect_tolerates_echoed_fc03_frames():
    responses = {}
    for request, response in _driver_responses().items():
        if request[1] == 0x03:
            responses[request] = request + response
        else:
            responses[request] = response

    backend, _fake_serial = _backend_and_serial_with_responses(responses)

    asyncio.run(backend.connect())

    assert backend.is_connected()
    assert backend.versions.driver_version_az == "1.50"
    assert backend.telemetry.az_raw == 32768


def test_axis_driver_single_register_status_mode_issues_individual_fc03_reads():
    backend, fake_serial = _backend_and_serial()
    asyncio.run(backend.connect())
    fake_serial.writes.clear()

    asyncio.run(backend.get_status())

    assert build_fc03_request(10, MOTION_STATE_REGISTER, 7) not in fake_serial.writes
    assert build_fc03_request(10, MOTION_STATE_REGISTER, 1) in fake_serial.writes
    assert build_fc03_request(10, RAW_POSITION_REGISTER, 1) in fake_serial.writes


def test_axis_driver_block_status_mode_issues_block_fc03_reads():
    fake_serial = FakeSerial(_driver_responses())
    config = AxisDriverConnectionConfig(
        comport="COM7",
        legacy_accept_short_fc6_response=False,
        status_read_mode="block",
    )
    backend = AxisDriverBackend(config, serial_factory=lambda **_kwargs: fake_serial)
    asyncio.run(backend.connect())
    fake_serial.writes.clear()

    asyncio.run(backend.get_status())

    assert build_fc03_request(10, MOTION_STATE_REGISTER, 7) in fake_serial.writes
    assert build_fc03_request(10, MOTION_STATE_REGISTER, 1) not in fake_serial.writes


def test_axis_driver_background_timeout_is_relaxed_but_below_command_timeout():
    backend, _fake_serial = _backend_and_serial()

    timeout_s = backend._request_timeout(background=True)

    assert timeout_s == 0.4


def test_axis_driver_success_clears_stale_diag_last_error():
    backend, _fake_serial = _backend_and_serial()
    backend._diag_last_error = "stale"
    backend._diag_failures = 0

    backend._record_modbus_success(0x03, latency_s=0.01)

    assert backend._diag_last_error is None


def test_axis_driver_snapshot_exposes_configured_and_observed_intervals():
    backend, _fake_serial = _backend_and_serial()

    snapshot = backend.get_diagnostics_snapshot()

    assert snapshot["configured_position_interval_s"] == pytest.approx(0.2)
    assert snapshot["configured_status_interval_s"] == pytest.approx(1.0)
    assert "position_interval_last_s" in snapshot
    assert "status_interval_last_s" in snapshot


def test_axis_driver_background_status_poll_skips_while_motion_active():
    backend, fake_serial = _backend_and_serial()
    asyncio.run(backend.connect())
    fake_serial.writes.clear()

    backend.axis_status["azimuth"] = "CW"
    payload = asyncio.run(backend.poll_status())

    assert payload == backend._last_status_payload
    assert fake_serial.writes == []


def test_axis_driver_background_position_poll_yields_to_command_priority():
    backend, fake_serial = _backend_and_serial()
    asyncio.run(backend.connect())
    fake_serial.writes.clear()

    backend.telemetry.az = 123.0
    backend.telemetry.el = 45.0
    backend._command_priority_until_monotonic = time.monotonic() + 1.0

    payload = asyncio.run(backend.poll_position())

    assert payload == (123.0, 45.0)
    assert fake_serial.writes == []

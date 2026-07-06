import asyncio

from antrack.core.antenna.config import AxisDriverConnectionConfig
from antrack.core.antenna.types import AntennaConnectionState
from antrack.core.axis.axis_driver_backend import AxisDriverBackend
from antrack.core.axis.axis_driver_constants import COMMAND_REGISTER, COMMAND_TRIGGER_REGISTER, RAW_POSITION_REGISTER, RELEASE_REGISTER, SPEED_REGISTER
from antrack.core.axis.modbus_rtu import append_crc, build_fc03_request, build_fc06_request


def _fc03_response(slave: int, value: int) -> bytes:
    return append_crc(bytes((slave, 0x03, 0x02, (value >> 8) & 0xFF, value & 0xFF)))


class FakeSerial:
    def __init__(self, responses):
        self.responses = responses
        self.writes = []
        self.last_request = b""
        self.is_open = True

    def write(self, data: bytes) -> int:
        self.writes.append(data)
        self.last_request = data
        return len(data)

    def read(self, size: int) -> bytes:
        return self.responses.get(self.last_request, b"")[:size]

    def close(self) -> None:
        self.is_open = False


def _driver_responses():
    responses = {
        build_fc03_request(10, RELEASE_REGISTER, 1): _fc03_response(10, 150),
        build_fc03_request(20, RELEASE_REGISTER, 1): _fc03_response(20, 151),
        build_fc03_request(10, RAW_POSITION_REGISTER, 1): _fc03_response(10, 32768),
        build_fc03_request(20, RAW_POSITION_REGISTER, 1): _fc03_response(20, 16384),
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


def test_axis_driver_connect_reads_versions_and_position():
    backend, fake_serial = _backend_and_serial()

    asyncio.run(backend.connect())

    assert backend.is_connected()
    assert backend.versions.server_version == "AxisDriver"
    assert backend.versions.driver_version_az == "1.50"
    assert backend.telemetry.az_raw == 32768
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

    try:
        asyncio.run(backend.get_position())
    except Exception:
        pass

    assert backend.get_connection_state() == AntennaConnectionState.DEGRADED

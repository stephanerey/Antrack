import asyncio
import time

import pytest

from antrack.core.antenna.config import AxisDriverConnectionConfig
from antrack.core.antenna.types import AntennaConnectionState
from antrack.core.axis.axis_driver_backend import AxisDriverBackend
from antrack.core.axis.axis_driver_constants import COMMAND_REGISTER, COMMAND_TRIGGER_REGISTER, ENDSTOP_REGISTER, MOTION_STATE_REGISTER, PARAMETER_TRIGGER_REGISTER, RAW_POSITION_REGISTER, RELEASE_REGISTER, SPEED_REGISTER
from antrack.core.axis.modbus_rtu import append_crc, build_fc03_request, build_fc06_request, build_fc16_request


def _fc03_response(slave: int, value: int) -> bytes:
    return append_crc(bytes((slave, 0x03, 0x02, (value >> 8) & 0xFF, value & 0xFF)))


def _fc03_block_response(slave: int, values: list[int]) -> bytes:
    payload = bytes((slave, 0x03, 2 * len(values)))
    for value in values:
        payload += bytes(((value >> 8) & 0xFF, value & 0xFF))
    return append_crc(payload)


def _fc16_response(slave: int, start_register: int, quantity: int) -> bytes:
    return append_crc(
        bytes(
            (
                slave,
                0x10,
                (start_register >> 8) & 0xFF,
                start_register & 0xFF,
                (quantity >> 8) & 0xFF,
                quantity & 0xFF,
            )
        )
    )


class FakeSerial:
    def __init__(self, responses, **kwargs):
        self.responses = responses
        self.kwargs = kwargs
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


class SequenceSerial(FakeSerial):
    def __init__(self, responses, read_sequences, **kwargs):
        super().__init__(responses, **kwargs)
        self.read_sequences = {key: list(value) for key, value in read_sequences.items()}

    def write(self, data: bytes) -> int:
        result = super().write(data)
        if data in self.read_sequences:
            self.pending_response = b""
        return result

    def read(self, size: int) -> bytes:
        sequence = self.read_sequences.get(self.last_request)
        if sequence:
            chunk = sequence.pop(0)
            if len(chunk) > size:
                head, tail = chunk[:size], chunk[size:]
                sequence.insert(0, tail)
                return head
            return chunk
        return super().read(size)


def _driver_responses():
    responses = {
        build_fc03_request(10, RELEASE_REGISTER, 1): _fc03_response(10, 150),
        build_fc03_request(20, RELEASE_REGISTER, 1): _fc03_response(20, 151),
        build_fc03_request(10, RAW_POSITION_REGISTER, 1): _fc03_response(10, 32768),
        build_fc03_request(20, RAW_POSITION_REGISTER, 1): _fc03_response(20, 16384),
        build_fc03_request(10, MOTION_STATE_REGISTER, 7): _fc03_block_response(10, [30, 1, 32768, 1, 150, 2, 0]),
        build_fc03_request(20, MOTION_STATE_REGISTER, 7): _fc03_block_response(20, [30, 1, 16384, 1, 151, 2, 0]),
        build_fc03_request(10, COMMAND_TRIGGER_REGISTER, 1): _fc03_response(10, 0),
        build_fc03_request(20, COMMAND_TRIGGER_REGISTER, 1): _fc03_response(20, 0),
        build_fc16_request(10, COMMAND_REGISTER, [10, 25]): _fc16_response(10, COMMAND_REGISTER, 2),
        build_fc16_request(10, COMMAND_REGISTER, [10, 500]): _fc16_response(10, COMMAND_REGISTER, 2),
        build_fc16_request(10, COMMAND_REGISTER, [100, 25]): _fc16_response(10, COMMAND_REGISTER, 2),
        build_fc16_request(10, COMMAND_REGISTER, [100, 300]): _fc16_response(10, COMMAND_REGISTER, 2),
        build_fc16_request(20, COMMAND_REGISTER, [100, 300]): _fc16_response(20, COMMAND_REGISTER, 2),
    }
    for slave in (10, 20):
        for register, value in ((101, 30), (104, 1), (106, 2), (107, 0)):
            responses[build_fc03_request(slave, register, 1)] = _fc03_response(slave, value)
    for request in (
        build_fc06_request(10, SPEED_REGISTER, 25),
        build_fc06_request(10, SPEED_REGISTER, 300),
        build_fc06_request(10, SPEED_REGISTER, 500),
        build_fc06_request(10, PARAMETER_TRIGGER_REGISTER, 1),
        build_fc06_request(10, COMMAND_TRIGGER_REGISTER, 1),
        build_fc06_request(10, COMMAND_REGISTER, 100),
        build_fc06_request(20, COMMAND_REGISTER, 100),
        build_fc06_request(10, COMMAND_REGISTER, 10),
        build_fc06_request(20, COMMAND_TRIGGER_REGISTER, 1),
        build_fc06_request(20, COMMAND_REGISTER, 10),
    ):
        responses[request] = request
    return responses


def _backend_and_serial():
    fake_serial = FakeSerial(_driver_responses())
    config = AxisDriverConnectionConfig(
        comport="COM7",
        legacy_accept_short_fc6_response=False,
    )
    backend = AxisDriverBackend(config, serial_factory=lambda **kwargs: fake_serial)
    return backend, fake_serial


def _backend_and_serial_with_responses(responses):
    fake_serial = FakeSerial(responses)
    config = AxisDriverConnectionConfig(
        comport="COM7",
        legacy_accept_short_fc6_response=False,
    )
    backend = AxisDriverBackend(config, serial_factory=lambda **kwargs: fake_serial)
    return backend, fake_serial


def _backend_and_sequence_serial(responses, read_sequences):
    fake_serial = SequenceSerial(responses, read_sequences)
    config = AxisDriverConnectionConfig(
        comport="COM7",
        legacy_accept_short_fc6_response=False,
    )
    backend = AxisDriverBackend(config, serial_factory=lambda **kwargs: fake_serial)
    return backend, fake_serial


def _confirm_frames(slave: int) -> list[bytes]:
    return [
        build_fc03_request(slave, COMMAND_TRIGGER_REGISTER, 1),
        build_fc03_request(slave, MOTION_STATE_REGISTER, 7),
    ]


def test_axis_driver_connect_reads_versions_and_status():
    backend, fake_serial = _backend_and_serial()

    asyncio.run(backend.connect())

    assert backend.is_connected()
    assert backend.versions.server_version == "AxisDriver"
    assert backend.versions.driver_version_az == "1.50"
    assert backend.telemetry.index_az == 2
    assert backend.telemetry.index_el == 2
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
        build_fc16_request(10, COMMAND_REGISTER, [10, 25]),
        build_fc06_request(10, COMMAND_TRIGGER_REGISTER, 1),
        *_confirm_frames(10),
        build_fc16_request(10, COMMAND_REGISTER, [100, 25]),
        build_fc06_request(10, COMMAND_TRIGGER_REGISTER, 1),
        *_confirm_frames(10),
    ]


def test_axis_driver_set_speed_while_moving_preserves_motion_state():
    backend, fake_serial = _backend_and_serial()
    asyncio.run(backend.connect())
    asyncio.run(backend.move_cw())
    fake_serial.writes.clear()

    asyncio.run(backend.set_az_speed(25))

    assert fake_serial.writes == [
        build_fc16_request(10, COMMAND_REGISTER, [100, 25]),
        build_fc06_request(10, COMMAND_TRIGGER_REGISTER, 1),
        *_confirm_frames(10),
    ]


def test_axis_driver_explicit_set_speed_rewrites_even_when_cached():
    backend, fake_serial = _backend_and_serial()
    asyncio.run(backend.connect())
    asyncio.run(backend.set_az_speed(25))
    fake_serial.writes.clear()

    asyncio.run(backend.set_az_speed(25))

    assert fake_serial.writes == [
        build_fc16_request(10, COMMAND_REGISTER, [10, 25]),
        build_fc06_request(10, COMMAND_TRIGGER_REGISTER, 1),
        *_confirm_frames(10),
    ]


def test_axis_driver_set_speed_uses_requested_settings_value():
    backend, fake_serial = _backend_and_serial()
    asyncio.run(backend.connect())
    fake_serial.writes.clear()

    ack = asyncio.run(backend.set_az_speed(500))

    assert ack == 500
    assert backend.telemetry.az_setrate == 500.0
    assert fake_serial.writes == [
        build_fc16_request(10, COMMAND_REGISTER, [10, 500]),
        build_fc06_request(10, COMMAND_TRIGGER_REGISTER, 1),
        *_confirm_frames(10),
    ]


def test_axis_driver_explicit_set_speed_retriggers_motion_even_when_cached():
    backend, fake_serial = _backend_and_serial()
    asyncio.run(backend.connect())
    asyncio.run(backend.set_az_speed(25))
    asyncio.run(backend.move_cw())
    fake_serial.writes.clear()

    asyncio.run(backend.set_az_speed(25))

    assert fake_serial.writes == [
        build_fc16_request(10, COMMAND_REGISTER, [100, 25]),
        build_fc06_request(10, COMMAND_TRIGGER_REGISTER, 1),
        *_confirm_frames(10),
    ]


def test_axis_driver_move_retries_when_update1_is_not_consumed():
    responses = _driver_responses()
    update_request = build_fc03_request(10, COMMAND_TRIGGER_REGISTER, 1)
    backend, fake_serial = _backend_and_sequence_serial(
        responses,
        read_sequences={
            update_request: [_fc03_response(10, 1), _fc03_response(10, 0)],
        },
    )
    backend.config.command_apply_confirmation_timeout_s = 0.0
    asyncio.run(backend.connect())
    fake_serial.writes.clear()

    asyncio.run(backend.move_cw())

    assert fake_serial.writes == [
        build_fc06_request(10, COMMAND_REGISTER, 100),
        build_fc06_request(10, COMMAND_TRIGGER_REGISTER, 1),
        *_confirm_frames(10),
        build_fc06_request(10, COMMAND_REGISTER, 100),
        build_fc06_request(10, COMMAND_TRIGGER_REGISTER, 1),
        *_confirm_frames(10),
    ]
    assert backend._last_command_diagnostics[10]["confirmation_attempt"] == 2
    assert backend._last_command_diagnostics[10]["command_final_status"] == "confirmed"


def test_axis_driver_move_retry_falls_back_to_fc06_after_unconfirmed_fc16():
    responses = _driver_responses()
    update_request = build_fc03_request(10, COMMAND_TRIGGER_REGISTER, 1)
    status_request = build_fc03_request(10, MOTION_STATE_REGISTER, 7)
    backend, fake_serial = _backend_and_sequence_serial(
        responses,
        read_sequences={
            update_request: [_fc03_response(10, 0), _fc03_response(10, 0), _fc03_response(10, 0)],
            status_request: [
                _fc03_block_response(10, [0, 1, 32768, 1, 150, 2, 0]),
                _fc03_block_response(10, [0, 1, 32768, 1, 150, 2, 0]),
                _fc03_block_response(10, [30, 1, 32768, 1, 150, 2, 0]),
            ],
        },
    )
    backend.config.command_apply_confirmation_timeout_s = 0.0
    asyncio.run(backend.connect())
    asyncio.run(backend.set_az_speed(25))
    fake_serial.writes.clear()

    asyncio.run(backend.move_cw())

    assert fake_serial.writes == [
        build_fc16_request(10, COMMAND_REGISTER, [100, 25]),
        build_fc06_request(10, COMMAND_TRIGGER_REGISTER, 1),
        *_confirm_frames(10),
        build_fc06_request(10, COMMAND_REGISTER, 100),
        build_fc06_request(10, SPEED_REGISTER, 25),
        build_fc06_request(10, COMMAND_TRIGGER_REGISTER, 1),
        *_confirm_frames(10),
    ]
    assert backend._last_command_diagnostics[10]["confirmation_attempt"] == 2
    assert backend._last_command_diagnostics[10]["command_final_status"] == "confirmed"


def test_axis_driver_stop_consumed_but_still_moving_does_not_spam_immediately():
    backend, fake_serial = _backend_and_serial()
    backend.config.stop_reinforce_delay_s = 10.0
    asyncio.run(backend.connect())
    asyncio.run(backend.move_cw())
    fake_serial.writes.clear()

    asyncio.run(backend.stop_az())

    assert fake_serial.writes == [
        build_fc06_request(10, COMMAND_REGISTER, 10),
        build_fc06_request(10, COMMAND_TRIGGER_REGISTER, 1),
        *_confirm_frames(10),
    ]
    assert backend._last_command_diagnostics[10]["command_final_status"] == "accepted_pending_stop_effect"


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
    assert backend.telemetry.index_az == 2


def test_axis_driver_single_register_status_mode_issues_individual_fc03_reads():
    backend, fake_serial = _backend_and_serial()
    asyncio.run(backend.connect())
    fake_serial.writes.clear()

    asyncio.run(backend.get_status())

    assert build_fc03_request(10, MOTION_STATE_REGISTER, 7) not in fake_serial.writes
    assert build_fc03_request(10, MOTION_STATE_REGISTER, 1) in fake_serial.writes
    assert build_fc03_request(10, RAW_POSITION_REGISTER, 1) not in fake_serial.writes


def test_axis_driver_status_include_position_reads_raw_position_when_enabled():
    fake_serial = FakeSerial(_driver_responses())
    config = AxisDriverConnectionConfig(
        comport="COM7",
        legacy_accept_short_fc6_response=False,
        status_read_mode="minimal_single_register",
        status_include_position=True,
    )
    backend = AxisDriverBackend(config, serial_factory=lambda **kwargs: fake_serial)
    asyncio.run(backend.connect())
    fake_serial.writes.clear()

    asyncio.run(backend.get_status())

    assert build_fc03_request(10, RAW_POSITION_REGISTER, 1) in fake_serial.writes


def test_axis_driver_minimal_status_does_not_refresh_global_position_timestamp():
    backend, _fake_serial = _backend_and_serial()
    asyncio.run(backend.connect())
    backend.telemetry.last_update_monotonic = 123.0

    asyncio.run(backend.get_status())

    assert backend.telemetry.last_update_monotonic == 123.0


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

    assert timeout_s == 0.2


def test_axis_driver_success_clears_stale_diag_last_error():
    backend, _fake_serial = _backend_and_serial()
    backend._diag_last_error = "stale"
    backend._diag_failures = 0

    backend._record_modbus_success(0x03, latency_s=0.01)

    assert backend._diag_last_error is None


def test_axis_driver_snapshot_exposes_configured_and_observed_intervals():
    backend, _fake_serial = _backend_and_serial()

    snapshot = backend.get_diagnostics_snapshot()

    assert snapshot["configured_position_interval_s"] == pytest.approx(0.15)
    assert snapshot["configured_status_interval_s"] == pytest.approx(1.0)
    assert "position_interval_last_s" in snapshot
    assert "status_interval_last_s" in snapshot


def test_axis_driver_background_status_poll_skips_while_motion_active():
    backend, fake_serial = _backend_and_serial()
    asyncio.run(backend.connect())
    fake_serial.writes.clear()

    backend.axis_status["azimuth"] = "CW"
    payload = asyncio.run(backend.poll_status())

    assert payload["endstop_az"] == 1
    assert build_fc03_request(10, ENDSTOP_REGISTER, 1) in fake_serial.writes
    assert build_fc03_request(10, RAW_POSITION_REGISTER, 1) not in fake_serial.writes


def test_axis_driver_background_position_poll_continues_during_motion_priority_window():
    backend, fake_serial = _backend_and_serial()
    asyncio.run(backend.connect())
    fake_serial.writes.clear()

    backend.axis_status["azimuth"] = "CW"
    backend._command_priority_until_monotonic = time.monotonic() + 1.0

    payload = asyncio.run(backend.poll_position())

    assert payload[0] is not None
    assert payload[1] is not None
    assert build_fc03_request(10, RAW_POSITION_REGISTER, 1) in fake_serial.writes
    assert build_fc03_request(20, RAW_POSITION_REGISTER, 1) in fake_serial.writes


def test_axis_driver_background_position_poll_defer_only_while_command_active_when_enabled():
    backend, fake_serial = _backend_and_serial()
    backend.config.background_position_defer_commands = True
    asyncio.run(backend.connect())
    fake_serial.writes.clear()

    backend._command_pending_count = 1

    payload = asyncio.run(backend.poll_position())

    assert payload == (backend.telemetry.az, backend.telemetry.el)
    assert fake_serial.writes == []


def test_axis_driver_background_status_poll_defer_only_while_command_active():
    backend, fake_serial = _backend_and_serial()
    asyncio.run(backend.connect())
    fake_serial.writes.clear()

    backend._command_pending_count = 1

    payload = asyncio.run(backend.poll_status())

    assert payload == backend._last_status_payload
    assert fake_serial.writes == []


def test_axis_driver_waits_until_command_deadline_when_first_read_is_empty():
    responses = _driver_responses()
    request = build_fc03_request(10, RELEASE_REGISTER, 1)
    fake_serial = SequenceSerial(
        responses,
        read_sequences={
            request: [b"", responses[request]],
        },
    )
    backend = AxisDriverBackend(
        AxisDriverConnectionConfig(
            comport="COM7",
            serial_timeout_s=0.01,
            command_timeout_s=0.05,
            legacy_accept_short_fc6_response=False,
        ),
        serial_factory=lambda **kwargs: fake_serial,
    )

    asyncio.run(backend.connect())

    assert backend.versions.driver_version_az == "1.50"


def test_axis_driver_raises_timeout_when_command_deadline_expires():
    request = build_fc03_request(10, RELEASE_REGISTER, 1)
    fake_serial = SequenceSerial(
        _driver_responses(),
        read_sequences={request: [b"", b"", b""]},
    )
    backend = AxisDriverBackend(
        AxisDriverConnectionConfig(
            comport="COM7",
            serial_timeout_s=0.01,
            command_timeout_s=0.03,
            legacy_accept_short_fc6_response=False,
        ),
        serial_factory=lambda **kwargs: fake_serial,
    )

    with pytest.raises(TimeoutError):
        asyncio.run(backend.connect())


def test_axis_driver_connect_passes_explicit_serial_8n1_and_flow_control():
    captured = {}

    def factory(**kwargs):
        captured.update(kwargs)
        return FakeSerial(_driver_responses(), **kwargs)

    backend = AxisDriverBackend(AxisDriverConnectionConfig(comport="COM8"), serial_factory=factory)

    asyncio.run(backend.connect())

    assert captured["bytesize"] == 8
    assert captured["stopbits"] == 1
    assert captured["xonxoff"] is False
    assert captured["rtscts"] is False
    assert captured["dsrdtr"] is False
    assert captured["write_timeout"] == pytest.approx(0.25)


def test_axis_driver_stop_reinforcement_sends_exactly_one_delayed_stop():
    backend, fake_serial = _backend_and_serial()
    backend.config.stop_reinforce_delay_s = 0.01
    asyncio.run(backend.connect())
    fake_serial.writes.clear()

    async def scenario():
        await backend.move_cw()
        await backend.stop_az()
        await asyncio.sleep(0.03)

    asyncio.run(scenario())

    assert fake_serial.writes == [
        build_fc06_request(10, COMMAND_REGISTER, 100),
        build_fc06_request(10, COMMAND_TRIGGER_REGISTER, 1),
        *_confirm_frames(10),
        build_fc06_request(10, COMMAND_REGISTER, 10),
        build_fc06_request(10, COMMAND_TRIGGER_REGISTER, 1),
        *_confirm_frames(10),
        build_fc06_request(10, COMMAND_REGISTER, 10),
        build_fc06_request(10, COMMAND_TRIGGER_REGISTER, 1),
        *_confirm_frames(10),
    ]


def test_axis_driver_stop_reinforcement_is_canceled_by_new_move():
    backend, fake_serial = _backend_and_serial()
    backend.config.stop_reinforce_delay_s = 0.02
    asyncio.run(backend.connect())
    fake_serial.writes.clear()

    async def scenario():
        await backend.move_cw()
        await backend.stop_az()
        await asyncio.sleep(0.005)
        await backend.move_cw()
        await asyncio.sleep(0.04)

    asyncio.run(scenario())

    assert fake_serial.writes == [
        build_fc06_request(10, COMMAND_REGISTER, 100),
        build_fc06_request(10, COMMAND_TRIGGER_REGISTER, 1),
        *_confirm_frames(10),
        build_fc06_request(10, COMMAND_REGISTER, 10),
        build_fc06_request(10, COMMAND_TRIGGER_REGISTER, 1),
        *_confirm_frames(10),
        build_fc06_request(10, COMMAND_REGISTER, 100),
        build_fc06_request(10, COMMAND_TRIGGER_REGISTER, 1),
        *_confirm_frames(10),
    ]

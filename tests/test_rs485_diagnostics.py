from datetime import datetime

import pytest

from antrack.core.antenna.config import AxisDriverConnectionConfig
from antrack.core.axis.axis_driver_backend import AxisDriverBackend
from antrack.core.axis.rs485_diagnostics import (
    RS485_DIAGNOSTICS,
    Rs485DiagnosticEvent,
    Rs485DiagnosticHub,
    Rs485Direction,
    Rs485Result,
    Rs485Statistics,
    classify_exception,
    request_details,
)
from antrack.core.axis.modbus_rtu import ModbusCrcError, ModbusFrameError, append_crc, build_fc03_request


def _event(
    event_id: int,
    *,
    direction: str,
    transaction_id: int | None = None,
    result: str = "OK",
    latency_ms: float | None = None,
    axis: str = "AZ",
    category: str = "Status",
) -> Rs485DiagnosticEvent:
    return Rs485DiagnosticEvent(
        event_id=event_id,
        timestamp_wall=datetime.now().astimezone(),
        timestamp_monotonic_ns=event_id * 1_000_000,
        direction=direction,
        axis=axis,
        category=category,
        transaction_id=transaction_id,
        logical_request_id=transaction_id,
        result=result,
        latency_ms=latency_ms,
    )


def test_diagnostic_hub_is_bounded_and_notifies_subscribers():
    hub = Rs485DiagnosticHub(max_events=2)
    received = []
    hub.subscribe(received.append)

    for _ in range(3):
        hub.publish(direction="EVENT")

    assert len(received) == 3
    assert [event.event_id for event in hub.snapshot()] == [2, 3]


def test_transaction_ids_are_unique_and_monotonic():
    hub = Rs485DiagnosticHub()
    assert [hub.next_transaction_id() for _ in range(3)] == [1, 2, 3]


def test_request_decoder_classifies_axis_function_and_category():
    details = request_details(
        build_fc03_request(10, 103, 1),
        context="position_slave_10",
        az_slave=10,
        el_slave=20,
    )

    assert details["axis"] == "AZ"
    assert details["function_code"] == 0x03
    assert details["category"] == "Position"
    assert "0x0067" in details["decoded"]


@pytest.mark.parametrize(
    ("error", "result"),
    [
        (TimeoutError("late"), Rs485Result.TIMEOUT.value),
        (ModbusCrcError("CRC mismatch"), Rs485Result.CRC_ERROR.value),
        (ModbusFrameError("length mismatch"), Rs485Result.LENGTH_ERROR.value),
        (ModbusFrameError("Unexpected function"), Rs485Result.UNEXPECTED_RESPONSE.value),
        (OSError("port lost"), Rs485Result.SERIAL_ERROR.value),
    ],
)
def test_error_classification(error, result):
    assert classify_exception(error)[0] == result


def test_statistics_correlate_success_and_compute_known_latencies():
    stats = Rs485Statistics()
    event_id = 1
    for transaction_id, latency in enumerate((10.0, 20.0, 30.0, 40.0, 50.0), start=1):
        stats.observe(_event(event_id, direction="TX", transaction_id=transaction_id))
        event_id += 1
        stats.observe(_event(event_id, direction="RX", transaction_id=transaction_id, latency_ms=latency))
        event_id += 1

    summary = stats.latency_summary()
    assert stats.total_requests == 5
    assert stats.completed == 5
    assert stats.successful == 5
    assert stats.pending == 0
    assert summary["min"] == 10.0
    assert summary["mean"] == 30.0
    assert summary["median"] == 30.0
    assert summary["p95"] == pytest.approx(48.0)
    assert summary["p99"] == pytest.approx(49.6)
    assert summary["max"] == 50.0
    assert stats.rates()["success"] == 1.0


def test_statistics_count_timeout_retry_and_axis_separately_without_double_completion():
    stats = Rs485Statistics()
    stats.observe(_event(1, direction="TX", transaction_id=7, axis="EL", category="Move"))
    stats.observe(
        _event(
            2,
            direction="EVENT",
            transaction_id=7,
            result=Rs485Result.TIMEOUT.value,
            latency_ms=250.0,
            axis="EL",
            category="Timeout",
        )
    )
    stats.observe(
        _event(3, direction="EVENT", result=Rs485Result.RETRY.value, axis="EL", category="Retry")
    )
    stats.observe(
        _event(
            4,
            direction="EVENT",
            transaction_id=7,
            result=Rs485Result.TIMEOUT.value,
            latency_ms=250.0,
            axis="EL",
            category="Timeout",
        )
    )

    assert stats.failed == 1
    assert stats.pending == 0
    assert stats.timeouts == 1
    assert stats.retries == 1
    assert stats.axis["EL"]["errors"] == 1


def test_statistics_reset_protects_zero_divisions():
    stats = Rs485Statistics()
    stats.observe(_event(1, direction="TX", transaction_id=1))
    stats.reset()

    assert stats.total_requests == 0
    assert stats.completed == 0
    assert stats.latency_summary()["mean"] is None
    assert stats.rates() == {"success": 0.0, "error": 0.0, "retry": 0.0, "timeout": 0.0}
    assert stats.quality() == "UNKNOWN"


def test_filters_do_not_exist_in_statistics_and_cannot_change_counts():
    stats = Rs485Statistics()
    stats.observe(_event(1, direction="TX", transaction_id=1, category="Position"))
    stats.observe(_event(2, direction="RX", transaction_id=1, latency_ms=4.0, category="Position"))

    before = stats.summary()
    assert before["total_requests"] == 1
    assert before["successful_transactions"] == 1


class _DiagnosticSerial:
    def __init__(self, response: bytes):
        self.response = response
        self.pending = b""
        self.writes = []
        self.is_open = True

    @property
    def in_waiting(self):
        return len(self.pending)

    def write(self, data):
        self.writes.append(bytes(data))
        self.pending = self.response
        return len(data)

    def read(self, size):
        chunk = self.pending[:size]
        self.pending = self.pending[size:]
        return chunk

    def reset_input_buffer(self):
        self.pending = b""


def _diagnostic_backend(response: bytes):
    serial = _DiagnosticSerial(response)
    backend = AxisDriverBackend(
        AxisDriverConnectionConfig(
            comport="COM_TEST",
            command_timeout_s=0.01,
            serial_timeout_s=0.001,
            az_slave_address=10,
            el_slave_address=20,
        ),
        serial_factory=lambda **_kwargs: serial,
    )
    backend.serial_port = serial
    return backend, serial


def test_driver_publishes_correlated_tx_rx_without_extra_request():
    response = append_crc(bytes((10, 0x03, 0x02, 0x00, 0x2A)))
    backend, serial = _diagnostic_backend(response)
    events = []
    callback = events.append
    RS485_DIAGNOSTICS.subscribe(callback)
    try:
        value = backend._read_register_locked(10, 103, context="position_slave_10")
    finally:
        RS485_DIAGNOSTICS.unsubscribe(callback)

    assert value == 42
    assert len(serial.writes) == 1
    assert [event.direction for event in events] == ["TX", "RX"]
    assert events[0].transaction_id == events[1].transaction_id
    assert events[1].latency_ms is not None
    assert events[1].latency_ms >= 0.0


def test_driver_publishes_crc_error_with_received_raw_frame():
    valid = append_crc(bytes((10, 0x03, 0x02, 0x00, 0x2A)))
    corrupted = valid[:-1] + bytes((valid[-1] ^ 0xFF,))
    backend, serial = _diagnostic_backend(corrupted)
    events = []
    callback = events.append
    RS485_DIAGNOSTICS.subscribe(callback)
    try:
        with pytest.raises(ModbusCrcError):
            backend._read_register_locked(10, 103, context="position_slave_10")
    finally:
        RS485_DIAGNOSTICS.unsubscribe(callback)

    assert len(serial.writes) == 1
    assert events[0].direction == "TX"
    assert events[-1].direction == "EVENT"
    assert events[-1].result == Rs485Result.CRC_ERROR.value
    assert events[-1].raw_frame == corrupted


def test_driver_publishes_timeout_for_request_without_response():
    backend, serial = _diagnostic_backend(b"")
    events = []
    callback = events.append
    RS485_DIAGNOSTICS.subscribe(callback)
    try:
        with pytest.raises(TimeoutError):
            backend._read_register_locked(10, 103, context="position_slave_10")
    finally:
        RS485_DIAGNOSTICS.unsubscribe(callback)

    assert len(serial.writes) == 1
    assert events[0].direction == "TX"
    assert events[-1].result == Rs485Result.TIMEOUT.value
    assert events[-1].raw_frame == b""


def test_driver_publishes_unexpected_address_as_protocol_error():
    wrong_axis_response = append_crc(bytes((20, 0x03, 0x02, 0x00, 0x2A)))
    backend, serial = _diagnostic_backend(wrong_axis_response)
    events = []
    callback = events.append
    RS485_DIAGNOSTICS.subscribe(callback)
    try:
        with pytest.raises(ModbusFrameError):
            backend._read_register_locked(10, 103, context="position_slave_10")
    finally:
        RS485_DIAGNOSTICS.unsubscribe(callback)

    assert len(serial.writes) == 1
    assert events[-1].result == Rs485Result.UNEXPECTED_RESPONSE.value
    assert "slave address" in events[-1].error_text

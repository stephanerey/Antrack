"""Thread-safe RS485 diagnostic events, decoding, and statistics."""

from __future__ import annotations

import math
import statistics
import threading
import time
from collections import Counter, deque
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from typing import Callable, Mapping

from antrack.core.axis.axis_driver_constants import (
    COMMAND_REGISTER,
    COMMAND_TRIGGER_REGISTER,
    ENDSTOP_REGISTER,
    INDEX_REGISTER,
    MOTOR_ALARM_REGISTER,
    MOTION_STATE_REGISTER,
    PARAMETER_TRIGGER_REGISTER,
    RAW_POSITION_REGISTER,
    RELEASE_REGISTER,
    SPEED_REGISTER,
)


MAX_DIAGNOSTIC_EVENTS = 20_000
MAX_GLOBAL_LATENCIES = 50_000
RECENT_LATENCY_WINDOW = 50


class Rs485Direction(str, Enum):
    TX = "TX"
    RX = "RX"
    EVENT = "EVENT"


class Rs485Result(str, Enum):
    OK = "OK"
    TIMEOUT = "Timeout"
    CRC_ERROR = "CRC error"
    FORMAT_ERROR = "Format error"
    LENGTH_ERROR = "Length error"
    EXCEPTION = "Exception"
    RETRY = "Retry"
    UNEXPECTED_RESPONSE = "Unexpected response"
    UNMATCHED_RESPONSE = "Unmatched response"
    SHORT_RESPONSE = "Short response"
    LEGACY_SHORT_ACCEPTED = "Legacy short accepted"
    SERIAL_ERROR = "Serial port error"
    INFO = "Info"
    OTHER = "Other"


ERROR_RESULTS = frozenset(
    {
        Rs485Result.TIMEOUT.value,
        Rs485Result.CRC_ERROR.value,
        Rs485Result.FORMAT_ERROR.value,
        Rs485Result.LENGTH_ERROR.value,
        Rs485Result.EXCEPTION.value,
        Rs485Result.UNEXPECTED_RESPONSE.value,
        Rs485Result.UNMATCHED_RESPONSE.value,
        Rs485Result.SHORT_RESPONSE.value,
        Rs485Result.SERIAL_ERROR.value,
        Rs485Result.OTHER.value,
    }
)
WARNING_RESULTS = frozenset({Rs485Result.RETRY.value, Rs485Result.LEGACY_SHORT_ACCEPTED.value})


@dataclass(frozen=True)
class Rs485DiagnosticEvent:
    """A lightweight event produced by the driver and consumed by diagnostics."""

    event_id: int
    timestamp_wall: datetime
    timestamp_monotonic_ns: int
    direction: str
    axis: str = "Unknown"
    category: str = "Unknown"
    function_code: int | None = None
    transaction_id: int | None = None
    logical_request_id: int | None = None
    attempt: int = 1
    raw_frame: bytes = b""
    decoded: str = ""
    latency_ms: float | None = None
    result: str = Rs485Result.INFO.value
    error_code: str = ""
    error_text: str = ""
    metadata: Mapping[str, object] = field(default_factory=dict)

    def to_record(self) -> dict[str, object]:
        record = asdict(self)
        record["timestamp_wall"] = self.timestamp_wall.isoformat(timespec="microseconds")
        record["raw_frame"] = self.raw_frame.hex(" ").upper()
        return record


class Rs485DiagnosticHub:
    """Bounded event journal with thread-safe, non-Qt subscriptions."""

    def __init__(self, max_events: int = MAX_DIAGNOSTIC_EVENTS) -> None:
        self.max_events = max(1, int(max_events))
        self._events: deque[Rs485DiagnosticEvent] = deque(maxlen=self.max_events)
        self._subscribers: set[Callable[[Rs485DiagnosticEvent], None]] = set()
        self._lock = threading.RLock()
        self._next_event = 1
        self._next_transaction = 1

    def next_transaction_id(self) -> int:
        with self._lock:
            value = self._next_transaction
            self._next_transaction += 1
            return value

    def publish(self, **values) -> Rs485DiagnosticEvent:
        with self._lock:
            event = Rs485DiagnosticEvent(
                event_id=self._next_event,
                timestamp_wall=values.pop("timestamp_wall", datetime.now().astimezone()),
                timestamp_monotonic_ns=values.pop("timestamp_monotonic_ns", time.perf_counter_ns()),
                **values,
            )
            self._next_event += 1
            self._events.append(event)
            subscribers = tuple(self._subscribers)
        for callback in subscribers:
            try:
                callback(event)
            except Exception:
                # Diagnostics must never affect RS485 communication.
                continue
        return event

    def snapshot(self) -> tuple[Rs485DiagnosticEvent, ...]:
        with self._lock:
            return tuple(self._events)

    def subscribe(self, callback: Callable[[Rs485DiagnosticEvent], None]) -> None:
        with self._lock:
            self._subscribers.add(callback)

    def unsubscribe(self, callback: Callable[[Rs485DiagnosticEvent], None]) -> None:
        with self._lock:
            self._subscribers.discard(callback)

    def clear(self) -> None:
        with self._lock:
            self._events.clear()


RS485_DIAGNOSTICS = Rs485DiagnosticHub()


def axis_name(slave: int | None, *, az_slave: int, el_slave: int) -> str:
    if slave == az_slave:
        return "AZ"
    if slave == el_slave:
        return "EL"
    if slave == 0:
        return "Broadcast"
    return "Unknown"


def request_details(
    frame: bytes,
    *,
    context: str,
    az_slave: int,
    el_slave: int,
) -> dict[str, object]:
    """Decode stable request fields without duplicating response validation."""

    slave = frame[0] if frame else None
    function_code = frame[1] if len(frame) > 1 else None
    register = int.from_bytes(frame[2:4], "big") if len(frame) >= 4 else None
    value = int.from_bytes(frame[4:6], "big") if len(frame) >= 6 else None
    category = _category_for_request(context, function_code, register, value)
    function = f"FC{function_code:02X}" if function_code is not None else "Unknown"
    if function_code == 0x03:
        decoded = f"Read {value or 0} register(s) from 0x{register or 0:04X}"
    elif function_code == 0x06:
        decoded = f"Write register 0x{register or 0:04X} = {value or 0}"
    elif function_code == 0x10:
        decoded = f"Write multiple registers from 0x{register or 0:04X}"
    else:
        decoded = context or "Unknown Modbus request"
    return {
        "axis": axis_name(slave, az_slave=az_slave, el_slave=el_slave),
        "category": category,
        "function_code": function_code,
        "function": function,
        "decoded": decoded,
        "register": register,
        "value": value,
        "context": context,
    }


def response_decoded(request: bytes, response: bytes, *, legacy_short: bool = False) -> str:
    function_code = request[1] if len(request) > 1 else None
    if legacy_short:
        return "Legacy short FC06 response accepted"
    if function_code == 0x03 and len(response) >= 3:
        return f"Read response: {response[2]} data byte(s)"
    if function_code == 0x06:
        return "Write acknowledged"
    if function_code == 0x10:
        return "Multiple-register write acknowledged"
    return "Response received"


def classify_exception(exc: Exception) -> tuple[str, str]:
    name = type(exc).__name__
    text = str(exc)
    lower = text.lower()
    if isinstance(exc, TimeoutError):
        return Rs485Result.TIMEOUT.value, "timeout"
    if "crc" in lower:
        return Rs485Result.CRC_ERROR.value, "crc"
    if "length" in lower or "too short" in lower:
        return Rs485Result.LENGTH_ERROR.value, "length"
    if "unexpected" in lower or "does not match" in lower:
        return Rs485Result.UNEXPECTED_RESPONSE.value, "unexpected_response"
    if name == "ModbusFrameError":
        return Rs485Result.FORMAT_ERROR.value, "format"
    if isinstance(exc, (ConnectionError, OSError)):
        return Rs485Result.SERIAL_ERROR.value, "serial"
    return Rs485Result.EXCEPTION.value, name


def _category_for_request(
    context: str,
    function_code: int | None,
    register: int | None,
    value: int | None,
) -> str:
    lowered = context.lower()
    if "position" in lowered or register == RAW_POSITION_REGISTER:
        return "Position"
    if "status" in lowered or register == MOTION_STATE_REGISTER:
        return "Status"
    if "endstop" in lowered:
        return "Limits"
    if "index" in lowered or register == INDEX_REGISTER:
        return "Index"
    if "alarm" in lowered or register == MOTOR_ALARM_REGISTER:
        return "Alarm"
    if register == COMMAND_REGISTER:
        return "Stop" if value == 10 else "Move"
    if register in {COMMAND_TRIGGER_REGISTER, PARAMETER_TRIGGER_REGISTER}:
        return "Move"
    if register in {SPEED_REGISTER, RELEASE_REGISTER} or function_code == 0x10:
        return "Configuration"
    return "Unknown"


@dataclass(frozen=True)
class Rs485QualityThresholds:
    degraded_error_rate: float = 0.01
    bad_error_rate: float = 0.10
    degraded_p95_ms: float = 100.0
    bad_p95_ms: float = 250.0
    degraded_stale_s: float = 2.0
    bad_stale_s: float = 5.0


class Rs485Statistics:
    """Incremental transaction and latency statistics since the last reset."""

    def __init__(self, thresholds: Rs485QualityThresholds | None = None) -> None:
        self.thresholds = thresholds or Rs485QualityThresholds()
        self.reset()

    def reset(self) -> None:
        self.since = datetime.now().astimezone()
        self.total_requests = 0
        self.successful = 0
        self.failed = 0
        self.retries = 0
        self.timeouts = 0
        self._pending: set[int] = set()
        self._completed: set[int] = set()
        self._error_transactions: set[int] = set()
        self._latencies: deque[float] = deque(maxlen=MAX_GLOBAL_LATENCIES)
        self._recent_latencies: deque[float] = deque(maxlen=RECENT_LATENCY_WINDOW)
        self._latency_count = 0
        self._latency_mean = 0.0
        self._latency_m2 = 0.0
        self._latency_min = math.inf
        self._latency_max = -math.inf
        self.errors: Counter[str] = Counter()
        self.axis: dict[str, Counter[str]] = {"AZ": Counter(), "EL": Counter()}
        self.categories: dict[str, Counter[str]] = {}
        self.last_valid_wall: datetime | None = None
        self.last_valid_monotonic_ns: int | None = None
        self.last_error = ""
        self.port_state = "UNKNOWN"

    @property
    def completed(self) -> int:
        return self.successful + self.failed

    @property
    def pending(self) -> int:
        return len(self._pending)

    def observe(self, event: Rs485DiagnosticEvent) -> None:
        transaction_id = event.transaction_id
        axis_stats = self.axis.get(event.axis)
        category_stats = self.categories.setdefault(event.category, Counter())
        if event.direction == Rs485Direction.TX.value and transaction_id is not None:
            self.total_requests += 1
            self._pending.add(transaction_id)
            category_stats["requests"] += 1
            if axis_stats is not None:
                axis_stats["requests"] += 1
        elif event.direction == Rs485Direction.RX.value and transaction_id is not None:
            if transaction_id not in self._completed:
                self._complete(event, success=True)
        elif event.result in ERROR_RESULTS and transaction_id is not None:
            if transaction_id not in self._completed:
                self._complete(event, success=False)
        if event.result == Rs485Result.RETRY.value:
            self.retries += 1
            category_stats["retries"] += 1
        error_is_new = event.result in ERROR_RESULTS and (
            transaction_id is None or transaction_id not in self._error_transactions
        )
        if error_is_new:
            self.errors[event.result] += 1
            self.last_error = event.error_text or event.result
            if transaction_id is not None:
                self._error_transactions.add(transaction_id)
        if event.result == Rs485Result.TIMEOUT.value and error_is_new:
            self.timeouts += 1
        if event.category == "Port" and event.direction == Rs485Direction.EVENT.value:
            self.port_state = str(event.metadata.get("state", event.decoded or "UNKNOWN")).upper()

    def _complete(self, event: Rs485DiagnosticEvent, *, success: bool) -> None:
        transaction_id = int(event.transaction_id)  # type: ignore[arg-type]
        self._completed.add(transaction_id)
        self._pending.discard(transaction_id)
        axis_stats = self.axis.get(event.axis)
        category_stats = self.categories.setdefault(event.category, Counter())
        key = "success" if success else "errors"
        if success:
            self.successful += 1
            self.last_valid_wall = event.timestamp_wall
            self.last_valid_monotonic_ns = event.timestamp_monotonic_ns
        else:
            self.failed += 1
        category_stats[key] += 1
        if axis_stats is not None:
            axis_stats[key] += 1
        if event.latency_ms is not None:
            self._add_latency(float(event.latency_ms), axis_stats, category_stats)

    def _add_latency(self, value: float, axis_stats: Counter[str] | None, category_stats: Counter[str]) -> None:
        self._latencies.append(value)
        self._recent_latencies.append(value)
        self._latency_count += 1
        delta = value - self._latency_mean
        self._latency_mean += delta / self._latency_count
        self._latency_m2 += delta * (value - self._latency_mean)
        self._latency_min = min(self._latency_min, value)
        self._latency_max = max(self._latency_max, value)
        for counter in (axis_stats, category_stats):
            if counter is not None:
                counter["latency_count"] += 1
                counter["latency_sum_us"] += round(value * 1000.0)
                counter["latency_max_us"] = max(counter["latency_max_us"], round(value * 1000.0))

    def latency_summary(self) -> dict[str, float | int | None]:
        values = list(self._latencies)
        recent = list(self._recent_latencies)
        return {
            "count": self._latency_count,
            "min": None if not values else self._latency_min,
            "mean": None if not values else self._latency_mean,
            "median": None if not values else statistics.median(values),
            "p95": _percentile(values, 95.0),
            "p99": _percentile(values, 99.0),
            "max": None if not values else self._latency_max,
            "stddev": None if self._latency_count < 2 else math.sqrt(self._latency_m2 / self._latency_count),
            "recent_mean": None if not recent else statistics.fmean(recent),
            "recent_p95": _percentile(recent, 95.0),
        }

    def rates(self) -> dict[str, float]:
        completed = self.completed
        return {
            "success": self.successful / completed if completed else 0.0,
            "error": self.failed / completed if completed else 0.0,
            "retry": self.retries / self.total_requests if self.total_requests else 0.0,
            "timeout": self.timeouts / self.total_requests if self.total_requests else 0.0,
        }

    def quality(self, *, now_ns: int | None = None) -> str:
        if not self.completed:
            return "UNKNOWN"
        rates = self.rates()
        p95 = self.latency_summary()["recent_p95"]
        age_s = 0.0
        if self.last_valid_monotonic_ns is not None:
            age_s = ((now_ns or time.perf_counter_ns()) - self.last_valid_monotonic_ns) / 1_000_000_000.0
        if rates["error"] >= self.thresholds.bad_error_rate or age_s >= self.thresholds.bad_stale_s:
            return "BAD"
        if p95 is not None and float(p95) >= self.thresholds.bad_p95_ms:
            return "BAD"
        if (
            rates["error"] >= self.thresholds.degraded_error_rate
            or self.retries > 0
            or age_s >= self.thresholds.degraded_stale_s
            or (p95 is not None and float(p95) >= self.thresholds.degraded_p95_ms)
        ):
            return "DEGRADED"
        return "GOOD"

    def summary(self) -> dict[str, object]:
        return {
            "statistics_since": self.since.isoformat(timespec="seconds"),
            "quality": self.quality(),
            "port_state": self.port_state,
            "total_requests": self.total_requests,
            "completed_transactions": self.completed,
            "successful_transactions": self.successful,
            "failed_transactions": self.failed,
            "pending_transactions": self.pending,
            "retries": self.retries,
            "timeouts": self.timeouts,
            "rates": self.rates(),
            "latency_ms": self.latency_summary(),
            "errors": dict(self.errors),
            "axis": {name: dict(values) for name, values in self.axis.items()},
            "categories": {name: dict(values) for name, values in self.categories.items()},
            "last_valid_response": self.last_valid_wall.isoformat(timespec="microseconds") if self.last_valid_wall else None,
            "last_error": self.last_error or None,
        }


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * float(percentile) / 100.0
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return ordered[lower]
    fraction = rank - lower
    return ordered[lower] + ((ordered[upper] - ordered[lower]) * fraction)

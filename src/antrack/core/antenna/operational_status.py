"""Business-level Axis index, health, and tracking permission states."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import time

from antrack.core.antenna.types import AntennaConnectionMode
from antrack.core.axis.axis_driver_constants import MODBUS_OK


class AxisIndexState(str, Enum):
    UNKNOWN = "UNKNOWN"
    NOT_INDEXED = "NOT_INDEXED"
    INDEXED = "INDEXED"


class AxisOperationalState(str, Enum):
    UNKNOWN = "UNKNOWN"
    OK = "OK"
    ALARM = "ALARM"


@dataclass(frozen=True)
class AxisOperationalStatus:
    axis: str
    state: AxisOperationalState
    raw_endstop: int | None
    raw_motor_alarm: int | None
    raw_modbus_status: int | None
    active_flags: tuple[str, ...]
    updated_monotonic: float | None
    updated_timestamp: float | None
    is_fresh: bool

    @property
    def primary_detail(self) -> str | None:
        return self.active_flags[0] if self.active_flags else None


@dataclass(frozen=True)
class TrackingPermission:
    allowed: bool
    reasons: tuple[str, ...] = ()

    def message(self) -> str:
        if self.allowed:
            return "Tracking available."
        return "Tracking unavailable:\n" + "\n".join(f"- {reason}" for reason in self.reasons)


def status_stale_timeout(status_poll_period_s: float, *, minimum_s: float = 2.0) -> float:
    """Allow several status periods before declaring cached health stale."""
    return max(float(minimum_s), 4.0 * max(0.0, float(status_poll_period_s)))


def decode_axis_operational_status(
    axis: str,
    *,
    endstop: int | None,
    motor_alarm: int | None,
    modbus_status: int | None,
    updated_monotonic: float | None,
    updated_timestamp: float | None = None,
    now_monotonic: float | None = None,
    stale_timeout_s: float = 2.0,
) -> AxisOperationalStatus:
    now = time.monotonic() if now_monotonic is None else float(now_monotonic)
    fresh = (
        isinstance(updated_monotonic, (int, float))
        and now - float(updated_monotonic) <= max(0.0, float(stale_timeout_s))
    )
    flags: list[str] = []
    if fresh and isinstance(endstop, int) and endstop != 0:
        flags.append(f"Endstop active (raw code {endstop})")
    if fresh and isinstance(motor_alarm, int) and motor_alarm != 0:
        flags.append(f"Motor alarm active (raw code {motor_alarm})")

    if flags:
        state = AxisOperationalState.ALARM
    elif not fresh or modbus_status != MODBUS_OK or endstop is None or motor_alarm is None:
        state = AxisOperationalState.UNKNOWN
    else:
        state = AxisOperationalState.OK

    return AxisOperationalStatus(
        axis=str(axis).upper(),
        state=state,
        raw_endstop=endstop,
        raw_motor_alarm=motor_alarm,
        raw_modbus_status=modbus_status,
        active_flags=tuple(flags),
        updated_monotonic=float(updated_monotonic) if isinstance(updated_monotonic, (int, float)) else None,
        updated_timestamp=float(updated_timestamp) if isinstance(updated_timestamp, (int, float)) else None,
        is_fresh=bool(fresh),
    )


def decode_axis_index_state(
    raw_index: int | None,
    *,
    referenced_latched: bool,
    status_is_fresh: bool,
) -> AxisIndexState:
    if not status_is_fresh:
        return AxisIndexState.UNKNOWN
    if referenced_latched or raw_index in (1, 2):
        return AxisIndexState.INDEXED
    if raw_index == 0:
        return AxisIndexState.NOT_INDEXED
    return AxisIndexState.UNKNOWN


def evaluate_tracking_permission(
    mode: str,
    *,
    az_index: AxisIndexState,
    el_index: AxisIndexState,
    az_status: AxisOperationalStatus,
    el_status: AxisOperationalStatus,
) -> TrackingPermission:
    # PST Rotator exposes no Axis index/alarm contract. Both Axis transports
    # expose the same business fields and must pass the safety gate.
    if str(mode) not in {
        AntennaConnectionMode.AXIS_DRIVER.value,
        AntennaConnectionMode.AXIS_SERVER.value,
    }:
        return TrackingPermission(True)

    reasons: list[str] = []
    for axis, index_state in (("AZ", az_index), ("EL", el_index)):
        if index_state == AxisIndexState.UNKNOWN:
            reasons.append(f"{axis} index state unknown")
        elif index_state == AxisIndexState.NOT_INDEXED:
            reasons.append(f"{axis} axis not indexed")
    for status in (az_status, el_status):
        if status.state == AxisOperationalState.UNKNOWN:
            reasons.append(f"{status.axis} status unknown")
        elif status.state == AxisOperationalState.ALARM:
            detail = status.primary_detail
            reasons.append(f"{status.axis} axis alarm" + (f": {detail}" if detail else ""))
    return TrackingPermission(not reasons, tuple(reasons))

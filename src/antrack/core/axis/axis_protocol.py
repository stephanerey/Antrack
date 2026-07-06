"""Axis TCP protocol helpers and raw position conversions."""

from __future__ import annotations

import struct
from enum import Enum


class AxisCommand(Enum):
    MOVE_CW = 1
    MOVE_CCW = 2
    MOVE_UP = 3
    MOVE_DOWN = 4
    STOP_AZ = 5
    STOP_EL = 6
    SPEED_AZ = 7
    SPEED_EL = 8
    QUERY_AZ = 20
    QUERY_EL = 21
    QUERY_MVT_AZ = 22
    QUERY_MVT_EL = 23
    QUERY_SPEED_AZ = 24
    QUERY_SPEED_EL = 25
    QUERY_ENDSTOP_AZ = 26
    QUERY_ENDSTOP_EL = 27
    QUERY_SIGNAL = 28
    QUERY_MODBUS_STATUS_AZ = 29
    QUERY_MODBUS_STATUS_EL = 30
    QUERY_AXIS_SERVER_VER = 50
    QUERY_AXIS_DRIVER_VER_AZ = 51
    QUERY_AXIS_DRIVER_VER_EL = 52
    QUERY_AXIS_INDEX_AZ = 53
    QUERY_AXIS_INDEX_EL = 54
    QUERY_AXIS_MOTOR_ALARM_AZ = 55
    QUERY_AXIS_MOTOR_ALARM_EL = 56
    CLOCK = 200
    END = 255


def pack_axis_request(command: AxisCommand, data: int = 0) -> bytes:
    value = int(data)
    if value < 0:
        raise ValueError("Axis request payload must be >= 0")
    if value > 0xFFFF:
        raise ValueError("Axis request payload must be <= 65535")
    return struct.pack("B3xH2x", command.value, value)


def parse_axis_response(frame: bytes) -> tuple[AxisCommand, int]:
    if len(frame) != 8:
        raise ValueError(f"Axis response frame must be 8 bytes, got {len(frame)}")
    command_id = frame[0]
    try:
        command = AxisCommand(command_id)
    except ValueError as exc:
        raise ValueError(f"Unknown Axis command id in response: {command_id}") from exc
    return command, int(struct.unpack("<i", frame[4:8])[0])


def raw_az_to_deg(raw: int) -> float:
    return (int(raw) & 0xFFFF) * 360.0 / 65535.0


def raw_el_to_deg(raw: int) -> float:
    value = raw_az_to_deg(raw)
    if value > 180.0:
        value -= 360.0
    return value


def deg_to_raw(deg: float) -> int:
    return round((float(deg) % 360.0) * 65535.0 / 360.0) & 0xFFFF

"""Modbus RTU helpers for the direct AxisDriver backend."""

from __future__ import annotations

import struct


class ModbusError(Exception):
    """Base Modbus RTU error."""


class ModbusCrcError(ModbusError):
    """Raised when CRC validation fails."""


class ModbusFrameError(ModbusError):
    """Raised when a frame shape is invalid."""


def crc16(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 0x0001:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc & 0xFFFF


def append_crc(payload: bytes) -> bytes:
    crc = crc16(payload)
    return payload + bytes((crc & 0xFF, (crc >> 8) & 0xFF))


def validate_crc(frame: bytes) -> None:
    if len(frame) < 3:
        raise ModbusFrameError("Frame too short to contain CRC")
    expected = crc16(frame[:-2])
    actual = frame[-2] | (frame[-1] << 8)
    if actual != expected:
        raise ModbusCrcError(f"CRC mismatch: expected 0x{expected:04X}, got 0x{actual:04X}")


def build_fc03_request(slave: int, register: int, length: int) -> bytes:
    payload = struct.pack(">BBHH", int(slave), 0x03, int(register), int(length))
    return append_crc(payload)


def parse_fc03_response(frame: bytes, *, slave: int, length: int) -> list[int]:
    if len(frame) != 5 + (2 * int(length)):
        raise ModbusFrameError(f"FC03 response length mismatch: expected {5 + (2 * int(length))}, got {len(frame)}")
    validate_crc(frame)
    if frame[0] != int(slave):
        raise ModbusFrameError(f"Unexpected FC03 slave address: {frame[0]}")
    if frame[1] != 0x03:
        raise ModbusFrameError(f"Unexpected FC03 function code: {frame[1]}")
    byte_count = frame[2]
    expected_byte_count = 2 * int(length)
    if byte_count != expected_byte_count:
        raise ModbusFrameError(f"Unexpected FC03 byte count: {byte_count}")
    values = []
    for index in range(int(length)):
        start = 3 + (2 * index)
        values.append(struct.unpack(">H", frame[start:start + 2])[0])
    return values


def build_fc06_request(slave: int, register: int, value: int) -> bytes:
    payload = struct.pack(">BBHH", int(slave), 0x06, int(register), int(value))
    return append_crc(payload)


def parse_fc06_response(
    frame: bytes,
    *,
    slave: int,
    register: int,
    value: int,
    accept_legacy_short_response: bool = False,
) -> tuple[int, int]:
    expected = build_fc06_request(slave, register, value)
    if len(frame) == 8:
        validate_crc(frame)
        if frame != expected:
            raise ModbusFrameError("FC06 echoed response does not match request")
        return register, value
    if len(frame) == 7 and accept_legacy_short_response:
        if frame[:6] != expected[:6]:
            raise ModbusFrameError("Legacy short FC06 response header mismatch")
        return register, value
    raise ModbusFrameError(f"Unexpected FC06 response length: {len(frame)}")

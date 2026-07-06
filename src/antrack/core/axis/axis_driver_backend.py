"""Direct RS485 Modbus RTU backend for Axis antenna drivers."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Callable

from antrack.core.antenna.backend import BaseAntennaBackend
from antrack.core.antenna.config import AxisDriverConnectionConfig
from antrack.core.antenna.types import AntennaConnectionState, AntennaVersions
from antrack.core.axis.axis_driver_constants import (
    COMMAND_REGISTER,
    COMMAND_TRIGGER_REGISTER,
    ENDSTOP_REGISTER,
    INDEX_REGISTER,
    MODBUS_FAIL,
    MODBUS_OK,
    MOTOR_ALARM_REGISTER,
    MOTION_CCW,
    MOTION_CW,
    MOTION_STATE_REGISTER,
    MOTION_STOP,
    RAW_POSITION_REGISTER,
    RELEASE_REGISTER,
    SPEED_REGISTER,
    format_release,
)
from antrack.core.axis.axis_protocol import raw_az_to_deg, raw_el_to_deg
from antrack.core.axis.modbus_rtu import (
    build_fc03_request,
    build_fc06_request,
    parse_fc03_response,
    parse_fc06_response,
)

try:
    import serial
except Exception:  # pragma: no cover - import is validated in real runtime
    serial = None


class AxisDriverBackend(BaseAntennaBackend):
    """Axis Modbus RTU backend using a shared serial transport."""

    def __init__(
        self,
        config: AxisDriverConnectionConfig,
        *,
        serial_factory: Callable[..., object] | None = None,
    ) -> None:
        super().__init__("AxisDriver")
        self.config = config
        self.logger = logging.getLogger("AxisDriverBackend")
        self.serial_factory = serial_factory or self._default_serial_factory
        self.serial_port = None
        self.axis_status = {
            "antenna": "STOPPED",
            "azimuth": "STOP",
            "elevation": "STOP",
        }
        self._async_loop = None
        self._io_lock = None

    async def _ensure_async_primitives(self) -> None:
        loop = asyncio.get_running_loop()
        if self._async_loop is loop and self._io_lock is not None:
            return
        self._async_loop = loop
        self._io_lock = asyncio.Lock()

    def _default_serial_factory(self, **kwargs):
        if serial is None:
            raise RuntimeError("pyserial is not available")
        return serial.Serial(**kwargs)

    def is_connected(self) -> bool:
        return bool(self.serial_port is not None and getattr(self.serial_port, "is_open", True))

    async def connect(self) -> None:
        await self._ensure_async_primitives()
        if self.is_connected():
            return
        self.state = AntennaConnectionState.CONNECTING
        self.last_error = None
        try:
            self.serial_port = self.serial_factory(
                port=self.config.comport,
                baudrate=self.config.baudrate,
                timeout=self.config.serial_timeout_s,
            )
            self.state = AntennaConnectionState.CONNECTED
            await self.get_versions()
            await self.get_position()
            await self.get_status()
        except Exception as exc:
            self.state = AntennaConnectionState.ERROR
            self.last_error = str(exc)
            self.logger.error("AxisDriver connect failed: %s", exc)
            self._close_serial()
            raise

    async def disconnect(self) -> None:
        await self._ensure_async_primitives()
        self.state = AntennaConnectionState.DISCONNECTING
        try:
            if self.is_connected():
                try:
                    await self.stop_all()
                except Exception:
                    pass
        finally:
            self._close_serial()
            self.state = AntennaConnectionState.DISCONNECTED

    async def set_az_speed(self, speed: float) -> int | None:
        await self._write_axis_speed(self.config.az_slave_address, speed)
        self.telemetry.az_setrate = float(speed)
        return int(speed)

    async def set_el_speed(self, speed: float) -> int | None:
        await self._write_axis_speed(self.config.el_slave_address, speed)
        self.telemetry.el_setrate = float(speed)
        return int(speed)

    async def move_cw(self) -> int | None:
        await self._write_motion(self.config.az_slave_address, MOTION_CW)
        self.axis_status["azimuth"] = "CW"
        return MOTION_CW

    async def move_ccw(self) -> int | None:
        await self._write_motion(self.config.az_slave_address, MOTION_CCW)
        self.axis_status["azimuth"] = "CCW"
        return MOTION_CCW

    async def move_up(self) -> int | None:
        await self._write_motion(self.config.el_slave_address, MOTION_CW)
        self.axis_status["elevation"] = "UP"
        return MOTION_CW

    async def move_down(self) -> int | None:
        await self._write_motion(self.config.el_slave_address, MOTION_CCW)
        self.axis_status["elevation"] = "DOWN"
        return MOTION_CCW

    async def stop_az(self) -> int | None:
        await self._write_motion(self.config.az_slave_address, MOTION_STOP)
        self.axis_status["azimuth"] = "STOP"
        return MOTION_STOP

    async def stop_el(self) -> int | None:
        await self._write_motion(self.config.el_slave_address, MOTION_STOP)
        self.axis_status["elevation"] = "STOP"
        return MOTION_STOP

    async def get_position(self) -> tuple[float | None, float | None]:
        az_raw = await self._read_register(self.config.az_slave_address, RAW_POSITION_REGISTER)
        el_raw = await self._read_register(self.config.el_slave_address, RAW_POSITION_REGISTER)
        self.telemetry.az_raw = az_raw
        self.telemetry.el_raw = el_raw
        self.telemetry.az = raw_az_to_deg(az_raw)
        self.telemetry.el = raw_el_to_deg(el_raw)
        self.telemetry.last_update_monotonic = time.monotonic()
        return self.telemetry.az, self.telemetry.el

    async def get_status(self) -> dict:
        az_motion = await self._read_register(self.config.az_slave_address, MOTION_STATE_REGISTER)
        el_motion = await self._read_register(self.config.el_slave_address, MOTION_STATE_REGISTER)
        endstop_az = await self._read_register(self.config.az_slave_address, ENDSTOP_REGISTER)
        endstop_el = await self._read_register(self.config.el_slave_address, ENDSTOP_REGISTER)
        index_az = await self._read_register(self.config.az_slave_address, INDEX_REGISTER)
        index_el = await self._read_register(self.config.el_slave_address, INDEX_REGISTER)
        alarm_az = await self._read_register(self.config.az_slave_address, MOTOR_ALARM_REGISTER)
        alarm_el = await self._read_register(self.config.el_slave_address, MOTOR_ALARM_REGISTER)

        self.telemetry.endstop_az = endstop_az
        self.telemetry.endstop_el = endstop_el
        self.telemetry.index_az = index_az
        self.telemetry.index_el = index_el
        self.telemetry.motor_alarm_az = alarm_az
        self.telemetry.motor_alarm_el = alarm_el
        self.telemetry.modbus_status_az = MODBUS_OK
        self.telemetry.modbus_status_el = MODBUS_OK
        self.telemetry.last_update_monotonic = time.monotonic()
        return {
            "motion_az": az_motion,
            "motion_el": el_motion,
            "endstop_az": endstop_az,
            "endstop_el": endstop_el,
            "index_az": index_az,
            "index_el": index_el,
            "motor_alarm_az": alarm_az,
            "motor_alarm_el": alarm_el,
            "modbus_az": MODBUS_OK,
            "modbus_el": MODBUS_OK,
        }

    async def get_versions(self) -> AntennaVersions:
        az_release = await self._read_register(self.config.az_slave_address, RELEASE_REGISTER)
        el_release = await self._read_register(self.config.el_slave_address, RELEASE_REGISTER)
        self.versions.server_version = "AxisDriver"
        self.versions.driver_version_az = format_release(az_release)
        self.versions.driver_version_el = format_release(el_release)
        return self.versions

    async def _read_register(self, slave: int, register: int) -> int:
        await self._ensure_async_primitives()
        async with self._io_lock:
            self._ensure_serial_open()
            request = build_fc03_request(slave, register, 1)
            response = self._exchange(request, expected_response_length=7)
            return parse_fc03_response(response, slave=slave, length=1)[0]

    async def _write_register(self, slave: int, register: int, value: int) -> tuple[int, int]:
        await self._ensure_async_primitives()
        async with self._io_lock:
            self._ensure_serial_open()
            request = build_fc06_request(slave, register, value)
            response_length = 7 if self.config.legacy_accept_short_fc6_response else 8
            response = self._exchange(request, expected_response_length=response_length)
            return parse_fc06_response(
                response,
                slave=slave,
                register=register,
                value=value,
                accept_legacy_short_response=self.config.legacy_accept_short_fc6_response,
            )

    async def _write_axis_speed(self, slave: int, speed: float) -> None:
        speed_value = int(max(0, round(float(speed))))
        await self._write_register(slave, SPEED_REGISTER, speed_value)
        await self._write_register(slave, COMMAND_TRIGGER_REGISTER, 1)

    async def _write_motion(self, slave: int, motion_value: int) -> None:
        await self._write_register(slave, COMMAND_REGISTER, motion_value)
        await self._write_register(slave, COMMAND_TRIGGER_REGISTER, 1)

    def _exchange(self, request: bytes, *, expected_response_length: int) -> bytes:
        try:
            self.serial_port.write(request)
            response = self.serial_port.read(expected_response_length)
            if len(response) != expected_response_length:
                raise TimeoutError(
                    f"Expected {expected_response_length} response bytes, got {len(response)}"
                )
            return response
        except Exception as exc:
            self.state = AntennaConnectionState.DEGRADED
            self.last_error = str(exc)
            self.telemetry.modbus_status_az = MODBUS_FAIL
            self.telemetry.modbus_status_el = MODBUS_FAIL
            raise

    def _ensure_serial_open(self) -> None:
        if not self.is_connected():
            raise ConnectionError("AxisDriver serial port is not open")

    def _close_serial(self) -> None:
        if self.serial_port is None:
            return
        try:
            self.serial_port.close()
        except Exception:
            pass
        self.serial_port = None

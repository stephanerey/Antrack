"""Direct RS485 Modbus RTU backend for Axis antenna drivers."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Callable

from antrack.core.antenna.backend import BaseAntennaBackend
from antrack.core.antenna.config import AxisDriverConnectionConfig
from antrack.core.antenna.rate_estimator import PositionRateEstimator
from antrack.core.antenna.types import AntennaConnectionState, AntennaVersions
from antrack.core.axis.axis_driver_constants import (
    COMMAND_REGISTER,
    COMMAND_TRIGGER_REGISTER,
    ENDSTOP_REGISTER,
    INDEX_REGISTER,
    MODBUS_FAIL,
    MODBUS_OK,
    MOTOR_ALARM_REGISTER,
    MOTION_DIRECTION_REGISTER,
    MOTION_CCW,
    MOTION_CW,
    MOTION_STATE_REGISTER,
    MOTION_STOP,
    PARAMETER_TRIGGER_REGISTER,
    RAW_POSITION_REGISTER,
    RELEASE_REGISTER,
    SPEED_REGISTER,
    format_release,
)
from antrack.core.axis.axis_protocol import raw_az_to_deg, raw_el_to_deg
from antrack.core.axis.modbus_rtu import (
    build_fc03_request,
    build_fc06_request,
    build_fc16_request,
    parse_fc03_response,
    parse_fc06_response,
    parse_fc16_response,
)

try:
    import serial
except Exception:  # pragma: no cover - import is validated in real runtime
    serial = None


class _BackgroundPollDeferred(RuntimeError):
    """Internal signal used to yield background polling to foreground motion commands."""


class AxisDriverBackend(BaseAntennaBackend):
    """Axis Modbus RTU backend using a shared serial transport."""

    STATUS_READ_MODE_BLOCK = "block"
    STATUS_READ_MODE_SINGLE_REGISTER = "single_register"
    STATUS_READ_MODE_MINIMAL_SINGLE_REGISTER = "minimal_single_register"
    _STATUS_BLOCK_LENGTH = 7
    _DIAGNOSTIC_WINDOW_S = 5.0
    _FAILURE_THRESHOLD = 3
    _BACKGROUND_DEFER_STEP_S = 0.005
    _BACKGROUND_DEFER_MAX_S = 0.05
    _COMMAND_PRIORITY_WINDOW_S = 0.15

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
        self._last_status_payload = self._empty_status_payload()
        self._consecutive_failures = 0
        self._diag_window_started_monotonic = time.monotonic()
        self._diag_requests = 0
        self._diag_fc03 = 0
        self._diag_fc06 = 0
        self._diag_failures = 0
        self._diag_timeouts = 0
        self._diag_last_error: str | None = None
        self._diag_total_requests = 0
        self._diag_total_fc03 = 0
        self._diag_total_fc06 = 0
        self._diag_total_failures = 0
        self._diag_total_timeouts = 0
        self._diag_latency_count = 0
        self._diag_latency_total_s = 0.0
        self._diag_latency_last_s: float | None = None
        self._diag_latency_min_s: float | None = None
        self._diag_latency_max_s: float | None = None
        self._position_last_update_monotonic: float | None = None
        self._status_last_update_monotonic: float | None = None
        self._position_interval_count = 0
        self._position_interval_total_s = 0.0
        self._position_interval_last_s: float | None = None
        self._position_interval_min_s: float | None = None
        self._position_interval_max_s: float | None = None
        self._status_interval_count = 0
        self._status_interval_total_s = 0.0
        self._status_interval_last_s: float | None = None
        self._status_interval_min_s: float | None = None
        self._status_interval_max_s: float | None = None
        self._command_pending_count = 0
        self._command_priority_until_monotonic = 0.0
        self._background_position_skips = 0
        self._background_status_skips = 0
        self._background_position_skip_reason: str | None = None
        self._background_status_skip_reason: str | None = None
        self._position_poll_started_monotonic: float | None = None
        self._position_poll_finished_monotonic: float | None = None
        self._safety_status_poll_started_monotonic: float | None = None
        self._safety_status_poll_finished_monotonic: float | None = None
        self._last_request_completed_monotonic = 0.0
        self._axis_motion_state = {
            self.config.az_slave_address: MOTION_STOP,
            self.config.el_slave_address: MOTION_STOP,
        }
        self._axis_requested_motion_state = {
            self.config.az_slave_address: MOTION_STOP,
            self.config.el_slave_address: MOTION_STOP,
        }
        self._axis_requested_speed_state = {
            self.config.az_slave_address: None,
            self.config.el_slave_address: None,
        }
        self._axis_speed_state = {
            self.config.az_slave_address: None,
            self.config.el_slave_address: None,
        }
        self._last_command_diagnostics: dict[int, dict[str, object]] = {}
        self._stop_reinforce_tasks: dict[int, asyncio.Task] = {}
        self._stop_reinforce_sent = 0
        self._stop_reinforce_scheduled = 0
        self._stop_reinforce_canceled = 0
        self._rate_estimator = PositionRateEstimator(
            window_s=float(getattr(config, "rate_estimation_window_s", 2.0)),
            smoothing_alpha=float(getattr(config, "rate_estimation_smoothing_alpha", 0.35)),
        )

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
        self.telemetry.index_az = None
        self.telemetry.index_el = None
        self.telemetry.endstop_az = None
        self.telemetry.endstop_el = None
        self.telemetry.motor_alarm_az = None
        self.telemetry.motor_alarm_el = None
        self.telemetry.status_update_monotonic = None
        self.telemetry.status_update_timestamp = None
        self.state = AntennaConnectionState.CONNECTING
        self.last_error = None
        self._log_startup_config()
        try:
            self.serial_port = self.serial_factory(
                port=self.config.comport,
                baudrate=self.config.baudrate,
                timeout=self.config.serial_timeout_s,
                bytesize=getattr(serial, "EIGHTBITS", 8) if serial is not None else 8,
                parity=getattr(serial, "PARITY_NONE", "N") if serial is not None else "N",
                stopbits=getattr(serial, "STOPBITS_ONE", 1) if serial is not None else 1,
                xonxoff=False,
                rtscts=False,
                dsrdtr=False,
                write_timeout=float(self.config.command_timeout_s),
            )
            self.state = AntennaConnectionState.CONNECTED
            await self.get_versions()
            await self.get_status()
        except Exception as exc:
            self.state = AntennaConnectionState.ERROR
            self.last_error = str(exc)
            self.logger.error("AxisDriver connect failed: %s", exc)
            self._close_serial()
            raise

    async def poll_position(self) -> tuple[float | None, float | None]:
        try:
            return await self._get_position(background=True)
        except _BackgroundPollDeferred:
            self._background_position_skips += 1
            self._background_position_skip_reason = "active_command"
            if self._motion_active():
                self.logger.warning("AxisDriver position poll skipped during active motion: reason=active_command")
            return self.telemetry.az, self.telemetry.el
        except Exception as exc:
            self.logger.warning("AxisDriver background position poll failed: %s", exc)
            return self.telemetry.az, self.telemetry.el

    async def poll_status(self) -> dict:
        try:
            return await self._get_status(background=True)
        except _BackgroundPollDeferred:
            self._background_status_skips += 1
            self._background_status_skip_reason = "active_command"
            return dict(self._last_status_payload)
        except Exception as exc:
            self.logger.warning("AxisDriver background status poll failed: %s", exc)
            return dict(self._last_status_payload)

    async def disconnect(self) -> None:
        await self._ensure_async_primitives()
        self.state = AntennaConnectionState.DISCONNECTING
        try:
            self._cancel_all_stop_reinforcements()
            if self.is_connected():
                try:
                    await self.stop_all()
                except Exception:
                    pass
        finally:
            self._close_serial()
            self.telemetry.status_update_monotonic = None
            self.telemetry.status_update_timestamp = None
            self.state = AntennaConnectionState.DISCONNECTED

    async def set_az_speed(self, speed: float) -> int | None:
        applied_speed = self._coerce_speed(speed)
        await self._apply_axis_state(self.config.az_slave_address, speed=applied_speed, force_speed_write=True)
        self.telemetry.az_setrate = float(applied_speed)
        return int(applied_speed)

    async def set_el_speed(self, speed: float) -> int | None:
        applied_speed = self._coerce_speed(speed)
        await self._apply_axis_state(self.config.el_slave_address, speed=applied_speed, force_speed_write=True)
        self.telemetry.el_setrate = float(applied_speed)
        return int(applied_speed)

    async def manual_jog(self, axis: str, direction: str, speed: float) -> int | None:
        """Send motion and speed in one AxisDriver command sequence."""
        axis_name = str(axis).strip().lower()
        direction_name = str(direction).strip().upper()
        applied_speed = self._coerce_speed(speed)
        if axis_name == "az" and direction_name in {"CW", "CCW"}:
            slave = self.config.az_slave_address
            motion = MOTION_CW if direction_name == "CW" else MOTION_CCW
            status_key = "azimuth"
            telemetry_key = "az_setrate"
        elif axis_name == "el" and direction_name in {"UP", "DOWN"}:
            slave = self.config.el_slave_address
            motion = MOTION_CW if direction_name == "UP" else MOTION_CCW
            status_key = "elevation"
            telemetry_key = "el_setrate"
        else:
            raise ValueError(f"Unsupported manual jog: axis={axis!r}, direction={direction!r}")

        await self._apply_axis_state(slave, motion=motion, speed=applied_speed)
        setattr(self.telemetry, telemetry_key, float(applied_speed))
        self.axis_status[status_key] = direction_name
        return motion

    async def move_cw(self) -> int | None:
        await self._apply_axis_state(self.config.az_slave_address, motion=MOTION_CW)
        self.axis_status["azimuth"] = "CW"
        return MOTION_CW

    async def move_ccw(self) -> int | None:
        await self._apply_axis_state(self.config.az_slave_address, motion=MOTION_CCW)
        self.axis_status["azimuth"] = "CCW"
        return MOTION_CCW

    async def move_up(self) -> int | None:
        await self._apply_axis_state(self.config.el_slave_address, motion=MOTION_CW)
        self.axis_status["elevation"] = "UP"
        return MOTION_CW

    async def move_down(self) -> int | None:
        await self._apply_axis_state(self.config.el_slave_address, motion=MOTION_CCW)
        self.axis_status["elevation"] = "DOWN"
        return MOTION_CCW

    async def stop_az(self) -> int | None:
        await self._apply_axis_state(self.config.az_slave_address, motion=MOTION_STOP)
        self.axis_status["azimuth"] = "STOP"
        self._schedule_stop_reinforcement(self.config.az_slave_address)
        return MOTION_STOP

    async def stop_el(self) -> int | None:
        await self._apply_axis_state(self.config.el_slave_address, motion=MOTION_STOP)
        self.axis_status["elevation"] = "STOP"
        self._schedule_stop_reinforcement(self.config.el_slave_address)
        return MOTION_STOP

    async def get_position(self) -> tuple[float | None, float | None]:
        return await self._get_position(background=False)

    async def _get_position(self, *, background: bool) -> tuple[float | None, float | None]:
        await self._ensure_async_primitives()
        position_poll_started = time.monotonic()
        self._position_poll_started_monotonic = position_poll_started
        az_raw = await self._read_register(
            self.config.az_slave_address,
            RAW_POSITION_REGISTER,
            background=background,
            context="az_position",
            defer_background=bool(getattr(self.config, "background_position_defer_commands", False)),
            defer_kind="position",
        )
        el_raw = await self._read_register(
            self.config.el_slave_address,
            RAW_POSITION_REGISTER,
            background=background,
            context="el_position",
            defer_background=bool(getattr(self.config, "background_position_defer_commands", False)),
            defer_kind="position",
        )
        self.telemetry.az_raw = az_raw
        self.telemetry.el_raw = el_raw
        self.telemetry.az = raw_az_to_deg(az_raw)
        self.telemetry.el = raw_el_to_deg(el_raw)
        now_monotonic = time.monotonic()
        self._update_rate_estimate(now_monotonic)
        self._record_update_interval(kind="position", previous_monotonic=self._position_last_update_monotonic, now_monotonic=now_monotonic)
        self.telemetry.last_update_monotonic = now_monotonic
        self._position_last_update_monotonic = now_monotonic
        self._position_poll_finished_monotonic = now_monotonic
        self._background_position_skip_reason = None
        return self.telemetry.az, self.telemetry.el

    async def get_status(self) -> dict:
        return await self._get_status(background=False)

    async def _get_status(self, *, background: bool) -> dict:
        await self._ensure_async_primitives()
        self._safety_status_poll_started_monotonic = time.monotonic()
        if background and not await self._await_background_slot("safety_status"):
            raise _BackgroundPollDeferred("Background safety status deferred for active command")
        status_mode = self._status_read_mode()
        if background and status_mode in {
            self.STATUS_READ_MODE_SINGLE_REGISTER,
            self.STATUS_READ_MODE_MINIMAL_SINGLE_REGISTER,
        }:
            try:
                az_status = await self._read_status_single_async(
                    self.config.az_slave_address,
                    background=True,
                    mode=status_mode,
                )
                el_status = await self._read_status_single_async(
                    self.config.el_slave_address,
                    background=True,
                    mode=status_mode,
                )
            except _BackgroundPollDeferred:
                self._background_status_skips += 1
                return dict(self._last_status_payload)
        else:
            async with self._io_lock:
                if status_mode == self.STATUS_READ_MODE_BLOCK:
                    az_status = self._read_status_block_locked(self.config.az_slave_address, background=background)
                    el_status = self._read_status_block_locked(self.config.el_slave_address, background=background)
                else:
                    az_status = self._read_status_single_locked(
                        self.config.az_slave_address,
                        background=background,
                        mode=status_mode,
                    )
                    el_status = self._read_status_single_locked(
                        self.config.el_slave_address,
                        background=background,
                        mode=status_mode,
                    )

        az_motion = az_status["motion"]
        el_motion = el_status["motion"]
        endstop_az = az_status["endstop"]
        endstop_el = el_status["endstop"]
        index_az = az_status.get("index")
        index_el = el_status.get("index")
        alarm_az = az_status["alarm"]
        alarm_el = el_status["alarm"]

        include_position = bool(az_status.get("includes_position")) and bool(el_status.get("includes_position"))
        if include_position:
            self.telemetry.az_raw = az_status["raw_position"]
            self.telemetry.el_raw = el_status["raw_position"]
            self.telemetry.az = raw_az_to_deg(az_status["raw_position"])
            self.telemetry.el = raw_el_to_deg(el_status["raw_position"])

        self.telemetry.endstop_az = endstop_az
        self.telemetry.endstop_el = endstop_el
        self.telemetry.index_az = index_az
        self.telemetry.index_el = index_el
        self.telemetry.motor_alarm_az = alarm_az
        self.telemetry.motor_alarm_el = alarm_el
        self.telemetry.modbus_status_az = MODBUS_OK
        self.telemetry.modbus_status_el = MODBUS_OK
        now_monotonic = time.monotonic()
        self._record_update_interval(kind="status", previous_monotonic=self._status_last_update_monotonic, now_monotonic=now_monotonic)
        if include_position:
            self._update_rate_estimate(now_monotonic)
            self.telemetry.last_update_monotonic = now_monotonic
        self._status_last_update_monotonic = now_monotonic
        self.telemetry.status_update_monotonic = now_monotonic
        self.telemetry.status_update_timestamp = time.time()
        self._safety_status_poll_finished_monotonic = now_monotonic
        self._background_status_skip_reason = None
        payload = {
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
        self._last_status_payload = dict(payload)
        return payload

    def _update_rate_estimate(self, timestamp_s: float) -> None:
        az_rate, el_rate = self._rate_estimator.add(timestamp_s, self.telemetry.az, self.telemetry.el)
        self.telemetry.az_rate = float(az_rate)
        self.telemetry.el_rate = float(el_rate)

    async def get_versions(self) -> AntennaVersions:
        await self._ensure_async_primitives()
        async with self._io_lock:
            az_release = self._read_register_locked(self.config.az_slave_address, RELEASE_REGISTER, context="az_release")
            el_release = self._read_register_locked(self.config.el_slave_address, RELEASE_REGISTER, context="el_release")
        self.versions.server_version = "AxisDriver"
        self.versions.driver_version_az = format_release(az_release)
        self.versions.driver_version_el = format_release(el_release)
        return self.versions

    async def _read_register(
        self,
        slave: int,
        register: int,
        *,
        background: bool = False,
        context: str = "fc03_read",
        defer_background: bool = True,
        defer_kind: str = "safety_status",
    ) -> int:
        await self._ensure_async_primitives()
        if background and defer_background and not await self._await_background_slot(defer_kind):
            raise _BackgroundPollDeferred("Background poll deferred for pending motion command")
        async with self._io_lock:
            return self._read_register_locked(slave, register, background=background, context=context)

    def _read_register_locked(
        self,
        slave: int,
        register: int,
        *,
        background: bool = False,
        context: str = "fc03_read",
    ) -> int:
        self._ensure_serial_open()
        request = build_fc03_request(slave, register, 1)
        values = self._exchange_and_parse(
            request,
            candidate_lengths=(7,),
            parser=lambda frame: parse_fc03_response(frame, slave=slave, length=1),
            func_code=0x03,
            timeout_s=self._request_timeout(background=background),
            background=background,
            context=context,
        )
        return values[0]

    def _read_registers_locked(
        self,
        slave: int,
        register: int,
        length: int,
        *,
        background: bool = False,
        context: str = "fc03_block_read",
    ) -> list[int]:
        self._ensure_serial_open()
        request = build_fc03_request(slave, register, length)
        return self._exchange_and_parse(
            request,
            candidate_lengths=(5 + (2 * int(length)),),
            parser=lambda frame: parse_fc03_response(frame, slave=slave, length=length),
            func_code=0x03,
            timeout_s=self._request_timeout(background=background),
            background=background,
            context=context,
        )

    def _read_status_block_locked(self, slave: int, *, background: bool = False) -> dict[str, int]:
        values = self._read_registers_locked(
            slave,
            MOTION_STATE_REGISTER,
            self._STATUS_BLOCK_LENGTH,
            background=background,
            context=f"status_block_slave_{slave}",
        )
        return {
            "motion": int(values[0]),
            "raw_position": int(values[RAW_POSITION_REGISTER - MOTION_STATE_REGISTER]),
            "endstop": int(values[ENDSTOP_REGISTER - MOTION_STATE_REGISTER]),
            "index": int(values[INDEX_REGISTER - MOTION_STATE_REGISTER]),
            "alarm": int(values[MOTOR_ALARM_REGISTER - MOTION_STATE_REGISTER]),
            "includes_position": True,
        }

    def _read_status_single_locked(
        self,
        slave: int,
        *,
        background: bool = False,
        mode: str | None = None,
    ) -> dict[str, int | bool | None]:
        selected_mode = str(mode or self._status_read_mode()).strip().lower()
        includes_position = selected_mode != self.STATUS_READ_MODE_MINIMAL_SINGLE_REGISTER or bool(
            getattr(self.config, "status_include_position", False)
        )
        include_index = not (background and selected_mode == self.STATUS_READ_MODE_MINIMAL_SINGLE_REGISTER)
        payload: dict[str, int | bool | None] = {
            "motion": int(
                self._read_register_locked(
                    slave,
                    MOTION_STATE_REGISTER,
                    background=background,
                    context=f"status_motion_slave_{slave}",
                )
            ),
            "raw_position": None,
            "endstop": int(
                self._read_register_locked(
                    slave,
                    ENDSTOP_REGISTER,
                    background=background,
                    context=f"status_endstop_slave_{slave}",
                )
            ),
            "index": None,
            "alarm": int(
                self._read_register_locked(
                    slave,
                    MOTOR_ALARM_REGISTER,
                    background=background,
                    context=f"status_alarm_slave_{slave}",
                )
            ),
            "includes_position": includes_position,
        }
        if include_index:
            payload["index"] = int(
                self._read_register_locked(
                    slave,
                    INDEX_REGISTER,
                    background=background,
                    context=f"status_index_slave_{slave}",
                )
            )
        if includes_position:
            payload["raw_position"] = int(
                self._read_register_locked(
                    slave,
                    RAW_POSITION_REGISTER,
                    background=background,
                    context=f"status_position_slave_{slave}",
                )
            )
        return payload

    async def _read_status_single_async(
        self,
        slave: int,
        *,
        background: bool,
        mode: str | None = None,
    ) -> dict[str, int | bool | None]:
        selected_mode = str(mode or self._status_read_mode()).strip().lower()
        includes_position = selected_mode != self.STATUS_READ_MODE_MINIMAL_SINGLE_REGISTER or bool(
            getattr(self.config, "status_include_position", False)
        )
        include_index = not (background and selected_mode == self.STATUS_READ_MODE_MINIMAL_SINGLE_REGISTER)
        payload: dict[str, int | bool | None] = {
            "motion": int(
                await self._read_register(
                    slave,
                    MOTION_STATE_REGISTER,
                    background=background,
                    context=f"status_motion_slave_{slave}",
                )
            ),
            "raw_position": None,
            "endstop": int(
                await self._read_register(
                    slave,
                    ENDSTOP_REGISTER,
                    background=background,
                    context=f"status_endstop_slave_{slave}",
                )
            ),
            "index": None,
            "alarm": int(
                await self._read_register(
                    slave,
                    MOTOR_ALARM_REGISTER,
                    background=background,
                    context=f"status_alarm_slave_{slave}",
                )
            ),
            "includes_position": includes_position,
        }
        if include_index:
            payload["index"] = int(
                await self._read_register(
                    slave,
                    INDEX_REGISTER,
                    background=background,
                    context=f"status_index_slave_{slave}",
                )
            )
        if includes_position:
            payload["raw_position"] = int(
                await self._read_register(
                    slave,
                    RAW_POSITION_REGISTER,
                    background=background,
                    context=f"status_position_slave_{slave}",
                )
            )
        return payload

    async def _write_register(self, slave: int, register: int, value: int) -> tuple[int, int]:
        await self._ensure_async_primitives()
        self._mark_command_priority()
        try:
            async with self._io_lock:
                return self._write_register_locked(slave, register, value)
        finally:
            self._release_command_priority()

    def _write_register_locked(self, slave: int, register: int, value: int) -> tuple[int, int]:
        self._ensure_serial_open()
        if register in {SPEED_REGISTER, PARAMETER_TRIGGER_REGISTER, COMMAND_REGISTER, COMMAND_TRIGGER_REGISTER}:
            self.logger.info("AxisDriver FC06 write: slave=%s register=%s value=%s", slave, register, value)
        request = build_fc06_request(slave, register, value)
        candidate_lengths = (7, 8) if self.config.legacy_accept_short_fc6_response else (8,)
        return self._exchange_and_parse(
            request,
            candidate_lengths=candidate_lengths,
            parser=lambda frame: parse_fc06_response(
                frame,
                slave=slave,
                register=register,
                value=value,
                accept_legacy_short_response=self.config.legacy_accept_short_fc6_response,
            ),
            func_code=0x06,
            timeout_s=float(getattr(self.config, "command_timeout_s", 0.5)),
            background=False,
            context=f"fc06_slave_{slave}_reg_{register}",
        )

    def _write_registers_locked(self, slave: int, start_register: int, values: list[int]) -> tuple[int, int]:
        self._ensure_serial_open()
        self.logger.info(
            "AxisDriver FC16 write: slave=%s start_register=%s values=%s",
            slave,
            start_register,
            values,
        )
        request = build_fc16_request(slave, start_register, values)
        return self._exchange_and_parse(
            request,
            candidate_lengths=(8,),
            parser=lambda frame: parse_fc16_response(
                frame,
                slave=slave,
                start_register=start_register,
                quantity=len(values),
            ),
            func_code=0x10,
            timeout_s=float(getattr(self.config, "command_timeout_s", 0.5)),
            background=False,
            context=f"fc16_slave_{slave}_reg_{start_register}",
        )

    async def _write_axis_speed(self, slave: int, speed: float) -> None:
        await self._apply_axis_state(slave, speed=speed)

    async def _write_motion(self, slave: int, motion_value: int) -> None:
        await self._apply_axis_state(slave, motion=motion_value)

    async def _apply_axis_state(
        self,
        slave: int,
        *,
        motion: int | None = None,
        speed: float | None = None,
        cancel_reinforce: bool = True,
        force_trigger: bool = False,
        force_speed_write: bool = False,
    ) -> None:
        await self._ensure_async_primitives()
        if motion is not None and cancel_reinforce:
            self._cancel_stop_reinforcement(slave)
        self._mark_command_priority()
        try:
            async with self._io_lock:
                self._apply_axis_state_locked(
                    slave,
                    motion=motion,
                    speed=speed,
                    force_trigger=force_trigger,
                    force_speed_write=force_speed_write,
                )
        finally:
            self._release_command_priority()

    def _apply_axis_state_locked(
        self,
        slave: int,
        *,
        motion: int | None = None,
        speed: float | None = None,
        force_trigger: bool = False,
        force_speed_write: bool = False,
    ) -> None:
        desired_motion = int(self._axis_requested_motion_state.get(slave, MOTION_STOP) if motion is None else motion)
        current_speed = self._axis_speed_state.get(slave)
        desired_speed = self._axis_requested_speed_state.get(slave, current_speed)
        if speed is not None:
            desired_speed = self._coerce_speed(speed)

        speed_changed = desired_speed is not None and desired_speed != current_speed
        explicit_speed_write = speed is not None and desired_speed is not None
        moving_command = motion is not None and desired_motion != MOTION_STOP
        should_write_speed = (
            (explicit_speed_write and (speed_changed or force_speed_write))
            or (moving_command and desired_speed is not None)
        )
        motion_changed = desired_motion != self._axis_motion_state.get(slave, MOTION_STOP)
        should_write_motion = motion is not None or force_trigger
        motion_write_needed = (
            should_write_motion
            and (motion_changed or force_trigger or should_write_speed)
        ) or should_write_speed

        if not motion_write_needed and not should_write_speed:
            return

        self._axis_requested_motion_state[slave] = int(desired_motion)
        if desired_speed is not None:
            self._axis_requested_speed_state[slave] = int(desired_speed)

        command_name = self._command_name(desired_motion)
        max_attempts = self._command_max_transmissions()
        final_diag: dict[str, object] = {}
        confirmed = False
        for attempt in range(1, max_attempts + 1):
            modbus_write_ack = False
            retry_reason = ""
            try:
                self._transmit_axis_command_locked(
                    slave,
                    motion=int(desired_motion),
                    speed=int(desired_speed) if should_write_speed and desired_speed is not None else None,
                    write_motion=motion_write_needed,
                    use_fc16=attempt == 1,
                )
                modbus_write_ack = True
                diag = self._confirm_axis_command_locked(
                    slave,
                    motion=int(desired_motion),
                    command_name=command_name,
                    attempt=attempt,
                    max_attempts=max_attempts,
                    modbus_write_ack=modbus_write_ack,
                )
                final_diag = diag
                confirmed = bool(diag["command_final_status"] in {"confirmed", "accepted_pending_stop_effect"})
                retry_reason = str(diag.get("command_retry_reason") or "")
            except Exception as exc:
                retry_reason = str(exc)
                final_diag = self._command_diag(
                    slave,
                    command_name=command_name,
                    motion=int(desired_motion),
                    attempt=attempt,
                    max_attempts=max_attempts,
                    modbus_write_ack=modbus_write_ack,
                    command_final_status="failed",
                    command_retry_reason=retry_reason,
                )
                if attempt >= max_attempts:
                    self._last_command_diagnostics[int(slave)] = final_diag
                    raise

            self._last_command_diagnostics[int(slave)] = final_diag
            if confirmed:
                break
            if attempt < max_attempts:
                self.logger.warning(
                    "AxisDriver %s %s: %s, retry %d/%d",
                    self._axis_name(slave),
                    command_name,
                    retry_reason or "not confirmed",
                    attempt + 1,
                    max_attempts,
                )

        if not confirmed:
            raise TimeoutError(
                f"AxisDriver {self._axis_name(slave)} {command_name} not confirmed after {max_attempts} attempts"
            )

        if (
            should_write_speed
            and (explicit_speed_write or moving_command)
            and bool(getattr(self.config, "speed_readback_enabled", False))
        ):
            try:
                readback = self._read_register_locked(
                    slave,
                    SPEED_REGISTER,
                    background=False,
                    context=f"speed_readback_slave_{slave}",
                )
                log_method = self.logger.info if int(readback) == int(desired_speed) else self.logger.warning
                log_method(
                    "AxisDriver speed readback: slave=%s requested=%s readback=%s motion=%s",
                    slave,
                    int(desired_speed),
                    int(readback),
                    int(desired_motion),
                )
            except Exception as exc:
                self.logger.warning(
                    "AxisDriver speed readback failed: slave=%s requested=%s error=%s",
                    slave,
                    int(desired_speed),
                    exc,
                )

        self._axis_motion_state[slave] = int(desired_motion)
        if desired_speed is not None:
            self._axis_speed_state[slave] = int(desired_speed)

    def _transmit_axis_command_locked(
        self,
        slave: int,
        *,
        motion: int,
        speed: int | None,
        write_motion: bool,
        use_fc16: bool,
    ) -> None:
        if write_motion and speed is not None:
            if use_fc16 and bool(getattr(self.config, "use_fc16_for_motion_speed", False)):
                try:
                    self._write_registers_locked(slave, COMMAND_REGISTER, [int(motion), int(speed)])
                except Exception as exc:
                    self.logger.warning("AxisDriver FC16 motion/speed failed, falling back to FC06: %s", exc)
                    self._write_register_locked(slave, COMMAND_REGISTER, int(motion))
                    self._write_register_locked(slave, SPEED_REGISTER, int(speed))
            else:
                self._write_register_locked(slave, COMMAND_REGISTER, int(motion))
                self._write_register_locked(slave, SPEED_REGISTER, int(speed))
        else:
            if write_motion:
                self._write_register_locked(slave, COMMAND_REGISTER, int(motion))
            if speed is not None:
                self._write_register_locked(slave, SPEED_REGISTER, int(speed))
        self._write_register_locked(slave, COMMAND_TRIGGER_REGISTER, 1)

    def _confirm_axis_command_locked(
        self,
        slave: int,
        *,
        motion: int,
        command_name: str,
        attempt: int,
        max_attempts: int,
        modbus_write_ack: bool,
    ) -> dict[str, object]:
        if not bool(getattr(self.config, "command_apply_confirmation_enabled", True)):
            return self._command_diag(
                slave,
                command_name=command_name,
                motion=motion,
                attempt=attempt,
                max_attempts=max_attempts,
                modbus_write_ack=modbus_write_ack,
                update1_consumed=True,
                motion_state_confirmed=True,
                command_final_status="confirmed",
            )

        delay_s = max(0.0, float(getattr(self.config, "command_apply_confirmation_delay_s", 0.05)))
        if delay_s > 0.0:
            time.sleep(delay_s)

        timeout_s = max(0.0, float(getattr(self.config, "command_apply_confirmation_timeout_s", 0.25)))
        deadline = time.monotonic() + timeout_s
        diag: dict[str, object] = {}
        while True:
            update1 = self._read_register_locked(
                slave,
                COMMAND_TRIGGER_REGISTER,
                background=False,
                context=f"confirm_update1_slave_{slave}",
            )
            status = self._read_confirmation_status_locked(slave, command_name=command_name)
            update1_consumed = int(update1) == 0 or not bool(getattr(self.config, "confirm_update1_reset", True))
            motion_state_confirmed = self._motion_confirmation_matches(command_name, motion, status)
            final_status = "confirmed" if update1_consumed and motion_state_confirmed else "retry"
            retry_reason = ""
            if not update1_consumed:
                retry_reason = f"UPDATE1 still {update1}"
            elif not motion_state_confirmed:
                retry_reason = self._motion_retry_reason(command_name, motion, status)
                if command_name == "stop" and int(status.get("motion", -1)) == 30:
                    final_status = "accepted_pending_stop_effect"
                    retry_reason = "stop update consumed, motion still MOVE"
                    motion_state_confirmed = True

            diag = self._command_diag(
                slave,
                command_name=command_name,
                motion=motion,
                attempt=attempt,
                max_attempts=max_attempts,
                modbus_write_ack=modbus_write_ack,
                update1_consumed=update1_consumed,
                motion_state_confirmed=motion_state_confirmed,
                confirmation_301_value=int(update1),
                confirmation_motion_state_101=status.get("motion"),
                confirmation_direction_102=status.get("direction"),
                confirmation_position_103=status.get("raw_position"),
                confirmation_endstop_104=status.get("endstop"),
                confirmation_alarm_107=status.get("alarm"),
                command_final_status=final_status,
                command_retry_reason=retry_reason,
            )
            if final_status in {"confirmed", "accepted_pending_stop_effect"}:
                self._log_command_confirmation(diag)
                return diag
            if time.monotonic() >= deadline:
                self._log_command_confirmation(diag)
                return diag
            time.sleep(min(0.01, max(0.0, deadline - time.monotonic())))

    def _read_confirmation_status_locked(self, slave: int, *, command_name: str) -> dict[str, int | None]:
        mode = str(getattr(self.config, "command_confirm_status_read_mode", "block")).strip().lower()
        if mode == "block_101_107":
            mode = self.STATUS_READ_MODE_BLOCK
        if mode == self.STATUS_READ_MODE_BLOCK:
            start = int(getattr(self.config, "command_confirm_status_block_start", MOTION_STATE_REGISTER))
            length = int(getattr(self.config, "command_confirm_status_block_length", self._STATUS_BLOCK_LENGTH))
            try:
                values = self._read_registers_locked(
                    slave,
                    start,
                    length,
                    background=False,
                    context=f"confirm_status_block_slave_{slave}",
                )
                return self._status_from_block(start, values)
            except Exception as exc:
                self.logger.warning("AxisDriver confirmation block read failed, falling back to singles: %s", exc)
        payload: dict[str, int | None] = {
            "motion": self._read_register_locked(
                slave,
                MOTION_STATE_REGISTER,
                background=False,
                context=f"confirm_motion_slave_{slave}",
            ),
            "direction": None,
            "raw_position": None,
            "endstop": None,
            "alarm": None,
        }
        if command_name != "stop":
            payload["direction"] = self._read_register_locked(
                slave,
                MOTION_DIRECTION_REGISTER,
                background=False,
                context=f"confirm_direction_slave_{slave}",
            )
        return payload

    @staticmethod
    def _status_from_block(start: int, values: list[int]) -> dict[str, int | None]:
        def value_at(register: int) -> int | None:
            index = int(register) - int(start)
            if 0 <= index < len(values):
                return int(values[index])
            return None

        return {
            "motion": value_at(MOTION_STATE_REGISTER),
            "direction": value_at(MOTION_DIRECTION_REGISTER),
            "raw_position": value_at(RAW_POSITION_REGISTER),
            "endstop": value_at(ENDSTOP_REGISTER),
            "alarm": value_at(MOTOR_ALARM_REGISTER),
        }

    def _motion_confirmation_matches(self, command_name: str, motion: int, status: dict[str, int | None]) -> bool:
        motion_state = status.get("motion")
        if motion_state is None:
            return False
        if command_name == "stop":
            if not bool(getattr(self.config, "confirm_stop_by_motion_state", True)):
                return True
            return int(motion_state) in {0, 1, 10}
        if not bool(getattr(self.config, "confirm_move_by_motion_state", True)):
            return True
        expected_direction = self._direction_for_motion(motion)
        return int(motion_state) in {20, 30} and int(status.get("direction", -1)) == expected_direction

    def _motion_retry_reason(self, command_name: str, motion: int, status: dict[str, int | None]) -> str:
        if command_name == "stop":
            return f"state={status.get('motion')} not stopped"
        return (
            f"state={status.get('motion')} direction={status.get('direction')} "
            f"expected_direction={self._direction_for_motion(motion)}"
        )

    def _command_diag(
        self,
        slave: int,
        *,
        command_name: str,
        motion: int,
        attempt: int,
        max_attempts: int,
        modbus_write_ack: bool,
        update1_consumed: bool = False,
        motion_state_confirmed: bool = False,
        confirmation_301_value: int | None = None,
        confirmation_motion_state_101: int | None = None,
        confirmation_direction_102: int | None = None,
        confirmation_position_103: int | None = None,
        confirmation_endstop_104: int | None = None,
        confirmation_alarm_107: int | None = None,
        command_final_status: str = "pending",
        command_retry_reason: str = "",
    ) -> dict[str, object]:
        return {
            "command_name": command_name,
            "axis": self._axis_name(slave),
            "requested_motion": int(motion),
            "requested_speed": self._axis_requested_speed_state.get(slave),
            "modbus_write_ack": bool(modbus_write_ack),
            "update1_consumed": bool(update1_consumed),
            "motion_state_confirmed": bool(motion_state_confirmed),
            "confirmation_attempt": int(attempt),
            "confirmation_max_attempts": int(max_attempts),
            "confirmation_read_mode": str(getattr(self.config, "command_confirm_status_read_mode", "block")),
            "confirmation_301_value": confirmation_301_value,
            "confirmation_motion_state_101": confirmation_motion_state_101,
            "confirmation_direction_102": confirmation_direction_102,
            "confirmation_position_103": confirmation_position_103,
            "confirmation_endstop_104": confirmation_endstop_104,
            "confirmation_alarm_107": confirmation_alarm_107,
            "command_final_status": command_final_status,
            "command_retry_reason": command_retry_reason,
        }

    def _log_command_confirmation(self, diag: dict[str, object]) -> None:
        status = str(diag.get("command_final_status"))
        log_method = self.logger.info if status in {"confirmed", "accepted_pending_stop_effect"} else self.logger.warning
        log_method(
            "AxisDriver %s %s: write %s, UPDATE1 %s, state=%s, dir=%s, status=%s attempt %s/%s%s",
            diag.get("axis"),
            diag.get("command_name"),
            "OK" if diag.get("modbus_write_ack") else "FAIL",
            "consumed" if diag.get("update1_consumed") else "pending",
            diag.get("confirmation_motion_state_101"),
            diag.get("confirmation_direction_102"),
            status,
            diag.get("confirmation_attempt"),
            diag.get("confirmation_max_attempts"),
            f", reason={diag.get('command_retry_reason')}" if diag.get("command_retry_reason") else "",
        )

    def _command_max_transmissions(self) -> int:
        return max(1, int(getattr(self.config, "command_max_transmissions", 3)))

    @staticmethod
    def _command_name(motion: int) -> str:
        if int(motion) == MOTION_STOP:
            return "stop"
        if int(motion) == MOTION_CW:
            return "move_cw"
        if int(motion) == MOTION_CCW:
            return "move_ccw"
        return f"motion_{motion}"

    def _axis_name(self, slave: int) -> str:
        if int(slave) == int(self.config.az_slave_address):
            return "AZ"
        if int(slave) == int(self.config.el_slave_address):
            return "EL"
        return str(slave)

    @staticmethod
    def _direction_for_motion(motion: int) -> int:
        if int(motion) == MOTION_CW:
            return 1
        return 0

    @classmethod
    def _coerce_speed(cls, speed: float) -> int:
        return int(round(float(speed)))

    def _exchange_and_parse(
        self,
        request: bytes,
        *,
        candidate_lengths: tuple[int, ...],
        parser: Callable[[bytes], object],
        func_code: int,
        timeout_s: float,
        background: bool,
        context: str,
    ):
        started = time.monotonic()
        try:
            self._wait_inter_request_gap_locked()
            reset_input = getattr(self.serial_port, "reset_input_buffer", None)
            if callable(reset_input):
                reset_input()
            self.serial_port.write(request)
            deadline = time.monotonic() + max(0.0, float(timeout_s))
            max_frame_length = max(int(length) for length in candidate_lengths)
            max_buffer_length = len(request) + max_frame_length
            buffer = b""
            last_error = None

            while time.monotonic() < deadline and len(buffer) < max_buffer_length:
                remaining = max_buffer_length - len(buffer)
                # A pyserial read waits for the requested byte count or for its
                # timeout.  Request only bytes already buffered, or one byte
                # while waiting for the frame to start, so a complete short
                # response is never followed by an artificial serial timeout.
                try:
                    available = max(0, int(getattr(self.serial_port, "in_waiting", 0) or 0))
                except Exception:
                    available = 0
                read_size = min(remaining, max(1, available))
                chunk = self.serial_port.read(read_size)
                if chunk:
                    buffer += chunk
                parsed, last_error = self._scan_for_valid_frame(buffer, candidate_lengths, parser)
                if parsed is not None:
                    self._last_request_completed_monotonic = time.monotonic()
                    self._record_modbus_success(func_code, latency_s=max(0.0, time.monotonic() - started))
                    return parsed
                if not chunk:
                    remaining_time_s = deadline - time.monotonic()
                    if remaining_time_s > 0:
                        time.sleep(min(0.002, remaining_time_s))

            raw = buffer.hex(" ") if buffer else "<empty>"
            if last_error is not None:
                raise type(last_error)(f"{last_error} | raw={raw}")
            raise TimeoutError(
                f"Expected valid Modbus response ({candidate_lengths}), got {len(buffer)} bytes | raw={raw}"
            )
        except Exception as exc:
            self._last_request_completed_monotonic = time.monotonic()
            self._record_modbus_failure(
                func_code,
                exc,
                background=background,
                context=context,
                latency_s=max(0.0, time.monotonic() - started),
            )
            raise

    @staticmethod
    def _scan_for_valid_frame(
        buffer: bytes,
        candidate_lengths: tuple[int, ...],
        parser: Callable[[bytes], object],
    ) -> tuple[object | None, Exception | None]:
        last_error = None
        for frame_length in sorted({int(length) for length in candidate_lengths}):
            if len(buffer) < frame_length:
                continue
            for start in range(0, len(buffer) - frame_length + 1):
                frame = buffer[start:start + frame_length]
                try:
                    return parser(frame), None
                except Exception as exc:
                    last_error = exc
        return None, last_error

    def _ensure_serial_open(self) -> None:
        if not self.is_connected():
            raise ConnectionError("AxisDriver serial port is not open")

    def _wait_inter_request_gap_locked(self) -> None:
        gap_s = max(0.0, float(getattr(self.config, "inter_request_gap_s", 0.0)))
        if gap_s <= 0.0:
            return
        elapsed_s = time.monotonic() - float(self._last_request_completed_monotonic)
        remaining_s = gap_s - elapsed_s
        if remaining_s > 0.0:
            time.sleep(remaining_s)

    def _request_timeout(self, *, background: bool) -> float:
        if not background:
            return float(getattr(self.config, "command_timeout_s", 0.5))
        serial_timeout = float(getattr(self.config, "serial_timeout_s", 0.15))
        command_timeout = float(getattr(self.config, "command_timeout_s", 0.5))
        return min(
            command_timeout,
            max(serial_timeout * 2.0, command_timeout * 0.8, serial_timeout + 0.05),
        )

    def _mark_command_priority(self) -> None:
        self._command_pending_count += 1
        self._command_priority_until_monotonic = max(
            self._command_priority_until_monotonic,
            time.monotonic() + self._COMMAND_PRIORITY_WINDOW_S,
        )

    def _release_command_priority(self) -> None:
        self._command_pending_count = max(0, self._command_pending_count - 1)
        self._command_priority_until_monotonic = max(
            self._command_priority_until_monotonic,
            time.monotonic() + self._COMMAND_PRIORITY_WINDOW_S,
        )

    def _motion_active(self) -> bool:
        return any(
            str(self.axis_status.get(axis, "STOP")).upper() != "STOP"
            for axis in ("azimuth", "elevation")
        )

    def _should_defer_background_poll(self, kind: str) -> bool:
        now = time.monotonic()
        if kind == "position":
            return self._command_pending_count > 0
        if kind == "safety_status":
            return self._command_pending_count > 0
        if self._command_pending_count > 0 or now < self._command_priority_until_monotonic:
            return True
        return False

    async def _await_background_slot(self, kind: str) -> bool:
        deadline = time.monotonic() + self._BACKGROUND_DEFER_MAX_S
        while self._should_defer_background_poll(kind):
            if time.monotonic() >= deadline:
                return False
            await asyncio.sleep(self._BACKGROUND_DEFER_STEP_S)
        return True

    def _status_read_mode(self) -> str:
        mode = str(
            getattr(self.config, "status_read_mode", self.STATUS_READ_MODE_MINIMAL_SINGLE_REGISTER)
        ).strip().lower()
        if mode not in {
            self.STATUS_READ_MODE_BLOCK,
            self.STATUS_READ_MODE_SINGLE_REGISTER,
            self.STATUS_READ_MODE_MINIMAL_SINGLE_REGISTER,
        }:
            return self.STATUS_READ_MODE_MINIMAL_SINGLE_REGISTER
        return mode

    def _record_modbus_success(self, func_code: int, *, latency_s: float) -> None:
        self._diag_requests += 1
        self._diag_total_requests += 1
        if func_code == 0x03:
            self._diag_fc03 += 1
            self._diag_total_fc03 += 1
        elif func_code == 0x06:
            self._diag_fc06 += 1
            self._diag_total_fc06 += 1
        self._record_latency(latency_s)
        if self._consecutive_failures and self.state == AntennaConnectionState.DEGRADED:
            self.logger.info("AxisDriver Modbus recovered after %d consecutive failures", self._consecutive_failures)
        self._consecutive_failures = 0
        if self.is_connected():
            self.state = AntennaConnectionState.CONNECTED
        self.last_error = None
        self.telemetry.modbus_status_az = MODBUS_OK
        self.telemetry.modbus_status_el = MODBUS_OK
        if self._diag_failures == 0:
            self._diag_last_error = None
        self._maybe_log_diagnostics()

    def _record_modbus_failure(
        self,
        func_code: int,
        exc: Exception,
        *,
        background: bool,
        context: str,
        latency_s: float,
    ) -> None:
        self._diag_requests += 1
        self._diag_total_requests += 1
        if func_code == 0x03:
            self._diag_fc03 += 1
            self._diag_total_fc03 += 1
        elif func_code == 0x06:
            self._diag_fc06 += 1
            self._diag_total_fc06 += 1
        self._diag_failures += 1
        self._diag_total_failures += 1
        if isinstance(exc, TimeoutError):
            self._diag_timeouts += 1
            self._diag_total_timeouts += 1
        self._record_latency(latency_s)
        self._diag_last_error = str(exc)
        self.last_error = str(exc)
        self.telemetry.modbus_status_az = MODBUS_FAIL
        self.telemetry.modbus_status_el = MODBUS_FAIL
        self._consecutive_failures += 1
        if self._consecutive_failures >= self._FAILURE_THRESHOLD:
            self.state = AntennaConnectionState.DEGRADED
            self.logger.warning(
                "AxisDriver Modbus degraded after %d consecutive failures during %s: %s",
                self._consecutive_failures,
                context,
                exc,
            )
        elif not background:
            self.logger.warning(
                "AxisDriver Modbus transient failure during %s (%d/%d): %s",
                context,
                self._consecutive_failures,
                self._FAILURE_THRESHOLD,
                exc,
            )
        self._maybe_log_diagnostics(force=not background)

    def _maybe_log_diagnostics(self, *, force: bool = False) -> None:
        now = time.monotonic()
        elapsed = now - self._diag_window_started_monotonic
        if not force and elapsed < self._DIAGNOSTIC_WINDOW_S:
            return
        if self._diag_requests <= 0:
            self._diag_window_started_monotonic = now
            return
        self.logger.info(
            "AxisDriver Modbus diag: window=%.1fs req=%d fc03=%d fc06=%d failures=%d timeouts=%d last_error=%s",
            max(elapsed, 0.0),
            self._diag_requests,
            self._diag_fc03,
            self._diag_fc06,
            self._diag_failures,
            self._diag_timeouts,
            self._diag_last_error or "-",
        )
        self._diag_window_started_monotonic = now
        self._diag_requests = 0
        self._diag_fc03 = 0
        self._diag_fc06 = 0
        self._diag_failures = 0
        self._diag_timeouts = 0
        self._diag_last_error = None

    def _record_latency(self, latency_s: float) -> None:
        latency = max(0.0, float(latency_s))
        self._diag_latency_last_s = latency
        self._diag_latency_count += 1
        self._diag_latency_total_s += latency
        self._diag_latency_min_s = latency if self._diag_latency_min_s is None else min(self._diag_latency_min_s, latency)
        self._diag_latency_max_s = latency if self._diag_latency_max_s is None else max(self._diag_latency_max_s, latency)

    def _record_update_interval(
        self,
        *,
        kind: str,
        previous_monotonic: float | None,
        now_monotonic: float,
    ) -> None:
        if previous_monotonic is None:
            return
        interval_s = max(0.0, float(now_monotonic) - float(previous_monotonic))
        if kind == "position":
            self._position_interval_last_s = interval_s
            self._position_interval_count += 1
            self._position_interval_total_s += interval_s
            self._position_interval_min_s = (
                interval_s if self._position_interval_min_s is None else min(self._position_interval_min_s, interval_s)
            )
            self._position_interval_max_s = (
                interval_s if self._position_interval_max_s is None else max(self._position_interval_max_s, interval_s)
            )
            return
        self._status_interval_last_s = interval_s
        self._status_interval_count += 1
        self._status_interval_total_s += interval_s
        self._status_interval_min_s = (
            interval_s if self._status_interval_min_s is None else min(self._status_interval_min_s, interval_s)
        )
        self._status_interval_max_s = (
            interval_s if self._status_interval_max_s is None else max(self._status_interval_max_s, interval_s)
        )

    def _log_startup_config(self) -> None:
        requested_position_interval = float(self.config.position_interval_s)
        requested_status_interval = float(self.config.status_interval_s)
        effective_position_interval = max(0.10, requested_position_interval)
        effective_status_interval = max(0.10, requested_status_interval)
        if requested_position_interval < 0.10:
            self.logger.warning(
                "AxisDriver position interval %.3fs is too fast for current firmware; clamped to %.3fs",
                requested_position_interval,
                effective_position_interval,
            )
        if requested_status_interval < 0.10:
            self.logger.warning(
                "AxisDriver status interval %.3fs is too fast for current firmware; clamped to %.3fs",
                requested_status_interval,
                effective_status_interval,
            )
        self.logger.info(
            "AxisDriver startup: mode=axis_driver port=%s baudrate=%s az_slave=%s el_slave=%s "
            "serial_timeout_s=%.3f command_timeout_s=%.3f position_interval_s=%.3f "
            "status_interval_s=%.3f health_interval_s=%.3f status_read_mode=%s "
            "status_include_position=%s inter_request_gap_s=%.3f move_refresh_mode=%s "
            "move_refresh_interval_s=%.3f motion_speed_write=%s stop_reinforce_enabled=%s "
            "stop_reinforce_delay_s=%.3f",
            self.config.comport,
            self.config.baudrate,
            self.config.az_slave_address,
            self.config.el_slave_address,
            float(self.config.serial_timeout_s),
            float(self.config.command_timeout_s),
            effective_position_interval,
            effective_status_interval,
            float(self.config.health_interval_s),
            self._status_read_mode(),
            bool(getattr(self.config, "status_include_position", False)),
            float(getattr(self.config, "inter_request_gap_s", 0.0)),
            str(getattr(self.config, "move_refresh_mode", "edge_only")).strip().lower(),
            float(getattr(self.config, "move_refresh_interval_s", 0.0)),
            "fc16" if bool(getattr(self.config, "use_fc16_for_motion_speed", False)) else "fc06_sequence",
            bool(getattr(self.config, "stop_reinforce_enabled", True)),
            float(getattr(self.config, "stop_reinforce_delay_s", 0.12)),
        )

    @staticmethod
    def _empty_status_payload() -> dict[str, int | None]:
        return {
            "motion_az": None,
            "motion_el": None,
            "endstop_az": None,
            "endstop_el": None,
            "index_az": None,
            "index_el": None,
            "motor_alarm_az": None,
            "motor_alarm_el": None,
            "modbus_az": None,
            "modbus_el": None,
        }

    def _cancel_stop_reinforcement(self, slave: int) -> None:
        task = self._stop_reinforce_tasks.pop(int(slave), None)
        if task is not None and not task.done():
            task.cancel()
            self._stop_reinforce_canceled += 1

    def _cancel_all_stop_reinforcements(self) -> None:
        for slave in list(self._stop_reinforce_tasks):
            self._cancel_stop_reinforcement(slave)

    def _schedule_stop_reinforcement(self, slave: int) -> None:
        if not bool(getattr(self.config, "stop_reinforce_enabled", True)):
            return
        if int(getattr(self.config, "stop_reinforce_count", 1)) <= 0:
            return
        self._cancel_stop_reinforcement(slave)
        if self._async_loop is None:
            return
        self._stop_reinforce_scheduled += 1
        task = self._async_loop.create_task(self._stop_reinforce_task(int(slave)))
        self._stop_reinforce_tasks[int(slave)] = task

    async def _stop_reinforce_task(self, slave: int) -> None:
        try:
            await asyncio.sleep(max(0.0, float(getattr(self.config, "stop_reinforce_delay_s", 0.12))))
            if self._axis_motion_state.get(slave, MOTION_STOP) != MOTION_STOP:
                self._stop_reinforce_canceled += 1
                return
            await self._apply_axis_state(slave, motion=MOTION_STOP, cancel_reinforce=False, force_trigger=True)
            self._stop_reinforce_sent += 1
            self.logger.debug("AxisDriver STOP reinforcement sent for slave=%s", slave)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.logger.debug("AxisDriver STOP reinforcement failed for slave=%s: %s", slave, exc)
        finally:
            self._stop_reinforce_tasks.pop(slave, None)

    def get_diagnostics_snapshot(self) -> dict:
        latency_avg = (
            self._diag_latency_total_s / self._diag_latency_count
            if self._diag_latency_count
            else None
        )
        position_interval_avg = (
            self._position_interval_total_s / self._position_interval_count
            if self._position_interval_count
            else None
        )
        status_interval_avg = (
            self._status_interval_total_s / self._status_interval_count
            if self._status_interval_count
            else None
        )
        return {
            "configured_position_interval_s": max(0.10, float(self.config.position_interval_s)),
            "configured_status_interval_s": max(0.10, float(self.config.status_interval_s)),
            "position_last_update_monotonic_s": self._position_last_update_monotonic,
            "status_last_update_monotonic_s": self._status_last_update_monotonic,
            "backend_state": self.state.value if hasattr(self.state, "value") else str(self.state),
            "last_error": self.last_error,
            "status_read_mode": self._status_read_mode(),
            "status_include_position": bool(getattr(self.config, "status_include_position", False)),
            "modbus_inter_request_gap_s": float(getattr(self.config, "inter_request_gap_s", 0.0)),
            "background_position_skips": self._background_position_skips,
            "background_status_skips": self._background_status_skips,
            "background_position_skip_reason": self._background_position_skip_reason,
            "background_status_skip_reason": self._background_status_skip_reason,
            "position_poll_started_s": self._position_poll_started_monotonic,
            "position_poll_finished_s": self._position_poll_finished_monotonic,
            "position_poll_latency_s": (
                None
                if self._position_poll_started_monotonic is None or self._position_poll_finished_monotonic is None
                else max(0.0, self._position_poll_finished_monotonic - self._position_poll_started_monotonic)
            ),
            "safety_status_poll_started_s": self._safety_status_poll_started_monotonic,
            "safety_status_poll_finished_s": self._safety_status_poll_finished_monotonic,
            "safety_status_poll_latency_s": (
                None
                if self._safety_status_poll_started_monotonic is None or self._safety_status_poll_finished_monotonic is None
                else max(0.0, self._safety_status_poll_finished_monotonic - self._safety_status_poll_started_monotonic)
            ),
            "stop_reinforce_enabled": bool(getattr(self.config, "stop_reinforce_enabled", True)),
            "stop_reinforce_scheduled": self._stop_reinforce_scheduled,
            "stop_reinforce_sent": self._stop_reinforce_sent,
            "stop_reinforce_canceled": self._stop_reinforce_canceled,
            "last_command_diagnostics": {
                str(slave): dict(payload)
                for slave, payload in self._last_command_diagnostics.items()
            },
            "modbus_requests": self._diag_total_requests,
            "modbus_fc03": self._diag_total_fc03,
            "modbus_fc06": self._diag_total_fc06,
            "modbus_failures": self._diag_total_failures,
            "modbus_timeouts": self._diag_total_timeouts,
            "modbus_latency_last_s": self._diag_latency_last_s,
            "modbus_latency_min_s": self._diag_latency_min_s,
            "modbus_latency_avg_s": latency_avg,
            "modbus_latency_max_s": self._diag_latency_max_s,
            "modbus_last_error": self._diag_last_error or self.last_error,
            "position_interval_last_s": self._position_interval_last_s,
            "position_interval_min_s": self._position_interval_min_s,
            "position_interval_avg_s": position_interval_avg,
            "position_interval_max_s": self._position_interval_max_s,
            "status_interval_last_s": self._status_interval_last_s,
            "status_interval_min_s": self._status_interval_min_s,
            "status_interval_avg_s": status_interval_avg,
            "status_interval_max_s": self._status_interval_max_s,
            "background_position_skips": self._background_position_skips,
            "background_status_skips": self._background_status_skips,
        }

    def _close_serial(self) -> None:
        self._cancel_all_stop_reinforcements()
        if self.serial_port is None:
            return
        try:
            self.serial_port.close()
        except Exception:
            pass
        self.serial_port = None

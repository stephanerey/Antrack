"""Qt-facing antenna controller facade."""

from __future__ import annotations

import asyncio
from concurrent.futures import TimeoutError as FutureTimeoutError
import logging
from types import SimpleNamespace

from PyQt5.QtCore import QObject, pyqtSignal

from antrack.core.antenna.backend import AntennaBackend
from antrack.core.antenna.config import AntennaConnectionConfig, load_antenna_connection_config
from antrack.core.antenna.types import (
    AntennaConnectionMode,
    AntennaConnectionState,
    AntennaTelemetry,
    AntennaVersions,
)
from antrack.core.axis.axis_server_backend import AxisServerBackend
from antrack.core.axis.axis_driver_backend import AxisDriverBackend
from antrack.core.pstrotator.pstrotator_backend import PstRotatorBackend


class AntennaControllerQt(QObject):
    """UI-friendly facade for backend-neutral antenna operations."""

    antenna_position_updated = pyqtSignal(float, float)
    connection_failed = pyqtSignal(str)
    connection_succeeded = pyqtSignal()
    status_updated = pyqtSignal(dict)
    connection_state_changed = pyqtSignal(str)
    antenna_telemetry_updated = pyqtSignal(dict)
    versions_updated = pyqtSignal(dict)
    telemetry_updated = pyqtSignal(object)

    def __init__(
        self,
        backend: AntennaBackend,
        *,
        thread_manager,
        loop_name: str = "AntennaCoreLoop",
        polling_intervals: tuple[float, float] = (0.2, 1.0),
    ) -> None:
        super().__init__()
        self.backend = backend
        self.thread_manager = thread_manager
        self.loop_name = loop_name
        self.logger = logging.getLogger("AntennaController")
        self.polling_intervals = polling_intervals
        self.connection_state = AntennaConnectionState.DISCONNECTED
        self.server_status = self.connection_state
        self.axis_status = {
            "antenna": "STOPPED",
            "azimuth": "STOP",
            "elevation": "STOP",
        }
        self.antenna = SimpleNamespace(
            az=None,
            el=None,
            az_rate=0.0,
            el_rate=0.0,
            az_setrate=0.0,
            el_setrate=0.0,
            endstop_az=None,
            endstop_el=None,
            modbus_status_az=None,
            modbus_status_el=None,
            index_az=None,
            index_el=None,
            motor_alarm_az=None,
            motor_alarm_el=None,
            signal=None,
        )
        self.axisClient = self
        self.backend.set_disconnect_callback(self._on_backend_disconnected)

    @classmethod
    def from_settings(
        cls,
        settings,
        thread_manager,
        *,
        mode: str | AntennaConnectionMode | None = None,
    ) -> "AntennaControllerQt":
        config = load_antenna_connection_config(settings)
        if mode is not None:
            config.mode = AntennaConnectionMode.from_value(mode)
        backend = _make_backend_from_config(config)
        polling = _polling_intervals_for_config(config)
        return cls(backend, thread_manager=thread_manager, polling_intervals=polling)

    @property
    def backend_name(self) -> str:
        return self.backend.name

    def current_mode(self) -> AntennaConnectionMode:
        if isinstance(self.backend, AxisServerBackend):
            return AntennaConnectionMode.AXIS_SERVER
        if isinstance(self.backend, AxisDriverBackend):
            return AntennaConnectionMode.AXIS_DRIVER
        return AntennaConnectionMode.PST_ROTATOR

    def is_connected(self) -> bool:
        return self.backend.is_connected()

    def supports_manual_jog(self) -> bool:
        return self.backend.supports_manual_jog()

    def supports_absolute_targets(self) -> bool:
        return self.backend.supports_absolute_targets()

    def connect(self) -> bool:
        try:
            self.connection_state = AntennaConnectionState.CONNECTING
            self.server_status = self.connection_state
            self.connection_state_changed.emit(self.connection_state.value)
            self._run_backend_call(self.backend.connect)
            self._refresh_from_backend()
            if self.backend.is_connected():
                self.connection_succeeded.emit()
                self.connection_state_changed.emit(self.connection_state.value)
                return True
            raise ConnectionError(self.backend.get_last_error() or "Antenna connection failed")
        except Exception as exc:
            self.connection_state = AntennaConnectionState.ERROR
            self.server_status = self.connection_state
            message = str(exc)
            self.logger.error("Antenna connect failed: %s", message)
            self.connection_failed.emit(message)
            self.connection_state_changed.emit(self.connection_state.value)
            return False

    def disconnect(self) -> None:
        try:
            self.connection_state = AntennaConnectionState.DISCONNECTING
            self.server_status = self.connection_state
            self.connection_state_changed.emit(self.connection_state.value)
            self._run_backend_call(self.backend.disconnect)
        finally:
            self._refresh_from_backend()
            self.connection_state_changed.emit(self.connection_state.value)

    def snapshot(self) -> dict:
        self._refresh_from_backend()
        return self.backend.snapshot().to_dict()

    def get_antenna_telemetry(self) -> dict:
        self._refresh_from_backend()
        return self.backend.get_telemetry().to_dict()

    def emit_versions(self) -> None:
        try:
            versions = self._run_backend_call(self.backend.get_versions)
        except Exception as exc:
            self.logger.error("Version query failed: %s", exc)
            return
        self._refresh_versions(versions)
        self.versions_updated.emit(self._versions_to_dict(versions))

    def get_position(self):
        result = self._run_backend_call(
            self.backend.get_position,
            timeout=self._position_timeout(),
        )
        self._refresh_from_backend()
        az, el = result
        if az is not None and el is not None:
            try:
                self.antenna_position_updated.emit(float(az), float(el))
            except Exception:
                pass
        try:
            self.antenna_telemetry_updated.emit(self.get_antenna_telemetry())
        except Exception:
            pass
        return result

    def get_status(self):
        result = self._run_backend_call(
            self.backend.get_status,
            timeout=self._status_timeout(),
        )
        self._refresh_from_backend()
        try:
            if isinstance(result, dict):
                self.status_updated.emit(result)
        except Exception:
            pass
        try:
            self.telemetry_updated.emit(self.snapshot())
        except Exception:
            pass
        return result

    def set_target_position(self, azimuth: float, elevation: float, timeout: float | None = None) -> None:
        self._run_backend_call(
            lambda: self.backend.set_target_position(azimuth, elevation),
            timeout=timeout or self._target_command_timeout(),
        )
        self._refresh_from_backend()

    def set_az_speed(self, speed: float, timeout: float | None = None):
        result = self._run_backend_call(
            lambda: self.backend.set_az_speed(speed),
            timeout=timeout or self._motion_command_timeout(),
        )
        self._refresh_from_backend()
        return result

    def set_el_speed(self, speed: float, timeout: float | None = None):
        result = self._run_backend_call(
            lambda: self.backend.set_el_speed(speed),
            timeout=timeout or self._motion_command_timeout(),
        )
        self._refresh_from_backend()
        return result

    def move_cw(self, timeout: float | None = None):
        result = self._run_backend_call(self.backend.move_cw, timeout=timeout or self._motion_command_timeout())
        self.axis_status["azimuth"] = "CW"
        self._refresh_from_backend()
        return result

    def move_ccw(self, timeout: float | None = None):
        result = self._run_backend_call(self.backend.move_ccw, timeout=timeout or self._motion_command_timeout())
        self.axis_status["azimuth"] = "CCW"
        self._refresh_from_backend()
        return result

    def move_up(self, timeout: float | None = None):
        result = self._run_backend_call(self.backend.move_up, timeout=timeout or self._motion_command_timeout())
        self.axis_status["elevation"] = "UP"
        self._refresh_from_backend()
        return result

    def move_down(self, timeout: float | None = None):
        result = self._run_backend_call(self.backend.move_down, timeout=timeout or self._motion_command_timeout())
        self.axis_status["elevation"] = "DOWN"
        self._refresh_from_backend()
        return result

    def stop_az(self, timeout: float | None = None):
        result = self._run_backend_call(self.backend.stop_az, timeout=timeout or self._motion_command_timeout())
        self.axis_status["azimuth"] = "STOP"
        self._refresh_from_backend()
        return result

    def stop_el(self, timeout: float | None = None):
        result = self._run_backend_call(self.backend.stop_el, timeout=timeout or self._motion_command_timeout())
        self.axis_status["elevation"] = "STOP"
        self._refresh_from_backend()
        return result

    def _run_backend_call(self, coro_factory, timeout: float | None = None):
        if not getattr(self, "thread_manager", None):
            raise RuntimeError("ThreadManager is required for antenna controller operations")
        try:
            return self.thread_manager.run_coro(self.loop_name, coro_factory, timeout=timeout)
        except FutureTimeoutError as exc:
            op_name = getattr(coro_factory, "__name__", None) or getattr(getattr(coro_factory, "func", None), "__name__", None) or "backend_call"
            if timeout is None:
                raise TimeoutError(f"{op_name} timed out") from exc
            raise TimeoutError(f"{op_name} timed out after {float(timeout):.2f}s") from exc

    def _default_timeout(self) -> float:
        command_timeout = float(getattr(getattr(self.backend, "config", None), "command_timeout_s", 0.5))
        serial_timeout = float(getattr(getattr(self.backend, "config", None), "serial_timeout_s", 0.0))
        return max(1.0, (2.0 * command_timeout) + serial_timeout + 0.25)

    def _position_timeout(self) -> float:
        if isinstance(self.backend, AxisDriverBackend):
            command_timeout = float(getattr(self.backend.config, "command_timeout_s", 0.5))
            serial_timeout = float(getattr(self.backend.config, "serial_timeout_s", 0.15))
            return max(2.0, (4.0 * command_timeout) + (2.0 * serial_timeout) + 0.5)
        return self._default_timeout()

    def _status_timeout(self) -> float:
        if isinstance(self.backend, AxisDriverBackend):
            command_timeout = float(getattr(self.backend.config, "command_timeout_s", 0.5))
            serial_timeout = float(getattr(self.backend.config, "serial_timeout_s", 0.15))
            return max(5.0, (10.0 * command_timeout) + (8.0 * serial_timeout) + 0.5)
        return self._default_timeout()

    def _motion_command_timeout(self) -> float:
        if isinstance(self.backend, AxisDriverBackend):
            command_timeout = float(getattr(self.backend.config, "command_timeout_s", 0.5))
            serial_timeout = float(getattr(self.backend.config, "serial_timeout_s", 0.15))
            return max(6.0, self._status_timeout() + command_timeout + serial_timeout + 0.5)
        return self._default_timeout()

    def _target_command_timeout(self) -> float:
        if isinstance(self.backend, AxisDriverBackend):
            return self._motion_command_timeout()
        return self._default_timeout()

    def _on_backend_disconnected(self) -> None:
        self._refresh_from_backend()
        self.connection_failed.emit(self.backend.get_last_error() or "Antenna connection interrupted")
        self.connection_state_changed.emit(self.connection_state.value)

    def _refresh_from_backend(self) -> None:
        telemetry = self.backend.get_telemetry()
        self._refresh_telemetry(telemetry)
        self._refresh_versions(self.backend.snapshot().versions)
        self.connection_state = self.backend.get_connection_state()
        self.server_status = self.connection_state
        backend_axis_status = getattr(self.backend, "axis_status", None)
        if isinstance(backend_axis_status, dict):
            self.axis_status.update(backend_axis_status)

    def _refresh_telemetry(self, telemetry: AntennaTelemetry) -> None:
        for key, value in telemetry.to_dict().items():
            setattr(self.antenna, key, value)

    def _refresh_versions(self, versions: AntennaVersions) -> None:
        self._versions = versions

    @staticmethod
    def _versions_to_dict(versions: AntennaVersions) -> dict:
        return {
            "server_version": versions.server_version,
            "driver_version_az": versions.driver_version_az,
            "driver_version_el": versions.driver_version_el,
        }


def _make_backend_from_config(config: AntennaConnectionConfig) -> AntennaBackend:
    if config.mode == AntennaConnectionMode.AXIS_SERVER:
        return AxisServerBackend(config.axis_server)
    if config.mode == AntennaConnectionMode.AXIS_DRIVER:
        return AxisDriverBackend(config.axis_driver)
    return PstRotatorBackend(config.pst_rotator)


def _polling_intervals_for_config(config: AntennaConnectionConfig) -> tuple[float, float]:
    selected = config.selected_config
    position_interval = float(getattr(selected, "position_interval_s", 0.2))
    status_interval = float(getattr(selected, "status_interval_s", 1.0))
    return position_interval, status_interval

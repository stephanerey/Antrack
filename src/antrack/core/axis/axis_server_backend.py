"""Backend wrapper for the legacy Axis TCP server transport."""

from __future__ import annotations

import time

from antrack.core.antenna.backend import BaseAntennaBackend
from antrack.core.antenna.config import AxisServerConnectionConfig
from antrack.core.antenna.types import AntennaConnectionState, AntennaVersions
from antrack.core.axis.axis_client import Axis, ServerStatus


class AxisServerBackend(BaseAntennaBackend):
    """Expose the current Axis TCP client through the backend abstraction."""

    def __init__(self, config: AxisServerConnectionConfig) -> None:
        super().__init__("Axis Server")
        self.config = config
        self.axis = Axis(config.host, config.port)

    @property
    def axis_status(self) -> dict:
        return self.axis.axis_status

    @property
    def server_status(self):
        return self.axis.server_status

    def is_connected(self) -> bool:
        return self.axis.server_status == ServerStatus.CONNECTED

    async def connect(self) -> None:
        self.state = AntennaConnectionState.CONNECTING
        self.last_error = None
        self.axis.set_disconnect_callback(self._handle_core_disconnect)
        await self.axis.connect()
        self._sync_from_axis()
        if not self.is_connected():
            self.state = AntennaConnectionState.ERROR
            self.last_error = f"Unable to connect to Axis Server {self.config.host}:{self.config.port}"
            raise ConnectionError(self.last_error)
        await self.axis.get_versions()
        self._sync_from_axis()

    async def disconnect(self) -> None:
        self.state = AntennaConnectionState.DISCONNECTING
        try:
            self.axis.clear_disconnect_callbacks()
        except Exception:
            pass
        try:
            await self.axis.stop_keep_alive()
        except Exception:
            pass
        await self.axis.disconnect()
        self._sync_from_axis(force_state=AntennaConnectionState.DISCONNECTED)

    async def set_az_speed(self, speed: float) -> int | None:
        ack = await self.axis.set_az_speed(speed)
        if ack is not None:
            self.telemetry.az_setrate = float(speed)
        self._sync_from_axis()
        return ack

    async def set_el_speed(self, speed: float) -> int | None:
        ack = await self.axis.set_el_speed(speed)
        if ack is not None:
            self.telemetry.el_setrate = float(speed)
        self._sync_from_axis()
        return ack

    async def move_cw(self) -> int | None:
        ack = await self.axis.move_cw()
        self._sync_from_axis()
        return ack

    async def move_ccw(self) -> int | None:
        ack = await self.axis.move_ccw()
        self._sync_from_axis()
        return ack

    async def move_up(self) -> int | None:
        ack = await self.axis.move_up()
        self._sync_from_axis()
        return ack

    async def move_down(self) -> int | None:
        ack = await self.axis.move_down()
        self._sync_from_axis()
        return ack

    async def stop_az(self) -> int | None:
        ack = await self.axis.stop_az()
        self._sync_from_axis()
        return ack

    async def stop_el(self) -> int | None:
        ack = await self.axis.stop_el()
        self._sync_from_axis()
        return ack

    async def get_position(self) -> tuple[float | None, float | None]:
        result = await self.axis.get_position()
        self._sync_from_axis()
        return result

    async def get_status(self) -> dict:
        status = await self.axis.get_status()
        self._sync_from_axis()
        return status

    async def get_versions(self) -> AntennaVersions:
        await self.axis.get_versions()
        self._sync_from_axis()
        return self.versions

    def _handle_core_disconnect(self) -> None:
        self._sync_from_axis(force_state=AntennaConnectionState.DISCONNECTED)
        self._notify_disconnect()

    def _sync_from_axis(self, force_state: AntennaConnectionState | None = None) -> None:
        antenna = getattr(self.axis, "antenna", None)
        if antenna is not None:
            self.telemetry.az = getattr(antenna, "az", None)
            self.telemetry.el = getattr(antenna, "el", None)
            self.telemetry.az_rate = float(getattr(antenna, "az_rate", 0.0) or 0.0)
            self.telemetry.el_rate = float(getattr(antenna, "el_rate", 0.0) or 0.0)
            self.telemetry.az_setrate = float(getattr(antenna, "az_setrate", 0.0) or 0.0)
            self.telemetry.el_setrate = float(getattr(antenna, "el_setrate", 0.0) or 0.0)
            self.telemetry.endstop_az = getattr(antenna, "endstop_az", None)
            self.telemetry.endstop_el = getattr(antenna, "endstop_el", None)
            self.telemetry.modbus_status_az = getattr(antenna, "modbus_status_az", None)
            self.telemetry.modbus_status_el = getattr(antenna, "modbus_status_el", None)
            self.telemetry.signal = getattr(antenna, "signal", None)
            self.telemetry.last_update_monotonic = time.monotonic()

        info = getattr(self.axis, "server_info", None)
        if info is not None:
            self.versions.server_version = getattr(info, "server_version", None)
            self.versions.driver_version_az = getattr(info, "driver_version_az", None)
            self.versions.driver_version_el = getattr(info, "driver_version_el", None)

        if force_state is not None:
            self.state = force_state
        elif self.axis.server_status == ServerStatus.CONNECTED:
            self.state = AntennaConnectionState.CONNECTED
        elif self.axis.server_status == ServerStatus.CONNECTING:
            self.state = AntennaConnectionState.CONNECTING
        elif self.axis.server_status == ServerStatus.DISCONNECTING:
            self.state = AntennaConnectionState.DISCONNECTING
        elif self.axis.server_status == ServerStatus.ERROR:
            self.state = AntennaConnectionState.ERROR
        else:
            self.state = AntennaConnectionState.DISCONNECTED

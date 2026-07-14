"""Abstract backend interface for antenna transports."""

from __future__ import annotations

import copy
from abc import ABC, abstractmethod
from typing import Callable, List

from antrack.core.antenna.types import (
    AntennaConnectionState,
    AntennaStatusSnapshot,
    AntennaTelemetry,
    AntennaVersions,
)


class AntennaBackend(ABC):
    """Backend interface used by the Qt controller."""

    name: str

    @abstractmethod
    async def connect(self) -> None:
        raise NotImplementedError

    @abstractmethod
    async def disconnect(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def is_connected(self) -> bool:
        raise NotImplementedError

    @abstractmethod
    async def set_az_speed(self, speed: float) -> int | None:
        raise NotImplementedError

    @abstractmethod
    async def set_el_speed(self, speed: float) -> int | None:
        raise NotImplementedError

    @abstractmethod
    async def move_cw(self) -> int | None:
        raise NotImplementedError

    @abstractmethod
    async def move_ccw(self) -> int | None:
        raise NotImplementedError

    @abstractmethod
    async def move_up(self) -> int | None:
        raise NotImplementedError

    @abstractmethod
    async def move_down(self) -> int | None:
        raise NotImplementedError

    @abstractmethod
    async def stop_az(self) -> int | None:
        raise NotImplementedError

    @abstractmethod
    async def stop_el(self) -> int | None:
        raise NotImplementedError

    async def stop_all(self) -> None:
        await self.stop_az()
        await self.stop_el()

    @abstractmethod
    async def get_position(self) -> tuple[float | None, float | None]:
        raise NotImplementedError

    @abstractmethod
    async def get_status(self) -> dict:
        raise NotImplementedError

    @abstractmethod
    async def get_versions(self) -> AntennaVersions:
        raise NotImplementedError

    @abstractmethod
    def get_telemetry(self) -> AntennaTelemetry:
        raise NotImplementedError

    @abstractmethod
    def snapshot(self) -> AntennaStatusSnapshot:
        raise NotImplementedError

    @abstractmethod
    def get_connection_state(self) -> AntennaConnectionState:
        raise NotImplementedError

    @abstractmethod
    def get_last_error(self) -> str | None:
        raise NotImplementedError

    def supports_manual_jog(self) -> bool:
        return True

    async def manual_jog(self, axis: str, direction: str, speed: float) -> int | None:
        """Apply a manual speed and direction command.

        Backends may override this to transmit both values atomically.  The
        default keeps the existing two-command behaviour for transports that
        do not offer a combined operation.
        """
        axis_name = str(axis).strip().lower()
        direction_name = str(direction).strip().upper()
        if axis_name == "az":
            await self.set_az_speed(speed)
            if direction_name == "CW":
                return await self.move_cw()
            if direction_name == "CCW":
                return await self.move_ccw()
        elif axis_name == "el":
            await self.set_el_speed(speed)
            if direction_name == "UP":
                return await self.move_up()
            if direction_name == "DOWN":
                return await self.move_down()
        raise ValueError(f"Unsupported manual jog: axis={axis!r}, direction={direction!r}")

    def supports_absolute_targets(self) -> bool:
        return False

    async def set_target_position(self, azimuth: float, elevation: float) -> None:
        raise NotImplementedError(f"{self.name} does not support absolute target positioning")

    def set_disconnect_callback(self, callback: Callable[[], None]) -> None:
        raise NotImplementedError

    def clear_disconnect_callbacks(self) -> None:
        raise NotImplementedError


class BaseAntennaBackend(AntennaBackend):
    """Shared bookkeeping for concrete backends."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.state = AntennaConnectionState.DISCONNECTED
        self.last_error: str | None = None
        self.telemetry = AntennaTelemetry()
        self.versions = AntennaVersions()
        self._disconnect_callbacks: List[Callable[[], None]] = []

    def get_telemetry(self) -> AntennaTelemetry:
        return self.telemetry

    def get_connection_state(self) -> AntennaConnectionState:
        return self.state

    def get_last_error(self) -> str | None:
        return self.last_error

    def get_diagnostics_snapshot(self) -> dict:
        return {}

    def snapshot(self) -> AntennaStatusSnapshot:
        return AntennaStatusSnapshot(
            state=self.state,
            telemetry=copy.deepcopy(self.telemetry),
            versions=copy.deepcopy(self.versions),
            backend_name=self.name,
            last_error=self.last_error,
        )

    def set_disconnect_callback(self, callback: Callable[[], None]) -> None:
        if callback not in self._disconnect_callbacks:
            self._disconnect_callbacks.append(callback)

    def clear_disconnect_callbacks(self) -> None:
        self._disconnect_callbacks.clear()

    def _notify_disconnect(self) -> None:
        for callback in list(self._disconnect_callbacks):
            try:
                callback()
            except Exception:
                continue

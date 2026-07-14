"""Common antenna dataclasses and enums."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class AntennaConnectionMode(str, Enum):
    AXIS_SERVER = "axis_server"
    AXIS_DRIVER = "axis_driver"
    PST_ROTATOR = "pst_rotator"

    @classmethod
    def from_value(cls, value: object) -> "AntennaConnectionMode":
        raw = str(value or "").strip().lower()
        if not raw:
            return cls.AXIS_SERVER
        for item in cls:
            if item.value == raw:
                return item
        raise ValueError(f"Unsupported antenna connection mode: {value}")


class AntennaAxisMotion(str, Enum):
    STOP = "STOP"
    CW = "CW"
    CCW = "CCW"
    UP = "UP"
    DOWN = "DOWN"


class AntennaConnectionState(str, Enum):
    DISCONNECTED = "DISCONNECTED"
    CONNECTING = "CONNECTING"
    CONNECTED = "CONNECTED"
    DISCONNECTING = "DISCONNECTING"
    DEGRADED = "DEGRADED"
    ERROR = "ERROR"


@dataclass
class AntennaTelemetry:
    az: Optional[float] = None
    el: Optional[float] = None
    az_raw: Optional[int] = None
    el_raw: Optional[int] = None
    az_rate: float = 0.0
    el_rate: float = 0.0
    az_setrate: float = 0.0
    el_setrate: float = 0.0
    endstop_az: Optional[int] = None
    endstop_el: Optional[int] = None
    modbus_status_az: Optional[int] = None
    modbus_status_el: Optional[int] = None
    index_az: Optional[int] = None
    index_el: Optional[int] = None
    motor_alarm_az: Optional[int] = None
    motor_alarm_el: Optional[int] = None
    status_update_monotonic: Optional[float] = None
    status_update_timestamp: Optional[float] = None
    signal: Optional[int] = None
    last_update_monotonic: Optional[float] = None

    def to_dict(self) -> dict:
        return {
            "az": self.az,
            "el": self.el,
            "az_raw": self.az_raw,
            "el_raw": self.el_raw,
            "az_rate": self.az_rate,
            "el_rate": self.el_rate,
            "az_setrate": self.az_setrate,
            "el_setrate": self.el_setrate,
            "endstop_az": self.endstop_az,
            "endstop_el": self.endstop_el,
            "modbus_status_az": self.modbus_status_az,
            "modbus_status_el": self.modbus_status_el,
            "index_az": self.index_az,
            "index_el": self.index_el,
            "motor_alarm_az": self.motor_alarm_az,
            "motor_alarm_el": self.motor_alarm_el,
            "status_update_monotonic": self.status_update_monotonic,
            "status_update_timestamp": self.status_update_timestamp,
            "signal": self.signal,
            "last_update_monotonic": self.last_update_monotonic,
        }


@dataclass
class AntennaVersions:
    server_version: Optional[str] = None
    driver_version_az: Optional[str] = None
    driver_version_el: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "server_version": self.server_version,
            "driver_version_az": self.driver_version_az,
            "driver_version_el": self.driver_version_el,
        }


@dataclass
class AntennaStatusSnapshot:
    state: AntennaConnectionState = AntennaConnectionState.DISCONNECTED
    telemetry: AntennaTelemetry = field(default_factory=AntennaTelemetry)
    versions: AntennaVersions = field(default_factory=AntennaVersions)
    backend_name: str = ""
    last_error: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "antenna": self.telemetry.to_dict(),
            "server": {
                "connection": self.state.value,
                "server_version": self.versions.server_version,
                "driver_version_az": self.versions.driver_version_az,
                "driver_version_el": self.versions.driver_version_el,
                "backend_name": self.backend_name,
                "last_error": self.last_error,
            },
        }

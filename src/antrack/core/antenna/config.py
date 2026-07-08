"""Antenna backend configuration parsing."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict

from antrack.core.antenna.types import AntennaConnectionMode


class AntennaConfigError(ValueError):
    """Raised when antenna connection settings are invalid."""


@dataclass
class AxisServerConnectionConfig:
    host: str = "192.168.1.48"
    port: int = 10000
    connect_timeout_s: float = 2.0
    command_timeout_s: float = 0.8
    keepalive_interval_s: float = 1.0
    position_interval_s: float = 0.2
    status_interval_s: float = 1.0


@dataclass
class AxisDriverConnectionConfig:
    comport: str = "COM11"
    baudrate: int = 38400
    az_slave_address: int = 10
    el_slave_address: int = 20
    serial_timeout_s: float = 0.05
    command_timeout_s: float = 0.25
    position_interval_s: float = 0.15
    status_interval_s: float = 1.0
    health_interval_s: float = 2.0
    inter_request_gap_s: float = 0.005
    background_position_defer_commands: bool = True
    status_read_mode: str = "minimal_single_register"
    status_include_position: bool = False
    move_refresh_mode: str = "edge_only"
    move_refresh_interval_s: float = 0.0
    stop_reinforce_enabled: bool = True
    stop_reinforce_delay_s: float = 0.12
    stop_reinforce_count: int = 1
    legacy_accept_short_fc6_response: bool = True


@dataclass
class PstRotatorConnectionConfig:
    host: str = "127.0.0.1"
    udp_port: int = 12000
    response_port: int = 12001
    command_timeout_s: float = 0.5
    position_interval_s: float = 0.5
    status_interval_s: float = 1.0


@dataclass
class AntennaConnectionConfig:
    mode: AntennaConnectionMode = AntennaConnectionMode.AXIS_SERVER
    axis_server: AxisServerConnectionConfig = field(default_factory=AxisServerConnectionConfig)
    axis_driver: AxisDriverConnectionConfig = field(default_factory=AxisDriverConnectionConfig)
    pst_rotator: PstRotatorConnectionConfig = field(default_factory=PstRotatorConnectionConfig)

    @property
    def selected_config(self) -> object:
        if self.mode == AntennaConnectionMode.AXIS_SERVER:
            return self.axis_server
        if self.mode == AntennaConnectionMode.AXIS_DRIVER:
            return self.axis_driver
        return self.pst_rotator


def _section(settings: Dict[str, Dict[str, Any]], name: str) -> Dict[str, Any]:
    if not isinstance(settings, dict):
        return {}
    return settings.get(name, settings.get(name.lower(), {})) or {}


def _get(section: Dict[str, Any], key: str, default: Any) -> Any:
    if not isinstance(section, dict):
        return default
    return section.get(key.lower(), section.get(key.upper(), default))


def _as_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _axis_driver_status_read_mode(value: Any, default: str = "minimal_single_register") -> str:
    mode = str(value if value is not None else default).strip().lower()
    if mode in {"block", "single_register", "minimal_single_register"}:
        return mode
    raise AntennaConfigError(
        "Invalid AXIS_DRIVER STATUS_READ_MODE. Allowed values: 'block', 'single_register', 'minimal_single_register'."
    )


def _axis_driver_move_refresh_mode(value: Any, default: str = "edge_only") -> str:
    mode = str(value if value is not None else default).strip().lower()
    if mode in {"edge_only", "interval"}:
        return mode
    raise AntennaConfigError(
        "Invalid AXIS_DRIVER MOVE_REFRESH_MODE. Allowed values: 'edge_only', 'interval'."
    )


def load_antenna_connection_config(settings: Dict[str, Dict[str, Any]]) -> AntennaConnectionConfig:
    antenna_section = _section(settings, "ANTENNA_CONNECTION")
    try:
        mode = AntennaConnectionMode.from_value(_get(antenna_section, "mode", AntennaConnectionMode.AXIS_SERVER.value))
    except ValueError as exc:
        raise AntennaConfigError(str(exc)) from exc

    axis_server_section = _section(settings, "AXIS_SERVER")
    axis_driver_section = _section(settings, "AXIS_DRIVER")
    pst_section = _section(settings, "PST_ROTATOR")

    return AntennaConnectionConfig(
        mode=mode,
        axis_server=AxisServerConnectionConfig(
            host=str(_get(axis_server_section, "ip_address", "192.168.1.48")),
            port=int(_get(axis_server_section, "port", 10000)),
            connect_timeout_s=float(_get(axis_server_section, "connect_timeout_s", 2.0)),
            command_timeout_s=float(_get(axis_server_section, "command_timeout_s", 0.8)),
            keepalive_interval_s=float(_get(axis_server_section, "keepalive_interval_s", 1.0)),
            position_interval_s=float(_get(axis_server_section, "position_interval_s", 0.2)),
            status_interval_s=float(_get(axis_server_section, "status_interval_s", 1.0)),
        ),
        axis_driver=AxisDriverConnectionConfig(
            comport=str(_get(axis_driver_section, "comport", "COM11")),
            baudrate=int(_get(axis_driver_section, "baudrate", 38400)),
            az_slave_address=int(_get(axis_driver_section, "az_slave_address", 10)),
            el_slave_address=int(_get(axis_driver_section, "el_slave_address", 20)),
            serial_timeout_s=float(_get(axis_driver_section, "serial_timeout_s", 0.05)),
            command_timeout_s=float(_get(axis_driver_section, "command_timeout_s", 0.25)),
            position_interval_s=float(_get(axis_driver_section, "position_interval_s", 0.15)),
            status_interval_s=float(_get(axis_driver_section, "status_interval_s", 1.0)),
            health_interval_s=float(_get(axis_driver_section, "health_interval_s", 2.0)),
            inter_request_gap_s=float(_get(axis_driver_section, "inter_request_gap_s", 0.005)),
            background_position_defer_commands=_as_bool(
                _get(axis_driver_section, "background_position_defer_commands", True),
                True,
            ),
            status_read_mode=_axis_driver_status_read_mode(
                _get(axis_driver_section, "status_read_mode", "minimal_single_register"),
                "minimal_single_register",
            ),
            status_include_position=_as_bool(
                _get(axis_driver_section, "status_include_position", False),
                False,
            ),
            move_refresh_mode=_axis_driver_move_refresh_mode(
                _get(axis_driver_section, "move_refresh_mode", "edge_only"),
                "edge_only",
            ),
            move_refresh_interval_s=float(_get(axis_driver_section, "move_refresh_interval_s", 0.0)),
            stop_reinforce_enabled=_as_bool(
                _get(axis_driver_section, "stop_reinforce_enabled", True),
                True,
            ),
            stop_reinforce_delay_s=float(_get(axis_driver_section, "stop_reinforce_delay_s", 0.12)),
            stop_reinforce_count=int(_get(axis_driver_section, "stop_reinforce_count", 1)),
            legacy_accept_short_fc6_response=_as_bool(
                _get(axis_driver_section, "legacy_accept_short_fc6_response", True),
                True,
            ),
        ),
        pst_rotator=PstRotatorConnectionConfig(
            host=str(_get(pst_section, "host", "127.0.0.1")),
            udp_port=int(_get(pst_section, "udp_port", 12000)),
            response_port=int(_get(pst_section, "response_port", 12001)),
            command_timeout_s=float(_get(pst_section, "command_timeout_s", 0.5)),
            position_interval_s=float(_get(pst_section, "position_interval_s", 0.5)),
            status_interval_s=float(_get(pst_section, "status_interval_s", 1.0)),
        ),
    )

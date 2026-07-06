"""Backend-neutral antenna control primitives."""

from antrack.core.antenna.backend import AntennaBackend, BaseAntennaBackend
from antrack.core.antenna.config import (
    AntennaConfigError,
    AntennaConnectionConfig,
    AxisDriverConnectionConfig,
    AxisServerConnectionConfig,
    PstRotatorConnectionConfig,
    load_antenna_connection_config,
)
from antrack.core.antenna.types import (
    AntennaAxisMotion,
    AntennaConnectionMode,
    AntennaConnectionState,
    AntennaStatusSnapshot,
    AntennaTelemetry,
    AntennaVersions,
)

__all__ = [
    "AntennaAxisMotion",
    "AntennaBackend",
    "AntennaConfigError",
    "AntennaConnectionConfig",
    "AntennaConnectionMode",
    "AntennaConnectionState",
    "AntennaStatusSnapshot",
    "AntennaTelemetry",
    "AntennaVersions",
    "AxisDriverConnectionConfig",
    "AxisServerConnectionConfig",
    "BaseAntennaBackend",
    "PstRotatorConnectionConfig",
    "load_antenna_connection_config",
]

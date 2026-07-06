"""Legacy compatibility wrapper for Axis Server mode."""

from __future__ import annotations

from antrack.core.antenna.controller_qt import AntennaControllerQt
from antrack.core.axis.axis_server_backend import AxisServerBackend
from antrack.core.antenna.config import AxisServerConnectionConfig


class AxisClientQt(AntennaControllerQt):
    """Backward-compatible wrapper that keeps the old import path working."""

    def __init__(self, ip_address, port):
        backend = AxisServerBackend(
            AxisServerConnectionConfig(
                host=str(ip_address),
                port=int(port),
            )
        )
        super().__init__(backend, thread_manager=None)

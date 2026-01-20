"""Qt wrapper for PowermeterClient."""

from __future__ import annotations

import logging
from typing import Optional

from PyQt5.QtCore import QObject, pyqtSignal

from antrack.core.instruments.powermeter_client import PowermeterClient


class Powermeter(QObject):
    """Qt wrapper exposing signals around PowermeterClient."""

    power_ready = pyqtSignal(float)
    error = pyqtSignal(str)
    status = pyqtSignal(str)

    def __init__(self, settings: dict, logger: Optional[logging.Logger] = None, parent=None):
        super().__init__(parent)
        self.logger = logger or logging.getLogger("Powermeter")
        self._client = PowermeterClient(
            settings,
            logger=self.logger,
            status_callback=self._emit_status,
        )

    @property
    def client(self) -> PowermeterClient:
        """Return the underlying PowermeterClient instance."""
        return self._client

    def read_power(self) -> float:
        """Read a single power measurement (dBm) and emit signals."""
        try:
            val = self._client.read_power()
            try:
                self.power_ready.emit(val)
            except Exception:
                pass
            return val
        except Exception as exc:
            self.logger.error(f"read_power: ERROR: {exc}")
            try:
                self.error.emit(str(exc))
            except Exception:
                pass
            raise

    def close(self) -> None:
        """Close the underlying serial connection."""
        self._client.close()

    def _emit_status(self, msg: str) -> None:
        try:
            self.status.emit(msg)
        except Exception:
            pass

    @staticmethod
    def extract_power_from_text(text: str) -> Optional[float]:
        """Compatibility helper; delegates to PowermeterClient."""
        return PowermeterClient.extract_power_from_text(text)

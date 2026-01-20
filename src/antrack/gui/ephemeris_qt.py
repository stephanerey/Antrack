"""Qt adapter for EphemerisService signals."""

from __future__ import annotations

from PyQt5.QtCore import QObject, pyqtSignal

from antrack.tracking.ephemeris_service import EphemerisService


class EphemerisQtAdapter(QObject):
    """Bridge EphemerisService callbacks to a Qt signal."""

    pose_updated = pyqtSignal(str, dict)

    def __init__(self, service: EphemerisService, parent=None) -> None:
        super().__init__(parent)
        self._service = service
        self._service.pose_updated.connect(self._on_pose_updated)

    @property
    def service(self) -> EphemerisService:
        """Return the underlying EphemerisService instance."""
        return self._service

    def _on_pose_updated(self, key: str, payload: dict) -> None:
        self.pose_updated.emit(key, payload)

    def __getattr__(self, name: str):
        return getattr(self._service, name)

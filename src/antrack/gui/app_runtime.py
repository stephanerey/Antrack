"""Qt application helpers for startup and error reporting."""

from __future__ import annotations

import sys
from typing import Optional

from PyQt5.QtWidgets import QApplication, QMessageBox


def get_or_create_app(argv: Optional[list[str]] = None) -> QApplication:
    """Return the QApplication instance, creating it if needed."""
    app = QApplication.instance()
    if app is None:
        app = QApplication(argv or sys.argv)
    return app


def show_startup_error(message: str, title: str = "Startup error") -> None:
    """Display a modal startup error message."""
    get_or_create_app()
    QMessageBox.critical(None, title, message)

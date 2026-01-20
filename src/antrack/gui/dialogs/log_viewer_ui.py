"""Log viewer dialog for the application log file."""

from __future__ import annotations

from collections import deque

from PyQt5.QtCore import QUrl
from PyQt5.QtGui import QDesktopServices
from PyQt5.QtWidgets import QDialog, QHBoxLayout, QMessageBox, QPushButton, QPlainTextEdit, QVBoxLayout

from antrack.utils.paths import get_log_file, get_logs_dir


class LogViewerDialog(QDialog):
    """Dialog showing the current application log with refresh controls."""

    def __init__(self, parent=None, max_lines: int = 2000) -> None:
        super().__init__(parent)
        self.log_dir = get_logs_dir()
        self.log_file = get_log_file()
        self.max_lines = max_lines
        self._build_ui()
        self.refresh()

    def _build_ui(self) -> None:
        self.setWindowTitle("Journal de l'application")
        self.resize(900, 600)
        layout = QVBoxLayout(self)

        self.text = QPlainTextEdit(self)
        self.text.setReadOnly(True)
        layout.addWidget(self.text)

        btns = QHBoxLayout()
        self.refresh_btn = QPushButton("Rafraichir", self)
        self.open_btn = QPushButton("Ouvrir le dossier", self)
        self.close_btn = QPushButton("Fermer", self)

        self.refresh_btn.clicked.connect(self.refresh)
        self.open_btn.clicked.connect(self.open_folder)
        self.close_btn.clicked.connect(self.close)

        for b in (self.refresh_btn, self.open_btn, self.close_btn):
            btns.addWidget(b)
        layout.addLayout(btns)

    def refresh(self) -> None:
        """Reload the log contents."""
        if not self.log_file.exists():
            QMessageBox.information(self, "Journal", "Fichier de log introuvable.")
            return
        try:
            with self.log_file.open("r", encoding="utf-8", errors="replace") as handle:
                lines = list(deque(handle, maxlen=self.max_lines))
        except Exception as exc:
            QMessageBox.warning(self, "Journal", f"Impossible de lire le log:\n{exc}")
            return
        self.text.setPlainText("".join(lines))
        self.text.moveCursor(self.text.textCursor().End)

    def open_folder(self) -> None:
        """Open the logs directory in the OS file explorer."""
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.log_dir)))

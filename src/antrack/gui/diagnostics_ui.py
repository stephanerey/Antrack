"""Top-level diagnostics and utility dialog wiring for MainUi."""

from __future__ import annotations

from PyQt5.QtWidgets import QAction, QDialog, QMessageBox, QVBoxLayout

from antrack.app_info import version
from antrack.gui.diagnostics.diagnostics_ui import ThreadDiagnosticsUI
from antrack.gui.dialogs.log_viewer_ui import LogViewerDialog


class DiagnosticsUiMixin:
    """Menu and dialog wiring kept out of the main composition root."""

    def setup_menu(self):
        """Configure the application menu."""
        menu_bar = self.menuBar()

        file_menu = menu_bar.addMenu("Files")
        exit_action = QAction("Quit", self)
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        tools_menu = menu_bar.addMenu("Tools")
        thread_diag_action = QAction("Threads diagnosis", self)
        thread_diag_action.triggered.connect(self.show_thread_diagnostics)
        tools_menu.addAction(thread_diag_action)

        help_menu = menu_bar.addMenu("Help")
        view_log_action = QAction("Display logs...", self)
        view_log_action.triggered.connect(self.show_log_viewer)
        help_menu.addAction(view_log_action)
        about_action = QAction("About", self)
        about_action.triggered.connect(self.show_about)
        help_menu.addAction(about_action)

    def show_thread_diagnostics(self):
        """Show the thread diagnostics dialog."""
        try:
            dlg = QDialog(self)
            dlg.setWindowTitle("Thread diagnostics")
            dlg.resize(900, 650)
            layout = QVBoxLayout(dlg)
            layout.addWidget(ThreadDiagnosticsUI(self.thread_manager, parent=dlg))
            dlg.exec_()
        except Exception as exc:
            self.logger.error(f"Erreur lors de l'affichage du diagnostic des threads: {exc}")
            QMessageBox.warning(
                self,
                "Erreur",
                f"Impossible d'afficher le diagnostic des threads: {str(exc)}",
            )

    def show_about(self):
        """Display the About dialog."""
        QMessageBox.about(
            self,
            "A propos d'Antenna Tracker",
            f"Antenna Noise Tracker {version}\n\n"
            f"Author: Stephane Rey\n"
            f"Date: 10.09.2025",
        )

    def show_log_viewer(self):
        """Show the log viewer dialog."""
        try:
            LogViewerDialog(self).exec_()
        except Exception as exc:
            self.logger.error(f"Erreur show_log_viewer: {exc}")
            QMessageBox.warning(self, "Journal", f"Erreur lors de l'affichage du journal:{exc}")

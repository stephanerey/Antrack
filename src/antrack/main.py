# Antenna Noise Tracker
# Author : Stephane Rey
# Date   : 07.07.2023

from pathlib import Path
import sys

# Allow direct execution via `python main.py` from `src/antrack`.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from antrack.app_info import display_version

import logging
from logging.handlers import TimedRotatingFileHandler
from PyQt5.QtWidgets import QApplication, QMessageBox
from PyQt5.QtCore import QTimer

from antrack.core.antenna.config import load_antenna_connection_config
from antrack.threading_utils.thread_manager import ThreadManager
from antrack.tracking.tracking_manager import TrackingManager
from antrack.utils.paths import get_log_file, get_logs_dir
from antrack.utils.settings_loader import load_settings, resolve_settings_path
from antrack.gui.main_ui import MainUi

# Configuration du logging (console + fichier tournant quotidien, conservation 7 jours)
log_dir = get_logs_dir()
log_dir.mkdir(parents=True, exist_ok=True)
log_file = get_log_file()

root_logger = logging.getLogger()
root_logger.setLevel(logging.INFO)

# Nettoyer d'éventuels handlers déjà présents (si basicConfig a été appelé ailleurs)
if root_logger.handlers:
    for h in list(root_logger.handlers):
        root_logger.removeHandler(h)

console_handler = logging.StreamHandler(sys.stdout)
console_handler.setLevel(logging.INFO)
console_handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))

file_handler = TimedRotatingFileHandler(
    str(log_file),
    when="D",
    interval=1,
    backupCount=7,
    encoding="utf-8",
    utc=False,
)
file_handler.setLevel(logging.INFO)
file_handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))

root_logger.addHandler(console_handler)
root_logger.addHandler(file_handler)

logger = logging.getLogger("main")


def main() -> int:
    app_version = display_version()
    logger.info(
        "\n"
        "================================================\n"
        f"Démarrage de l'application Antenna Noise Tracker {app_version}\n"
        "================================================"
    )
    logger.info(f"Version application: {app_version}")

    app = None
    thread_manager = None

    try:
        # Settings
        settings_path = resolve_settings_path()
        logger.info(f"Chargement des paramètres depuis: {settings_path}")
        settings = load_settings(settings_path)
        logger.info("Paramètres chargés avec succès")

        antenna_config = load_antenna_connection_config(settings)
        perf_settings = settings.get("PERFORMANCE", settings.get("performance", {})) if isinstance(settings, dict) else {}
        max_workers = int(perf_settings.get("max_workers", 4))
        logger.info(
            "Configuration antenne: mode=%s axis_server=%s:%s",
            antenna_config.mode.value,
            antenna_config.axis_server.host,
            antenna_config.axis_server.port,
        )

        # Qt app
        app = QApplication(sys.argv)
        app.setApplicationName("Antenna Noise Tracker")

        # Thread manager
        thread_manager = ThreadManager(max_workers=max_workers)
        thread_manager.tracking_manager = TrackingManager(thread_manager=thread_manager, settings=settings)
        logger.info("Gestionnaire de threads initialisé")

        # UI
        ui = MainUi(thread_manager=thread_manager, settings=settings)
        ui.show()
        logger.info("Interface graphique initialisée")

        exit_code = app.exec_()

        logger.info("Fermeture de l'application...")
        thread_manager.shutdown(graceful=True, timeout_s=0.5)

        return int(exit_code)

    except Exception as e:
        logger.exception("Erreur lors de l'initialisation de l'application")

        # Ensure a QApplication exists before showing a QMessageBox
        if QApplication.instance() is None:
            app = QApplication(sys.argv)

        QMessageBox.critical(
            None,
            "Erreur de démarrage",
            f"L'application n'a pas pu démarrer correctement: {str(e)}\n\n"
            "Veuillez consulter les logs pour plus de détails."
        )

        # Best-effort cleanup
        if thread_manager is not None:
            try:
                thread_manager.stop_all_threads()
            except Exception:
                logger.exception("Erreur pendant le nettoyage des threads")

        return 1


if __name__ == "__main__":
    raise SystemExit(main())

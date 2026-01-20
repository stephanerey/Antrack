# Antenna Noise Tracker
# Author : Stephane Rey
# Date   : 07.07.2023


from antrack.app_info import version

import sys
import os
import logging
from logging.handlers import TimedRotatingFileHandler
from PyQt5.QtWidgets import QApplication, QMessageBox
from PyQt5.QtCore import QTimer

from antrack.threading_utils.thread_manager import ThreadManager
from antrack.utils.settings_loader import load_settings
from antrack.gui.main_ui import MainUi

# Configuration du logging (console + fichier tournant quotidien, conservation 7 jours)
log_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'logs'))
os.makedirs(log_dir, exist_ok=True)
log_file = os.path.join(log_dir, 'antenna_tracker.log')

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
    log_file, when='midnight', backupCount=7, encoding='utf-8', utc=False
)
file_handler.setLevel(logging.INFO)
file_handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))

root_logger.addHandler(console_handler)
root_logger.addHandler(file_handler)

logger = logging.getLogger("main")


def _resolve_settings_path() -> str:
    """Return an absolute path to settings.cfg (prefer package-local, fallback one level up)."""
    base_dir = os.path.abspath(os.path.dirname(__file__))
    candidate_paths = [
        os.path.join(base_dir, "settings.cfg"),
        os.path.abspath(os.path.join(base_dir, "..", "settings.cfg")),
    ]
    return next((p for p in candidate_paths if os.path.exists(p)), candidate_paths[0])


def main() -> int:
    logger.info(
        "\n"
        "================================================\n"
        "Démarrage de l'application Antenna Noise Tracker\n"
        "================================================"
    )

    app = None
    thread_manager = None

    try:
        # Settings
        settings_path = _resolve_settings_path()
        logger.info(f"Chargement des paramètres depuis: {settings_path}")
        settings = load_settings(settings_path)
        logger.info("Paramètres chargés avec succès")

        ip = settings["AXIS_SERVER"]["ip_address"]
        port = settings["AXIS_SERVER"]["port"]
        logger.info(f"Configuration serveur: {ip}:{port}")

        # Qt app
        app = QApplication(sys.argv)
        app.setApplicationName("Antenna Noise Tracker")

        # Thread manager
        thread_manager = ThreadManager()
        logger.info("Gestionnaire de threads initialisé")

        # UI
        ui = MainUi(thread_manager=thread_manager, settings=settings, ip_address=ip, port=port)
        ui.show()
        logger.info("Interface graphique initialisée")

        # Cleanup hook
        app.aboutToQuit.connect(thread_manager.stop_all_threads)

        exit_code = app.exec_()

        logger.info("Fermeture de l'application...")
        thread_manager.stop_all_threads()

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

if __name__ == "__main__":
    main()

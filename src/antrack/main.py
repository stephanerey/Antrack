"""Application entry point for Antenna Noise Tracker."""

from __future__ import annotations

import logging
import sys
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

from antrack.gui.app_runtime import get_or_create_app, show_startup_error
from antrack.gui.main_ui import MainUi
from antrack.threading_utils.thread_manager import ThreadManager
from antrack.utils.paths import get_log_file, get_logs_dir
from antrack.utils.settings_loader import load_settings, resolve_settings_path

logger = logging.getLogger("main")


def _init_logging() -> Path:
    """Initialize console + rotating file logging and return log file path."""
    log_dir = get_logs_dir()
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = get_log_file()

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)

    if root_logger.handlers:
        for handler in list(root_logger.handlers):
            root_logger.removeHandler(handler)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(
        logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    )

    file_handler = TimedRotatingFileHandler(
        log_file, when="midnight", backupCount=7, encoding="utf-8", utc=False
    )
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    )

    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)
    return log_file


def main() -> int:
    _init_logging()
    logger.info(
        "\n"
        "================================================\n"
        "Demarrage de l'application Antenna Noise Tracker\n"
        "================================================"
    )

    app = None
    thread_manager = None

    try:
        settings_path = resolve_settings_path()
        logger.info("Chargement des parametres depuis: %s", settings_path)
        settings = load_settings(settings_path)
        logger.info("Parametres charges avec succes")

        ip = settings["AXIS_SERVER"]["ip_address"]
        port = settings["AXIS_SERVER"]["port"]
        logger.info("Configuration serveur: %s:%s", ip, port)

        app = get_or_create_app()
        app.setApplicationName("Antenna Noise Tracker")

        thread_manager = ThreadManager()
        logger.info("Gestionnaire de threads initialise")

        ui = MainUi(thread_manager=thread_manager, settings=settings, ip_address=ip, port=port)
        ui.show()
        logger.info("Interface graphique initialisee")

        app.aboutToQuit.connect(thread_manager.shutdown)

        exit_code = app.exec_()

        logger.info("Fermeture de l'application...")
        thread_manager.shutdown()

        return int(exit_code)

    except Exception as e:
        logger.exception("Erreur lors de l'initialisation de l'application")
        show_startup_error(
            "L'application n'a pas pu demarrer correctement:\n"
            f"{str(e)}\n\nVeuillez consulter les logs pour plus de details.",
            title="Erreur de demarrage",
        )

        if thread_manager is not None:
            try:
                thread_manager.shutdown()
            except Exception:
                logger.exception("Erreur pendant le nettoyage des threads")

        return 1


if __name__ == "__main__":
    raise SystemExit(main())

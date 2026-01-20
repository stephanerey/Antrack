# axis_client_qt.py
# Wrapper Qt pour Axis (non-Qt) avec signaux PyQt5

import logging
import time
import time
from PyQt5.QtCore import QObject, pyqtSignal
from antrack.core.axis.axis_client import Axis, AxisCommand, ServerStatus

logger = logging.getLogger("AxisClientQt")


class AxisClientQt(QObject):
    """Client Axis avec signaux Qt"""
    antenna_position_updated = pyqtSignal(float, float)  # az, el
    connection_failed = pyqtSignal(str)  # message d'erreur
    connection_succeeded = pyqtSignal()  # connexion réussie
    status_updated = pyqtSignal(dict)  # status complet
    connection_state_changed = pyqtSignal(str)  # 'CONNECTED' | 'DISCONNECTED'
    antenna_telemetry_updated = pyqtSignal(dict)  # {'az','el','az_rate','el_rate','az_setrate','el_setrate','endstop_az','endstop_el'}
    versions_updated = pyqtSignal(dict)  # {'server_version','driver_version_az','driver_version_el'}
    telemetry_updated = pyqtSignal(object)  # dict: {'antenna': ..., 'server': ...}

    def __init__(self, ip_address, port):
        super().__init__()
        self.ip_address = ip_address
        self.port = port
        self.connected = False
        self.logger = logging.getLogger("AxisClientQt")

        self.axisClient = Axis(ip_address, port)

        # Expose l'état antenne du client Axis
        self.antenna = self.axisClient.antenna

        self.thread_manager = None


    def is_connected(self):
        """Vérifie si le client est connecté """
        try:
            return getattr(self.axisClient, "server_status", None) == ServerStatus.CONNECTED
        except Exception:
            return False

    def get_antenna_telemetry(self) -> dict:
        """
        Retourne un dict de télémétrie antenne (sans versions serveur).
        Contenu: az, el, az_rate, el_rate, az_setrate, el_setrate, endstop_az, endstop_el
        """
        try:
            a = self.axisClient.antenna
            return {
                'az': a.az,
                'el': a.el,
                'az_rate': a.az_rate,
                'el_rate': a.el_rate,
                'az_setrate': a.az_setrate,
                'el_setrate': a.el_setrate,
                'endstop_az': a.endstop_az,
                'endstop_el': a.endstop_el,
            }
        except Exception:
            return {}

    def emit_versions(self):
        """
        Émet le signal versions_updated avec les versions serveur/drivers.
        - Si nécessaire, déclenche une récupération via le core avant émission.
        """
        try:
            # Si les versions ne sont pas encore disponibles, les récupérer
            info = getattr(self.axisClient, "server_info", None)
            need_fetch = not info or (info.server_version is None and info.driver_version_az is None and info.driver_version_el is None)
            if need_fetch and getattr(self, "thread_manager", None):
                try:
                    self.thread_manager.run_coro("AxisCoreLoop", lambda: self.axisClient.get_versions())
                    info = getattr(self.axisClient, "server_info", None)
                except Exception as e:
                    self.logger.error(f"Récupération des versions impossible: {e}")

            if info:
                self.versions_updated.emit({
                    'server_version': getattr(info, 'server_version', None),
                    'driver_version_az': getattr(info, 'driver_version_az', None),
                    'driver_version_el': getattr(info, 'driver_version_el', None),
                })
        except Exception as e:
            self.logger.error(f"Emission des versions impossible: {e}")

    def snapshot(self):
        """
        Retourne un instantané dict combinant antenne et serveur, prêt à être émis.
        """
        try:
            return self.axisClient.snapshot().to_dict()
        except Exception:
            return {}
        

    def get_position(self):
        """Récupère la position actuelle (azimut, élévation) via le client (async) en passant par ThreadManager."""
        try:
            if not getattr(self, "thread_manager", None):
                self.logger.error("ThreadManager manquant pour exécuter les coroutines du core")
                return None, None
            return self.thread_manager.run_coro("AxisCoreLoop", lambda: self.axisClient.get_position())
        except Exception as e:
            self.logger.error(f"Erreur lors de la lecture de position (core): {e}")
            return None, None
        

    def get_status(self):
        """Récupère les informations de statut via le core (async) en passant par ThreadManager."""
        try:
            if not getattr(self, "thread_manager", None):
                self.logger.error("ThreadManager manquant pour exécuter les coroutines du core")
                return {}
            status = self.thread_manager.run_coro("AxisCoreLoop", lambda: self.axisClient.get_status())
            return status
        except Exception as e:
            self.logger.error(f"Erreur lors de la lecture du statut (core): {e}")
            return {}

    def disconnect(self):
        """
        Arrête le keep-alive côté core puis se déconnecte proprement.
        """
        try:
            if getattr(self, "thread_manager", None):
                # Nettoyer les callbacks de déconnexion pour éviter des doublons
                try:
                    self.axisClient.clear_disconnect_callbacks()
                except Exception:
                    pass
                # Stopper keep-alive et se déconnecter
                try:
                    self.thread_manager.run_coro("AxisCoreLoop", lambda: self.axisClient.stop_keep_alive())
                except Exception as e:
                    self.logger.error(f"Erreur lors de l'arrêt du keep-alive: {e}")
                try:
                    self.thread_manager.run_coro("AxisCoreLoop", lambda: self.axisClient.disconnect())
                except Exception as e:
                    self.logger.error(f"Erreur lors de la déconnexion (core): {e}")
            else:
                self.logger.warning("ThreadManager manquant pour exécuter la déconnexion du core")
        finally:
            self.connected = False
            self.connection_state_changed.emit("DISCONNECTED")

    def _on_core_disconnected(self):
        """
        Callback invoqué par le core (thread asyncio) en cas de coupure serveur.
        Émet des signaux Qt thread-safe pour permettre au GUI de réagir.
        """
        try:
            self.connected = False
            # Emettre les signaux Qt (file d'événements)
            self.connection_failed.emit("La connexion au serveur a été interrompue")
            self.connection_state_changed.emit("DISCONNECTED")
        except Exception as e:
            self.logger.error(f"Erreur dans _on_core_disconnected: {e}")


    def connect(self):
        """
        Se connecte au serveur Axis via le client core (async) et émet les signaux Qt.
        Utilise un event loop asyncio persistant géré par ThreadManager.
        """
        try:
            if not getattr(self, "thread_manager", None):
                self.logger.error("ThreadManager manquant pour initialiser la connexion au core")
                self.connection_failed.emit("ThreadManager manquant")
                self.connected = False
                return False

            self.thread_manager.run_coro("AxisCoreLoop", lambda: self.axisClient.connect())

            if getattr(self.axisClient, "server_status", None) == ServerStatus.CONNECTED:
                self.connected = True
                self.connection_succeeded.emit()
                self.connection_state_changed.emit("CONNECTED")

                self.logger.info(f"Connexion établie avec le serveur Axis: {self.ip_address}:{self.port}")

                # Enregistrer un callback de déconnexion côté core => signaux Qt
                try:
                    self.axisClient.set_disconnect_callback(self._on_core_disconnected)
                except Exception as e:
                    self.logger.error(f"Impossible d'enregistrer le callback de déconnexion: {e}")


                # Watchdog de connexion (détection déconnexion intempestive)
                if getattr(self, "thread_manager", None):
                    def _conn_watchdog(interval=0.5):
                        worker = self.thread_manager.get_worker("AxisConnWatchdog")
                        try:
                            while worker and not worker.abort:
                                if not self.is_connected():
                                    # Déconnexion non initiée par l'UI
                                    self.connection_failed.emit("La connexion au serveur a été interrompue")
                                    self.connection_state_changed.emit("DISCONNECTED")
                                    break
                                time.sleep(interval)
                        except Exception as e:
                            self.logger.error(f"Erreur watchdog connexion: {e}")
                            raise
                    self.thread_manager.start_thread("AxisConnWatchdog", _conn_watchdog, interval=0.5)

                return True
            else:
                error_msg = f"Impossible de se connecter au serveur: {self.ip_address}:{self.port}"
                self.connection_failed.emit(error_msg)
                self.connected = False
                return False
        except Exception as e:
            error_msg = f"Erreur de connexion Axis: {str(e)}"
            self.logger.error(error_msg)
            self.connection_failed.emit(error_msg)
            self.connected = False
            return False

"""Connection and live-telemetry UI extraction for MainUi."""

from __future__ import annotations

from PyQt5.QtWidgets import QMessageBox

from antrack.core.axis.axis_client import AxisClientPollingAdapter
from antrack.gui.axis.axis_client_qt import AxisClientQt
from antrack.gui.ui_styles import green_label_color, orange_label_color, red_label_color, standard_label_color
from antrack.tracking.tracking import Tracker


class ConnectionUiMixin:
    """Own Axis connection, polling, and live telemetry UI glue."""

    def has_connection(self) -> bool:
        return bool(getattr(self, "axis_client", None) and self.axis_client.is_connected())

    def on_connect_button_clicked(self):
        if self._connect_toggle_in_progress:
            return
        self._connect_toggle_in_progress = True
        try:
            if self.has_connection():
                self.request_disconnect()
            else:
                self.request_connect()
        except Exception as exc:
            self.logger.error(f"Erreur toggle connect/disconnect: {exc}")
        finally:
            self._connect_toggle_in_progress = False

    def stop_polling_threads(self):
        """Stop Axis polling threads and ephemeris workers."""
        try:
            if hasattr(self, "thread_manager") and self.thread_manager:
                for name in ("AxisPositionPoller", "AxisStatusPoller"):
                    try:
                        self.thread_manager.stop_thread(name)
                    except Exception:
                        pass
            if hasattr(self, "ephem"):
                try:
                    self.ephem.stop_all()
                except Exception:
                    pass
        except Exception as exc:
            self.logger.error(f"Erreur stop_polling_threads: {exc}")

    def request_connect(self):
        """Start the Axis connection in a background thread."""
        if not self.connection_ready:
            self.logger.info("Demarrage de la connexion au serveur Axis depuis un thread separe")
            self._user_requested_disconnect = False

            worker = self.thread_manager.start_thread(
                "AxisConnection",
                self.connect_to_axis_server,
                self.ip_address,
                self.port,
            )

            worker.status.connect(lambda msg: self.status_bar.showMessage(msg))
            worker.error.connect(self.on_connection_error)
            worker.result.connect(self.on_connection_success)

            self.connection_ready = True

    def request_disconnect(self):
        self.logger.info("[UI] request_disconnect: begin")
        try:
            self._user_requested_disconnect = True
            self._auto_restart_tracking = False

            try:
                self.pushButton_server_connect.setEnabled(False)
                self.pushButton_server_connect.setText("DISCONNECTING...")
                self.status_bar.showMessage("Deconnexion...")
            except Exception:
                pass

            try:
                self.stop_tracking_ui_timer()
            except Exception:
                pass
            try:
                if getattr(self, "tracker", None):
                    self.tracker.stop()
            except Exception:
                pass
            try:
                if getattr(self, "positioner", None):
                    self.positioner.stop()
            except Exception:
                pass
            try:
                if hasattr(self, "_stop_manual_jog"):
                    self._stop_manual_jog("az")
                    self._stop_manual_jog("el")
            except Exception:
                pass
            self.stop_polling_threads()
            self.axis_polling = None

            try:
                if getattr(self, "axis_client", None):
                    try:
                        self.axis_client.blockSignals(True)
                    except Exception:
                        pass
                    for sig, slot in (
                        ("connection_state_changed", self.on_axis_connection_state_changed),
                        ("connection_failed", self.on_axis_connection_failed),
                        ("antenna_telemetry_updated", self.ui_display_antenna_status),
                        ("antenna_telemetry_updated", self.on_antenna_telemetry_ready),
                        ("versions_updated", self.ui_display_versions),
                    ):
                        try:
                            if hasattr(self.axis_client, sig):
                                getattr(self.axis_client, sig).disconnect(slot)
                        except Exception:
                            pass
                    try:
                        if hasattr(self.axis_client, "set_auto_reconnect"):
                            self.axis_client.set_auto_reconnect(False)
                        elif hasattr(self.axis_client, "auto_reconnect"):
                            self.axis_client.auto_reconnect = False
                    except Exception:
                        pass
            except Exception:
                pass

            if getattr(self, "axis_client", None):
                try:
                    self.axis_client.disconnect()
                except Exception as exc:
                    self.logger.error(f"axis_client.disconnect error: {exc}")
                finally:
                    try:
                        self.axis_client.deleteLater()
                    except Exception:
                        pass
                    self.axis_client = None

            try:
                self.thread_manager.stop_thread("AxisConnWatchdog")
            except Exception:
                pass
            try:
                self.thread_manager.stop_asyncio_loop("AxisCoreLoop")
            except Exception:
                pass

            self.ui_set_default_state()
            self.set_server_status("DISCONNECTED")
            self.pushButton_server_connect.setText("CONNECT")
            self.connection_ready = False
            self.telemetry_ready = False
            self.status_bar.showMessage("Deconnecte du serveur Axis")
        except Exception as exc:
            self.logger.error(f"Erreur de deconnexion: {exc}")
        finally:
            try:
                self.pushButton_server_connect.setEnabled(True)
            except Exception:
                pass
            self.logger.info("[UI] request_disconnect: end")

    def connect_to_axis_server(self, ip_address, port):
        """Function executed in a background thread to connect to Axis."""
        try:
            self.logger.info(f"Tentative de connexion au serveur Axis {ip_address}:{port}")

            axis_client = AxisClientQt(ip_address, port)
            axis_client.thread_manager = self.thread_manager

            connected = axis_client.connect()

            if connected:
                self.logger.info(f"Connexion etablie avec le serveur Axis: {ip_address}:{port}")
                return axis_client
            raise ConnectionError(f"Impossible de se connecter au serveur {ip_address}:{port}")

        except Exception as exc:
            self.logger.error(f"Erreur de connexion au serveur Axis: {exc}")
            raise

    def on_connection_success(self, axis_client):
        """Called when the connection to the Axis server succeeds."""
        if getattr(self, "_user_requested_disconnect", False):
            self.logger.info("Connexion etablie mais l'utilisateur a demande la deconnexion -> teardown immediat.")
            try:
                axis_client.disconnect()
            except Exception:
                pass
            try:
                axis_client.deleteLater()
            except Exception:
                pass
            return

        self.axis_client = axis_client
        self.status_bar.showMessage("Connecte au serveur Axis")

        if hasattr(self.axis_client, "connection_state_changed"):
            self.axis_client.connection_state_changed.connect(self.on_axis_connection_state_changed)
        if hasattr(self.axis_client, "connection_failed"):
            self.axis_client.connection_failed.connect(self.on_axis_connection_failed)
        if hasattr(self.axis_client, "antenna_telemetry_updated"):
            self.axis_client.antenna_telemetry_updated.connect(self.ui_display_antenna_status)
            try:
                self.axis_client.antenna_telemetry_updated.connect(self.on_antenna_telemetry_ready)
            except Exception:
                pass
        try:
            az0 = getattr(getattr(self.axis_client, "antenna", None), "az", None)
            el0 = getattr(getattr(self.axis_client, "antenna", None), "el", None)
            self.telemetry_ready = isinstance(az0, (int, float)) and isinstance(el0, (int, float))
        except Exception:
            self.telemetry_ready = False
        if hasattr(self.axis_client, "versions_updated"):
            self.axis_client.versions_updated.connect(self.ui_display_versions)
            try:
                self.axis_client.emit_versions()
            except Exception as exc:
                self.logger.error(f"Impossible de declencher l'emission des versions: {exc}")

        self.pushButton_server_connect.setText("DISCONNECT")
        self.set_server_status("CONNECTED")
        self.set_data_labels_enabled(True)

        self.start_polling()

        try:
            self.prime_axis_motion()
        except Exception as exc:
            self.logger.error(f"prime_axis_motion apres reconnexion a echoue: {exc}")

        try:
            self.thread_manager.stop_thread("TrackingLoop")
        except Exception:
            pass

        try:
            self.tracker = Tracker(self.axis_client, self.settings, self.thread_manager, self.tracked_object)
            try:
                if hasattr(self.tracker, "mark_speeds_dirty"):
                    self.tracker.mark_speeds_dirty()
            except Exception:
                pass
            if hasattr(self, "pushButton_antenna_track"):
                self.pushButton_antenna_track.setEnabled(True)
                self.pushButton_antenna_track.setText("Track")

        except Exception as exc:
            self.logger.error(f"Impossible d'initialiser le tracker: {exc}")

        try:
            if self._auto_restart_tracking:
                self.logger.info("[Tracking] Auto-restart apres reconnexion")
                self._start_tracker_when_ready(attempts_left=20)
        except Exception as exc:
            self.logger.error(f"Auto-restart tracking error: {exc}")

    def on_connection_error(self, error_message):
        """Called when a connection error occurs."""
        self.logger.error(f"Erreur de connexion: {error_message}")
        self.status_bar.showMessage(f"Erreur: {error_message}")
        self.connection_ready = False
        QMessageBox.critical(
            self,
            "Erreur de connexion",
            f"Impossible de se connecter au serveur: {error_message}",
        )

    def on_axis_connection_failed(self, message: str):
        """Handle disconnect/failure reported by AxisClientQt."""
        if getattr(self, "_user_requested_disconnect", False):
            self.logger.info("Deconnexion demandee par l'utilisateur: aucune reconnexion automatique.")
            return

        try:
            self.logger.error(f"AxisClient: {message}")
            try:
                self.logger.info("[UI] STOP tracking (server disconnect/watchdog)")
            except Exception:
                pass
            self.telemetry_ready = False
            self.stop_polling_threads()
            self.axis_polling = None
            try:
                if getattr(self, "tracker", None):
                    self.tracker.stop()
            except Exception:
                pass
            try:
                self.stop_tracking_ui_timer()
            except Exception:
                pass
            try:
                if hasattr(self, "pushButton_antenna_track"):
                    self.pushButton_antenna_track.setText("Track")
            except Exception:
                pass

            self.pushButton_server_connect.setText("CONNECT")
            self.set_server_status("DISCONNECTED")
            self.ui_set_default_state()
            self.connection_ready = False
            self.status_bar.showMessage("Connexion interrompue")
            QMessageBox.warning(
                self,
                "Connexion interrompue",
                message or "La connexion au serveur a ete interrompue.",
            )
        except Exception as exc:
            self.logger.error(f"Erreur on_axis_connection_failed: {exc}")

    def on_axis_connection_state_changed(self, state: str):
        """Update the UI according to connection state."""
        if getattr(self, "_user_requested_disconnect", False):
            return
        try:
            normalized = (state or "").upper()
            self.set_server_status(normalized)
            if normalized == "CONNECTED":
                self.pushButton_server_connect.setText("DISCONNECT")
                self.set_data_labels_enabled(True)
            else:
                self.pushButton_server_connect.setText("CONNECT")
                try:
                    self.logger.info("[UI] STOP tracking (connection state changed to DISCONNECTED)")
                except Exception:
                    pass
                try:
                    if getattr(self, "tracker", None):
                        self.tracker.stop()
                except Exception:
                    pass
                try:
                    self.stop_tracking_ui_timer()
                except Exception:
                    pass
                self.telemetry_ready = False
                self.ui_set_default_state()
        except Exception as exc:
            self.logger.error(f"Erreur on_axis_connection_state_changed: {exc}")

    def set_server_status(self, state: str):
        """Update the server status label with unified text and style."""
        normalized = (state or "").upper()
        if normalized == "CONNECTED":
            self.label_antenna_server_status.setText("CONNECTED")
            self.label_antenna_server_status.setStyleSheet(green_label_color)
        elif normalized == "DISCONNECTED":
            self.label_antenna_server_status.setText("DISCONNECTED")
            self.label_antenna_server_status.setStyleSheet(red_label_color)
        else:
            self.label_antenna_server_status.setText(normalized or "UNKNOWN")
            self.label_antenna_server_status.setStyleSheet(standard_label_color)

    def set_data_labels_enabled(self, enabled: bool):
        """Enable or disable live data labels."""
        try:
            for attr in (
                "label_axisapp_version",
                "label_axisaz_version",
                "label_axisel_version",
                "label_antenna_az_rate",
                "label_antenna_el_rate",
                "label_antenna_az_setrate",
                "label_antenna_el_setrate",
                "label_antenna_endstop_az",
                "label_antenna_endstop_el",
            ):
                if hasattr(self, attr):
                    getattr(self, attr).setEnabled(enabled)
        except Exception as exc:
            self.logger.error(f"Erreur set_data_labels_enabled: {exc}")

    def start_polling(self):
        """
        Start polling threads via the core adapter.
        """
        try:
            if hasattr(self, "axis_polling") and self.axis_polling is not None:
                try:
                    self.axis_polling.stop()
                except Exception:
                    pass
            self.axis_polling = AxisClientPollingAdapter(self.axis_client, self.thread_manager)
            self.axis_polling.start(pos_interval=0.2, status_interval=1.0)
        except Exception as exc:
            try:
                self.logger.error(f"Erreur start_polling: {exc}")
            except Exception:
                pass

    def ui_set_default_state(self):
        """Apply the default disconnected UI state."""
        try:
            if hasattr(self, "label_axisapp_version"):
                self.label_axisapp_version.setText("")
            if hasattr(self, "label_axisaz_version"):
                self.label_axisaz_version.setText("")
            if hasattr(self, "label_axisel_version"):
                self.label_axisel_version.setText("")
            # self.label_antenna_az_deg.setText("---.--°")
            # self.label_antenna_el_deg.setText("---.--°")
            if hasattr(self, "label_antenna_az_rate"):
                self.label_antenna_az_rate.setText("0.00 °/s")
            if hasattr(self, "label_antenna_el_rate"):
                self.label_antenna_el_rate.setText("0.00 °/s")
            if hasattr(self, "label_antenna_az_setrate"):
                self.label_antenna_az_setrate.setText("--")
            if hasattr(self, "label_antenna_el_setrate"):
                self.label_antenna_el_setrate.setText("--")
            if hasattr(self, "label_antenna_endstop_az"):
                self.label_antenna_endstop_az.setText("-")
            if hasattr(self, "label_antenna_endstop_el"):
                self.label_antenna_endstop_el.setText("-")
            if hasattr(self, "label_antenna_az_set_deg"):
                self.label_antenna_az_set_deg.setText("---.--°")
            if hasattr(self, "label_antenna_el_set_deg"):
                self.label_antenna_el_set_deg.setText("---.--°")
            if hasattr(self, "label_object_distance_km"):
                self.label_object_distance_km.setText("-")
            if hasattr(self, "label_tracked_object"):
                self.label_tracked_object.setText("-")

            self.g1.set_setpoint(None)
            self.g1.set_angle(None)
            self.g1.set_error(None)
            self.g2.set_setpoint(None)
            self.g2.set_angle(None)
            self.g2.set_error(None)

            for attr in (
                "target_ra_label",
                "target_dec_label",
                "target_dist_au_label",
                "target_visible_now_label",
                "target_aos_label",
                "target_los_label",
                "target_dur_label",
                "target_max_el_label",
                "target_max_el_time_label",
                "target_el_now_label",
            ):
                try:
                    if hasattr(self, attr):
                        getattr(self, attr).setText("-")
                except Exception:
                    pass

            self.set_server_status("DISCONNECTED")

            try:
                self.logger.info("[UI] Apply default STOP state (ui_set_default_state)")
            except Exception:
                pass
            self._ui_show_tracking_stopped()

            self.set_data_labels_enabled(False)

            if hasattr(self, "pushButton_antenna_track"):
                self.pushButton_antenna_track.setEnabled(False)
                self.pushButton_antenna_track.setText("Track")

        except Exception as exc:
            self.logger.error(f"Erreur ui_set_default_state: {exc}")

    def ui_display_versions(self, versions: dict):
        """Update server/driver version labels when connected."""
        try:
            if not isinstance(versions, dict):
                return
            if hasattr(self, "label_axisapp_version"):
                self.label_axisapp_version.setText(str(versions.get("server_version") or ""))
            if hasattr(self, "label_axisaz_version"):
                self.label_axisaz_version.setText(str(versions.get("driver_version_az") or ""))
            if hasattr(self, "label_axisel_version"):
                self.label_axisel_version.setText(str(versions.get("driver_version_el") or ""))
        except Exception as exc:
            self.logger.error(f"Erreur ui_display_versions: {exc}")

    def ui_display_antenna_status(self, data: dict):
        """Unified live update of antenna labels and gauges."""
        try:
            if not isinstance(data, dict):
                return
            az = data.get("az")
            el = data.get("el")
            # self.label_antenna_az_deg.setText(f"{az:.2f}°" if isinstance(az, (int, float)) else "---.--°")
            self.g1.set_angle(az)
            # self.label_antenna_el_deg.setText(f"{el:.2f}°" if isinstance(el, (int, float)) else "---.--°")
            self.g2.set_angle(el)

            az_rate = data.get("az_rate")
            el_rate = data.get("el_rate")
            self.label_antenna_az_rate.setText(
                f"{az_rate:.2f} °/s" if isinstance(az_rate, (int, float)) else "0.00 °/s"
            )
            self.label_antenna_el_rate.setText(
                f"{el_rate:.2f} °/s" if isinstance(el_rate, (int, float)) else "0.00 °/s"
            )

            self.label_antenna_az_setrate.setText(f"{data.get('az_setrate'):.0f}")
            self.label_antenna_el_setrate.setText(f"{data.get('el_setrate'):.0f}")

            end_az = data.get("endstop_az")
            end_el = data.get("endstop_el")
            self.label_antenna_endstop_az.setText(str(end_az) if end_az is not None else "-")
            self.label_antenna_endstop_el.setText(str(end_el) if end_el is not None else "-")

            try:
                if isinstance(az, (int, float)):
                    self._last_tel_az = az
                if isinstance(el, (int, float)):
                    self._last_tel_el = el
            except Exception:
                pass

            try:
                status = (
                    getattr(self.axis_client.axisClient, "axis_status", None)
                    if hasattr(self, "axis_client")
                    else None
                )
                if isinstance(status, dict):
                    az_state = status.get("azimuth")
                    el_state = status.get("elevation")
                    az_text = None
                    el_text = None
                    if az_state is not None:
                        az_text = getattr(az_state, "display_name", None) or getattr(az_state, "name", str(az_state))
                        self.label_antenna_az_status.setText(az_text)
                        az_status_color = green_label_color if az_text != "STOP" else orange_label_color
                        self.label_antenna_az_status.setStyleSheet(az_status_color)
                    if el_state is not None:
                        el_text = getattr(el_state, "display_name", None) or getattr(el_state, "name", str(el_state))
                        self.label_antenna_el_status.setText(el_text)
                        el_status_color = green_label_color if el_text != "STOP" else orange_label_color
                        self.label_antenna_el_status.setStyleSheet(el_status_color)
            except Exception:
                pass
        except Exception as exc:
            self.logger.error(f"Erreur ui_display_antenna_status: {exc}")

"""Tracking-related UI extraction for MainUi."""

from __future__ import annotations

from PyQt5.QtCore import QTimer
from PyQt5.QtWidgets import QComboBox, QGridLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QVBoxLayout

from antrack.gui.ui_styles import (
    green_label_color,
    lightgrey_label_color,
    orange_label_color,
    red_label_color,
    standard_label_color,
)
from antrack.gui.widgets.multi_track_card import MultiTrackStrip
from antrack.tracking.tracking import Tracker


class TrackingUiMixin:
    """Keep tracking selection and tracking-state UI logic out of main_ui.py."""

    def has_setpoints(self) -> bool:
        az = getattr(self.tracked_object, "az_set", None)
        el = getattr(self.tracked_object, "el_set", None)
        return isinstance(az, (int, float)) and isinstance(el, (int, float))

    def setup_tracker_tab(self):
        """
        Create the controls used to select a target object and display its payload.
        """
        try:
            container = getattr(self, "tab_TargetObject", None)
            if container is None:
                self.logger.warning("tab_TargetObject introuvable dans l'UI")
                return

            layout = QVBoxLayout()

            self.object_dropdown = QComboBox()
            self.object_dropdown.addItems(
                ["Select Type", "Solar System", "Star", "Artificial Satellite", "Radio Source"]
            )
            self.object_dropdown.currentIndexChanged.connect(self.update_secondary_dropdown)
            layout.addWidget(self.object_dropdown)

            self.tle_group_dropdown = QComboBox()
            self.tle_group_dropdown.setToolTip("TLE group (stations, active, amateur, weather, ...)")
            self.tle_group_dropdown.currentIndexChanged.connect(self._on_tle_group_changed)
            self.tle_group_dropdown.setVisible(False)
            layout.addWidget(self.tle_group_dropdown)

            self.tle_sat_dropdown = QComboBox()
            self.tle_sat_dropdown.currentIndexChanged.connect(
                lambda _index: self.apply_target_btn.setEnabled(bool(self._current_sat_query()))
            )
            self.tle_sat_dropdown.setToolTip("Satellite (depuis le groupe selectionne)")
            self.tle_sat_dropdown.setVisible(False)
            layout.addWidget(self.tle_sat_dropdown)

            self.tle_query_edit = QLineEdit()
            self.tle_query_edit.textChanged.connect(
                lambda _text: self.apply_target_btn.setEnabled(bool(self._current_sat_query()))
            )
            self.tle_query_edit.setPlaceholderText("Nom/NORAD (optionnel, ex: 25544 ou 'NOAA 19')")
            self.tle_query_edit.setVisible(False)
            layout.addWidget(self.tle_query_edit)

            self.rs_group_dropdown = QComboBox()
            self.rs_group_dropdown.setToolTip("Catalogue (fichier CSV)")
            self.rs_group_dropdown.currentIndexChanged.connect(self._on_rs_group_changed)
            self.rs_group_dropdown.setVisible(False)
            layout.addWidget(self.rs_group_dropdown)

            self.rs_source_dropdown = QComboBox()
            self.rs_source_dropdown.setToolTip("Source radio (depuis le catalogue)")
            self.rs_source_dropdown.setVisible(False)
            layout.addWidget(self.rs_source_dropdown)

            self.rs_query_edit = QLineEdit()
            self.rs_query_edit.setPlaceholderText("Nom (optionnel, ex: 3C 273)")
            self.rs_query_edit.setVisible(False)
            layout.addWidget(self.rs_query_edit)

            self.rs_query_edit.textChanged.connect(
                lambda _text: self.apply_target_btn.setEnabled(bool(self._current_rs_query()))
            )
            self.rs_source_dropdown.currentIndexChanged.connect(
                lambda _index: self.apply_target_btn.setEnabled(bool(self._current_rs_query()))
            )

            self.specific_object_dropdown = QComboBox()
            self.specific_object_dropdown.currentIndexChanged.connect(self.on_target_selection_changed)
            layout.addWidget(self.specific_object_dropdown)

            self.apply_target_btn = QPushButton("Appliquer la selection")
            self.apply_target_btn.setEnabled(False)
            self.apply_target_btn.setToolTip(
                "Demarre le calcul periodique des consignes (Set) pour l'objet selectionne. "
                "N'actionne pas les moteurs. Utilisez 'Track' pour demarrer/arreter le suivi."
            )
            self.apply_target_btn.clicked.connect(self.on_apply_target_clicked)
            layout.addWidget(self.apply_target_btn)

            grid = QGridLayout()
            row = 0

            def add_row(title: str, attr_name: str):
                nonlocal row
                grid.addWidget(QLabel(title), row, 0)
                value = QLabel("-")
                setattr(self, attr_name, value)
                grid.addWidget(value, row, 1)
                row += 1

            add_row("Azimuth:", "azimuth_label")
            add_row("Elevation:", "elevation_label")
            add_row("Distance (km):", "distance_label")
            add_row("RA (hms):", "target_ra_label")
            add_row("DEC (dms):", "target_dec_label")
            add_row("Distance (AU):", "target_dist_au_label")
            add_row("Visible now:", "target_visible_now_label")
            add_row("AOS (UTC):", "target_aos_label")
            add_row("LOS (UTC):", "target_los_label")
            add_row("Duration:", "target_dur_label")
            add_row("Max EL:", "target_max_el_label")
            add_row("Max EL @ (UTC):", "target_max_el_time_label")
            add_row("EL NOW:", "target_el_now_label")

            layout.addLayout(grid)

            try:
                self.object_dropdown.setCurrentText("Solar System")
                self.update_secondary_dropdown()
                for index in range(self.specific_object_dropdown.count()):
                    if self.specific_object_dropdown.itemText(index).lower() == "sun":
                        self.specific_object_dropdown.setCurrentIndex(index)
                        break
            except Exception:
                pass

            container.setLayout(layout)
        except Exception as exc:
            self.logger.error(f"Erreur setup_tracker_tab: {exc}")

    def update_secondary_dropdown(self):
        """
        Update the secondary selection area based on the chosen target type.
        """
        try:
            selection = (self.object_dropdown.currentText() or "").strip()

            is_sat = selection == "Artificial Satellite"
            is_rs = selection == "Radio Source"

            if hasattr(self, "tle_group_dropdown"):
                self.tle_group_dropdown.setVisible(is_sat)
            if hasattr(self, "tle_sat_dropdown"):
                self.tle_sat_dropdown.setVisible(is_sat)
            if hasattr(self, "tle_query_edit"):
                self.tle_query_edit.setVisible(is_sat)

            if hasattr(self, "rs_group_dropdown"):
                self.rs_group_dropdown.setVisible(is_rs)
            if hasattr(self, "rs_source_dropdown"):
                self.rs_source_dropdown.setVisible(is_rs)
            if hasattr(self, "rs_query_edit"):
                self.rs_query_edit.setVisible(is_rs)

            self.specific_object_dropdown.setVisible(not (is_sat or is_rs))
            self.specific_object_dropdown.clear()

            if selection == "Solar System":
                self.specific_object_dropdown.addItems(
                    ["Mercury", "Venus", "Earth", "Mars", "Jupiter", "Saturn", "Uranus", "Neptune", "Moon", "Sun"]
                )
            elif selection == "Star":
                self.specific_object_dropdown.addItems(["Polaris", "Sirius", "Betelgeuse"])
            elif is_sat:
                try:
                    self.ephem.tle_refresh(force=False)
                    groups = self.ephem.tle_groups() or []
                    if not groups and isinstance(self.settings, dict):
                        groups = self.settings.get("TLE_GROUPS", []) or []
                    if not groups:
                        groups = ["stations", "active", "amateur", "weather"]

                    self.tle_group_dropdown.blockSignals(True)
                    self.tle_group_dropdown.clear()
                    self.tle_group_dropdown.addItems(groups)
                    self.tle_group_dropdown.blockSignals(False)

                    self._populate_tle_satellites_for_current_group()
                except Exception as exc:
                    self.logger.error(f"update_secondary_dropdown TLE init error: {exc}")
            elif is_rs:
                try:
                    self.ephem.rs_refresh(force=False)
                    groups = self.ephem.rs_groups() or []

                    self.rs_group_dropdown.blockSignals(True)
                    self.rs_group_dropdown.clear()
                    self.rs_group_dropdown.addItems(groups)
                    self.rs_group_dropdown.blockSignals(False)

                    self._populate_rs_sources_for_current_group()
                except Exception as exc:
                    self.logger.error(f"update_secondary_dropdown RS init error: {exc}")

            try:
                if is_sat:
                    selected_object = "(selectionnez un satellite)"
                elif is_rs:
                    selected_object = "(selectionnez une source radio)"
                else:
                    selected_object = self.specific_object_dropdown.currentText()
                self.status_bar.showMessage(
                    f"Selection: {selection} / {selected_object} - cliquez 'Appliquer la selection' pour demarrer.",
                    3000,
                )
            except Exception:
                pass

            try:
                if is_sat:
                    enabled = bool(self._current_sat_query())
                elif is_rs:
                    enabled = bool(self._current_rs_query())
                else:
                    enabled = self.specific_object_dropdown.count() > 0
                self.apply_target_btn.setEnabled(enabled)
            except Exception:
                pass

        except Exception as exc:
            self.logger.error(f"Erreur update_secondary_dropdown: {exc}")

    def _on_tle_group_changed(self):
        try:
            self._populate_tle_satellites_for_current_group()
        except Exception as exc:
            self.logger.error(f"_on_tle_group_changed error: {exc}")

    def _populate_tle_satellites_for_current_group(self):
        try:
            if not hasattr(self, "tle_group_dropdown") or not hasattr(self, "tle_sat_dropdown"):
                return

            group = (self.tle_group_dropdown.currentText() or "").strip()
            if not group:
                self.tle_sat_dropdown.clear()
                self.tle_sat_dropdown.addItem("(aucun groupe)")
                return

            try:
                self.ephem.tle_refresh(force=False)
            except Exception:
                pass

            rows = []
            try:
                rows = self.ephem.tle_list_satellites(group=group) or []
            except Exception as exc:
                self.logger.error(f"tle_list_satellites error: {exc}")

            self.tle_sat_dropdown.blockSignals(True)
            self.tle_sat_dropdown.clear()
            if not rows:
                self.tle_sat_dropdown.addItem("(vide)")
            else:
                for name, norad in rows:
                    label = f"{name} [{norad}]" if isinstance(norad, int) and norad >= 0 else name
                    self.tle_sat_dropdown.addItem(label)
            self.tle_sat_dropdown.blockSignals(False)

            try:
                name = self._parse_sat_label(self.tle_sat_dropdown.currentText() or "")
                enabled = bool(name) or bool((self.tle_query_edit.text() or "").strip())
                self.apply_target_btn.setEnabled(enabled)
            except Exception:
                pass

        except Exception as exc:
            self.logger.error(f"_populate_tle_satellites_for_current_group error: {exc}")
            try:
                self.tle_sat_dropdown.blockSignals(True)
                self.tle_sat_dropdown.clear()
                self.tle_sat_dropdown.addItem("(erreur)")
                self.tle_sat_dropdown.blockSignals(False)
            except Exception:
                pass

    def _parse_rs_label(self, label: str) -> str:
        normalized = (label or "").strip()
        if normalized in ("(vide)", "(erreur)", "(aucun groupe)"):
            return ""
        return normalized

    def _current_rs_query(self) -> str:
        query = (self.rs_query_edit.text() if hasattr(self, "rs_query_edit") else "") or ""
        query = query.strip()
        if query:
            return query
        label = (self.rs_source_dropdown.currentText() if hasattr(self, "rs_source_dropdown") else "") or ""
        return self._parse_rs_label(label)

    def _on_rs_group_changed(self):
        try:
            self._populate_rs_sources_for_current_group()
        except Exception as exc:
            self.logger.error(f"_on_rs_group_changed error: {exc}")

    def _populate_rs_sources_for_current_group(self):
        try:
            group = (self.rs_group_dropdown.currentText() or "").strip()
            self.ephem.rs_refresh(force=False)
            names = self.ephem.rs_list_sources(group=group) or []
            self.rs_source_dropdown.blockSignals(True)
            self.rs_source_dropdown.clear()
            if not names:
                self.rs_source_dropdown.addItem("(vide)")
            else:
                for name in names:
                    self.rs_source_dropdown.addItem(name)
            self.rs_source_dropdown.blockSignals(False)
        except Exception as exc:
            self.logger.error(f"_populate_rs_sources_for_current_group error: {exc}")
            self.rs_source_dropdown.blockSignals(True)
            self.rs_source_dropdown.clear()
            self.rs_source_dropdown.addItem("(erreur)")
            self.rs_source_dropdown.blockSignals(False)

    def _on_pose_updated(self, key: str, payload: dict):
        """
        Receive computed positions from EphemerisService.
        Only the 'primary' key updates the tracking selection state.
        """
        try:
            if key != "primary":
                return

            az = payload.get("az")
            el = payload.get("el")
            dist_km = payload.get("dist_km")
            dist_au = payload.get("dist_au")
            ra_hms = payload.get("ra_hms")
            dec_dms = payload.get("dec_dms")

            if isinstance(az, (int, float)):
                self.tracked_object.az_set = az
            if isinstance(el, (int, float)):
                self.tracked_object.el_set = el
            if isinstance(dist_km, (int, float)):
                self.tracked_object.distance_km = dist_km
            if isinstance(dist_au, (int, float)):
                self.tracked_object.distance_au = dist_au
            if isinstance(ra_hms, tuple):
                hour, minute, second = ra_hms
                self.tracked_object.ra_set.decimal_hours = hour + minute / 60.0 + second / 3600.0
            if isinstance(dec_dms, tuple):
                degree, minute, second = dec_dms
                sign = -1 if degree < 0 else 1
                self.tracked_object.dec_set.decimal_degrees = (
                    (abs(degree) + minute / 60.0 + second / 3600.0) * sign
                )

            self.ui_update_tracked_display(payload)

            az_ok = isinstance(self.tracked_object.az_set, (int, float))
            el_ok = isinstance(self.tracked_object.el_set, (int, float))
            if self.telemetry_ready and az_ok and el_ok and hasattr(self, "pushButton_antenna_track"):
                self.pushButton_antenna_track.setEnabled(True)

            try:
                if hasattr(self, "calib_plots") and self.calib_plots is not None:
                    self.calib_plots.update_current(az, el)
            except Exception:
                pass

        except Exception as exc:
            self.logger.error(f"Erreur _on_pose_updated: {exc}")

    def start_object_selection_tracking(self):
        """Start the periodic position computation for the selected object."""
        try:
            selected_type = self.object_dropdown.currentText() if hasattr(self, "object_dropdown") else "Select Type"
            selected_object = (
                self.specific_object_dropdown.currentText() if hasattr(self, "specific_object_dropdown") else ""
            )
            self.ephem.start_object("primary", selected_type, selected_object, interval=0.1)
        except Exception as exc:
            self.logger.error(f"Erreur start_object_selection_tracking: {exc}")

    def on_target_selection_changed(self):
        """
        Selection changes do not start tracking automatically.
        """
        try:
            selected_type = self.object_dropdown.currentText() if hasattr(self, "object_dropdown") else "?"
            selected_object = (
                self.specific_object_dropdown.currentText() if hasattr(self, "specific_object_dropdown") else "?"
            )
            self.status_bar.showMessage(
                f"Selection: {selected_type} / {selected_object} - cliquez 'Appliquer la selection' pour demarrer l'actualisation.",
                3000,
            )
        except Exception as exc:
            self.logger.error(f"Erreur on_target_selection_changed: {exc}")

    def track_object(self):
        """Compatibility entry point."""
        try:
            self.start_object_selection_tracking()
        except Exception as exc:
            self.logger.error(f"Erreur track_object (deprecated): {exc}")

    def ui_update_tracked_display(self, payload: dict):
        """
        Update the Target tab with ephemeris worker data.
        """
        try:
            name = payload.get("name") or "Unknown"
            az = payload.get("az")
            el = payload.get("el")
            dist_km = payload.get("dist_km")
            ra_hms = payload.get("ra_hms")
            dec_dms = payload.get("dec_dms")
            dist_au = payload.get("dist_au")

            if hasattr(self, "label_tracked_object"):
                self.label_tracked_object.setText(str(name))
                self.label_tracked_object.setStyleSheet(orange_label_color)

            if hasattr(self, "azimuth_label"):
                self.azimuth_label.setText(f"Azimuth: {az:.2f}°" if isinstance(az, (int, float)) else "Azimuth: N/A")
            if hasattr(self, "elevation_label"):
                self.elevation_label.setText(
                    f"Elevation: {el:.2f}°" if isinstance(el, (int, float)) else "Elevation: N/A"
                )
            if hasattr(self, "distance_label"):
                self.distance_label.setText(
                    f"Distance: {dist_km:.0f} km" if isinstance(dist_km, (int, float)) else "Distance: N/A"
                )

            running = bool(getattr(self, "tracker", None) and self.tracker.is_running())
            if not running:
                if hasattr(self, "label_antenna_az_set_deg"):
                    self.label_antenna_az_set_deg.setText(
                        f"{az:.2f}°" if isinstance(az, (int, float)) else "---.--°"
                    )
                if hasattr(self, "label_antenna_el_set_deg"):
                    self.label_antenna_el_set_deg.setText(
                        f"{el:.2f}°" if isinstance(el, (int, float)) else "---.--°"
                    )

            if isinstance(ra_hms, tuple) and hasattr(self, "target_ra_label"):
                hour, minute, second = ra_hms
                self.target_ra_label.setText(f"{int(hour)}h {int(minute)}m {second:04.1f}s")
            if isinstance(dec_dms, tuple) and hasattr(self, "target_dec_label"):
                degree, minute, second = dec_dms
                self.target_dec_label.setText(f"{int(degree)}° {int(minute)}' {second:04.1f}\"")
            if isinstance(dist_au, (int, float)) and hasattr(self, "target_dist_au_label"):
                self.target_dist_au_label.setText(f"{dist_au:.3f}")

            if isinstance(ra_hms, tuple) and hasattr(self, "label_antenna_ra_set"):
                hour, minute, second = ra_hms
                self.label_antenna_ra_set.setText(f"{int(hour)}h {int(minute)}m {second:04.1f}s")
                self.label_antenna_ra_set.setStyleSheet(standard_label_color)
            if isinstance(dec_dms, tuple) and hasattr(self, "label_antenna_dec_set"):
                degree, minute, second = dec_dms
                self.label_antenna_dec_set.setText(f"{int(degree)}° {int(minute)}' {second:04.1f}\"")
                self.label_antenna_dec_set.setStyleSheet(standard_label_color)
            if isinstance(dist_au, (int, float)) and hasattr(self, "label_object_distance_au"):
                self.label_object_distance_au.setText(f"{dist_au:.3f}")
                self.label_object_distance_au.setStyleSheet(standard_label_color)

            visible = payload.get("visible_now", None)
            if hasattr(self, "target_visible_now_label"):
                if visible is True:
                    self.target_visible_now_label.setText("YES")
                    self.target_visible_now_label.setStyleSheet(green_label_color)
                elif visible is False:
                    self.target_visible_now_label.setText("NO")
                    self.target_visible_now_label.setStyleSheet(red_label_color)
                else:
                    self.target_visible_now_label.setText("-")
                    self.target_visible_now_label.setStyleSheet(standard_label_color)

            if hasattr(self, "target_aos_label"):
                self.target_aos_label.setText(payload.get("aos_utc") or "-")
            if hasattr(self, "target_los_label"):
                self.target_los_label.setText(payload.get("los_utc") or "-")

            if hasattr(self, "target_dur_label"):
                duration = payload.get("dur_str")
                self.target_dur_label.setText(duration if duration else "-")

            if hasattr(self, "target_max_el_label"):
                max_el = payload.get("max_el_deg")
                self.target_max_el_label.setText(f"{max_el:.1f}°" if isinstance(max_el, (int, float)) else "-")
            if hasattr(self, "target_max_el_time_label"):
                self.target_max_el_time_label.setText(payload.get("max_el_time_utc") or "-")

            if hasattr(self, "target_el_now_label"):
                el_now = payload.get("el_now_deg", None)
                if not isinstance(el_now, (int, float)):
                    el_now = el if isinstance(el, (int, float)) else None
                self.target_el_now_label.setText(f"{el_now:.2f}°" if isinstance(el_now, (int, float)) else "-")

        except Exception as exc:
            self.logger.error(f"Erreur ui_update_tracked_display: {exc}")

    def start_tracking_ui_timer(self):
        try:
            if getattr(self, "_tracking_ui_timer", None) is None:
                self._tracking_ui_timer = QTimer(self)
                self._tracking_ui_timer.setInterval(100)
                self._tracking_ui_timer.timeout.connect(self._update_tracking_ui)
            if not self._tracking_ui_timer.isActive():
                self._tracking_ui_timer.start()
        except Exception as exc:
            self.logger.error(f"Erreur start_tracking_ui_timer: {exc}")

    def prime_axis_motion(self):
        """
        Reset controller-side motion state: axis stop plus speed reapplication.
        """
        try:
            antenna_settings = {}
            if isinstance(self.settings, dict):
                antenna_settings = self.settings.get("ANTENNA", self.settings.get("antenna", {}))
            az_far = float(antenna_settings.get("az_speed_far_tracking", 500))
            el_far = float(antenna_settings.get("el_speed_far_tracking", 500))

            try:
                self.thread_manager.run_coro("AxisCoreLoop", self.axis_client.axisClient.stop_az, timeout=1.0)
            except Exception:
                pass
            try:
                self.thread_manager.run_coro("AxisCoreLoop", self.axis_client.axisClient.stop_el, timeout=1.0)
            except Exception:
                pass

            try:
                self.thread_manager.run_coro(
                    "AxisCoreLoop",
                    lambda: self.axis_client.axisClient.set_az_speed(az_far),
                    timeout=1.0,
                )
                try:
                    self.axis_client.antenna.az_setrate = az_far
                except Exception:
                    pass
            except Exception as exc:
                self.logger.debug(f"prime_axis_motion: set_az_speed erreur: {exc}")
            try:
                self.thread_manager.run_coro(
                    "AxisCoreLoop",
                    lambda: self.axis_client.axisClient.set_el_speed(el_far),
                    timeout=1.0,
                )
                try:
                    self.axis_client.antenna.el_setrate = el_far
                except Exception:
                    pass
            except Exception as exc:
                self.logger.debug(f"prime_axis_motion: set_el_speed erreur: {exc}")

        except Exception as exc:
            self.logger.error(f"Erreur prime_axis_motion: {exc}")

    def on_antenna_telemetry_ready(self, payload: dict):
        """Mark telemetry_ready when az/el are numeric."""
        try:
            if isinstance(payload, dict):
                az = payload.get("az")
                el = payload.get("el")
                if isinstance(az, (int, float)) and isinstance(el, (int, float)):
                    self.telemetry_ready = True
        except Exception:
            pass

    def stop_tracking_ui_timer(self):
        try:
            if getattr(self, "_tracking_ui_timer", None) and self._tracking_ui_timer.isActive():
                self._tracking_ui_timer.stop()
        except Exception as exc:
            self.logger.error(f"Erreur stop_tracking_ui_timer: {exc}")

    def _ui_show_tracking_stopped(self):
        """Display the stopped state and reset error visuals."""
        try:
            if hasattr(self, "label_antenna_status"):
                self.label_antenna_status.setText("Stopped")
                self.label_antenna_status.setStyleSheet(red_label_color)
            if hasattr(self, "label_antenna_az_error"):
                self.label_antenna_az_error.setText("-.-- °")
                self.label_antenna_az_error.setStyleSheet(lightgrey_label_color)
            self.g1.set_setpoint(None)
            self.g1.set_error(None)

            if hasattr(self, "label_antenna_el_error"):
                self.label_antenna_el_error.setText("-.-- °")
                self.label_antenna_el_error.setStyleSheet(lightgrey_label_color)
            self.g2.set_setpoint(None)
            self.g2.set_error(None)
        except Exception:
            pass

    def _start_tracker_when_ready(self, attempts_left: int = 20):
        """
        Wait until telemetry and setpoints are ready, then start the tracker.
        """
        try:
            tel_ok = bool(self.telemetry_ready)
            az_set = getattr(self.tracked_object, "az_set", None)
            el_set = getattr(self.tracked_object, "el_set", None)
            set_ok = isinstance(az_set, (int, float)) and isinstance(el_set, (int, float))

            if tel_ok and set_ok:
                try:
                    self.thread_manager.stop_thread("TrackingLoop")
                except Exception:
                    pass

                self.tracker.start()

                try:
                    if hasattr(self, "label_antenna_az_set_deg"):
                        self.label_antenna_az_set_deg.setText(f"{az_set:.2f}°")
                    if hasattr(self, "label_antenna_el_set_deg"):
                        self.label_antenna_el_set_deg.setText(f"{el_set:.2f}°")
                    self.g1.set_setpoint(az_set)
                    self.g2.set_setpoint(el_set)
                except Exception:
                    pass

                try:
                    self._update_tracking_ui()
                except Exception:
                    pass
                self.start_tracking_ui_timer()
                try:
                    self.pushButton_antenna_track.setText("Stop")
                except Exception:
                    pass
            else:
                if attempts_left > 0:
                    if attempts_left == 20:
                        self.logger.info(
                            f"[Tracking] Waiting for readiness (telemetry_ready={self.telemetry_ready}, "
                            f"az_set={az_set}, el_set={el_set})"
                        )
                    QTimer.singleShot(250, lambda: self._start_tracker_when_ready(attempts_left - 1))
                else:
                    self.logger.warning("[Tracking] Abandon start: telemetry or setpoints still not ready")
                    QMessageBox.information(
                        self,
                        "Tracking",
                        "Tracking non demarre: telemetrie ou setpoints indisponibles.",
                    )
        except Exception as exc:
            self.logger.error(f"_start_tracker_when_ready error: {exc}")

    def _update_tracking_ui(self):
        """
        Update tracking status, errors, and gauges from telemetry and setpoints.
        """
        try:
            if not hasattr(self, "_ui_tick"):
                self._ui_tick = 0
            self._ui_tick += 1

            running = bool(getattr(self, "tracker", None) and self.tracker.is_running())
            if hasattr(self, "label_antenna_status"):
                if running:
                    self.label_antenna_status.setText("Tracking")
                    self.label_antenna_status.setStyleSheet(green_label_color)
                else:
                    self.label_antenna_status.setText("Stopped")
                    self.label_antenna_status.setStyleSheet(red_label_color)

            az_cur = (
                getattr(getattr(self.axis_client, "antenna", None), "az", None)
                if hasattr(self, "axis_client")
                else None
            )
            el_cur = (
                getattr(getattr(self.axis_client, "antenna", None), "el", None)
                if hasattr(self, "axis_client")
                else None
            )
            if az_cur is None:
                az_cur = getattr(self, "_last_tel_az", None)
            if el_cur is None:
                el_cur = getattr(self, "_last_tel_el", None)

            az_set = getattr(self.tracked_object, "az_set", None)
            el_set = getattr(self.tracked_object, "el_set", None)

            if running and isinstance(az_set, (int, float)) and isinstance(el_set, (int, float)):
                if hasattr(self, "label_antenna_az_set_deg"):
                    self.label_antenna_az_set_deg.setText(f"{az_set:.2f}°")
                if hasattr(self, "label_antenna_el_set_deg"):
                    self.label_antenna_el_set_deg.setText(f"{el_set:.2f}°")
                try:
                    self.g1.set_setpoint(az_set)
                    self.g2.set_setpoint(el_set)
                except Exception:
                    pass

                if isinstance(az_cur, (int, float)):
                    az_err = az_cur - az_set
                    self.tracked_object.az_error = az_err
                    if hasattr(self, "label_antenna_az_error"):
                        self.label_antenna_az_error.setText(f"{az_err:.2f} °")
                        threshold = self.settings.get("ANTENNA", {}).get("az_error_threshold", 0.05)
                        color = green_label_color if abs(az_err) <= float(threshold) else red_label_color
                        self.label_antenna_az_error.setStyleSheet(color)
                    self.g1.set_error(az_err)
                if isinstance(el_cur, (int, float)):
                    el_err = el_cur - el_set
                    self.tracked_object.el_error = el_err
                    if hasattr(self, "label_antenna_el_error"):
                        self.label_antenna_el_error.setText(f"{el_err:.2f} °")
                        threshold = self.settings.get("ANTENNA", {}).get("el_error_threshold", 0.05)
                        color = green_label_color if abs(el_err) <= float(threshold) else red_label_color
                        self.label_antenna_el_error.setStyleSheet(color)
                    self.g2.set_error(el_err)
            else:
                try:
                    self.g1.set_setpoint(None)
                    self.g2.set_setpoint(None)
                    self.g1.set_error(None)
                    self.g2.set_error(None)
                except Exception:
                    pass

        except Exception as exc:
            self.logger.error(f"Erreur _update_tracking_ui: {exc}")

    def on_apply_target_clicked(self):
        try:
            selected_type = (self.object_dropdown.currentText() or "").strip()
            if selected_type == "Artificial Satellite":
                selected_object = self._current_sat_query()
                if not selected_object:
                    QMessageBox.information(
                        self,
                        "Target Object",
                        "Choisissez un satellite ou saisissez un Nom/NORAD.",
                    )
                    return
            elif selected_type == "Radio Source":
                selected_object = self._current_rs_query()
                if not selected_object:
                    QMessageBox.information(
                        self,
                        "Target Object",
                        "Choisissez une source radio ou tapez son nom (ex: 3C 273).",
                    )
                    return
            else:
                selected_object = self.specific_object_dropdown.currentText()
                if not selected_object:
                    QMessageBox.information(self, "Target Object", "Veuillez selectionner un objet.")
                    return

            self.ephem.start_object("primary", selected_type, selected_object, interval=0.1)
            self.status_bar.showMessage(f"Consignes demarrees pour: {selected_type} / {selected_object}", 3000)
            try:
                self.refresh_calibration_plots(step_s=2.0)
            except Exception as exc:
                self.logger.error(f"refresh_calibration_plots failed: {exc}")

        except Exception as exc:
            self.logger.error(f"Erreur on_apply_target_clicked: {exc}")
            QMessageBox.warning(self, "Target Object", f"Impossible de demarrer les consignes:\n{exc}")

    def on_track_button_clicked(self):
        """
        Start or stop the motor tracking loop and its associated UI updates.
        """
        try:
            if not self.has_connection():
                QMessageBox.warning(self, "Tracking", "Veuillez d'abord vous connecter au serveur Axis.")
                return

            if self.tracker is None:
                try:
                    self.tracker = Tracker(
                        self.axis_client,
                        self.settings,
                        self.thread_manager,
                        self.tracked_object,
                    )
                except Exception as exc:
                    self.logger.error(f"Impossible d'initialiser le tracker: {exc}")
                    QMessageBox.warning(self, "Tracking", "Initialisation du tracker impossible.")
                    return

            if not self.tracker.is_running():
                az_set = getattr(self.tracked_object, "az_set", None)
                el_set = getattr(self.tracked_object, "el_set", None)

                if not self.has_setpoints():
                    QMessageBox.information(
                        self,
                        "Tracking",
                        "Selectionnez un objet puis cliquez sur 'Appliquer la selection' pour calculer les consignes.",
                    )
                    return

                try:
                    selected_type = self.object_dropdown.currentText() if hasattr(self, "object_dropdown") else "?"
                    selected_object = (
                        self.specific_object_dropdown.currentText()
                        if hasattr(self, "specific_object_dropdown")
                        else "?"
                    )
                    az_cur = getattr(getattr(self, "axis_client", None), "antenna", None)
                    az_cur = getattr(az_cur, "az", None)
                    el_cur = (
                        getattr(getattr(self.axis_client, "antenna", None), "el", None)
                        if hasattr(self, "axis_client")
                        else None
                    )
                    self.logger.info(
                        f"[Tracking] Selection type='{selected_type}' object='{selected_object}' | "
                        f"tel az={az_cur} el={el_cur} | set az={az_set} el={el_set}"
                    )
                except Exception:
                    pass

                try:
                    self.thread_manager.stop_thread("TrackingLoop")
                except Exception:
                    pass

                try:
                    if hasattr(self.tracker, "mark_speeds_dirty"):
                        self.tracker.mark_speeds_dirty()
                except Exception:
                    pass
                self._auto_restart_tracking = True
                self._start_tracker_when_ready(attempts_left=20)

            else:
                self._auto_restart_tracking = False
                try:
                    self.logger.info("[UI] STOP tracking (user action)")
                except Exception:
                    pass
                self.tracker.stop()
                self.stop_tracking_ui_timer()
                self._ui_show_tracking_stopped()
                try:
                    self.pushButton_antenna_track.setText("Track")
                except Exception:
                    pass

        except Exception as exc:
            self.logger.error(f"Erreur on_track_button_clicked: {exc}")

    def setup_multi_tracking_tab_in_tabwidget3(self):
        """
        Add the MultiTrack strip to tabWidget_3 -> 'Solar System'.
        """
        tw = getattr(self, "tabWidget_3", None)
        if tw is None:
            self.logger.warning("tabWidget_3 introuvable dans l'UI.")
            return

        page = None
        for index in range(tw.count()):
            if tw.tabText(index).strip().lower() == "solar system":
                page = tw.widget(index)
                break
        if page is None:
            self.logger.warning("Onglet 'Solar System' introuvable dans tabWidget_3.")
            return

        from PyQt5.QtWidgets import QGridLayout as _QGridLayout, QVBoxLayout as _QVBoxLayout

        layout = page.layout()
        if layout is None:
            layout = _QVBoxLayout(page)
            page.setLayout(layout)

        self.multi_strip = MultiTrackStrip(self.ephem, on_pick=self._on_multitrack_pick, parent=page)

        if isinstance(layout, _QGridLayout):
            row = layout.rowCount()
            col_span = max(1, layout.columnCount())
            layout.addWidget(self.multi_strip, row, 0, 1, col_span)
        else:
            layout.addWidget(self.multi_strip)

    def _on_multitrack_pick(self, obj_type: str, name: str):
        try:
            if hasattr(self, "object_dropdown"):
                self.object_dropdown.setCurrentText(obj_type)
                self.update_secondary_dropdown()

            if hasattr(self, "specific_object_dropdown"):
                index = -1
                for current in range(self.specific_object_dropdown.count()):
                    if self.specific_object_dropdown.itemText(current).lower() == name.lower():
                        index = current
                        break
                if index >= 0:
                    self.specific_object_dropdown.setCurrentIndex(index)
                else:
                    self.specific_object_dropdown.addItem(name)
                    self.specific_object_dropdown.setCurrentText(name)

            self.on_apply_target_clicked()
            self.status_bar.showMessage(f"Objet selectionne: {obj_type} / {name}", 3000)
        except Exception as exc:
            self.logger.error(f"_on_multitrack_pick error: {exc}")

    def _on_multi_pick(self, obj_type: str, name: str):
        try:
            self.object_dropdown.setCurrentText(obj_type)
            self.update_secondary_dropdown()
            for index in range(self.specific_object_dropdown.count()):
                if self.specific_object_dropdown.itemText(index).lower() == name.lower():
                    self.specific_object_dropdown.setCurrentIndex(index)
                    break
            self.on_apply_target_clicked()
        except Exception as exc:
            self.logger.error(f"_on_multi_pick error: {exc}")

    def _parse_sat_label(self, label: str) -> str:
        """
        Extract the satellite name from a label like 'ISS [25544]'.
        """
        if not label:
            return ""
        normalized = label.strip()
        if normalized in ("(vide)", "(erreur)", "(aucun groupe)"):
            return ""
        pos = normalized.rfind("[")
        if pos > 0 and normalized.endswith("]"):
            return normalized[:pos].strip()
        return normalized

    def _current_sat_query(self) -> str:
        """
        Return the satellite query to send to Ephemeris.
        """
        query = (self.tle_query_edit.text() if hasattr(self, "tle_query_edit") else "") or ""
        query = query.strip()
        if query:
            return query
        label = (self.tle_sat_dropdown.currentText() if hasattr(self, "tle_sat_dropdown") else "") or ""
        return self._parse_sat_label(label)

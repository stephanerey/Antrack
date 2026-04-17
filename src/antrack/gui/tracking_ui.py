"""Tracking-related UI extraction for MainUi."""

from __future__ import annotations

from PyQt5.QtGui import QFont
from PyQt5.QtCore import QTimer, Qt
from PyQt5.QtWidgets import (
    QComboBox,
    QGridLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from antrack.gui.ui_styles import (
    green_label_color,
    lightgrey_label_color,
    orange_label_color,
    red_label_color,
    standard_label_color,
)
from antrack.gui.event_countdown import format_next_event_countdown, next_event_tooltip
from antrack.gui.widgets.multi_track_card import MultiTrackStrip
from antrack.tracking.positioning import PositioningController
from antrack.tracking.tracking import Tracker
from antrack.utils.settings_loader import update_and_persist_setting


class TrackingUiMixin:
    """Keep tracking selection and tracking-state UI logic out of main_ui.py."""

    def setup_manual_antenna_controls(self):
        """Wire the manual-control panel and initialize it in Auto mode."""
        self._manual_control_mode = False
        self._manual_jog_state = {"az": None, "el": None}

        manual_btn = getattr(self, "pushButton_antenna_manual", None)
        if manual_btn is not None:
            manual_btn.setCheckable(True)
            try:
                manual_btn.clicked.disconnect()
            except Exception:
                pass
            manual_btn.clicked.connect(self.on_manual_mode_toggled)

        goto_btn = getattr(self, "pushButton_antenna_manual_goto", None)
        if goto_btn is not None:
            goto_btn.clicked.connect(self.on_manual_goto_clicked)

        button_bindings = (
            ("pushButton_antenna_manual_left", "az", "CCW"),
            ("pushButton_antenna_manual_right", "az", "CW"),
            ("pushButton_antenna_manual_up", "el", "UP"),
            ("pushButton_antenna_manual_bottom", "el", "DOWN"),
        )
        for attr_name, axis, direction in button_bindings:
            button = getattr(self, attr_name, None)
            if button is None:
                continue
            button.setAutoRepeat(False)
            button.pressed.connect(lambda a=axis, d=direction: self._start_manual_jog(a, d))
            button.released.connect(lambda a=axis: self._stop_manual_jog(a))

        self._apply_manual_mode_ui()

    def _antenna_settings(self) -> dict:
        if not isinstance(self.settings, dict):
            return {}
        return self.settings.get("ANTENNA", self.settings.get("antenna", {})) or {}

    def _session_tracking_offset(self) -> tuple[float, float]:
        az = float(getattr(self, "_scan_session_offset_az_deg", 0.0) or 0.0)
        el = float(getattr(self, "_scan_session_offset_el_deg", 0.0) or 0.0)
        return az, el

    def _persistent_tracking_offset(self) -> tuple[float, float]:
        antenna = self._antenna_settings()
        az = float(antenna.get("scan_offset_az_deg", antenna.get("SCAN_OFFSET_AZ_DEG", 0.0)) or 0.0)
        el = float(antenna.get("scan_offset_el_deg", antenna.get("SCAN_OFFSET_EL_DEG", 0.0)) or 0.0)
        return az, el

    def _current_tracking_offset(self) -> tuple[float, float]:
        session_az, session_el = self._session_tracking_offset()
        persistent_az, persistent_el = self._persistent_tracking_offset()
        return session_az + persistent_az, session_el + persistent_el

    def _apply_tracking_offset_to_pointing(self, az_deg: float, el_deg: float) -> tuple[float, float]:
        offset_az, offset_el = self._current_tracking_offset()
        return float(az_deg) + offset_az, float(el_deg) + offset_el

    def _format_tracking_offset(self) -> str:
        offset_az, offset_el = self._current_tracking_offset()
        return f"dAZ={offset_az:+.3f} dEL={offset_el:+.3f}"

    def _update_selected_target_snr_display(self, snr_db: float, mode: str) -> None:
        if hasattr(self, "target_snr_label"):
            if isinstance(snr_db, (int, float)):
                self.target_snr_label.setText(f"{float(snr_db):.2f} dB ({mode})")
            else:
                self.target_snr_label.setText("-")

    def _update_selected_target_scan_offset_display(self) -> None:
        text = self._format_tracking_offset()
        if hasattr(self, "target_scan_offset_label"):
            self.target_scan_offset_label.setText(text)
        if hasattr(self, "tracked_object"):
            total_az, total_el = self._current_tracking_offset()
            self.tracked_object.scan_offset_az_deg = float(total_az)
            self.tracked_object.scan_offset_el_deg = float(total_el)

    def apply_scan_offset(self, az_offset_deg: float, el_offset_deg: float, *, persist: bool = False) -> None:
        if persist:
            self._scan_session_offset_az_deg = 0.0
            self._scan_session_offset_el_deg = 0.0
            update_and_persist_setting(self.settings, "ANTENNA", "SCAN_OFFSET_AZ_DEG", float(az_offset_deg))
            update_and_persist_setting(self.settings, "ANTENNA", "SCAN_OFFSET_EL_DEG", float(el_offset_deg))
        else:
            self._scan_session_offset_az_deg = float(az_offset_deg)
            self._scan_session_offset_el_deg = float(el_offset_deg)
        self._update_selected_target_scan_offset_display()

    def _stop_tracking_loop_from_ui(self):
        """Stop the motor tracking loop and restore the idle UI state."""
        self._auto_restart_tracking = False
        try:
            self.logger.info("[UI] STOP tracking")
        except Exception:
            pass
        try:
            if getattr(self, "tracker", None) is not None:
                self.tracker.stop()
        except Exception:
            pass
        self.stop_tracking_ui_timer()
        self._ui_show_tracking_stopped()
        try:
            self.pushButton_antenna_track.setText("Track")
        except Exception:
            pass

    def _stop_positioning_loop_from_ui(self):
        """Stop the fixed-position controller and restore the idle UI state."""
        try:
            if getattr(self, "positioner", None) is not None:
                self.positioner.stop()
        except Exception:
            pass
        self.stop_tracking_ui_timer()
        self._ui_show_tracking_stopped()

    def _motion_controller_running(self) -> bool:
        tracker_running = bool(getattr(self, "tracker", None) and self.tracker.is_running())
        positioner_running = bool(getattr(self, "positioner", None) and self.positioner.is_running())
        return tracker_running or positioner_running

    def _manual_control_widgets(self):
        return (
            "lineEdit_azimuth_goto_pos",
            "lineEdit_elevation_goto_pos",
            "lineEdit_azimuth_goto_rate",
            "lineEdit_elevation_goto_rate",
            "pushButton_antenna_manual_goto",
            "pushButton_antenna_manual_up",
            "pushButton_antenna_manual_left",
            "pushButton_antenna_manual_bottom",
            "pushButton_antenna_manual_right",
        )

    def _apply_manual_mode_ui(self):
        enabled = bool(getattr(self, "_manual_control_mode", False))
        for attr_name in self._manual_control_widgets():
            widget = getattr(self, attr_name, None)
            if widget is not None:
                widget.setEnabled(enabled)

        button = getattr(self, "pushButton_antenna_manual", None)
        if button is not None:
            button.blockSignals(True)
            button.setChecked(enabled)
            button.blockSignals(False)
            button.setText("Manual" if enabled else "Auto")
            button.setStyleSheet(orange_label_color if enabled else "")

    def _set_manual_control_mode(self, enabled: bool):
        enabled = bool(enabled)
        self._manual_control_mode = enabled
        self._manual_setpoint_mode = enabled
        if enabled:
            if getattr(self, "tracker", None) and self.tracker.is_running():
                self._stop_tracking_loop_from_ui()
            if getattr(self, "positioner", None) and self.positioner.is_running():
                self._stop_positioning_loop_from_ui()
            self._stop_manual_jog("az")
            self._stop_manual_jog("el")
            try:
                self.ephem.stop_object("primary")
            except Exception:
                pass
        else:
            if getattr(self, "positioner", None) and self.positioner.is_running():
                self._stop_positioning_loop_from_ui()
            self._stop_manual_jog("az")
            self._stop_manual_jog("el")
        self._apply_manual_mode_ui()
        if not enabled and not self._motion_controller_running():
            self._ui_show_tracking_stopped()
        elif enabled:
            try:
                if hasattr(self, "label_antenna_status"):
                    self.label_antenna_status.setText("Manual")
                    self.label_antenna_status.setStyleSheet(orange_label_color)
            except Exception:
                pass

    def on_manual_mode_toggled(self, checked: bool):
        self._set_manual_control_mode(bool(checked))

    def _manual_rate_for_axis(self, axis: str) -> float | None:
        edit_attr = "lineEdit_azimuth_goto_rate" if axis == "az" else "lineEdit_elevation_goto_rate"
        widget = getattr(self, edit_attr, None)
        raw = (widget.text() if widget is not None else "") or ""
        raw = raw.strip()
        if not raw:
            return None
        try:
            rate = float(raw)
        except Exception:
            return None
        return rate if rate > 0 else None

    def _start_manual_jog(self, axis: str, direction: str):
        if not getattr(self, "_manual_control_mode", False):
            return
        if not self.has_connection():
            return

        rate = self._manual_rate_for_axis(axis)
        if rate is None:
            QMessageBox.information(
                self,
                "Manual",
                "Renseignez une vitesse positive pour l'axe manuel utilise.",
            )
            return

        try:
            if getattr(self, "positioner", None) and self.positioner.is_running():
                self._stop_positioning_loop_from_ui()
            if getattr(self, "tracker", None) and self.tracker.is_running():
                self._stop_tracking_loop_from_ui()
        except Exception:
            pass

        try:
            if axis == "az":
                ack = self.thread_manager.run_coro("AxisCoreLoop", lambda: self.axis_client.axisClient.set_az_speed(rate), timeout=1.0)
                if ack is not None:
                    self.axis_client.antenna.az_setrate = rate
                if direction == "CW":
                    self.thread_manager.run_coro("AxisCoreLoop", self.axis_client.axisClient.move_cw, timeout=1.0)
                else:
                    self.thread_manager.run_coro("AxisCoreLoop", self.axis_client.axisClient.move_ccw, timeout=1.0)
                self._manual_jog_state["az"] = direction
            else:
                ack = self.thread_manager.run_coro("AxisCoreLoop", lambda: self.axis_client.axisClient.set_el_speed(rate), timeout=1.0)
                if ack is not None:
                    self.axis_client.antenna.el_setrate = rate
                if direction == "UP":
                    self.thread_manager.run_coro("AxisCoreLoop", self.axis_client.axisClient.move_up, timeout=1.0)
                else:
                    self.thread_manager.run_coro("AxisCoreLoop", self.axis_client.axisClient.move_down, timeout=1.0)
                self._manual_jog_state["el"] = direction

            if hasattr(self, "label_antenna_status"):
                self.label_antenna_status.setText("Manual")
                self.label_antenna_status.setStyleSheet(orange_label_color)
        except Exception as exc:
            self.logger.error(f"_start_manual_jog error: {exc}")

    def _stop_manual_jog(self, axis: str):
        if not getattr(self, "axis_client", None):
            return
        try:
            if axis == "az" and self._manual_jog_state.get("az") is not None:
                self.thread_manager.run_coro("AxisCoreLoop", self.axis_client.axisClient.stop_az, timeout=1.0)
                self._manual_jog_state["az"] = None
            elif axis == "el" and self._manual_jog_state.get("el") is not None:
                self.thread_manager.run_coro("AxisCoreLoop", self.axis_client.axisClient.stop_el, timeout=1.0)
                self._manual_jog_state["el"] = None
        except Exception as exc:
            self.logger.error(f"_stop_manual_jog error: {exc}")

        if not any(self._manual_jog_state.values()) and getattr(self, "_manual_control_mode", False):
            try:
                if hasattr(self, "label_antenna_status"):
                    self.label_antenna_status.setText("Manual")
                    self.label_antenna_status.setStyleSheet(orange_label_color)
            except Exception:
                pass

    def on_manual_goto_clicked(self):
        if not getattr(self, "_manual_control_mode", False):
            return
        if not self.has_connection():
            QMessageBox.warning(self, "Manual", "Veuillez d'abord vous connecter au serveur Axis.")
            return

        az_widget = getattr(self, "lineEdit_azimuth_goto_pos", None)
        el_widget = getattr(self, "lineEdit_elevation_goto_pos", None)
        az_raw = (az_widget.text() if az_widget is not None else "") or ""
        el_raw = (el_widget.text() if el_widget is not None else "") or ""
        try:
            az_set = float(az_raw.strip())
            el_set = float(el_raw.strip())
        except Exception:
            QMessageBox.information(
                self,
                "Manual Goto",
                "Renseignez un azimuth et une elevation valides.",
            )
            return

        self.start_fixed_positioning(az_set, el_set, label="Manual Goto")

    def _apply_manual_setpoints(self, az_set: float, el_set: float):
        """Load explicit AZ/EL setpoints into the shared tracking state and UI."""
        self.tracked_object.az_set = float(az_set)
        self.tracked_object.el_set = float(el_set)
        self.tracked_object.az_error = 0.0
        self.tracked_object.el_error = 0.0

        if hasattr(self, "label_antenna_az_set_deg"):
            self.label_antenna_az_set_deg.setText(f"{float(az_set):.2f}°")
        if hasattr(self, "label_antenna_el_set_deg"):
            self.label_antenna_el_set_deg.setText(f"{float(el_set):.2f}°")

        try:
            self.g1.set_setpoint(float(az_set))
            self.g2.set_setpoint(float(el_set))
        except Exception:
            pass

    def _ensure_positioner(self) -> bool:
        if self.positioner is None:
            try:
                self.positioner = PositioningController(
                    self.axis_client,
                    self.settings,
                    self.thread_manager,
                    self.tracked_object,
                )
            except Exception as exc:
                self.logger.error(f"Impossible d'initialiser le positioner: {exc}")
                QMessageBox.warning(self, "Positioning", "Initialisation du positionnement impossible.")
                return False
        return True

    def _clear_selected_target_details(self):
        """Clear non-pointing fields when the target context is no longer an ephemeris object."""
        try:
            text_fields = (
                "label_object_distance_km",
                "target_ra_label",
                "target_dec_label",
                "target_dist_au_label",
                "target_el_now_label",
                "target_next_event_label",
                "target_dur_label",
                "target_aos_label",
                "target_los_label",
                "target_max_el_label",
                "target_max_el_time_label",
            )
            for attr in text_fields:
                if hasattr(self, attr):
                    widget = getattr(self, attr)
                    widget.setText("-")
                    widget.setStyleSheet("")
                    try:
                        widget.setToolTip("-")
                    except Exception:
                        pass
            if hasattr(self, "target_visible_now_label"):
                self.target_visible_now_label.setText("-")
                self.target_visible_now_label.setStyleSheet("")
                try:
                    self.target_visible_now_label.setToolTip("-")
                except Exception:
                    pass
        except Exception as exc:
            self.logger.error(f"_clear_selected_target_details error: {exc}")

    def _start_fixed_positioning_motion(self, attempts_left: int = 20):
        """Start fixed-position motion toward the already-loaded AZ/EL setpoints."""
        try:
            if getattr(self, "tracker", None) and self.tracker.is_running():
                if attempts_left > 0:
                    QTimer.singleShot(100, lambda: self._start_fixed_positioning_motion(attempts_left - 1))
                else:
                    self.logger.warning("[Positioning] Tracker still running; fixed-position restart abandoned")
                return
            if getattr(self, "positioner", None) and self.positioner.is_running():
                return
            if not self._ensure_positioner():
                return
            if hasattr(self, "pushButton_antenna_track"):
                self.pushButton_antenna_track.setEnabled(True)
                self.pushButton_antenna_track.setText("Stop")
            self.positioner.start()
            self.start_tracking_ui_timer()
        except Exception as exc:
            self.logger.error(f"_start_fixed_positioning_motion error: {exc}")

        try:
            self._update_tracking_ui()
        except Exception:
            pass

    def start_fixed_positioning(self, az_set: float, el_set: float, label: str = "Goto"):
        """Drive the antenna to a fixed AZ/EL target and stop on arrival."""
        self._manual_setpoint_mode = True

        if getattr(self, "tracker", None) and self.tracker.is_running():
            self._stop_tracking_loop_from_ui()
        if getattr(self, "positioner", None) and self.positioner.is_running():
            self._stop_positioning_loop_from_ui()

        try:
            self.ephem.stop_object("primary")
        except Exception:
            pass

        self._apply_manual_setpoints(az_set, el_set)
        if hasattr(self, "pushButton_antenna_track"):
            self.pushButton_antenna_track.setEnabled(True)
            self.pushButton_antenna_track.setText("Track")

        try:
            if hasattr(self, "label_tracked_object"):
                self.label_tracked_object.setText(label)
                self._apply_selected_target_header_style()
        except Exception:
            pass

        self._clear_selected_target_details()
        QTimer.singleShot(100, lambda: self._start_fixed_positioning_motion(attempts_left=20))

    def _apply_selected_target_header_style(self):
        """Keep the selected-target header centered and visually prominent."""
        if not hasattr(self, "label_tracked_object"):
            return
        self.label_tracked_object.setAlignment(Qt.AlignCenter)
        font = self.label_tracked_object.font() or QFont()
        base_size = getattr(self, "_selected_target_header_base_point_size", None)
        if not isinstance(base_size, int) or base_size <= 0:
            base_size = font.pointSize()
            if base_size <= 0:
                base_size = 9
            self._selected_target_header_base_point_size = base_size
        font.setBold(True)
        font.setPointSize(base_size + 2)
        self.label_tracked_object.setFont(font)
        self.label_tracked_object.setStyleSheet(orange_label_color)

    def _select_target_from_card(self, obj_type: str, name: str):
        """Map a quick-pick card click back to the target-selection UI."""
        if not hasattr(self, "object_dropdown"):
            return

        if (self.object_dropdown.currentText() or "") != obj_type:
            self.object_dropdown.setCurrentText(obj_type)

        if obj_type == "Artificial Satellite":
            if hasattr(self, "tle_query_edit"):
                self.tle_query_edit.setText(name)
            if hasattr(self, "apply_target_btn"):
                self.apply_target_btn.setEnabled(bool(name))
            return

        if obj_type == "Radio Source":
            if hasattr(self, "rs_query_edit"):
                self.rs_query_edit.setText(name)
            if hasattr(self, "apply_target_btn"):
                self.apply_target_btn.setEnabled(bool(name))
            return

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

    def _prime_selected_target_display(self, name: str):
        """Show the newly selected target immediately while fresh ephemeris is loading."""
        try:
            if hasattr(self, "label_tracked_object"):
                self.label_tracked_object.setText(str(name or "-"))
                self._apply_selected_target_header_style()

            self._clear_selected_target_details()

            running = self._motion_controller_running()
            if not running:
                for attr in ("label_antenna_az_set_deg", "label_antenna_el_set_deg"):
                    if hasattr(self, attr):
                        widget = getattr(self, attr)
                        widget.setText("-")
                        widget.setStyleSheet("")
        except Exception as exc:
            self.logger.error(f"_prime_selected_target_display error: {exc}")

    def _setup_selected_target_groupbox(self) -> bool:
        """Create the live target-info labels inside groupBox_SelectedTarget when available."""
        group = getattr(self, "groupBox_SelectedTarget", None)
        if group is None:
            return False
        if getattr(self, "_selected_target_groupbox_ready", False):
            return True

        self._selected_target_groupbox_ready = True

        if not hasattr(self, "label_tracked_object"):
            self.label_tracked_object = QLabel("-", group)
            self.label_tracked_object.setGeometry(10, 30, 121, 21)
        self._apply_selected_target_header_style()

        for attr in (
            "label_LocalTime_10",
            "label_LocalTime_11",
            "label_LocalTime_19",
            "label_antenna_az_set_deg",
            "label_antenna_el_set_deg",
            "label_object_distance_km",
        ):
            if hasattr(self, attr):
                try:
                    getattr(self, attr).hide()
                except Exception:
                    pass

        panel = QWidget(group)
        panel.setObjectName("selected_target_info_panel")
        panel.setGeometry(10, 60, 231, 275)

        layout = QGridLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setHorizontalSpacing(8)
        layout.setVerticalSpacing(3)
        layout.setColumnStretch(1, 1)

        def add_row(row: int, title: str, attr_name: str):
            title_label = QLabel(title, panel)
            value_label = QLabel("-", panel)
            title_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            value_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            title_label.setMinimumWidth(82)
            value_label.setStyleSheet("")
            setattr(self, attr_name, value_label)
            layout.addWidget(title_label, row, 0)
            layout.addWidget(value_label, row, 1)

        add_row(0, "Azimuth", "label_antenna_az_set_deg")
        add_row(1, "Elevation", "label_antenna_el_set_deg")
        add_row(2, "Distance (km)", "label_object_distance_km")
        add_row(3, "RA", "target_ra_label")
        add_row(4, "DEC", "target_dec_label")
        add_row(5, "Distance (AU)", "target_dist_au_label")
        add_row(6, "Visible", "target_visible_now_label")
        add_row(7, "EL Now", "target_el_now_label")
        add_row(8, "Next Event", "target_next_event_label")
        add_row(9, "Duration", "target_dur_label")
        add_row(10, "AOS", "target_aos_label")
        add_row(11, "LOS", "target_los_label")
        add_row(12, "Max EL", "target_max_el_label")
        add_row(13, "Max EL @", "target_max_el_time_label")
        add_row(14, "SNR", "target_snr_label")
        add_row(15, "Scan Offset", "target_scan_offset_label")

        self.target_max_el_time_label.setWordWrap(True)
        self._update_selected_target_scan_offset_display()
        self._selected_target_info_panel = panel
        return True

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

            if not self._setup_selected_target_groupbox():
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
                add_row("Next event:", "target_next_event_label")
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
            if getattr(self, "_manual_setpoint_mode", False):
                return

            az = payload.get("az")
            el = payload.get("el")
            dist_km = payload.get("dist_km")
            dist_au = payload.get("dist_au")
            ra_hms = payload.get("ra_hms")
            dec_dms = payload.get("dec_dms")

            corrected_az = az
            corrected_el = el
            if isinstance(az, (int, float)) and isinstance(el, (int, float)):
                corrected_az, corrected_el = self._apply_tracking_offset_to_pointing(float(az), float(el))

            if isinstance(corrected_az, (int, float)):
                self.tracked_object.az_set = corrected_az
            if isinstance(corrected_el, (int, float)):
                self.tracked_object.el_set = corrected_el
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

            payload = dict(payload)
            payload["az"] = corrected_az
            payload["el"] = corrected_el
            self.ui_update_tracked_display(payload)

            az_ok = isinstance(self.tracked_object.az_set, (int, float))
            el_ok = isinstance(self.tracked_object.el_set, (int, float))
            if self.telemetry_ready and az_ok and el_ok and hasattr(self, "pushButton_antenna_track"):
                self.pushButton_antenna_track.setEnabled(True)

            try:
                if hasattr(self, "calib_plots") and self.calib_plots is not None:
                    self.calib_plots.update_current(corrected_az, corrected_el)
            except Exception:
                pass

        except Exception as exc:
            self.logger.error(f"Erreur _on_pose_updated: {exc}")

    def start_object_selection_tracking(self):
        """Start the periodic position computation for the selected object."""
        try:
            if getattr(self, "_manual_control_mode", False):
                self._set_manual_control_mode(False)
            self._manual_setpoint_mode = False
            if getattr(self, "positioner", None) and self.positioner.is_running():
                self._stop_positioning_loop_from_ui()
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
                self._apply_selected_target_header_style()

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
            if hasattr(self, "label_object_distance_km"):
                self.label_object_distance_km.setText(
                    f"{dist_km:.0f}" if isinstance(dist_km, (int, float)) else "-"
                )
                self.label_object_distance_km.setStyleSheet("")

            running = self._motion_controller_running()
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
                self.label_antenna_ra_set.setStyleSheet("")
            if isinstance(dec_dms, tuple) and hasattr(self, "label_antenna_dec_set"):
                degree, minute, second = dec_dms
                self.label_antenna_dec_set.setText(f"{int(degree)}° {int(minute)}' {second:04.1f}\"")
                self.label_antenna_dec_set.setStyleSheet("")
            if isinstance(dist_au, (int, float)) and hasattr(self, "label_object_distance_au"):
                self.label_object_distance_au.setText(f"{dist_au:.3f}")
                self.label_object_distance_au.setStyleSheet("")

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
                    self.target_visible_now_label.setStyleSheet("")

            aos_utc = payload.get("aos_utc")
            los_utc = payload.get("los_utc")
            max_el_time_utc = payload.get("max_el_time_utc")

            if hasattr(self, "target_aos_label"):
                self.target_aos_label.setText(self.format_event_time_for_ui(aos_utc) if aos_utc else "-")
                self.target_aos_label.setToolTip(self.format_event_tooltip_for_ui(aos_utc))
            if hasattr(self, "target_los_label"):
                self.target_los_label.setText(self.format_event_time_for_ui(los_utc) if los_utc else "-")
                self.target_los_label.setToolTip(self.format_event_tooltip_for_ui(los_utc))

            if hasattr(self, "target_dur_label"):
                duration = payload.get("dur_str")
                self.target_dur_label.setText(duration if duration else "-")
            if hasattr(self, "target_next_event_label"):
                self.target_next_event_label.setText(format_next_event_countdown(payload))
                self.target_next_event_label.setToolTip(next_event_tooltip(payload))

            if hasattr(self, "target_max_el_label"):
                max_el = payload.get("max_el_deg")
                self.target_max_el_label.setText(f"{max_el:.1f}°" if isinstance(max_el, (int, float)) else "-")
            if hasattr(self, "target_max_el_time_label"):
                self.target_max_el_time_label.setText(
                    self.format_event_time_for_ui(max_el_time_utc) if max_el_time_utc else "-"
                )
                self.target_max_el_time_label.setToolTip(self.format_event_tooltip_for_ui(max_el_time_utc))

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
                ack = self.thread_manager.run_coro(
                    "AxisCoreLoop",
                    lambda: self.axis_client.axisClient.set_az_speed(az_far),
                    timeout=1.0,
                )
                try:
                    if ack is not None:
                        self.axis_client.antenna.az_setrate = az_far
                except Exception:
                    pass
            except Exception as exc:
                self.logger.debug(f"prime_axis_motion: set_az_speed erreur: {exc}")
            try:
                ack = self.thread_manager.run_coro(
                    "AxisCoreLoop",
                    lambda: self.axis_client.axisClient.set_el_speed(el_far),
                    timeout=1.0,
                )
                try:
                    if ack is not None:
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

            tracker_running = bool(getattr(self, "tracker", None) and self.tracker.is_running())
            positioner_running = bool(getattr(self, "positioner", None) and self.positioner.is_running())
            running = tracker_running or positioner_running
            if hasattr(self, "label_antenna_status"):
                if tracker_running:
                    self.label_antenna_status.setText("Tracking")
                    self.label_antenna_status.setStyleSheet(green_label_color)
                elif positioner_running:
                    self.label_antenna_status.setText("Positioning")
                    self.label_antenna_status.setStyleSheet(orange_label_color)
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
                try:
                    if hasattr(self, "pushButton_antenna_track"):
                        self.pushButton_antenna_track.setText("Track")
                    self.stop_tracking_ui_timer()
                except Exception:
                    pass

        except Exception as exc:
            self.logger.error(f"Erreur _update_tracking_ui: {exc}")

    def on_apply_target_clicked(self):
        try:
            if getattr(self, "_manual_control_mode", False):
                self._set_manual_control_mode(False)
            self._manual_setpoint_mode = False
            if getattr(self, "positioner", None) and self.positioner.is_running():
                self._stop_positioning_loop_from_ui()
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

            self._prime_selected_target_display(selected_object)
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
            if (
                getattr(self, "_manual_control_mode", False)
                and not (getattr(self, "positioner", None) and self.positioner.is_running())
            ):
                QMessageBox.information(self, "Tracking", "Repassez en mode Auto pour utiliser Track.")
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

            if getattr(self, "positioner", None) and self.positioner.is_running():
                self._stop_positioning_loop_from_ui()
                try:
                    self.pushButton_antenna_track.setText("Track")
                except Exception:
                    pass
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
                self._stop_tracking_loop_from_ui()

        except Exception as exc:
            self.logger.error(f"Erreur on_track_button_clicked: {exc}")

    def on_park_button_clicked(self):
        """Toggle out of target tracking and drive the antenna toward park setpoints."""
        try:
            if not self.has_connection():
                QMessageBox.warning(self, "Park", "Veuillez d'abord vous connecter au serveur Axis.")
                return
            if getattr(self, "_manual_control_mode", False):
                self._set_manual_control_mode(False)

            try:
                self.ephem.stop_object("primary")
            except Exception:
                pass

            antenna_settings = self._antenna_settings()
            park_az = antenna_settings.get("PARK_AZ", antenna_settings.get("park_az"))
            park_el = antenna_settings.get("PARK_EL", antenna_settings.get("park_el"))
            if park_az is None or park_el is None:
                QMessageBox.warning(
                    self,
                    "Park",
                    "Parametres PARK_AZ / PARK_EL manquants dans la section ANTENNA.",
                )
                return

            try:
                park_az = float(park_az)
                park_el = float(park_el)
            except Exception:
                QMessageBox.warning(
                    self,
                    "Park",
                    "Parametres PARK_AZ / PARK_EL invalides dans la section ANTENNA.",
                )
                return

            self.start_fixed_positioning(park_az, park_el, label="Park")

            try:
                self.status_bar.showMessage(
                    f"Park en cours: AZ={park_az:.2f}° EL={park_el:.2f}°",
                    5000,
                )
            except Exception:
                pass

        except Exception as exc:
            self.logger.error(f"Erreur on_park_button_clicked: {exc}")
            QMessageBox.warning(self, "Park", f"Impossible de charger le park:\n{exc}")

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
            self._select_target_from_card(obj_type, name)
            self.on_apply_target_clicked()
            self.status_bar.showMessage(f"Objet selectionne: {obj_type} / {name}", 3000)
        except Exception as exc:
            self.logger.error(f"_on_multitrack_pick error: {exc}")

    def _on_multi_pick(self, obj_type: str, name: str):
        try:
            self._select_target_from_card(obj_type, name)
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

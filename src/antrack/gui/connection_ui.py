"""Connection and live-telemetry UI extraction for MainUi."""

from __future__ import annotations
from datetime import datetime
from time import monotonic

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QComboBox, QFrame, QGridLayout, QLabel, QMessageBox, QSizePolicy

from antrack.core.antenna.config import load_antenna_connection_config
from antrack.core.antenna.types import AntennaConnectionMode
from antrack.core.antenna.operational_status import (
    AxisIndexState,
    AxisOperationalState,
    TrackingPermission,
    decode_axis_index_state,
    decode_axis_operational_status,
    evaluate_tracking_permission,
    status_stale_timeout,
)
from antrack.core.antenna.controller_qt import AntennaControllerQt
from antrack.core.axis.axis_client import AxisClientPollingAdapter
from antrack.gui.ui_styles import (
    green_label_color,
    lightgrey_label_color,
    orange_label_color,
    red_label_color,
    standard_label_color,
)
from antrack.tracking.tracking import Tracker
from antrack.utils.settings_loader import update_and_persist_setting


_AXIS_DRIVER_REFERENCE_WARNING = "Antenna not referenced - pass AZ/EL index before trusting position"
_INDEX_PASSING_BLUE = "color: white; background-color: #2F80ED;"
_TOP_BANNER_GROUP_HEIGHT = 82
_ANTENNA_LINK_FIELD_WIDTH = 150


def format_antenna_endpoint_summary(config, mode: str | None = None) -> str:
    selected_mode = str(mode or getattr(config.mode, "value", AntennaConnectionMode.AXIS_SERVER.value))
    if selected_mode == AntennaConnectionMode.AXIS_SERVER.value:
        endpoint = config.axis_server
        return f"{endpoint.host}:{endpoint.port}"
    if selected_mode == AntennaConnectionMode.AXIS_DRIVER.value:
        endpoint = config.axis_driver
        return f"{endpoint.comport} @ {endpoint.baudrate}"
    if selected_mode == AntennaConnectionMode.PST_ROTATOR.value:
        endpoint = config.pst_rotator
        return f"{endpoint.host} {endpoint.udp_port}"
    return "-"


def format_axis_index_status(mode: str, index_value: int | None) -> str:
    if str(mode) != AntennaConnectionMode.AXIS_DRIVER.value:
        return "N/A"
    if index_value == 0:
        return "NOT REF"
    if index_value == 1:
        return "REF"
    if index_value == 2:
        return "TRIG"
    return "UNKNOWN"


def axis_reference_valid(mode: str, index_az: int | None, index_el: int | None) -> bool | None:
    if str(mode) != AntennaConnectionMode.AXIS_DRIVER.value:
        return None
    return (index_az in (1, 2)) and (index_el in (1, 2))


def compute_axis_reference_indicator(
    mode: str,
    index_value: int | None,
    latched: bool,
    flash_active: bool = False,
) -> tuple[str, bool]:
    if str(mode) != AntennaConnectionMode.AXIS_DRIVER.value:
        return "N/A", False
    if latched and (index_value == 2 or flash_active):
        return "PASSING", True
    if latched:
        return "REF", True
    if index_value == 0:
        return "NOT REF", False
    if index_value == 1:
        return "REF", True
    if index_value == 2:
        return "TRIG", True
    return "UNKNOWN", False


def format_axis_index_tooltip(
    axis_name: str,
    mode: str,
    index_value: int | None,
    latched: bool = False,
    passing: bool = False,
) -> str:
    prefix = f"{axis_name} index:"
    if str(mode) != AntennaConnectionMode.AXIS_DRIVER.value:
        return f"{prefix} N/A"
    raw_value = "None" if index_value is None else str(index_value)
    if latched and passing:
        return f"{prefix} referenced, raw={raw_value}, passing index"
    if latched and index_value is None:
        return f"{prefix} referenced, raw=unknown"
    if latched:
        return f"{prefix} referenced, raw={raw_value}"
    if index_value == 0:
        return f"{prefix} not referenced, raw=0"
    if index_value == 1:
        return f"{prefix} referenced, raw=1"
    if index_value == 2:
        return f"{prefix} acquiring, raw=2"
    return f"{prefix} unknown, raw={raw_value}"


def _axis_index_style(display_state: str) -> str:
    if display_state == "NOT REF":
        return red_label_color
    if display_state == "REF":
        return green_label_color
    if display_state == "TRIG":
        return orange_label_color
    if display_state == "PASSING":
        return _INDEX_PASSING_BLUE
    if display_state == "UNKNOWN":
        return lightgrey_label_color
    return lightgrey_label_color


def _axis_operational_style(state: AxisOperationalState) -> str:
    if state == AxisOperationalState.OK:
        return green_label_color
    if state == AxisOperationalState.ALARM:
        return red_label_color
    return lightgrey_label_color


def format_axis_operational_tooltip(status) -> str:
    lines = [f"{status.axis} axis status: {status.state.value}"]
    if status.state == AxisOperationalState.UNKNOWN:
        lines.append("No valid fresh status received.")
    else:
        lines.extend(status.active_flags)
        lines.append(f"Raw endstop: {status.raw_endstop}")
        lines.append(f"Raw motor alarm: {status.raw_motor_alarm}")
    if isinstance(status.updated_timestamp, (int, float)):
        lines.append(f"Last update: {datetime.fromtimestamp(status.updated_timestamp).strftime('%H:%M:%S.%f')[:-3]}")
    return "\n".join(lines)


class ConnectionUiMixin:
    """Own Axis connection, polling, and live telemetry UI glue."""

    def setup_connection_mode_selector(self):
        config = load_antenna_connection_config(self.settings)
        self._antenna_mode_items = (
            ("Axis Server", "axis_server"),
            ("AxisDriver", "axis_driver"),
            ("PstRotator", "pst_rotator"),
        )
        self.label_antenna_mode = QLabel("Antenna mode", self)
        self.combo_antenna_mode = QComboBox(self)
        for text, value in self._antenna_mode_items:
            self.combo_antenna_mode.addItem(text, value)
        index = max(
            0,
            next(
                (idx for idx, (_text, value) in enumerate(self._antenna_mode_items) if value == config.mode.value),
                0,
            ),
        )
        self.combo_antenna_mode.setCurrentIndex(index)
        self.combo_antenna_mode.currentIndexChanged.connect(self.on_antenna_mode_changed)
        self.az_reference_latched = False
        self.el_reference_latched = False
        self._az_index_blue_until = 0.0
        self._el_index_blue_until = 0.0
        self._latest_antenna_status_payload = {}
        self._axis_operational_signatures = {}
        self._last_tracking_inhibit_signature = None
        self._setup_connection_link_panel()
        self._setup_reference_status_panel()
        self._refresh_connection_panel()
        self._refresh_reference_status_panel()

    def _setup_connection_link_panel(self):
        group = getattr(self, "groupBox_10", None)
        if group is None:
            return

        geometry = group.geometry()
        group.setGeometry(geometry.x(), geometry.y(), geometry.width(), max(geometry.height(), _TOP_BANNER_GROUP_HEIGHT))
        group.setMinimumHeight(_TOP_BANNER_GROUP_HEIGHT)
        group.setTitle("Antenna Link")
        self._relayout_top_banner_groups()

        self.label_antenna_mode.setParent(group)
        self.label_antenna_mode.setText("Mode")
        self.label_antenna_mode.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

        self.label_antenna_endpoint = QLabel("Endpoint", group)
        self.label_antenna_endpoint_summary = QLabel("-", group)
        self.label_antenna_endpoint_summary.setFrameShape(QFrame.StyledPanel)
        self.label_antenna_endpoint_summary.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.label_antenna_endpoint_summary.setStyleSheet(standard_label_color)
        self.label_antenna_endpoint.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.label_antenna_endpoint_summary.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

        if hasattr(self, "label_LocalTime_40"):
            self.label_LocalTime_40.setText("Version")
            self.label_LocalTime_40.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

        self.label_antenna_server_status.setAlignment(Qt.AlignCenter)
        self.label_antenna_server_status.setMinimumWidth(110)
        self.label_antenna_server_status.setMinimumHeight(22)
        self.label_antenna_server_status.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.label_axisapp_version.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.label_axisapp_version.setMinimumWidth(90)
        self.label_axisapp_version.setMinimumHeight(20)
        self.label_axisapp_version.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.label_axisapp_version.setMaximumWidth(120)
        self.combo_antenna_mode.setFixedWidth(_ANTENNA_LINK_FIELD_WIDTH)
        self.combo_antenna_mode.setMinimumHeight(24)
        self.pushButton_server_connect.setMinimumWidth(104)
        self.pushButton_server_connect.setMinimumHeight(24)
        self.pushButton_server_connect.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.label_antenna_endpoint_summary.setFixedWidth(_ANTENNA_LINK_FIELD_WIDTH)
        self.label_antenna_endpoint_summary.setMinimumHeight(20)

        layout = QGridLayout()
        layout.setContentsMargins(8, 12, 8, 6)
        layout.setHorizontalSpacing(8)
        layout.setVerticalSpacing(3)
        layout.addWidget(self.pushButton_server_connect, 0, 0)
        layout.addWidget(self.label_antenna_server_status, 1, 0)
        layout.addWidget(self.label_antenna_mode, 0, 1)
        layout.addWidget(self.combo_antenna_mode, 0, 2)
        layout.addWidget(self.label_antenna_endpoint, 1, 1)
        layout.addWidget(self.label_antenna_endpoint_summary, 1, 2)
        layout.addWidget(self.label_LocalTime_40, 1, 3)
        layout.addWidget(self.label_axisapp_version, 1, 4)
        layout.setColumnMinimumWidth(0, 110)
        layout.setColumnMinimumWidth(2, 130)
        group.setLayout(layout)

    def _relayout_top_banner_groups(self):
        frame = getattr(self, "frame_top_bar", None)
        group = getattr(self, "groupBox_10", None)
        time_group = getattr(self, "groupBox_Time", None)
        if frame is None or group is None or time_group is None:
            return

        frame_width = max(int(frame.width()), 900)
        banner_height = max(int(group.height()), _TOP_BANNER_GROUP_HEIGHT)
        spacing = 8
        time_width = max(int(time_group.width()), 241)
        link_width = min(590, max(520, frame_width - time_width - spacing - 8))
        time_x = min(link_width + spacing, max(0, frame_width - time_width))

        frame.setMinimumHeight(banner_height)
        group.setGeometry(0, 0, link_width, banner_height)
        time_group.setMinimumHeight(banner_height)
        time_group.setGeometry(time_x, 0, time_width, banner_height)

    def _setup_reference_status_panel(self):
        group = getattr(self, "groupBox_5", None)
        if group is None:
            return

        if not hasattr(self, "label_antenna_index_az"):
            self.label_antenna_reference_title = QLabel("Index", group)
            self.label_antenna_reference_title.setGeometry(10, 140, 91, 20)
            self.label_antenna_reference_title.setStyleSheet("")
            self.label_antenna_reference_title.setFrameShape(QFrame.NoFrame)
            self.label_antenna_reference_title.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)

            self.label_antenna_index_az = QLabel("", group)
            self.label_antenna_index_el = QLabel("", group)
            self.label_antenna_index_az.setGeometry(123, 143, 14, 14)
            self.label_antenna_index_el.setGeometry(193, 143, 14, 14)

        self.label_antenna_axis_status_title = QLabel("Status", group)
        self.label_antenna_axis_status_title.setGeometry(10, 162, 91, 20)
        self.label_antenna_axis_status_title.setFrameShape(QFrame.NoFrame)
        self.label_antenna_axis_status_title.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.label_antenna_status_az = QLabel("", group)
        self.label_antenna_status_el = QLabel("", group)
        self.label_antenna_status_az.setGeometry(123, 165, 14, 14)
        self.label_antenna_status_el.setGeometry(193, 165, 14, 14)

        for widget in (
            self.label_antenna_index_az,
            self.label_antenna_index_el,
            self.label_antenna_status_az,
            self.label_antenna_status_el,
        ):
            widget.setFrameShape(QFrame.Box)
            widget.setLineWidth(1)
            widget.setAlignment(Qt.AlignCenter)
            widget.setStyleSheet(lightgrey_label_color)
            widget.setText("")
            widget.setToolTip("")
            widget.setAccessibleDescription("")

        if hasattr(self, "verticalLayoutWidget_2"):
            self.verticalLayoutWidget_2.setGeometry(10, 190, 221, 331)
            layout = self.verticalLayout_gauges.layout()
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(4)

    def selected_antenna_mode(self) -> str:
        combo = getattr(self, "combo_antenna_mode", None)
        if combo is None:
            return load_antenna_connection_config(self.settings).mode.value
        return str(combo.currentData() or "axis_server")

    def on_antenna_mode_changed(self, index: int):
        combo = getattr(self, "combo_antenna_mode", None)
        if combo is None:
            return
        mode = str(combo.itemData(index) or "axis_server")
        if self.has_connection():
            combo.blockSignals(True)
            try:
                config = load_antenna_connection_config(self.settings)
                previous = config.mode.value
                revert_index = next(
                    (idx for idx, (_text, value) in enumerate(self._antenna_mode_items) if value == previous),
                    0,
                )
                combo.setCurrentIndex(revert_index)
            finally:
                combo.blockSignals(False)
            QMessageBox.information(
                self,
                "Antenna mode",
                "Disconnect the antenna before changing backend mode.",
            )
            return
        update_and_persist_setting(self.settings, "ANTENNA_CONNECTION", "MODE", mode)
        self._reset_reference_latches()
        self._refresh_connection_panel()
        self._refresh_reference_status_panel()

    def has_connection(self) -> bool:
        return bool(getattr(self, "axis_client", None) and self.axis_client.is_connected())

    def _current_connection_config(self):
        config = load_antenna_connection_config(self.settings)
        config.mode = AntennaConnectionMode.from_value(self.selected_antenna_mode())
        return config

    def _refresh_connection_panel(self):
        self._relayout_top_banner_groups()
        if hasattr(self, "label_antenna_endpoint_summary"):
            try:
                config = self._current_connection_config()
                self.label_antenna_endpoint_summary.setText(
                    format_antenna_endpoint_summary(config, self.selected_antenna_mode())
                )
            except Exception:
                self.label_antenna_endpoint_summary.setText("-")

        combo = getattr(self, "combo_antenna_mode", None)
        if combo is not None:
            combo.setEnabled(not self.has_connection())

    def _reset_reference_latches(self):
        self.az_reference_latched = False
        self.el_reference_latched = False
        self._az_index_blue_until = 0.0
        self._el_index_blue_until = 0.0
        self._latest_antenna_status_payload = {}
        self._axis_operational_signatures = {}
        self._last_tracking_inhibit_signature = None

    def _accept_antenna_telemetry_payload(self, data: dict) -> bool:
        """Reject queued telemetry snapshots older than the latest displayed status."""
        if not isinstance(data, dict):
            return False
        previous = self._latest_antenna_status_payload
        if not isinstance(previous, dict) or not previous:
            return True
        previous_update = previous.get("status_update_monotonic")
        incoming_update = data.get("status_update_monotonic")
        if not isinstance(previous_update, (int, float)):
            return True
        if not isinstance(incoming_update, (int, float)):
            return False
        return incoming_update >= previous_update

    def _refresh_reference_status_panel(self, data: dict | None = None):
        if not hasattr(self, "label_antenna_index_az") or not hasattr(self, "label_antenna_index_el"):
            return

        mode = self.selected_antenna_mode()
        payload = data if isinstance(data, dict) else {}
        index_az = payload.get("index_az")
        index_el = payload.get("index_el")
        now = monotonic()

        if mode == AntennaConnectionMode.AXIS_DRIVER.value and self.az_reference_latched and index_az == 2:
            self._az_index_blue_until = max(self._az_index_blue_until, now + 0.5)
        if mode == AntennaConnectionMode.AXIS_DRIVER.value and self.el_reference_latched and index_el == 2:
            self._el_index_blue_until = max(self._el_index_blue_until, now + 0.5)

        az_flash_active = mode == AntennaConnectionMode.AXIS_DRIVER.value and now < self._az_index_blue_until
        el_flash_active = mode == AntennaConnectionMode.AXIS_DRIVER.value and now < self._el_index_blue_until

        az_state, next_az_latched = compute_axis_reference_indicator(
            mode,
            index_az,
            self.az_reference_latched,
            flash_active=az_flash_active,
        )
        el_state, next_el_latched = compute_axis_reference_indicator(
            mode,
            index_el,
            self.el_reference_latched,
            flash_active=el_flash_active,
        )
        self.az_reference_latched = next_az_latched
        self.el_reference_latched = next_el_latched

        self.label_antenna_index_az.setText("")
        self.label_antenna_index_el.setText("")
        self.label_antenna_index_az.setStyleSheet(_axis_index_style(az_state))
        self.label_antenna_index_el.setStyleSheet(_axis_index_style(el_state))
        self.label_antenna_index_az.setToolTip(
            format_axis_index_tooltip(
                "AZ",
                mode,
                index_az,
                self.az_reference_latched,
                passing=(az_state == "PASSING"),
            )
        )
        self.label_antenna_index_el.setToolTip(
            format_axis_index_tooltip(
                "EL",
                mode,
                index_el,
                self.el_reference_latched,
                passing=(el_state == "PASSING"),
            )
        )
        self.label_antenna_index_az.setAccessibleDescription(az_state)
        self.label_antenna_index_el.setAccessibleDescription(el_state)

        if mode == AntennaConnectionMode.AXIS_DRIVER.value and not (
            self.az_reference_latched and self.el_reference_latched
        ):
            self.label_antenna_reference_title.setToolTip(_AXIS_DRIVER_REFERENCE_WARNING)
        else:
            self.label_antenna_reference_title.setToolTip("")

        self._refresh_axis_operational_panel(payload)

    def _axis_status_stale_timeout(self) -> float:
        status_period = 1.0
        try:
            status_period = float(getattr(self.axis_client, "polling_intervals", (0.2, 1.0))[1])
        except Exception:
            pass
        return status_stale_timeout(status_period)

    def _axis_safety_state(self, data: dict | None = None):
        payload = data if isinstance(data, dict) else self._latest_antenna_status_payload
        payload = payload if isinstance(payload, dict) else {}
        stale_timeout_s = self._axis_status_stale_timeout()
        common = {
            "updated_monotonic": payload.get("status_update_monotonic"),
            "updated_timestamp": payload.get("status_update_timestamp"),
            "stale_timeout_s": stale_timeout_s,
        }
        az_status = decode_axis_operational_status(
            "AZ",
            endstop=payload.get("endstop_az"),
            motor_alarm=payload.get("motor_alarm_az"),
            modbus_status=payload.get("modbus_status_az", payload.get("modbus_az")),
            **common,
        )
        el_status = decode_axis_operational_status(
            "EL",
            endstop=payload.get("endstop_el"),
            motor_alarm=payload.get("motor_alarm_el"),
            modbus_status=payload.get("modbus_status_el", payload.get("modbus_el")),
            **common,
        )
        az_index = decode_axis_index_state(
            payload.get("index_az"),
            referenced_latched=bool(getattr(self, "az_reference_latched", False)),
            status_is_fresh=az_status.is_fresh,
        )
        el_index = decode_axis_index_state(
            payload.get("index_el"),
            referenced_latched=bool(getattr(self, "el_reference_latched", False)),
            status_is_fresh=el_status.is_fresh,
        )
        permission = evaluate_tracking_permission(
            self.selected_antenna_mode(),
            az_index=az_index,
            el_index=el_index,
            az_status=az_status,
            el_status=el_status,
        )
        return az_index, el_index, az_status, el_status, permission

    def tracking_permission(self) -> TrackingPermission:
        """Central business check used by every automatic tracking start path."""
        return self._axis_safety_state()[4]

    def validate_tracking_start(self, *, show_message: bool = True) -> bool:
        permission = self.tracking_permission()
        if permission.allowed:
            return True
        message = permission.message()
        self.status_bar.showMessage(message.replace("\n", " "), 6000)
        if show_message:
            QMessageBox.information(self, "Tracking", message)
        return False

    def _refresh_axis_operational_panel(self, data: dict | None = None):
        if isinstance(data, dict) and data:
            self._latest_antenna_status_payload = dict(data)
        az_index, el_index, az_status, el_status, permission = self._axis_safety_state(data)

        for index_state, widget_name in (
            (az_index, "label_antenna_index_az"),
            (el_index, "label_antenna_index_el"),
        ):
            if index_state == AxisIndexState.UNKNOWN:
                widget = getattr(self, widget_name, None)
                if widget is not None:
                    widget.setStyleSheet(lightgrey_label_color)
                    widget.setAccessibleDescription(AxisIndexState.UNKNOWN.value)
                    widget.setToolTip("Axis index: UNKNOWN\nNo valid fresh status received.")

        for status, widget_name in (
            (az_status, "label_antenna_status_az"),
            (el_status, "label_antenna_status_el"),
        ):
            widget = getattr(self, widget_name, None)
            if widget is not None:
                widget.setStyleSheet(_axis_operational_style(status.state))
                widget.setToolTip(format_axis_operational_tooltip(status))
                widget.setAccessibleDescription(status.state.value)
            signature = (status.state.value, status.active_flags)
            previous = self._axis_operational_signatures.get(status.axis)
            if previous is not None and signature != previous:
                if status.state == AxisOperationalState.ALARM:
                    detail = status.primary_detail or "unknown fault"
                    self.status_bar.showMessage(f"{status.axis} axis alarm: {detail}", 6000)
                elif previous[0] == AxisOperationalState.ALARM.value and status.state == AxisOperationalState.OK:
                    self.status_bar.showMessage(f"{status.axis} axis status returned to normal.", 5000)
            self._axis_operational_signatures[status.axis] = signature

        self._refresh_tracking_permission_ui(permission)
        self._handle_active_tracking_permission(permission)

    def _refresh_tracking_permission_ui(self, permission: TrackingPermission | None = None):
        current_permission = permission or self.tracking_permission()
        client = getattr(self, "axis_client", None)
        setter = getattr(client, "set_tracking_permission_state", None)
        if callable(setter):
            setter(current_permission.allowed, current_permission.reasons)
        button = getattr(self, "pushButton_antenna_track", None)
        if button is None:
            return
        tracker_running = bool(getattr(self, "tracker", None) and self.tracker.is_running())
        positioner_running = bool(getattr(self, "positioner", None) and self.positioner.is_running())
        can_stop_motion = tracker_running or positioner_running
        button.setEnabled(bool(self.has_connection() and (can_stop_motion or current_permission.allowed)))
        button.setToolTip("" if current_permission.allowed or can_stop_motion else current_permission.message())

    def _handle_active_tracking_permission(self, permission: TrackingPermission):
        tracker_running = bool(getattr(self, "tracker", None) and self.tracker.is_running())
        if permission.allowed:
            self._last_tracking_inhibit_signature = None
            return
        if not tracker_running:
            return
        signature = permission.reasons
        if signature == self._last_tracking_inhibit_signature:
            return
        self._last_tracking_inhibit_signature = signature
        self._auto_restart_tracking = False
        message = "Tracking stopped: " + "; ".join(permission.reasons)
        self.logger.warning(message)
        self._stop_tracking_loop_from_ui()
        self.status_bar.showMessage(message, 8000)

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
        """Start the selected antenna backend connection in a background thread."""
        if not self.connection_ready:
            self.logger.info(
                "Demarrage de la connexion antenne (%s) depuis un thread separe",
                self.selected_antenna_mode(),
            )
            self._user_requested_disconnect = False
            self._reset_reference_latches()

            worker = self.thread_manager.start_thread(
                "AntennaConnection",
                self.connect_antenna_controller,
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
                self.thread_manager.stop_asyncio_loop("AntennaCoreLoop")
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
            self.status_bar.showMessage("Antenna disconnected")
        except Exception as exc:
            self.logger.error(f"Erreur de deconnexion: {exc}")
        finally:
            try:
                self.pushButton_server_connect.setEnabled(True)
            except Exception:
                pass
            self.logger.info("[UI] request_disconnect: end")

    def connect_antenna_controller(self):
        """Function executed in a background thread to connect to the selected backend."""
        try:
            mode = self.selected_antenna_mode()
            self.logger.info("Tentative de connexion antenne: mode=%s", mode)
            axis_client = AntennaControllerQt.from_settings(
                self.settings,
                self.thread_manager,
                mode=mode,
            )
            connected = axis_client.connect()

            if connected:
                self.logger.info("Connexion antenne etablie: mode=%s backend=%s", mode, axis_client.backend_name)
                return axis_client
            raise ConnectionError(f"Unable to connect antenna backend in mode '{mode}'")

        except Exception as exc:
            self.logger.error(f"Erreur de connexion antenne: {exc}")
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
        self.status_bar.showMessage(f"Connected to {self.axis_client.backend_name}")
        self._refresh_connection_panel()

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
        self._refresh_reference_status_panel(self.axis_client.get_antenna_telemetry())

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
                self.pushButton_antenna_track.setText("Track")
                self._refresh_tracking_permission_ui()

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
            f"Impossible de se connecter a l'antenne: {error_message}",
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
            self._refresh_connection_panel()
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
                "label_antenna_index_az",
                "label_antenna_index_el",
                "label_antenna_status_az",
                "label_antenna_status_el",
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
            pos_interval = 0.2
            status_interval = 1.0
            if getattr(self, "axis_client", None) is not None:
                try:
                    pos_interval, status_interval = getattr(self.axis_client, "polling_intervals", (0.2, 1.0))
                except Exception:
                    pass
            self.axis_polling = AxisClientPollingAdapter(self.axis_client, self.thread_manager)
            self.axis_polling.start(pos_interval=pos_interval, status_interval=status_interval)
        except Exception as exc:
            try:
                self.logger.error(f"Erreur start_polling: {exc}")
            except Exception:
                pass

    def ui_set_default_state(self):
        """Apply the default disconnected UI state."""
        try:
            self._reset_reference_latches()
            if hasattr(self, "label_axisapp_version"):
                self.label_axisapp_version.setText("")
            if hasattr(self, "label_antenna_endpoint_summary"):
                self.label_antenna_endpoint_summary.setStyleSheet(standard_label_color)
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
            self._refresh_connection_panel()
            self._refresh_reference_status_panel()

            if hasattr(self, "pushButton_antenna_track"):
                self.pushButton_antenna_track.setEnabled(False)
                self.pushButton_antenna_track.setText("Track")
                self.pushButton_antenna_track.setToolTip("Tracking unavailable: antenna status unknown.")

        except Exception as exc:
            self.logger.error(f"Erreur ui_set_default_state: {exc}")

    def ui_display_versions(self, versions: dict):
        """Update server/driver version labels when connected."""
        try:
            if not isinstance(versions, dict):
                return
            if hasattr(self, "label_axisapp_version"):
                version_text = str(versions.get("server_version") or getattr(self.axis_client, "backend_name", "") or "")
                self.label_axisapp_version.setText(version_text)
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
            if not self._accept_antenna_telemetry_payload(data):
                return
            az = data.get("az")
            el = data.get("el")
            # self.label_antenna_az_deg.setText(f"{az:.2f}°" if isinstance(az, (int, float)) else "---.--°")
            self.g1.set_angle(az)
            # self.label_antenna_el_deg.setText(f"{el:.2f}°" if isinstance(el, (int, float)) else "---.--°")
            self.g2.set_angle(el)

            az_rate = data.get("az_rate")
            el_rate = data.get("el_rate")
            antenna_settings = self.settings.get("ANTENNA", self.settings.get("antenna", {})) if isinstance(self.settings, dict) else {}
            rate_decimals = max(0, min(5, int(antenna_settings.get("rate_display_decimals", 3))))
            self.label_antenna_az_rate.setText(
                f"{az_rate:.{rate_decimals}f} °/s" if isinstance(az_rate, (int, float)) else f"{0.0:.{rate_decimals}f} °/s"
            )
            self.label_antenna_el_rate.setText(
                f"{el_rate:.{rate_decimals}f} °/s" if isinstance(el_rate, (int, float)) else f"{0.0:.{rate_decimals}f} °/s"
            )

            self.label_antenna_az_setrate.setText(f"{data.get('az_setrate'):.0f}")
            self.label_antenna_el_setrate.setText(f"{data.get('el_setrate'):.0f}")

            end_az = data.get("endstop_az")
            end_el = data.get("endstop_el")
            self.label_antenna_endstop_az.setText(str(end_az) if end_az is not None else "-")
            self.label_antenna_endstop_el.setText(str(end_el) if end_el is not None else "-")
            self._refresh_reference_status_panel(data)

            try:
                if isinstance(az, (int, float)):
                    self._last_tel_az = az
                if isinstance(el, (int, float)):
                    self._last_tel_el = el
            except Exception:
                pass

            try:
                status = (
                    getattr(self.axis_client, "axis_status", None)
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

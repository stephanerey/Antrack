"""Main application window composition root."""

from __future__ import annotations

import logging
from pathlib import Path

from PyQt5.uic import loadUi
from PyQt5.QtWidgets import QMainWindow, QMessageBox, QStatusBar

from antrack.gui.calibration_ui import CalibrationUiMixin
from antrack.gui.connection_ui import ConnectionUiMixin
from antrack.gui.diagnostics_ui import DiagnosticsUiMixin
from antrack.gui.instrument_ui import InstrumentUiMixin
from antrack.gui.instruments.sdr_ui import SdrUiMixin
from antrack.gui.scan_ui import ScanUiMixin
from antrack.gui.time_ui import TimeUiMixin
from antrack.gui.tracking_ui import TrackingUiMixin
from antrack.gui.ephemeris_qt import EphemerisQtAdapter
from antrack.gui.widgets.angle_gauge_widget import AngleGauge
from antrack.gui.widgets.multi_track_card import MultiTrackTabsManager
from antrack.tracking.ephemeris_service import EphemerisService, load_planets
from antrack.tracking.motion_constraints import parse_forbidden_ranges
from antrack.tracking.observer import Observer
from antrack.tracking.tracking import TrackedObject
from antrack.utils.paths import get_tle_dir


class MainUi(
    QMainWindow,
    DiagnosticsUiMixin,
    TrackingUiMixin,
    CalibrationUiMixin,
    InstrumentUiMixin,
    SdrUiMixin,
    ScanUiMixin,
    TimeUiMixin,
    ConnectionUiMixin,
):
    def __init__(self, thread_manager, settings, ip_address, port, parent=None):
        super().__init__(parent)
        ui_path = Path(__file__).with_name("main_3.ui")
        loadUi(str(ui_path), self)
        self.setWindowTitle("Antenna Tracker")

        self.settings = settings
        self.thread_manager = thread_manager
        self.logger = logging.getLogger("MainUi")
        self.task_manager = self.thread_manager
        self.setup_menu()

        self.ip_address = ip_address
        self.port = port
        self.connection_ready = False
        self.axis_client = None
        self.axis_polling = None
        self.instrument = None
        self.calib_plots = None

        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage("Pret")

        antenna_settings = {}
        if isinstance(self.settings, dict):
            antenna_settings = self.settings.get("ANTENNA", self.settings.get("antenna", {})) or {}
        az_forbidden = parse_forbidden_ranges(
            antenna_settings.get("az_forbidden_ranges"),
            default=[(45.0, 90.0), (270.0, 300.0)],
        )
        el_forbidden = parse_forbidden_ranges(
            antenna_settings.get("el_forbidden_ranges"),
            default=[(-10.0, 0.0), (95.0, 100.0)],
        )

        self.g1 = AngleGauge(
            span_angle=360,
            forbidden_ranges=az_forbidden,
            decimals=2,
            origin_screen_deg=90,
            clockwise=True,
            show_cardinal_labels=True,
            major_anchor_deg=0,
            gradient_angle_deg=315,
            set_value_y_ratio=-0.52,
            actual_value_y_ratio=0.00,
            error_value_y_ratio=0.52,
            set_label_y_ratio=-0.75,
            actual_label_y_ratio=-0.25,
            error_label_y_ratio=0.32,
        )
        self.g2 = AngleGauge(
            start_angle_deg=-10,
            span_angle=110,
            minor_step_deg=10,
            major_step_deg=30,
            forbidden_ranges=el_forbidden,
            decimals=2,
            origin_screen_deg=0,
            clockwise=False,
            show_cardinal_labels=True,
            major_anchor_deg=0,
            gradient_angle_deg=315,
            set_value_y_ratio=-0.55,
            actual_value_y_ratio=0.00,
            error_value_y_ratio=0.50,
            set_label_y_ratio=-0.75,
            actual_label_y_ratio=-0.25,
            error_label_y_ratio=0.30,
        )
        self.verticalLayout_gauges.layout().addWidget(self.g1)
        self.verticalLayout_gauges.layout().addWidget(self.g2)

        self.pushButton_server_connect.clicked.connect(self.on_connect_button_clicked)
        self.pushButton_antenna_track.clicked.connect(self.on_track_button_clicked)
        if hasattr(self, "pushButton_antenna_park"):
            self.pushButton_antenna_park.clicked.connect(self.on_park_button_clicked)
        self.pushButton_antenna_track.setEnabled(False)

        self.ui_set_default_state()
        self._user_requested_disconnect = False
        self._connect_toggle_in_progress = False
        self.setup_time_ui()

        self.tracked_object = TrackedObject()
        self.tracker = None
        self.positioner = None
        self.telemetry_ready = False
        self._auto_restart_tracking = False
        self._last_tel_az = None
        self._last_tel_el = None

        try:
            self.setup_tracker_tab()
            self.setup_manual_antenna_controls()
            self._setup_calibration_tab()
        except Exception as exc:
            self.logger.error(f"setup_tracker_tab ou calibration a echoue: {exc}")

        self.logger.info("Interface principale initialisee")

        try:
            self.planets = load_planets(logger=self.logger)
            earth = self.planets["earth"]
            self.logger.info("Ephemerides chargees")
        except Exception as exc:
            self.planets = None
            earth = None
            self.logger.exception("Impossible de charger les ephemerides: %s", exc)
            QMessageBox.warning(
                self,
                "Ephemeris",
                f"Ephemeris loading failed:\n{exc}",
            )

        try:
            self.tle_dir = get_tle_dir()
            self.tle_dir.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            self.tle_dir = None
            self.logger.error(f"Impossible d'initialiser le dossier TLE: {exc}")

        try:
            obs_cfg = self.settings.get("OBSERVER", {}) if isinstance(self.settings, dict) else {}
            name = obs_cfg.get("name")
            latitude = obs_cfg.get("latitude")
            longitude = obs_cfg.get("longitude")
            altitude = obs_cfg.get("altitude")
            self.observer = Observer()
            self.observer.create_observer(name, longitude, latitude, altitude, earth)
            self.logger.info(
                f"Observateur initialise: {getattr(self.observer, 'name', '')} "
                f"({latitude}, {longitude}, {altitude} m)"
            )
        except Exception as exc:
            self.observer = None
            self.logger.error(f"Impossible d'initialiser l'observateur: {exc}")

        tle_groups = None
        try:
            tle_groups = self.settings.get("TLE_GROUPS", None) if isinstance(self.settings, dict) else None
        except Exception:
            pass

        base_ephem = EphemerisService(
            self.thread_manager,
            self.observer,
            self.planets,
            logger=self.logger,
            tle_dir=self.tle_dir,
            tle_groups=tle_groups,
            tle_refresh_hours=6.0,
        )
        self.ephem = EphemerisQtAdapter(base_ephem)
        self.ephem.pose_updated.connect(self._on_pose_updated)

        self.multi_cards = MultiTrackTabsManager(
            ephem=self.ephem,
            on_pick=self._on_multi_pick,
            tab_widget=self.tabWidget_3,
            time_formatter=self.format_event_time_for_ui,
            time_tooltip_formatter=self.format_event_tooltip_for_ui,
        )

        for body in ["Sun", "Moon", "Mercury", "Venus", "Mars", "Jupiter", "Saturn", "Uranus", "Neptune"]:
            self.multi_cards.add_target(tab="Solar System", obj_type="Solar System", name=body)

        for sat in ["ISS", "NOAA 20", "NOAA 21", "CUBESAT", "ES'HAIL 2"]:
            self.multi_cards.add_target(
                tab="Artificial Satellites",
                obj_type="Artificial Satellite",
                name=sat,
            )

        for celestial in ["Sirius", "Polaris", "Betelgeuse"]:
            self.multi_cards.add_target(tab="Celestial Objects", obj_type="Star", name=celestial)

        self.setup_instrument_ui()
        self.setup_sdr_ui()
        self.setup_scan_ui()

    def closeEvent(self, event):
        try:
            self.stop_polling_threads()
        except Exception:
            pass
        try:
            if hasattr(self, "multi_cards"):
                self.multi_cards.stop_all()
        except Exception:
            pass
        try:
            self.close_instrument_ui()
        except Exception:
            pass
        try:
            self.close_sdr_ui()
        except Exception:
            pass
        try:
            self.close_scan_ui()
        except Exception:
            pass
        try:
            if getattr(self, "thread_manager", None):
                self.thread_manager.shutdown(graceful=True, timeout_s=2.0)
        except Exception:
            pass
        super().closeEvent(event)

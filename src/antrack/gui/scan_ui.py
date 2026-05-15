"""Scan tab wiring for Antrack."""

from __future__ import annotations

import csv
import math
import threading
import time

import numpy as np
import pyqtgraph as pg
from PyQt5.QtCore import QObject, Qt, QTimer, pyqtSignal
from PyQt5.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QFileDialog,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSplitter,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from antrack.core.dsp.snr import compute_snr
from antrack.gui.widgets.heatmap_widget import HeatmapWidget
from antrack.tracking.motion_constraints import (
    constrained_azimuth_error,
    constrained_elevation_error,
    parse_forbidden_ranges,
)
from antrack.tracking.scan_cross import generate_cross_points
from antrack.tracking.scan_grid import generate_grid_points, generate_two_pass_grid_points
from antrack.tracking.scan_session import ScanSession
from antrack.tracking.scan_spiral import generate_spiral_points


class _ScanMoveBridge(QObject):
    move_requested = pyqtSignal(object)


class ScanUiMixin:
    """Populate and manage the Scan tab."""

    def setup_scan_ui(self) -> None:
        if getattr(self, "_scan_ui_ready", False):
            return
        container = getattr(self, "tab_10", None)
        if container is None:
            return
        self._scan_ui_ready = True
        root_layout = container.layout()
        if root_layout is None:
            root_layout = QVBoxLayout(container)
        self._clear_layout(root_layout)

        config_group = QGroupBox("Scan Configuration", container)
        config_layout = QGridLayout(config_group)
        self.scan_strategy_combo = QComboBox(config_group)
        self.scan_strategy_combo.addItem("grid", "grid")
        self.scan_strategy_combo.addItem("cross (experimental)", "cross")
        self.scan_strategy_combo.addItem("spiral (experimental)", "spiral")
        self.scan_strategy_combo.addItem("adaptive (experimental)", "adaptive")
        self.scan_span_spin = QDoubleSpinBox(config_group)
        self.scan_span_spin.setRange(0.1, 20.0)
        self.scan_span_spin.setValue(2.0)
        self.scan_span_spin.setSuffix(" deg")
        self.scan_step_spin = QDoubleSpinBox(config_group)
        self.scan_step_spin.setRange(0.01, 5.0)
        self.scan_step_spin.setValue(0.5)
        self.scan_step_spin.setSuffix(" deg")
        self.scan_settle_spin = QDoubleSpinBox(config_group)
        self.scan_settle_spin.setRange(0.0, 10.0)
        self.scan_settle_spin.setValue(0.2)
        self.scan_settle_spin.setSuffix(" s")
        self.scan_metric_combo = QComboBox(config_group)
        self.scan_metric_combo.addItem("band_power", "band_power")
        self.scan_metric_combo.addItem("snr_relative (experimental)", "snr_relative")
        self.scan_metric_combo.addItem("snr_absolute (experimental)", "snr_absolute")
        self.scan_center_mode_combo = QComboBox(config_group)
        self.scan_center_mode_combo.addItem("tracking_relative", "tracking_relative")
        self.scan_center_mode_combo.addItem("current_position", "current_position")
        self.scan_peak_estimator_combo = QComboBox(config_group)
        self.scan_peak_estimator_combo.addItem("best_sample", "best_sample")
        self.scan_peak_estimator_combo.addItem("four_point_divergence (experimental)", "four_point_divergence")
        self.scan_strategy_info_button = QToolButton(config_group)
        self.scan_strategy_info_button.setText("i")
        self.scan_strategy_info_button.setFixedSize(18, 18)
        self.scan_strategy_info_button.setAutoRaise(True)
        self.scan_strategy_info_button.setToolTip(self._scan_strategy_info_tooltip())
        self.scan_strategy_info_button.setToolTipDuration(30000)

        self.scan_start_button = QPushButton("Start", config_group)
        self.scan_pause_button = QPushButton("Pause", config_group)
        self.scan_resume_button = QPushButton("Resume", config_group)
        self.scan_stop_button = QPushButton("Stop", config_group)
        self.scan_apply_button = QPushButton("Apply Offset", config_group)
        self.scan_save_button = QPushButton("Save Offset", config_group)
        self.scan_export_error_csv_button = QPushButton("Export Error CSV", config_group)
        self.scan_repeat_checkbox = QCheckBox("Repeat While Tracking", config_group)
        self.scan_repeat_interval_spin = QDoubleSpinBox(config_group)
        self.scan_repeat_interval_spin.setRange(1.0, 3600.0)
        self.scan_repeat_interval_spin.setValue(60.0)
        self.scan_repeat_interval_spin.setSuffix(" s")
        self.scan_repeat_checkbox.setChecked(True)
        self.scan_progress_label = QLabel("-", config_group)
        self.scan_best_label = QLabel("-", config_group)
        self.scan_offset_label = QLabel("-", config_group)
        self.scan_orbit_scan_label = QLabel("-", config_group)
        self.scan_next_wait_label = QLabel("-", config_group)
        self.scan_next_wait_bar = QProgressBar(config_group)
        self.scan_next_wait_bar.setRange(0, 100)
        self.scan_next_wait_bar.setValue(0)
        self.scan_next_wait_bar.setTextVisible(False)
        repeat_row = QHBoxLayout()
        repeat_row.setContentsMargins(0, 0, 0, 0)
        repeat_row.addWidget(self.scan_repeat_checkbox)
        repeat_row.addWidget(self.scan_repeat_interval_spin)
        strategy_label_row = QHBoxLayout()
        strategy_label_row.setContentsMargins(0, 0, 0, 0)
        strategy_label_row.setSpacing(4)
        strategy_label_row.addWidget(QLabel("Strategy"))
        strategy_label_row.addWidget(self.scan_strategy_info_button)
        strategy_label_row.addStretch(1)

        config_layout.addLayout(strategy_label_row, 0, 0)
        config_layout.addWidget(self.scan_strategy_combo, 0, 1)
        config_layout.addWidget(QLabel("Center Mode"), 0, 2)
        config_layout.addWidget(self.scan_center_mode_combo, 0, 3)
        config_layout.addWidget(self.scan_start_button, 0, 4)
        config_layout.addWidget(self.scan_stop_button, 0, 5)
        config_layout.addWidget(QLabel("Span (deg)"), 1, 0)
        config_layout.addWidget(self.scan_span_spin, 1, 1)
        config_layout.addWidget(QLabel("Step (deg)"), 1, 2)
        config_layout.addWidget(self.scan_step_spin, 1, 3)
        config_layout.addWidget(self.scan_pause_button, 1, 4)
        config_layout.addWidget(self.scan_resume_button, 1, 5)
        config_layout.addWidget(QLabel("Settle (s)"), 2, 0)
        config_layout.addWidget(self.scan_settle_spin, 2, 1)
        config_layout.addWidget(QLabel("Repeat"), 2, 2)
        config_layout.addLayout(repeat_row, 2, 3)
        config_layout.addWidget(self.scan_apply_button, 2, 4)
        config_layout.addWidget(self.scan_save_button, 2, 5)
        config_layout.addWidget(QLabel("Metric"), 3, 0)
        config_layout.addWidget(self.scan_metric_combo, 3, 1)
        config_layout.addWidget(QLabel("Peak Estimator"), 3, 2)
        config_layout.addWidget(self.scan_peak_estimator_combo, 3, 3)
        config_layout.addWidget(self.scan_export_error_csv_button, 3, 4, 1, 2)
        config_layout.addWidget(QLabel("Progress"), 4, 0)
        config_layout.addWidget(self.scan_progress_label, 4, 1)
        config_layout.addWidget(QLabel("Orbit Scan"), 4, 2)
        config_layout.addWidget(self.scan_orbit_scan_label, 4, 3)
        config_layout.addWidget(QLabel("Next"), 4, 4)
        config_layout.addWidget(self.scan_next_wait_label, 4, 5)
        config_layout.addWidget(QLabel("Best"), 5, 0)
        config_layout.addWidget(self.scan_best_label, 5, 1)
        config_layout.addWidget(QLabel("Offset"), 5, 2)
        config_layout.addWidget(self.scan_offset_label, 5, 3)
        config_layout.addWidget(self.scan_next_wait_bar, 5, 4, 1, 2)
        config_layout.setColumnStretch(1, 1)
        config_layout.setColumnStretch(3, 1)

        splitter = QSplitter(Qt.Vertical, container)
        visual_panel = QWidget(splitter)
        visual_layout = QGridLayout(visual_panel)
        visual_layout.setContentsMargins(0, 0, 0, 0)
        visual_layout.setHorizontalSpacing(6)
        visual_layout.setVerticalSpacing(6)
        self.scan_vertical_plot = pg.PlotWidget(visual_panel)
        self.scan_vertical_plot.addLegend()
        self.scan_vertical_plot.showGrid(x=True, y=True)
        self.scan_vertical_plot.setMaximumWidth(220)
        self.scan_vertical_plot.setLabel("bottom", "Metric")
        self.scan_vertical_plot.setLabel("left", "Elevation", units="deg")
        self.scan_vertical_curve = self.scan_vertical_plot.plot(name="Elevation", pen=pg.mkPen("c"))
        self.scan_vertical_theoretical_marker = pg.ScatterPlotItem(
            name="Theoretical",
            symbol="t",
            size=11,
            brush=pg.mkBrush(255, 220, 120, 220),
            pen=pg.mkPen("k"),
        )
        self.scan_vertical_real_marker = pg.ScatterPlotItem(
            name="Measured Peak",
            symbol="o",
            size=11,
            brush=pg.mkBrush(255, 255, 255, 220),
            pen=pg.mkPen("k"),
        )
        self.scan_vertical_plot.addItem(self.scan_vertical_theoretical_marker)
        self.scan_vertical_plot.addItem(self.scan_vertical_real_marker)

        self.scan_heatmap_widget = HeatmapWidget(visual_panel)

        self.scan_horizontal_plot = pg.PlotWidget(visual_panel)
        self.scan_horizontal_plot.addLegend()
        self.scan_horizontal_plot.showGrid(x=True, y=True)
        self.scan_horizontal_plot.setLabel("bottom", "Azimuth", units="deg")
        self.scan_horizontal_plot.setLabel("left", "Metric")
        self.scan_horizontal_curve = self.scan_horizontal_plot.plot(name="Azimuth", pen=pg.mkPen("y"))
        self.scan_horizontal_theoretical_marker = pg.ScatterPlotItem(
            name="Theoretical",
            symbol="t",
            size=11,
            brush=pg.mkBrush(255, 220, 120, 220),
            pen=pg.mkPen("k"),
        )
        self.scan_horizontal_real_marker = pg.ScatterPlotItem(
            name="Measured Peak",
            symbol="o",
            size=11,
            brush=pg.mkBrush(255, 255, 255, 220),
            pen=pg.mkPen("k"),
        )
        self.scan_horizontal_plot.addItem(self.scan_horizontal_theoretical_marker)
        self.scan_horizontal_plot.addItem(self.scan_horizontal_real_marker)

        visual_layout.addWidget(self.scan_vertical_plot, 0, 0)
        visual_layout.addWidget(self.scan_heatmap_widget, 0, 1)
        visual_layout.addWidget(self.scan_horizontal_plot, 1, 1)
        visual_layout.setColumnStretch(1, 1)
        visual_layout.setRowStretch(0, 1)
        self._sync_scan_profile_margins()

        history_panel = QWidget(splitter)
        history_layout = QVBoxLayout(history_panel)
        history_layout.setContentsMargins(0, 0, 0, 0)
        history_layout.setSpacing(6)
        self.scan_az_history_plot = pg.PlotWidget(history_panel)
        self.scan_az_history_plot.addLegend()
        self.scan_az_history_plot.showGrid(x=True, y=True)
        self.scan_az_history_plot.setLabel("bottom", "Theoretical Azimuth", units="deg")
        self.scan_az_history_plot.setLabel("left", "AZ Error", units="deg")
        self.scan_az_history_plot.setXRange(0.0, 360.0, padding=0.0)
        self.scan_az_zero_line = pg.InfiniteLine(pos=0.0, angle=0, pen=pg.mkPen((160, 160, 160), style=Qt.DashLine))
        self.scan_az_history_plot.addItem(self.scan_az_zero_line)
        self.scan_az_error_curve = self.scan_az_history_plot.plot(
            name="AZ error",
            pen=pg.mkPen("y", width=2),
            symbol="o",
            symbolSize=6,
            symbolBrush=pg.mkBrush("y"),
        )
        self.scan_el_history_plot = pg.PlotWidget(history_panel)
        self.scan_el_history_plot.addLegend()
        self.scan_el_history_plot.showGrid(x=True, y=True)
        self.scan_el_history_plot.setLabel("bottom", "Theoretical Elevation", units="deg")
        self.scan_el_history_plot.setLabel("left", "EL Error", units="deg")
        self.scan_el_history_plot.setXRange(0.0, 90.0, padding=0.0)
        self.scan_el_zero_line = pg.InfiniteLine(pos=0.0, angle=0, pen=pg.mkPen((160, 160, 160), style=Qt.DashLine))
        self.scan_el_history_plot.addItem(self.scan_el_zero_line)
        self.scan_el_error_curve = self.scan_el_history_plot.plot(
            name="EL error",
            pen=pg.mkPen("c", width=2),
            symbol="o",
            symbolSize=6,
            symbolBrush=pg.mkBrush("c"),
        )
        history_layout.addWidget(self.scan_az_history_plot)
        history_layout.addWidget(self.scan_el_history_plot)
        splitter.addWidget(visual_panel)
        splitter.addWidget(history_panel)
        splitter.setSizes([700, 260])

        root_layout.addWidget(config_group)
        root_layout.addWidget(splitter, 1)

        self.scan_session = ScanSession(
            thread_manager=self.thread_manager,
            move_to=self._scan_move_to,
            measure=self._scan_measure,
            wait_for_settle=self._scan_wait_for_settle,
            center_provider=self._scan_current_theoretical_center,
            telemetry_provider=self._scan_telemetry_snapshot,
            logger=self.logger.getChild("Scan"),
        )
        self._scan_move_bridge = _ScanMoveBridge(self)
        self._scan_move_bridge.move_requested.connect(self._scan_apply_move_request)
        self.scan_session.progress_updated.connect(self._on_scan_progress_updated)
        self.scan_session.point_measured.connect(self._on_scan_point_measured)
        self.scan_session.completed.connect(self._on_scan_completed)
        self.scan_session.error.connect(self._on_scan_error)
        self.scan_session.state_changed.connect(self._on_scan_state_changed)

        self._scan_samples = []
        self._scan_current_result = None
        self._scan_plan_points = []
        self._scan_current_point = None
        self._scan_current_stage = "idle"
        self._scan_progress_current = 0
        self._scan_progress_total = 0
        self._scan_error_history = []
        self._scan_repeat_active = False
        self._scan_repeat_pending = False
        self._scan_orbit_scan_count = 0
        self._scan_repeat_interval_s = 0.0
        self._scan_repeat_due_monotonic = 0.0
        self._scan_repeat_countdown_timer = QTimer(container)
        self._scan_repeat_countdown_timer.setInterval(500)
        self._scan_repeat_countdown_timer.timeout.connect(self._update_scan_repeat_countdown)
        self.scan_start_button.clicked.connect(self.start_scan_session)
        self.scan_pause_button.clicked.connect(self.scan_session.pause)
        self.scan_resume_button.clicked.connect(self.scan_session.resume)
        self.scan_stop_button.clicked.connect(self._stop_scan_session)
        self.scan_apply_button.clicked.connect(lambda: self._apply_scan_offset(False))
        self.scan_save_button.clicked.connect(lambda: self._apply_scan_offset(True))
        self.scan_export_error_csv_button.clicked.connect(self._export_scan_error_csv)
        self.scan_span_spin.valueChanged.connect(lambda *_args: self._update_scan_error_plot(self._scan_error_history))

    def _build_scan_config(self) -> dict:
        center_mode = self._scan_combo_value(self.scan_center_mode_combo, "tracking_relative")
        center_az, center_el = (0.0, 0.0) if center_mode == "tracking_relative" else self._scan_current_antenna_center()
        sdr_settings = self._scan_current_sdr_measure_settings()
        return {
            "strategy": self._scan_combo_value(self.scan_strategy_combo, "grid"),
            "center_mode": center_mode,
            "center_az_deg": center_az,
            "center_el_deg": center_el,
            "span_deg": self.scan_span_spin.value(),
            "span_az_deg": self.scan_span_spin.value(),
            "span_el_deg": self.scan_span_spin.value(),
            "step_deg": self.scan_step_spin.value(),
            "radial_step_deg": self.scan_step_spin.value(),
            "settle_s": self.scan_settle_spin.value(),
            "integration_s": sdr_settings["integration_s"],
            "bandwidth_hz": sdr_settings["bandwidth_hz"],
            "band_offset_hz": sdr_settings["band_offset_hz"],
            "metric": self._scan_combo_value(self.scan_metric_combo, "band_power"),
            "peak_estimator": self._scan_combo_value(self.scan_peak_estimator_combo, "best_sample"),
            "coarse_span_deg": self.scan_span_spin.value(),
            "coarse_step_deg": self.scan_step_spin.value(),
            "fine_span_deg": max(0.2, self.scan_span_spin.value() / 4.0),
            "fine_step_deg": max(0.05, self.scan_step_spin.value() / 4.0),
            "grid_step_deg": max(0.05, self.scan_step_spin.value()),
        }

    @staticmethod
    def _scan_combo_value(combo: QComboBox, default: str) -> str:
        value = combo.currentData()
        if isinstance(value, str) and value:
            return value
        text = combo.currentText().strip()
        if not text:
            return default
        return text.split(" ", 1)[0].strip() or default

    @staticmethod
    def _scan_strategy_info_tooltip() -> str:
        return """
        <div style="min-width: 520px;">
          <b>Scan strategies</b>
          <p><b>grid</b> - Stable/recommended. Measures every point on a regular AZ/EL offset grid.</p>
          <pre style="font-family: Consolas, monospace;">
EL +1  o--o--o--o--o
   +0.5 o--o--o--o--o
    0   o--o--o--o--o
   -0.5 o--o--o--o--o
   -1   o--o--o--o--o
        -1 -.5 0 +.5 +1 AZ
          </pre>
          <p><b>cross (experimental)</b> - Measures one horizontal and one vertical cut through the center.
          Faster, but can miss a peak that is off the two axes.</p>
          <pre style="font-family: Consolas, monospace;">
          o
          o
    o--o--X--o--o
          o
          o
          </pre>
          <p><b>spiral (experimental)</b> - Starts near the center and expands outward.
          Useful for quick searches, but the heatmap projection is less direct.</p>
          <pre style="font-family: Consolas, monospace;">
        o-o-o
        |   |
        o X-o
        |
        o-o-o
          </pre>
          <p><b>adaptive (experimental)</b> - Coarse grid first, then a finer grid around the best coarse point.
          Intended to reduce scan time, still under validation.</p>
          <pre style="font-family: Consolas, monospace;">
    coarse:  o-----o-----o
             |     |     |
             o-----*-----o
             |   fine    |
             o--o--o--o--o
          </pre>
        </div>
        """

    def _scan_current_antenna_center(self) -> tuple[float, float]:
        antenna_state = getattr(getattr(self, "axis_client", None), "antenna", None)
        az = getattr(antenna_state, "az", None)
        el = getattr(antenna_state, "el", None)
        if isinstance(az, (int, float)) and isinstance(el, (int, float)):
            return float(az), float(el)
        tracked_object = getattr(self, "tracked_object", None)
        az = getattr(tracked_object, "az_set", 0.0)
        el = getattr(tracked_object, "el_set", 0.0)
        return float(az) if isinstance(az, (int, float)) else 0.0, float(el) if isinstance(el, (int, float)) else 0.0

    def _scan_current_sdr_measure_settings(self) -> dict[str, float]:
        sdr_client = getattr(self, "sdr_client", None)
        center_freq_hz = float(getattr(sdr_client, "center_freq", 0.0)) if sdr_client is not None else 0.0
        receiver_freq_hz = float(getattr(sdr_client, "receiver_freq_hz", center_freq_hz)) if sdr_client is not None else center_freq_hz
        bandwidth_hz = float(max(100.0, getattr(sdr_client, "bandwidth_hz", 25_000.0))) if sdr_client is not None else 25_000.0
        integration_s = float(max(0.01, getattr(self, "_sdr_snr_integration_s", 0.25)))
        return {
            "band_offset_hz": receiver_freq_hz - center_freq_hz,
            "bandwidth_hz": bandwidth_hz,
            "integration_s": integration_s,
        }

    def _scan_current_theoretical_center(self) -> tuple[float, float]:
        tracked_object = getattr(self, "tracked_object", None)
        az = getattr(tracked_object, "az_theoretical_deg", None)
        el = getattr(tracked_object, "el_theoretical_deg", None)
        if not isinstance(az, (int, float)):
            az = getattr(tracked_object, "az_set", self._scan_current_antenna_center()[0])
        if not isinstance(el, (int, float)):
            el = getattr(tracked_object, "el_set", self._scan_current_antenna_center()[1])
        return float(az), float(el)

    def start_scan_session(self) -> None:
        self._start_scan_sequence(repeating=False)

    def _start_scan_sequence(self, *, repeating: bool) -> None:
        config = self._build_scan_config()
        if not self._prepare_scan_session(config):
            self._scan_repeat_pending = False
            self._cancel_scan_repeat_countdown()
            return
        if not repeating:
            self._scan_error_history = []
            self._scan_repeat_active = bool(self.scan_repeat_checkbox.isChecked())
            self._scan_orbit_scan_count = 0
        self._cancel_scan_repeat_countdown()
        self._scan_orbit_scan_count += 1
        self.scan_orbit_scan_label.setText(f"{self._scan_orbit_scan_count}")
        self._scan_repeat_pending = False
        self._scan_samples = []
        self._scan_current_result = None
        self._scan_plan_points = self._build_scan_preview_points(config)
        self._scan_current_point = None
        self._scan_current_stage = "queued"
        self._scan_progress_current = 0
        self._scan_progress_total = len(self._scan_plan_points)
        self._scan_active_config = dict(config)
        self.scan_heatmap_widget.clear()
        self._update_scan_profile_axes(self._scan_uses_tracking_relative(config))
        self.scan_horizontal_curve.clear()
        self.scan_vertical_curve.clear()
        self._clear_scan_profile_markers()
        self._update_scan_error_plot(self._scan_error_history)
        self._scan_active_center_mode = str(config.get("center_mode", "fixed")).strip().lower()
        self._refresh_scan_path_visuals()
        self.scan_progress_label.setText(f"0/{len(self._scan_plan_points) or 0} | queued")
        self.scan_session.start(config)

    @staticmethod
    def _scan_uses_tracking_relative(config: dict | None) -> bool:
        if not isinstance(config, dict):
            return False
        mode = str(config.get("center_mode", "fixed")).strip().lower()
        return mode in {"dynamic", "follow", "orbit", "theoretical", "tracking", "tracking_relative"}

    def _update_scan_profile_axes(self, relative: bool) -> None:
        self.scan_heatmap_widget.set_axis_mode(relative=relative)
        self.scan_horizontal_plot.setLabel(
            "bottom",
            "Offset Azimuth" if relative else "Azimuth",
            units="deg",
        )
        self.scan_horizontal_plot.setLabel("left", "Metric")
        self.scan_vertical_plot.setLabel("bottom", "Metric")
        self.scan_vertical_plot.setLabel(
            "left",
            "Offset Elevation" if relative else "Elevation",
            units="deg",
        )
        self._sync_scan_profile_margins()

    def _sync_scan_profile_margins(self) -> None:
        try:
            heatmap_left_axis = self.scan_heatmap_widget.plot.getAxis("left")
            horizontal_left_axis = self.scan_horizontal_plot.getAxis("left")
            width = int(max(1, round(float(heatmap_left_axis.width()))))
            horizontal_left_axis.setWidth(width)
        except Exception:
            pass

    def _prepare_scan_session(self, config: dict) -> bool:
        if not self.has_connection():
            QMessageBox.warning(self, "Scan", "Le positionneur doit etre connecte pour lancer un scan.")
            return False
        if self._scan_uses_tracking_relative(config):
            tracker = getattr(self, "tracker", None)
            if tracker is None or not tracker.is_running():
                QMessageBox.warning(self, "Scan", "Le mode tracking_relative exige un tracking deja actif.")
                return False
            if hasattr(self, "clear_scan_probe_offset"):
                self.clear_scan_probe_offset()
            self._manual_setpoint_mode = False
            return True

        if getattr(self, "tracker", None) and self.tracker.is_running():
            self._stop_tracking_loop_from_ui()
        if getattr(self, "positioner", None) and self.positioner.is_running():
            self._stop_positioning_loop_from_ui()
        try:
            self.ephem.stop_object("primary")
        except Exception:
            pass
        if hasattr(self, "clear_scan_probe_offset"):
            self.clear_scan_probe_offset()
        self._manual_setpoint_mode = True
        try:
            if hasattr(self, "label_tracked_object"):
                self.label_tracked_object.setText("Scan")
                self._apply_selected_target_header_style()
        except Exception:
            pass
        self._clear_selected_target_details()
        return True

    def _stop_scan_session(self) -> None:
        self._scan_repeat_active = False
        self._scan_repeat_pending = False
        self._cancel_scan_repeat_countdown()
        self.scan_session.stop()
        self._reset_scan_probe_offset(stop_fixed_positioner=True)

    def _reset_scan_probe_offset(self, *, stop_fixed_positioner: bool = False) -> None:
        if hasattr(self, "clear_scan_probe_offset"):
            self.clear_scan_probe_offset()
        if stop_fixed_positioner and str(getattr(self, "_scan_active_center_mode", "fixed")).strip().lower() in {"fixed", "current_position"}:
            if getattr(self, "positioner", None) and self.positioner.is_running():
                self._stop_positioning_loop_from_ui()

    def _scan_move_to(self, point: dict, config: dict | None = None) -> None:
        config = dict(config or {})
        if self._scan_uses_tracking_relative(config):
            az_deg = float(point.get("az", 0.0))
            rel_az = float(point.get("relative_az_deg", az_deg - float(point.get("theoretical_az", az_deg))))
            el_deg = float(point.get("el", 0.0))
            rel_el = float(point.get("relative_el_deg", el_deg - float(point.get("theoretical_el", el_deg))))
            tracker = getattr(self, "tracker", None)
            if tracker is None or not tracker.is_running():
                raise RuntimeError("tracking_relative requires an active tracking loop.")
            if hasattr(self, "_set_scan_probe_offset_state"):
                self._set_scan_probe_offset_state(rel_az, rel_el)
            elif hasattr(self, "set_scan_probe_offset"):
                self.set_scan_probe_offset(rel_az, rel_el)
            return
        request = {
            "point": dict(point or {}),
            "config": config,
            "done": threading.Event(),
            "error": None,
        }
        self._scan_move_bridge.move_requested.emit(request)
        if not request["done"].wait(timeout=10.0):
            raise TimeoutError("Timed out while dispatching scan motion command.")
        if request["error"] is not None:
            raise RuntimeError(str(request["error"]))

    def _scan_apply_move_request(self, request: dict) -> None:
        try:
            point = dict(request.get("point") or {})
            config = dict(request.get("config") or {})
            az_deg = float(point.get("az", 0.0))
            el_deg = float(point.get("el", 0.0))
            if self._scan_uses_tracking_relative(config):
                tracker = getattr(self, "tracker", None)
                if tracker is None or not tracker.is_running():
                    raise RuntimeError("tracking_relative requires an active tracking loop.")
                rel_az = float(point.get("relative_az_deg", az_deg - float(point.get("theoretical_az", az_deg))))
                rel_el = float(point.get("relative_el_deg", el_deg - float(point.get("theoretical_el", el_deg))))
                if hasattr(self, "set_scan_probe_offset"):
                    self.set_scan_probe_offset(rel_az, rel_el)
                return

            if not hasattr(self, "tracked_object"):
                raise RuntimeError("Tracked object state is unavailable.")
            self._manual_setpoint_mode = True
            self._apply_manual_setpoints(az_deg, el_deg)
            if not self._ensure_positioner():
                raise RuntimeError("Positioner initialization failed.")
            if not self.positioner.is_running():
                self.positioner.start()
                self.start_tracking_ui_timer()
        except Exception as exc:
            request["error"] = exc
        finally:
            request["done"].set()

    def _scan_wait_for_settle(self, point: dict, config: dict | None = None, settle_s: float = 0.2) -> None:
        config = dict(config or {})
        tracking_relative = self._scan_uses_tracking_relative(config)
        antenna = self.settings.get("ANTENNA", self.settings.get("antenna", {})) if isinstance(self.settings, dict) else {}
        az_err_th = float(antenna.get("positioning_az_error_threshold", antenna.get("az_error_threshold", 0.05)))
        el_err_th = float(antenna.get("positioning_el_error_threshold", antenna.get("el_error_threshold", 0.05)))
        stable_cycles_required = max(2, int(antenna.get("positioning_stable_cycles", 3)))
        if tracking_relative:
            az_err_th = float(antenna.get("az_error_threshold", az_err_th))
            el_err_th = float(antenna.get("el_error_threshold", el_err_th))
            stable_cycles_required = max(2, int(antenna.get("positioning_stable_cycles", 3)))
        az_forbidden = parse_forbidden_ranges(antenna.get("az_forbidden_ranges"), default=[(45.0, 90.0), (270.0, 300.0)])
        el_forbidden = parse_forbidden_ranges(antenna.get("el_forbidden_ranges"), default=[(-10.0, 0.0), (95.0, 100.0)])
        stable_cycles = 0
        timeout_t = time.monotonic() + max(5.0, float(settle_s) + (20.0 if tracking_relative else 20.0))
        az_error = None
        el_error = None
        actual_offset_az = None
        actual_offset_el = None
        offset_error_az = None
        offset_error_el = None
        az_cur = None
        el_cur = None
        az_set = None
        el_set = None
        requested_offset_az = float(point.get("relative_az_deg", point.get("offset_az", 0.0)))
        requested_offset_el = float(point.get("relative_el_deg", point.get("offset_el", 0.0)))
        while time.monotonic() < timeout_t:
            antenna_state = getattr(getattr(self, "axis_client", None), "antenna", None)
            az_cur = getattr(antenna_state, "az", None)
            el_cur = getattr(antenna_state, "el", None)
            target = getattr(self, "tracked_object", None)
            az_set = getattr(target, "az_set", point.get("az"))
            el_set = getattr(target, "el_set", point.get("el"))
            if not all(isinstance(value, (int, float)) for value in (az_cur, el_cur, az_set, el_set)):
                time.sleep(0.05)
                continue
            az_error = constrained_azimuth_error(float(az_cur), float(az_set), az_forbidden)
            el_error = constrained_elevation_error(float(el_cur), float(el_set), el_forbidden)
            if az_error is None or el_error is None:
                stable_cycles = 0
                time.sleep(0.05)
                continue
            if tracking_relative:
                theoretical_az = getattr(target, "az_theoretical_deg", point.get("theoretical_az"))
                theoretical_el = getattr(target, "el_theoretical_deg", point.get("theoretical_el"))
                if not all(isinstance(value, (int, float)) for value in (theoretical_az, theoretical_el)):
                    stable_cycles = 0
                    time.sleep(0.05)
                    continue
                actual_offset_az = ((float(az_cur) - float(theoretical_az) + 180.0) % 360.0) - 180.0
                actual_offset_el = float(el_cur) - float(theoretical_el)
                offset_error_az = float(actual_offset_az) - requested_offset_az
                offset_error_el = float(actual_offset_el) - requested_offset_el
                settled = abs(float(offset_error_az)) <= az_err_th and abs(float(offset_error_el)) <= el_err_th
            else:
                settled = abs(float(az_error)) <= az_err_th and abs(float(el_error)) <= el_err_th
            if settled:
                stable_cycles += 1
                if stable_cycles >= stable_cycles_required:
                    self.logger.debug(
                        "[ScanSettle] ok target_az=%.3f target_el=%.3f req_offset_az=%.3f req_offset_el=%.3f actual_offset_az=%s actual_offset_el=%s offset_error_az=%s offset_error_el=%s",
                        float(point.get("az", 0.0)),
                        float(point.get("el", 0.0)),
                        requested_offset_az,
                        requested_offset_el,
                        actual_offset_az,
                        actual_offset_el,
                        offset_error_az,
                        offset_error_el,
                    )
                    if float(settle_s) > 0.0:
                        time.sleep(float(settle_s))
                    return
            else:
                stable_cycles = 0
            time.sleep(0.05)
        az_text = f"{float(az_error):+.2f}" if isinstance(az_error, (int, float)) else "?"
        el_text = f"{float(el_error):+.2f}" if isinstance(el_error, (int, float)) else "?"
        if tracking_relative:
            offset_az_text = f"{float(actual_offset_az):+.2f}" if isinstance(actual_offset_az, (int, float)) else "?"
            offset_el_text = f"{float(actual_offset_el):+.2f}" if isinstance(actual_offset_el, (int, float)) else "?"
            off_err_az_text = f"{float(offset_error_az):+.2f}" if isinstance(offset_error_az, (int, float)) else "?"
            off_err_el_text = f"{float(offset_error_el):+.2f}" if isinstance(offset_error_el, (int, float)) else "?"
            self.logger.debug(
                "[ScanSettle] timeout target_az=%.3f target_el=%.3f req_offset_az=%.3f req_offset_el=%.3f actual_offset_az=%s actual_offset_el=%s offset_error_az=%s offset_error_el=%s actual_az=%s actual_el=%s set_az=%s set_el=%s",
                float(point.get("az", 0.0)),
                float(point.get("el", 0.0)),
                requested_offset_az,
                requested_offset_el,
                offset_az_text,
                offset_el_text,
                off_err_az_text,
                off_err_el_text,
                az_cur if isinstance(az_cur, (int, float)) else None,
                el_cur if isinstance(el_cur, (int, float)) else None,
                az_set if isinstance(az_set, (int, float)) else None,
                el_set if isinstance(el_set, (int, float)) else None,
            )
            raise TimeoutError(
                f"Scan offset did not settle before timeout (offset_err_az={off_err_az_text}, offset_err_el={off_err_el_text})."
            )
        raise TimeoutError(f"Scan move did not settle before timeout (az_err={az_text}, el_err={el_text}).")

    def _scan_telemetry_snapshot(self) -> dict:
        antenna = getattr(getattr(self, "axis_client", None), "antenna", None)
        tracked = getattr(self, "tracked_object", None)
        return {
            "actual_az": getattr(antenna, "az", None),
            "actual_el": getattr(antenna, "el", None),
            "set_az": getattr(tracked, "az_set", None),
            "set_el": getattr(tracked, "el_set", None),
            "theoretical_az_live": getattr(tracked, "az_theoretical_deg", None),
            "theoretical_el_live": getattr(tracked, "el_theoretical_deg", None),
        }

    def _build_scan_preview_points(self, config: dict) -> list[dict]:
        strategy = str(config.get("strategy", "grid")).strip().lower()
        center_az = float(config.get("center_az_deg", 0.0))
        center_el = float(config.get("center_el_deg", 0.0))
        if strategy == "cross":
            curves = generate_cross_points(
                center_az,
                center_el,
                float(config.get("span_deg", 2.0)),
                float(config.get("step_deg", 0.5)),
            )
            return list(curves["azimuth"]) + list(curves["elevation"])
        if strategy == "spiral":
            return generate_spiral_points(
                center_az,
                center_el,
                float(config.get("span_deg", 2.0)),
                float(config.get("radial_step_deg", config.get("step_deg", 0.25))),
            )
        if strategy == "adaptive":
            return generate_two_pass_grid_points(
                center_az,
                center_el,
                float(config.get("coarse_span_deg", config.get("span_deg", 2.0))),
                float(config.get("coarse_step_deg", config.get("step_deg", 0.5))),
                float(config.get("fine_span_deg", max(0.2, float(config.get("span_deg", 2.0)) / 4.0))),
                float(config.get("fine_step_deg", max(0.05, float(config.get("step_deg", 0.5)) / 4.0))),
                order=str(config.get("order", "zigzag")),
            )
        return generate_grid_points(
            center_az,
            center_el,
            float(config.get("span_az_deg", config.get("span_deg", 2.0))),
            float(config.get("span_el_deg", config.get("span_deg", 2.0))),
            float(config.get("step_deg", 0.5)),
            order=str(config.get("order", "zigzag")),
        )

    def _scan_plot_coordinates(self, point: dict | None) -> tuple[float, float] | None:
        if not isinstance(point, dict):
            return None
        if str(getattr(self, "_scan_active_center_mode", "fixed")).strip().lower() == "tracking_relative":
            az = point.get("relative_az_deg", point.get("offset_az", point.get("az")))
            el = point.get("relative_el_deg", point.get("offset_el", point.get("el")))
        else:
            az = point.get("az")
            el = point.get("el")
        if not isinstance(az, (int, float)) or not isinstance(el, (int, float)):
            return None
        return float(az), float(el)

    def _refresh_scan_path_visuals(self) -> None:
        planned = [coords for coords in (self._scan_plot_coordinates(point) for point in self._scan_plan_points) if coords is not None]
        measured = [coords for coords in (self._scan_plot_coordinates(point) for point in self._scan_samples) if coords is not None]
        current = self._scan_plot_coordinates(self._scan_current_point)
        self.scan_heatmap_widget.set_scan_points(planned, measured, current)
        if planned:
            x_min, x_max, y_min, y_max = self._scan_plot_bounds()
            self.scan_heatmap_widget.set_scan_bounds(x_min, x_max, y_min, y_max)
        else:
            self.scan_heatmap_widget.clear_scan_bounds()
        current_result = getattr(self, "_scan_current_result", None) or {}
        strategy = str(
            current_result.get(
                "strategy",
                self._scan_combo_value(self.scan_strategy_combo, "grid") if hasattr(self, "scan_strategy_combo") else "grid",
            )
        ).strip().lower()
        if strategy in {"grid", "adaptive"} and self._scan_plan_points:
            cell_width, cell_height = self._scan_grid_cell_size()
            values = [float(point.get("value", 0.0)) for point in self._scan_samples]
            self.scan_heatmap_widget.set_grid_cells(
                measured,
                values,
                cell_width=cell_width,
                cell_height=cell_height,
            )
            self.scan_heatmap_widget.set_sample_cells([], [])
        elif measured:
            values = [float(point.get("value", 0.0)) for point in self._scan_samples]
            step_deg = float(self.scan_step_spin.value()) if hasattr(self, "scan_step_spin") else 0.5
            symbol_size = max(14.0, min(36.0, 18.0 + step_deg * 8.0))
            self.scan_heatmap_widget.set_sample_cells(measured, values, size=symbol_size)
        else:
            self.scan_heatmap_widget.set_grid_cells([], [], cell_width=1.0, cell_height=1.0)
            self.scan_heatmap_widget.set_sample_cells([], [])
        if planned:
            try:
                self.scan_horizontal_plot.enableAutoRange(x=False)
                self.scan_horizontal_plot.setXRange(float(x_min), float(x_max), padding=0.0)
            except Exception:
                pass
            try:
                self.scan_vertical_plot.enableAutoRange(y=False)
                self.scan_vertical_plot.setYRange(float(y_min), float(y_max), padding=0.0)
            except Exception:
                pass
            self._sync_scan_profile_margins()
            self.scan_heatmap_widget.set_scan_bounds(x_min, x_max, y_min, y_max)
        else:
            try:
                self.scan_horizontal_plot.enableAutoRange(x=True)
            except Exception:
                pass
            try:
                self.scan_vertical_plot.enableAutoRange(y=True)
            except Exception:
                pass

    def _scan_live_grid_heatmap(self) -> dict | None:
        if not self._scan_samples or not self._scan_plan_points:
            return None
        sample_map: dict[tuple[float, float], float] = {}
        for sample in self._scan_samples:
            coords = self._scan_plot_coordinates(sample)
            if coords is None:
                continue
            sample_map[(round(coords[0], 6), round(coords[1], 6))] = float(sample.get("value", 0.0))
        az_unique = sorted({round(coords[0], 6) for coords in (self._scan_plot_coordinates(point) for point in self._scan_plan_points) if coords is not None})
        el_unique = sorted({round(coords[1], 6) for coords in (self._scan_plot_coordinates(point) for point in self._scan_plan_points) if coords is not None})
        if len(az_unique) < 2 or len(el_unique) < 2:
            return None
        grid = np.full((len(el_unique), len(az_unique)), np.nan, dtype=np.float32)
        az_index = {value: index for index, value in enumerate(az_unique)}
        el_index = {value: index for index, value in enumerate(el_unique)}
        for (az_value, el_value), value in sample_map.items():
            grid[el_index[el_value], az_index[az_value]] = value
        return {"az_values": az_unique, "el_values": el_unique, "grid": grid}

    def _scan_grid_cell_size(self) -> tuple[float, float]:
        coords = [coord for coord in (self._scan_plot_coordinates(point) for point in self._scan_plan_points) if coord is not None]
        if not coords:
            step = float(self.scan_step_spin.value()) if hasattr(self, "scan_step_spin") else 0.5
            return step, step
        az_unique = sorted({round(coord[0], 6) for coord in coords})
        el_unique = sorted({round(coord[1], 6) for coord in coords})
        default_step = float(self.scan_step_spin.value()) if hasattr(self, "scan_step_spin") else 0.5
        az_steps = [abs(b - a) for a, b in zip(az_unique, az_unique[1:]) if abs(b - a) > 1e-9]
        el_steps = [abs(b - a) for a, b in zip(el_unique, el_unique[1:]) if abs(b - a) > 1e-9]
        cell_width = min(az_steps) if az_steps else default_step
        cell_height = min(el_steps) if el_steps else default_step
        return float(cell_width), float(cell_height)

    def _scan_plot_bounds(self) -> tuple[float, float, float, float]:
        config = getattr(self, "_scan_active_config", None)
        relative = str(getattr(self, "_scan_active_center_mode", "fixed")).strip().lower() == "tracking_relative"
        if relative:
            span_az = float((config or {}).get("span_az_deg", (config or {}).get("span_deg", self.scan_span_spin.value() if hasattr(self, "scan_span_spin") else 2.0)))
            span_el = float((config or {}).get("span_el_deg", (config or {}).get("span_deg", self.scan_span_spin.value() if hasattr(self, "scan_span_spin") else 2.0)))
            step = float((config or {}).get("step_deg", self.scan_step_spin.value() if hasattr(self, "scan_step_spin") else 0.5))
            half_w = step / 2.0
            half_h = step / 2.0
            return (
                -(span_az / 2.0) - half_w,
                +(span_az / 2.0) + half_w,
                -(span_el / 2.0) - half_h,
                +(span_el / 2.0) + half_h,
            )
        coords = [coord for coord in (self._scan_plot_coordinates(point) for point in self._scan_plan_points) if coord is not None]
        if not coords:
            return -1.0, 1.0, -1.0, 1.0
        cell_width, cell_height = self._scan_grid_cell_size()
        half_w = float(cell_width) / 2.0
        half_h = float(cell_height) / 2.0
        xs = [coord[0] for coord in coords]
        ys = [coord[1] for coord in coords]
        return (
            min(xs) - half_w,
            max(xs) + half_w,
            min(ys) - half_h,
            max(ys) + half_h,
        )

    @staticmethod
    def _project_scan_profile(samples: list[dict], axis: str, *, relative: bool) -> tuple[list[float], list[float]]:
        buckets: dict[float, float] = {}
        for sample in samples:
            if relative:
                coord = sample.get("relative_az_deg" if axis == "az" else "relative_el_deg")
            else:
                coord = sample.get("az" if axis == "az" else "el")
            value = sample.get("value")
            if not isinstance(coord, (int, float)) or not isinstance(value, (int, float)):
                continue
            key = round(float(coord), 6)
            buckets[key] = max(float(value), buckets.get(key, float("-inf")))
        xs = sorted(buckets)
        ys = [buckets[x] for x in xs]
        return xs, ys

    @staticmethod
    def _nearest_curve_value(xs: list[float], ys: list[float], target_x: float) -> float | None:
        if not xs or not ys or len(xs) != len(ys):
            return None
        index = min(range(len(xs)), key=lambda idx: abs(xs[idx] - target_x))
        return float(ys[index])

    def _scan_measure(self, config: dict) -> float:
        metric = str(config.get("metric", "band_power")).strip().lower()
        if not hasattr(self, "sdr_client") or self.sdr_client is None:
            raise RuntimeError("SDR backend is required for scans.")
        sdr_settings = self._scan_current_sdr_measure_settings()
        band_offset_hz = float(sdr_settings.get("band_offset_hz", config.get("band_offset_hz", 0.0)))
        bandwidth_hz = float(sdr_settings.get("bandwidth_hz", config.get("bandwidth_hz", 25_000.0)))
        integration_s = float(sdr_settings.get("integration_s", config.get("integration_s", 0.25)))
        if metric == "band_power":
            return self.sdr_client.measure_band_power(
                band_offset_hz,
                bandwidth_hz,
                integration_s,
            )
        spectrum = getattr(self.sdr_client, "_latest_spectrum_db", None)
        if spectrum is None:
            return self.sdr_client.measure_band_power(
                band_offset_hz,
                bandwidth_hz,
                integration_s,
            )
        if metric == "snr_absolute":
            return float(compute_snr(spectrum, "absolute", self.sdr_client.noise_floor_ref_db))
        return float(compute_snr(spectrum, "relative", self.sdr_client.noise_floor_ref_db))

    def _on_scan_point_measured(self, sample: dict) -> None:
        self._scan_samples.append(sample)
        self._refresh_scan_path_visuals()
        best = max(self._scan_samples, key=lambda point: float(point.get("value", float("-inf"))))
        self.scan_best_label.setText(f"AZ={best.get('az', 0.0):.2f} EL={best.get('el', 0.0):.2f} V={best.get('value', 0.0):.2f}")
        self.scan_offset_label.setText(f"dAZ={best.get('offset_az', 0.0):+.3f} dEL={best.get('offset_el', 0.0):+.3f}")
        relative = str(getattr(self, "_scan_active_center_mode", "fixed")).strip().lower() == "tracking_relative"
        az_xs, az_ys = self._project_scan_profile(self._scan_samples, "az", relative=relative)
        el_xs, el_ys = self._project_scan_profile(self._scan_samples, "el", relative=relative)
        self.scan_horizontal_curve.setData(az_xs, az_ys)
        self.scan_vertical_curve.setData(el_ys, el_xs)

    def _on_scan_progress_updated(self, snapshot: dict) -> None:
        current = int(snapshot.get("current", 0))
        total = int(snapshot.get("total", 0))
        point = snapshot.get("point", {})
        stage = str(snapshot.get("stage", "running")).strip().lower()
        self._scan_progress_current = current
        self._scan_progress_total = total
        self._scan_current_stage = stage
        self._scan_current_point = point if stage in {"move", "settle", "measure"} else None
        self._refresh_scan_path_visuals()
        coords = self._scan_plot_coordinates(point)
        if coords is None:
            self.scan_progress_label.setText(f"{current}/{total} | {stage}")
            return
        axis_label = "dAZ/dEL" if str(getattr(self, "_scan_active_center_mode", "fixed")).strip().lower() == "tracking_relative" else "AZ/EL"
        self.scan_progress_label.setText(f"{current}/{total} | {stage} | {axis_label}={coords[0]:+.2f}/{coords[1]:+.2f}")

    def _on_scan_error(self, message: str) -> None:
        self._scan_current_stage = "error"
        self._scan_current_point = None
        self._refresh_scan_path_visuals()
        current = int(getattr(self, "_scan_progress_current", 0))
        total = int(getattr(self, "_scan_progress_total", 0))
        self.scan_progress_label.setText(f"{current}/{total} | error | {message}")
        self.status_bar.showMessage(f"Scan: {message}", 5000)

    def _on_scan_completed(self, result: dict) -> None:
        self._scan_current_result = result
        self._reset_scan_probe_offset()
        self._scan_current_point = None
        self._scan_current_stage = "completed"
        self._scan_progress_current = int(len(result.get("samples", [])))
        self._scan_progress_total = max(self._scan_progress_total, self._scan_progress_current)
        peak = result.get("peak_estimate", {})
        best = result.get("best_point", {})
        self.scan_progress_label.setText(f"{self._scan_progress_current}/{self._scan_progress_total} | completed")
        self.scan_best_label.setText(f"AZ={best.get('az', 0.0):.2f} EL={best.get('el', 0.0):.2f} V={best.get('value', 0.0):.2f}")
        self.scan_offset_label.setText(f"dAZ={result.get('az_offset_deg', 0.0):.3f} dEL={result.get('el_offset_deg', 0.0):.3f}")
        best_coords = self._scan_plot_coordinates(best)
        if best_coords is not None:
            cell_width, cell_height = self._scan_grid_cell_size()
            self.scan_heatmap_widget.set_best_point(best_coords[0], best_coords[1], cell_width=cell_width, cell_height=cell_height)
        if result.get("strategy") == "spiral" and "heatmap" in result:
            heatmap = result["heatmap"]
            self.scan_heatmap_widget.set_heatmap(heatmap["az_values"], heatmap["el_values"], heatmap["grid"])
        elif result.get("strategy") in {"grid", "adaptive"} and self._scan_samples:
            measured = [coords for coords in (self._scan_plot_coordinates(point) for point in self._scan_samples) if coords is not None]
            values = [float(point.get("value", 0.0)) for point in self._scan_samples]
            cell_width, cell_height = self._scan_grid_cell_size()
            self.scan_heatmap_widget.set_grid_cells(
                measured,
                values,
                cell_width=cell_width,
                cell_height=cell_height,
            )
        self._refresh_scan_path_visuals()
        self._append_scan_error_history(peak)
        self._update_scan_error_plot(self._scan_error_history)
        self._update_profile_markers(peak)
        self._schedule_next_scan_sequence()

    def _update_scan_error_plot(self, error_trace: list[dict]) -> None:
        points = [point for point in error_trace if isinstance(point, dict)]
        y_extent = max(0.1, float(self.scan_span_spin.value()) / 2.0)
        self.scan_az_history_plot.setYRange(-y_extent, y_extent, padding=0.0)
        self.scan_el_history_plot.setYRange(-y_extent, y_extent, padding=0.0)
        if not points:
            self.scan_az_error_curve.clear()
            self.scan_el_error_curve.clear()
            return
        az_x = [float(point.get("theoretical_az", 0.0)) % 360.0 for point in points]
        az_y = [float(point.get("az_error_deg", 0.0)) for point in points]
        el_x = [float(point.get("theoretical_el", 0.0)) for point in points]
        el_y = [float(point.get("el_error_deg", 0.0)) for point in points]
        self.scan_az_history_plot.setXRange(0.0, 360.0, padding=0.0)
        self.scan_el_history_plot.setXRange(0.0, 90.0, padding=0.0)
        self.scan_az_error_curve.setData(az_x, az_y)
        self.scan_el_error_curve.setData(el_x, el_y)

    def _append_scan_error_history(self, peak: dict) -> None:
        if isinstance(peak, dict) and peak:
            point = dict(peak)
            point.setdefault("timestamp", time.time())
            self._scan_error_history.append(point)

    def _schedule_next_scan_sequence(self) -> bool:
        if not (self._scan_repeat_active and getattr(self, "tracker", None) and self.tracker.is_running()):
            self._cancel_scan_repeat_countdown()
            return False
        if self._scan_repeat_pending:
            return True
        interval_s = max(1.0, float(self.scan_repeat_interval_spin.value()))
        interval_ms = int(interval_s * 1000.0)
        self._scan_repeat_pending = True
        self._scan_repeat_interval_s = interval_s
        self._scan_repeat_due_monotonic = time.monotonic() + interval_s
        self._update_scan_repeat_countdown()
        self._scan_repeat_countdown_timer.start()
        self.status_bar.showMessage(f"Next scan sequence in {interval_s:.0f}s", 3000)
        pg.QtCore.QTimer.singleShot(
            interval_ms,
            lambda: self._start_scan_sequence(repeating=True) if self._scan_repeat_pending else None,
        )
        return True

    def _cancel_scan_repeat_countdown(self) -> None:
        timer = getattr(self, "_scan_repeat_countdown_timer", None)
        if timer is not None:
            timer.stop()
        self._scan_repeat_due_monotonic = 0.0
        self._scan_repeat_interval_s = 0.0
        if hasattr(self, "scan_next_wait_bar"):
            self.scan_next_wait_bar.setValue(0)
        if hasattr(self, "scan_next_wait_label"):
            self.scan_next_wait_label.setText("-")

    def _update_scan_repeat_countdown(self) -> None:
        if not self._scan_repeat_pending or self._scan_repeat_due_monotonic <= 0.0:
            self._cancel_scan_repeat_countdown()
            return
        remaining_s = max(0.0, self._scan_repeat_due_monotonic - time.monotonic())
        interval_s = max(1.0, float(self._scan_repeat_interval_s))
        percent = int(round((remaining_s / interval_s) * 100.0))
        self.scan_next_wait_bar.setValue(max(0, min(100, percent)))
        self.scan_next_wait_label.setText(f"{remaining_s:.0f}s -> scan {self._scan_orbit_scan_count + 1}")
        if remaining_s <= 0.0:
            self.scan_next_wait_bar.setValue(0)
            self.scan_next_wait_label.setText(f"starting scan {self._scan_orbit_scan_count + 1}")
            self._scan_repeat_countdown_timer.stop()

    def _update_profile_markers(self, peak: dict) -> None:
        relative = str(getattr(self, "_scan_active_center_mode", "fixed")).strip().lower() == "tracking_relative"
        az_xs, az_ys = self._project_scan_profile(self._scan_samples, "az", relative=relative)
        el_xs, el_ys = self._project_scan_profile(self._scan_samples, "el", relative=relative)
        theoretical_az_x = 0.0 if relative else float(peak.get("theoretical_az", 0.0))
        theoretical_el_y = 0.0 if relative else float(peak.get("theoretical_el", 0.0))
        az_peak_x = float(peak.get("az_error_deg", 0.0)) if relative else float(peak.get("az", 0.0))
        el_peak_y = float(peak.get("el_error_deg", 0.0)) if relative else float(peak.get("el", 0.0))
        horizontal_theoretical_points = []
        horizontal_real_points = []
        vertical_theoretical_points = []
        vertical_real_points = []
        az_theoretical_metric = self._nearest_curve_value(az_xs, az_ys, theoretical_az_x)
        az_peak_y = self._nearest_curve_value(az_xs, az_ys, az_peak_x)
        el_theoretical_metric = self._nearest_curve_value(el_xs, el_ys, theoretical_el_y)
        el_peak_metric = self._nearest_curve_value(el_xs, el_ys, el_peak_y)
        if az_theoretical_metric is not None:
            horizontal_theoretical_points.append({"pos": (theoretical_az_x, az_theoretical_metric)})
        if az_peak_y is not None:
            horizontal_real_points.append({"pos": (az_peak_x, az_peak_y)})
        if el_theoretical_metric is not None:
            vertical_theoretical_points.append({"pos": (el_theoretical_metric, theoretical_el_y)})
        if el_peak_metric is not None:
            vertical_real_points.append({"pos": (el_peak_metric, el_peak_y)})
        self.scan_horizontal_theoretical_marker.setData(horizontal_theoretical_points)
        self.scan_horizontal_real_marker.setData(horizontal_real_points)
        self.scan_vertical_theoretical_marker.setData(vertical_theoretical_points)
        self.scan_vertical_real_marker.setData(vertical_real_points)

    def _clear_scan_profile_markers(self) -> None:
        self.scan_horizontal_theoretical_marker.setData([])
        self.scan_horizontal_real_marker.setData([])
        self.scan_vertical_theoretical_marker.setData([])
        self.scan_vertical_real_marker.setData([])

    def _on_scan_state_changed(self, state: str) -> None:
        if state == "error" and self._scan_repeat_active and getattr(self, "tracker", None) and self.tracker.is_running():
            self._reset_scan_probe_offset(stop_fixed_positioner=True)
            self._scan_current_point = None
            self._refresh_scan_path_visuals()
            self._schedule_next_scan_sequence()
            self.status_bar.showMessage("Scan error: next repeated sequence scheduled", 5000)
            return
        if state in {"stopped", "error"}:
            self._scan_repeat_active = False
            self._scan_repeat_pending = False
            self._cancel_scan_repeat_countdown()
            self._reset_scan_probe_offset(stop_fixed_positioner=True)
            self._scan_current_point = None
            self._refresh_scan_path_visuals()
        if state == "stopped":
            self.scan_progress_label.setText(f"{self._scan_progress_current}/{self._scan_progress_total} | stopped")
        self.status_bar.showMessage(f"Scan state: {state}", 3000)

    def _apply_scan_offset(self, persist: bool) -> None:
        if not self._scan_current_result or not hasattr(self, "apply_scan_offset"):
            return
        az_offset = float(self._scan_current_result.get("az_offset_deg", 0.0))
        el_offset = float(self._scan_current_result.get("el_offset_deg", 0.0))
        self.apply_scan_offset(az_offset, el_offset, persist=persist)

    def _export_scan_error_csv(self) -> None:
        points = [point for point in getattr(self, "_scan_error_history", []) if isinstance(point, dict)]
        if not points:
            QMessageBox.information(self, "Scan", "Aucune courbe d'erreur a exporter.")
            return
        path, _selected_filter = QFileDialog.getSaveFileName(
            self,
            "Exporter les erreurs de scan",
            "scan_error_curve.csv",
            "CSV Files (*.csv);;All Files (*)",
        )
        if not path:
            return
        fieldnames = [
            "index",
            "timestamp",
            "theoretical_az_deg",
            "measured_az_deg",
            "az_error_deg",
            "theoretical_el_deg",
            "measured_el_deg",
            "el_error_deg",
            "angular_error_deg",
            "value",
            "method",
            "confidence",
        ]
        try:
            with open(path, "w", newline="", encoding="utf-8") as csv_file:
                writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
                writer.writeheader()
                for index, point in enumerate(points, start=1):
                    writer.writerow(
                        {
                            "index": index,
                            "timestamp": point.get("timestamp", ""),
                            "theoretical_az_deg": point.get("theoretical_az", ""),
                            "measured_az_deg": point.get("az", ""),
                            "az_error_deg": point.get("az_error_deg", ""),
                            "theoretical_el_deg": point.get("theoretical_el", ""),
                            "measured_el_deg": point.get("el", ""),
                            "el_error_deg": point.get("el_error_deg", ""),
                            "angular_error_deg": point.get("angular_error_deg", ""),
                            "value": point.get("value", ""),
                            "method": point.get("method", ""),
                            "confidence": point.get("confidence", ""),
                        }
                    )
        except OSError as exc:
            QMessageBox.warning(self, "Scan", f"Export CSV impossible: {exc}")
            return
        self.status_bar.showMessage(f"Scan error CSV exported: {path}", 5000)

    def close_scan_ui(self) -> None:
        if getattr(self, "scan_session", None) is not None:
            self.scan_session.stop()
        self._cancel_scan_repeat_countdown()

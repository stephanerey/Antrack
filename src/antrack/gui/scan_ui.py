"""Scan tab wiring for Antrack."""

from __future__ import annotations

import math
import threading
import time

import numpy as np
import pyqtgraph as pg
from PyQt5.QtCore import QObject, Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSplitter,
    QVBoxLayout,
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
from antrack.tracking.scan_results import scan_error_series
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
        self.scan_strategy_combo.addItems(["grid", "cross", "spiral", "adaptive"])
        self.scan_center_az_spin = QDoubleSpinBox(config_group)
        self.scan_center_az_spin.setRange(-720.0, 720.0)
        self.scan_center_el_spin = QDoubleSpinBox(config_group)
        self.scan_center_el_spin.setRange(-90.0, 180.0)
        self.scan_span_spin = QDoubleSpinBox(config_group)
        self.scan_span_spin.setRange(0.1, 20.0)
        self.scan_span_spin.setValue(2.0)
        self.scan_step_spin = QDoubleSpinBox(config_group)
        self.scan_step_spin.setRange(0.01, 5.0)
        self.scan_step_spin.setValue(0.5)
        self.scan_settle_spin = QDoubleSpinBox(config_group)
        self.scan_settle_spin.setRange(0.0, 10.0)
        self.scan_settle_spin.setValue(0.2)
        self.scan_integration_spin = QDoubleSpinBox(config_group)
        self.scan_integration_spin.setRange(0.01, 10.0)
        self.scan_integration_spin.setValue(0.25)
        self.scan_bandwidth_spin = QDoubleSpinBox(config_group)
        self.scan_bandwidth_spin.setRange(100.0, 5_000_000.0)
        self.scan_bandwidth_spin.setValue(25_000.0)
        self.scan_bandwidth_spin.setSuffix(" Hz")
        self.scan_offset_hz_spin = QDoubleSpinBox(config_group)
        self.scan_offset_hz_spin.setRange(-5_000_000.0, 5_000_000.0)
        self.scan_offset_hz_spin.setValue(0.0)
        self.scan_offset_hz_spin.setSuffix(" Hz")
        self.scan_metric_combo = QComboBox(config_group)
        self.scan_metric_combo.addItems(["band_power", "snr_relative", "snr_absolute"])
        self.scan_center_mode_combo = QComboBox(config_group)
        self.scan_center_mode_combo.addItems(["fixed", "tracking_relative"])
        self.scan_peak_estimator_combo = QComboBox(config_group)
        self.scan_peak_estimator_combo.addItems(["best_sample", "four_point_divergence"])

        config_layout.addWidget(QLabel("Strategy"), 0, 0)
        config_layout.addWidget(self.scan_strategy_combo, 0, 1)
        config_layout.addWidget(QLabel("Center AZ"), 0, 2)
        config_layout.addWidget(self.scan_center_az_spin, 0, 3)
        config_layout.addWidget(QLabel("Center EL"), 0, 4)
        config_layout.addWidget(self.scan_center_el_spin, 0, 5)
        config_layout.addWidget(QLabel("Span"), 1, 0)
        config_layout.addWidget(self.scan_span_spin, 1, 1)
        config_layout.addWidget(QLabel("Step"), 1, 2)
        config_layout.addWidget(self.scan_step_spin, 1, 3)
        config_layout.addWidget(QLabel("Settle"), 1, 4)
        config_layout.addWidget(self.scan_settle_spin, 1, 5)
        config_layout.addWidget(QLabel("Integration"), 2, 0)
        config_layout.addWidget(self.scan_integration_spin, 2, 1)
        config_layout.addWidget(QLabel("Bandwidth"), 2, 2)
        config_layout.addWidget(self.scan_bandwidth_spin, 2, 3)
        config_layout.addWidget(QLabel("Band Offset"), 2, 4)
        config_layout.addWidget(self.scan_offset_hz_spin, 2, 5)
        config_layout.addWidget(QLabel("Metric"), 3, 0)
        config_layout.addWidget(self.scan_metric_combo, 3, 1)
        config_layout.addWidget(QLabel("Center Mode"), 3, 2)
        config_layout.addWidget(self.scan_center_mode_combo, 3, 3)
        config_layout.addWidget(QLabel("Peak Estimator"), 3, 4)
        config_layout.addWidget(self.scan_peak_estimator_combo, 3, 5)

        control_group = QGroupBox("Scan Control", container)
        control_form = QFormLayout(control_group)
        self.scan_start_button = QPushButton("Start", control_group)
        self.scan_pause_button = QPushButton("Pause", control_group)
        self.scan_resume_button = QPushButton("Resume", control_group)
        self.scan_stop_button = QPushButton("Stop", control_group)
        self.scan_apply_button = QPushButton("Apply Offset", control_group)
        self.scan_save_button = QPushButton("Save Offset", control_group)
        self.scan_repeat_checkbox = QCheckBox("Repeat While Tracking", control_group)
        self.scan_repeat_interval_spin = QDoubleSpinBox(control_group)
        self.scan_repeat_interval_spin.setRange(1.0, 3600.0)
        self.scan_repeat_interval_spin.setValue(60.0)
        self.scan_repeat_interval_spin.setSuffix(" s")
        self.scan_progress_label = QLabel("-", control_group)
        self.scan_best_label = QLabel("-", control_group)
        self.scan_offset_label = QLabel("-", control_group)
        repeat_row = QHBoxLayout()
        repeat_row.setContentsMargins(0, 0, 0, 0)
        repeat_row.addWidget(self.scan_repeat_checkbox)
        repeat_row.addWidget(self.scan_repeat_interval_spin)
        control_form.addRow(self.scan_start_button)
        control_form.addRow(self.scan_pause_button)
        control_form.addRow(self.scan_resume_button)
        control_form.addRow(self.scan_stop_button)
        control_form.addRow("Repeat", repeat_row)
        control_form.addRow("Progress", self.scan_progress_label)
        control_form.addRow("Best", self.scan_best_label)
        control_form.addRow("Offset", self.scan_offset_label)
        control_form.addRow(self.scan_apply_button)
        control_form.addRow(self.scan_save_button)

        splitter = QSplitter(Qt.Vertical, container)
        self.scan_heatmap_widget = HeatmapWidget(splitter)
        self.scan_cross_plot = pg.PlotWidget(splitter)
        self.scan_cross_plot.addLegend()
        self.scan_cross_plot.setLabel("bottom", "Angle", units="deg")
        self.scan_cross_plot.setLabel("left", "Metric")
        self.scan_cross_az_curve = self.scan_cross_plot.plot(name="Azimuth", pen=pg.mkPen("y"))
        self.scan_cross_el_curve = self.scan_cross_plot.plot(name="Elevation", pen=pg.mkPen("c"))
        self.scan_cross_theoretical_marker = pg.ScatterPlotItem(name="Theoretical", symbol="t", size=11, brush=pg.mkBrush(255, 220, 120, 220), pen=pg.mkPen("k"))
        self.scan_cross_real_marker = pg.ScatterPlotItem(name="Measured Peak", symbol="o", size=11, brush=pg.mkBrush(255, 255, 255, 220), pen=pg.mkPen("k"))
        self.scan_cross_plot.addItem(self.scan_cross_theoretical_marker)
        self.scan_cross_plot.addItem(self.scan_cross_real_marker)
        self.scan_error_plot = pg.PlotWidget(splitter)
        self.scan_error_plot.addLegend()
        self.scan_error_plot.setLabel("bottom", "Estimate", units="#")
        self.scan_error_plot.setLabel("left", "Error", units="deg")
        self.scan_error_az_curve = self.scan_error_plot.plot(name="AZ error", pen=pg.mkPen("y"))
        self.scan_error_el_curve = self.scan_error_plot.plot(name="EL error", pen=pg.mkPen("c"))
        self.scan_error_total_curve = self.scan_error_plot.plot(name="Angular error", pen=pg.mkPen("m"))
        splitter.addWidget(self.scan_heatmap_widget)
        splitter.addWidget(self.scan_cross_plot)
        splitter.addWidget(self.scan_error_plot)
        splitter.setSizes([550, 250, 250])

        root_layout.addWidget(config_group)
        root_layout.addWidget(control_group)
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
        self.scan_start_button.clicked.connect(self.start_scan_session)
        self.scan_pause_button.clicked.connect(self.scan_session.pause)
        self.scan_resume_button.clicked.connect(self.scan_session.resume)
        self.scan_stop_button.clicked.connect(self._stop_scan_session)
        self.scan_apply_button.clicked.connect(lambda: self._apply_scan_offset(False))
        self.scan_save_button.clicked.connect(lambda: self._apply_scan_offset(True))
        self._populate_scan_center_from_tracking()

    def _populate_scan_center_from_tracking(self) -> None:
        az = getattr(getattr(self, "tracked_object", None), "az_set", 0.0)
        el = getattr(getattr(self, "tracked_object", None), "el_set", 0.0)
        if isinstance(az, (int, float)):
            self.scan_center_az_spin.setValue(float(az))
        if isinstance(el, (int, float)):
            self.scan_center_el_spin.setValue(float(el))

    def _build_scan_config(self) -> dict:
        return {
            "strategy": self.scan_strategy_combo.currentText(),
            "center_mode": self.scan_center_mode_combo.currentText(),
            "center_az_deg": self.scan_center_az_spin.value(),
            "center_el_deg": self.scan_center_el_spin.value(),
            "span_deg": self.scan_span_spin.value(),
            "span_az_deg": self.scan_span_spin.value(),
            "span_el_deg": self.scan_span_spin.value(),
            "step_deg": self.scan_step_spin.value(),
            "radial_step_deg": self.scan_step_spin.value(),
            "settle_s": self.scan_settle_spin.value(),
            "integration_s": self.scan_integration_spin.value(),
            "bandwidth_hz": self.scan_bandwidth_spin.value(),
            "band_offset_hz": self.scan_offset_hz_spin.value(),
            "metric": self.scan_metric_combo.currentText(),
            "peak_estimator": self.scan_peak_estimator_combo.currentText(),
            "coarse_span_deg": self.scan_span_spin.value(),
            "coarse_step_deg": self.scan_step_spin.value(),
            "fine_span_deg": max(0.2, self.scan_span_spin.value() / 4.0),
            "fine_step_deg": max(0.05, self.scan_step_spin.value() / 4.0),
            "grid_step_deg": max(0.05, self.scan_step_spin.value()),
        }

    def _scan_current_theoretical_center(self) -> tuple[float, float]:
        tracked_object = getattr(self, "tracked_object", None)
        az = getattr(tracked_object, "az_theoretical_deg", None)
        el = getattr(tracked_object, "el_theoretical_deg", None)
        if not isinstance(az, (int, float)):
            az = getattr(tracked_object, "az_set", self.scan_center_az_spin.value())
        if not isinstance(el, (int, float)):
            el = getattr(tracked_object, "el_set", self.scan_center_el_spin.value())
        return float(az), float(el)

    def start_scan_session(self) -> None:
        self._start_scan_sequence(repeating=False)

    def _start_scan_sequence(self, *, repeating: bool) -> None:
        config = self._build_scan_config()
        if not self._prepare_scan_session(config):
            return
        if not repeating:
            self._scan_error_history = []
            self._scan_repeat_active = bool(self.scan_repeat_checkbox.isChecked())
        self._scan_repeat_pending = False
        self._scan_samples = []
        self._scan_current_result = None
        self._scan_plan_points = self._build_scan_preview_points(config)
        self._scan_current_point = None
        self._scan_current_stage = "queued"
        self._scan_progress_current = 0
        self._scan_progress_total = len(self._scan_plan_points)
        self.scan_heatmap_widget.clear()
        self.scan_heatmap_widget.set_axis_mode(relative=self._scan_uses_tracking_relative(config))
        self.scan_cross_az_curve.clear()
        self.scan_cross_el_curve.clear()
        self.scan_cross_theoretical_marker.clear()
        self.scan_cross_real_marker.clear()
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
        self.scan_session.stop()
        self._reset_scan_probe_offset(stop_fixed_positioner=True)

    def _reset_scan_probe_offset(self, *, stop_fixed_positioner: bool = False) -> None:
        if hasattr(self, "clear_scan_probe_offset"):
            self.clear_scan_probe_offset()
        if stop_fixed_positioner and str(getattr(self, "_scan_active_center_mode", "fixed")).strip().lower() == "fixed":
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
                    self.logger.info(
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
            self.logger.info(
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
        current_result = getattr(self, "_scan_current_result", None) or {}
        strategy = str(current_result.get("strategy", self.scan_strategy_combo.currentText() if hasattr(self, "scan_strategy_combo") else "grid")).strip().lower()
        if strategy in {"grid", "adaptive"} and self._scan_plan_points:
            heatmap = self._scan_live_grid_heatmap()
            if heatmap is not None:
                self.scan_heatmap_widget.set_heatmap(heatmap["az_values"], heatmap["el_values"], heatmap["grid"])
            self.scan_heatmap_widget.set_sample_cells([], [])
        elif measured:
            values = [float(point.get("value", 0.0)) for point in self._scan_samples]
            step_deg = float(self.scan_step_spin.value()) if hasattr(self, "scan_step_spin") else 0.5
            symbol_size = max(14.0, min(36.0, 18.0 + step_deg * 8.0))
            self.scan_heatmap_widget.set_sample_cells(measured, values, size=symbol_size)
        else:
            self.scan_heatmap_widget.set_sample_cells([], [])

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
        if metric == "band_power":
            return self.sdr_client.measure_band_power(
                float(config.get("band_offset_hz", 0.0)),
                float(config.get("bandwidth_hz", 25_000.0)),
                float(config.get("integration_s", 0.25)),
            )
        spectrum = getattr(self.sdr_client, "_latest_spectrum_db", None)
        if spectrum is None:
            return self.sdr_client.measure_band_power(
                float(config.get("band_offset_hz", 0.0)),
                float(config.get("bandwidth_hz", 25_000.0)),
                float(config.get("integration_s", 0.25)),
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
        self.scan_cross_az_curve.setData(az_xs, az_ys)
        self.scan_cross_el_curve.setData(el_xs, el_ys)

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
            self.scan_heatmap_widget.set_best_point(best_coords[0], best_coords[1])
        if result.get("strategy") == "spiral" and "heatmap" in result:
            heatmap = result["heatmap"]
            self.scan_heatmap_widget.set_heatmap(heatmap["az_values"], heatmap["el_values"], heatmap["grid"])
        elif result.get("strategy") in {"grid", "adaptive"} and self._scan_samples:
            coords = [(self._scan_plot_coordinates(point), point) for point in self._scan_samples]
            coords = [(coord, point) for coord, point in coords if coord is not None]
            az_unique = sorted({round(coord[0], 6) for coord, _point in coords})
            el_unique = sorted({round(coord[1], 6) for coord, _point in coords})
            grid = np.full((len(el_unique), len(az_unique)), np.nan, dtype=np.float32)
            az_index = {value: index for index, value in enumerate(az_unique)}
            el_index = {value: index for index, value in enumerate(el_unique)}
            for coord, point in coords:
                grid[el_index[round(coord[1], 6)], az_index[round(coord[0], 6)]] = float(point["value"])
            self.scan_heatmap_widget.set_heatmap(az_unique, el_unique, grid)
        self._refresh_scan_path_visuals()
        self._append_scan_error_history(peak)
        self._update_scan_error_plot(self._scan_error_history)
        self._update_cross_markers(peak)
        if self._scan_repeat_active and getattr(self, "tracker", None) and self.tracker.is_running():
            interval_ms = int(max(1000.0, float(self.scan_repeat_interval_spin.value()) * 1000.0))
            self._scan_repeat_pending = True
            self.status_bar.showMessage(f"Next scan sequence in {self.scan_repeat_interval_spin.value():.0f}s", 3000)
            pg.QtCore.QTimer.singleShot(interval_ms, lambda: self._start_scan_sequence(repeating=True) if self._scan_repeat_pending else None)

    def _update_scan_error_plot(self, error_trace: list[dict]) -> None:
        series = scan_error_series(error_trace)
        self.scan_error_az_curve.setData(series["x"], series["az_error_deg"])
        self.scan_error_el_curve.setData(series["x"], series["el_error_deg"])
        self.scan_error_total_curve.setData(series["x"], series["angular_error_deg"])

    def _append_scan_error_history(self, peak: dict) -> None:
        if isinstance(peak, dict) and peak:
            self._scan_error_history.append(dict(peak))

    def _update_cross_markers(self, peak: dict) -> None:
        relative = str(getattr(self, "_scan_active_center_mode", "fixed")).strip().lower() == "tracking_relative"
        az_xs, az_ys = self._project_scan_profile(self._scan_samples, "az", relative=relative)
        el_xs, el_ys = self._project_scan_profile(self._scan_samples, "el", relative=relative)
        theoretical_az_x = 0.0 if relative else float(peak.get("theoretical_az", 0.0))
        theoretical_el_x = 0.0 if relative else float(peak.get("theoretical_el", 0.0))
        az_peak_x = float(peak.get("az_error_deg", 0.0)) if relative else float(peak.get("az", 0.0))
        el_peak_x = float(peak.get("el_error_deg", 0.0)) if relative else float(peak.get("el", 0.0))
        theoretical_points = []
        real_points = []
        az_theoretical_y = self._nearest_curve_value(az_xs, az_ys, theoretical_az_x)
        el_theoretical_y = self._nearest_curve_value(el_xs, el_ys, theoretical_el_x)
        az_peak_y = self._nearest_curve_value(az_xs, az_ys, az_peak_x)
        el_peak_y = self._nearest_curve_value(el_xs, el_ys, el_peak_x)
        if az_theoretical_y is not None:
            theoretical_points.append({"pos": (theoretical_az_x, az_theoretical_y)})
        if el_theoretical_y is not None:
            theoretical_points.append({"pos": (theoretical_el_x, el_theoretical_y)})
        if az_peak_y is not None:
            real_points.append({"pos": (az_peak_x, az_peak_y)})
        if el_peak_y is not None:
            real_points.append({"pos": (el_peak_x, el_peak_y)})
        self.scan_cross_theoretical_marker.setData(theoretical_points)
        self.scan_cross_real_marker.setData(real_points)

    def _on_scan_state_changed(self, state: str) -> None:
        if state in {"stopped", "error"}:
            self._scan_repeat_active = False
            self._scan_repeat_pending = False
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

    def close_scan_ui(self) -> None:
        if getattr(self, "scan_session", None) is not None:
            self.scan_session.stop()

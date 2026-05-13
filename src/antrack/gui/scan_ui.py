"""Scan tab wiring for Antrack."""

from __future__ import annotations

import math
import threading
import time

import numpy as np
import pyqtgraph as pg
from PyQt5.QtCore import QObject, Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
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
from antrack.tracking.scan_results import scan_error_series
from antrack.tracking.scan_session import ScanSession


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
        self.scan_progress_label = QLabel("-", control_group)
        self.scan_best_label = QLabel("-", control_group)
        self.scan_offset_label = QLabel("-", control_group)
        control_form.addRow(self.scan_start_button)
        control_form.addRow(self.scan_pause_button)
        control_form.addRow(self.scan_resume_button)
        control_form.addRow(self.scan_stop_button)
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
            logger=self.logger.getChild("Scan"),
        )
        self._scan_move_bridge = _ScanMoveBridge(self)
        self._scan_move_bridge.move_requested.connect(self._scan_apply_move_request)
        self.scan_session.progress_updated.connect(self._on_scan_progress_updated)
        self.scan_session.point_measured.connect(self._on_scan_point_measured)
        self.scan_session.completed.connect(self._on_scan_completed)
        self.scan_session.error.connect(lambda message: self.status_bar.showMessage(f"Scan: {message}", 5000))
        self.scan_session.state_changed.connect(self._on_scan_state_changed)

        self._scan_samples = []
        self._scan_current_result = None
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
        config = self._build_scan_config()
        if not self._prepare_scan_session(config):
            return
        self._scan_samples = []
        self._scan_current_result = None
        self.scan_heatmap_widget.clear()
        self.scan_cross_az_curve.clear()
        self.scan_cross_el_curve.clear()
        self._update_scan_error_plot([])
        self._scan_active_center_mode = str(config.get("center_mode", "fixed")).strip().lower()
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
        self.scan_session.stop()
        self._reset_scan_probe_offset(stop_fixed_positioner=True)

    def _reset_scan_probe_offset(self, *, stop_fixed_positioner: bool = False) -> None:
        if hasattr(self, "clear_scan_probe_offset"):
            self.clear_scan_probe_offset()
        if stop_fixed_positioner and str(getattr(self, "_scan_active_center_mode", "fixed")).strip().lower() == "fixed":
            if getattr(self, "positioner", None) and self.positioner.is_running():
                self._stop_positioning_loop_from_ui()

    def _scan_move_to(self, point: dict, config: dict | None = None) -> None:
        request = {
            "point": dict(point or {}),
            "config": dict(config or {}),
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
        antenna = self.settings.get("ANTENNA", self.settings.get("antenna", {})) if isinstance(self.settings, dict) else {}
        az_err_th = float(antenna.get("positioning_az_error_threshold", antenna.get("az_error_threshold", 0.05)))
        el_err_th = float(antenna.get("positioning_el_error_threshold", antenna.get("el_error_threshold", 0.05)))
        stable_cycles_required = max(2, int(antenna.get("positioning_stable_cycles", 3)))
        az_forbidden = parse_forbidden_ranges(antenna.get("az_forbidden_ranges"), default=[(45.0, 90.0), (270.0, 300.0)])
        el_forbidden = parse_forbidden_ranges(antenna.get("el_forbidden_ranges"), default=[(-10.0, 0.0), (95.0, 100.0)])
        stable_cycles = 0
        timeout_t = time.monotonic() + max(5.0, float(settle_s) + 20.0)
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
            if abs(float(az_error)) <= az_err_th and abs(float(el_error)) <= el_err_th:
                stable_cycles += 1
                if stable_cycles >= stable_cycles_required:
                    if float(settle_s) > 0.0:
                        time.sleep(float(settle_s))
                    return
            else:
                stable_cycles = 0
            time.sleep(0.05)
        raise TimeoutError("Scan move did not settle before timeout.")

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
        az_points = [point for point in self._scan_samples if point.get("axis") == "az"]
        el_points = [point for point in self._scan_samples if point.get("axis") == "el"]
        if az_points:
            self.scan_cross_az_curve.setData([point["az"] for point in az_points], [point["value"] for point in az_points])
        if el_points:
            self.scan_cross_el_curve.setData([point["el"] for point in el_points], [point["value"] for point in el_points])

    def _on_scan_progress_updated(self, snapshot: dict) -> None:
        current = int(snapshot.get("current", 0))
        total = int(snapshot.get("total", 0))
        point = snapshot.get("point", {})
        self.scan_progress_label.setText(f"{current}/{total} @ AZ={point.get('az', 0.0):.2f} EL={point.get('el', 0.0):.2f}")

    def _on_scan_completed(self, result: dict) -> None:
        self._scan_current_result = result
        self._reset_scan_probe_offset()
        best = result.get("best_point", {})
        self.scan_best_label.setText(f"AZ={best.get('az', 0.0):.2f} EL={best.get('el', 0.0):.2f} V={best.get('value', 0.0):.2f}")
        self.scan_offset_label.setText(f"dAZ={result.get('az_offset_deg', 0.0):.3f} dEL={result.get('el_offset_deg', 0.0):.3f}")
        self.scan_heatmap_widget.set_best_point(float(best.get("az", 0.0)), float(best.get("el", 0.0)))
        if result.get("strategy") == "spiral" and "heatmap" in result:
            heatmap = result["heatmap"]
            self.scan_heatmap_widget.set_heatmap(heatmap["az_values"], heatmap["el_values"], heatmap["grid"])
        elif result.get("strategy") in {"grid", "adaptive"} and self._scan_samples:
            az_unique = sorted({round(point["az"], 6) for point in self._scan_samples})
            el_unique = sorted({round(point["el"], 6) for point in self._scan_samples})
            grid = np.full((len(el_unique), len(az_unique)), np.nan, dtype=np.float32)
            az_index = {value: index for index, value in enumerate(az_unique)}
            el_index = {value: index for index, value in enumerate(el_unique)}
            for point in self._scan_samples:
                grid[el_index[round(point["el"], 6)], az_index[round(point["az"], 6)]] = float(point["value"])
            self.scan_heatmap_widget.set_heatmap(az_unique, el_unique, grid)
        self._update_scan_error_plot(result.get("error_trace", []))

    def _update_scan_error_plot(self, error_trace: list[dict]) -> None:
        series = scan_error_series(error_trace)
        self.scan_error_az_curve.setData(series["x"], series["az_error_deg"])
        self.scan_error_el_curve.setData(series["x"], series["el_error_deg"])
        self.scan_error_total_curve.setData(series["x"], series["angular_error_deg"])

    def _on_scan_state_changed(self, state: str) -> None:
        if state in {"stopped", "error"}:
            self._reset_scan_probe_offset(stop_fixed_positioner=True)
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

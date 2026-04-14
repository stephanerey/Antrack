"""Scan tab wiring for Antrack."""

from __future__ import annotations

import math
import time

import numpy as np
import pyqtgraph as pg
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QLabel,
    QPushButton,
    QSplitter,
    QVBoxLayout,
)

from antrack.core.dsp.snr import compute_snr
from antrack.gui.widgets.heatmap_widget import HeatmapWidget
from antrack.tracking.scan_session import ScanSession


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
        splitter.addWidget(self.scan_heatmap_widget)
        splitter.addWidget(self.scan_cross_plot)
        splitter.setSizes([600, 300])

        root_layout.addWidget(config_group)
        root_layout.addWidget(control_group)
        root_layout.addWidget(splitter, 1)

        self.scan_session = ScanSession(
            thread_manager=self.thread_manager,
            move_to=self._scan_move_to,
            measure=self._scan_measure,
            wait_for_settle=self._scan_wait_for_settle,
            logger=self.logger.getChild("Scan"),
        )
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
        self.scan_stop_button.clicked.connect(self.scan_session.stop)
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
            "coarse_span_deg": self.scan_span_spin.value(),
            "coarse_step_deg": self.scan_step_spin.value(),
            "fine_span_deg": max(0.2, self.scan_span_spin.value() / 4.0),
            "fine_step_deg": max(0.05, self.scan_step_spin.value() / 4.0),
            "grid_step_deg": max(0.05, self.scan_step_spin.value()),
        }

    def start_scan_session(self) -> None:
        self._scan_samples = []
        self._scan_current_result = None
        self.scan_heatmap_widget.clear()
        self.scan_cross_az_curve.clear()
        self.scan_cross_el_curve.clear()
        self.scan_session.start(self._build_scan_config())

    def _scan_move_to(self, az_deg: float, el_deg: float) -> None:
        if hasattr(self, "start_fixed_positioning") and self.has_connection():
            self.start_fixed_positioning(az_deg, el_deg, label="Scan")
        else:
            if hasattr(self, "tracked_object"):
                self.tracked_object.az_set = float(az_deg)
                self.tracked_object.el_set = float(el_deg)

    def _scan_wait_for_settle(self, az_deg: float, el_deg: float, settle_s: float) -> None:
        timeout_t = time.monotonic() + max(1.0, float(settle_s) + 15.0)
        time.sleep(float(settle_s))
        while time.monotonic() < timeout_t:
            positioner = getattr(self, "positioner", None)
            if positioner is None or not positioner.is_running():
                return
            time.sleep(0.05)

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

    def _on_scan_state_changed(self, state: str) -> None:
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

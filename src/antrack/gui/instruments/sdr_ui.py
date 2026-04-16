"""SDR tab wiring for the Antrack main window."""

from __future__ import annotations

import math

import numpy as np

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QGroupBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSpacerItem,
    QSlider,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from antrack.core.dsp.snr import average_power_spectrum_db, bin_width_to_density_offset_db, compute_band_power_metrics
from antrack.core.instruments.sdr_client import SAMPLE_RATE_PRESETS_HZ, SdrClient
from antrack.gui.dialogs.auto_gain_table import AutoGainTableDialog
from antrack.gui.widgets.frequency_control import FrequencyControlWidget
from antrack.gui.widgets.level_meter import LevelMeterWidget
from antrack.gui.widgets.spectrum_plot import SpectrumPlotWidget
from antrack.gui.widgets.waterfall_plot import WaterfallPlotWidget
from antrack.tools.gain_table import clamp_lna_state, lna_attenuation_db, max_lna_state_for_frequency
from antrack.utils.settings_loader import update_and_persist_setting


SMOOTHING_OPTIONS = ["Off", "Light", "Medium", "Strong"]


class SdrUiMixin:
    """Populate and manage the SDR tab."""

    def setup_sdr_ui(self) -> None:
        if getattr(self, "_sdr_ui_ready", False):
            return
        container = getattr(self, "tab_map", None)
        if container is None:
            return

        self._sdr_ui_ready = True
        self.sdr_client = SdrClient(
            self.settings,
            thread_manager=self.thread_manager,
            logger=self.logger.getChild("SDR"),
        )
        self.sdr_auto_gain_dialog = AutoGainTableDialog(self)
        self.sdr_auto_gain_dialog.widget.activeLevelChanged.connect(self._on_auto_gain_profile_changed)
        self.sdr_auto_gain_dialog.widget.profilesChanged.connect(self._on_auto_gain_profiles_changed)

        self._sdr_freq_timer = QTimer(container)
        self._sdr_freq_timer.setSingleShot(True)
        self._sdr_freq_timer.setInterval(120)
        self._sdr_freq_timer.timeout.connect(self._apply_pending_sdr_frequency)
        self._pending_sdr_freq_mhz = None
        self._sdr_snr_reference_db = None
        self._sdr_snr_reference_per_bin_db = None
        self._sdr_last_absolute_metrics = None
        self._sdr_last_view_center_hz = None
        self._sdr_last_view_sample_rate_hz = None

        cfg = self._sdr_settings()
        self._sdr_view_mode = str(cfg.get("view_mode", "center")).strip().lower()
        if self._sdr_view_mode not in {"center", "fix"}:
            self._sdr_view_mode = "center"
        self._sdr_power_unit = str(cfg.get("power_unit", "db_per_bin")).strip().lower()
        if self._sdr_power_unit not in {"db_per_bin", "db_per_hz"}:
            self._sdr_power_unit = "db_per_bin"
        self._sdr_level_unit = str(cfg.get("level_unit", "dbm")).strip().lower()
        if self._sdr_level_unit not in {"dbm", "db_per_hz", "s_meter"}:
            self._sdr_level_unit = "dbm"
        self._sdr_snr_mode = str(cfg.get("snr_mode", "absolute")).strip().lower()
        if self._sdr_snr_mode not in {"absolute", "relative"}:
            self._sdr_snr_mode = "absolute"
        self._sdr_snr_integration_s = self._coerce_float(cfg.get("snr_integration_s", 2.0), 2.0, minimum=0.1)
        self._sdr_spectrum_y_min_db = self._coerce_float(cfg.get("spectrum_y_min_db", -130.0), -130.0)
        self._sdr_spectrum_y_range_db = self._coerce_float(cfg.get("spectrum_y_range_db", 100.0), 100.0, minimum=10.0)
        self._sdr_waterfall_baseline_db = self._coerce_float(cfg.get("waterfall_baseline_db", -120.0), -120.0)
        self._sdr_waterfall_time_stride = int(max(1, round(self._coerce_float(cfg.get("waterfall_time_stride", 1), 1.0, minimum=1.0, maximum=20.0))))
        self.sdr_client.set_snr_mode(self._sdr_snr_mode)
        self.sdr_client.data_storage.set_waterfall_time_stride(self._sdr_waterfall_time_stride)

        root_layout = container.layout()
        if root_layout is None:
            root_layout = QVBoxLayout(container)
        self._clear_layout(root_layout)

        controls_widget = QWidget(container)
        controls_layout = QVBoxLayout(controls_widget)
        controls_layout.setContentsMargins(0, 0, 0, 0)
        controls_layout.setSpacing(8)

        self.sdr_source_combo = QComboBox(controls_widget)
        self.sdr_sample_rate_combo = QComboBox(controls_widget)

        self.sdr_antenna_combo = QComboBox(controls_widget)
        self.sdr_fft_size_combo = QComboBox(controls_widget)
        self.sdr_refresh_rate_combo = QComboBox(controls_widget)
        self.sdr_refresh_rate_combo.addItem("Auto", None)
        for fps in (2.0, 5.0, 10.0, 15.0, 20.0, 30.0):
            self.sdr_refresh_rate_combo.addItem(f"{int(fps)} fps", float(fps))
        self.sdr_power_unit_combo = QComboBox(controls_widget)
        self.sdr_power_unit_combo.addItem("dBm", "db_per_bin")
        self.sdr_power_unit_combo.addItem("dBm/Hz", "db_per_hz")
        self.sdr_view_mode_combo = QComboBox(controls_widget)
        self.sdr_view_mode_combo.addItem("Center", "center")
        self.sdr_view_mode_combo.addItem("Fix", "fix")

        self.sdr_if_gain_label = QLabel("IF Gain:", controls_widget)
        self.sdr_if_value_label = QLabel("", controls_widget)
        self.sdr_if_gain_slider = QSlider(Qt.Horizontal, controls_widget)
        self.sdr_if_gain_slider.setRange(0, 59)

        self.sdr_trace_avg_label = QLabel("Trace avg.:", controls_widget)
        self.sdr_trace_avg_combo = QComboBox(controls_widget)
        self.sdr_trace_avg_combo.addItems(SMOOTHING_OPTIONS)

        self.sdr_rf_gain_label = QLabel("RF Gain:", controls_widget)
        self.sdr_rf_value_label = QLabel("", controls_widget)
        self.sdr_rf_gain_slider = QSlider(Qt.Horizontal, controls_widget)
        self.sdr_rf_gain_slider.setRange(0, 27)

        self.sdr_agc_check = QCheckBox("AGC", controls_widget)
        self.sdr_auto_table_check = QCheckBox("Auto Table", controls_widget)
        self.sdr_auto_table_check.setChecked(True)
        self.sdr_auto_table_button = QPushButton("Edit Table...", controls_widget)
        self.sdr_bias_tee_check = QCheckBox("Bias Tee (Ant B)", controls_widget)
        self.sdr_fm_notch_check = QCheckBox("FM Notch", controls_widget)
        self.sdr_dab_notch_check = QCheckBox("DAB Notch", controls_widget)

        self.sdr_ppm_spin = QDoubleSpinBox(controls_widget)
        self.sdr_ppm_spin.setDecimals(2)
        self.sdr_ppm_spin.setRange(-500.0, 500.0)
        self.sdr_ppm_spin.setSingleStep(0.1)

        self.sdr_snr_mode_button = QPushButton(controls_widget)
        self.sdr_snr_mode_button.setCheckable(True)
        self.sdr_snr_mode_button.setMinimumWidth(84)
        self.sdr_snr_zero_button = QPushButton("Zero", controls_widget)
        self.sdr_snr_zero_button.setMinimumWidth(48)
        self.sdr_snr_average_spin = QDoubleSpinBox(controls_widget)
        self.sdr_snr_average_spin.setDecimals(1)
        self.sdr_snr_average_spin.setRange(0.1, 30.0)
        self.sdr_snr_average_spin.setSingleStep(0.1)
        self.sdr_snr_average_spin.setSuffix(" s")
        self.sdr_level_meter = LevelMeterWidget(controls_widget)
        self.sdr_level_meter.set_unit(self._sdr_level_unit)
        self.sdr_display_fps_label = QLabel("FPS: -", controls_widget)
        self.sdr_display_fps_label.setStyleSheet("color: #707070; font-size: 10px;")
        self.sdr_fft_window_label = QLabel("FFT: -", controls_widget)
        self.sdr_fft_window_label.setStyleSheet("color: #707070; font-size: 10px;")

        self.sdr_run_toggle_button = QPushButton("Start", controls_widget)
        self.sdr_run_toggle_button.setCheckable(True)
        self.sdr_run_toggle_button.setMinimumWidth(80)

        self.sdr_frequency_control = FrequencyControlWidget(controls_widget)
        self.sdr_frequency_control.set_range_hz(1_000.0, 6_000_000_000.0)

        self.sdr_bandwidth_spin = QDoubleSpinBox(controls_widget)
        self.sdr_bandwidth_spin.setDecimals(1)
        self.sdr_bandwidth_spin.setRange(0.1, 5000.0)
        self.sdr_bandwidth_spin.setSingleStep(0.1)
        self.sdr_bandwidth_spin.setSuffix(" kHz")

        top_groups_widget = QWidget(controls_widget)
        top_groups_layout = QHBoxLayout(top_groups_widget)
        top_groups_layout.setContentsMargins(0, 0, 0, 0)
        top_groups_layout.setSpacing(8)

        source_group = QGroupBox("Source", top_groups_widget)
        source_layout = QGridLayout(source_group)
        source_layout.setContentsMargins(8, 10, 8, 8)
        source_layout.setHorizontalSpacing(8)
        source_layout.setVerticalSpacing(6)
        source_layout.addWidget(QLabel("Source", source_group), 0, 0)
        source_layout.addWidget(self.sdr_source_combo, 0, 1)
        source_layout.addWidget(QLabel("Sampling", source_group), 0, 2)
        source_layout.addWidget(self.sdr_sample_rate_combo, 0, 3)
        source_layout.addWidget(QLabel("Antenna", source_group), 0, 4)
        source_layout.addWidget(self.sdr_antenna_combo, 0, 5)
        source_layout.addWidget(self.sdr_if_gain_label, 1, 0)
        source_layout.addWidget(self.sdr_if_value_label, 1, 1)
        source_layout.addWidget(self.sdr_if_gain_slider, 1, 2, 1, 2)
        source_layout.addWidget(self.sdr_rf_gain_label, 1, 4)
        source_layout.addWidget(self.sdr_rf_value_label, 1, 5)
        source_layout.addWidget(self.sdr_rf_gain_slider, 1, 6, 1, 2)
        source_layout.addWidget(self.sdr_auto_table_check, 2, 0)
        source_layout.addWidget(self.sdr_auto_table_button, 2, 1)
        source_layout.addWidget(self.sdr_fm_notch_check, 2, 2)
        source_layout.addWidget(self.sdr_dab_notch_check, 2, 3)
        source_layout.addWidget(self.sdr_agc_check, 2, 4)
        source_layout.addWidget(self.sdr_bias_tee_check, 2, 5)
        source_layout.addWidget(QLabel("Corr. [ppm]", source_group), 2, 6)
        source_layout.addWidget(self.sdr_ppm_spin, 2, 7)

        fft_group = QGroupBox("FFT", top_groups_widget)
        fft_layout = QGridLayout(fft_group)
        fft_layout.setContentsMargins(8, 10, 8, 8)
        fft_layout.setHorizontalSpacing(8)
        fft_layout.setVerticalSpacing(6)
        fft_layout.addWidget(self.sdr_trace_avg_label, 0, 0)
        fft_layout.addWidget(self.sdr_trace_avg_combo, 0, 1)
        fft_layout.addWidget(QLabel("FFT Size", fft_group), 0, 2)
        fft_layout.addWidget(self.sdr_fft_size_combo, 0, 3)
        fft_layout.addWidget(QLabel("Refresh", fft_group), 1, 0)
        fft_layout.addWidget(self.sdr_refresh_rate_combo, 1, 1)
        fft_layout.addWidget(QLabel("Units", fft_group), 1, 2)
        fft_layout.addWidget(self.sdr_power_unit_combo, 1, 3)
        fft_layout.addWidget(QLabel("View", fft_group), 2, 0)
        fft_layout.addWidget(self.sdr_view_mode_combo, 2, 1)
        fft_layout.addWidget(self.sdr_display_fps_label, 2, 2)
        fft_layout.addWidget(self.sdr_fft_window_label, 2, 3)

        top_groups_layout.addWidget(source_group, 3)
        top_groups_layout.addWidget(fft_group, 2)
        controls_layout.addWidget(top_groups_widget)

        center_band_widget = QWidget(controls_widget)
        center_band_widget.setMinimumHeight(94)
        center_band_layout = QHBoxLayout(center_band_widget)
        center_band_layout.setContentsMargins(0, 0, 0, 0)
        center_band_layout.setSpacing(10)
        center_band_layout.setAlignment(Qt.AlignVCenter)

        freq_panel = QWidget(center_band_widget)
        freq_panel_layout = QVBoxLayout(freq_panel)
        freq_panel_layout.setContentsMargins(0, 0, 0, 0)
        freq_panel_layout.setSpacing(2)
        freq_title = QLabel("SDR Center Frequency", freq_panel)
        freq_title.setStyleSheet("font-weight: 600;")
        freq_panel_layout.addWidget(freq_title)
        freq_panel_layout.addWidget(self.sdr_frequency_control)

        measure_panel = QWidget(center_band_widget)
        measure_layout = QGridLayout(measure_panel)
        measure_layout.setContentsMargins(0, 0, 0, 0)
        measure_layout.setHorizontalSpacing(8)
        measure_layout.setVerticalSpacing(2)
        measure_layout.addWidget(QLabel("Bandwidth", measure_panel), 0, 0)
        measure_layout.addWidget(self.sdr_bandwidth_spin, 0, 1)
        measure_layout.addWidget(QLabel("Noise avg.", measure_panel), 0, 2)
        measure_layout.addWidget(self.sdr_snr_average_spin, 0, 3)
        measure_layout.addWidget(self.sdr_snr_mode_button, 0, 4)
        measure_layout.addWidget(self.sdr_snr_zero_button, 0, 5)
        measure_layout.addWidget(self.sdr_run_toggle_button, 0, 6)
        measure_layout.addWidget(self.sdr_level_meter, 1, 0, 1, 7)
        measure_layout.setColumnStretch(3, 1)

        center_band_layout.addWidget(freq_panel, 3, Qt.AlignVCenter)
        center_band_layout.addWidget(measure_panel, 2, Qt.AlignVCenter)
        controls_layout.addWidget(center_band_widget)

        splitter = QSplitter(Qt.Vertical, container)
        spectrum_panel = QWidget(splitter)
        spectrum_panel_layout = QHBoxLayout(spectrum_panel)
        spectrum_panel_layout.setContentsMargins(0, 0, 0, 0)
        spectrum_panel_layout.setSpacing(8)
        self.sdr_spectrum_plot = SpectrumPlotWidget(spectrum_panel)
        spectrum_panel_layout.addWidget(self.sdr_spectrum_plot, 1)

        spectrum_scale_box = QWidget(spectrum_panel)
        spectrum_scale_box.setFixedWidth(80)
        spectrum_scale_layout = QVBoxLayout(spectrum_scale_box)
        spectrum_scale_layout.setContentsMargins(0, 0, 0, 0)
        spectrum_scale_layout.setSpacing(4)
        spectrum_scale_layout.addWidget(QLabel("Spec Min", spectrum_scale_box))
        self.sdr_spectrum_min_slider = QSlider(Qt.Vertical, spectrum_scale_box)
        self.sdr_spectrum_min_slider.setRange(-160, 20)
        self.sdr_spectrum_min_slider.setToolTip("Spectrum Y min")
        spectrum_scale_layout.addWidget(self.sdr_spectrum_min_slider, 1)
        spectrum_scale_layout.addWidget(QLabel("Spec Max", spectrum_scale_box))
        self.sdr_spectrum_range_slider = QSlider(Qt.Vertical, spectrum_scale_box)
        self.sdr_spectrum_range_slider.setRange(-140, 80)
        self.sdr_spectrum_range_slider.setToolTip("Spectrum and waterfall Y max")
        spectrum_scale_layout.addWidget(self.sdr_spectrum_range_slider, 1)
        spectrum_panel_layout.addWidget(spectrum_scale_box)

        waterfall_panel = QWidget(splitter)
        waterfall_panel_layout = QHBoxLayout(waterfall_panel)
        waterfall_panel_layout.setContentsMargins(0, 0, 0, 0)
        waterfall_panel_layout.setSpacing(8)
        self.sdr_waterfall_plot = WaterfallPlotWidget(waterfall_panel)
        waterfall_panel_layout.addWidget(self.sdr_waterfall_plot, 1)

        waterfall_scale_box = QWidget(waterfall_panel)
        waterfall_scale_box.setFixedWidth(80)
        waterfall_scale_layout = QVBoxLayout(waterfall_scale_box)
        waterfall_scale_layout.setContentsMargins(0, 0, 0, 0)
        waterfall_scale_layout.setSpacing(4)
        waterfall_scale_layout.addWidget(QLabel("WF Avg", waterfall_scale_box))
        self.sdr_waterfall_stride_slider = QSlider(Qt.Vertical, waterfall_scale_box)
        self.sdr_waterfall_stride_slider.setRange(1, 20)
        self.sdr_waterfall_stride_slider.setToolTip("Waterfall line averaging")
        waterfall_scale_layout.addWidget(self.sdr_waterfall_stride_slider, 1)
        waterfall_panel_layout.addWidget(waterfall_scale_box)

        splitter.addWidget(spectrum_panel)
        splitter.addWidget(waterfall_panel)
        splitter.setSizes([620, 360])
        self.sdr_waterfall_plot.plot.setXLink(self.sdr_spectrum_plot.plot)

        root_layout.addWidget(controls_widget)
        root_layout.addWidget(splitter, 1)

        self.sdr_client.data_storage.data_updated.connect(self.sdr_spectrum_plot.update_plot)
        self.sdr_client.data_storage.data_updated.connect(self._update_sdr_snr_display)
        self.sdr_client.data_storage.data_recalculated.connect(self.sdr_spectrum_plot.recalculate_plot)
        self.sdr_client.data_storage.data_recalculated.connect(self._update_sdr_snr_display)
        self.sdr_client.data_storage.history_updated.connect(self.sdr_waterfall_plot.update_plot)
        self.sdr_client.data_storage.history_recalculated.connect(self.sdr_waterfall_plot.recalculate_plot)
        self.sdr_spectrum_plot.visible_span_changed.connect(self.sdr_client.update_fft_for_view)
        self.sdr_spectrum_plot.selection_frequency_changed.connect(self._on_sdr_overlay_frequency_changed)
        self.sdr_spectrum_plot.selection_bandwidth_changed.connect(self._on_sdr_overlay_bandwidth_changed)
        self.sdr_spectrum_plot.frequency_clicked.connect(self._on_sdr_plot_frequency_clicked)
        self.sdr_waterfall_plot.frequency_clicked.connect(self._on_sdr_plot_frequency_clicked)
        self.sdr_client.status.connect(lambda message: self.status_bar.showMessage(message, 3000))
        self.sdr_client.error.connect(lambda message: self.status_bar.showMessage(f"SDR: {message}", 5000))
        self.sdr_client.perf_updated.connect(self._on_sdr_perf_updated)
        self.sdr_client.settings_changed.connect(self._refresh_sdr_controls)
        self.sdr_client.started.connect(self._refresh_sdr_run_buttons)
        self.sdr_client.stopped.connect(self._refresh_sdr_run_buttons)

        self.sdr_run_toggle_button.clicked.connect(self._on_sdr_run_toggle_clicked)
        self.sdr_source_combo.currentIndexChanged.connect(self._on_sdr_source_changed)
        self.sdr_sample_rate_combo.currentIndexChanged.connect(self._on_sdr_sample_rate_changed)
        self.sdr_antenna_combo.currentTextChanged.connect(self._on_sdr_antenna_changed)
        self.sdr_fft_size_combo.currentIndexChanged.connect(self._on_sdr_fft_size_changed)
        self.sdr_refresh_rate_combo.currentIndexChanged.connect(self._on_sdr_refresh_rate_changed)
        self.sdr_power_unit_combo.currentIndexChanged.connect(self._on_sdr_power_unit_changed)
        self.sdr_view_mode_combo.currentIndexChanged.connect(self._on_sdr_view_mode_changed)
        self.sdr_if_gain_slider.valueChanged.connect(self._on_sdr_if_gain_changed)
        self.sdr_rf_gain_slider.valueChanged.connect(self._on_sdr_rf_gain_changed)
        self.sdr_trace_avg_combo.currentTextChanged.connect(self._on_sdr_smoothing_changed)
        self.sdr_agc_check.toggled.connect(self._on_sdr_agc_changed)
        self.sdr_auto_table_check.toggled.connect(self._on_sdr_auto_table_toggled)
        self.sdr_auto_table_button.clicked.connect(self._show_auto_gain_dialog)
        self.sdr_bias_tee_check.toggled.connect(self._on_sdr_bias_tee_changed)
        self.sdr_fm_notch_check.toggled.connect(self._on_sdr_fm_notch_changed)
        self.sdr_dab_notch_check.toggled.connect(self._on_sdr_dab_notch_changed)
        self.sdr_ppm_spin.valueChanged.connect(self._on_sdr_ppm_changed)
        self.sdr_frequency_control.valueChanged.connect(self._on_sdr_frequency_control_changed)
        self.sdr_bandwidth_spin.valueChanged.connect(self._on_sdr_bandwidth_spin_changed)
        self.sdr_snr_average_spin.valueChanged.connect(self._on_sdr_snr_average_changed)
        self.sdr_snr_mode_button.clicked.connect(self._on_sdr_snr_mode_button_clicked)
        self.sdr_snr_zero_button.clicked.connect(self._on_sdr_snr_zero_clicked)
        self.sdr_level_meter.unitChanged.connect(self._on_sdr_level_unit_changed)
        self.sdr_spectrum_min_slider.valueChanged.connect(self._on_sdr_spectrum_min_changed)
        self.sdr_spectrum_range_slider.valueChanged.connect(self._on_sdr_spectrum_range_changed)
        self.sdr_waterfall_stride_slider.valueChanged.connect(self._on_sdr_waterfall_stride_changed)

        self._apply_sdr_plot_scales()

        self._refresh_sdr_controls(self.sdr_client.snapshot_state())
        self._refresh_sdr_run_buttons()
        if self.sdr_auto_table_check.isChecked():
            self._apply_auto_gain_profile()
        if self._sdr_snr_mode == "relative":
            self._capture_sdr_relative_reference()

    def _clear_layout(self, layout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _persist_sdr_value(self, key: str, value) -> None:
        update_and_persist_setting(self.settings, "SDR", key, value)

    def _sdr_settings(self) -> dict:
        if not isinstance(self.settings, dict):
            return {}
        return self.settings.get("SDR", self.settings.get("sdr", {})) or {}

    @staticmethod
    def _coerce_float(value, default: float, *, minimum: float | None = None, maximum: float | None = None) -> float:
        try:
            result = float(value)
        except Exception:
            result = float(default)
        if minimum is not None:
            result = max(float(minimum), result)
        if maximum is not None:
            result = min(float(maximum), result)
        return result

    def _set_slider_value(self, slider: QSlider, value: int) -> None:
        try:
            slider.blockSignals(True)
            slider.setValue(int(value))
        finally:
            slider.blockSignals(False)

    def _refresh_sdr_run_buttons(self) -> None:
        running = bool(getattr(self.sdr_client, "running", False))
        try:
            self.sdr_run_toggle_button.blockSignals(True)
            self.sdr_run_toggle_button.setChecked(running)
            self.sdr_run_toggle_button.setText("Stop" if running else "Start")
        finally:
            self.sdr_run_toggle_button.blockSignals(False)

    def _refresh_sdr_controls(self, snapshot: dict) -> None:
        self._populate_source_combo(snapshot)
        self._populate_antenna_combo(snapshot)
        self._populate_sample_rate_combo(snapshot)
        self._select_sample_rate(snapshot)
        self._populate_fft_size_combo(snapshot)
        self._populate_refresh_rate_combo(snapshot)
        self._populate_power_unit_combo()
        self._populate_view_mode_combo()
        self.sdr_level_meter.set_unit(self._sdr_level_unit)

        try:
            self.sdr_frequency_control.blockSignals(True)
            self.sdr_frequency_control.set_value_hz(float(snapshot.get("center_freq_hz", 0.0)))
        finally:
            self.sdr_frequency_control.blockSignals(False)

        center_freq_hz = float(snapshot.get("center_freq_hz", 137_000_000.0))
        receiver_freq_hz = float(snapshot.get("receiver_freq_hz", center_freq_hz))
        sample_rate_hz = float(snapshot.get("sample_rate_hz", getattr(self.sdr_client, "sample_rate", 2_000_000.0)))

        try:
            self.sdr_bandwidth_spin.blockSignals(True)
            self.sdr_bandwidth_spin.setMaximum(float(max(0.1, sample_rate_hz * 0.5 / 1e3)))
            self.sdr_bandwidth_spin.setValue(float(snapshot.get("bandwidth_hz", 25_000.0)) / 1e3)
        finally:
            self.sdr_bandwidth_spin.blockSignals(False)

        try:
            self.sdr_ppm_spin.blockSignals(True)
            self.sdr_ppm_spin.setValue(float(snapshot.get("ppm", 0.0)))
        finally:
            self.sdr_ppm_spin.blockSignals(False)

        try:
            self.sdr_agc_check.blockSignals(True)
            self.sdr_agc_check.setChecked(bool(snapshot.get("agc", False)))
        finally:
            self.sdr_agc_check.blockSignals(False)

        try:
            self.sdr_auto_table_check.blockSignals(True)
            self.sdr_auto_table_check.setChecked(bool(snapshot.get("auto_table", True)))
        finally:
            self.sdr_auto_table_check.blockSignals(False)

        try:
            self.sdr_bias_tee_check.blockSignals(True)
            self.sdr_bias_tee_check.setChecked(bool(snapshot.get("bias_tee", False)))
        finally:
            self.sdr_bias_tee_check.blockSignals(False)

        try:
            self.sdr_fm_notch_check.blockSignals(True)
            self.sdr_fm_notch_check.setChecked(bool(snapshot.get("fm_notch", False)))
        finally:
            self.sdr_fm_notch_check.blockSignals(False)

        try:
            self.sdr_dab_notch_check.blockSignals(True)
            self.sdr_dab_notch_check.setChecked(bool(snapshot.get("dab_notch", False)))
        finally:
            self.sdr_dab_notch_check.blockSignals(False)

        try:
            self.sdr_trace_avg_combo.blockSignals(True)
            self.sdr_trace_avg_combo.setCurrentText(str(snapshot.get("smoothing", "light")).title())
        finally:
            self.sdr_trace_avg_combo.blockSignals(False)

        self._update_rf_slider_range()
        self._set_slider_value(self.sdr_if_gain_slider, int(snapshot.get("if_gain", 0)))
        self._set_slider_value(self.sdr_rf_gain_slider, int(snapshot.get("rf_gain", 0)))
        self._update_gain_labels()
        self._update_gain_controls_enabled()
        self.sdr_bias_tee_check.setEnabled(self.sdr_antenna_combo.currentText() == "Antenna B")
        self.sdr_auto_gain_dialog.widget.set_current_frequency(float(snapshot.get("center_freq_hz", 137_000_000.0)))
        self.sdr_spectrum_plot.set_max_selection_bandwidth_hz(sample_rate_hz * 0.5)
        self.sdr_spectrum_plot.set_receiver_selection(
            receiver_freq_hz,
            float(snapshot.get("bandwidth_hz", 25_000.0)),
        )
        self.sdr_spectrum_plot.set_max_visible_span_hz(sample_rate_hz)
        sample_rate_changed = (
            self._sdr_last_view_sample_rate_hz is None
            or abs(sample_rate_hz - float(self._sdr_last_view_sample_rate_hz)) > 1.0
        )
        self._apply_sdr_power_unit_to_plots(snapshot)
        if sample_rate_changed or (
            self._sdr_view_mode == "center" and (
                self._sdr_last_view_center_hz is None
                or abs(center_freq_hz - float(self._sdr_last_view_center_hz)) > 1.0
            )
        ):
            self.sdr_spectrum_plot.center_view_on_frequency(center_freq_hz, default_span_hz=sample_rate_hz)
            self._sdr_last_view_center_hz = center_freq_hz
            self._sdr_last_view_sample_rate_hz = sample_rate_hz
        self._sdr_snr_mode = str(snapshot.get("snr_mode", self._sdr_snr_mode)).strip().lower()
        if self._sdr_snr_mode not in {"absolute", "relative"}:
            self._sdr_snr_mode = "absolute"
        self._update_sdr_snr_mode_button()
        try:
            self.sdr_snr_average_spin.blockSignals(True)
            self.sdr_snr_average_spin.setValue(float(self._sdr_snr_integration_s))
        finally:
            self.sdr_snr_average_spin.blockSignals(False)
        self._sync_plot_scale_controls()
        self._update_sdr_snr_display()
        self._refresh_sdr_run_buttons()

    def _populate_source_combo(self, snapshot: dict) -> None:
        sources = list(snapshot.get("sources", []))
        current_index = int(snapshot.get("source_index", 0))
        try:
            self.sdr_source_combo.blockSignals(True)
            self.sdr_source_combo.clear()
            for index, source in enumerate(sources):
                self.sdr_source_combo.addItem(str(source.get("label", f"Source {index + 1}")), index)
            if self.sdr_source_combo.count():
                self.sdr_source_combo.setCurrentIndex(max(0, min(current_index, self.sdr_source_combo.count() - 1)))
        finally:
            self.sdr_source_combo.blockSignals(False)

    def _populate_antenna_combo(self, snapshot: dict) -> None:
        antennas = [str(item) for item in snapshot.get("antennas", [])]
        current_antenna = str(snapshot.get("antenna", ""))
        try:
            self.sdr_antenna_combo.blockSignals(True)
            self.sdr_antenna_combo.clear()
            self.sdr_antenna_combo.addItems(antennas)
            if current_antenna:
                self.sdr_antenna_combo.setCurrentText(current_antenna)
        finally:
            self.sdr_antenna_combo.blockSignals(False)

    def _populate_sample_rate_combo(self, snapshot: dict) -> None:
        supported_rates: list[float] = []
        for raw_rate in snapshot.get("sample_rates", []):
            try:
                rate_hz = float(raw_rate)
            except (TypeError, ValueError):
                continue
            if rate_hz > 0.0:
                supported_rates.append(rate_hz)
        if not supported_rates:
            supported_rates = [float(rate_hz) for rate_hz in SAMPLE_RATE_PRESETS_HZ]
        supported_rates = sorted(set(supported_rates))
        try:
            self.sdr_sample_rate_combo.blockSignals(True)
            current_value = self.sdr_sample_rate_combo.currentData()
            self.sdr_sample_rate_combo.clear()
            selected_index = 0
            for index, rate_hz in enumerate(supported_rates):
                self.sdr_sample_rate_combo.addItem(f"{rate_hz / 1e6:.3f} MHz", float(rate_hz))
                if current_value is not None and abs(float(rate_hz) - float(current_value)) < 1.0:
                    selected_index = index
            if self.sdr_sample_rate_combo.count():
                self.sdr_sample_rate_combo.setCurrentIndex(selected_index)
        finally:
            self.sdr_sample_rate_combo.blockSignals(False)

    def _select_sample_rate(self, snapshot: dict) -> None:
        target = float(snapshot.get("sample_rate_hz", 2_000_000.0))
        best_index = 0
        best_error = None
        try:
            self.sdr_sample_rate_combo.blockSignals(True)
            for index in range(self.sdr_sample_rate_combo.count()):
                value = float(self.sdr_sample_rate_combo.itemData(index))
                error = abs(value - target)
                if best_error is None or error < best_error:
                    best_error = error
                    best_index = index
            self.sdr_sample_rate_combo.setCurrentIndex(best_index)
        finally:
            self.sdr_sample_rate_combo.blockSignals(False)

    def _populate_fft_size_combo(self, snapshot: dict) -> None:
        current_fft = int(snapshot.get("fft_size", self.sdr_client.fft_size))
        mode = str(snapshot.get("fft_size_mode", getattr(self.sdr_client, "fft_size_mode", "auto"))).strip().lower()
        try:
            self.sdr_fft_size_combo.blockSignals(True)
            self.sdr_fft_size_combo.clear()
            self.sdr_fft_size_combo.addItem(f"Auto ({current_fft})", None)
            selected_index = 0
            for size in self.sdr_client.available_fft_sizes():
                self.sdr_fft_size_combo.addItem(str(int(size)), int(size))
                if mode == "manual" and int(size) == current_fft:
                    selected_index = self.sdr_fft_size_combo.count() - 1
            self.sdr_fft_size_combo.setCurrentIndex(selected_index)
        finally:
            self.sdr_fft_size_combo.blockSignals(False)

    def _populate_refresh_rate_combo(self, snapshot: dict) -> None:
        target = snapshot.get("plot_refresh_fps", getattr(self.sdr_client, "plot_refresh_fps", None))
        try:
            self.sdr_refresh_rate_combo.blockSignals(True)
            selected_index = 0
            if target is not None:
                target_fps = float(target)
                for index in range(self.sdr_refresh_rate_combo.count()):
                    value = self.sdr_refresh_rate_combo.itemData(index)
                    if value is None:
                        continue
                    if abs(float(value) - target_fps) < 0.1:
                        selected_index = index
                        break
            self.sdr_refresh_rate_combo.setCurrentIndex(selected_index)
        finally:
            self.sdr_refresh_rate_combo.blockSignals(False)

    def _populate_power_unit_combo(self) -> None:
        try:
            self.sdr_power_unit_combo.blockSignals(True)
            self.sdr_power_unit_combo.setCurrentIndex(1 if self._sdr_power_unit == "db_per_hz" else 0)
        finally:
            self.sdr_power_unit_combo.blockSignals(False)

    def _populate_view_mode_combo(self) -> None:
        try:
            self.sdr_view_mode_combo.blockSignals(True)
            index = 0 if self._sdr_view_mode == "center" else 1
            self.sdr_view_mode_combo.setCurrentIndex(index)
        finally:
            self.sdr_view_mode_combo.blockSignals(False)

    def _current_sdr_bin_width_hz(self, snapshot: dict | None = None) -> float:
        source = snapshot or self.sdr_client.snapshot_state()
        sample_rate_hz = float(source.get("sample_rate_hz", getattr(self.sdr_client, "sample_rate", 1.0)))
        fft_size = int(source.get("fft_size", getattr(self.sdr_client, "fft_size", 1)))
        return float(max(1e-12, sample_rate_hz / max(1, fft_size)))

    def _apply_sdr_power_unit_to_plots(self, snapshot: dict | None = None) -> None:
        bin_width_hz = self._current_sdr_bin_width_hz(snapshot)
        self.sdr_spectrum_plot.set_bin_width_hz(bin_width_hz)
        self.sdr_spectrum_plot.set_power_unit(self._sdr_power_unit)
        self.sdr_waterfall_plot.set_bin_width_hz(bin_width_hz)
        self.sdr_waterfall_plot.set_power_unit(self._sdr_power_unit)

    def _update_rf_slider_range(self) -> None:
        center_freq_hz = float(self.sdr_client.center_freq)
        max_state = int(max_lna_state_for_frequency(center_freq_hz))
        self.sdr_rf_gain_slider.setMinimum(0)
        self.sdr_rf_gain_slider.setMaximum(max_state)

    def _update_gain_labels(self) -> None:
        center_freq_hz = float(self.sdr_client.center_freq)
        lna_state = clamp_lna_state(center_freq_hz, self.sdr_client.rf_gain)
        lna_attn_db = lna_attenuation_db(center_freq_hz, lna_state)
        if_attn_db = int(self.sdr_client.if_gain)
        self.sdr_if_value_label.setText(f"IF attn: {if_attn_db} dB")
        self.sdr_rf_value_label.setText(f"LNA attn: {lna_attn_db} dB (state {lna_state})")

    def _update_gain_controls_enabled(self) -> None:
        auto_table = self.sdr_auto_table_check.isChecked()
        agc = self.sdr_agc_check.isChecked()
        self.sdr_agc_check.setEnabled(not auto_table)
        self.sdr_if_gain_slider.setEnabled((not auto_table) and (not agc))
        self.sdr_rf_gain_slider.setEnabled(not auto_table)

    def _apply_pending_sdr_frequency(self) -> None:
        if self._pending_sdr_freq_mhz is None:
            return
        frequency_hz = float(self._pending_sdr_freq_mhz) * 1e6
        if self._sdr_view_mode == "center":
            self.sdr_client.set_frequency(frequency_hz)
            self._persist_sdr_value("CENTER_FREQ_HZ", int(round(frequency_hz)))
            self._persist_sdr_value("RECEIVER_FREQ_HZ", int(round(frequency_hz)))
        else:
            self.sdr_client.set_center_frequency(frequency_hz)
            self._persist_sdr_value("CENTER_FREQ_HZ", int(round(frequency_hz)))
        self.sdr_auto_gain_dialog.widget.set_current_frequency(frequency_hz)
        self._update_rf_slider_range()
        self._update_gain_labels()
        if self.sdr_auto_table_check.isChecked():
            self._apply_auto_gain_profile()
        self.sdr_spectrum_plot.center_view_on_frequency(frequency_hz, default_span_hz=float(self.sdr_client.sample_rate))
        self._sdr_last_view_center_hz = frequency_hz
        self._sdr_last_view_sample_rate_hz = float(self.sdr_client.sample_rate)
        self._pending_sdr_freq_mhz = None

    def start_sdr_stream(self) -> None:
        self.sdr_client.start()
        self._refresh_sdr_run_buttons()

    def stop_sdr_stream(self) -> None:
        self.sdr_client.stop()
        self._refresh_sdr_run_buttons()

    def _on_sdr_run_toggle_clicked(self, checked: bool) -> None:
        if checked:
            self.start_sdr_stream()
        else:
            self.stop_sdr_stream()

    def _on_sdr_source_changed(self, index: int) -> None:
        if index < 0:
            return
        self.sdr_client.set_source_index(index)
        snapshot = self.sdr_client.snapshot_state()
        self._persist_sdr_value("SOURCE_INDEX", int(snapshot.get("source_index", 0)))
        self._persist_sdr_value("SOURCE_KEY", str(snapshot.get("source_key", "")))

    def _on_sdr_sample_rate_changed(self, index: int) -> None:
        value = self.sdr_sample_rate_combo.itemData(index)
        if value is None:
            return
        self.sdr_client.set_sample_rate(float(value))
        max_bandwidth_hz = float(self.sdr_client.bandwidth_hz)
        try:
            self.sdr_bandwidth_spin.blockSignals(True)
            self.sdr_bandwidth_spin.setMaximum(float(max(0.1, self.sdr_client.sample_rate * 0.5 / 1e3)))
            self.sdr_bandwidth_spin.setValue(float(max_bandwidth_hz / 1e3))
        finally:
            self.sdr_bandwidth_spin.blockSignals(False)
        self.sdr_spectrum_plot.set_max_selection_bandwidth_hz(float(self.sdr_client.sample_rate) * 0.5)
        self.sdr_spectrum_plot.set_receiver_selection(
            float(getattr(self.sdr_client, "receiver_freq_hz", self.sdr_client.center_freq)),
            float(self.sdr_client.bandwidth_hz),
        )
        self._persist_sdr_value("SAMPLE_RATE_HZ", int(round(float(self.sdr_client.sample_rate))))

    def _on_sdr_fft_size_changed(self, index: int) -> None:
        value = self.sdr_fft_size_combo.itemData(index)
        if value is None:
            self.sdr_client.set_fft_size(None)
            self._persist_sdr_value("FFT_SIZE_MODE", "auto")
            return
        self.sdr_client.set_fft_size(int(value))
        self._persist_sdr_value("FFT_SIZE_MODE", "manual")
        self._persist_sdr_value("FFT_SIZE", int(value))

    def _on_sdr_refresh_rate_changed(self, index: int) -> None:
        value = self.sdr_refresh_rate_combo.itemData(index)
        if value is None:
            self.sdr_client.set_plot_refresh_fps(None)
            self._persist_sdr_value("PLOT_REFRESH_FPS", "auto")
            return
        self.sdr_client.set_plot_refresh_fps(float(value))
        self._persist_sdr_value("PLOT_REFRESH_FPS", float(value))

    def _on_sdr_power_unit_changed(self, index: int) -> None:
        value = self.sdr_power_unit_combo.itemData(index)
        target_unit = "db_per_hz" if str(value).strip().lower() == "db_per_hz" else "db_per_bin"
        if target_unit == self._sdr_power_unit:
            return
        offset_db = bin_width_to_density_offset_db(self._current_sdr_bin_width_hz())
        if target_unit == "db_per_hz":
            self._sdr_spectrum_y_min_db -= offset_db
        else:
            self._sdr_spectrum_y_min_db += offset_db
        self._sdr_power_unit = target_unit
        self._persist_sdr_value("POWER_UNIT", self._sdr_power_unit)
        self._apply_sdr_power_unit_to_plots()
        self._apply_sdr_plot_scales()
        self.sdr_spectrum_plot.recalculate_plot(self.sdr_client.data_storage)
        self.sdr_waterfall_plot.recalculate_plot(self.sdr_client.data_storage)
        self._update_sdr_snr_display()

    def _on_sdr_view_mode_changed(self, index: int) -> None:
        value = self.sdr_view_mode_combo.itemData(index)
        self._sdr_view_mode = "fix" if str(value).strip().lower() == "fix" else "center"
        self._persist_sdr_value("VIEW_MODE", self._sdr_view_mode)
        if self._sdr_view_mode == "center":
            center_freq_hz = float(self.sdr_client.center_freq)
            sample_rate_hz = float(self.sdr_client.sample_rate)
            self.sdr_spectrum_plot.center_view_on_frequency(center_freq_hz, default_span_hz=sample_rate_hz)
            self._sdr_last_view_center_hz = center_freq_hz
            self._sdr_last_view_sample_rate_hz = sample_rate_hz

    def _on_sdr_antenna_changed(self, value: str) -> None:
        if not value:
            return
        self.sdr_client.set_antenna(value)
        self.sdr_bias_tee_check.setEnabled(value == "Antenna B")
        self._persist_sdr_value("ANTENNA", value)

    def _on_sdr_if_gain_changed(self, value: int) -> None:
        if self.sdr_auto_table_check.isChecked():
            return
        self.sdr_client.update_if_gain(int(value))
        self._persist_sdr_value("IF_GAIN", int(value))
        self._update_gain_labels()

    def _on_sdr_rf_gain_changed(self, value: int) -> None:
        if self.sdr_auto_table_check.isChecked():
            return
        clamped = clamp_lna_state(self.sdr_client.center_freq, int(value))
        if clamped != int(value):
            self._set_slider_value(self.sdr_rf_gain_slider, clamped)
        self.sdr_client.update_rf_gain(clamped)
        self._persist_sdr_value("RF_GAIN", int(clamped))
        self._update_gain_labels()

    def _on_sdr_smoothing_changed(self, value: str) -> None:
        level = str(value).strip().lower()
        self.sdr_client.set_smoothing(level)
        self._persist_sdr_value("SMOOTHING", level)

    def _on_sdr_agc_changed(self, checked: bool) -> None:
        if self.sdr_auto_table_check.isChecked() and checked:
            try:
                self.sdr_agc_check.blockSignals(True)
                self.sdr_agc_check.setChecked(False)
            finally:
                self.sdr_agc_check.blockSignals(False)
            checked = False
        self.sdr_client.update_agc(bool(checked))
        self._persist_sdr_value("AGC", bool(checked))
        self._update_gain_controls_enabled()

    def _on_sdr_auto_table_toggled(self, checked: bool) -> None:
        self.sdr_client.set_auto_table_enabled(bool(checked))
        self._persist_sdr_value("AUTO_TABLE", bool(checked))
        if checked:
            try:
                self.sdr_agc_check.blockSignals(True)
                self.sdr_agc_check.setChecked(False)
            finally:
                self.sdr_agc_check.blockSignals(False)
            self.sdr_client.update_agc(False)
            self._persist_sdr_value("AGC", False)
            self._apply_auto_gain_profile()
        self._update_gain_controls_enabled()

    def _show_auto_gain_dialog(self) -> None:
        self.sdr_auto_gain_dialog.show()
        self.sdr_auto_gain_dialog.raise_()
        self.sdr_auto_gain_dialog.activateWindow()

    def _on_sdr_bias_tee_changed(self, checked: bool) -> None:
        self.sdr_client.set_bias_tee(bool(checked))
        self._persist_sdr_value("BIAS_TEE", bool(checked))

    def _on_sdr_fm_notch_changed(self, checked: bool) -> None:
        self.sdr_client.set_fm_notch(bool(checked))
        self._persist_sdr_value("FM_NOTCH", bool(checked))

    def _on_sdr_dab_notch_changed(self, checked: bool) -> None:
        self.sdr_client.set_dab_notch(bool(checked))
        self._persist_sdr_value("DAB_NOTCH", bool(checked))

    def _on_sdr_ppm_changed(self, value: float) -> None:
        self.sdr_client.set_ppm(float(value))
        self._persist_sdr_value("PPM", float(value))

    def _on_sdr_snr_average_changed(self, value: float) -> None:
        self._sdr_snr_integration_s = self._coerce_float(value, 2.0, minimum=0.1)
        self._persist_sdr_value("SNR_INTEGRATION_S", self._sdr_snr_integration_s)
        self._update_sdr_snr_display()

    def _on_sdr_snr_mode_button_clicked(self, checked: bool) -> None:
        target_mode = "relative" if checked else "absolute"
        self._set_sdr_snr_mode(target_mode, capture_reference=(target_mode == "relative"))

    def _on_sdr_snr_zero_clicked(self) -> None:
        self._capture_sdr_relative_reference()
        self._set_sdr_snr_mode("relative", capture_reference=False)
        self._update_sdr_snr_display()

    def _on_sdr_level_unit_changed(self, unit_key: str) -> None:
        normalized = str(unit_key).strip().lower()
        if normalized not in {"dbm", "db_per_hz", "s_meter"}:
            normalized = "dbm"
        if self._sdr_snr_mode == "relative" and normalized == "s_meter":
            normalized = "dbm"
        self._sdr_level_unit = normalized
        self.sdr_level_meter.set_unit(normalized)
        self._persist_sdr_value("LEVEL_UNIT", normalized)
        self._update_sdr_snr_display()

    def _on_sdr_spectrum_min_changed(self, value: int) -> None:
        self._sdr_spectrum_y_min_db = float(value)
        current_max = self._sdr_spectrum_y_min_db + self._sdr_spectrum_y_range_db
        if current_max < self._sdr_spectrum_y_min_db + 10.0:
            current_max = self._sdr_spectrum_y_min_db + 10.0
            self._sdr_spectrum_y_range_db = current_max - self._sdr_spectrum_y_min_db
        self._persist_sdr_value("SPECTRUM_Y_MIN_DB", self._sdr_spectrum_y_min_db)
        self._apply_sdr_plot_scales()

    def _on_sdr_spectrum_range_changed(self, value: int) -> None:
        y_max_db = float(value)
        self._sdr_spectrum_y_range_db = float(max(10.0, y_max_db - self._sdr_spectrum_y_min_db))
        self._persist_sdr_value("SPECTRUM_Y_RANGE_DB", self._sdr_spectrum_y_range_db)
        self._apply_sdr_plot_scales()

    def _on_sdr_waterfall_stride_changed(self, value: int) -> None:
        self._sdr_waterfall_time_stride = int(max(1, int(value)))
        self.sdr_client.data_storage.set_waterfall_time_stride(self._sdr_waterfall_time_stride)
        self._persist_sdr_value("WATERFALL_TIME_STRIDE", self._sdr_waterfall_time_stride)

    def _on_sdr_frequency_control_changed(self, value_hz: float) -> None:
        self._pending_sdr_freq_mhz = float(value_hz) / 1e6
        self._sdr_freq_timer.start()

    def _on_sdr_bandwidth_spin_changed(self, value_khz: float) -> None:
        bandwidth_hz = float(value_khz) * 1e3
        self.sdr_client.set_bandwidth(bandwidth_hz)
        bandwidth_hz = float(self.sdr_client.bandwidth_hz)
        receiver_freq_hz = float(getattr(self.sdr_client, "receiver_freq_hz", self.sdr_client.center_freq))
        self.sdr_spectrum_plot.set_receiver_selection(receiver_freq_hz, bandwidth_hz)
        self._persist_sdr_value("BANDWIDTH_HZ", int(round(bandwidth_hz)))

    def _on_sdr_overlay_frequency_changed(self, frequency_hz: float) -> None:
        if self._sdr_view_mode == "center":
            try:
                self.sdr_frequency_control.blockSignals(True)
                self.sdr_frequency_control.set_value_hz(float(frequency_hz))
            finally:
                self.sdr_frequency_control.blockSignals(False)
            self._pending_sdr_freq_mhz = float(frequency_hz) / 1e6
            self._sdr_freq_timer.start()
            return
        self.sdr_client.set_receiver_frequency(float(frequency_hz))
        self._persist_sdr_value("RECEIVER_FREQ_HZ", int(round(float(frequency_hz))))
        self._update_sdr_snr_display()

    def _on_sdr_overlay_bandwidth_changed(self, bandwidth_hz: float) -> None:
        self.sdr_client.set_bandwidth(float(bandwidth_hz))
        bandwidth_hz = float(self.sdr_client.bandwidth_hz)
        try:
            self.sdr_bandwidth_spin.blockSignals(True)
            self.sdr_bandwidth_spin.setValue(float(bandwidth_hz) / 1e3)
        finally:
            self.sdr_bandwidth_spin.blockSignals(False)
        self.sdr_spectrum_plot.set_receiver_selection(
            float(getattr(self.sdr_client, "receiver_freq_hz", self.sdr_client.center_freq)),
            bandwidth_hz,
        )
        self._persist_sdr_value("BANDWIDTH_HZ", int(round(float(bandwidth_hz))))
        self._update_sdr_snr_display()

    def _on_sdr_plot_frequency_clicked(self, frequency_hz: float) -> None:
        frequency_hz = float(frequency_hz)
        if not np.isfinite(frequency_hz):
            return
        bandwidth_hz = float(max(100.0, self.sdr_client.bandwidth_hz))
        self.sdr_spectrum_plot.set_receiver_selection(frequency_hz, bandwidth_hz)
        if self._sdr_view_mode == "center":
            self.sdr_spectrum_plot.center_view_on_frequency(frequency_hz, default_span_hz=float(self.sdr_client.sample_rate))
            self._sdr_last_view_center_hz = frequency_hz
            self._sdr_last_view_sample_rate_hz = float(self.sdr_client.sample_rate)
            self._on_sdr_overlay_frequency_changed(frequency_hz)
            return
        self.sdr_client.set_receiver_frequency(frequency_hz)
        self._persist_sdr_value("RECEIVER_FREQ_HZ", int(round(frequency_hz)))
        self._update_sdr_snr_display()

    def _apply_auto_gain_profile(self) -> None:
        lna_state, if_gain = self.sdr_auto_gain_dialog.widget.get_active_pair_for_frequency(self.sdr_client.center_freq)
        self._apply_gain_pair(lna_state=lna_state, if_gain=if_gain)

    def _apply_gain_pair(self, *, lna_state: int, if_gain: int) -> None:
        rf_value = clamp_lna_state(self.sdr_client.center_freq, lna_state)
        if_value = int(max(self.sdr_if_gain_slider.minimum(), min(int(if_gain), self.sdr_if_gain_slider.maximum())))
        self.sdr_client.update_rf_gain(rf_value)
        self.sdr_client.update_if_gain(if_value)
        self._set_slider_value(self.sdr_rf_gain_slider, rf_value)
        self._set_slider_value(self.sdr_if_gain_slider, if_value)
        self._persist_sdr_value("RF_GAIN", int(rf_value))
        self._persist_sdr_value("IF_GAIN", int(if_value))
        self._update_gain_labels()

    def _on_auto_gain_profile_changed(self, _level_dbm: int) -> None:
        if self.sdr_auto_table_check.isChecked():
            self._apply_auto_gain_profile()

    def _on_auto_gain_profiles_changed(self, _profiles: object) -> None:
        if self.sdr_auto_table_check.isChecked():
            self._apply_auto_gain_profile()

    def _update_sdr_snr_mode_button(self) -> None:
        is_relative = self._sdr_snr_mode == "relative"
        try:
            self.sdr_snr_mode_button.blockSignals(True)
            self.sdr_snr_mode_button.setChecked(is_relative)
        finally:
            self.sdr_snr_mode_button.blockSignals(False)
        self.sdr_snr_mode_button.setText("Relative" if is_relative else "Absolute")
        self.sdr_snr_zero_button.setEnabled(True)

    def _set_sdr_snr_mode(self, mode: str, *, capture_reference: bool = False) -> None:
        normalized = "relative" if str(mode).strip().lower() == "relative" else "absolute"
        if normalized == "relative" and capture_reference:
            self._capture_sdr_relative_reference()
        if normalized == "absolute":
            self._sdr_snr_reference_db = None
            self._sdr_snr_reference_per_bin_db = None
        self._sdr_snr_mode = normalized
        if self._sdr_snr_mode == "relative" and self._sdr_level_unit == "s_meter":
            self._sdr_level_unit = "dbm"
            self.sdr_level_meter.set_unit(self._sdr_level_unit)
            self._persist_sdr_value("LEVEL_UNIT", self._sdr_level_unit)
        self.sdr_client.set_snr_mode(normalized)
        self._persist_sdr_value("SNR_MODE", normalized)
        self._update_sdr_snr_mode_button()
        self._update_sdr_snr_display()

    def _capture_sdr_relative_reference(self) -> None:
        metrics = self._sdr_last_absolute_metrics or self._compute_sdr_band_snr_average(absolute=True)
        if metrics is None:
            self._sdr_snr_reference_db = None
            self._sdr_snr_reference_per_bin_db = None
            return
        self._sdr_snr_reference_db = metrics["integrated_db"] if math.isfinite(metrics["integrated_db"]) else None
        self._sdr_snr_reference_per_bin_db = metrics["per_bin_db"] if math.isfinite(metrics["per_bin_db"]) else None

    def _sync_plot_scale_controls(self) -> None:
        for slider, value in (
            (self.sdr_spectrum_min_slider, int(round(self._sdr_spectrum_y_min_db))),
            (self.sdr_spectrum_range_slider, int(round(self._sdr_spectrum_y_min_db + self._sdr_spectrum_y_range_db))),
            (self.sdr_waterfall_stride_slider, int(self._sdr_waterfall_time_stride)),
        ):
            try:
                slider.blockSignals(True)
                slider.setValue(value)
            finally:
                slider.blockSignals(False)

    def _apply_sdr_plot_scales(self) -> None:
        y_max_db = self._sdr_spectrum_y_min_db + self._sdr_spectrum_y_range_db
        if getattr(self, "sdr_spectrum_plot", None) is not None:
            self.sdr_spectrum_plot.set_y_window(
                y_min_db=self._sdr_spectrum_y_min_db,
                y_range_db=self._sdr_spectrum_y_range_db,
            )
        if getattr(self, "sdr_waterfall_plot", None) is not None:
            self.sdr_waterfall_plot.set_level_window(self._sdr_spectrum_y_min_db, y_max_db)
            self.sdr_waterfall_plot.recalculate_plot(self.sdr_client.data_storage)
        self._sync_plot_scale_controls()

    def _compute_sdr_band_snr_average(self, *, absolute: bool = True) -> dict[str, float] | None:
        data_storage = getattr(self.sdr_client, "data_storage", None)
        if data_storage is None:
            return None
        x = getattr(data_storage, "x", None)
        if x is None or len(x) == 0:
            return None

        history = getattr(data_storage, "history", None)
        if history is not None and int(getattr(history, "history_size", 0)) > 0:
            fps = float(max(1.0, getattr(self.sdr_client, "fft_fps", 20.0)))
            frame_count = int(max(1, round(float(self._sdr_snr_integration_s) * fps)))
            traces = history.get_recent(frame_count)
        else:
            y = getattr(data_storage, "y", None)
            if y is None:
                return None
            traces = np.asarray([y], dtype=np.float32)

        traces = np.asarray(traces, dtype=np.float32)
        if traces.ndim == 1:
            traces = traces.reshape(1, -1)
        if traces.size == 0:
            return None

        center = float(getattr(self.sdr_client, "receiver_freq_hz", self.sdr_client.center_freq))
        bandwidth_hz = float(max(100.0, self.sdr_client.bandwidth_hz))
        bin_width_hz = float(max(1e-12, self._current_sdr_bin_width_hz()))
        half_bw = bandwidth_hz * 0.5
        x = np.asarray(x, dtype=np.float64)
        mask = (x >= center - half_bw) & (x <= center + half_bw)
        if not mask.any():
            nearest = int(np.argmin(np.abs(x - center)))
            mask = np.zeros_like(x, dtype=bool)
            mask[nearest] = True

        if traces.shape[1] != x.shape[0]:
            return None

        band_traces = traces[:, mask]
        if band_traces.size == 0:
            return None
        averaged_band = np.atleast_1d(average_power_spectrum_db(band_traces, axis=0))
        effective_bandwidth_hz = float(max(bin_width_hz, int(np.count_nonzero(mask)) * bin_width_hz))
        metrics = compute_band_power_metrics(
            averaged_band,
            bin_width_hz=bin_width_hz,
            bandwidth_hz=effective_bandwidth_hz,
        )
        if int(metrics.get("bin_count", 0.0)) <= 0:
            return None
        return metrics

    @staticmethod
    def _format_s_meter(value_dbm: float) -> tuple[str, float]:
        s_value = (float(value_dbm) + 127.0) / 6.0
        if s_value <= 9.0:
            clamped = max(0.0, min(9.0, s_value))
            rounded = int(round(clamped))
            return f"S{rounded}", clamped
        over_db = max(0.0, float(value_dbm) + 73.0)
        rounded_over = int(10 * round(over_db / 10.0))
        return f"S9+{rounded_over}", min(19.0, 9.0 + over_db / 10.0)

    def _update_sdr_snr_display(self, *_args) -> None:
        absolute_metrics = self._compute_sdr_band_snr_average(absolute=True)
        if absolute_metrics is None:
            self._sdr_last_absolute_metrics = None
            self.sdr_level_meter.set_display(value=None, text="-", minimum=-140.0, maximum=0.0, relative=False)
            return
        self._sdr_last_absolute_metrics = dict(absolute_metrics)

        if self._sdr_snr_mode == "relative":
            if (
                self._sdr_snr_reference_db is None
                or not math.isfinite(self._sdr_snr_reference_db)
                or self._sdr_snr_reference_per_bin_db is None
                or not math.isfinite(self._sdr_snr_reference_per_bin_db)
            ):
                self.sdr_level_meter.set_display(value=None, text="-", minimum=-40.0, maximum=40.0, relative=True)
                return
            integrated_db = absolute_metrics["integrated_db"] - float(self._sdr_snr_reference_db)
            per_bin_db = absolute_metrics["per_bin_db"] - float(self._sdr_snr_reference_per_bin_db)
        else:
            integrated_db = absolute_metrics["integrated_db"]
            per_bin_db = absolute_metrics["per_bin_db"]

        if not math.isfinite(integrated_db) or not math.isfinite(per_bin_db):
            self.sdr_level_meter.set_display(value=None, text="-", minimum=-140.0, maximum=0.0, relative=False)
            return

        is_relative = self._sdr_snr_mode == "relative"
        if self._sdr_level_unit == "db_per_hz":
            display_value = per_bin_db if is_relative else float(absolute_metrics.get("per_hz_db", per_bin_db - bin_width_to_density_offset_db(self._current_sdr_bin_width_hz())))
            display_text = f"{display_value:+.2f} dBm/Hz" if is_relative else f"{display_value:.2f} dBm/Hz"
            minimum, maximum = (-40.0, 40.0) if is_relative else (-130.0, -30.0)
        elif self._sdr_level_unit == "s_meter" and not is_relative:
            display_text, s_bar_value = self._format_s_meter(integrated_db)
            self.sdr_level_meter.set_display(
                value=s_bar_value,
                text=display_text,
                minimum=0.0,
                maximum=19.0,
                relative=False,
            )
            return
        else:
            display_value = integrated_db
            display_text = f"{display_value:+.2f} dBm" if is_relative else f"{display_value:.2f} dBm"
            minimum, maximum = (-40.0, 40.0) if is_relative else (-130.0, -30.0)

        self.sdr_level_meter.set_display(
            value=display_value,
            text=display_text,
            minimum=minimum,
            maximum=maximum,
            relative=is_relative,
        )

    def _on_sdr_perf_updated(self, snapshot: dict) -> None:
        self.sdr_spectrum_plot.consume_profile_metrics()
        self.sdr_waterfall_plot.consume_profile_metrics()
        self.sdr_display_fps_label.setText(f"FPS: {float(snapshot.get('display_fps', 0.0)):.1f}")
        self.sdr_fft_window_label.setText(f"FFT: {float(snapshot.get('fft_window_ms', 0.0)):.0f} ms")

    def close_sdr_ui(self) -> None:
        if getattr(self, "sdr_auto_gain_dialog", None) is not None:
            self.sdr_auto_gain_dialog.close()
        if getattr(self, "sdr_client", None) is not None:
            self.sdr_client.close()

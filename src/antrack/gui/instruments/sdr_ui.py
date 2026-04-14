"""SDR tab wiring for the Antrack main window."""

from __future__ import annotations

import math

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from antrack.core.instruments.sdr_client import SdrClient
from antrack.gui.widgets.spectrum_plot import SpectrumPlotWidget
from antrack.gui.widgets.waterfall_plot import WaterfallPlotWidget
from antrack.utils.settings_loader import update_and_persist_setting


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

        root_layout = container.layout()
        if root_layout is None:
            root_layout = QVBoxLayout(container)
        self._clear_layout(root_layout)

        controls_group = QGroupBox("SDR Control", container)
        controls_layout = QGridLayout(controls_group)
        controls_layout.setColumnStretch(5, 1)

        self.sdr_freq_spin = QDoubleSpinBox(controls_group)
        self.sdr_freq_spin.setDecimals(6)
        self.sdr_freq_spin.setRange(0.001, 6000.0)
        self.sdr_freq_spin.setSuffix(" MHz")
        self.sdr_freq_spin.setValue(self.sdr_client.center_freq / 1e6)

        self.sdr_sample_rate_spin = QDoubleSpinBox(controls_group)
        self.sdr_sample_rate_spin.setDecimals(3)
        self.sdr_sample_rate_spin.setRange(0.2, 20.0)
        self.sdr_sample_rate_spin.setSuffix(" MS/s")
        self.sdr_sample_rate_spin.setValue(self.sdr_client.sample_rate / 1e6)

        self.sdr_if_gain_spin = QDoubleSpinBox(controls_group)
        self.sdr_if_gain_spin.setDecimals(0)
        self.sdr_if_gain_spin.setRange(0, 100)
        self.sdr_if_gain_spin.setValue(self.sdr_client.if_gain)

        self.sdr_rf_gain_spin = QDoubleSpinBox(controls_group)
        self.sdr_rf_gain_spin.setDecimals(0)
        self.sdr_rf_gain_spin.setRange(0, 100)
        self.sdr_rf_gain_spin.setValue(self.sdr_client.rf_gain)

        self.sdr_agc_combo = QComboBox(controls_group)
        self.sdr_agc_combo.addItems(["Off", "On"])
        self.sdr_agc_combo.setCurrentText("On" if self.sdr_client.agc else "Off")

        self.sdr_antenna_combo = QComboBox(controls_group)
        self.sdr_antenna_combo.addItems([str(item) for item in self.sdr_client.hwinfo.get("antennas", [])])
        if self.sdr_client.antenna:
            self.sdr_antenna_combo.setCurrentText(self.sdr_client.antenna)

        self.sdr_snr_mode_combo = QComboBox(controls_group)
        self.sdr_snr_mode_combo.addItems(["relative", "absolute"])
        self.sdr_snr_mode_combo.setCurrentText(self.sdr_client.snr_mode)

        self.sdr_noise_floor_spin = QDoubleSpinBox(controls_group)
        self.sdr_noise_floor_spin.setDecimals(2)
        self.sdr_noise_floor_spin.setRange(-200.0, 20.0)
        self.sdr_noise_floor_spin.setSuffix(" dB")
        self.sdr_noise_floor_spin.setValue(self.sdr_client.noise_floor_ref_db)

        self.sdr_start_button = QPushButton("Start", controls_group)
        self.sdr_minus_10k = QPushButton("-10 kHz", controls_group)
        self.sdr_minus_1k = QPushButton("-1 kHz", controls_group)
        self.sdr_plus_1k = QPushButton("+1 kHz", controls_group)
        self.sdr_plus_10k = QPushButton("+10 kHz", controls_group)

        controls_layout.addWidget(QLabel("Center"), 0, 0)
        controls_layout.addWidget(self.sdr_freq_spin, 0, 1)
        controls_layout.addWidget(self.sdr_minus_10k, 0, 2)
        controls_layout.addWidget(self.sdr_minus_1k, 0, 3)
        controls_layout.addWidget(self.sdr_plus_1k, 0, 4)
        controls_layout.addWidget(self.sdr_plus_10k, 0, 5)
        controls_layout.addWidget(QLabel("Sample Rate"), 1, 0)
        controls_layout.addWidget(self.sdr_sample_rate_spin, 1, 1)
        controls_layout.addWidget(QLabel("IF Gain"), 1, 2)
        controls_layout.addWidget(self.sdr_if_gain_spin, 1, 3)
        controls_layout.addWidget(QLabel("RF Gain"), 1, 4)
        controls_layout.addWidget(self.sdr_rf_gain_spin, 1, 5)
        controls_layout.addWidget(QLabel("AGC"), 2, 0)
        controls_layout.addWidget(self.sdr_agc_combo, 2, 1)
        controls_layout.addWidget(QLabel("Antenna"), 2, 2)
        controls_layout.addWidget(self.sdr_antenna_combo, 2, 3)
        controls_layout.addWidget(QLabel("SNR"), 2, 4)
        controls_layout.addWidget(self.sdr_snr_mode_combo, 2, 5)
        controls_layout.addWidget(QLabel("Noise Floor"), 3, 0)
        controls_layout.addWidget(self.sdr_noise_floor_spin, 3, 1)
        controls_layout.addWidget(self.sdr_start_button, 3, 5)

        status_group = QGroupBox("SDR Status", container)
        status_form = QFormLayout(status_group)
        self.sdr_status_mode = QLabel(self.sdr_client.mode, status_group)
        self.sdr_status_fc = QLabel("-", status_group)
        self.sdr_status_fs = QLabel("-", status_group)
        self.sdr_status_snr = QLabel("-", status_group)
        self.sdr_status_frames = QLabel("0", status_group)
        self.sdr_status_timeouts = QLabel("0", status_group)
        self.sdr_status_errors = QLabel("0", status_group)
        status_form.addRow("Mode", self.sdr_status_mode)
        status_form.addRow("Center", self.sdr_status_fc)
        status_form.addRow("Sample Rate", self.sdr_status_fs)
        status_form.addRow("SNR", self.sdr_status_snr)
        status_form.addRow("Frames", self.sdr_status_frames)
        status_form.addRow("Timeouts", self.sdr_status_timeouts)
        status_form.addRow("Errors", self.sdr_status_errors)

        splitter = QSplitter(Qt.Vertical, container)
        self.sdr_spectrum_plot = SpectrumPlotWidget(splitter)
        self.sdr_waterfall_plot = WaterfallPlotWidget(splitter)
        splitter.addWidget(self.sdr_spectrum_plot)
        splitter.addWidget(self.sdr_waterfall_plot)
        splitter.setSizes([600, 400])

        root_layout.addWidget(controls_group)
        root_layout.addWidget(status_group)
        root_layout.addWidget(splitter, 1)

        self.sdr_client.data_storage.data_updated.connect(self.sdr_spectrum_plot.update_plot)
        self.sdr_client.data_storage.data_recalculated.connect(self.sdr_spectrum_plot.recalculate_plot)
        self.sdr_client.data_storage.history_updated.connect(self.sdr_waterfall_plot.update_plot)
        self.sdr_client.data_storage.history_recalculated.connect(self.sdr_waterfall_plot.recalculate_plot)
        self.sdr_spectrum_plot.visible_span_changed.connect(self.sdr_client.update_fft_for_view)
        self.sdr_client.snr_updated.connect(self._on_sdr_snr_updated)
        self.sdr_client.perf_updated.connect(self._on_sdr_perf_updated)
        self.sdr_client.mode_changed.connect(self.sdr_status_mode.setText)
        self.sdr_client.status.connect(lambda message: self.status_bar.showMessage(message, 3000))
        self.sdr_client.error.connect(lambda message: self.status_bar.showMessage(f"SDR: {message}", 5000))
        self.sdr_client.settings_changed.connect(self._refresh_sdr_status_labels)

        self.sdr_start_button.clicked.connect(self.toggle_sdr_stream)
        self.sdr_freq_spin.valueChanged.connect(self._on_sdr_frequency_changed)
        self.sdr_sample_rate_spin.valueChanged.connect(self._on_sdr_sample_rate_changed)
        self.sdr_if_gain_spin.valueChanged.connect(self._on_sdr_if_gain_changed)
        self.sdr_rf_gain_spin.valueChanged.connect(self._on_sdr_rf_gain_changed)
        self.sdr_agc_combo.currentTextChanged.connect(self._on_sdr_agc_changed)
        self.sdr_antenna_combo.currentTextChanged.connect(self._on_sdr_antenna_changed)
        self.sdr_snr_mode_combo.currentTextChanged.connect(self._on_sdr_snr_mode_changed)
        self.sdr_noise_floor_spin.valueChanged.connect(self._on_sdr_noise_floor_changed)
        self.sdr_minus_10k.clicked.connect(lambda: self._step_sdr_frequency(-10_000.0))
        self.sdr_minus_1k.clicked.connect(lambda: self._step_sdr_frequency(-1_000.0))
        self.sdr_plus_1k.clicked.connect(lambda: self._step_sdr_frequency(1_000.0))
        self.sdr_plus_10k.clicked.connect(lambda: self._step_sdr_frequency(10_000.0))
        self._refresh_sdr_status_labels(self.sdr_client.snapshot_state())

    def _clear_layout(self, layout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def toggle_sdr_stream(self) -> None:
        if self.sdr_client.running:
            self.sdr_client.stop()
            self.sdr_start_button.setText("Start")
        else:
            self.sdr_client.start()
            self.sdr_start_button.setText("Stop")
        self._refresh_sdr_status_labels(self.sdr_client.snapshot_state())

    def _persist_sdr_value(self, key: str, value) -> None:
        update_and_persist_setting(self.settings, "SDR", key, value)

    def _step_sdr_frequency(self, delta_hz: float) -> None:
        new_freq_mhz = max(0.001, self.sdr_freq_spin.value() + delta_hz / 1e6)
        self.sdr_freq_spin.setValue(new_freq_mhz)

    def _on_sdr_frequency_changed(self, value_mhz: float) -> None:
        frequency_hz = float(value_mhz) * 1e6
        self.sdr_client.set_frequency(frequency_hz)
        self._persist_sdr_value("CENTER_FREQ_HZ", int(round(frequency_hz)))

    def _on_sdr_sample_rate_changed(self, value_msps: float) -> None:
        sample_rate_hz = float(value_msps) * 1e6
        self.sdr_client.set_sample_rate(sample_rate_hz)
        self._persist_sdr_value("SAMPLE_RATE_HZ", int(round(sample_rate_hz)))

    def _on_sdr_if_gain_changed(self, value: float) -> None:
        self.sdr_client.update_if_gain(int(round(value)))
        self._persist_sdr_value("IF_GAIN", int(round(value)))

    def _on_sdr_rf_gain_changed(self, value: float) -> None:
        self.sdr_client.update_rf_gain(int(round(value)))
        self._persist_sdr_value("RF_GAIN", int(round(value)))

    def _on_sdr_agc_changed(self, value: str) -> None:
        enabled = str(value).strip().lower() == "on"
        self.sdr_client.update_agc(enabled)
        self._persist_sdr_value("AGC", enabled)

    def _on_sdr_antenna_changed(self, value: str) -> None:
        if not value:
            return
        self.sdr_client.set_antenna(value)
        self._persist_sdr_value("ANTENNA", value)

    def _on_sdr_snr_mode_changed(self, mode: str) -> None:
        self.sdr_client.set_snr_mode(mode)
        self._persist_sdr_value("SNR_MODE", mode)

    def _on_sdr_noise_floor_changed(self, value: float) -> None:
        self.sdr_client.set_noise_floor_ref_db(value)
        self._persist_sdr_value("NOISE_FLOOR_REF_DB", float(value))

    def _on_sdr_snr_updated(self, snr_db: float, mode: str) -> None:
        label = f"{snr_db:.2f} dB ({mode})" if math.isfinite(float(snr_db)) else "-"
        self.sdr_status_snr.setText(label)
        if hasattr(self, "tracked_object"):
            self.tracked_object.snr_db = float(snr_db)
            self.tracked_object.snr_mode = str(mode)
        if hasattr(self, "_update_selected_target_snr_display"):
            self._update_selected_target_snr_display(snr_db, mode)

    def _on_sdr_perf_updated(self, snapshot: dict) -> None:
        self.sdr_status_frames.setText(str(snapshot.get("frames", 0)))
        self.sdr_status_timeouts.setText(str(snapshot.get("timeouts", 0)))
        self.sdr_status_errors.setText(str(snapshot.get("stream_errors", 0)))
        self._refresh_sdr_status_labels(snapshot)

    def _refresh_sdr_status_labels(self, snapshot: dict) -> None:
        self.sdr_status_mode.setText(str(snapshot.get("mode", self.sdr_client.mode)))
        self.sdr_status_fc.setText(f"{float(snapshot.get('center_freq_hz', self.sdr_client.center_freq)) / 1e6:.6f} MHz")
        self.sdr_status_fs.setText(f"{float(snapshot.get('sample_rate_hz', self.sdr_client.sample_rate)) / 1e6:.3f} MS/s")

    def close_sdr_ui(self) -> None:
        if getattr(self, "sdr_client", None) is not None:
            self.sdr_client.close()

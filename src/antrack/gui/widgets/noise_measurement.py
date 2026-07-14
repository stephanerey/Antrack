"""Noise measurement widget with live plot and optional audio monitor."""

from __future__ import annotations

import math
import struct
import time

import pyqtgraph as pg
from PyQt5.QtCore import QIODevice, QSettings, QTimer, Qt, pyqtSignal
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from antrack.gui.noise_measurement_state import NoiseMeasurementState

try:
    from PyQt5.QtMultimedia import QAudio, QAudioDeviceInfo, QAudioFormat, QAudioOutput
except Exception as exc:  # pragma: no cover - depends on local Qt multimedia support
    _QT_MULTIMEDIA_IMPORT_ERROR = repr(exc)
    QAudio = None
    QAudioDeviceInfo = None
    QAudioFormat = None
    QAudioOutput = None
else:
    _QT_MULTIMEDIA_IMPORT_ERROR = ""


_DEFAULT_AUDIO_DEVICE_ID = "__default__"
_NO_AUDIO_DEVICE_ID = "__none__"
_ABSOLUTE_SOUND_MIN_SPAN_DB = 20.0
_ABSOLUTE_SOUND_MIN_FREQ_HZ = 400.0
_ABSOLUTE_SOUND_MAX_FREQ_HZ = 2000.0
_RELATIVE_SOUND_CENTER_HZ = 700.0
_RELATIVE_SOUND_HZ_PER_DB = 80.0
_SOUND_MIN_FREQ_HZ = 200.0
_SOUND_MAX_FREQ_HZ = 3000.0


def _qt_multimedia_unavailable_reason() -> str:
    if _QT_MULTIMEDIA_IMPORT_ERROR:
        return f"QtMultimedia audio backend unavailable: {_QT_MULTIMEDIA_IMPORT_ERROR}"
    return "QtMultimedia audio backend unavailable."


class _ToneGeneratorIODevice(QIODevice):
    """Generate a smoothed sine wave for continuous audio monitoring."""

    def __init__(self, parent=None, *, sample_rate_hz: int = 44_100, volume: float = 0.12) -> None:
        super().__init__(parent)
        self.sample_rate_hz = int(max(8_000, sample_rate_hz))
        self.volume = float(max(0.0, min(volume, 1.0)))
        self._phase = 0.0
        self._current_freq_hz = 0.0
        self._target_freq_hz = 0.0

    def set_frequency(self, frequency_hz: float) -> None:
        self._target_freq_hz = float(max(0.0, frequency_hz))

    def bytesAvailable(self) -> int:
        return 4096 + super().bytesAvailable()

    def readData(self, maxlen: int) -> bytes:  # noqa: N802 - Qt API name
        if maxlen <= 0:
            return b""
        frame_count = max(1, int(maxlen // 2))
        buffer = bytearray(frame_count * 2)
        smoothing = 0.02
        current_freq_hz = float(self._current_freq_hz)
        phase = float(self._phase)
        for index in range(frame_count):
            current_freq_hz += (self._target_freq_hz - current_freq_hz) * smoothing
            if current_freq_hz <= 0.0:
                sample = 0
            else:
                phase += (2.0 * math.pi * current_freq_hz) / float(self.sample_rate_hz)
                if phase >= 2.0 * math.pi:
                    phase %= 2.0 * math.pi
                sample = int(32767.0 * self.volume * math.sin(phase))
            struct.pack_into("<h", buffer, index * 2, sample)
        self._current_freq_hz = current_freq_hz
        self._phase = phase
        return bytes(buffer)

    def writeData(self, data: bytes) -> int:  # noqa: N802 - Qt API name
        return 0


def _base_audio_format() -> QAudioFormat | None:
    if QAudioFormat is None:
        return None
    audio_format = QAudioFormat()
    audio_format.setSampleRate(44_100)
    audio_format.setChannelCount(1)
    audio_format.setSampleSize(16)
    audio_format.setCodec("audio/pcm")
    audio_format.setByteOrder(QAudioFormat.LittleEndian)
    audio_format.setSampleType(QAudioFormat.SignedInt)
    return audio_format


def _device_display_name(device_name: str, realm: str) -> str:
    cleaned_name = str(device_name or "Unknown output").strip() or "Unknown output"
    cleaned_realm = str(realm or "").strip()
    if cleaned_realm:
        return f"{cleaned_name} [{cleaned_realm}]"
    return cleaned_name


class _QtAudioToneManager:
    """Minimal QtMultimedia tone output manager with explicit device selection."""

    @staticmethod
    def backend_available() -> bool:
        return QAudio is not None and QAudioDeviceInfo is not None and QAudioFormat is not None and QAudioOutput is not None

    @classmethod
    def list_output_devices(cls) -> list[dict[str, object]]:
        if not cls.backend_available():
            return []
        devices: list[dict[str, object]] = []
        occurrences: dict[tuple[str, str], int] = {}
        try:
            available = list(QAudioDeviceInfo.availableDevices(QAudio.AudioOutput))
        except Exception:
            return []
        if not available:
            try:
                default_device = QAudioDeviceInfo.defaultOutputDevice()
            except Exception:
                default_device = None
            if default_device is not None and not default_device.isNull():
                available = [default_device]
        for device in available:
            try:
                name = str(device.deviceName())
            except Exception:
                name = "Unknown output"
            try:
                realm = str(device.realm())
            except Exception:
                realm = ""
            key = (name, realm)
            occurrence = occurrences.get(key, 0)
            occurrences[key] = occurrence + 1
            device_id = f"{realm}|{name}|{occurrence}"
            devices.append(
                {
                    "id": device_id,
                    "label": _device_display_name(name, realm),
                    "device": device,
                    "name": name,
                    "realm": realm,
                }
            )
        return devices

    @classmethod
    def default_output_entry(cls) -> dict[str, object]:
        return {
            "id": _DEFAULT_AUDIO_DEVICE_ID,
            "label": "Default system output",
            "device": None,
            "name": "Default system output",
            "realm": "",
        }

    def __init__(self, parent=None, *, device_info: object | None = None) -> None:
        if not self.backend_available():
            raise RuntimeError("QtMultimedia audio output is unavailable.")
        audio_format = _base_audio_format()
        if audio_format is None:
            raise RuntimeError("Audio format initialization failed.")
        if device_info is None:
            device_info = QAudioDeviceInfo.defaultOutputDevice()
        if device_info is None or getattr(device_info, "isNull", lambda: True)():
            raise RuntimeError("No audio output device is available.")
        try:
            if not device_info.isFormatSupported(audio_format):
                audio_format = device_info.nearestFormat(audio_format)
        except Exception:
            pass
        self._audio_output = QAudioOutput(device_info, audio_format, parent)
        self._audio_output.setVolume(0.35)
        self._generator = _ToneGeneratorIODevice(parent)
        self._generator.open(QIODevice.ReadOnly)
        self._audio_output.start(self._generator)
        self._error_getter = getattr(self._audio_output, "error", None)
        self._state_getter = getattr(self._audio_output, "state", None)
        self._device_name = str(getattr(device_info, "deviceName", lambda: "Default system output")())

    def set_frequency(self, frequency_hz: float) -> None:
        self._generator.set_frequency(frequency_hz)

    def has_error(self) -> bool:
        if callable(self._error_getter):
            try:
                error_value = self._error_getter()
            except Exception:
                return True
            no_error = getattr(QAudioOutput, "NoError", None)
            if no_error is not None:
                return error_value != no_error
            return bool(error_value)
        return False

    def error_string(self) -> str:
        if self.has_error():
            return "audio backend error"
        if callable(self._state_getter):
            try:
                state_value = self._state_getter()
            except Exception:
                return "audio backend state unavailable"
            stopped_state = getattr(QAudioOutput, "StoppedState", None)
            active_state = getattr(QAudioOutput, "ActiveState", None)
            if stopped_state is not None and state_value == stopped_state and active_state is not None:
                return "audio backend stopped"
        return ""

    def stop(self) -> None:
        try:
            self._generator.set_frequency(0.0)
        except Exception:
            pass
        try:
            self._audio_output.stop()
        except Exception:
            pass
        try:
            self._generator.close()
        except Exception:
            pass

    @property
    def device_name(self) -> str:
        return self._device_name


class NoiseMeasurementWidget(QWidget):
    """Display live absolute and relative noise measurements."""

    monitorToggled = pyqtSignal(bool)

    def __init__(self, parent=None, *, logger=None, status_callback=None) -> None:
        super().__init__(parent)
        self.logger = logger
        self.status_callback = status_callback
        self._settings = QSettings("Antrack", "Antrack")
        self._state = NoiseMeasurementState()
        self._audio_controller: _QtAudioToneManager | None = None
        self._test_tone_controller: _QtAudioToneManager | None = None
        self._latest_absolute_db: float | None = None
        self._latest_timestamp_s: float | None = None
        self._has_pending_measurement = False
        self._audio_backend_available = _QtAudioToneManager.backend_available()
        self._audio_error_message = ""
        self._audio_devices: list[dict[str, object]] = []
        self._selected_audio_device_id = str(self._settings.value("noise_monitor/audio_device_id", _DEFAULT_AUDIO_DEVICE_ID))
        self._auto_scale_y = self._settings.value("noise_monitor/auto_scale_y", True, type=bool)
        try:
            saved_y_min = float(self._settings.value("noise_monitor/y_min", -120.0))
            saved_y_max = float(self._settings.value("noise_monitor/y_max", -20.0))
        except (TypeError, ValueError):
            saved_y_min, saved_y_max = -120.0, -20.0
        if not self._state.valid_y_range(saved_y_min, saved_y_max):
            saved_y_min, saved_y_max = -120.0, -20.0
        self._last_valid_y_range = (saved_y_min, saved_y_max)
        self._active_audio_device_id: str | None = None
        self._last_invalid_log_monotonic = 0.0
        self._axis_mode = "absolute"
        self._monitor_active = False
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(100)
        self._refresh_timer.timeout.connect(self._on_refresh_timer)
        self._test_tone_timer = QTimer(self)
        self._test_tone_timer.setSingleShot(True)
        self._test_tone_timer.timeout.connect(self._stop_test_tone)

        self._build_ui()
        self._refresh_audio_devices(initial=True)
        self._update_button_text()
        self._update_display_labels()
        self._refresh_plot(force=True)

    def shutdown(self) -> None:
        self._refresh_timer.stop()
        self._test_tone_timer.stop()
        self._stop_test_tone()
        self._stop_sound()

    def set_measurement(self, value_db: float | None, *, timestamp_s: float | None = None) -> None:
        if value_db is None:
            return
        try:
            numeric_value = float(value_db)
        except Exception:
            self._log_invalid_measurement("conversion failed")
            return
        if not math.isfinite(numeric_value):
            self._log_invalid_measurement("not finite")
            return
        timestamp = float(timestamp_s) if timestamp_s is not None else float(time.time())
        if not math.isfinite(timestamp):
            timestamp = float(time.time())
        self._latest_absolute_db = numeric_value
        self._latest_timestamp_s = timestamp
        self._has_pending_measurement = True
        self._state.record_statistics(numeric_value, timestamp_s=timestamp)

    def clear_measurement(self) -> None:
        self._latest_absolute_db = None
        self._latest_timestamp_s = None
        self._has_pending_measurement = False
        self._state.clear_current()
        self._update_display_labels()
        self._apply_sound_target(None)

    def _build_ui(self) -> None:
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(10, 10, 10, 10)
        root_layout.setSpacing(8)

        band = QWidget(self)
        band_layout = QGridLayout(band)
        band_layout.setContentsMargins(0, 0, 0, 0)
        band_layout.setHorizontalSpacing(12)
        band_layout.setVerticalSpacing(4)

        self.absolute_value_label = QLabel("--", band)
        self.relative_value_label = QLabel("--", band)
        self.reference_value_label = QLabel("Reference: --", band)
        self.statistics_min_label = QLabel("--", band)
        self.statistics_mean_label = QLabel("--", band)
        self.statistics_max_label = QLabel("--", band)
        self.absolute_value_label.setStyleSheet("font-size: 16px; font-weight: 600;")
        relative_font = QFont(self.relative_value_label.font())
        relative_font.setPointSize(max(relative_font.pointSize() + 6, 18))
        relative_font.setBold(True)
        self.relative_value_label.setFont(relative_font)
        self.relative_value_label.setStyleSheet("font-size: 24px; font-weight: 700;")
        self.reference_value_label.setStyleSheet("color: #808080;")

        absolute_column = self._metric_column("Absolute", self.absolute_value_label, band)
        relative_column = self._metric_column("Relative", self.relative_value_label, band)
        reference_column = self._metric_column("Reference", self.reference_value_label, band)
        statistics_min_column = self._metric_column("Min", self.statistics_min_label, band)
        statistics_mean_column = self._metric_column("Mean", self.statistics_mean_label, band)
        statistics_max_column = self._metric_column("Max", self.statistics_max_label, band)

        self.monitor_button = QPushButton(band)
        self.monitor_button.setCheckable(True)
        self.relative_mode_button = QPushButton(band)
        self.relative_mode_button.setCheckable(True)
        self.sound_button = QPushButton(band)
        self.sound_button.setCheckable(True)
        self.audio_output_combo = QComboBox(band)
        self.audio_output_combo.setMinimumWidth(220)
        self.refresh_audio_button = QPushButton("Refresh Audio", band)
        self.test_tone_button = QPushButton("Test Tone", band)
        self.window_button = QPushButton(band)
        self.clear_button = QPushButton("Clear", band)
        self.reset_statistics_button = QPushButton("Reset statistics", band)
        self.auto_scale_y_checkbox = QCheckBox("Auto scale Y", band)
        self.auto_scale_y_checkbox.setChecked(bool(self._auto_scale_y))
        self.y_min_spin = QDoubleSpinBox(band)
        self.y_max_spin = QDoubleSpinBox(band)
        for spin in (self.y_min_spin, self.y_max_spin):
            spin.setRange(-1_000_000.0, 1_000_000.0)
            spin.setDecimals(2)
            spin.setSuffix(" dB")
        self.y_min_spin.setValue(self._last_valid_y_range[0])
        self.y_max_spin.setValue(self._last_valid_y_range[1])
        self.audio_status_label = QLabel("Audio: -", band)
        self.audio_status_label.setStyleSheet("color: #808080; font-size: 11px;")

        self.monitor_button.clicked.connect(self._on_monitor_toggled)
        self.relative_mode_button.clicked.connect(self._on_relative_mode_toggled)
        self.sound_button.clicked.connect(self._on_sound_toggled)
        self.audio_output_combo.currentIndexChanged.connect(self._on_audio_output_changed)
        self.refresh_audio_button.clicked.connect(self._on_refresh_audio_clicked)
        self.test_tone_button.clicked.connect(self._on_test_tone_clicked)
        self.window_button.clicked.connect(self._on_cycle_window_clicked)
        self.clear_button.clicked.connect(self._on_clear_clicked)
        self.reset_statistics_button.clicked.connect(self._on_reset_statistics_clicked)
        self.auto_scale_y_checkbox.toggled.connect(self._on_auto_scale_y_toggled)
        self.y_min_spin.valueChanged.connect(self._on_manual_y_range_changed)
        self.y_max_spin.valueChanged.connect(self._on_manual_y_range_changed)

        monitor_controls_row = QWidget(band)
        monitor_controls_layout = QHBoxLayout(monitor_controls_row)
        monitor_controls_layout.setContentsMargins(0, 0, 0, 0)
        monitor_controls_layout.setSpacing(8)
        monitor_controls_layout.addWidget(self.monitor_button)
        monitor_controls_layout.addWidget(self.relative_mode_button)
        monitor_controls_layout.addWidget(self.sound_button)
        monitor_controls_layout.addWidget(self.window_button)
        monitor_controls_layout.addWidget(self.clear_button)
        monitor_controls_layout.addWidget(self.reset_statistics_button)
        monitor_controls_layout.addStretch(1)

        scale_controls_row = QWidget(band)
        scale_controls_layout = QHBoxLayout(scale_controls_row)
        scale_controls_layout.setContentsMargins(0, 0, 0, 0)
        scale_controls_layout.setSpacing(8)
        scale_controls_layout.addWidget(self.auto_scale_y_checkbox)
        scale_controls_layout.addWidget(QLabel("Y min", scale_controls_row))
        scale_controls_layout.addWidget(self.y_min_spin)
        scale_controls_layout.addWidget(QLabel("Y max", scale_controls_row))
        scale_controls_layout.addWidget(self.y_max_spin)
        scale_controls_layout.addSpacing(12)
        scale_controls_layout.addWidget(statistics_min_column)
        scale_controls_layout.addWidget(statistics_mean_column)
        scale_controls_layout.addWidget(statistics_max_column)
        scale_controls_layout.addStretch(1)

        audio_controls_row = QWidget(band)
        audio_controls_layout = QHBoxLayout(audio_controls_row)
        audio_controls_layout.setContentsMargins(0, 0, 0, 0)
        audio_controls_layout.setSpacing(8)
        audio_controls_layout.addWidget(QLabel("Audio Output", audio_controls_row))
        audio_controls_layout.addWidget(self.audio_output_combo, 1)
        audio_controls_layout.addWidget(self.refresh_audio_button)
        audio_controls_layout.addWidget(self.test_tone_button)
        audio_controls_layout.addWidget(self.audio_status_label)

        band_layout.addWidget(absolute_column, 0, 0)
        band_layout.addWidget(relative_column, 0, 1)
        band_layout.addWidget(reference_column, 0, 2)
        band_layout.setColumnStretch(3, 1)
        band_layout.addWidget(monitor_controls_row, 1, 0, 1, 4)
        band_layout.addWidget(audio_controls_row, 2, 0, 1, 4)
        band_layout.addWidget(scale_controls_row, 3, 0, 1, 4)

        self.plot = pg.PlotWidget(axisItems={"bottom": pg.DateAxisItem(orientation="bottom")}, parent=self)
        self.plot.showGrid(x=True, y=True, alpha=0.25)
        self.plot.setClipToView(True)
        self.plot.setDownsampling(auto=True, mode="subsample")
        self.plot.setLabel("bottom", "Time")
        self.plot.setLabel("left", "Noise Power (dB)")
        self.plot_curve = self.plot.plot(pen=pg.mkPen(80, 200, 255, width=2))
        statistics_pen = pg.mkPen(255, 210, 80, width=1, style=Qt.DashLine)
        self.statistics_mean_line = pg.InfiniteLine(angle=0, movable=False, pen=statistics_pen)
        self.statistics_min_marker = pg.ScatterPlotItem(
            size=10, symbol="t", pen=pg.mkPen(80, 220, 140), brush=pg.mkBrush(80, 220, 140)
        )
        self.statistics_max_marker = pg.ScatterPlotItem(
            size=10, symbol="t1", pen=pg.mkPen(255, 110, 90), brush=pg.mkBrush(255, 110, 90)
        )
        self.statistics_text = pg.TextItem(anchor=(0.0, 0.0), color=(230, 230, 230), fill=pg.mkBrush(20, 20, 20, 180))
        self.plot.addItem(self.statistics_mean_line, ignoreBounds=True)
        self.plot.addItem(self.statistics_min_marker, ignoreBounds=True)
        self.plot.addItem(self.statistics_max_marker, ignoreBounds=True)
        self.plot.addItem(self.statistics_text, ignoreBounds=True)

        root_layout.addWidget(band)
        root_layout.addWidget(self.plot, 1)

        self.relative_mode_button.setEnabled(False)
        self.sound_button.setEnabled(False)
        self.window_button.setEnabled(False)
        self.clear_button.setEnabled(False)
        self._on_auto_scale_y_toggled(self.auto_scale_y_checkbox.isChecked())

    @staticmethod
    def _metric_column(title: str, value_label: QLabel, parent: QWidget) -> QWidget:
        column = QWidget(parent)
        layout = QVBoxLayout(column)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        title_label = QLabel(title, column)
        title_label.setStyleSheet("color: #808080; font-size: 11px;")
        layout.addWidget(title_label)
        layout.addWidget(value_label)
        return column

    def set_monitor_active(self, active: bool) -> None:
        active = bool(active)
        if active == self._monitor_active:
            return
        self._monitor_active = active
        try:
            self.monitor_button.blockSignals(True)
            self.monitor_button.setChecked(active)
        finally:
            self.monitor_button.blockSignals(False)
        self.relative_mode_button.setEnabled(active)
        self.sound_button.setEnabled(active and self._can_attempt_audio())
        self.window_button.setEnabled(active)
        self.clear_button.setEnabled(active)
        if active:
            self._refresh_timer.start()
        else:
            self._refresh_timer.stop()
            self._stop_sound()
            try:
                self.sound_button.blockSignals(True)
                self.sound_button.setChecked(False)
            finally:
                self.sound_button.blockSignals(False)
            try:
                self.relative_mode_button.blockSignals(True)
                self.relative_mode_button.setChecked(False)
            finally:
                self.relative_mode_button.blockSignals(False)
            self._state.set_relative_mode(False)
            self._state.reference_absolute_db = None
            self._state.clear_history()
            self.clear_measurement()
            self._stop_test_tone()
        self._update_plot_axis_label()
        self._update_button_text()
        self._refresh_plot(force=True)

    def _on_monitor_toggled(self, checked: bool) -> None:
        self.set_monitor_active(checked)
        self.monitorToggled.emit(self._monitor_active)

    def _on_relative_mode_toggled(self, checked: bool) -> None:
        if not self._monitor_active:
            return
        if checked and not self._state.set_relative_mode(True):
            self._set_status("Noise reference capture skipped: no valid measurement yet.", 4000)
            try:
                self.relative_mode_button.blockSignals(True)
                self.relative_mode_button.setChecked(False)
            finally:
                self.relative_mode_button.blockSignals(False)
            self._update_button_text()
            self._update_display_labels()
            self._refresh_plot(force=True)
            return
        if not checked:
            self._state.set_relative_mode(False)
        self._update_plot_axis_label()
        self._update_button_text()
        self._update_display_labels()
        self._refresh_plot(force=True)

    def _on_sound_toggled(self, checked: bool) -> None:
        if not self._monitor_active:
            try:
                self.sound_button.blockSignals(True)
                self.sound_button.setChecked(False)
            finally:
                self.sound_button.blockSignals(False)
            self._update_button_text()
            return
        if checked and not self._ensure_sound_controller():
            try:
                self.sound_button.blockSignals(True)
                self.sound_button.setChecked(False)
            finally:
                self.sound_button.blockSignals(False)
            self._update_button_text()
            return
        if not checked:
            self._stop_sound()
        else:
            self._apply_sound_target(self._sound_driver_value())
        self._update_button_text()

    def _on_cycle_window_clicked(self) -> None:
        self._state.cycle_window()
        self._update_button_text()
        self._refresh_plot(force=True)

    def _on_reset_statistics_clicked(self) -> None:
        self._state.reset_statistics()
        self._update_statistics_display()
        self._refresh_plot(force=True)

    def _on_auto_scale_y_toggled(self, checked: bool) -> None:
        self._auto_scale_y = bool(checked)
        self.y_min_spin.setEnabled(not checked)
        self.y_max_spin.setEnabled(not checked)
        self._settings.setValue("noise_monitor/auto_scale_y", bool(checked))
        self.plot.enableAutoRange(y=bool(checked))
        if not checked:
            self._apply_manual_y_range()

    def _on_manual_y_range_changed(self, *_args) -> None:
        minimum = float(self.y_min_spin.value())
        maximum = float(self.y_max_spin.value())
        if not self._state.valid_y_range(minimum, maximum):
            self._set_status("Noise monitor Y range ignored: Y min must be lower than Y max.", 3000)
            return
        self._last_valid_y_range = (minimum, maximum)
        self._settings.setValue("noise_monitor/y_min", minimum)
        self._settings.setValue("noise_monitor/y_max", maximum)
        if not self.auto_scale_y_checkbox.isChecked():
            self._apply_manual_y_range()

    def _apply_manual_y_range(self) -> None:
        minimum, maximum = self._last_valid_y_range
        self.plot.enableAutoRange(y=False)
        self.plot.setYRange(minimum, maximum, padding=0.0)

    def _on_audio_output_changed(self, index: int) -> None:
        device_id = self.audio_output_combo.itemData(index)
        if device_id is None:
            return
        new_device_id = str(device_id)
        if new_device_id == self._selected_audio_device_id:
            return
        self._selected_audio_device_id = new_device_id
        self._settings.setValue("noise_monitor/audio_device_id", new_device_id)
        self._settings.setValue("noise_monitor/audio_device_label", self._selected_audio_label())
        self._audio_error_message = ""
        self._stop_test_tone()
        if self.logger is not None:
            self.logger.info("Noise monitor selected audio device: %s", self._selected_audio_label())
        if self.sound_button.isChecked():
            self._restart_sound_for_selected_device()
        self._update_audio_status_label()
        self._update_button_text()

    def _on_refresh_audio_clicked(self) -> None:
        self._stop_test_tone()
        self._refresh_audio_devices(initial=False, manual=True)

    def _on_test_tone_clicked(self) -> None:
        if not self._can_attempt_audio():
            self._set_status(self._audio_error_message or "Audio output unavailable.", 4000)
            return
        if self.sound_button.isChecked():
            self._set_status("Test tone ignored while live sound is running.", 3000)
            return
        self._stop_test_tone()
        try:
            device_info = self._selected_device_info()
            self._test_tone_controller = _QtAudioToneManager(self, device_info=device_info)
            self._test_tone_controller.set_frequency(1000.0)
            self._test_tone_timer.start(700)
            self._audio_error_message = ""
            if self.logger is not None:
                self.logger.info("Noise monitor test tone started on: %s", self._selected_audio_label())
            self._set_status(f"Test tone on {self._selected_audio_label()}", 2000)
        except Exception as exc:
            self._stop_test_tone()
            self._handle_audio_failure(f"Test tone failed on {self._selected_audio_label()}: {exc}")

    def _on_clear_clicked(self) -> None:
        self._state.clear_history()
        self._refresh_plot(force=True)

    def _ensure_sound_controller(self) -> bool:
        if not self._monitor_active:
            return False
        if not self._can_attempt_audio():
            self._set_status(self._audio_error_message or "Noise sound unavailable.", 4000)
            return False
        if self._audio_controller is not None:
            return True
        try:
            device_info = self._selected_device_info()
            self._audio_controller = _QtAudioToneManager(self, device_info=device_info)
            self._active_audio_device_id = self._selected_audio_device_id
            self._audio_error_message = ""
            if self.logger is not None:
                self.logger.info("Noise monitor sound started on: %s", self._selected_audio_label())
        except Exception as exc:  # pragma: no cover - depends on local audio device
            self._audio_controller = None
            self._active_audio_device_id = None
            self._handle_audio_failure(f"Sound start failed on {self._selected_audio_label()}: {exc}")
            return False
        if self._audio_controller.has_error():
            reason = self._audio_controller.error_string() or "audio backend error"
            self._stop_sound()
            self._handle_audio_failure(f"Sound start failed on {self._selected_audio_label()}: {reason}")
            return False
        self._update_audio_status_label()
        return True

    def _handle_audio_failure(self, reason: str) -> None:
        self._audio_error_message = str(reason or "audio backend unavailable")
        if self.logger is not None:
            self.logger.warning("Noise measurement sound disabled: %s", reason)
        self._set_status(f"Noise sound disabled: {reason}", 5000)
        try:
            self.sound_button.blockSignals(True)
            self.sound_button.setChecked(False)
        finally:
            self.sound_button.blockSignals(False)
        self.sound_button.setToolTip(self._audio_error_message)
        self._update_audio_status_label()
        self._update_button_text()

    def _stop_sound(self) -> None:
        if self._audio_controller is None:
            return
        try:
            self._audio_controller.stop()
        finally:
            self._audio_controller = None
            self._active_audio_device_id = None

    def _stop_test_tone(self) -> None:
        controller = self._test_tone_controller
        self._test_tone_controller = None
        if controller is None:
            return
        try:
            controller.stop()
        except Exception:
            pass

    def _restart_sound_for_selected_device(self) -> None:
        was_checked = bool(self.sound_button.isChecked())
        self._stop_sound()
        if not was_checked:
            return
        try:
            self.sound_button.blockSignals(True)
            self.sound_button.setChecked(False)
        finally:
            self.sound_button.blockSignals(False)
        if self._ensure_sound_controller():
            try:
                self.sound_button.blockSignals(True)
                self.sound_button.setChecked(True)
            finally:
                self.sound_button.blockSignals(False)
            self._apply_sound_target(self._sound_driver_value())
        self._update_button_text()

    def _update_button_text(self) -> None:
        self.monitor_button.setText("Monitor ON" if self._monitor_active else "Monitor OFF")
        self.relative_mode_button.setText("Relative ON" if self._state.relative_mode else "Relative OFF")
        if not self._monitor_active:
            self.sound_button.setText("Sound OFF")
        elif not self._can_attempt_audio():
            self.sound_button.setText("Sound N/A")
        else:
            self.sound_button.setText("Sound ON" if self.sound_button.isChecked() else "Sound OFF")
        window_s = self._state.current_window_s
        window_labels = {
            30.0: "30 s",
            60.0: "1 min",
            600.0: "10 min",
            3600.0: "1 h",
            86400.0: "24 h",
        }
        window_text = window_labels.get(window_s, f"{int(round(window_s))} s")
        self.window_button.setText(f"Window: {window_text}")
        self.sound_button.setEnabled(self._monitor_active and self._can_attempt_audio())
        self.test_tone_button.setEnabled(self._can_attempt_audio())

    def _update_display_labels(self) -> None:
        absolute_db = self._state.current_absolute_db
        self.absolute_value_label.setText("--" if absolute_db is None else f"{absolute_db:.2f} dB")
        relative_db = self._state.relative_db
        self.relative_value_label.setText("--" if relative_db is None else f"{relative_db:+.2f} dB")
        reference_db = self._state.reference_absolute_db
        self.reference_value_label.setText("Reference: --" if reference_db is None else f"Reference: {reference_db:.2f} dB")
        self._update_statistics_display()

    def _update_statistics_display(self) -> None:
        statistics = self._state.statistics()
        values = (statistics["min"], statistics["mean"], statistics["max"])
        labels = (self.statistics_min_label, self.statistics_mean_label, self.statistics_max_label)
        for label, value in zip(labels, values):
            label.setText("--" if value is None else f"{float(value):.2f} dB")

    def _update_statistics_plot_items(self) -> None:
        statistics = self._state.statistics()
        if not statistics["count"]:
            self.statistics_mean_line.setVisible(False)
            self.statistics_min_marker.setData([])
            self.statistics_max_marker.setData([])
            self.statistics_text.setText("Min --   Mean --   Max --")
        else:
            minimum = float(statistics["min"])
            mean = float(statistics["mean"])
            maximum = float(statistics["max"])
            self.statistics_mean_line.setPos(mean)
            self.statistics_mean_line.setVisible(True)
            self.statistics_min_marker.setData(
                [float(statistics["min_timestamp"])], [minimum]
            )
            self.statistics_max_marker.setData(
                [float(statistics["max_timestamp"])], [maximum]
            )
            self.statistics_text.setText(f"Min {minimum:.2f}   Mean {mean:.2f}   Max {maximum:.2f} dB")
        view_range = self.plot.getViewBox().viewRange()
        self.statistics_text.setPos(float(view_range[0][0]), float(view_range[1][1]))

    def _on_refresh_timer(self) -> None:
        if not self._monitor_active:
            return
        if self._has_pending_measurement:
            applied = self._state.update_absolute(
                self._latest_absolute_db,
                timestamp_s=self._latest_timestamp_s,
                record_statistics=False,
            )
            self._has_pending_measurement = False
            if applied:
                self._state.append_history_point(timestamp_s=self._latest_timestamp_s)
        self._update_display_labels()
        self._refresh_plot()
        self._refresh_sound()

    def _refresh_plot(self, force: bool = False) -> None:
        _ = force
        now_s = time.time()
        if not self._monitor_active:
            self.plot_curve.setData([], [])
            self.plot.setXRange(now_s - self._state.current_window_s, now_s, padding=0.0)
            self._update_statistics_plot_items()
            return
        xs, ys = self._state.plot_series(now_s=now_s)
        if not xs or not ys:
            self.plot_curve.setData([], [])
            self.plot.setXRange(now_s - self._state.current_window_s, now_s, padding=0.0)
            self._update_statistics_plot_items()
            return
        self.plot_curve.setData(xs, ys)
        self.plot.setXRange(now_s - self._state.current_window_s, now_s, padding=0.0)
        self._update_statistics_plot_items()

    def _update_plot_axis_label(self) -> None:
        new_mode = "relative" if self._state.relative_mode else "absolute"
        if new_mode == self._axis_mode:
            return
        self._axis_mode = new_mode
        self.plot.setLabel("left", "Relative Noise (dB)" if new_mode == "relative" else "Noise Power (dB)")

    def _sound_driver_value(self) -> float | None:
        if self._state.relative_mode:
            relative_db = self._state.relative_db
            if relative_db is not None:
                return float(relative_db)
        absolute_db = self._state.current_absolute_db
        return float(absolute_db) if absolute_db is not None else None

    def _apply_sound_target(self, value_db: float | None) -> None:
        if not self._monitor_active or self._audio_controller is None or not self.sound_button.isChecked():
            return
        try:
            frequency_hz = self._map_sound_frequency(value_db)
            self._audio_controller.set_frequency(frequency_hz)
            if self._audio_controller.has_error():
                raise RuntimeError(self._audio_controller.error_string() or "audio streaming error")
        except Exception as exc:
            self._stop_sound()
            self._handle_audio_failure(f"Audio stream failed on {self._selected_audio_label()}: {exc}")

    def _refresh_sound(self) -> None:
        if not self._monitor_active or self._audio_controller is None:
            return
        self._apply_sound_target(self._sound_driver_value())

    def _map_sound_frequency(self, value_db: float | None) -> float:
        if value_db is None or not math.isfinite(float(value_db)):
            return 0.0
        if self._state.relative_mode and self._state.has_reference:
            return float(
                max(
                    _SOUND_MIN_FREQ_HZ,
                    min(_SOUND_MAX_FREQ_HZ, _RELATIVE_SOUND_CENTER_HZ + (_RELATIVE_SOUND_HZ_PER_DB * float(value_db))),
                )
            )
        local_range = self._state.recent_absolute_range()
        if local_range is None:
            return _RELATIVE_SOUND_CENTER_HZ
        minimum, maximum = local_range
        midpoint = 0.5 * (minimum + maximum)
        span_db = max(_ABSOLUTE_SOUND_MIN_SPAN_DB, maximum - minimum)
        lower_bound = midpoint - (0.5 * span_db)
        normalized = (float(value_db) - lower_bound) / span_db
        normalized = max(0.0, min(1.0, normalized))
        return _ABSOLUTE_SOUND_MIN_FREQ_HZ + ((_ABSOLUTE_SOUND_MAX_FREQ_HZ - _ABSOLUTE_SOUND_MIN_FREQ_HZ) * normalized)

    def _set_status(self, message: str, duration_ms: int) -> None:
        if self.status_callback is not None:
            try:
                self.status_callback(message, duration_ms)
            except Exception:
                pass

    def _log_invalid_measurement(self, reason: str) -> None:
        if self.logger is None:
            return
        now = time.monotonic()
        if now - self._last_invalid_log_monotonic < 5.0:
            return
        self._last_invalid_log_monotonic = now
        self.logger.debug("Noise measurement ignored invalid value: %s", reason)

    def _can_attempt_audio(self) -> bool:
        return self._audio_backend_available and self._selected_audio_device_id != _NO_AUDIO_DEVICE_ID

    def _selected_audio_label(self) -> str:
        for entry in self._audio_devices:
            if str(entry.get("id")) == str(self._selected_audio_device_id):
                return str(entry.get("label"))
        if self._selected_audio_device_id == _DEFAULT_AUDIO_DEVICE_ID:
            return "Default system output"
        return "No audio output"

    def _selected_device_info(self):
        if not self._audio_backend_available:
            return None
        if self._selected_audio_device_id == _DEFAULT_AUDIO_DEVICE_ID:
            return None
        for entry in self._audio_devices:
            if str(entry.get("id")) == str(self._selected_audio_device_id):
                return entry.get("device")
        raise RuntimeError(f"Selected audio device is missing: {self._selected_audio_device_id}")

    def _audio_device_labels(self, devices: list[dict[str, object]] | None = None) -> str:
        source = self._audio_devices if devices is None else devices
        return ", ".join(str(entry.get("label")) for entry in source) or "<none>"

    def _refresh_audio_summary(self, default_device_name: str) -> str:
        detected_count = max(0, len(self._audio_devices) - 1)
        if self._selected_audio_device_id == _NO_AUDIO_DEVICE_ID:
            return f"Audio refresh: no output detected (default={default_device_name})."
        return (
            f"Audio refresh: {detected_count} output(s) detected "
            f"(default={default_device_name}, selected={self._selected_audio_label()})."
        )

    def _refresh_audio_devices(self, *, initial: bool, manual: bool = False) -> None:
        previous_selected_id = str(self._selected_audio_device_id)
        previous_selected_label = str(self._settings.value("noise_monitor/audio_device_label", ""))
        active_sound = bool(self.sound_button.isChecked())
        if not self._audio_backend_available:
            self._audio_devices = [{"id": _NO_AUDIO_DEVICE_ID, "label": "No audio output", "device": None, "name": "", "realm": ""}]
            self._selected_audio_device_id = _NO_AUDIO_DEVICE_ID
            self._audio_error_message = _qt_multimedia_unavailable_reason()
            self._rebuild_audio_combo()
            self._update_audio_status_label()
            self._update_button_text()
            if manual:
                message = f"Audio refresh: {self._audio_error_message}"
                if self.logger is not None:
                    self.logger.warning(message)
                self._set_status(message, 5000)
            return

        try:
            default_device = QAudioDeviceInfo.defaultOutputDevice()
            default_device_name = (
                str(default_device.deviceName())
                if default_device is not None and not default_device.isNull()
                else "<none>"
            )
        except Exception as exc:
            default_device_name = f"<error:{exc}>"

        try:
            detected_devices = _QtAudioToneManager.list_output_devices()
        except Exception as exc:
            detected_devices = []
            self._audio_error_message = f"Audio enumeration failed: {exc}"
            if self.logger is not None:
                self.logger.exception("Noise monitor audio enumeration failed")
        if self.logger is not None:
            log_method = self.logger.warning if manual else self.logger.info
            log_method(
                "Noise monitor audio refresh default=%s devices=%s",
                default_device_name,
                self._audio_device_labels(detected_devices),
            )

        self._audio_devices = [_QtAudioToneManager.default_output_entry(), *detected_devices]
        available_ids = {str(entry.get("id")) for entry in self._audio_devices}
        active_device_missing = active_sound and self._active_audio_device_id not in available_ids
        if len(self._audio_devices) == 1:
            self._audio_devices = [{"id": _NO_AUDIO_DEVICE_ID, "label": "No audio output", "device": None, "name": "", "realm": ""}]
            self._selected_audio_device_id = _NO_AUDIO_DEVICE_ID
            self._audio_error_message = "No audio output device detected."
        else:
            if previous_selected_id in available_ids:
                self._selected_audio_device_id = previous_selected_id
                if initial and self.logger is not None and previous_selected_id not in {_DEFAULT_AUDIO_DEVICE_ID, _NO_AUDIO_DEVICE_ID}:
                    self.logger.info("Noise monitor restored saved audio device: %s", self._selected_audio_label())
            else:
                if previous_selected_id not in {_DEFAULT_AUDIO_DEVICE_ID, _NO_AUDIO_DEVICE_ID} and self.logger is not None:
                    label_hint = previous_selected_label or previous_selected_id
                    self.logger.warning("Noise monitor saved audio device missing, falling back to default: %s", label_hint)
                self._selected_audio_device_id = _DEFAULT_AUDIO_DEVICE_ID
            self._audio_error_message = ""

        self._settings.setValue("noise_monitor/audio_device_id", self._selected_audio_device_id)
        self._settings.setValue("noise_monitor/audio_device_label", self._selected_audio_label())
        self._rebuild_audio_combo()
        self._update_audio_status_label()
        self._update_button_text()
        if manual:
            self._set_status(self._refresh_audio_summary(default_device_name), 5000)

        selected_device_still_exists = previous_selected_id in {str(entry.get("id")) for entry in self._audio_devices}
        if active_sound and (not selected_device_still_exists or active_device_missing):
            self._stop_sound()
            try:
                self.sound_button.blockSignals(True)
                self.sound_button.setChecked(False)
            finally:
                self.sound_button.blockSignals(False)
            self._handle_audio_failure("Active audio device disappeared. Sound stopped.")
        elif active_sound and not initial and previous_selected_id != self._selected_audio_device_id:
            self._restart_sound_for_selected_device()

    def _rebuild_audio_combo(self) -> None:
        try:
            self.audio_output_combo.blockSignals(True)
            self.audio_output_combo.clear()
            selected_index = 0
            for index, entry in enumerate(self._audio_devices):
                self.audio_output_combo.addItem(str(entry.get("label")), str(entry.get("id")))
                if str(entry.get("id")) == str(self._selected_audio_device_id):
                    selected_index = index
            self.audio_output_combo.setCurrentIndex(selected_index)
        finally:
            self.audio_output_combo.blockSignals(False)

    def _update_audio_status_label(self) -> None:
        if not self._audio_backend_available:
            self.audio_status_label.setText("Audio: unavailable")
            self.audio_status_label.setToolTip(self._audio_error_message)
            return
        if self._selected_audio_device_id == _NO_AUDIO_DEVICE_ID:
            self.audio_status_label.setText("Audio: unavailable")
            self.audio_status_label.setToolTip(self._audio_error_message)
            return
        if self._audio_error_message:
            self.audio_status_label.setText(f"Audio error: {self._selected_audio_label()}")
            self.audio_status_label.setToolTip(self._audio_error_message)
            return
        self.audio_status_label.setText(f"Audio: {self._selected_audio_label()}")
        self.audio_status_label.setToolTip(self._selected_audio_label())

    def closeEvent(self, event) -> None:
        self.shutdown()
        super().closeEvent(event)

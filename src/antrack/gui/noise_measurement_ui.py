"""Noise Monitor tab wiring for Antrack."""

from __future__ import annotations

import math
import time

from PyQt5.QtCore import QTimer
from PyQt5.QtWidgets import QVBoxLayout, QWidget

from antrack.gui.widgets.noise_measurement import NoiseMeasurementWidget


class NoiseMeasurementUiMixin:
    """Populate and manage the Noise Monitor tab."""

    def setup_noise_measurement_ui(self) -> None:
        if getattr(self, "_noise_measurement_ui_ready", False):
            return
        tab_widget = getattr(self, "tabWidget", None)
        scan_tab = getattr(self, "tab_10", None)
        if tab_widget is None or scan_tab is None:
            return

        self._noise_measurement_ui_ready = True
        self.tab_noise_measurement = QWidget(tab_widget)
        self.tab_noise_measurement.setObjectName("tab_noise_measurement")
        insert_index = tab_widget.indexOf(scan_tab)
        if insert_index < 0:
            insert_index = tab_widget.count()
        tab_widget.insertTab(insert_index, self.tab_noise_measurement, "Noise Monitor")

        root_layout = self.tab_noise_measurement.layout()
        if root_layout is None:
            root_layout = QVBoxLayout(self.tab_noise_measurement)
        self._clear_layout(root_layout)

        self.noise_measurement_widget = NoiseMeasurementWidget(
            self.tab_noise_measurement,
            logger=self.logger.getChild("NoiseMeasurement"),
            status_callback=lambda message, duration_ms=3000: self.status_bar.showMessage(message, duration_ms),
        )
        self.noise_measurement_widget.monitorToggled.connect(self._set_noise_measurement_active)
        root_layout.addWidget(self.noise_measurement_widget)
        self._noise_measurement_update_timer = QTimer(self.tab_noise_measurement)
        self._noise_measurement_update_timer.setInterval(100)
        self._noise_measurement_update_timer.timeout.connect(self._poll_noise_measurement_value)
        self._noise_measurement_measurement_dirty = False
        self._noise_measurement_active = False
        self._noise_measurement_last_invalid_log_monotonic = 0.0

        if getattr(self, "sdr_client", None) is not None and getattr(self.sdr_client, "data_storage", None) is not None:
            self.sdr_client.data_storage.data_updated.connect(self._mark_noise_measurement_dirty)
            self.sdr_client.data_storage.data_recalculated.connect(self._mark_noise_measurement_dirty)
            self.sdr_client.started.connect(self._mark_noise_measurement_dirty)
            self.sdr_client.stopped.connect(self._mark_noise_measurement_dirty)

    def _mark_noise_measurement_dirty(self, *_args) -> None:
        if not getattr(self, "_noise_measurement_active", False):
            return
        self._noise_measurement_measurement_dirty = True

    def _set_noise_measurement_active(self, active: bool) -> None:
        self._noise_measurement_active = bool(active)
        self._noise_measurement_measurement_dirty = bool(active)
        if self._noise_measurement_active:
            self._noise_measurement_update_timer.start()
            QTimer.singleShot(0, self._poll_noise_measurement_value)
        else:
            self._noise_measurement_update_timer.stop()

    def _poll_noise_measurement_value(self) -> None:
        widget = getattr(self, "noise_measurement_widget", None)
        if widget is None:
            return
        if not getattr(self, "_noise_measurement_active", False):
            return
        if not getattr(self, "_noise_measurement_measurement_dirty", False):
            return
        self._noise_measurement_measurement_dirty = False
        metrics = getattr(self, "_sdr_last_absolute_metrics", None)
        if not metrics:
            if not bool(getattr(getattr(self, "sdr_client", None), "running", False)):
                widget.clear_measurement()
            return
        value_db = metrics.get("integrated_db")
        if value_db is None:
            self._log_noise_measurement_invalid("missing integrated_db")
            return
        try:
            numeric_value = float(value_db)
        except Exception:
            self._log_noise_measurement_invalid("invalid numeric conversion")
            return
        if not math.isfinite(numeric_value):
            self._log_noise_measurement_invalid("non-finite integrated_db")
            return
        widget.set_measurement(numeric_value, timestamp_s=time.time())

    def _log_noise_measurement_invalid(self, reason: str) -> None:
        now = time.monotonic()
        last = float(getattr(self, "_noise_measurement_last_invalid_log_monotonic", 0.0))
        if now - last < 5.0:
            return
        self._noise_measurement_last_invalid_log_monotonic = now
        self.logger.debug("Noise measurement ignored invalid SDR sample: %s", reason)

    def close_noise_measurement_ui(self) -> None:
        timer = getattr(self, "_noise_measurement_update_timer", None)
        if timer is not None:
            timer.stop()
        widget = getattr(self, "noise_measurement_widget", None)
        if widget is not None:
            widget.shutdown()

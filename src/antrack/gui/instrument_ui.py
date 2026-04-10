"""Instrument-related UI wiring with transitional powermeter support."""

from __future__ import annotations

from antrack.gui.instruments.powermeter_qt import Powermeter


class InstrumentUiMixin:
    """Own transitional instrument UI glue outside the composition root."""

    def setup_instrument_ui(self):
        """Initialize the current instrument backend and bind existing widgets."""
        if getattr(self, "instrument", None) is not None:
            return

        self.instrument = Powermeter(self.settings, logger=self.logger.getChild("Instrument"))
        self.powermeter = self.instrument  # Transitional alias for current behavior.
        self.instrument.power_ready.connect(self._on_instrument_value)

        if hasattr(self, "pushButton_readpowermeter"):
            self.pushButton_readpowermeter.clicked.connect(self.start_instrument_read)

    def _on_instrument_value(self, val_dbm: float):
        try:
            self.status_bar.showMessage(f"Instrument: {val_dbm:.2f} dBm", 3000)
            if hasattr(self, "label_powermeter_dbm"):
                self.label_powermeter_dbm.setText(f"{val_dbm:.2f} dBm")
        except Exception:
            pass

    def start_instrument_read(self):
        worker = self.thread_manager.start_thread(
            "InstrumentRead",
            self.instrument.read_power,
        )
        worker.result.connect(self._on_instrument_value)
        worker.error.connect(lambda msg: self.logger.error(f"[InstrumentRead] {msg}"))
        worker.status.connect(lambda status: self.logger.info(f"[InstrumentRead] {status}"))

    def _on_powermeter_value(self, val_dbm: float):
        """Compatibility wrapper while UI widgets still use powermeter naming."""
        self._on_instrument_value(val_dbm)

    def start_powermeter_read(self):
        """Compatibility wrapper while callers still use powermeter naming."""
        self.start_instrument_read()

    def close_instrument_ui(self):
        try:
            if getattr(self, "instrument", None):
                self.instrument.close()
        except Exception:
            pass

"""Frequency control widget with wheel-adjustable digits."""

from __future__ import annotations

from PyQt5 import QtCore
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QHBoxLayout, QLabel, QWidget


class _FrequencyDigitLabel(QLabel):
    step_requested = QtCore.pyqtSignal(int)

    def __init__(self, step_hz: int, parent=None) -> None:
        super().__init__("0", parent)
        self._step_hz = int(step_hz)
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumWidth(20)
        self.setStyleSheet("font-size: 36px; font-weight: 700; color: #202020;")

    def set_dimmed(self, dimmed: bool) -> None:
        color = "#8e8e8e" if dimmed else "#202020"
        self.setStyleSheet(f"font-size: 36px; font-weight: 700; color: {color};")

    def wheelEvent(self, event) -> None:  # noqa: N802
        delta = event.angleDelta().y()
        if delta == 0:
            event.ignore()
            return
        self.step_requested.emit(self._step_hz if delta > 0 else -self._step_hz)
        event.accept()


class FrequencyControlWidget(QWidget):
    """Display frequency in Hz as xxxx.xxx.xxx.xxx with per-digit wheel edits."""

    valueChanged = QtCore.pyqtSignal(float)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._min_hz = 1_000
        self._max_hz = 6_000_000_000
        self._value_hz = 137_000_000
        self._digit_widgets: list[_FrequencyDigitLabel] = []
        self._digit_steps = [
            1_000_000_000_000,
            100_000_000_000,
            10_000_000_000,
            1_000_000_000,
            100_000_000,
            10_000_000,
            1_000_000,
            100_000,
            10_000,
            1_000,
            100,
            10,
            1,
        ]

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(1)

        for index, step_hz in enumerate(self._digit_steps):
            if index in {4, 7, 10}:
                sep = QLabel(".", self)
                sep.setAlignment(Qt.AlignCenter)
                sep.setStyleSheet("font-size: 36px; font-weight: 700; color: #8a8a8a;")
                layout.addWidget(sep)
            digit = _FrequencyDigitLabel(step_hz, self)
            digit.step_requested.connect(self._apply_delta)
            self._digit_widgets.append(digit)
            layout.addWidget(digit)
        layout.addStretch(1)
        self._refresh_labels()

    def set_range_hz(self, minimum_hz: float, maximum_hz: float) -> None:
        self._min_hz = int(max(1, round(float(minimum_hz))))
        self._max_hz = int(max(self._min_hz, round(float(maximum_hz))))
        self.set_value_hz(self._value_hz)

    def value_hz(self) -> float:
        return float(self._value_hz)

    def set_value_hz(self, value_hz: float) -> None:
        self._value_hz = int(max(self._min_hz, min(self._max_hz, round(float(value_hz)))))
        self._refresh_labels()

    def _apply_delta(self, delta_hz: int) -> None:
        next_value = int(max(self._min_hz, min(self._max_hz, self._value_hz + int(delta_hz))))
        if next_value == self._value_hz:
            return
        self._value_hz = next_value
        self._refresh_labels()
        self.valueChanged.emit(float(self._value_hz))

    def _refresh_labels(self) -> None:
        digits = f"{int(self._value_hz):013d}"
        first_non_zero = next((idx for idx, char in enumerate(digits[:-1]) if char != "0"), len(digits) - 1)
        for index, widget in enumerate(self._digit_widgets):
            widget.setText(digits[index])
            widget.set_dimmed(index < first_non_zero)

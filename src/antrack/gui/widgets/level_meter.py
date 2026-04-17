"""Compact SDR level meter widget with selectable units."""

from __future__ import annotations

from PyQt5 import QtCore, QtGui
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QHBoxLayout, QLabel, QMenu, QSizePolicy, QToolButton, QVBoxLayout, QWidget


class _LevelBarWidget(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._value = 0.0
        self._minimum = -140.0
        self._maximum = 0.0
        self._relative = False
        self._unit_key = "dbm"
        self.setMinimumHeight(36)

    def set_measure(self, value: float, minimum: float, maximum: float, *, relative: bool, unit_key: str) -> None:
        self._value = float(value)
        self._minimum = float(minimum)
        self._maximum = float(max(minimum + 1e-6, maximum))
        self._relative = bool(relative)
        self._unit_key = str(unit_key).strip().lower()
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        rect = self.rect().adjusted(0, 4, -1, -10)
        painter.setPen(QtGui.QPen(QtGui.QColor("#98a0a6"), 1))
        painter.setBrush(QtGui.QColor("#243039"))
        painter.drawRoundedRect(rect, 4, 4)

        if self._relative:
            center_x = rect.center().x()
            painter.setPen(QtGui.QPen(QtGui.QColor("#d8e0e6"), 1))
            painter.drawLine(center_x, rect.top() + 2, center_x, rect.bottom() - 2)
            span = max(1e-6, max(abs(self._minimum), abs(self._maximum)))
            norm = max(-1.0, min(1.0, self._value / span))
            bar_width = int(abs(norm) * (rect.width() * 0.5))
            if bar_width > 0:
                color = QtGui.QColor("#58d26a") if norm >= 0.0 else QtGui.QColor("#e26a5a")
                if norm >= 0.0:
                    bar_rect = QtCore.QRect(center_x, rect.top() + 2, bar_width, rect.height() - 4)
                else:
                    bar_rect = QtCore.QRect(center_x - bar_width, rect.top() + 2, bar_width, rect.height() - 4)
                painter.fillRect(bar_rect, color)
        else:
            norm = (self._value - self._minimum) / max(1e-6, self._maximum - self._minimum)
            norm = max(0.0, min(1.0, norm))
            bar_width = int(norm * rect.width())
            if bar_width > 0:
                bar_rect = QtCore.QRect(rect.left(), rect.top() + 2, bar_width, rect.height() - 4)
                if self._unit_key == "dbm":
                    painter.fillRect(bar_rect, QtGui.QColor("#4ab8d8"))
                    return

                threshold_dbm = -93.0
                threshold_norm = (threshold_dbm - self._minimum) / max(1e-6, self._maximum - self._minimum)
                threshold_norm = max(0.0, min(1.0, threshold_norm))
                threshold_x = rect.left() + int(threshold_norm * rect.width())
                green_color = QtGui.QColor("#58d26a")
                red_color = QtGui.QColor("#d9534f")

                bar_right = bar_rect.right() + 1
                green_right = min(bar_right, threshold_x)
                if green_right > rect.left():
                    painter.fillRect(
                        QtCore.QRect(rect.left(), rect.top() + 2, green_right - rect.left(), rect.height() - 4),
                        green_color,
                    )
                if bar_right > max(rect.left(), threshold_x):
                    red_left = max(rect.left(), threshold_x)
                    painter.fillRect(
                        QtCore.QRect(red_left, rect.top() + 2, bar_right - red_left, rect.height() - 4),
                        red_color,
                    )


class _LevelScaleWidget(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._ticks: list[tuple[float, str, bool]] = []
        self.setMinimumHeight(20)

    def set_ticks(self, ticks: list[tuple[float, str, bool]]) -> None:
        self._ticks = list(ticks)
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.TextAntialiasing, True)
        rect = self.rect().adjusted(0, 0, -1, -1)
        painter.setPen(QtGui.QPen(QtGui.QColor("#202020"), 1))
        font = painter.font()
        font.setPointSize(8)
        painter.setFont(font)
        for position, label, show_label in self._ticks:
            x = rect.left() + int(max(0.0, min(1.0, position)) * rect.width())
            painter.drawLine(x, 0, x, 5)
            if not show_label:
                continue
            label_width = 44
            if label.startswith("+"):
                label_width = 32
            if position <= 0.001:
                text_rect = QtCore.QRect(x, 6, label_width, 12)
                align = Qt.AlignLeft | Qt.AlignTop
            elif position >= 0.999:
                text_rect = QtCore.QRect(x - label_width, 6, label_width, 12)
                align = Qt.AlignRight | Qt.AlignTop
            else:
                text_rect = QtCore.QRect(x - (label_width // 2), 6, label_width, 12)
                align = Qt.AlignHCenter | Qt.AlignTop
            painter.drawText(text_rect, align, label)

class LevelMeterWidget(QWidget):
    """Bargraph plus numeric label with clickable unit selector."""

    unitChanged = QtCore.pyqtSignal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._unit_key = "dbm"
        self._unit_text = "dBm"
        self._minimum = -130.0
        self._maximum = -30.0
        self._menu = QMenu(self)
        self._unit_button = QToolButton(self)
        self._unit_button.setPopupMode(QToolButton.InstantPopup)
        self._unit_button.setMenu(self._menu)
        self._unit_button.setText(self._unit_text)
        self._unit_button.setFixedWidth(56)
        self._unit_button.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self._bar = _LevelBarWidget(self)
        self._bar.setMinimumWidth(420)
        self._bar.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._scale = _LevelScaleWidget(self)
        self._value_label = QLabel("-", self)
        self._value_label.setMinimumWidth(96)
        self._value_label.setMaximumWidth(120)
        self._value_label.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self._value_label.setStyleSheet("color: #202020; font-weight: 600;")
        self.setMinimumHeight(46)

        center_box = QWidget(self)
        center_layout = QVBoxLayout(center_box)
        center_layout.setContentsMargins(0, 0, 0, 0)
        center_layout.setSpacing(0)
        center_layout.addWidget(self._bar)
        center_layout.addWidget(self._scale)

        value_box = QWidget(self)
        value_layout = QVBoxLayout(value_box)
        value_layout.setContentsMargins(0, 8, 0, 0)
        value_layout.setSpacing(0)
        value_layout.addWidget(self._value_label)
        value_layout.addStretch(1)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(self._unit_button)
        layout.addWidget(center_box, 1)
        layout.addWidget(value_box)

        self._add_unit_action("dBm", "dbm")
        self._add_unit_action("dBm/Hz", "db_per_hz")
        self._add_unit_action("S", "s_meter")

    def _add_unit_action(self, text: str, key: str) -> None:
        action = self._menu.addAction(text)
        action.triggered.connect(lambda _checked=False, unit_key=key: self.unitChanged.emit(unit_key))

    def set_unit(self, unit_key: str) -> None:
        mapping = {
            "dbm": "dBm",
            "db_per_hz": "dBm/Hz",
            "s_meter": "S",
        }
        normalized = str(unit_key).strip().lower()
        self._unit_key = normalized if normalized in mapping else "dbm"
        self._unit_text = mapping.get(self._unit_key, "dBm")
        self._unit_button.setText(self._unit_text)
        self._update_scale_ticks(relative=False)

    def _update_scale_ticks(self, *, relative: bool) -> None:
        if relative:
            values = list(range(-40, 41, 10))
            ticks = [
                ((value + 40.0) / 80.0, f"{value:+d}".replace("+0", "0"), value % 20 == 0)
                for value in values
            ]
        elif self._unit_key == "s_meter":
            minimum = float(self._minimum)
            maximum = float(self._maximum)
            span = max(1e-6, maximum - minimum)
            smeter_points = [
                (-147.0, "S", True),
                (-141.0, "1", True),
                (-135.0, "2", False),
                (-129.0, "3", True),
                (-123.0, "4", False),
                (-117.0, "5", True),
                (-111.0, "6", False),
                (-105.0, "7", True),
                (-99.0, "8", False),
                (-93.0, "9", True),
                (-73.0, "+20", True),
                (-53.0, "+40", True),
                (-33.0, "+60", True),
            ]
            ticks = [
                ((value_dbm - minimum) / span, label, show_label)
                for value_dbm, label, show_label in smeter_points
                if minimum - 1e-6 <= value_dbm <= maximum + 1e-6
            ]
        else:
            minimum = float(self._minimum)
            maximum = float(self._maximum)
            start = int(10 * round(minimum / 10.0))
            stop = int(10 * round(maximum / 10.0))
            if stop <= start:
                stop = start + 10
            values = list(range(start, stop + 1, 10))
            span = max(1e-6, maximum - minimum)
            ticks = [
                ((value - minimum) / span, str(value), ((value - start) % 20) == 0)
                for value in values
            ]
        self._scale.set_ticks(ticks)

    def set_display(self, *, value: float | None, text: str, minimum: float, maximum: float, relative: bool) -> None:
        self._minimum = float(minimum)
        self._maximum = float(maximum)
        self._value_label.setText(text)
        self._update_scale_ticks(relative=relative)
        if value is None:
            self._bar.set_measure(minimum, minimum, maximum, relative=relative, unit_key=self._unit_key)
            return
        self._bar.set_measure(float(value), float(minimum), float(maximum), relative=relative, unit_key=self._unit_key)

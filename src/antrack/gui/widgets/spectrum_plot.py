"""Spectrum plot widget adapted from the RSPdx display design."""

from __future__ import annotations

import collections
import math

import numpy as np
import pyqtgraph as pg
from PyQt5 import QtCore
from PyQt5.QtCore import pyqtProperty, pyqtSignal
from PyQt5.QtWidgets import QVBoxLayout, QWidget


pg.setConfigOptions(antialias=False)


class SpectrumPlotWidget(QWidget):
    """Real-time dB spectrum plot with optional peak hold, average, and baseline display."""

    visible_span_changed = pyqtSignal(float, int)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._max_display_bins = 8192
        self._fill_display_bins = 4096
        self._peak_hold_enabled = True
        self._average_enabled = True
        self._baseline_enabled = False
        self._subtract_baseline = False
        self._main_fill_level = -120.0
        self._main_fill_brush = pg.mkBrush(20, 95, 120, 120)
        self._main_color = pg.mkColor(110, 235, 255)
        self._peak_hold_max_color = pg.mkColor("r")
        self._peak_hold_min_color = pg.mkColor("b")
        self._average_color = pg.mkColor("c")
        self._baseline_color = pg.mkColor("m")
        self._persistence_enabled = False
        self._persistence_length = 5
        self._persistence_decay = "exponential"
        self._persistence_color = pg.mkColor("g")
        self._persistence_data = None
        self._persistence_curves = []
        self._last_xrange = None
        self._last_visible_span_emit = None
        self._pending_visible_span = 0.0
        self._pending_plot_width = 0
        self._baseline_cache_key = None
        self._baseline_cache_y = None
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.graphics = pg.GraphicsLayoutWidget(self)
        layout.addWidget(self.graphics)

        self.pos_label = self.graphics.addLabel(row=0, col=0, justify="right")
        self.plot = self.graphics.addPlot(row=1, col=0)
        self.plot.showGrid(x=True, y=True)
        self.plot.setLabel("left", "Power", units="dB")
        self.plot.setLabel("bottom", "Frequency", units="Hz")
        self.plot.setClipToView(True)
        self.plot.setDownsampling(auto=True, mode="subsample")
        self.plot.showButtons()
        self.plot.getViewBox().sigXRangeChanged.connect(self._on_view_xrange_changed)

        self.curve_fill = self.plot.plot(pen=None)
        self.curve_fill.setFillLevel(self._main_fill_level)
        self.curve_fill.setBrush(self._main_fill_brush)
        self.curve_fill.setZValue(880)

        self.curve = self.plot.plot(pen=self._main_color)
        self.curve.setZValue(900)
        self.curve_peak_hold_max = self.plot.plot(pen=self._peak_hold_max_color)
        self.curve_peak_hold_max.setZValue(800)
        self.curve_peak_hold_min = self.plot.plot(pen=self._peak_hold_min_color)
        self.curve_peak_hold_min.setZValue(800)
        self.curve_average = self.plot.plot(pen=self._average_color)
        self.curve_average.setZValue(700)
        self.curve_baseline = self.plot.plot(pen=self._baseline_color)
        self.curve_baseline.setZValue(500)

        self.v_line = pg.InfiniteLine(angle=90, movable=False, pen="cyan")
        self.h_line = pg.InfiniteLine(angle=0, movable=False, pen="cyan")
        self.v_line.setZValue(1000)
        self.h_line.setZValue(1000)
        self.plot.addItem(self.v_line, ignoreBounds=True)
        self.plot.addItem(self.h_line, ignoreBounds=True)
        self.mouse_proxy = pg.SignalProxy(self.plot.scene().sigMouseMoved, rateLimit=30, slot=self._mouse_moved)

        self._visible_span_timer = QtCore.QTimer(self)
        self._visible_span_timer.setSingleShot(True)
        self._visible_span_timer.setInterval(120)
        self._visible_span_timer.timeout.connect(self._emit_visible_span)

    def _mouse_moved(self, evt) -> None:
        pos = evt[0]
        if self.plot.sceneBoundingRect().contains(pos):
            mouse_point = self.plot.vb.mapSceneToView(pos)
            self.pos_label.setText(
                f"<span style='font-size: 12pt'>f={mouse_point.x() / 1e6:0.3f} MHz, P={mouse_point.y():0.3f} dB</span>"
            )
            self.v_line.setPos(mouse_point.x())
            self.h_line.setPos(mouse_point.y())

    def _on_view_xrange_changed(self, *_args) -> None:
        try:
            xr = self.plot.getViewBox().viewRange()[0]
            span = abs(float(xr[1]) - float(xr[0]))
            width_px = int(max(1, round(self.plot.getViewBox().sceneBoundingRect().width())))
        except Exception:
            return
        if not np.isfinite(span) or span <= 0.0:
            return
        self._pending_visible_span = span
        self._pending_plot_width = width_px
        self._visible_span_timer.start()

    def _emit_visible_span(self) -> None:
        span = float(self._pending_visible_span)
        width_px = int(max(1, self._pending_plot_width))
        if not np.isfinite(span) or span <= 0.0:
            return
        key = (round(span, 3), width_px)
        if self._last_visible_span_emit == key:
            return
        self._last_visible_span_emit = key
        self.visible_span_changed.emit(span, width_px)

    def _decimate_line_for_display(self, x, y, *, mode: str = "peak", max_bins: int | None = None):
        x = np.asarray(x)
        y = np.asarray(y)
        n = int(y.size)
        max_bins = int(max(512, self._max_display_bins if max_bins is None else max_bins))
        if n <= max_bins:
            return x, y
        step = int(np.ceil(n / float(max_bins)))
        m = int(np.ceil(n / float(step)))
        pad = m * step - n
        if pad > 0:
            x_pad = np.pad(x, (0, pad), mode="edge")
            y_pad = np.pad(y, (0, pad), mode="edge")
        else:
            x_pad = x
            y_pad = y
        x_r = x_pad.reshape(m, step)
        y_r = y_pad.reshape(m, step)
        with np.errstate(all="ignore"):
            if mode == "median":
                y_d = np.nanmedian(y_r, axis=1)
            elif mode == "mean":
                y_d = np.nanmean(y_r, axis=1)
            else:
                y_d = np.nanmax(y_r, axis=1)
        y_d = np.where(np.isfinite(y_d), y_d, np.nan)
        x_d = x_r[:, step // 2]
        return x_d, y_d

    def _display_peak_bins(self) -> int:
        try:
            vb = self.plot.getViewBox()
            width_px = int(max(256, round(vb.sceneBoundingRect().width())))
        except Exception:
            width_px = 1024
        return int(min(self._max_display_bins, max(512, width_px)))

    def _decay_linear(self, x: int, length: int) -> float:
        return (-x / length) + 1.0

    def _decay_exponential(self, x: int, length: int, const: float = 1 / 3) -> float:
        return math.e ** (-x / (length * const))

    def _get_decay(self):
        return self._decay_exponential if self._persistence_decay == "exponential" else self._decay_linear

    def _ensure_persistence_curves(self) -> None:
        if self._persistence_curves and len(self._persistence_curves) == self._persistence_length:
            return
        for curve in self._persistence_curves:
            try:
                self.plot.removeItem(curve)
            except Exception:
                pass
        self._persistence_curves = []
        decay = self._get_decay()
        for index in range(self._persistence_length):
            alpha = 255 * decay(index + 1, self._persistence_length + 1)
            color = self._persistence_color
            curve = self.plot.plot(pen=(color.red(), color.green(), color.blue(), alpha))
            curve.setZValue(600 - index)
            self._persistence_curves.append(curve)

    def _baseline_for_x(self, x: np.ndarray, bx: np.ndarray, by: np.ndarray) -> np.ndarray:
        key = (int(x.size), float(x[0]), float(x[-1]), id(bx), id(by))
        if key == self._baseline_cache_key and self._baseline_cache_y is not None:
            return self._baseline_cache_y
        base = np.interp(x, bx, by, left=float(by[0]), right=float(by[-1])).astype(np.float32)
        self._baseline_cache_key = key
        self._baseline_cache_y = base
        return base

    def _current_y_with_options(self, data_storage):
        y = np.asarray(data_storage.y, dtype=np.float32)
        if self._subtract_baseline and getattr(data_storage, "baseline", None) is not None and getattr(data_storage, "baseline_x", None) is not None:
            x = np.asarray(data_storage.x, dtype=np.float64)
            bx = np.asarray(data_storage.baseline_x, dtype=np.float64)
            by = np.asarray(data_storage.baseline, dtype=np.float64)
            if len(x) == len(bx) and np.allclose(x, bx, rtol=0.0, atol=1e-9):
                base = by.astype(np.float32, copy=False)
            else:
                base = self._baseline_for_x(x, bx, by)
            y = y - base
        return y

    def update_plot(self, data_storage, force: bool = False) -> None:
        if getattr(data_storage, "x", None) is None or getattr(data_storage, "y", None) is None:
            return
        self._ensure_persistence_curves()
        y_plot = self._current_y_with_options(data_storage)
        x_plot, y_peak = self._decimate_line_for_display(
            data_storage.x,
            y_plot,
            mode="peak",
            max_bins=self._display_peak_bins(),
        )

        x0 = float(data_storage.x[0])
        x1 = float(data_storage.x[-1])
        xr = (x0, x1)
        if self._last_xrange != xr:
            self.plot.setXRange(x0, x1)
            self._last_xrange = xr

        if self._subtract_baseline:
            try:
                y_max = float(np.nanmax(y_peak))
                y_min = float(np.nanmin(y_peak))
            except ValueError:
                y_min, y_max = 0.0, 6.0
            self.plot.setYRange(y_min - 1.0, y_max + 3.0)
            self.curve.setPen(pg.mkPen(pg.mkColor("w"), width=2))
            self.curve_fill.clear()
        else:
            self.plot.setYRange(-140.0, 20.0)
            self.curve.setPen(self._main_color)
            self.curve_fill.setFillLevel(self._main_fill_level)
            self.curve_fill.setBrush(self._main_fill_brush)
            self.curve_fill.setData(x_plot, y_peak, connect="finite")

        self.curve.setData(x_plot, y_peak, connect="finite")
        if self._peak_hold_enabled and getattr(data_storage, "peak_hold_max", None) is not None:
            self.curve_peak_hold_max.setData(data_storage.x, data_storage.peak_hold_max)
        elif force:
            self.curve_peak_hold_max.clear()
        if getattr(data_storage, "peak_hold_min", None) is not None:
            self.curve_peak_hold_min.setData(data_storage.x, data_storage.peak_hold_min)
        elif force:
            self.curve_peak_hold_min.clear()
        if self._average_enabled and getattr(data_storage, "average", None) is not None:
            self.curve_average.setData(data_storage.x, data_storage.average)
        elif force:
            self.curve_average.clear()
        if self._baseline_enabled and getattr(data_storage, "baseline", None) is not None and getattr(data_storage, "baseline_x", None) is not None:
            self.curve_baseline.setData(data_storage.baseline_x, data_storage.baseline)
        elif force:
            self.curve_baseline.clear()
        if self._persistence_enabled:
            if self._persistence_data is None:
                self._persistence_data = collections.deque(maxlen=self._persistence_length)
            else:
                for index, y_prev in enumerate(self._persistence_data):
                    self._persistence_curves[index].setData(data_storage.x, y_prev)
            self._persistence_data.appendleft(np.asarray(data_storage.y, dtype=np.float32))
        elif force:
            for curve in self._persistence_curves:
                curve.clear()

    def recalculate_plot(self, data_storage) -> None:
        if getattr(data_storage, "x", None) is None:
            return
        QtCore.QTimer.singleShot(0, lambda: self.update_plot(data_storage, force=True))

    def clear_plot(self) -> None:
        self.curve_fill.clear()
        self.curve.clear()
        self.curve_peak_hold_max.clear()
        self.curve_peak_hold_min.clear()
        self.curve_average.clear()
        self.curve_baseline.clear()
        if self._persistence_data is not None:
            self._persistence_data.clear()
        for curve in self._persistence_curves:
            curve.clear()

    def get_max_display_bins(self) -> int:
        return self._max_display_bins

    def set_max_display_bins(self, value: int) -> None:
        self._max_display_bins = int(max(512, value))

    def get_peak_hold_enabled(self) -> bool:
        return self._peak_hold_enabled

    def set_peak_hold_enabled(self, enabled: bool) -> None:
        self._peak_hold_enabled = bool(enabled)
        self.curve_peak_hold_max.setVisible(self._peak_hold_enabled)

    def get_average_enabled(self) -> bool:
        return self._average_enabled

    def set_average_enabled(self, enabled: bool) -> None:
        self._average_enabled = bool(enabled)
        self.curve_average.setVisible(self._average_enabled)

    def get_baseline_enabled(self) -> bool:
        return self._baseline_enabled

    def set_baseline_enabled(self, enabled: bool) -> None:
        self._baseline_enabled = bool(enabled)
        self.curve_baseline.setVisible(self._baseline_enabled)

    def get_subtract_baseline(self) -> bool:
        return self._subtract_baseline

    def set_subtract_baseline(self, enabled: bool) -> None:
        self._subtract_baseline = bool(enabled)

    maxDisplayBins = pyqtProperty(int, fget=get_max_display_bins, fset=set_max_display_bins)
    peakHoldEnabled = pyqtProperty(bool, fget=get_peak_hold_enabled, fset=set_peak_hold_enabled)
    averageEnabled = pyqtProperty(bool, fget=get_average_enabled, fset=set_average_enabled)
    baselineEnabled = pyqtProperty(bool, fget=get_baseline_enabled, fset=set_baseline_enabled)
    subtractBaseline = pyqtProperty(bool, fget=get_subtract_baseline, fset=set_subtract_baseline)

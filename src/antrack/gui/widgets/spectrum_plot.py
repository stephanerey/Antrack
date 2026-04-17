"""Spectrum plot widget with a single interactive receiver selection overlay."""

from __future__ import annotations

import time

import numpy as np
import pyqtgraph as pg
from PyQt5 import QtCore
from PyQt5.QtWidgets import QVBoxLayout, QWidget

from antrack.core.dsp.snr import bin_width_to_density_offset_db, db_to_linear_power, linear_power_to_db


pg.setConfigOptions(antialias=False)


class SpectrumPlotWidget(QWidget):
    """Real-time dB spectrum plot with a draggable center-frequency/bandwidth overlay."""

    selection_frequency_changed = QtCore.pyqtSignal(float)
    selection_bandwidth_changed = QtCore.pyqtSignal(float)
    visible_span_changed = QtCore.pyqtSignal(float, int)
    frequency_clicked = QtCore.pyqtSignal(float)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._max_display_bins = 8192
        self._y_min_db = -140.0
        self._y_range_db = 160.0
        self._main_fill_level = self._y_min_db
        self._main_fill_brush = pg.mkBrush(20, 95, 120, 120)
        self._main_color = pg.mkColor(110, 235, 255)
        self._selection_color = pg.mkColor(215, 60, 60)
        self._selection_freq_hz = 137_000_000.0
        self._selection_bw_hz = 25_000.0
        self._max_selection_bw_hz = None
        self._overlay_guard = False
        self._last_xrange = None
        self._last_visible_span_emit = None
        self._pending_visible_span = 0.0
        self._pending_plot_width = 0
        self._power_unit = "db_per_bin"
        self._bin_width_hz = 1.0
        self._max_visible_span_hz = None
        self._data_xrange = None
        self._clamp_xrange_guard = False
        self._profile_calls = 0
        self._profile_elapsed_s = 0.0
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.graphics = pg.GraphicsLayoutWidget(self)
        layout.addWidget(self.graphics)

        self.pos_label = self.graphics.addLabel(row=0, col=0, justify="right")
        self._set_inactive_pos_label()
        self.plot = self.graphics.addPlot(row=1, col=0)
        self.plot.showGrid(x=True, y=True)
        self.plot.setLabel("left", "Power (dBm)")
        self.plot.setLabel("bottom", "Frequency", units="Hz")
        self.plot.setClipToView(True)
        self.plot.setDownsampling(auto=True, mode="subsample")
        self.plot.getViewBox().setMouseEnabled(x=True, y=False)
        self.plot.showButtons()

        self._visible_span_timer = QtCore.QTimer(self)
        self._visible_span_timer.setSingleShot(True)
        self._visible_span_timer.setInterval(120)
        self._visible_span_timer.timeout.connect(self._emit_visible_span)
        self.plot.getViewBox().sigXRangeChanged.connect(self._on_view_xrange_changed)

        self.curve_fill = self.plot.plot(pen=None)
        self.curve_fill.setFillLevel(self._main_fill_level)
        self.curve_fill.setBrush(self._main_fill_brush)
        self.curve_fill.setZValue(880)

        self.curve = self.plot.plot(pen=self._main_color)
        self.curve.setZValue(900)

        self.selection_region = pg.LinearRegionItem(
            values=[
                self._selection_freq_hz - (self._selection_bw_hz * 0.5),
                self._selection_freq_hz + (self._selection_bw_hz * 0.5),
            ],
            orientation="vertical",
            brush=pg.mkBrush(
                self._selection_color.red(),
                self._selection_color.green(),
                self._selection_color.blue(),
                80,
            ),
            movable=True,
        )
        self.selection_region.setZValue(960)
        self.selection_line = pg.InfiniteLine(
            pos=self._selection_freq_hz,
            angle=90,
            movable=True,
            pen=pg.mkPen(self._selection_color, width=2),
        )
        self.selection_line.setZValue(950)
        self.selection_line.sigPositionChanged.connect(self._on_selection_line_changed)
        self.selection_region.sigRegionChanged.connect(self._on_selection_region_changed)
        self.plot.addItem(self.selection_region)
        self.plot.addItem(self.selection_line)

        self.v_line = pg.InfiniteLine(angle=90, movable=False, pen="cyan")
        self.h_line = pg.InfiniteLine(angle=0, movable=False, pen="cyan")
        self.v_line.setZValue(1000)
        self.h_line.setZValue(1000)
        self.plot.addItem(self.v_line, ignoreBounds=True)
        self.plot.addItem(self.h_line, ignoreBounds=True)
        self.v_line.hide()
        self.h_line.hide()
        self.mouse_proxy = pg.SignalProxy(self.plot.scene().sigMouseMoved, rateLimit=30, slot=self._mouse_moved)
        self.plot.scene().sigMouseClicked.connect(self._mouse_clicked)
        self.graphics.viewport().installEventFilter(self)

    def _mouse_moved(self, evt) -> None:
        pos = evt[0]
        if not self.plot.sceneBoundingRect().contains(pos):
            self._hide_cursor_overlay()
            return
        mouse_point = self.plot.vb.mapSceneToView(pos)
        unit_label = "dBm/Hz" if self._power_unit == "db_per_hz" else "dBm"
        self.pos_label.setText(
            f"<span style='font-size: 12pt'>f={mouse_point.x() / 1e6:0.3f} MHz, P={mouse_point.y():0.3f} {unit_label}</span>"
        )
        self.v_line.setPos(mouse_point.x())
        self.h_line.setPos(mouse_point.y())
        self.v_line.show()
        self.h_line.show()

    def leaveEvent(self, event) -> None:
        self._hide_cursor_overlay()
        super().leaveEvent(event)

    def eventFilter(self, obj, event) -> bool:
        if obj is self.graphics.viewport() and event.type() == QtCore.QEvent.Leave:
            self._hide_cursor_overlay()
        return super().eventFilter(obj, event)

    def _hide_cursor_overlay(self) -> None:
        self._set_inactive_pos_label()
        self.v_line.hide()
        self.h_line.hide()

    def _set_inactive_pos_label(self) -> None:
        unit_label = "dBm/Hz" if self._power_unit == "db_per_hz" else "dBm"
        self.pos_label.setText(
            "<span style='font-size: 12pt; color: #5a6670'>"
            f"f=---.--- MHz, P=---.--- {unit_label}"
            "</span>"
        )

    def _display_values(self, values_db):
        values = np.asarray(values_db, dtype=np.float64)
        if self._power_unit == "db_per_hz":
            values = values - bin_width_to_density_offset_db(self._bin_width_hz)
        return values

    def set_power_unit(self, unit: str) -> None:
        normalized = "db_per_hz" if str(unit).strip().lower() in {"db/hz", "db_per_hz"} else "db_per_bin"
        if normalized == self._power_unit:
            return
        self._power_unit = normalized
        self.plot.setLabel("left", "Power (dBm/Hz)" if normalized == "db_per_hz" else "Power (dBm)")

    def set_bin_width_hz(self, bin_width_hz: float) -> None:
        width_hz = float(max(1e-12, abs(float(bin_width_hz))))
        self._bin_width_hz = width_hz

    def set_max_visible_span_hz(self, span_hz: float | None) -> None:
        if span_hz is None:
            self._max_visible_span_hz = None
            return
        self._max_visible_span_hz = float(max(1.0, abs(float(span_hz))))

    def set_max_selection_bandwidth_hz(self, bandwidth_hz: float | None) -> None:
        if bandwidth_hz is None:
            self._max_selection_bw_hz = None
            return
        normalized = float(max(100.0, abs(float(bandwidth_hz))))
        if self._max_selection_bw_hz is not None and abs(float(self._max_selection_bw_hz) - normalized) <= 1.0:
            return
        self._max_selection_bw_hz = normalized
        clamped_freq_hz, clamped_bw_hz = self._clamp_selection(self._selection_freq_hz, self._selection_bw_hz)
        if (
            abs(clamped_freq_hz - float(self._selection_freq_hz)) > 1.0
            or abs(clamped_bw_hz - float(self._selection_bw_hz)) > 1.0
        ):
            self.set_receiver_selection(clamped_freq_hz, clamped_bw_hz)

    def _clamp_selection(self, freq_hz: float, bw_hz: float) -> tuple[float, float]:
        freq_hz = float(freq_hz)
        bw_hz = float(max(100.0, abs(float(bw_hz))))
        if self._max_selection_bw_hz is not None and np.isfinite(self._max_selection_bw_hz):
            bw_hz = float(min(bw_hz, self._max_selection_bw_hz))
        if self._data_xrange is None:
            return freq_hz, bw_hz
        x0 = float(self._data_xrange[0])
        x1 = float(self._data_xrange[1])
        span_hz = float(max(100.0, x1 - x0))
        bw_hz = float(min(bw_hz, span_hz))
        half_bw = bw_hz * 0.5
        min_center = x0 + half_bw
        max_center = x1 - half_bw
        if min_center <= max_center:
            freq_hz = float(min(max(freq_hz, min_center), max_center))
        else:
            freq_hz = 0.5 * (x0 + x1)
        return freq_hz, bw_hz

    def _mouse_clicked(self, evt) -> None:
        if evt is None or evt.button() != QtCore.Qt.LeftButton:
            return
        scene_pos = evt.scenePos()
        if not self.plot.sceneBoundingRect().contains(scene_pos):
            return
        mouse_point = self.plot.vb.mapSceneToView(scene_pos)
        frequency_hz = float(mouse_point.x())
        if not np.isfinite(frequency_hz):
            return
        self.frequency_clicked.emit(frequency_hz)

    def _on_view_xrange_changed(self, *_args) -> None:
        if self._clamp_xrange_guard:
            return
        try:
            vb = self.plot.getViewBox()
            xr = vb.viewRange()[0]
            span = abs(float(xr[1]) - float(xr[0]))
            width_px = int(max(1, round(vb.sceneBoundingRect().width())))
        except Exception:
            return
        if not np.isfinite(span) or span <= 0.0:
            return
        max_span = self._max_visible_span_hz
        data_xrange = self._data_xrange
        if (
            max_span is not None
            and np.isfinite(max_span)
            and span > float(max_span) * 1.001
            and data_xrange is not None
        ):
            self._clamp_xrange_guard = True
            try:
                self.plot.setXRange(float(data_xrange[0]), float(data_xrange[1]), padding=0.0)
                span = float(max_span)
            finally:
                self._clamp_xrange_guard = False
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

    def _on_selection_line_changed(self) -> None:
        if self._overlay_guard:
            return
        self._overlay_guard = True
        try:
            self._selection_freq_hz, self._selection_bw_hz = self._clamp_selection(
                float(self.selection_line.value()),
                self._selection_bw_hz,
            )
            half_bw = max(50.0, float(self._selection_bw_hz) * 0.5)
            self.selection_line.setValue(self._selection_freq_hz)
            self.selection_region.setRegion([
                self._selection_freq_hz - half_bw,
                self._selection_freq_hz + half_bw,
            ])
        finally:
            self._overlay_guard = False
        self.selection_frequency_changed.emit(float(self._selection_freq_hz))

    def _on_selection_region_changed(self) -> None:
        if self._overlay_guard:
            return
        region = self.selection_region.getRegion()
        center = 0.5 * (float(region[0]) + float(region[1]))
        bandwidth = max(100.0, float(region[1]) - float(region[0]))
        center, bandwidth = self._clamp_selection(center, bandwidth)
        self._overlay_guard = True
        try:
            self._selection_freq_hz = center
            self._selection_bw_hz = bandwidth
            self.selection_line.setValue(center)
            half_bw = bandwidth * 0.5
            self.selection_region.setRegion([
                center - half_bw,
                center + half_bw,
            ])
        finally:
            self._overlay_guard = False
        self.selection_frequency_changed.emit(center)
        self.selection_bandwidth_changed.emit(bandwidth)

    def set_receiver_selection(self, freq_hz: float, bw_hz: float) -> None:
        next_freq_hz, next_bw_hz = self._clamp_selection(freq_hz, bw_hz)
        if (
            abs(next_freq_hz - float(self._selection_freq_hz)) <= 1.0
            and abs(next_bw_hz - float(self._selection_bw_hz)) <= 1.0
        ):
            return
        self._selection_freq_hz = next_freq_hz
        self._selection_bw_hz = next_bw_hz
        self._overlay_guard = True
        try:
            self.selection_line.setValue(self._selection_freq_hz)
            self.selection_region.setRegion([
                self._selection_freq_hz - (self._selection_bw_hz * 0.5),
                self._selection_freq_hz + (self._selection_bw_hz * 0.5),
            ])
        finally:
            self._overlay_guard = False

    def set_selection_color(self, color) -> None:
        qcolor = pg.mkColor(color)
        self._selection_color = qcolor
        self.selection_line.setPen(pg.mkPen(qcolor, width=2))
        self.selection_region.setBrush(pg.mkBrush(qcolor.red(), qcolor.green(), qcolor.blue(), 80))

    def set_y_window(self, y_min_db: float | None = None, y_range_db: float | None = None) -> None:
        if y_min_db is not None:
            self._y_min_db = float(y_min_db)
        if y_range_db is not None:
            self._y_range_db = float(max(10.0, y_range_db))
        self._main_fill_level = self._y_min_db
        self.curve_fill.setFillLevel(self._main_fill_level)
        self.plot.setYRange(self._y_min_db, self._y_min_db + self._y_range_db, padding=0.0)

    def _decimate_line_for_display(self, x, y, *, max_bins: int | None = None):
        x = np.asarray(x)
        y = np.asarray(y)
        n = int(y.size)
        max_bins = int(max(512, self._max_display_bins if max_bins is None else max_bins))
        if n <= max_bins:
            return x, y
        step = int(np.ceil(n / float(max_bins)))
        m = int(np.ceil(n / float(step)))
        pad = m * step - n
        x_pad = np.pad(x, (0, pad), mode="edge") if pad > 0 else x
        y_pad = np.pad(y, (0, pad), mode="edge") if pad > 0 else y
        x_r = x_pad.reshape(m, step)
        y_r = y_pad.reshape(m, step)
        finite_mask = np.isfinite(y_r)
        linear = db_to_linear_power(np.where(finite_mask, y_r, np.nan))
        with np.errstate(all="ignore"):
            linear_mean = np.nanmean(linear, axis=1)
            x_d = np.nanmean(x_r, axis=1)
        y_d = np.asarray(linear_power_to_db(np.nan_to_num(linear_mean, nan=0.0)), dtype=np.float32)
        y_d = np.where(np.isfinite(linear_mean), y_d, np.nan)
        return x_d, y_d

    def _display_peak_bins(self) -> int:
        try:
            vb = self.plot.getViewBox()
            width_px = int(max(256, round(vb.sceneBoundingRect().width())))
        except Exception:
            width_px = 1024
        return int(min(self._max_display_bins, max(512, width_px)))

    def update_plot(self, data_storage, force: bool = False) -> None:
        start_t = time.perf_counter()
        if getattr(data_storage, "x", None) is None or getattr(data_storage, "y", None) is None:
            return

        y_source = np.asarray(data_storage.y, dtype=np.float32)
        x_plot, y_plot = self._decimate_line_for_display(
            data_storage.x,
            y_source,
            max_bins=self._display_peak_bins(),
        )

        x0 = float(data_storage.x[0])
        x1 = float(data_storage.x[-1])
        self._data_xrange = (x0, x1)
        if self._last_xrange is None:
            self.plot.setXRange(x0, x1)
            self._last_xrange = (x0, x1)

        y_display = np.asarray(self._display_values(y_plot), dtype=np.float32)
        self.plot.setYRange(self._y_min_db, self._y_min_db + self._y_range_db, padding=0.0)
        self.curve_fill.setData(x_plot, y_display, connect="finite")
        self.curve.setData(x_plot, y_display, connect="finite")
        if force:
            self.curve.setVisible(True)
            self.curve_fill.setVisible(True)
        self._profile_calls += 1
        self._profile_elapsed_s += max(0.0, time.perf_counter() - start_t)

    def recalculate_plot(self, data_storage) -> None:
        if getattr(data_storage, "x", None) is None:
            return
        QtCore.QTimer.singleShot(0, lambda: self.update_plot(data_storage, force=True))

    def center_view_on_frequency(self, freq_hz: float, *, default_span_hz: float | None = None) -> None:
        freq_hz = float(freq_hz)
        if not np.isfinite(freq_hz):
            return
        try:
            current = self.plot.getViewBox().viewRange()[0]
            current_span = abs(float(current[1]) - float(current[0]))
        except Exception:
            current_span = 0.0
        span_hz = float(default_span_hz) if default_span_hz is not None and np.isfinite(default_span_hz) and default_span_hz > 0.0 else current_span
        if not np.isfinite(span_hz) or span_hz <= 0.0:
            span_hz = 1_000_000.0
        half_span = span_hz * 0.5
        self.plot.setXRange(freq_hz - half_span, freq_hz + half_span, padding=0.0)
        self._last_xrange = (freq_hz - half_span, freq_hz + half_span)

    def clear_plot(self) -> None:
        self.curve_fill.clear()
        self.curve.clear()
        self._last_xrange = None
        self._data_xrange = None

    def consume_profile_metrics(self) -> dict[str, float]:
        calls = int(self._profile_calls)
        elapsed_s = float(self._profile_elapsed_s)
        self._profile_calls = 0
        self._profile_elapsed_s = 0.0
        return {
            "calls": float(calls),
            "avg_ms": float((elapsed_s / max(1, calls)) * 1000.0),
        }

"""Waterfall widget for SDR history display."""

from __future__ import annotations

import time

import numpy as np
import pyqtgraph as pg
from PyQt5 import QtCore
from PyQt5.QtWidgets import QHBoxLayout, QWidget

from antrack.core.dsp.snr import bin_width_to_density_offset_db


class WaterfallPlotWidget(QWidget):
    """Time-frequency waterfall using a flame palette."""

    frequency_clicked = QtCore.pyqtSignal(float)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.history_size = 120
        self.auto_levels = False
        self.wf_min_db = -120.0
        self.wf_dynamic_range_db = 100.0
        self.wf_max_db = self.wf_min_db + self.wf_dynamic_range_db
        self.max_bins = 8192
        self._last_draw_t = 0.0
        self._draw_min_interval_s = 1.0 / 30.0
        self._last_transform_key = None
        self._power_unit = "db_per_bin"
        self._bin_width_hz = 1.0
        self._profile_calls = 0
        self._profile_elapsed_s = 0.0
        self._lookup_table = self._build_lookup_table()
        self._hover_view = None
        self._hover_x = None
        self._hover_y0 = 0.0
        self._setup_ui()

    def _build_lookup_table(self):
        waterfall_map = pg.ColorMap(
            pos=np.array([0.0, 0.08, 0.2, 0.42, 0.66, 0.84, 1.0], dtype=float),
            color=np.array(
                [
                    (2, 4, 28),
                    (16, 22, 90),
                    (40, 96, 190),
                    (118, 186, 255),
                    (248, 245, 176),
                    (255, 164, 64),
                    (255, 255, 255),
                ],
                dtype=np.ubyte,
            ),
        )
        return waterfall_map.getLookupTable(nPts=256)

    def _setup_ui(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.graphics = pg.GraphicsLayoutWidget(self)
        layout.addWidget(self.graphics)
        self.pos_label = self.graphics.addLabel(row=0, col=0, justify="right")
        self._set_inactive_pos_label()
        self.plot = self.graphics.addPlot(row=1, col=0)
        self.plot.setLabel("bottom", "Frequency", units="Hz")
        self.plot.setLabel("left", "Time")
        self.plot.setYRange(-self.history_size, 0)
        self.plot.setLimits(yMax=0)
        self.plot.getViewBox().setMouseEnabled(x=True, y=False)
        self.plot.showButtons()
        self.waterfall_img = pg.ImageItem()
        try:
            self.waterfall_img.setAutoDownsample(False)
        except Exception:
            pass
        self.plot.addItem(self.waterfall_img)
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

    def _mouse_moved(self, evt) -> None:
        pos = evt[0]
        if not self.plot.sceneBoundingRect().contains(pos):
            self._hide_cursor_overlay()
            return
        mouse_point = self.plot.vb.mapSceneToView(pos)
        if self._hover_view is None or self._hover_x is None:
            self._hide_cursor_overlay()
            return
        x = self._hover_x
        view = self._hover_view
        if x.size == 0 or view.size == 0:
            self._hide_cursor_overlay()
            return
        bin_index = int(np.clip(np.searchsorted(x, float(mouse_point.x())), 0, x.size - 1))
        row_index = int(np.floor(float(mouse_point.y()) - float(self._hover_y0)))
        row_index = int(np.clip(row_index, 0, view.shape[0] - 1))
        power_db = float(view[row_index, bin_index])
        unit_label = "dBm/Hz" if self._power_unit == "db_per_hz" else "dBm"
        self.pos_label.setText(
            f"<span style='font-size: 12pt'>f={float(x[bin_index]) / 1e6:0.3f} MHz, P={power_db:0.3f} {unit_label}</span>"
        )
        self.v_line.setPos(float(x[bin_index]))
        self.h_line.setPos(float(self._hover_y0) + row_index + 0.5)
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

    def update_plot(self, data_storage) -> None:
        start_t = time.perf_counter()
        if data_storage is None:
            return

        now_t = time.monotonic()
        if now_t - self._last_draw_t < self._draw_min_interval_s:
            return

        hist_src = getattr(data_storage, "waterfall_history", None)
        x_src = getattr(data_storage, "x_wf", None)
        if hist_src is None:
            hist_src = getattr(data_storage, "history", None)
            x_src = getattr(data_storage, "x", None)
        if hist_src is None or x_src is None:
            return
        hist = hist_src.get_recent(self.history_size)
        if hist is None or len(hist) == 0:
            return
        count = int(min(self.history_size, hist.shape[0]))
        view = np.asarray(hist[-count:], dtype=np.float32)
        x_use = np.asarray(x_src, dtype=np.float64)

        if self._power_unit == "db_per_hz":
            view = np.asarray(view - bin_width_to_density_offset_db(self._bin_width_hz), dtype=np.float32)
        if view.shape[1] > int(self.max_bins):
            stride = int(np.ceil(view.shape[1] / float(self.max_bins)))
            view = view[:, ::stride]
            x_use = x_use[::stride]
        self._hover_view = view
        self._hover_x = x_use

        lo = float(self.wf_min_db)
        hi = float(self.wf_max_db)
        rng = max(1e-3, hi - lo)
        z = np.clip((view - lo) / rng, 0.0, 1.0).astype(np.float32, copy=False)

        x0 = float(x_use[0])
        x1 = float(x_use[-1])
        bins = int(max(1, view.shape[1] - 1))
        transform_key = (x0, x1, bins)
        if self._last_transform_key != transform_key:
            self.waterfall_img.setTransform(pg.QtGui.QTransform().scale((x1 - x0) / bins, 1))
            self._last_transform_key = transform_key

        self.waterfall_img.setImage(z.T, autoLevels=False, autoRange=False, levels=(0.0, 1.0))
        self.waterfall_img.setLookupTable(self._lookup_table)
        y0 = -count if count < self.history_size else -self.history_size
        self._hover_y0 = float(y0)
        self.waterfall_img.setPos(x0, y0)
        self._last_draw_t = now_t
        self._profile_calls += 1
        self._profile_elapsed_s += max(0.0, time.perf_counter() - start_t)

    def recalculate_plot(self, data_storage) -> None:
        self.update_plot(data_storage)

    def set_baseline_db(self, value_db: float) -> None:
        self.wf_min_db = float(value_db)
        self.wf_max_db = self.wf_min_db + float(self.wf_dynamic_range_db)

    def set_level_window(self, minimum_db: float, maximum_db: float) -> None:
        self.wf_min_db = float(minimum_db)
        self.wf_max_db = float(max(self.wf_min_db + 1.0, maximum_db))
        self.wf_dynamic_range_db = float(self.wf_max_db - self.wf_min_db)

    def set_power_unit(self, unit: str) -> None:
        self._power_unit = "db_per_hz" if str(unit).strip().lower() in {"db/hz", "db_per_hz"} else "db_per_bin"

    def set_bin_width_hz(self, bin_width_hz: float) -> None:
        self._bin_width_hz = float(max(1e-12, abs(float(bin_width_hz))))

    def clear_plot(self) -> None:
        self.waterfall_img.clear()
        self._last_transform_key = None
        self._hover_view = None
        self._hover_x = None
        self._hide_cursor_overlay()

    def consume_profile_metrics(self) -> dict[str, float]:
        calls = int(self._profile_calls)
        elapsed_s = float(self._profile_elapsed_s)
        self._profile_calls = 0
        self._profile_elapsed_s = 0.0
        return {
            "calls": float(calls),
            "avg_ms": float((elapsed_s / max(1, calls)) * 1000.0),
        }

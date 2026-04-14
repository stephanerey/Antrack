"""Waterfall widget for SDR history display."""

from __future__ import annotations

import time

import numpy as np
import pyqtgraph as pg
from PyQt5.QtWidgets import QHBoxLayout, QWidget


class WaterfallPlotWidget(QWidget):
    """Time-frequency waterfall using a flame palette."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.history_size = 120
        self.auto_levels = False
        self.wf_min_db = -120.0
        self.wf_max_db = -20.0
        self.max_bins = 4096
        self._last_draw_t = 0.0
        self._draw_min_interval_s = 1.0 / 20.0
        self._last_transform_key = None
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.graphics = pg.GraphicsLayoutWidget(self)
        layout.addWidget(self.graphics)
        self.plot = self.graphics.addPlot()
        self.plot.setLabel("bottom", "Frequency", units="Hz")
        self.plot.setLabel("left", "Time")
        self.plot.setYRange(-self.history_size, 0)
        self.plot.setLimits(yMax=0)
        self.plot.showButtons()
        self.waterfall_img = pg.ImageItem()
        self.plot.addItem(self.waterfall_img)

    def update_plot(self, data_storage) -> None:
        if data_storage is None:
            return
        hist_src = getattr(data_storage, "waterfall_history", None)
        x_src = getattr(data_storage, "x_wf", None)
        if hist_src is None:
            hist_src = getattr(data_storage, "history", None)
            x_src = getattr(data_storage, "x", None)
        if hist_src is None or x_src is None:
            return

        now_t = time.monotonic()
        if now_t - self._last_draw_t < self._draw_min_interval_s:
            return

        hist = hist_src.get_recent(self.history_size)
        if hist is None or len(hist) == 0:
            return

        count = int(min(self.history_size, hist.shape[0]))
        view = np.asarray(hist[-count:], dtype=np.float32)
        if view.shape[1] > int(self.max_bins):
            stride = int(np.ceil(view.shape[1] / float(self.max_bins)))
            view = view[:, ::stride]
            x_use = np.asarray(x_src, dtype=np.float64)[::stride]
        else:
            x_use = np.asarray(x_src, dtype=np.float64)

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
        self.waterfall_img.setLookupTable(pg.colormap.get("flame").getLookupTable())
        self.waterfall_img.setPos(x0, -count if count < self.history_size else -self.history_size)
        self._last_draw_t = now_t

    def recalculate_plot(self, data_storage) -> None:
        self.update_plot(data_storage)

    def clear_plot(self) -> None:
        self.waterfall_img.clear()
        self._last_transform_key = None

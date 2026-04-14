"""Heatmap widget used by scan workflows."""

from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from PyQt5.QtWidgets import QVBoxLayout, QWidget


class HeatmapWidget(QWidget):
    """2D heatmap with optional best-point marker."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.plot = pg.PlotWidget(self)
        self.plot.setLabel("bottom", "Azimuth", units="deg")
        self.plot.setLabel("left", "Elevation", units="deg")
        self.plot.showGrid(x=True, y=True)
        layout.addWidget(self.plot)
        self.image = pg.ImageItem()
        self.plot.addItem(self.image)
        self.best_marker = pg.ScatterPlotItem(size=12, brush=pg.mkBrush(255, 255, 255, 220), pen=pg.mkPen("k"))
        self.plot.addItem(self.best_marker)

    def set_heatmap(self, az_values, el_values, grid_values) -> None:
        az = np.asarray(az_values, dtype=np.float64)
        el = np.asarray(el_values, dtype=np.float64)
        grid = np.asarray(grid_values, dtype=np.float32)
        if az.size < 2 or el.size < 2 or grid.size == 0:
            return
        x0 = float(np.min(az))
        x1 = float(np.max(az))
        y0 = float(np.min(el))
        y1 = float(np.max(el))
        nx = max(1, grid.shape[1] - 1)
        ny = max(1, grid.shape[0] - 1)
        self.image.setImage(grid, autoLevels=True)
        transform = pg.QtGui.QTransform()
        transform.scale((x1 - x0) / nx, (y1 - y0) / ny)
        self.image.setTransform(transform)
        self.image.setPos(x0, y0)

    def set_best_point(self, az_deg: float, el_deg: float) -> None:
        self.best_marker.setData([az_deg], [el_deg])

    def clear(self) -> None:
        self.image.clear()
        self.best_marker.clear()

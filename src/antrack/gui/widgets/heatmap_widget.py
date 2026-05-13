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
        self.sample_cells = pg.ScatterPlotItem(symbol="s", size=18, pen=pg.mkPen(220, 220, 220, 100))
        self.planned_marker = pg.ScatterPlotItem(size=8, brush=pg.mkBrush(180, 180, 180, 90), pen=pg.mkPen(120, 120, 120, 120))
        self.measured_marker = pg.ScatterPlotItem(size=9, brush=pg.mkBrush(80, 220, 120, 180), pen=pg.mkPen(20, 60, 20, 160))
        self.current_marker = pg.ScatterPlotItem(size=13, brush=pg.mkBrush(255, 170, 40, 220), pen=pg.mkPen("k"))
        self.best_marker = pg.ScatterPlotItem(size=12, brush=pg.mkBrush(255, 255, 255, 220), pen=pg.mkPen("k"))
        self.plot.addItem(self.sample_cells)
        self.plot.addItem(self.planned_marker)
        self.plot.addItem(self.measured_marker)
        self.plot.addItem(self.current_marker)
        self.plot.addItem(self.best_marker)

    def set_heatmap(self, az_values, el_values, grid_values) -> None:
        az = np.asarray(az_values, dtype=np.float64)
        el = np.asarray(el_values, dtype=np.float64)
        grid = np.asarray(grid_values, dtype=np.float32)
        if az.size < 2 or el.size < 2 or grid.size == 0:
            return
        az_sorted = np.sort(np.unique(az))
        el_sorted = np.sort(np.unique(el))
        dx = float(np.median(np.diff(az_sorted))) if az_sorted.size >= 2 else 1.0
        dy = float(np.median(np.diff(el_sorted))) if el_sorted.size >= 2 else 1.0
        x0 = float(np.min(az_sorted) - dx / 2.0)
        x1 = float(np.max(az_sorted) + dx / 2.0)
        y0 = float(np.min(el_sorted) - dy / 2.0)
        y1 = float(np.max(el_sorted) + dy / 2.0)
        nx = max(1, grid.shape[1])
        ny = max(1, grid.shape[0])
        self.image.setImage(grid, autoLevels=True)
        transform = pg.QtGui.QTransform()
        transform.scale((x1 - x0) / nx, (y1 - y0) / ny)
        self.image.setTransform(transform)
        self.image.setPos(x0, y0)

    def set_best_point(self, az_deg: float, el_deg: float) -> None:
        self.best_marker.setData([az_deg], [el_deg])

    def set_axis_mode(self, *, relative: bool) -> None:
        if relative:
            self.plot.setLabel("bottom", "Offset Azimuth", units="deg")
            self.plot.setLabel("left", "Offset Elevation", units="deg")
        else:
            self.plot.setLabel("bottom", "Azimuth", units="deg")
            self.plot.setLabel("left", "Elevation", units="deg")

    def set_scan_points(
        self,
        planned_points: list[tuple[float, float]] | None,
        measured_points: list[tuple[float, float]] | None,
        current_point: tuple[float, float] | None,
    ) -> None:
        planned = list(planned_points or [])
        measured = list(measured_points or [])
        if planned:
            self.planned_marker.setData([point[0] for point in planned], [point[1] for point in planned])
        else:
            self.planned_marker.clear()
        if measured:
            self.measured_marker.setData([point[0] for point in measured], [point[1] for point in measured])
        else:
            self.measured_marker.clear()
        if current_point is not None:
            self.current_marker.setData([current_point[0]], [current_point[1]])
        else:
            self.current_marker.clear()

    def set_sample_cells(self, points: list[tuple[float, float]], values: list[float], *, size: float = 18.0) -> None:
        if not points or not values or len(points) != len(values):
            self.sample_cells.clear()
            return
        val_min = float(min(values))
        val_max = float(max(values))
        span = val_max - val_min
        spots = []
        for (x, y), value in zip(points, values):
            ratio = 0.5 if span <= 1e-9 else max(0.0, min(1.0, (float(value) - val_min) / span))
            red = int(40 + 215 * ratio)
            green = int(80 + 140 * ratio)
            blue = int(180 - 120 * ratio)
            spots.append(
                {
                    "pos": (float(x), float(y)),
                    "brush": pg.mkBrush(red, green, blue, 220),
                    "pen": pg.mkPen(240, 240, 240, 60),
                    "size": float(size),
                }
            )
        self.sample_cells.setData(spots)

    def clear(self) -> None:
        self.image.clear()
        self.sample_cells.clear()
        self.planned_marker.clear()
        self.measured_marker.clear()
        self.current_marker.clear()
        self.best_marker.clear()

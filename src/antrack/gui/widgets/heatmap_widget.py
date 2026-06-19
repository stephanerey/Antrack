"""Heatmap widget used by scan workflows."""

from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from PyQt5.QtCore import pyqtSignal
from PyQt5.QtWidgets import QGraphicsRectItem, QWidget


class HeatmapWidget(QWidget):
    """2D heatmap with optional best-point marker."""

    plot_area_changed = pyqtSignal(int)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._last_plot_side = 0
        self._setup_ui()

    def _setup_ui(self) -> None:
        self.plot = pg.PlotWidget(self)
        self.plot.setLabel("bottom", "Azimuth", units="deg")
        self.plot.setLabel("left", "Elevation", units="deg")
        self.plot.showGrid(x=True, y=True)
        self.plot.getViewBox().setAspectLocked(True, ratio=1.0)
        self.plot.setGeometry(0, 0, 1, 1)
        self.image = pg.ImageItem()
        self.plot.addItem(self.image)
        self.sample_cells = pg.ScatterPlotItem(symbol="s", size=18, pen=pg.mkPen(220, 220, 220, 100))
        self.planned_marker = pg.ScatterPlotItem(size=8, brush=pg.mkBrush(180, 180, 180, 90), pen=pg.mkPen(120, 120, 120, 120))
        self.current_marker = pg.ScatterPlotItem(size=13, brush=pg.mkBrush(255, 170, 40, 220), pen=pg.mkPen("k"))
        self.best_outline = QGraphicsRectItem()
        self.best_outline.setBrush(pg.mkBrush(0, 0, 0, 0))
        self.best_outline.setPen(pg.mkPen(255, 40, 40, width=3))
        self.best_outline.setZValue(20)
        self.best_outline.setVisible(False)
        self.plot.addItem(self.sample_cells)
        self.plot.addItem(self.planned_marker)
        self.plot.addItem(self.current_marker)
        self.plot.addItem(self.best_outline)
        self._grid_cell_items: list[QGraphicsRectItem] = []

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        side = max(1, min(self.width(), self.height()))
        x = max(0, (self.width() - side) // 2)
        y = max(0, (self.height() - side) // 2)
        self.plot.setGeometry(x, y, side, side)
        if side != self._last_plot_side:
            self._last_plot_side = side
            self.plot_area_changed.emit(side)

    def set_heatmap(self, az_values, el_values, grid_values) -> None:
        az = np.asarray(az_values, dtype=np.float64)
        el = np.asarray(el_values, dtype=np.float64)
        grid = np.asarray(grid_values, dtype=np.float32)
        if az.size < 2 or el.size < 2 or grid.size == 0:
            return
        self._clear_grid_cells()
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

    def set_grid_cells(
        self,
        points: list[tuple[float, float]],
        values: list[float],
        *,
        cell_width: float,
        cell_height: float,
    ) -> None:
        self.image.clear()
        self._clear_grid_cells()
        if not points or not values or len(points) != len(values):
            return
        width = max(1e-6, float(cell_width))
        height = max(1e-6, float(cell_height))
        val_min = float(min(values))
        val_max = float(max(values))
        span = val_max - val_min
        for (x, y), value in zip(points, values):
            ratio = 0.5 if span <= 1e-9 else max(0.0, min(1.0, (float(value) - val_min) / span))
            red = int(40 + 215 * ratio)
            green = int(80 + 140 * ratio)
            blue = int(180 - 120 * ratio)
            item = QGraphicsRectItem(float(x) - width / 2.0, float(y) - height / 2.0, width, height)
            item.setBrush(pg.mkBrush(red, green, blue, 220))
            item.setPen(pg.mkPen(240, 240, 240, 70))
            item.setZValue(-5)
            self.plot.addItem(item)
            self._grid_cell_items.append(item)

    def set_best_point(self, az_deg: float, el_deg: float, *, cell_width: float = 0.1, cell_height: float = 0.1) -> None:
        width = max(1e-6, float(cell_width))
        height = max(1e-6, float(cell_height))
        self.best_outline.setRect(float(az_deg) - width / 2.0, float(el_deg) - height / 2.0, width, height)
        self.best_outline.setVisible(True)

    def set_scan_bounds(self, x_min: float, x_max: float, y_min: float, y_max: float) -> None:
        x_range = (float(x_min), float(x_max))
        y_range = (float(y_min), float(y_max))
        view_box = self.plot.getViewBox()
        try:
            self.plot.enableAutoRange(x=False, y=False)
        except Exception:
            pass
        try:
            view_box.enableAutoRange(x=False, y=False)
        except Exception:
            pass
        view_box.setRange(
            xRange=x_range,
            yRange=y_range,
            padding=0.0,
            disableAutoRange=True,
        )
        view_box.setAspectLocked(True, ratio=1.0)

    def clear_scan_bounds(self) -> None:
        view_box = self.plot.getViewBox()
        try:
            self.plot.enableAutoRange(x=True, y=True)
        except Exception:
            pass
        try:
            view_box.enableAutoRange(x=True, y=True)
        except Exception:
            pass
        view_box.setAspectLocked(True, ratio=1.0)

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
        measured_keys = {
            (round(float(point[0]), 6), round(float(point[1]), 6))
            for point in measured
        }
        current_key = None
        if current_point is not None:
            current_key = (round(float(current_point[0]), 6), round(float(current_point[1]), 6))
        remaining_planned = [
            point
            for point in planned
            if (round(float(point[0]), 6), round(float(point[1]), 6)) not in measured_keys
            and (current_key is None or (round(float(point[0]), 6), round(float(point[1]), 6)) != current_key)
        ]
        if remaining_planned:
            self.planned_marker.setData([point[0] for point in remaining_planned], [point[1] for point in remaining_planned])
        else:
            self.planned_marker.clear()
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
        self._clear_grid_cells()
        self.sample_cells.clear()
        self.planned_marker.clear()
        self.current_marker.clear()
        self.best_outline.setVisible(False)
        self.clear_scan_bounds()

    def _clear_grid_cells(self) -> None:
        while self._grid_cell_items:
            item = self._grid_cell_items.pop()
            try:
                self.plot.removeItem(item)
            except Exception:
                pass

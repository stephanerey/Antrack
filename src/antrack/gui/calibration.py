# gui/calibration.py
import math
import numpy as np
from datetime import datetime
from PyQt5.QtWidgets import QWidget, QVBoxLayout, QLabel
from PyQt5.QtCore import Qt
import pyqtgraph as pg


class CalibrationPlots(QWidget):
    """
    Deux graphes:
      1) time_plot : Az/El vs échantillons (AOS→LOS) + marqueurs AOS/NOW/LOS
      2) polar_plot : trajectoire polaire (r = 90 - el, az 0°=Nord 90°=Est) + mêmes marqueurs.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(6)

        self.title = QLabel("Calibration – Current/Next Pass")
        self.title.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        lay.addWidget(self.title)

        # ===== Courbes temporelles =====
        self.time_plot = pg.PlotWidget()
        self.time_plot.showGrid(x=True, y=True, alpha=0.3)
        self.time_plot.setLabel('left', 'Angle (deg)')
        self.time_plot.setLabel('bottom', 'Samples (AOS→LOS)')
        self.time_plot.addLegend()
        pi_t = self.time_plot.getPlotItem()
        pi_t.setMenuEnabled(False)
        pi_t.setMouseEnabled(x=False, y=False)

        self.curve_az = self.time_plot.plot([], [], pen=pg.mkPen(width=2), name="Azimuth", connect='finite')
        self.curve_el = self.time_plot.plot([], [], pen=pg.mkPen(width=2, style=Qt.DashLine),
                                            name="Elevation", connect='finite')

        # Points + repères verticaux
        self.t_aos = self.time_plot.plot([], [], pen=None, symbol='o', symbolSize=9,
                                         symbolBrush=pg.mkBrush(220, 50, 47), name="AOS")
        self.vline_aos = pg.InfiniteLine(angle=90, movable=False, pen=pg.mkPen(220, 50, 47, 120))
        self.time_plot.addItem(self.vline_aos)

        self.t_los = self.time_plot.plot([], [], pen=None, symbol='o', symbolSize=9,
                                         symbolBrush=pg.mkBrush(255, 215, 0), name="LOS")
        self.vline_los = pg.InfiniteLine(angle=90, movable=False, pen=pg.mkPen(255, 215, 0, 120))
        self.time_plot.addItem(self.vline_los)

        # NOW dessiné en dernier + bord blanc pour rester visible en cas de superposition
        self.t_now = self.time_plot.plot([], [], pen=None, symbol='o', symbolSize=10,
                                         symbolBrush=pg.mkBrush(80, 200, 120),
                                         symbolPen=pg.mkPen(255, 255, 255),
                                         name="Now")
        self.vline_now = pg.InfiniteLine(angle=90, movable=False, pen=pg.mkPen(80, 200, 120, 120))
        self.time_plot.addItem(self.vline_now)

        # Z-order pour garantir que NOW est au-dessus
        for it in (self.t_aos, self.t_los, self.vline_aos, self.vline_los):
            it.setZValue(10)
        self.t_now.setZValue(20)
        self.vline_now.setZValue(15)

        lay.addWidget(self.time_plot, stretch=1)

        # ===== Diagramme polaire =====
        self.polar_plot = pg.PlotWidget()
        pi = self.polar_plot.getPlotItem()
        pi.setAspectLocked(True, ratio=1)
        pi.setMenuEnabled(False)
        pi.setMouseEnabled(x=False, y=False)
        pi.hideAxis('left')
        pi.hideAxis('bottom')
        pi.addLegend(offset=(10, 10))

        self.curve_track = self.polar_plot.plot([], [], pen=pg.mkPen(width=2), name="Trajectoire", connect='finite')
        self.p_aos = self.polar_plot.plot([], [], pen=None, symbol='o', symbolSize=10,
                                          symbolBrush=pg.mkBrush(220, 50, 47), name="AOS")
        self.p_los = self.polar_plot.plot([], [], pen=None, symbol='o', symbolSize=10,
                                          symbolBrush=pg.mkBrush(255, 215, 0), name="LOS")
        self.p_now = self.polar_plot.plot([], [], pen=None, symbol='o', symbolSize=11,
                                          symbolBrush=pg.mkBrush(80, 200, 120),
                                          symbolPen=pg.mkPen(255, 255, 255),
                                          name="Now")
        # NOW au-dessus
        self.p_now.setZValue(20)
        for it in (self.p_aos, self.p_los):
            it.setZValue(10)

        lay.addWidget(self.polar_plot, stretch=1)

        # Grille polaire
        self._grid_items = []
        self._build_polar_grid(max_r=90.0)

        self._have_data = False

    # ---------- API ----------
    def clear(self):
        # time plot
        self.curve_az.setData([], [])
        self.curve_el.setData([], [])
        for it in (self.t_aos, self.t_now, self.t_los):
            it.setData([], [])
        for vl in (self.vline_aos, self.vline_now, self.vline_los):
            vl.setPos(0)
        # polar
        self.curve_track.setData([], [])
        for it in (self.p_aos, self.p_now, self.p_los):
            it.setData([], [])
        self._have_data = False

    def update_from_track(self, track: dict):
        """
        track:
          - 'utc': [str 'YYYY-MM-DD HH:MM:SS']
          - 'az':  [float]   - 'el': [float]
          - 'aos_idx','los_idx','now_idx' (optionnels)
        """
        if not isinstance(track, dict):
            self.clear(); return

        az = list(track.get('az') or [])
        el = list(track.get('el') or [])
        n = min(len(az), len(el))
        if n < 2:
            self.clear(); return

        idx_aos = int(track.get('aos_idx')) if track.get('aos_idx') is not None else 0
        idx_los = int(track.get('los_idx')) if track.get('los_idx') is not None else (n - 1)

        # NOW: utiliser now_idx si fourni, sinon estimer via les timestamps 'utc'
        idx_now = track.get('now_idx')
        if idx_now is None:
            utcs = track.get('utc') or []
            idx_now = self._infer_now_idx_from_utc(utcs, n)
        idx_now = int(max(0, min(n - 1, idx_now)))

        # ===== time plot =====
        x = np.arange(n, dtype=float)
        az_arr = np.array([np.nan if v is None else float(v) for v in az], dtype=float)
        el_arr = np.array([np.nan if v is None else float(v) for v in el], dtype=float)
        self.curve_az.setData(x, az_arr)
        self.curve_el.setData(x, el_arr)
        try:
            y_min = math.floor(np.nanmin([az_arr, el_arr]))
            y_max = math.ceil(np.nanmax([az_arr, el_arr]))
            self.time_plot.setYRange(y_min - 5, y_max + 5, padding=0)
        except Exception:
            pass

        def set_point(item, i):
            if 0 <= i < n and not math.isnan(el_arr[i]):
                item.setData([x[i]], [el_arr[i]])
            else:
                item.setData([], [])

        set_point(self.t_aos, idx_aos)
        set_point(self.t_los, idx_los)
        set_point(self.t_now, idx_now)  # dessiné en dernier

        self.vline_aos.setPos(idx_aos)
        self.vline_los.setPos(idx_los)
        self.vline_now.setPos(idx_now)

        # ===== polar plot =====
        X, Y = [], []
        for i in range(n):
            a = az[i]; e = el[i]
            if a is None or e is None:
                X.append(np.nan); Y.append(np.nan); continue
            x_i, y_i = self._az_el_to_xy(a, e)
            X.append(x_i); Y.append(y_i)
        self.curve_track.setData(X, Y)

        try:
            all_r = [abs(v) for v in X if not math.isnan(v)] + [abs(v) for v in Y if not math.isnan(v)]
            if all_r:
                R = max(60.0, max(all_r) + 5.0)
                self.polar_plot.setXRange(-R, R, padding=0)
                self.polar_plot.setYRange(-R, R, padding=0)
        except Exception:
            pass

        def set_point_xy(item, i):
            if 0 <= i < n and az[i] is not None and el[i] is not None:
                x_i, y_i = self._az_el_to_xy(az[i], el[i])
                item.setData([x_i], [y_i])
            else:
                item.setData([], [])

        set_point_xy(self.p_aos, idx_aos)
        set_point_xy(self.p_los, idx_los)
        set_point_xy(self.p_now, idx_now)  # au-dessus

        self._have_data = True

    # ---------- Helpers ----------
    @staticmethod
    def _infer_now_idx_from_utc(utc_list, n):
        """Trouve l'index le plus proche de maintenant (UTC) dans la liste 'utc'."""
        try:
            if not utc_list:
                return n - 1
            now = datetime.utcnow()
            best_i, best_dt = 0, None
            for i, s in enumerate(utc_list[:n]):
                try:
                    dt = datetime.strptime(str(s), "%Y-%m-%d %H:%M:%S")
                except Exception:
                    continue
                diff = abs((dt - now).total_seconds())
                if best_dt is None or diff < best_dt:
                    best_dt, best_i = diff, i
            return best_i
        except Exception:
            return n - 1  # fallback

    @staticmethod
    def _az_el_to_xy(az: float, el: float):
        """Skyfield: az 0°=Nord, 90°=Est (sens horaire). X=Est, Y=Nord, r=90-el."""
        r = max(0.0, 90.0 - float(el))
        th = math.radians(float(az))
        x = r * math.sin(th)   # Est +
        y = r * math.cos(th)   # Nord +
        return x, y

    def _build_polar_grid(self, max_r: float = 90.0):
        """Grille polaire (anneaux + radiales + labels cardinaux)."""
        for it in getattr(self, "_grid_items", []):
            try:
                self.polar_plot.removeItem(it)
            except Exception:
                pass
        self._grid_items = []

        pen = pg.mkPen(120, 160, 200, 120)

        theta = np.linspace(0, 2 * np.pi, 361)
        for rr in (15, 30, 45, 60, 75, 90):
            r = float(rr)
            x = r * np.cos(theta)
            y = r * np.sin(theta)
            ring = pg.PlotDataItem(x, y, pen=pen)
            self.polar_plot.addItem(ring)
            self._grid_items.append(ring)

        for az in (0, 90, 180, 270):
            xs, ys = [], []
            th = math.radians(az)
            for r in (0.0, max_r):
                xs.append(r * math.sin(th))
                ys.append(r * math.cos(th))
            rad = pg.PlotDataItem(xs, ys, pen=pen)
            self.polar_plot.addItem(rad)
            self._grid_items.append(rad)

        for txt, az in (("0°", 0), ("90°", 90), ("180°", 180), ("270°", 270)):
            th = math.radians(float(az))
            x = (max_r + 2) * math.sin(th)
            y = (max_r + 2) * math.cos(th)
            ti = pg.TextItem(txt, anchor=(0.5, 0.5), color=(180, 220, 255))
            ti.setPos(x, y)
            self.polar_plot.addItem(ti)
            self._grid_items.append(ti)

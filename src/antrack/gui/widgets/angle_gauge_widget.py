# -*- coding: utf-8 -*-
import math
from typing import Iterable, List, Tuple, Optional, Union
from PyQt5 import QtCore, QtGui, QtWidgets

# ===================== Couleurs par défaut =====================
_QCOLOR_BG      = QtGui.QColor("#0a0a0a")
_QCOLOR_RING    = QtGui.QColor("#0f0f0f")
_QCOLOR_TICKS   = QtGui.QColor("#FFDF20")    # graduations (jaune)
_QCOLOR_ACTUAL  = QtGui.QColor("#42D3F2")    # cyan
_QCOLOR_SET     = QtGui.QColor("#ffffff")    # blanc
_QCOLOR_LABEL   = QtGui.QColor("#99A1AF")    # libellés (gris)
_QCOLOR_FORBID  = QtGui.QColor("#8b1e1e")    # rouge foncé
_QCOLOR_ERR_OK  = QtGui.QColor("#00c853")    # error OK
_QCOLOR_ERR_BAD = QtGui.QColor("#ff1744")    # error KO
_QCOLOR_LISERET = QtGui.QColor("#033654")    # liseré bleu autour du disque
_QCOLOR_PLACEHOLDER = QtGui.QColor("#666666")  # gris neutre

# Fond du disque (dégradé)
_QCOLOR_GRAD_START = QtGui.QColor("#00101f")  # sombre
_QCOLOR_GRAD_END   = QtGui.QColor("#073758")  # moins sombre

def _qcol(c: Union[str, QtGui.QColor]) -> QtGui.QColor:
    return c if isinstance(c, QtGui.QColor) else QtGui.QColor(str(c))

def _clamp(v, vmin, vmax): return vmin if v < vmin else vmax if v > vmax else v

# ---------- Rendu numérique : point fixe + largeur fixe ----------
def _split_fixed(value: float, decimals: int) -> Tuple[str, str]:
    s = f"{value:.{decimals}f}"
    if value < 0 and not s.startswith("-"):
        s = "-" + s
    sign = "-" if s.startswith("-") else ""
    if sign: s = s[1:]
    if "." in s:
        left_int, frac = s.split(".")
        right = "." + frac + "°"
    else:
        left_int = s
        right = "." + ("0" * decimals) + "°" if decimals > 0 else "°"
    left = sign + left_int
    return left, right

def _draw_fixed_centered(p: QtGui.QPainter, cx: float, cy: float,
                         value: float, decimals: int,
                         font: QtGui.QFont, color: QtGui.QColor,
                         int_digits_template: int = 3):
    """Affiche value avec point fixe centré (gabarit xxx.xx°)."""
    p.setFont(font)
    fm = QtGui.QFontMetrics(font)
    left_tpl  = "-" + ("8" * int_digits_template)
    right_tpl = "." + ("8" * decimals) + "°" if decimals > 0 else "°"
    left_w  = fm.boundingRect(left_tpl).width()
    right_w = fm.boundingRect(right_tpl).width()
    h = fm.ascent() + fm.descent()
    rect_left  = QtCore.QRectF(cx - left_w, cy - h/2.0, left_w, h)
    rect_right = QtCore.QRectF(cx,          cy - h/2.0, right_w, h)
    left_txt, right_txt = _split_fixed(value, decimals)
    p.setPen(color)
    p.drawText(rect_left,  QtCore.Qt.AlignVCenter | QtCore.Qt.AlignRight, left_txt)
    p.drawText(rect_right, QtCore.Qt.AlignVCenter | QtCore.Qt.AlignLeft,  right_txt)

def _draw_placeholder_centered(p: QtGui.QPainter, cx: float, cy: float,
                               decimals: int, font: QtGui.QFont,
                               color: QtGui.QColor, int_digits_template: int = 3):
    """
    Affiche le placeholder '---.--°' (même centrage/largeur que _draw_fixed_centered).
    """
    p.setFont(font)
    fm = QtGui.QFontMetrics(font)
    left_tpl  = "-" + ("8" * int_digits_template)
    right_tpl = "." + ("8" * decimals) + "°" if decimals > 0 else "°"
    left_w  = fm.boundingRect(left_tpl).width()
    right_w = fm.boundingRect(right_tpl).width()
    h = fm.ascent() + fm.descent()
    rect_left  = QtCore.QRectF(cx - left_w, cy - h/2.0, left_w, h)
    rect_right = QtCore.QRectF(cx,          cy - h/2.0, right_w, h)
    left_txt  = "-" * min(3, int_digits_template)
    right_txt = "." + ("-" * decimals) + "°" if decimals > 0 else "°"
    p.setPen(color)
    p.drawText(rect_left,  QtCore.Qt.AlignVCenter | QtCore.Qt.AlignRight, left_txt)
    p.drawText(rect_right, QtCore.Qt.AlignVCenter | QtCore.Qt.AlignLeft,  right_txt)

# ====================================================

class AngleGauge(QtWidgets.QWidget):
    """Cadran angulaire (0–360° ou partiel), triangles Actual/Set, Error, graduations, cardinaux."""

    valueChanged    = QtCore.pyqtSignal(float)
    setpointChanged = QtCore.pyqtSignal(float)
    errorChanged    = QtCore.pyqtSignal(float)

    _reqSetAngle  = QtCore.pyqtSignal(object)  # accepte float ou None/NaN
    _reqSetSetpt  = QtCore.pyqtSignal(object)
    _reqSetError  = QtCore.pyqtSignal(object)
    _reqSetRanges = QtCore.pyqtSignal(list)

    def __init__(self,
                 start_angle_deg: float = 0.0,
                 span_angle: float = 360.0,
                 minor_step_deg: float = 10.0,
                 major_step_deg: float = 30.0,
                 forbidden_ranges: Optional[Iterable[Tuple[float, float]]] = None,
                 decimals: int = 2,
                 tick_minor_ratio: float = 0.50,
                 tick_major_ratio: float = 0.85,
                 *,
                 origin_screen_deg: float = 90.0,  # 0° à droite / 90° en haut
                 clockwise: bool = True,
                 show_cardinal_labels: bool = True,
                 major_anchor_deg: float = 0.0,
                 gradient_angle_deg: float = 315.0,
                 # Couleurs dégradé du disque
                 gradient_color_start: Union[str, QtGui.QColor] = _QCOLOR_GRAD_START,
                 gradient_color_end:   Union[str, QtGui.QColor] = _QCOLOR_GRAD_END,
                 # ---- Positions indépendantes (en R_inner, positives = vers le bas)
                 set_value_y_ratio: float    = -0.40,
                 actual_value_y_ratio: float =  0.00,
                 error_value_y_ratio: float  =  0.64,
                 set_label_y_ratio: float    = -0.55,
                 actual_label_y_ratio: float = -0.12,
                 error_label_y_ratio: float  =  0.49,
                 # Activation initiale des 3 indicateurs
                 set_enabled: bool = True,
                 actual_enabled: bool = True,
                 error_enabled: bool = True,
                 parent=None):
        super().__init__(parent)
        self.setMinimumSize(160, 160)
        self.setAttribute(QtCore.Qt.WA_OpaquePaintEvent, True)

        self.start_angle = float(start_angle_deg)
        self.span_angle  = float(span_angle)
        self.minor_step  = float(minor_step_deg)
        self.major_step  = float(major_step_deg)
        self.decimals    = int(decimals)
        self.tick_minor_ratio = float(tick_minor_ratio)
        self.tick_major_ratio = float(tick_major_ratio)

        self.origin_screen_deg = float(origin_screen_deg)
        self.clockwise = bool(clockwise)
        self.show_cardinals = bool(show_cardinal_labels)
        self.major_anchor_deg = float(major_anchor_deg)
        self.gradient_angle_deg = float(gradient_angle_deg)
        self.gradient_color_start = _qcol(gradient_color_start)
        self.gradient_color_end   = _qcol(gradient_color_end)

        # Y ratios indépendants pour labels et valeurs
        self.set_value_y_ratio    = float(set_value_y_ratio)
        self.actual_value_y_ratio = float(actual_value_y_ratio)
        self.error_value_y_ratio  = float(error_value_y_ratio)
        self.set_label_y_ratio    = float(set_label_y_ratio)
        self.actual_label_y_ratio = float(actual_label_y_ratio)
        self.error_label_y_ratio  = float(error_label_y_ratio)

        self._min_angle = self.start_angle
        self._max_angle = self.start_angle + self.span_angle

        # valeurs + états d'activation
        self._angle: float    = self.start_angle
        self._setpoint: float = self.start_angle
        self._error: float    = 0.0
        self._actual_enabled = bool(actual_enabled)
        self._set_enabled    = bool(set_enabled)
        self._error_enabled  = bool(error_enabled)

        self._err_thr   = 0.05
        self._forbidden = list(forbidden_ranges or [])
        self._static_cache: Optional[QtGui.QPixmap] = None

        # Signaux thread-safe
        self._reqSetAngle.connect(self._set_angle_gui, QtCore.Qt.QueuedConnection)
        self._reqSetSetpt.connect(self._set_setpt_gui, QtCore.Qt.QueuedConnection)
        self._reqSetError.connect(self._set_error_gui, QtCore.Qt.QueuedConnection)
        self._reqSetRanges.connect(self._set_ranges_gui, QtCore.Qt.QueuedConnection)

    # ----------------------------- API publique -------------------------------
    def set_angle(self, angle_deg: Optional[float]) -> None: self._reqSetAngle.emit(angle_deg)
    def set_setpoint(self, angle_deg: Optional[float]) -> None: self._reqSetSetpt.emit(angle_deg)
    def set_error(self, value: Optional[float]) -> None: self._reqSetError.emit(value)

    def set_error_threshold(self, thr: float) -> None: self._err_thr = max(0.0, float(thr)); self.update()
    def set_forbidden_ranges(self, ranges: Iterable[Tuple[float, float]]) -> None: self._reqSetRanges.emit(list(ranges))

    # Activation/désactivation explicite
    def set_actual_enabled(self, enabled: bool) -> None: self._actual_enabled = bool(enabled); self.update()
    def set_set_enabled(self, enabled: bool) -> None: self._set_enabled = bool(enabled); self.update()
    def set_error_enabled(self, enabled: bool) -> None: self._error_enabled = bool(enabled); self.update()

    def configure(self, *,
                  minor_step: Optional[float] = None,
                  major_step: Optional[float] = None,
                  start_angle_deg: Optional[float] = None,
                  span_angle: Optional[float] = None,
                  decimals: Optional[int] = None,
                  tick_minor_ratio: Optional[float] = None,
                  tick_major_ratio: Optional[float] = None,
                  origin_screen_deg: Optional[float] = None,
                  clockwise: Optional[bool] = None,
                  show_cardinal_labels: Optional[bool] = None,
                  major_anchor_deg: Optional[float] = None,
                  gradient_angle_deg: Optional[float] = None,
                  gradient_color_start: Optional[Union[str, QtGui.QColor]] = None,
                  gradient_color_end:   Optional[Union[str, QtGui.QColor]] = None,
                  # positions indépendantes
                  set_value_y_ratio: Optional[float] = None,
                  actual_value_y_ratio: Optional[float] = None,
                  error_value_y_ratio: Optional[float] = None,
                  set_label_y_ratio: Optional[float] = None,
                  actual_label_y_ratio: Optional[float] = None,
                  error_label_y_ratio: Optional[float] = None) -> None:
        if minor_step is not None: self.minor_step = float(minor_step)
        if major_step is not None: self.major_step = float(major_step)
        if tick_minor_ratio is not None: self.tick_minor_ratio = float(tick_minor_ratio)
        if tick_major_ratio is not None: self.tick_major_ratio = float(tick_major_ratio)
        if origin_screen_deg is not None: self.origin_screen_deg = float(origin_screen_deg)
        if clockwise is not None: self.clockwise = bool(clockwise)
        if show_cardinal_labels is not None: self.show_cardinals = bool(show_cardinal_labels)
        if major_anchor_deg is not None: self.major_anchor_deg = float(major_anchor_deg)
        if gradient_angle_deg is not None: self.gradient_angle_deg = float(gradient_angle_deg)
        if gradient_color_start is not None: self.gradient_color_start = _qcol(gradient_color_start)
        if gradient_color_end   is not None: self.gradient_color_end   = _qcol(gradient_color_end)
        # ratios indépendants
        if set_value_y_ratio is not None:    self.set_value_y_ratio    = float(set_value_y_ratio)
        if actual_value_y_ratio is not None: self.actual_value_y_ratio = float(actual_value_y_ratio)
        if error_value_y_ratio is not None:  self.error_value_y_ratio  = float(error_value_y_ratio)
        if set_label_y_ratio is not None:    self.set_label_y_ratio    = float(set_label_y_ratio)
        if actual_label_y_ratio is not None: self.actual_label_y_ratio = float(actual_label_y_ratio)
        if error_label_y_ratio is not None:  self.error_label_y_ratio  = float(error_label_y_ratio)

        cfg_changed = False
        if start_angle_deg is not None: self.start_angle = float(start_angle_deg); cfg_changed = True
        if span_angle is not None: self.span_angle = float(span_angle); cfg_changed = True
        if decimals is not None: self.decimals = int(decimals)
        if cfg_changed:
            self._min_angle = self.start_angle
            self._max_angle = self.start_angle + self.span_angle
            self._angle    = _clamp(self._angle, self._min_angle, self._max_angle)
            self._setpoint = _clamp(self._setpoint, self._min_angle, self._max_angle)
        self._static_cache = None; self.update()

    # ----------------------------- Qt events ----------------------------------
    def resizeEvent(self, ev: QtGui.QResizeEvent) -> None:
        self._static_cache = None
        super().resizeEvent(ev)

    def paintEvent(self, ev: QtGui.QPaintEvent) -> None:
        if self._static_cache is None:
            self._rebuild_static()

        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing, True)
        p.drawPixmap(0, 0, self._static_cache)

        # --------- Dynamique : triangles + valeurs numériques ----------
        cx, cy, R_outer, R_inner, ring_w = self._geom()

        # Triangles — base identique, affichés seulement si 'enabled'
        p.setPen(QtCore.Qt.NoPen)
        if self._actual_enabled:
            p.setBrush(_QCOLOR_ACTUAL)
            p.drawPath(self._triangles_equal_base(True,  self._angle,   cx, cy, R_inner, R_outer))
        if self._set_enabled:
            p.setBrush(_QCOLOR_SET)
            p.drawPath(self._triangles_equal_base(False, self._setpoint,cx, cy, R_inner, R_outer))

        # Tailles police
        s = min(self.width(), self.height())
        f_set = QtGui.QFont("DejaVu Sans", max(9,  int(s * 0.076))); f_set.setBold(True)  # Set plus petit
        f_act = QtGui.QFont("DejaVu Sans", max(10, int(s * 0.098))); f_act.setBold(True)
        f_err = QtGui.QFont("DejaVu Sans", max(8,  int(s * 0.070))); f_err.setBold(True)

        # Valeurs aux positions indépendantes (ou placeholder)
        if self._set_enabled and math.isfinite(self._setpoint):
            _draw_fixed_centered(p, cx, cy + R_inner * self.set_value_y_ratio,
                                 self._setpoint, self.decimals, f_set, _QCOLOR_SET)
        else:
            _draw_placeholder_centered(p, cx, cy + R_inner * self.set_value_y_ratio,
                                       self.decimals, f_set, _QCOLOR_PLACEHOLDER)

        if self._actual_enabled and math.isfinite(self._angle):
            _draw_fixed_centered(p, cx, cy + R_inner * self.actual_value_y_ratio,
                                 self._angle, self.decimals, f_act, _QCOLOR_ACTUAL)
        else:
            _draw_placeholder_centered(p, cx, cy + R_inner * self.actual_value_y_ratio,
                                       self.decimals, f_act, _QCOLOR_PLACEHOLDER)

        err_col = _QCOLOR_ERR_OK if abs(self._error) <= self._err_thr else _QCOLOR_ERR_BAD
        if self._error_enabled and math.isfinite(self._error):
            _draw_fixed_centered(p, cx, cy + R_inner * self.error_value_y_ratio,
                                 self._error, self.decimals, f_err, err_col)
        else:
            _draw_placeholder_centered(p, cx, cy + R_inner * self.error_value_y_ratio,
                                       self.decimals, f_err, _QCOLOR_PLACEHOLDER)

        p.end()

    # ----------------------------- internes -----------------------------------
    def _set_angle_gui(self, angle: Optional[float]) -> None:
        if angle is None or (isinstance(angle, float) and not math.isfinite(angle)):
            self._actual_enabled = False
            self.update()
            return
        a = _clamp(float(angle), self._min_angle, self._max_angle)
        self._angle = a
        self._actual_enabled = True
        self.valueChanged.emit(a)
        self.update()

    def _set_setpt_gui(self, angle: Optional[float]) -> None:
        if angle is None or (isinstance(angle, float) and not math.isfinite(angle)):
            self._set_enabled = False
            self.update()
            return
        s = _clamp(float(angle), self._min_angle, self._max_angle)
        self._setpoint = s
        self._set_enabled = True
        self.setpointChanged.emit(s)
        self.update()

    def _set_error_gui(self, value: Optional[float]) -> None:
        if value is None or (isinstance(value, float) and not math.isfinite(value)):
            self._error_enabled = False
            self.update()
            return
        self._error = float(value)
        self._error_enabled = True
        self.errorChanged.emit(self._error)
        self.update()

    def _set_ranges_gui(self, ranges: List[Tuple[float, float]]) -> None:
        cleaned: List[Tuple[float, float]] = []
        for a, b in ranges:
            if b <= a: continue
            a = max(self._min_angle, min(self._max_angle, float(a)))
            b = max(self._min_angle, min(self._max_angle, float(b)))
            if b > a: cleaned.append((a, b))
        self._forbidden = cleaned
        self._static_cache = None
        self.update()

    # --- géométrie / outils ---
    def _geom(self):
        w, h = self.width(), self.height()
        s = min(w, h)
        cx, cy = w / 2.0, h / 2.0
        R_outer = s * 0.48
        ring_w  = s * 0.13
        R_inner = R_outer - ring_w
        return cx, cy, R_outer, R_inner, ring_w

    def _theta_to_screen_rad(self, theta_deg: float) -> float:
        if self.clockwise:
            scr = self.origin_screen_deg - theta_deg
        else:
            scr = self.origin_screen_deg + theta_deg
        return math.radians(scr)

    def _pt(self, cx, cy, r, theta_deg) -> QtCore.QPointF:
        a = self._theta_to_screen_rad(theta_deg)
        return QtCore.QPointF(cx + r * math.cos(a), cy - r * math.sin(a))

    # Triangles : même base visuelle pour inner/outer --------------------------
    def _triangles_equal_base(self, inner: bool, theta_deg, cx, cy, r_in, r_out):
        ring_th = (r_out - r_in)
        r_mid   = (r_in + r_out) * 0.5
        r_base  = (r_in + ring_th*0.02) if inner else (r_out - ring_th*0.02)
        r_apex  = r_mid
        dtheta_ref_deg = max(2.5, self.minor_step * 0.35)   # référence au rayon médian
        dtheta_base_deg = dtheta_ref_deg * (r_mid / r_base) # même longueur de base
        p1 = self._pt(cx, cy, r_apex, theta_deg)
        p2 = self._pt(cx, cy, r_base, theta_deg - dtheta_base_deg)
        p3 = self._pt(cx, cy, r_base, theta_deg + dtheta_base_deg)
        path = QtGui.QPainterPath(p1); path.lineTo(p2); path.lineTo(p3); path.closeSubpath()
        return path

    # --- fond statique (dégradé orienté + ticks + liseré + cardinaux) ---------
    def _rebuild_static(self) -> None:
        dpr = float(self.devicePixelRatioF()) if hasattr(self, "devicePixelRatioF") else 1.0
        pm = QtGui.QPixmap(max(1, int(self.width() * dpr)), max(1, int(self.height() * dpr)))
        pm.setDevicePixelRatio(dpr)
        pm.fill(_QCOLOR_BG)

        p = QtGui.QPainter(pm)
        p.setRenderHint(QtGui.QPainter.Antialiasing, True)

        cx, cy, R_outer, R_inner, ring_w = self._geom()

        # Disque central: dégradé **linéaire** orientable avec couleurs configurables
        ang = math.radians(self.gradient_angle_deg)
        vx, vy = math.cos(ang), -math.sin(ang)
        r = R_inner
        p1 = QtCore.QPointF(cx - vx*r, cy - vy*r)
        p2 = QtCore.QPointF(cx + vx*r, cy + vy*r)
        grad = QtGui.QLinearGradient(p1, p2)
        grad.setColorAt(0.0, self.gradient_color_start)
        grad.setColorAt(1.0, self.gradient_color_end)
        p.setPen(QtCore.Qt.NoPen)
        p.setBrush(QtGui.QBrush(grad))
        ellipse_rect = QtCore.QRectF(cx - R_inner*0.96, cy - R_inner*0.96, 2*R_inner*0.96, 2*R_inner*0.96)
        p.drawEllipse(ellipse_rect)

        # Liseré
        p.setPen(QtGui.QPen(_QCOLOR_LISERET, max(1.0, ring_w*0.08)))
        p.setBrush(QtCore.Qt.NoBrush)
        p.drawEllipse(ellipse_rect)

        # Couronne
        pen_ring = QtGui.QPen(_QCOLOR_RING, ring_w, QtCore.Qt.SolidLine, QtCore.Qt.FlatCap)
        p.setPen(pen_ring)
        rect_ring = QtCore.QRectF(cx - (R_inner + ring_w / 2.0), cy - (R_inner + ring_w / 2.0),
                                  2 * (R_inner + ring_w / 2.0), 2 * (R_inner + ring_w / 2.0))
        start_qt = (self.origin_screen_deg - self.start_angle if self.clockwise
                    else self.origin_screen_deg + self.start_angle) * 16.0
        span_qt  = ( -self.span_angle if self.clockwise else self.span_angle ) * 16.0
        p.drawArc(rect_ring, int(start_qt), int(span_qt))

        # Zones interdites
        if self._forbidden:
            pen_forbid = QtGui.QPen(_QCOLOR_FORBID, max(2.0, ring_w * 0.92),
                                    QtCore.Qt.SolidLine, QtCore.Qt.FlatCap)
            p.setPen(pen_forbid)
            for a, b in self._forbidden:
                a = _clamp(a, self._min_angle, self._max_angle)
                b = _clamp(b, self._min_angle, self._max_angle)
                if b > a:
                    sa = (self.origin_screen_deg - a if self.clockwise
                          else self.origin_screen_deg + a) * 16.0
                    sp = (-(b - a) if self.clockwise else (b - a)) * 16.0
                    p.drawArc(rect_ring, int(sa), int(sp))

        # Graduations
        tick_width = max(1.0, ring_w * 0.08)
        p.setPen(QtGui.QPen(_QCOLOR_TICKS, tick_width, QtCore.Qt.SolidLine, QtCore.Qt.FlatCap))
        minor_len = ring_w * float(self.tick_minor_ratio)
        major_len = ring_w * float(self.tick_major_ratio)

        def is_major(theta):
            rel = (theta - self.major_anchor_deg) / self.major_step
            return abs(rel - round(rel)) < 1e-6

        def is_cardinal(theta):
            t = theta % 360.0
            return any(abs(t - c) < 1e-6 for c in (0.0, 90.0, 180.0, 270.0))

        steps = max(1, int(round(self.span_angle / self.minor_step)))
        for i in range(steps + 1):
            theta = min(self.start_angle + i * self.minor_step, self._max_angle)
            if self.show_cardinals and is_cardinal(theta):
                continue  # remplacé par texte
            length = major_len if is_major(theta) else minor_len
            r1 = R_outer - length
            r2 = R_outer - 1.0
            p1c = self._pt(cx, cy, r1, theta)
            p2c = self._pt(cx, cy, r2, theta)
            p.drawLine(p1c, p2c)

        # Libellés cardinaux : petits, fins, non rognés et un peu plus dedans
        if self.show_cardinals:
            f_card = QtGui.QFont("DejaVu Sans", max(7, int(min(self.width(), self.height()) * 0.035)))
            f_card.setBold(False)
            p.setFont(f_card)
            p.setPen(_QCOLOR_TICKS)

            def draw_card(val_deg):
                theta = val_deg
                if theta < self._min_angle - 1e-6 or theta > self._max_angle + 1e-6:
                    return
                r_txt = R_outer - ring_w * 0.28
                pos = self._pt(cx, cy, r_txt, theta)
                txt = f"{int(val_deg)%360}"
                fm = QtGui.QFontMetrics(f_card)
                w = fm.width(txt) + 4
                h = fm.height()
                br = QtCore.QRectF(pos.x() - w/2.0, pos.y() - h/2.0, w, h)
                p.drawText(br, QtCore.Qt.AlignCenter, txt)

            if abs(self.span_angle - 360.0) < 1e-6:
                for v in (0, 90, 180, 270): draw_card(v)
            else:
                for v in (0, 90): draw_card(v)

        # Libellés "Set/Actual/Error" : positions **indépendantes**
        p.setPen(_QCOLOR_LABEL)
        s = min(self.width(), self.height())
        f_label = QtGui.QFont("DejaVu Sans", max(8, int(s * 0.045))); f_label.setBold(True)
        p.setFont(f_label)
        lbl_h = R_inner * 0.15

        def draw_label(text: str, y_ratio: float):
            y_center = cy + R_inner * y_ratio
            p.drawText(QtCore.QRectF(cx - R_inner*0.9, y_center - lbl_h/2, 2*R_inner*0.9, lbl_h),
                       QtCore.Qt.AlignHCenter | QtCore.Qt.AlignVCenter, text)

        draw_label("Set",    self.set_label_y_ratio)
        draw_label("Actual", self.actual_label_y_ratio)
        draw_label("Error",  self.error_label_y_ratio)

        p.end()
        self._static_cache = pm

# ---------------- Démo locale ----------------
if __name__ == "__main__":
    import sys, threading, time
    app = QtWidgets.QApplication(sys.argv)

    root = QtWidgets.QWidget()
    lay  = QtWidgets.QHBoxLayout(root)

    # Cadran 1 : 0..360°, 0° en haut
    g1 = AngleGauge(
        span_angle=360,
        forbidden_ranges=[(45, 90), (270, 300)],
        decimals=2,
        origin_screen_deg=90,
        clockwise=True,
        show_cardinal_labels=True,
        major_anchor_deg=0,
        gradient_angle_deg=315,
        gradient_color_start="#00101f",
        gradient_color_end="#073758",
        set_value_y_ratio=-0.55, actual_value_y_ratio=0.00, error_value_y_ratio=0.50,
        set_label_y_ratio=-0.75, actual_label_y_ratio=-0.25, error_label_y_ratio=0.30,
    )

    # Cadran 2 : -10..+100° (élévation), 0° à droite, anti-horaire
    g2 = AngleGauge(
        start_angle_deg=-10, span_angle=110,
        minor_step_deg=10, major_step_deg=30,
        forbidden_ranges=[(-10, 0), (95, 100)],
        decimals=2,
        origin_screen_deg=0,
        clockwise=False,
        show_cardinal_labels=True,
        major_anchor_deg=0,
        gradient_angle_deg=315,
        gradient_color_start="#00101f",
        gradient_color_end="#073758",
        set_value_y_ratio=-0.55, actual_value_y_ratio=0.00, error_value_y_ratio=0.50,
        set_label_y_ratio=-0.75, actual_label_y_ratio=-0.25, error_label_y_ratio=0.30,
    )

    lay.addWidget(g1, 1)
    lay.addWidget(g2, 1)

    root.resize(1000, 560)
    root.show()

    def worker():
        a = 0.0
        t = 0
        while True:
            # démonstration de NaN pour forcer le placeholder toutes les ~3s
            t += 1
            if t % 100 == 0:
                g1.set_angle(float("nan"))      # affiche ---.--°
                g2.set_setpoint(None)           # affiche ---.--°
            else:
                sp1 = (a * 0.9) % 360
                ac1 = (a * 1.1) % 360
                g1.set_setpoint(sp1); g1.set_angle(ac1)
                e1 = ((ac1 - sp1 + 540) % 360) - 180
                if   e1 > 180: e1 -= 360
                elif e1 < -180: e1 += 360
                g1.set_error(e1/100)

                sp2 = -10 + ((a * 0.7) % 110)
                ac2 = -10 + ((a * 0.95) % 110)
                g2.set_setpoint(sp2); g2.set_angle(ac2)
                g2.set_error(ac2 - sp2)

            a += 1.8
            time.sleep(0.03)

    threading.Thread(target=worker, daemon=True).start()
    sys.exit(app.exec_())

# gui/multi_track_card.py
from PyQt5.QtCore import Qt, pyqtSignal, QEvent
from PyQt5.QtWidgets import (
    QWidget, QGroupBox, QFormLayout, QLabel, QHBoxLayout, QVBoxLayout,
    QScrollArea, QFrame, QSizePolicy, QTabWidget
)
from datetime import datetime, timezone
import time

PASTEL_GREEN = "#CCFFCC"
PASTEL_RED   = "#FFCCCC"
BORDER_CSS   = "border:1px solid #A0A0A0; border-radius:8px;"
CARD_WIDTH   = 130  # largeur fixe des cartes

class MultiTrackCard(QGroupBox):
    """Carte compacte (cliquable) affichant AOS/LOS/DUR/MAX EL/EL NOW pour un objet."""
    clicked = pyqtSignal(str, str)  # obj_type, name

    def __init__(self, ephem, key: str, obj_type: str, name: str, parent=None):
        super().__init__(name, parent)
        self.ephem = ephem
        self.key = key
        self.obj_type = obj_type
        self.target_name = name

        # largeur fixe + size policy
        self.setMinimumWidth(CARD_WIDTH)
        self.setMaximumWidth(CARD_WIDTH)
        sp = self.sizePolicy()
        sp.setHorizontalPolicy(QSizePolicy.Fixed)
        sp.setVerticalPolicy(QSizePolicy.Minimum)
        self.setSizePolicy(sp)
        self.setCursor(Qt.PointingHandCursor)

        self.setStyleSheet(
            f"QGroupBox {{ {BORDER_CSS}; background:{PASTEL_RED}; font-weight:600; margin-top:8px; }}"
            "QGroupBox::title { subcontrol-origin: margin; left: 10px; padding:0 3px; }"
            "QLabel { background: transparent; }"
        )

        form = QFormLayout()
        form.setContentsMargins(8, 10, 8, 8)
        form.setSpacing(6)

        self.lbl_aos = QLabel("—")
        self.lbl_los = QLabel("—")
        self.lbl_dur = QLabel("—")
        self.lbl_maxel = QLabel("—")
        self.lbl_now = QLabel("—")
        for l in (self.lbl_aos, self.lbl_los, self.lbl_dur, self.lbl_maxel, self.lbl_now):
            l.setTextInteractionFlags(Qt.TextSelectableByMouse)

        form.addRow("AOS:", self.lbl_aos)
        form.addRow("LOS:", self.lbl_los)
        form.addRow("DUR:", self.lbl_dur)
        form.addRow("MAX EL:", self.lbl_maxel)
        form.addRow("EL NOW:", self.lbl_now)
        self.setLayout(form)

        # Rendre toute la carte cliquable, labels inclus (sans casser le drag pour sélectionner du texte)
        self._press_ts = None
        self._press_pos = None
        for w in (self.lbl_aos, self.lbl_los, self.lbl_dur, self.lbl_maxel, self.lbl_now):
            w.installEventFilter(self)

        # branchements
        self.ephem.pose_updated.connect(self._on_pose_updated)
        self.ephem.start_object(self.key, self.obj_type, self.target_name, interval=0.5)

    # ---- formatage compact des heures AOS/LOS ----
    def _compact_time(self, utc_str: str) -> str:
        """Convertit 'YYYY-MM-DD HH:MM:SS' (UTC) en 'HH:MM' ou '+1j HH:MM' pour gagner de la place."""
        if not utc_str:
            return None
        try:
            dt = datetime.strptime(utc_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            now = datetime.now(timezone.utc)
            d_days = (dt.date() - now.date()).days
            hhmm = dt.strftime("%H:%M")
            if d_days == 0:
                return hhmm
            if -2 <= d_days <= 2:
                sign = "+" if d_days > 0 else "−"
                return f"{sign}{abs(d_days)}j {hhmm}"
            # plus éloigné → mois-jour HH:MM (toujours étroit)
            return dt.strftime("%m-%d %H:%M")
        except Exception:
            return utc_str  # fallback

    # Event filter : clic court sur un label -> clicked
    def eventFilter(self, obj, ev):
        if ev.type() == QEvent.MouseButtonPress and ev.button() == Qt.LeftButton:
            self._press_ts = time.monotonic()
            self._press_pos = ev.globalPos()
        elif ev.type() == QEvent.MouseButtonRelease and ev.button() == Qt.LeftButton:
            if self._press_ts is not None and self._press_pos is not None:
                dt = time.monotonic() - self._press_ts
                moved = (ev.globalPos() - self._press_pos).manhattanLength()
                if dt < 0.35 and moved <= 6:
                    self.clicked.emit(self.obj_type, self.target_name)
        return False

    def _on_pose_updated(self, key: str, payload: dict):
        if key != self.key or not isinstance(payload, dict):
            return

        aos_full = payload.get("aos_utc")
        los_full = payload.get("los_utc")

        self.lbl_aos.setText(self._compact_time(aos_full) or "—")
        self.lbl_los.setText(self._compact_time(los_full) or "—")

        # tooltips avec l'heure complète UTC
        self.lbl_aos.setToolTip(aos_full or "—")
        self.lbl_los.setToolTip(los_full or "—")

        self.lbl_dur.setText(payload.get("dur_str") or "—")
        max_el = payload.get("max_el_deg")
        self.lbl_maxel.setText(f"{max_el:.1f}°" if isinstance(max_el, (int, float)) else "—")
        el_now = payload.get("el_now_deg", payload.get("el"))
        self.lbl_now.setText(f"{el_now:.1f}°" if isinstance(el_now, (int, float)) else "—")

        vis = payload.get("visible_now")
        self._set_bg(PASTEL_GREEN if vis is True else PASTEL_RED)

    def _set_bg(self, color: str):
        self.setStyleSheet(
            f"QGroupBox {{ {BORDER_CSS}; background:{color}; font-weight:600; margin-top:8px; }}"
            "QGroupBox::title { subcontrol-origin: margin; left: 10px; padding:0 3px; }"
            "QLabel { background: transparent; }"
        )

    # clic sur la zone “vide” de la carte
    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.clicked.emit(self.obj_type, self.target_name)
        super().mousePressEvent(e)

    def closeEvent(self, e):
        # coupe le worker de cette carte si le widget est détruit
        try:
            self.ephem.stop_object(self.key)
        except Exception:
            pass
        super().closeEvent(e)


class MultiTrackStrip(QWidget):
    """Une barre horizontale scrollable de cartes."""
    def __init__(self, ephem, on_pick, parent=None):
        super().__init__(parent)
        self.ephem = ephem
        self.on_pick = on_pick
        self.cards = {}

        content = QWidget()
        self.row = QHBoxLayout(content)
        self.row.setContentsMargins(8, 8, 8, 8)
        self.row.setSpacing(8)
        self.row.setAlignment(Qt.AlignLeft | Qt.AlignTop)

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(content)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

    def add_target(self, obj_type: str, name: str, key: str = None):
        key = key or f"mt:{obj_type}:{name}".lower().replace(" ", "_")
        if key in self.cards:
            return self.cards[key]

        card = MultiTrackCard(self.ephem, key, obj_type, name)
        card.clicked.connect(lambda t=obj_type, n=name: self.on_pick(t, n))
        self.row.addWidget(card, 0, Qt.AlignTop)
        self.cards[key] = card
        return card

    def stop_all(self):
        for k in list(self.cards):
            try:
                self.ephem.stop_object(k)
            except Exception:
                pass


class MultiTrackTabsManager:
    """
    Gère plusieurs MultiTrackStrip — une par onglet d’un QTabWidget — et fournit
    add_target(tab=..., obj_type=..., name=...) pour ajouter une carte dans l’onglet voulu.
    """
    def __init__(self, ephem, on_pick, tab_widget: QTabWidget):
        self.ephem = ephem
        self.on_pick = on_pick
        self.tab_widget = tab_widget
        self._strips_by_page = {}  # QWidget page -> MultiTrackStrip

    def _page_for(self, tab) -> QWidget:
        """tab: titre d’onglet (str) ou QWidget page."""
        if isinstance(tab, QWidget):
            return tab
        if isinstance(tab, str):
            text = tab.strip().lower()
            for i in range(self.tab_widget.count()):
                if self.tab_widget.tabText(i).strip().lower() == text:
                    return self.tab_widget.widget(i)
        return None

    def _ensure_strip(self, page: QWidget) -> 'MultiTrackStrip':
        strip = self._strips_by_page.get(page)
        if strip is not None:
            return strip
        # crée la strip et l’insère dans la page (créé un layout si absent)
        strip = MultiTrackStrip(self.ephem, self.on_pick, parent=page)
        layout = page.layout()
        if layout is None:
            layout = QVBoxLayout(page)
            layout.setContentsMargins(4, 4, 4, 4)
            layout.setSpacing(4)
        layout.addWidget(strip)
        self._strips_by_page[page] = strip
        return strip

    def add_target(self, tab, obj_type: str, name: str, key: str = None):
        """Ajoute une carte dans l’onglet `tab` (titre ou QWidget), pour obj_type/name."""
        page = self._page_for(tab)
        if page is None:
            raise ValueError(f"Onglet introuvable: {tab!r}")
        strip = self._ensure_strip(page)
        return strip.add_target(obj_type=obj_type, name=name, key=key)

    def stop_all(self):
        for _, strip in list(self._strips_by_page.items()):
            strip.stop_all()

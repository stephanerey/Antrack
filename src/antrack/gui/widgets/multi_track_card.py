# gui/multi_track_card.py
from datetime import datetime, timezone
import time

from PyQt5.QtCore import QEvent, Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from antrack.gui.event_countdown import format_next_event_countdown, next_event_tooltip

PASTEL_GREEN = "#CCFFCC"
PASTEL_RED = "#FFCCCC"
BORDER_CSS = "border:1px solid #A0A0A0; border-radius:8px;"
CARD_WIDTH = 135


class MultiTrackCard(QGroupBox):
    """Compact clickable card showing pass summary for one object."""

    clicked = pyqtSignal(str, str)

    def __init__(self, ephem, key: str, obj_type: str, name: str, parent=None, time_formatter=None, time_tooltip_formatter=None):
        super().__init__(name, parent)
        self.ephem = ephem
        self.key = key
        self.obj_type = obj_type
        self.target_name = name
        self._time_formatter = time_formatter
        self._time_tooltip_formatter = time_tooltip_formatter

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
        form.setContentsMargins(8, 8, 8, 6)
        form.setHorizontalSpacing(6)
        form.setVerticalSpacing(2)

        self.lbl_aos = QLabel("-")
        self.lbl_los = QLabel("-")
        self.lbl_next = QLabel("-")
        self.lbl_dur = QLabel("-")
        self.lbl_maxel = QLabel("-")
        self.lbl_now = QLabel("-")
        for label in (
            self.lbl_aos,
            self.lbl_los,
            self.lbl_next,
            self.lbl_dur,
            self.lbl_maxel,
            self.lbl_now,
        ):
            label.setTextInteractionFlags(Qt.TextSelectableByMouse)

        form.addRow("AOS:", self.lbl_aos)
        form.addRow("LOS:", self.lbl_los)
        form.addRow("NEXT:", self.lbl_next)
        form.addRow("DUR:", self.lbl_dur)
        form.addRow("MAX EL:", self.lbl_maxel)
        form.addRow("EL NOW:", self.lbl_now)
        self.setLayout(form)

        self._press_ts = None
        self._press_pos = None
        for label in (
            self.lbl_aos,
            self.lbl_los,
            self.lbl_next,
            self.lbl_dur,
            self.lbl_maxel,
            self.lbl_now,
        ):
            label.installEventFilter(self)

        self.ephem.pose_updated.connect(self._on_pose_updated)
        self.ephem.start_object(self.key, self.obj_type, self.target_name, interval=0.5)

    def _compact_time(self, utc_str: str) -> str | None:
        if not utc_str:
            return None
        if callable(self._time_formatter):
            try:
                return self._time_formatter(utc_str, compact=True)
            except Exception:
                pass
        try:
            dt = datetime.strptime(utc_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            now = datetime.now(timezone.utc)
            d_days = (dt.date() - now.date()).days
            hhmm = dt.strftime("%H:%M")
            if d_days == 0:
                return hhmm
            if -2 <= d_days <= 2:
                sign = "+" if d_days > 0 else "-"
                return f"{sign}{abs(d_days)}j {hhmm}"
            return dt.strftime("%m-%d %H:%M")
        except Exception:
            return utc_str

    def _tooltip_time(self, utc_str: str) -> str:
        if not utc_str:
            return "-"
        if callable(self._time_tooltip_formatter):
            try:
                return self._time_tooltip_formatter(utc_str)
            except Exception:
                pass
        return utc_str

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

        self.lbl_aos.setText(self._compact_time(aos_full) or "-")
        self.lbl_los.setText(self._compact_time(los_full) or "-")
        self.lbl_aos.setToolTip(self._tooltip_time(aos_full))
        self.lbl_los.setToolTip(self._tooltip_time(los_full))

        self.lbl_next.setText(format_next_event_countdown(payload))
        self.lbl_next.setToolTip(next_event_tooltip(payload))

        self.lbl_dur.setText(payload.get("dur_str") or "-")
        max_el = payload.get("max_el_deg")
        self.lbl_maxel.setText(f"{max_el:.1f}°" if isinstance(max_el, (int, float)) else "-")
        el_now = payload.get("el_now_deg", payload.get("el"))
        self.lbl_now.setText(f"{el_now:.1f}°" if isinstance(el_now, (int, float)) else "-")

        vis = payload.get("visible_now")
        self._set_bg(PASTEL_GREEN if vis is True else PASTEL_RED)

    def _set_bg(self, color: str):
        self.setStyleSheet(
            f"QGroupBox {{ {BORDER_CSS}; background:{color}; font-weight:600; margin-top:8px; }}"
            "QGroupBox::title { subcontrol-origin: margin; left: 10px; padding:0 3px; }"
            "QLabel { background: transparent; }"
        )

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self.obj_type, self.target_name)
        super().mousePressEvent(event)

    def closeEvent(self, event):
        try:
            self.ephem.stop_object(self.key)
        except Exception:
            pass
        super().closeEvent(event)


class MultiTrackStrip(QWidget):
    """Horizontal scrollable strip of cards."""

    def __init__(self, ephem, on_pick, parent=None, time_formatter=None, time_tooltip_formatter=None):
        super().__init__(parent)
        self.ephem = ephem
        self.on_pick = on_pick
        self.cards = {}
        self._time_formatter = time_formatter
        self._time_tooltip_formatter = time_tooltip_formatter

        content = QWidget()
        self.row = QHBoxLayout(content)
        self.row.setContentsMargins(8, 8, 8, 8)
        self.row.setSpacing(8)
        self.row.setAlignment(Qt.AlignLeft | Qt.AlignTop)

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(content)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

    def add_target(self, obj_type: str, name: str, key: str = None):
        key = key or f"mt:{obj_type}:{name}".lower().replace(" ", "_")
        if key in self.cards:
            return self.cards[key]

        card = MultiTrackCard(
            self.ephem,
            key,
            obj_type,
            name,
            time_formatter=self._time_formatter,
            time_tooltip_formatter=self._time_tooltip_formatter,
        )
        card.clicked.connect(lambda t=obj_type, n=name: self.on_pick(t, n))
        self.row.addWidget(card, 0, Qt.AlignTop)
        self.cards[key] = card
        return card

    def stop_all(self):
        for key in list(self.cards):
            try:
                self.ephem.stop_object(key)
            except Exception:
                pass


class MultiTrackTabsManager:
    """Manage one MultiTrackStrip per page in a QTabWidget."""

    def __init__(self, ephem, on_pick, tab_widget: QTabWidget, time_formatter=None, time_tooltip_formatter=None):
        self.ephem = ephem
        self.on_pick = on_pick
        self.tab_widget = tab_widget
        self._strips_by_page = {}
        self._time_formatter = time_formatter
        self._time_tooltip_formatter = time_tooltip_formatter

    def _page_for(self, tab) -> QWidget | None:
        if isinstance(tab, QWidget):
            return tab
        if isinstance(tab, str):
            text = tab.strip().lower()
            for index in range(self.tab_widget.count()):
                if self.tab_widget.tabText(index).strip().lower() == text:
                    return self.tab_widget.widget(index)
        return None

    def _ensure_strip(self, page: QWidget) -> MultiTrackStrip:
        strip = self._strips_by_page.get(page)
        if strip is not None:
            return strip

        strip = MultiTrackStrip(
            self.ephem,
            self.on_pick,
            parent=page,
            time_formatter=self._time_formatter,
            time_tooltip_formatter=self._time_tooltip_formatter,
        )
        layout = page.layout()
        if layout is None:
            layout = QVBoxLayout(page)
            layout.setContentsMargins(4, 4, 4, 4)
            layout.setSpacing(4)
        layout.addWidget(strip)
        self._strips_by_page[page] = strip
        return strip

    def add_target(self, tab, obj_type: str, name: str, key: str = None):
        page = self._page_for(tab)
        if page is None:
            raise ValueError(f"Onglet introuvable: {tab!r}")
        strip = self._ensure_strip(page)
        return strip.add_target(obj_type=obj_type, name=name, key=key)

    def stop_all(self):
        for strip in list(self._strips_by_page.values()):
            strip.stop_all()

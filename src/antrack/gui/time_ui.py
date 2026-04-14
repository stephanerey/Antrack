"""Time display helpers for the main window."""

from __future__ import annotations

from datetime import datetime, timezone

from PyQt5.QtCore import QTimer


class TimeUiMixin:
    """Own the time display block and its display-mode cycling."""

    def active_event_time_mode(self) -> str:
        mode = getattr(self, "_time_display_mode", "local")
        return "utc" if mode == "utc" else "local"

    def setup_time_ui(self):
        self._time_display_mode = "local"

        if hasattr(self, "groupBox_Time"):
            try:
                original_handler = self.groupBox_Time.mousePressEvent

                def _on_mouse_press(event):
                    self.cycle_time_display_mode()
                    try:
                        original_handler(event)
                    except Exception:
                        pass

                self.groupBox_Time.mousePressEvent = _on_mouse_press
            except Exception:
                pass

        self._time_ui_timer = QTimer(self)
        self._time_ui_timer.setInterval(1000)
        self._time_ui_timer.timeout.connect(self.refresh_time_display)
        self._time_ui_timer.start()
        self.refresh_time_display()

    def cycle_time_display_mode(self):
        order = ("local", "utc", "sidereal")
        current = getattr(self, "_time_display_mode", "local")
        try:
            index = order.index(current)
        except ValueError:
            index = 0
        self._time_display_mode = order[(index + 1) % len(order)]
        self.refresh_time_display()

    def refresh_time_display(self):
        mode = getattr(self, "_time_display_mode", "local")

        if mode == "utc":
            now = datetime.now(timezone.utc)
            date_text = now.strftime("%Y-%m-%d")
            time_text = now.strftime("%H:%M:%S")
            title_text = "UTC"
        elif mode == "sidereal":
            now = datetime.now(timezone.utc)
            date_text = now.strftime("%Y-%m-%d")
            time_text = self._current_sidereal_time_text()
            title_text = "Sidereal"
        else:
            now = datetime.now().astimezone()
            date_text = now.strftime("%Y-%m-%d")
            time_text = now.strftime("%H:%M:%S")
            title_text = "Local"

        if hasattr(self, "label_date"):
            self.label_date.setText(date_text)
        if hasattr(self, "label_local_time"):
            self.label_local_time.setText(time_text)
        if hasattr(self, "label_LocalTime"):
            self.label_LocalTime.setText(title_text)

    def format_event_time_for_ui(self, utc_str: str, compact: bool = False) -> str:
        if not utc_str:
            return "-"
        try:
            dt_utc = datetime.strptime(utc_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        except Exception:
            return utc_str

        mode = self.active_event_time_mode()
        if mode == "utc":
            dt_display = dt_utc
        else:
            dt_display = dt_utc.astimezone()

        if compact:
            now_display = datetime.now(dt_display.tzinfo)
            d_days = (dt_display.date() - now_display.date()).days
            hhmm = dt_display.strftime("%H:%M")
            if d_days == 0:
                return hhmm
            if -2 <= d_days <= 2:
                sign = "+" if d_days > 0 else "-"
                return f"{sign}{abs(d_days)}j {hhmm}"
            return dt_display.strftime("%m-%d %H:%M")
        return dt_display.strftime("%Y-%m-%d %H:%M:%S")

    def format_event_tooltip_for_ui(self, utc_str: str) -> str:
        if not utc_str:
            return "-"
        label = "UTC" if self.active_event_time_mode() == "utc" else "Local"
        return f"{self.format_event_time_for_ui(utc_str, compact=False)} {label}"

    def _current_sidereal_time_text(self) -> str:
        try:
            if not getattr(self, "observer", None) or getattr(self.observer, "longitude", None) is None:
                return "--:--:--"
            t = self.observer.timescale.now()
            lst_hours = (float(t.gmst) + (float(self.observer.longitude) / 15.0)) % 24.0
            hours = int(lst_hours)
            minutes_float = (lst_hours - hours) * 60.0
            minutes = int(minutes_float)
            seconds = int(round((minutes_float - minutes) * 60.0))
            if seconds >= 60:
                seconds = 0
                minutes += 1
            if minutes >= 60:
                minutes = 0
                hours = (hours + 1) % 24
            return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
        except Exception:
            return "--:--:--"

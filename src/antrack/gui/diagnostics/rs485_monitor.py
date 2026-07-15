"""Non-modal, read-only RS485 communication monitor."""

from __future__ import annotations

import csv
import json
from collections import deque
from pathlib import Path

from PyQt5.QtCore import QAbstractTableModel, QModelIndex, QObject, QSize, QSortFilterProxyModel, Qt, QTimer, pyqtSignal, pyqtSlot
from PyQt5.QtGui import QColor, QFont, QPalette
from PyQt5.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTableView,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from antrack.app_info import display_version
from antrack.core.axis.rs485_diagnostics import (
    ERROR_RESULTS,
    MAX_DIAGNOSTIC_EVENTS,
    RS485_DIAGNOSTICS,
    WARNING_RESULTS,
    Rs485DiagnosticEvent,
    Rs485Direction,
    Rs485Result,
    Rs485Statistics,
)


MAX_VISIBLE_EVENTS = 10_000
UI_BATCH_INTERVAL_MS = 75
STATISTICS_REFRESH_MS = 250
DEFAULT_WINDOW_WIDTH = 1450
DEFAULT_WINDOW_HEIGHT = 1120
AVAILABLE_SCREEN_RATIO = 0.94


class Rs485MonitorPalette:
    """Central theme-aware colors used only by the Qt presentation layer."""

    LIGHT = {
        "tx": QColor("#E8F2FF"),
        "rx": QColor("#E8F7ED"),
        "event": QColor("#F1F2F4"),
        "warning": QColor("#FFE0A3"),
        "error": QColor("#FFD1D1"),
        "text": QColor("#202124"),
        "error_text": QColor("#8B0000"),
        "warning_text": QColor("#6B4100"),
    }
    DARK = {
        "tx": QColor("#253A55"),
        "rx": QColor("#214532"),
        "event": QColor("#383A3D"),
        "warning": QColor("#604716"),
        "error": QColor("#5A292C"),
        "text": QColor("#F0F0F0"),
        "error_text": QColor("#FFB3B3"),
        "warning_text": QColor("#FFD58A"),
    }

    @classmethod
    def colors(cls) -> dict[str, QColor]:
        app = QApplication.instance()
        palette = app.palette() if app is not None else QPalette()
        return cls.DARK if palette.color(QPalette.Base).lightness() < 128 else cls.LIGHT

    @classmethod
    def background(cls, event: Rs485DiagnosticEvent) -> QColor:
        colors = cls.colors()
        if event.result in ERROR_RESULTS:
            return colors["error"]
        if event.result in WARNING_RESULTS:
            return colors["warning"]
        if event.direction == Rs485Direction.TX.value:
            return colors["tx"]
        if event.direction == Rs485Direction.RX.value:
            return colors["rx"]
        return colors["event"]

    @classmethod
    def foreground(cls, event: Rs485DiagnosticEvent) -> QColor:
        colors = cls.colors()
        if event.result in ERROR_RESULTS:
            return colors["error_text"]
        if event.result in WARNING_RESULTS:
            return colors["warning_text"]
        return colors["text"]


class Rs485EventTableModel(QAbstractTableModel):
    COLUMNS = (
        "Time",
        "Axis",
        "Direction",
        "Category",
        "Function",
        "Transaction",
        "Length",
        "Raw frame",
        "Decoded",
        "Latency",
        "Result",
    )

    def __init__(self, parent=None, *, max_rows: int | None = None) -> None:
        super().__init__(parent)
        self.max_rows = None if max_rows is None else max(1, int(max_rows))
        self._events: list[Rs485DiagnosticEvent] = []
        self._display_rows: list[tuple[str, ...]] = []
        self._event_ids: set[int] = set()

    def rowCount(self, parent=QModelIndex()) -> int:  # noqa: N802 - Qt API
        return 0 if parent.isValid() else len(self._events)

    def columnCount(self, parent=QModelIndex()) -> int:  # noqa: N802 - Qt API
        return 0 if parent.isValid() else len(self.COLUMNS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):  # noqa: N802 - Qt API
        if role == Qt.DisplayRole and orientation == Qt.Horizontal and 0 <= section < len(self.COLUMNS):
            return self.COLUMNS[section]
        return super().headerData(section, orientation, role)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid() or not (0 <= index.row() < len(self._events)):
            return None
        event = self._events[index.row()]
        if role == Qt.DisplayRole:
            return self._display_rows[index.row()][index.column()]
        if role == Qt.UserRole:
            return event
        if role == Qt.BackgroundRole:
            return Rs485MonitorPalette.background(event)
        if role == Qt.ForegroundRole:
            return Rs485MonitorPalette.foreground(event)
        if role == Qt.FontRole and (
            index.column() in {2, 10} or event.result in ERROR_RESULTS or event.result in WARNING_RESULTS
        ):
            font = QFont()
            font.setBold(True)
            return font
        if role == Qt.ToolTipRole:
            return event.error_text or event.decoded
        if role == Qt.TextAlignmentRole and index.column() in {1, 2, 4, 5, 6, 9, 10}:
            return int(Qt.AlignCenter)
        return None

    def event_at(self, row: int) -> Rs485DiagnosticEvent | None:
        return self._events[row] if 0 <= row < len(self._events) else None

    def events(self) -> tuple[Rs485DiagnosticEvent, ...]:
        return tuple(self._events)

    def append_events(self, events) -> int:
        new_events = [event for event in events if event.event_id not in self._event_ids]
        if not new_events:
            return 0
        if self.max_rows is not None and len(new_events) >= self.max_rows:
            kept = new_events[-self.max_rows:]
            self.beginResetModel()
            self._events = list(kept)
            self._display_rows = [self._format_row(event) for event in kept]
            self._event_ids = {event.event_id for event in kept}
            self.endResetModel()
            return len(kept)
        overflow = (
            max(0, len(self._events) + len(new_events) - self.max_rows)
            if self.max_rows is not None
            else 0
        )
        if overflow:
            self.beginRemoveRows(QModelIndex(), 0, overflow - 1)
            removed = self._events[:overflow]
            del self._events[:overflow]
            del self._display_rows[:overflow]
            self._event_ids.difference_update(event.event_id for event in removed)
            self.endRemoveRows()
        first = len(self._events)
        last = first + len(new_events) - 1
        self.beginInsertRows(QModelIndex(), first, last)
        self._events.extend(new_events)
        self._display_rows.extend(self._format_row(event) for event in new_events)
        self._event_ids.update(event.event_id for event in new_events)
        self.endInsertRows()
        return len(new_events)

    def set_max_rows(self, max_rows: int | None) -> None:
        """Set the retention limit; ``None`` keeps the whole monitor session."""
        normalized = None if max_rows is None else max(1, int(max_rows))
        if normalized == self.max_rows:
            return
        self.max_rows = normalized
        if normalized is None or len(self._events) <= normalized:
            return
        overflow = len(self._events) - normalized
        self.beginRemoveRows(QModelIndex(), 0, overflow - 1)
        removed = self._events[:overflow]
        del self._events[:overflow]
        del self._display_rows[:overflow]
        self._event_ids.difference_update(event.event_id for event in removed)
        self.endRemoveRows()

    def clear(self) -> None:
        if not self._events:
            return
        self.beginResetModel()
        self._events.clear()
        self._display_rows.clear()
        self._event_ids.clear()
        self.endResetModel()

    @staticmethod
    def _format_row(event: Rs485DiagnosticEvent) -> tuple[str, ...]:
        return (
            event.timestamp_wall.strftime("%H:%M:%S.%f"),
            event.axis,
            event.direction,
            event.category,
            f"FC{event.function_code:02X}" if event.function_code is not None else "—",
            str(event.transaction_id) if event.transaction_id is not None else "—",
            str(len(event.raw_frame)),
            event.raw_frame.hex(" ").upper(),
            event.decoded,
            f"{event.latency_ms:.3f} ms" if event.latency_ms is not None else "—",
            event.result,
        )


class Rs485FilterProxyModel(QSortFilterProxyModel):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.directions = {direction.value for direction in Rs485Direction}
        self.categories: set[str] | None = None
        self.axis = "All"
        self.search_text = ""
        self.errors_only = False
        self.transactions_only = False

    def filterAcceptsRow(self, source_row, source_parent):  # noqa: N802 - Qt API
        model = self.sourceModel()
        event = model.event_at(source_row) if isinstance(model, Rs485EventTableModel) else None
        if event is None:
            return False
        if event.direction not in self.directions:
            return False
        if self.categories is not None and event.category not in self.categories:
            return False
        if self.axis != "All" and event.axis != self.axis:
            return False
        if self.errors_only and event.result not in ERROR_RESULTS | WARNING_RESULTS:
            return False
        if self.transactions_only and event.direction == Rs485Direction.EVENT.value:
            return False
        if self.search_text:
            haystack = " ".join(
                (
                    f"FC{event.function_code:02X}" if event.function_code is not None else "",
                    event.raw_frame.hex(" "),
                    event.decoded,
                    event.result,
                    str(event.transaction_id or ""),
                    event.error_text,
                )
            ).lower()
            if self.search_text not in haystack:
                return False
        return True

    def set_directions(self, directions: set[str]) -> None:
        self.directions = set(directions)
        self.invalidateFilter()

    def set_categories(self, categories: set[str] | None) -> None:
        self.categories = None if categories is None else set(categories)
        self.invalidateFilter()

    def set_axis(self, axis: str) -> None:
        self.axis = axis
        self.invalidateFilter()

    def set_search_text(self, text: str) -> None:
        self.search_text = text.strip().lower()
        self.invalidateFilter()


class _Rs485EventBridge(QObject):
    event_received = pyqtSignal(object)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._subscribed = False

    def start(self) -> None:
        if not self._subscribed:
            RS485_DIAGNOSTICS.subscribe(self._relay)
            self._subscribed = True

    def stop(self) -> None:
        if self._subscribed:
            RS485_DIAGNOSTICS.unsubscribe(self._relay)
            self._subscribed = False

    def _relay(self, event: Rs485DiagnosticEvent) -> None:
        self.event_received.emit(event)


class Rs485MonitorDialog(QDialog):
    """Real-time monitor that observes, but never controls, the serial backend."""

    CATEGORIES = (
        "Position",
        "Status",
        "Move",
        "Stop",
        "Configuration",
        "Limits",
        "Index",
        "Alarm",
        "Retry",
        "Timeout",
        "Error",
        "Port",
        "Unknown",
    )

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("AntTrack — RS485 Communication Monitor")
        screen = QApplication.primaryScreen()
        available_size = screen.availableGeometry().size() if screen is not None else None
        self.resize(_initial_window_size(available_size))
        self.setModal(False)
        self._pending: deque[Rs485DiagnosticEvent] = deque()
        self._view_floor_event_id = 0
        self._statistics = Rs485Statistics()
        self._statistics_event_ids: set[int] = set()
        self._bridge = _Rs485EventBridge(self)
        self._bridge.event_received.connect(self._queue_event, Qt.QueuedConnection)
        self._build_ui()

        self._batch_timer = QTimer(self)
        self._batch_timer.setInterval(UI_BATCH_INTERVAL_MS)
        self._batch_timer.timeout.connect(self._flush_pending)
        self._stats_timer = QTimer(self)
        self._stats_timer.setInterval(STATISTICS_REFRESH_MS)
        self._stats_timer.timeout.connect(self._refresh_statistics)
        self._resume_observation()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.addWidget(self._build_filters())
        splitter = QSplitter(Qt.Horizontal, self)
        splitter.addWidget(self._build_traffic_panel())
        splitter.addWidget(self._build_statistics_panel())
        splitter.setSizes([980, 470])
        splitter.setStretchFactor(0, 7)
        splitter.setStretchFactor(1, 3)
        root.addWidget(splitter, 1)

    def _build_filters(self) -> QWidget:
        box = QGroupBox("Filters and controls", self)
        layout = QGridLayout(box)
        self.direction_checks = {}
        direction_row = QHBoxLayout()
        direction_row.addWidget(QLabel("Direction:"))
        for direction in ("TX", "RX", "EVENT"):
            checkbox = QCheckBox(direction)
            checkbox.setChecked(True)
            checkbox.toggled.connect(self._apply_filters)
            self.direction_checks[direction] = checkbox
            direction_row.addWidget(checkbox)
        direction_row.addStretch(1)
        layout.addLayout(direction_row, 0, 0, 1, 2)

        category_row = QHBoxLayout()
        category_row.addWidget(QLabel("Category:"))
        self.category_checks = {}
        for category in self.CATEGORIES:
            checkbox = QCheckBox(category)
            checkbox.setChecked(True)
            checkbox.toggled.connect(self._apply_filters)
            self.category_checks[category] = checkbox
            category_row.addWidget(checkbox)
        category_row.addStretch(1)
        layout.addLayout(category_row, 1, 0, 1, 4)

        self.axis_combo = QComboBox()
        self.axis_combo.addItems(("All", "AZ", "EL", "Broadcast", "Unknown"))
        self.axis_combo.currentTextChanged.connect(self._apply_filters)
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Function, raw frame, decoded, result or transaction")
        self.search_edit.textChanged.connect(self._apply_filters)
        layout.addWidget(QLabel("Axis:"), 2, 0)
        layout.addWidget(self.axis_combo, 2, 1)
        layout.addWidget(QLabel("Search:"), 2, 2)
        layout.addWidget(self.search_edit, 2, 3)

        quick = QHBoxLayout()
        for label, callback in (
            ("All", self._select_all_filters),
            ("None", self._select_no_filters),
            ("Errors only", self._show_errors_only),
            ("Transactions only", self._show_transactions_only),
        ):
            button = QPushButton(label)
            button.clicked.connect(callback)
            quick.addWidget(button)
        self.pause_check = QCheckBox("Pause display")
        self.pause_check.toggled.connect(self._pause_changed)
        self.autoscroll_check = QCheckBox("Auto-scroll")
        self.autoscroll_check.setChecked(True)
        quick.addWidget(self.pause_check)
        quick.addWidget(self.autoscroll_check)
        quick.addWidget(QLabel("Retention:"))
        self.retention_combo = QComboBox()
        self.retention_combo.addItem("Unlimited (session)", None)
        self.retention_combo.addItem("10,000 rows", MAX_VISIBLE_EVENTS)
        self.retention_combo.addItem("20,000 rows", 20_000)
        self.retention_combo.addItem("50,000 rows", 50_000)
        self.retention_combo.setToolTip(
            "Unlimited keeps every event received while this monitor window remains open."
        )
        self.retention_combo.currentIndexChanged.connect(self._retention_changed)
        quick.addWidget(self.retention_combo)
        quick.addStretch(1)
        layout.addLayout(quick, 3, 0, 1, 4)
        return box

    def _build_traffic_panel(self) -> QWidget:
        panel = QWidget(self)
        layout = QVBoxLayout(panel)
        controls = QHBoxLayout()
        for label, callback in (
            ("Clear view", self._clear_view),
            ("Save log", self._save_log),
            ("Reset statistics", self._reset_statistics),
            ("Clear all", self._clear_all),
            ("Save report", self._save_report),
        ):
            button = QPushButton(label)
            button.clicked.connect(callback)
            controls.addWidget(button)
        controls.addStretch(1)
        layout.addLayout(controls)

        legend = QHBoxLayout()
        legend.addWidget(QLabel("Legend:"))
        for text, direction, result in (
            ("TX AntTrack → Axis", "TX", "OK"),
            ("RX Axis → AntTrack", "RX", "OK"),
            ("EVENT internal", "EVENT", "Info"),
            ("Warning / retry", "EVENT", "Retry"),
            ("Error", "EVENT", "CRC error"),
        ):
            sample = Rs485DiagnosticEvent(0, _now(), 0, direction=direction, result=result)
            label = QLabel(text)
            bg = Rs485MonitorPalette.background(sample).name()
            fg = Rs485MonitorPalette.foreground(sample).name()
            label.setStyleSheet(f"QLabel {{ background: {bg}; color: {fg}; padding: 3px 7px; border-radius: 3px; }}")
            legend.addWidget(label)
        legend.addStretch(1)
        layout.addLayout(legend)

        self.model = Rs485EventTableModel(self)
        self.proxy = Rs485FilterProxyModel(self)
        self.proxy.setSourceModel(self.model)
        self.table = QTableView(self)
        self.table.setModel(self.proxy)
        self.table.setAlternatingRowColors(False)
        self.table.setSelectionBehavior(QTableView.SelectRows)
        self.table.setSortingEnabled(False)
        self.table.setWordWrap(False)
        self.table.verticalScrollBar().sliderPressed.connect(lambda: self.autoscroll_check.setChecked(False))
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeToContents)
        header.setSectionResizeMode(7, QHeaderView.Stretch)
        header.setSectionResizeMode(8, QHeaderView.Stretch)
        layout.addWidget(self.table, 1)
        return panel

    def _build_statistics_panel(self) -> QWidget:
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        panel = QWidget(scroll)
        layout = QVBoxLayout(panel)

        quality_box = QGroupBox("Communication quality")
        quality_form = QFormLayout(quality_box)
        self.quality_label = QLabel("UNKNOWN")
        self.stats_since_label = QLabel("—")
        self.last_valid_label = QLabel("—")
        self.last_error_label = QLabel("—")
        self.port_state_label = QLabel("UNKNOWN")
        quality_form.addRow("Quality:", self.quality_label)
        quality_form.addRow("Statistics since:", self.stats_since_label)
        quality_form.addRow("Last valid response:", self.last_valid_label)
        quality_form.addRow("Last error:", self.last_error_label)
        quality_form.addRow("Port state:", self.port_state_label)
        layout.addWidget(quality_box)

        transactions_box = QGroupBox("Transactions")
        transactions_form = QFormLayout(transactions_box)
        self.transaction_labels = {}
        for key, label in (
            ("total", "Total requests"),
            ("completed", "Completed"),
            ("success", "Successful"),
            ("failed", "Failed"),
            ("pending", "Pending"),
            ("retries", "Retries"),
            ("timeouts", "Timeouts"),
            ("success_rate", "Success rate"),
            ("error_rate", "Error rate"),
        ):
            value = QLabel("0")
            self.transaction_labels[key] = value
            transactions_form.addRow(f"{label}:", value)
        layout.addWidget(transactions_box)

        latency_box = QGroupBox("Latency (ms)")
        latency_form = QFormLayout(latency_box)
        self.latency_labels = {}
        for key in ("count", "min", "mean", "median", "p95", "p99", "max", "stddev", "recent_mean", "recent_p95"):
            value = QLabel("—")
            self.latency_labels[key] = value
            latency_form.addRow(f"{key.replace('_', ' ').title()}:", value)
        layout.addWidget(latency_box)

        errors_box = QGroupBox("Communication errors")
        errors_layout = QVBoxLayout(errors_box)
        self.errors_label = QLabel("No errors")
        self.errors_label.setWordWrap(True)
        errors_layout.addWidget(self.errors_label)
        layout.addWidget(errors_box)

        axis_box = QGroupBox("Per axis")
        axis_layout = QVBoxLayout(axis_box)
        self.axis_table = QTableWidget(2, 6)
        self.axis_table.setHorizontalHeaderLabels(("Axis", "Requests", "Success", "Errors", "Mean ms", "Max ms"))
        self.axis_table.setVerticalHeaderLabels(("AZ", "EL"))
        self.axis_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        axis_layout.addWidget(self.axis_table)
        layout.addWidget(axis_box)

        category_box = QGroupBox("Per category")
        category_layout = QVBoxLayout(category_box)
        self.category_table = QTableWidget(0, 5)
        self.category_table.setHorizontalHeaderLabels(("Category", "Count", "Mean ms", "Errors", "Retries"))
        self.category_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        category_layout.addWidget(self.category_table)
        layout.addWidget(category_box)
        layout.addStretch(1)
        scroll.setWidget(panel)
        return scroll

    @pyqtSlot(object)
    def _queue_event(self, event: Rs485DiagnosticEvent) -> None:
        self._observe_once(event)
        self._pending.append(event)

    def _observe_once(self, event: Rs485DiagnosticEvent) -> None:
        if event.event_id in self._statistics_event_ids:
            return
        self._statistics_event_ids.add(event.event_id)
        self._statistics.observe(event)

    def _resume_observation(self) -> None:
        self._bridge.start()
        for event in RS485_DIAGNOSTICS.snapshot():
            self._observe_once(event)
            if event.event_id > self._view_floor_event_id:
                self._pending.append(event)
        self._batch_timer.start()
        self._stats_timer.start()

    def _flush_pending(self) -> None:
        if self.pause_check.isChecked() or not self._pending:
            return
        batch = list(self._pending)
        self._pending.clear()
        if self.model.append_events(batch) and self.autoscroll_check.isChecked():
            self.table.scrollToBottom()

    def _pause_changed(self, paused: bool) -> None:
        if not paused:
            self._flush_pending()

    def _retention_changed(self, _index: int) -> None:
        self.model.set_max_rows(self.retention_combo.currentData())

    def _apply_filters(self, *_args) -> None:
        self.proxy.errors_only = False
        self.proxy.transactions_only = False
        self.proxy.set_directions({name for name, box in self.direction_checks.items() if box.isChecked()})
        self.proxy.set_categories({name for name, box in self.category_checks.items() if box.isChecked()})
        self.proxy.set_axis(self.axis_combo.currentText())
        self.proxy.set_search_text(self.search_edit.text())

    def _select_all_filters(self) -> None:
        for checkbox in (*self.direction_checks.values(), *self.category_checks.values()):
            checkbox.blockSignals(True)
            checkbox.setChecked(True)
            checkbox.blockSignals(False)
        self.proxy.errors_only = False
        self.proxy.transactions_only = False
        self.axis_combo.setCurrentText("All")
        self.search_edit.clear()
        self._apply_filters()

    def _select_no_filters(self) -> None:
        for checkbox in (*self.direction_checks.values(), *self.category_checks.values()):
            checkbox.blockSignals(True)
            checkbox.setChecked(False)
            checkbox.blockSignals(False)
        self._apply_filters()

    def _show_errors_only(self) -> None:
        self._select_all_filters()
        self.proxy.errors_only = True
        self.proxy.invalidateFilter()

    def _show_transactions_only(self) -> None:
        self._select_all_filters()
        self.proxy.transactions_only = True
        self.proxy.invalidateFilter()

    def _clear_view(self) -> None:
        snapshot = RS485_DIAGNOSTICS.snapshot()
        if snapshot:
            self._view_floor_event_id = snapshot[-1].event_id
        self.model.clear()
        self._pending.clear()

    def _reset_statistics(self) -> None:
        self._statistics.reset()
        # Events already present belong to the period before the reset and
        # must not be counted again if the hidden window is reopened.
        self._statistics_event_ids = {event.event_id for event in RS485_DIAGNOSTICS.snapshot()}
        self._refresh_statistics()

    def _clear_all(self) -> None:
        self._clear_view()
        self._reset_statistics()

    def _save_log(self) -> None:
        filename, _ = QFileDialog.getSaveFileName(self, "Save RS485 log", "rs485-log.csv", "CSV (*.csv)")
        if not filename:
            return
        events = self.model.events()
        fields = tuple(events[0].to_record()) if events else (
            "timestamp_wall", "timestamp_monotonic_ns", "axis", "direction", "category", "function_code",
            "transaction_id", "logical_request_id", "attempt", "raw_frame", "decoded", "latency_ms", "result",
            "error_code", "error_text", "metadata", "event_id",
        )
        try:
            with Path(filename).open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
                writer.writeheader()
                for event in events:
                    record = event.to_record()
                    record["metadata"] = json.dumps(record["metadata"], ensure_ascii=False)
                    writer.writerow(record)
        except Exception as exc:
            QMessageBox.warning(self, "RS485 log", f"Unable to save log: {exc}")

    def _save_report(self) -> None:
        filename, _ = QFileDialog.getSaveFileName(self, "Save diagnostic report", "rs485-report.json", "JSON (*.json)")
        if not filename:
            return
        report = self._statistics.summary()
        report["anttrack_version"] = display_version()
        snapshot = RS485_DIAGNOSTICS.snapshot()
        report["events_in_buffer"] = len(snapshot)
        report["buffer_capacity"] = MAX_DIAGNOSTIC_EVENTS
        report["period_end"] = _now().isoformat(timespec="seconds")
        report["duration_s"] = max(0.0, (_now() - self._statistics.since).total_seconds())
        port_event = next((event for event in reversed(snapshot) if event.category == "Port"), None)
        if port_event is not None:
            report["connection"] = dict(port_event.metadata)
        try:
            Path(filename).write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception as exc:
            QMessageBox.warning(self, "RS485 report", f"Unable to save report: {exc}")

    def _refresh_statistics(self) -> None:
        stats = self._statistics
        summary = stats.latency_summary()
        rates = stats.rates()
        quality = stats.quality()
        quality_colors = {"GOOD": "#2E8B57", "DEGRADED": "#C47A00", "BAD": "#B22222", "UNKNOWN": "#777777"}
        self.quality_label.setText(quality)
        self.quality_label.setStyleSheet(f"font-weight: bold; color: {quality_colors[quality]};")
        self.stats_since_label.setText(stats.since.strftime("%Y-%m-%d %H:%M:%S"))
        self.last_valid_label.setText(stats.last_valid_wall.strftime("%H:%M:%S.%f") if stats.last_valid_wall else "—")
        self.last_error_label.setText(stats.last_error or "—")
        self.port_state_label.setText(stats.port_state)
        for key, value in {
            "total": stats.total_requests,
            "completed": stats.completed,
            "success": stats.successful,
            "failed": stats.failed,
            "pending": stats.pending,
            "retries": stats.retries,
            "timeouts": stats.timeouts,
            "success_rate": f"{rates['success']:.2%}",
            "error_rate": f"{rates['error']:.2%}",
        }.items():
            self.transaction_labels[key].setText(str(value))
        for key, value in summary.items():
            self.latency_labels[key].setText("—" if value is None else (str(value) if key == "count" else f"{float(value):.3f}"))
        self.errors_label.setText(
            "No errors" if not stats.errors else "\n".join(f"{name}: {count}" for name, count in sorted(stats.errors.items()))
        )
        self._refresh_axis_table()
        self._refresh_category_table()

    def _refresh_axis_table(self) -> None:
        for row, axis in enumerate(("AZ", "EL")):
            values = self._statistics.axis[axis]
            count = values["latency_count"]
            mean = (values["latency_sum_us"] / count / 1000.0) if count else 0.0
            maximum = values["latency_max_us"] / 1000.0 if count else 0.0
            for column, value in enumerate((axis, values["requests"], values["success"], values["errors"], f"{mean:.3f}", f"{maximum:.3f}")):
                self.axis_table.setItem(row, column, QTableWidgetItem(str(value)))

    def _refresh_category_table(self) -> None:
        categories = sorted(self._statistics.categories.items())
        self.category_table.setRowCount(len(categories))
        for row, (category, values) in enumerate(categories):
            count = values["latency_count"]
            mean = (values["latency_sum_us"] / count / 1000.0) if count else 0.0
            for column, value in enumerate((category, values["requests"], f"{mean:.3f}", values["errors"], values["retries"])):
                self.category_table.setItem(row, column, QTableWidgetItem(str(value)))

    def showEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().showEvent(event)
        self._resume_observation()

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt API
        self._bridge.stop()
        self._batch_timer.stop()
        self._stats_timer.stop()
        self.hide()
        event.ignore()


def _now():
    from datetime import datetime

    return datetime.now().astimezone()


def _initial_window_size(available_size: QSize | None) -> QSize:
    """Use the tall diagnostic layout while remaining visible on smaller screens."""

    if available_size is None:
        return QSize(DEFAULT_WINDOW_WIDTH, DEFAULT_WINDOW_HEIGHT)
    return QSize(
        min(DEFAULT_WINDOW_WIDTH, max(1, round(available_size.width() * AVAILABLE_SCREEN_RATIO))),
        min(DEFAULT_WINDOW_HEIGHT, max(1, round(available_size.height() * AVAILABLE_SCREEN_RATIO))),
    )

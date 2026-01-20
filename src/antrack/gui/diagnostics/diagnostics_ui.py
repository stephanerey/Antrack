"""Thread diagnostics UI for viewing and managing background tasks."""

from __future__ import annotations

import time
from datetime import datetime
import logging
from typing import Any, Dict, Optional

from PyQt5.QtCore import Qt, QTimer, pyqtSlot
from PyQt5.QtGui import QColor, QFont
from PyQt5.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from antrack.threading_utils.thread_manager import TaskStatus, ThreadManager


class TaskDetailsDialog(QDialog):
    """Dialog showing task details and traceback if available."""

    def __init__(self, task_info: Dict[str, Any], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Task details: {task_info.get('name', '')}")
        self.resize(700, 500)

        layout = QVBoxLayout(self)

        form_layout = QFormLayout()
        form_layout.addRow("Name:", QLabel(task_info.get("name", "")))
        form_layout.addRow("Description:", QLabel(task_info.get("description", "")))

        status = task_info.get("status")
        status_text = status.value if hasattr(status, "value") else str(status)
        form_layout.addRow("Status:", QLabel(status_text))

        start_time = task_info.get("started_at")
        if start_time:
            start_time_str = datetime.fromtimestamp(start_time).strftime("%Y-%m-%d %H:%M:%S")
            form_layout.addRow("Started:", QLabel(start_time_str))

        duration = task_info.get("duration") or task_info.get("duration_so_far") or task_info.get("last_duration_s")
        if duration is not None:
            form_layout.addRow("Duration:", QLabel(f"{duration:.2f} s"))

        tags = task_info.get("tags", [])
        if tags:
            form_layout.addRow("Tags:", QLabel(", ".join(tags)))

        layout.addLayout(form_layout)

        exception = task_info.get("exception") or task_info.get("last_error")
        if exception:
            layout.addWidget(QLabel("Exception:"))
            exception_text = QTextEdit()
            exception_text.setReadOnly(True)
            exception_text.setText(str(exception))
            layout.addWidget(exception_text)

        traceback_text = task_info.get("traceback") or task_info.get("last_traceback")
        if traceback_text:
            layout.addWidget(QLabel("Traceback:"))
            trace = QTextEdit()
            trace.setReadOnly(True)
            trace.setFont(QFont("Courier New", 9))
            trace.setText(traceback_text)
            layout.addWidget(trace)

            copy_btn = QPushButton("Copy traceback")
            copy_btn.clicked.connect(lambda: QApplication.clipboard().setText(traceback_text))  # type: ignore[name-defined]
            layout.addWidget(copy_btn)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)


class ThreadDiagnosticsUI(QWidget):
    """UI widget to display ThreadManager diagnostics."""

    def __init__(self, task_manager: ThreadManager, parent=None) -> None:
        super().__init__(parent)
        self.task_manager = task_manager
        self.logger = logging.getLogger("ThreadDiagnosticsUI")
        self.update_interval = 1000
        self.setup_ui()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update_ui)
        self.timer.start(self.update_interval)

        self.update_ui()

    def setup_ui(self) -> None:
        self.setWindowTitle("Thread diagnostics")
        self.resize(900, 650)

        main_layout = QVBoxLayout(self)

        self.header_label = QLabel("Loading task statistics...")
        self.header_label.setAlignment(Qt.AlignCenter)
        main_layout.addWidget(self.header_label)

        tabs = QTabWidget()

        active_tasks_widget = QWidget()
        active_layout = QVBoxLayout(active_tasks_widget)

        self.active_tasks_table = QTableWidget(0, 5)
        self.active_tasks_table.setHorizontalHeaderLabels(
            ["Name", "Description", "Duration (s)", "Status", "Tags"]
        )
        self.active_tasks_table.horizontalHeader().setStretchLastSection(True)
        self.active_tasks_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.active_tasks_table.itemDoubleClicked.connect(self.show_task_details)
        active_layout.addWidget(self.active_tasks_table)

        active_buttons_layout = QHBoxLayout()
        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.clicked.connect(self.update_ui)
        self.cancel_task_button = QPushButton("Cancel selected")
        self.cancel_task_button.clicked.connect(self.cancel_selected_task)
        self.cancel_all_button = QPushButton("Cancel all")
        self.cancel_all_button.clicked.connect(self.cancel_all_tasks)
        active_buttons_layout.addWidget(self.refresh_button)
        active_buttons_layout.addWidget(self.cancel_task_button)
        active_buttons_layout.addWidget(self.cancel_all_button)
        active_layout.addLayout(active_buttons_layout)

        tabs.addTab(active_tasks_widget, "Active tasks")

        history_widget = QWidget()
        history_layout = QVBoxLayout(history_widget)

        self.history_table = QTableWidget(0, 6)
        self.history_table.setHorizontalHeaderLabels(
            ["Name", "Description", "Started", "Duration (s)", "Status", "Tags"]
        )
        self.history_table.horizontalHeader().setStretchLastSection(True)
        self.history_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.history_table.itemDoubleClicked.connect(self.show_task_details)
        history_layout.addWidget(self.history_table)

        history_buttons_layout = QHBoxLayout()
        self.clear_history_button = QPushButton("Clear history")
        self.clear_history_button.clicked.connect(self.clear_history)
        history_buttons_layout.addWidget(self.clear_history_button)
        history_layout.addLayout(history_buttons_layout)

        tabs.addTab(history_widget, "History")

        errors_widget = QWidget()
        errors_layout = QVBoxLayout(errors_widget)

        self.errors_table = QTableWidget(0, 4)
        self.errors_table.setHorizontalHeaderLabels(["Task", "Time", "Exception", "Tags"])
        self.errors_table.horizontalHeader().setStretchLastSection(True)
        self.errors_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.errors_table.itemDoubleClicked.connect(self.show_error_details)
        errors_layout.addWidget(self.errors_table)

        tabs.addTab(errors_widget, "Errors")

        main_layout.addWidget(tabs)

    @pyqtSlot()
    def update_ui(self) -> None:
        """Refresh UI with latest diagnostics."""
        try:
            stats = self.task_manager.get_diagnostics()
            running_tasks = {
                name: info for name, info in stats.items() if info.get("status") == TaskStatus.RUNNING
            }

            total_tasks = len(stats)
            active_tasks = len(running_tasks)
            completed_tasks = sum(1 for s in stats.values() if s.get("status") == TaskStatus.FINISHED)
            failed_tasks = sum(1 for s in stats.values() if s.get("status") == TaskStatus.FAILED)

            self.header_label.setText(
                f"Total: {total_tasks} | Active: {active_tasks} | "
                f"Completed: {completed_tasks} | Failed: {failed_tasks}"
            )

            self.update_active_tasks_table(running_tasks)
            self.update_history_table(stats)
            self.update_errors_table(self.task_manager.get_task_exceptions())
        except Exception as exc:
            self.logger.error("Diagnostics UI update failed: %s", exc)

    def update_active_tasks_table(self, running_tasks: Dict[str, Dict[str, Any]]) -> None:
        self.active_tasks_table.setRowCount(0)
        row = 0
        for name, info in running_tasks.items():
            self.active_tasks_table.insertRow(row)
            name_item = QTableWidgetItem(name)
            name_item.setData(Qt.UserRole, name)
            self.active_tasks_table.setItem(row, 0, name_item)

            desc_item = QTableWidgetItem(info.get("description", ""))
            self.active_tasks_table.setItem(row, 1, desc_item)

            duration = 0.0
            if info.get("started_at"):
                duration = max(0.0, time.time() - float(info.get("started_at")))
            duration_item = QTableWidgetItem(f"{duration:.2f}")
            self.active_tasks_table.setItem(row, 2, duration_item)

            status = info.get("status", TaskStatus.RUNNING)
            status_text = status.value if hasattr(status, "value") else str(status)
            status_item = QTableWidgetItem(status_text)
            if status == TaskStatus.RUNNING:
                status_item.setBackground(QColor(200, 230, 200))
            self.active_tasks_table.setItem(row, 3, status_item)

            tags = info.get("tags", [])
            tags_item = QTableWidgetItem(", ".join(tags))
            self.active_tasks_table.setItem(row, 4, tags_item)
            row += 1

    def update_history_table(self, stats: Dict[str, Dict[str, Any]]) -> None:
        self.history_table.setRowCount(0)
        row = 0

        def sort_key(item: tuple[str, Dict[str, Any]]) -> float:
            return float(item[1].get("finished_at") or item[1].get("started_at") or 0.0)

        for name, info in sorted(stats.items(), key=sort_key, reverse=True):
            if info.get("status") == TaskStatus.RUNNING:
                continue

            self.history_table.insertRow(row)
            name_item = QTableWidgetItem(name)
            name_item.setData(Qt.UserRole, name)
            self.history_table.setItem(row, 0, name_item)

            desc_item = QTableWidgetItem(info.get("description", ""))
            self.history_table.setItem(row, 1, desc_item)

            start_time = info.get("started_at")
            start_time_str = (
                datetime.fromtimestamp(start_time).strftime("%H:%M:%S") if start_time else ""
            )
            time_item = QTableWidgetItem(start_time_str)
            self.history_table.setItem(row, 2, time_item)

            duration = info.get("last_duration_s") or 0.0
            duration_item = QTableWidgetItem(f"{duration:.2f}")
            self.history_table.setItem(row, 3, duration_item)

            status = info.get("status", "")
            status_text = status.value if hasattr(status, "value") else str(status)
            status_item = QTableWidgetItem(status_text)

            if status == TaskStatus.FINISHED:
                status_item.setBackground(QColor(200, 230, 200))
            elif status == TaskStatus.FAILED:
                status_item.setBackground(QColor(255, 200, 200))
            elif status == TaskStatus.CANCELLED:
                status_item.setBackground(QColor(255, 230, 180))
            self.history_table.setItem(row, 4, status_item)

            tags = info.get("tags", [])
            tags_item = QTableWidgetItem(", ".join(tags))
            self.history_table.setItem(row, 5, tags_item)
            row += 1

    def update_errors_table(self, exceptions: Dict[str, Dict[str, Any]]) -> None:
        self.errors_table.setRowCount(0)
        row = 0

        sorted_exceptions = sorted(
            [(name, info) for name, info in exceptions.items() if info.get("time")],
            key=lambda x: x[1]["time"] or 0.0,
            reverse=True,
        )

        for name, info in sorted_exceptions:
            self.errors_table.insertRow(row)
            name_item = QTableWidgetItem(name)
            name_item.setData(Qt.UserRole, name)
            self.errors_table.setItem(row, 0, name_item)

            error_time = info.get("time")
            time_str = datetime.fromtimestamp(error_time).strftime("%Y-%m-%d %H:%M:%S") if error_time else ""
            time_item = QTableWidgetItem(time_str)
            self.errors_table.setItem(row, 1, time_item)

            error_msg = str(info.get("exception") or "")
            error_item = QTableWidgetItem(error_msg)
            self.errors_table.setItem(row, 2, error_item)

            task_info = self.task_manager.get_diagnostics().get(name, {})
            tags = task_info.get("tags", [])
            tags_item = QTableWidgetItem(", ".join(tags))
            self.errors_table.setItem(row, 3, tags_item)
            row += 1

    def _task_info(self, task_name: str) -> Dict[str, Any]:
        info = self.task_manager.get_diagnostics().get(task_name, {}).copy()
        info["name"] = task_name
        return info

    def show_task_details(self, item) -> None:
        table = item.tableWidget()
        row = item.row()
        task_name = table.item(row, 0).data(Qt.UserRole)
        dialog = TaskDetailsDialog(self._task_info(task_name), self)
        dialog.exec_()

    def show_error_details(self, item) -> None:
        table = item.tableWidget()
        row = item.row()
        task_name = table.item(row, 0).data(Qt.UserRole)
        info = self._task_info(task_name)
        exc = self.task_manager.get_task_exceptions().get(task_name, {})
        info.update(exc)
        dialog = TaskDetailsDialog(info, self)
        dialog.exec_()

    def cancel_selected_task(self) -> None:
        selected_items = self.active_tasks_table.selectedItems()
        if not selected_items:
            return
        row = selected_items[0].row()
        task_name = self.active_tasks_table.item(row, 0).data(Qt.UserRole)
        if task_name:
            self.task_manager.stop_thread(task_name)
            self.update_ui()

    def cancel_all_tasks(self) -> None:
        for name in list(self.task_manager.threads.keys()):
            self.task_manager.stop_thread(name)
        self.update_ui()

    def clear_history(self) -> None:
        self.task_manager.clear_history(keep_running=True)
        self.update_ui()

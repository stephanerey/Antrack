# threading_utils/diagnostics_ui.py
# Interface de diagnostic pour surveiller les threads et tâches asyncio

import time
from datetime import datetime
import logging
from typing import Dict, Any, List, Optional

from PyQt5.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QTableWidget, 
                             QTableWidgetItem, QPushButton, QLabel, QComboBox,
                             QTabWidget, QTextEdit, QHeaderView, QDialog,
                             QDialogButtonBox, QFormLayout, QLineEdit, QCheckBox)
from PyQt5.QtCore import Qt, QTimer, pyqtSlot
from PyQt5.QtGui import QColor, QFont

from antrack.threading_utils.thread_manager import AsyncTaskManager, TaskStatus


class TaskDetailsDialog(QDialog):
    """Dialogue affichant les détails d'une tâche spécifique"""

    def __init__(self, task_info: Dict[str, Any], parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Détails de la tâche: {task_info.get('name', '')}")
        self.resize(600, 400)

        layout = QVBoxLayout(self)

        # Informations de base
        form_layout = QFormLayout()
        form_layout.addRow("Nom:", QLabel(task_info.get('name', '')))
        form_layout.addRow("Description:", QLabel(task_info.get('description', '')))
        form_layout.addRow("Statut:", QLabel(task_info.get('status', '').value if hasattr(task_info.get('status', ''), 'value') else str(task_info.get('status', ''))))

        # Formatage du temps
        start_time = task_info.get('start_time')
        if start_time:
            start_time_str = datetime.fromtimestamp(start_time).strftime('%Y-%m-%d %H:%M:%S')
            form_layout.addRow("Démarré le:", QLabel(start_time_str))

        duration = task_info.get('duration') or task_info.get('duration_so_far')
        if duration is not None:
            form_layout.addRow("Durée:", QLabel(f"{duration:.2f} secondes"))

        # Tags
        tags = task_info.get('tags', [])
        if tags:
            form_layout.addRow("Tags:", QLabel(", ".join(tags)))

        layout.addLayout(form_layout)

        # Exception et traceback si disponible
        exception = task_info.get('exception')
        if exception:
            layout.addWidget(QLabel("Exception:"))
            exception_text = QTextEdit()
            exception_text.setReadOnly(True)
            exception_text.setText(str(exception))
            layout.addWidget(exception_text)

        traceback = task_info.get('traceback')
        if traceback:
            layout.addWidget(QLabel("Traceback:"))
            traceback_text = QTextEdit()
            traceback_text.setReadOnly(True)
            traceback_text.setFont(QFont("Courier New", 9))
            traceback_text.setText(traceback)
            layout.addWidget(traceback_text)

        # Boutons
        buttons = QDialogButtonBox(QDialogButtonBox.Ok)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)


class ThreadDiagnosticsUI(QWidget):
    """Interface de diagnostic pour visualiser et gérer les threads"""

    def __init__(self, task_manager: AsyncTaskManager, parent=None):
        super().__init__(parent)
        self.task_manager = task_manager
        self.logger = logging.getLogger("ThreadDiagnosticsUI")
        self.update_interval = 1000  # ms
        self.setup_ui()

        # Démarrer les mises à jour périodiques
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update_ui)
        self.timer.start(self.update_interval)

        # Connexion des signaux du gestionnaire de tâches
        self.task_manager.diagnostics.task_created.connect(self.on_task_created)
        self.task_manager.diagnostics.task_completed.connect(self.on_task_completed)
        self.task_manager.diagnostics.task_failed.connect(self.on_task_failed)

        self.update_ui()

    def setup_ui(self):
        self.setWindowTitle("Diagnostic des threads")
        self.resize(800, 600)

        main_layout = QVBoxLayout(self)

        # En-tête avec statistiques globales
        self.header_label = QLabel("Chargement des statistiques...")
        self.header_label.setAlignment(Qt.AlignCenter)
        main_layout.addWidget(self.header_label)

        # Onglets
        tabs = QTabWidget()

        # Onglet des tâches actives
        active_tasks_widget = QWidget()
        active_layout = QVBoxLayout(active_tasks_widget)

        # Tableau des tâches actives
        self.active_tasks_table = QTableWidget(0, 5)
        self.active_tasks_table.setHorizontalHeaderLabels(["Nom", "Description", "Durée (s)", "Statut", "Tags"])
        self.active_tasks_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.active_tasks_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.active_tasks_table.itemDoubleClicked.connect(self.show_task_details)
        active_layout.addWidget(self.active_tasks_table)

        # Boutons d'action pour les tâches actives
        active_buttons_layout = QHBoxLayout()
        self.refresh_button = QPushButton("Actualiser")
        self.refresh_button.clicked.connect(self.update_ui)
        self.cancel_task_button = QPushButton("Annuler la tâche sélectionnée")
        self.cancel_task_button.clicked.connect(self.cancel_selected_task)
        active_buttons_layout.addWidget(self.refresh_button)
        active_buttons_layout.addWidget(self.cancel_task_button)
        active_layout.addLayout(active_buttons_layout)

        tabs.addTab(active_tasks_widget, "Tâches actives")

        # Onglet de l'historique des tâches
        history_widget = QWidget()
        history_layout = QVBoxLayout(history_widget)

        # Tableau de l'historique
        self.history_table = QTableWidget(0, 6)
        self.history_table.setHorizontalHeaderLabels(["Nom", "Description", "Démarré le", "Durée (s)", "Statut", "Tags"])
        self.history_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.history_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.history_table.itemDoubleClicked.connect(self.show_task_details)
        history_layout.addWidget(self.history_table)

        # Boutons d'action pour l'historique
        history_buttons_layout = QHBoxLayout()
        self.clear_history_button = QPushButton("Effacer l'historique")
        self.clear_history_button.clicked.connect(self.clear_history)
        history_buttons_layout.addWidget(self.clear_history_button)
        history_layout.addLayout(history_buttons_layout)

        tabs.addTab(history_widget, "Historique")

        # Onglet des erreurs
        errors_widget = QWidget()
        errors_layout = QVBoxLayout(errors_widget)

        self.errors_table = QTableWidget(0, 4)
        self.errors_table.setHorizontalHeaderLabels(["Tâche", "Heure", "Exception", "Tags"])
        self.errors_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.errors_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.errors_table.itemDoubleClicked.connect(self.show_error_details)
        errors_layout.addWidget(self.errors_table)

        tabs.addTab(errors_widget, "Erreurs")

        main_layout.addWidget(tabs)

        # Contrôles de mise à jour
        update_layout = QHBoxLayout()
        update_layout.addWidget(QLabel("Intervalle de mise à jour:"))
        self.update_interval_combo = QComboBox()
        self.update_interval_combo.addItems(["0.5s", "1s", "2s", "5s", "10s"])
        self.update_interval_combo.setCurrentIndex(1)  # 1s par défaut
        self.update_interval_combo.currentIndexChanged.connect(self.change_update_interval)
        update_layout.addWidget(self.update_interval_combo)
        update_layout.addStretch()
        main_layout.addLayout(update_layout)

    @pyqtSlot()
    def update_ui(self):
        """Mise à jour de l'interface avec les dernières données"""
        try:
            # Statistiques globales
            stats = self.task_manager.get_stats()
            running_tasks = self.task_manager.get_running_tasks()

            total_tasks = len(stats)
            active_tasks = len(running_tasks)
            completed_tasks = sum(1 for s in stats.values() 
                               if s.get('status') == TaskStatus.COMPLETED)
            failed_tasks = sum(1 for s in stats.values() 
                             if s.get('status') == TaskStatus.FAILED)

            self.header_label.setText(
                f"Total des tâches: {total_tasks} | Actives: {active_tasks} | "  
                f"Terminées: {completed_tasks} | Échouées: {failed_tasks}"
            )

            # Tâches actives
            self.update_active_tasks_table(running_tasks)

            # Historique des tâches
            self.update_history_table(stats)

            # Erreurs
            self.update_errors_table(self.task_manager.get_task_exceptions())

        except Exception as e:
            self.logger.error(f"Erreur lors de la mise à jour de l'interface: {e}")

    def update_active_tasks_table(self, running_tasks: Dict[str, Dict[str, Any]]):
        """Mise à jour du tableau des tâches actives"""
        self.active_tasks_table.setRowCount(0)
        row = 0

        for name, info in running_tasks.items():
            self.active_tasks_table.insertRow(row)

            # Nom de la tâche
            name_item = QTableWidgetItem(name)
            name_item.setData(Qt.UserRole, name)  # Stocker le nom pour référence
            self.active_tasks_table.setItem(row, 0, name_item)

            # Description
            desc_item = QTableWidgetItem(info.get('description', ''))
            self.active_tasks_table.setItem(row, 1, desc_item)

            # Durée
            duration = info.get('duration_so_far', 0)
            duration_item = QTableWidgetItem(f"{duration:.2f}")
            self.active_tasks_table.setItem(row, 2, duration_item)

            # Statut
            status = info.get('status', TaskStatus.RUNNING)
            status_text = status.value if hasattr(status, 'value') else str(status)
            status_item = QTableWidgetItem(status_text)
            if status == TaskStatus.RUNNING:
                status_item.setBackground(QColor(200, 230, 200))  # Vert clair
            self.active_tasks_table.setItem(row, 3, status_item)

            # Tags
            tags = info.get('tags', [])
            tags_item = QTableWidgetItem(", ".join(tags))
            self.active_tasks_table.setItem(row, 4, tags_item)

            row += 1

    def update_history_table(self, stats: Dict[str, Dict[str, Any]]):
        """Mise à jour du tableau d'historique des tâches"""
        self.history_table.setRowCount(0)
        row = 0

        # Trier par heure de début (plus récent en premier)
        sorted_stats = sorted(
            [(name, info) for name, info in stats.items() if 'start_time' in info],
            key=lambda x: x[1]['start_time'],
            reverse=True
        )

        for name, info in sorted_stats:
            # Ne pas inclure les tâches en cours
            if name in self.task_manager.tasks and not self.task_manager.tasks[name].done():
                continue

            self.history_table.insertRow(row)

            # Nom de la tâche
            name_item = QTableWidgetItem(name)
            name_item.setData(Qt.UserRole, name)  # Stocker le nom pour référence
            self.history_table.setItem(row, 0, name_item)

            # Description
            description = info.get('description', '')
            desc_item = QTableWidgetItem(description)
            self.history_table.setItem(row, 1, desc_item)

            # Heure de début formatée
            start_time = info.get('start_time')
            start_time_str = datetime.fromtimestamp(start_time).strftime('%H:%M:%S') if start_time else ''
            time_item = QTableWidgetItem(start_time_str)
            self.history_table.setItem(row, 2, time_item)

            # Durée
            duration = info.get('duration', 0)
            duration_item = QTableWidgetItem(f"{duration:.2f}")
            self.history_table.setItem(row, 3, duration_item)

            # Statut
            status = info.get('status', '')
            status_text = status.value if hasattr(status, 'value') else str(status)
            status_item = QTableWidgetItem(status_text)

            # Couleur selon le statut
            if status == TaskStatus.COMPLETED:
                status_item.setBackground(QColor(200, 230, 200))  # Vert
            elif status == TaskStatus.FAILED:
                status_item.setBackground(QColor(255, 200, 200))  # Rouge
            elif status == TaskStatus.CANCELLED:
                status_item.setBackground(QColor(255, 230, 180))  # Orange

            self.history_table.setItem(row, 4, status_item)

            # Tags
            tags = info.get('tags', [])
            tags_item = QTableWidgetItem(", ".join(tags))
            self.history_table.setItem(row, 5, tags_item)

            row += 1

    def update_errors_table(self, exceptions: Dict[str, Dict[str, Any]]):
        """Mise à jour du tableau des erreurs"""
        self.errors_table.setRowCount(0)
        row = 0

        # Trier par heure (plus récent en premier)
        sorted_exceptions = sorted(
            [(name, info) for name, info in exceptions.items() if info.get('time')],
            key=lambda x: x[1]['time'] or 0,
            reverse=True
        )

        for name, info in sorted_exceptions:
            self.errors_table.insertRow(row)

            # Nom de la tâche
            name_item = QTableWidgetItem(name)
            name_item.setData(Qt.UserRole, name)  # Stocker le nom pour référence
            self.errors_table.setItem(row, 0, name_item)

            # Heure formatée
            error_time = info.get('time')
            time_str = datetime.fromtimestamp(error_time).strftime('%Y-%m-%d %H:%M:%S') if error_time else ''
            time_item = QTableWidgetItem(time_str)
            self.errors_table.setItem(row, 1, time_item)

            # Message d'erreur
            exception = info.get('exception')
            error_msg = str(exception) if exception else ''
            error_item = QTableWidgetItem(error_msg)
            self.errors_table.setItem(row, 2, error_item)

            # Tags (si disponibles dans les statistiques)
            task_stats = self.task_manager.task_stats.get(name, {})
            tags = task_stats.get('tags', [])
            tags_item = QTableWidgetItem(", ".join(tags))
            self.errors_table.setItem(row, 3, tags_item)

            row += 1

    def show_task_details(self, item):
        """Affiche les détails d'une tâche lorsqu'on double-clique sur une ligne"""
        table = item.tableWidget()
        row = item.row()
        task_name = table.item(row, 0).data(Qt.UserRole)

        # Récupérer toutes les informations sur la tâche
        task_info = {}
        task_info['name'] = task_name

        # Chercher dans les tâches actives
        active_tasks = self.task_manager.get_running_tasks()
        if task_name in active_tasks:
            task_info.update(active_tasks[task_name])

        # Chercher dans les statistiques pour plus d'informations
        stats = self.task_manager.get_stats()
        if task_name in stats:
            task_info.update(stats[task_name])

        # Chercher les exceptions
        exceptions = self.task_manager.get_task_exceptions()
        if task_name in exceptions:
            task_info['exception'] = exceptions[task_name].get('exception')
            task_info['traceback'] = exceptions[task_name].get('traceback')

        # Afficher le dialogue
        dialog = TaskDetailsDialog(task_info, self)
        dialog.exec_()

    def show_error_details(self, item):
        """Affiche les détails d'une erreur lorsqu'on double-clique sur une ligne"""
        table = item.tableWidget()
        row = item.row()
        task_name = table.item(row, 0).data(Qt.UserRole)

        # Récupérer toutes les informations sur l'erreur
        exceptions = self.task_manager.get_task_exceptions()
        stats = self.task_manager.get_stats()

        task_info = {
            'name': task_name,
            'description': self.task_manager.task_descriptions.get(task_name, ''),
            'status': TaskStatus.FAILED
        }

        if task_name in exceptions:
            task_info['exception'] = exceptions[task_name].get('exception')
            task_info['traceback'] = exceptions[task_name].get('traceback')
            task_info['time'] = exceptions[task_name].get('time')

        if task_name in stats:
            task_info.update(stats[task_name])

        # Afficher le dialogue
        dialog = TaskDetailsDialog(task_info, self)
        dialog.exec_()

    def cancel_selected_task(self):
        """Annule la tâche sélectionnée dans le tableau des tâches actives"""
        selected_items = self.active_tasks_table.selectedItems()
        if not selected_items:
            return

        row = selected_items[0].row()
        task_name = self.active_tasks_table.item(row, 0).data(Qt.UserRole)

        if task_name and self.task_manager.is_task_running(task_name):
            self.logger.info(f"Annulation de la tâche: {task_name}")
            self.task_manager.cancel_task(task_name)
            self.update_ui()  # Rafraîchir immédiatement

    def clear_history(self):
        """Efface l'historique des tâches terminées"""
        self.task_manager.clear_history(keep_running=True)
        self.update_ui()

    def change_update_interval(self, index):
        """Change l'intervalle de mise à jour de l'interface"""
        intervals = [500, 1000, 2000, 5000, 10000]  # en ms
        if index < len(intervals):
            self.update_interval = intervals[index]
            self.timer.stop()
            self.timer.start(self.update_interval)
            self.logger.debug(f"Intervalle de mise à jour changé à {self.update_interval}ms")

    @pyqtSlot(str, str)
    def on_task_created(self, name, description):
        """Réagit à la création d'une nouvelle tâche"""
        self.update_ui()

    @pyqtSlot(str, float)
    def on_task_completed(self, name, duration):
        """Réagit à la fin d'une tâche"""
        self.update_ui()

    @pyqtSlot(str, str)
    def on_task_failed(self, name, error):
        """Réagit à l'échec d'une tâche"""
        self.update_ui()

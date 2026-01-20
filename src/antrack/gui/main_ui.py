# main_ui.py
# Interface principale de l'application avec intégration du client Axis Qt

import logging

from PyQt5.uic import loadUi
import os
from PyQt5.QtWidgets import (QMainWindow, QLabel, QMessageBox, QAction, QStatusBar, QComboBox, QLineEdit, QHBoxLayout, QPushButton,
    QVBoxLayout, QDialog)
from PyQt5.QtWidgets import QGridLayout


from PyQt5.QtCore import QTimer
from antrack.gui.axis.axis_client_qt import AxisClientQt
from antrack.core.axis.axis_client import AxisClientPollingAdapter
from antrack.app_info import version
from antrack.tracking.observer import Observer
from antrack.tracking.tracking import TrackedObject, Tracker
from antrack.tracking.ephemeris_service import EphemerisService, load_planets
from antrack.gui.ephemeris_qt import EphemerisQtAdapter
from antrack.gui.widgets.angle_gauge_widget import AngleGauge
from antrack.gui.widgets.multi_track_card import MultiTrackStrip
from antrack.gui.widgets.multi_track_card import MultiTrackTabsManager
from antrack.gui.instruments.powermeter_qt import Powermeter
from antrack.gui.widgets.calibration import CalibrationPlots
from antrack.gui.diagnostics.diagnostics_ui import ThreadDiagnosticsUI
from antrack.gui.dialogs.log_viewer_ui import LogViewerDialog
from antrack.utils.paths import get_tle_dir


# local stylesheets
standard_label_color = "color: black; background-color: #CCE5FF;"
green_label_color = "color: black; background-color: #CCFFCC;"
red_label_color = "color: black; background-color: #FFCCCC;"
orange_label_color = "color: black; background-color: #FFCC99;"
lightgrey_label_color = "color: #DCDCDC; background-color: #5D5D69;"


class MainUi(QMainWindow):
    def __init__(self, thread_manager, settings, ip_address, port, parent=None):
        super().__init__(parent)
        ui_path = os.path.join(os.path.dirname(__file__), "main.ui")
        loadUi(ui_path, self)
        self.setWindowTitle("Antenna Tracker")

        self.settings = settings
        self.thread_manager = thread_manager
        self.logger = logging.getLogger("MainUi")
        self.task_manager = self.thread_manager
        self.setup_menu()

        # Initialiser la carte
        #self.map_canvas = MapCanvas()
        # self.map_container = self.findChild(QWidget, "mapContainer")
        # if self.map_container:
        #     layout = QVBoxLayout(self.map_container)
        #     layout.setContentsMargins(0, 0, 0, 0)
        #     layout.addWidget(self.map_canvas)

        # Éléments d'affichage (lignes) pour la carte et cache des dernières valeurs
        # self._antenna_line = None
        # self._target_line = None
        # self._last_pos_ts = None
        # self._last_az = None
        # self._last_el = None

        # Paramètres pour la connexion
        self.ip_address = ip_address
        self.port = port
        self.connection_ready = False

        # Barre d'état
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage("Prêt")

        # AZ and EL gauges
        self.g1 = AngleGauge(
            span_angle=360,
            forbidden_ranges=[(45, 90), (270, 300)],
            decimals=2,
            origin_screen_deg=90,
            clockwise=True,
            show_cardinal_labels=True,
            major_anchor_deg=0,
            gradient_angle_deg=315,
            # positions par défaut (tu peux jouer avec ces 6 ratios)
            set_value_y_ratio=-0.52, actual_value_y_ratio=0.00, error_value_y_ratio=0.52,
            set_label_y_ratio=-0.75, actual_label_y_ratio=-0.25, error_label_y_ratio=0.32,
        )

        # Cadran 2 : -10..+100° (élévation), 0° à droite, anti-horaire
        self.g2 = AngleGauge(
            start_angle_deg=-10, span_angle=110,
            minor_step_deg=10, major_step_deg=30,
            forbidden_ranges=[(-10, 0), (95, 100)],
            decimals=2,
            origin_screen_deg=0,
            clockwise=False,
            show_cardinal_labels=True,
            major_anchor_deg=0,  # majors sur 0/30/60/90
            gradient_angle_deg=315,
            set_value_y_ratio=-0.55, actual_value_y_ratio=0.00, error_value_y_ratio=0.50,
            set_label_y_ratio=-0.75, actual_label_y_ratio=-0.25, error_label_y_ratio=0.30,
        )
        self.verticalLayout_gauges.layout().addWidget(self.g1)
        self.verticalLayout_gauges.layout().addWidget(self.g2)

        # Bouton de poursuite antenne (toggle Track/Stop)
        self.pushButton_server_connect.clicked.connect(self.on_connect_button_clicked)
        self.pushButton_antenna_track.clicked.connect(self.on_track_button_clicked)
        self.pushButton_antenna_track.setEnabled(False)  # désactivé tant qu’on n’a pas de consignes

        # État UI par défaut au lancement (avant toute connexion)
        self.ui_set_default_state()
        self._user_requested_disconnect = False
        self._connect_toggle_in_progress = False

        # Objet suivi (poursuite) et UI de sélection
        self.tracked_object = TrackedObject()
        self.tracker = None  # sera initialisé après connexion Axis
        self.telemetry_ready = False  # basculé par les signaux de télémétrie
        self._auto_restart_tracking = False  # relance auto du tracking après reconnexion si non stoppé par l'utilisateur
        # Cache des dernières télémétries valides pour l'affichage des erreurs au redémarrage
        self._last_tel_az = None
        self._last_tel_el = None

        try:
            # Tracker tab
            self.setup_tracker_tab()
            # Calibration tab: plots
            self._setup_calibration_tab()
        except Exception as e:
            self.logger.error(f"setup_tracker_tab ou calibration a échoué: {e}")

        self.logger.info("Interface principale initialisée")


        # Charger les ephemerides (Skyfield) et initialiser l'observateur depuis les settings
        try:
            self.planets = load_planets(logger=self.logger)
            earth = self.planets["earth"]
            self.logger.info("Ephemerides chargees")
        except Exception as e:
            self.planets = None
            earth = None
            self.logger.exception("Impossible de charger les ephemerides: %s", e)
            QMessageBox.warning(
                self,
                "Ephemeris",
                f"Ephemeris loading failed:\n{e}",
            )

        # --- Dossier TLE local (src/data/tle) ---
        try:
            self.tle_dir = get_tle_dir()
            self.tle_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            self.tle_dir = None
            self.logger.error(f"Impossible d'initialiser le dossier TLE: {e}")

        # Créer l'observateur
        try:
            obs_cfg = self.settings.get("OBSERVER", {}) if isinstance(self.settings, dict) else {}
            name = obs_cfg.get("name")
            latitude = obs_cfg.get("latitude")
            longitude = obs_cfg.get("longitude")
            altitude = obs_cfg.get("altitude")
            self.observer = Observer()
            self.observer.create_observer(name, longitude, latitude, altitude, earth)
            self.logger.info(f"Observateur initialisé: {getattr(self.observer, 'name', '')} "
                             f"({latitude}, {longitude}, {altitude} m)")
        except Exception as e:
            self.observer = None
            self.logger.error(f"Impossible d'initialiser l'observateur: {e}")

        # Service d'ephemerides multi-objets
        #self.ephem = EphemerisService(self.thread_manager, self.observer, self.planets, logger=self.logger)
        # Tu peux surcharger les groupes via settings: SETTINGS["TLE_GROUPS"] = ["stations","active","amateur","weather","starlink"]
        tle_groups = None
        try:
            tle_groups = self.settings.get("TLE_GROUPS", None) if isinstance(self.settings, dict) else None
        except Exception:
            pass

        base_ephem = EphemerisService(
            self.thread_manager,
            self.observer,
            self.planets,
            logger=self.logger,
            tle_dir=self.tle_dir,
            tle_groups=tle_groups,
            tle_refresh_hours=6.0,
        )
        self.ephem = EphemerisQtAdapter(base_ephem)
        self.ephem.pose_updated.connect(self._on_pose_updated)
        # cree le multitracking cards
        self.multi_cards = MultiTrackTabsManager(
            ephem=self.ephem,
            on_pick=self._on_multi_pick,  # callback au clic d’une carte
            tab_widget=self.tabWidget_3
        )

        # Tous les objets du système solaire (sans Earth)
        solar_system_bodies = ["Sun", "Moon", "Mercury", "Venus", "Mars","Jupiter", "Saturn", "Uranus", "Neptune"]
        for body in solar_system_bodies:
            self.multi_cards.add_target(tab="Solar System", obj_type="Solar System", name=body)

        # Satellites
        satellites = ['ISS', 'NOAA 20', 'NOAA 21', 'CUBESAT', "ES'HAIL 2"]
        for sat in satellites:
            self.multi_cards.add_target(tab="Artificial Satellites", obj_type="Artificial Satellite", name=sat)

        # celestial object
        celestials = ['Sirius', 'Polaris', 'Betelgeuse']
        for celest in celestials:
            self.multi_cards.add_target(tab="Celestial Objects", obj_type="Star", name=celest)

        # Powermeter
        self.powermeter = Powermeter(self.settings, logger=self.logger.getChild("Powermeter"))
        self.powermeter.power_ready.connect(self._on_powermeter_value)
        self.pushButton_readpowermeter.clicked.connect(self.start_powermeter_read)


    ##############
    # UI Functions
    ##############
    def setup_menu(self):
        """
        Configure le menu de l'application
        """
        menu_bar = self.menuBar()

        # Menu Fichier
        file_menu = menu_bar.addMenu("Files")
        exit_action = QAction("Quit", self)
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        # Menu Outils
        tools_menu = menu_bar.addMenu("Tools")

        # Action pour le diagnostic des threads
        thread_diag_action = QAction("Threads diagnosis", self)
        thread_diag_action.triggered.connect(self.show_thread_diagnostics)
        tools_menu.addAction(thread_diag_action)

        # Menu Aide
        help_menu = menu_bar.addMenu("Help")
        view_log_action = QAction("Display logs...", self)
        view_log_action.triggered.connect(self.show_log_viewer)
        help_menu.addAction(view_log_action)
        about_action = QAction("About", self)
        about_action.triggered.connect(self.show_about)
        help_menu.addAction(about_action)


    def show_thread_diagnostics(self):
        """Show the thread diagnostics dialog."""
        try:
            dlg = QDialog(self)
            dlg.setWindowTitle("Thread diagnostics")
            dlg.resize(900, 650)
            layout = QVBoxLayout(dlg)
            layout.addWidget(ThreadDiagnosticsUI(self.thread_manager, parent=dlg))
            dlg.exec_()
        except Exception as e:
            self.logger.error(f"Erreur lors de l'affichage du diagnostic des threads: {e}")
            QMessageBox.warning(self, "Erreur",
                              f"Impossible d'afficher le diagnostic des threads: {str(e)}")

    def show_about(self):
        """
        Affiche la boîte de dialogue À propos
        """

        QMessageBox.about(self, "À propos d'Antenna Tracker",
                        f"Antenna Noise Tracker {version}\n\n" 
                        f"Author: Stephane Rey\n" 
                        f"Date: 10.09.2025")

    def show_log_viewer(self):
        """Show the log viewer dialog."""
        try:
            LogViewerDialog(self).exec_()
        except Exception as e:
            self.logger.error(f"Erreur show_log_viewer: {e}")
            QMessageBox.warning(self, "Journal", f"Erreur lors de l'affichage du journal:{e}")

    def setup_tracker_tab(self):
        """
        Crée les contrôles pour sélectionner un objet et afficher tout le payload.
        Requiert un widget self.tab_TargetObject dans le .ui.
        Ajouts: sélecteurs TLE (groupe + satellite) + champ recherche (nom/NORAD) pour "Artificial Satellite".
        """
        try:
            container = getattr(self, "tab_TargetObject", None)
            if container is None:
                self.logger.warning("tab_TargetObject introuvable dans l'UI")
                return

            layout = QVBoxLayout()

            # --- Type d'objet ---
            self.object_dropdown = QComboBox()
            self.object_dropdown.addItems(["Select Type", "Solar System", "Star", "Artificial Satellite", "Radio Source"])
            self.object_dropdown.currentIndexChanged.connect(self.update_secondary_dropdown)
            layout.addWidget(self.object_dropdown)

            # --- Widgets spécifiques SAT (cachés par défaut) ---
            # 1) Groupe TLE
            self.tle_group_dropdown = QComboBox()
            self.tle_group_dropdown.setToolTip("TLE group (stations, active, amateur, weather, ...)")
            self.tle_group_dropdown.currentIndexChanged.connect(self._on_tle_group_changed)
            self.tle_group_dropdown.setVisible(False)
            layout.addWidget(self.tle_group_dropdown)

            # 2) Satellite du groupe
            self.tle_sat_dropdown = QComboBox()
            self.tle_sat_dropdown.currentIndexChanged.connect(lambda _i: self.apply_target_btn.setEnabled(bool(self._current_sat_query())))
            self.tle_sat_dropdown.setToolTip("Satellite (depuis le groupe sélectionné)")
            self.tle_sat_dropdown.setVisible(False)
            layout.addWidget(self.tle_sat_dropdown)

            # 3) Recherche directe
            self.tle_query_edit = QLineEdit()
            self.tle_query_edit.textChanged.connect(lambda _t: self.apply_target_btn.setEnabled(bool(self._current_sat_query())))
            self.tle_query_edit.setPlaceholderText("Nom/NORAD (optionnel, ex: 25544 ou 'NOAA 19')")
            self.tle_query_edit.setVisible(False)
            layout.addWidget(self.tle_query_edit)

            # --- Widgets RadioSource (cachés par défaut) ---
            self.rs_group_dropdown = QComboBox()
            self.rs_group_dropdown.setToolTip("Catalogue (fichier CSV)")
            self.rs_group_dropdown.currentIndexChanged.connect(self._on_rs_group_changed)
            self.rs_group_dropdown.setVisible(False)
            layout.addWidget(self.rs_group_dropdown)

            self.rs_source_dropdown = QComboBox()
            self.rs_source_dropdown.setToolTip("Source radio (depuis le catalogue)")
            self.rs_source_dropdown.setVisible(False)
            layout.addWidget(self.rs_source_dropdown)

            self.rs_query_edit = QLineEdit()
            self.rs_query_edit.setPlaceholderText("Nom (optionnel, ex: 3C 273)")
            self.rs_query_edit.setVisible(False)
            layout.addWidget(self.rs_query_edit)

            # activer/désactiver 'Appliquer' selon saisie RadioSource
            self.rs_query_edit.textChanged.connect(
                lambda _t: self.apply_target_btn.setEnabled(bool(self._current_rs_query()))
            )
            self.rs_source_dropdown.currentIndexChanged.connect(
                lambda _i: self.apply_target_btn.setEnabled(bool(self._current_rs_query()))
            )

            # --- Sélecteur secondaire générique (conserve ta logique existante) ---
            self.specific_object_dropdown = QComboBox()
            self.specific_object_dropdown.currentIndexChanged.connect(self.on_target_selection_changed)
            layout.addWidget(self.specific_object_dropdown)

            # --- Bouton Appliquer ---
            self.apply_target_btn = QPushButton("Appliquer la sélection")
            # désactivé par défaut (activé selon le type/état)
            self.apply_target_btn.setEnabled(False)

            self.apply_target_btn.setToolTip(
                "Démarre le calcul périodique des consignes (Set) pour l'objet sélectionné. "
                "N'actionne pas les moteurs. Utilisez 'Track' pour démarrer/arrêter le suivi."
            )
            self.apply_target_btn.clicked.connect(self.on_apply_target_clicked)
            layout.addWidget(self.apply_target_btn)

            # --- Labels existants (RA/DEC/etc.) ---
            # self.azimuth_label = QLabel("Azimuth: ---.--°")
            # self.elevation_label = QLabel("Elevation: ---.--°")
            # self.distance_label = QLabel("Distance: N/A")
            # layout.addWidget(self.azimuth_label)
            # layout.addWidget(self.elevation_label)
            # layout.addWidget(self.distance_label)

            grid = QGridLayout()
            row = 0

            def add_row(title: str, attr_name: str):
                nonlocal row
                grid.addWidget(QLabel(title), row, 0)
                v = QLabel("—")
                setattr(self, attr_name, v)
                grid.addWidget(v, row, 1)
                row += 1

            add_row("Azimuth:", "azimuth_label")
            add_row("Elevation:", "elevation_label")
            add_row("Distance (km):", "distance_label")
            add_row("RA (hms):", "target_ra_label")
            add_row("DEC (dms):", "target_dec_label")
            add_row("Distance (AU):", "target_dist_au_label")
            add_row("Visible now:", "target_visible_now_label")
            add_row("AOS (UTC):", "target_aos_label")
            add_row("LOS (UTC):", "target_los_label")
            add_row("Duration:", "target_dur_label")
            add_row("Max EL:", "target_max_el_label")
            add_row("Max EL @ (UTC):", "target_max_el_time_label")
            add_row("EL NOW:", "target_el_now_label")

            layout.addLayout(grid)

            # Valeurs par défaut (Solar System → Sun)
            try:
                self.object_dropdown.setCurrentText("Solar System")
                self.update_secondary_dropdown()
                for i in range(self.specific_object_dropdown.count()):
                    if self.specific_object_dropdown.itemText(i).lower() == "sun":
                        self.specific_object_dropdown.setCurrentIndex(i)
                        break
            except Exception:
                pass

            container.setLayout(layout)
        except Exception as e:
            self.logger.error(f"Erreur setup_tracker_tab: {e}")

    def update_secondary_dropdown(self):
        """
        Met à jour la zone secondaire selon le type choisi.
        - Solar System / Star : dropdown générique.
        - Artificial Satellite : widgets TLE (groupe + satellite + champ libre).
        - Radio Source : widgets RadioSource (catalogue + source + champ libre).
        """
        try:
            sel = (self.object_dropdown.currentText() or "").strip()

            # ---------- Visibilité des blocs ----------
            is_sat = (sel == "Artificial Satellite")
            is_rs = (sel == "Radio Source")

            # TLE widgets
            if hasattr(self, "tle_group_dropdown"):
                self.tle_group_dropdown.setVisible(is_sat)
            if hasattr(self, "tle_sat_dropdown"):
                self.tle_sat_dropdown.setVisible(is_sat)
            if hasattr(self, "tle_query_edit"):
                self.tle_query_edit.setVisible(is_sat)

            # RadioSource widgets
            if hasattr(self, "rs_group_dropdown"):
                self.rs_group_dropdown.setVisible(is_rs)
            if hasattr(self, "rs_source_dropdown"):
                self.rs_source_dropdown.setVisible(is_rs)
            if hasattr(self, "rs_query_edit"):
                self.rs_query_edit.setVisible(is_rs)

            # Dropdown générique visible seulement pour Solar System / Star
            self.specific_object_dropdown.setVisible(not (is_sat or is_rs))

            # ---------- Peuplement des listes ----------
            self.specific_object_dropdown.clear()

            if sel == "Solar System":
                self.specific_object_dropdown.addItems(
                    ["Mercury", "Venus", "Earth", "Mars", "Jupiter", "Saturn", "Uranus", "Neptune", "Moon", "Sun"]
                )

            elif sel == "Star":
                self.specific_object_dropdown.addItems(["Polaris", "Sirius", "Betelgeuse"])

            elif is_sat:
                # Groupes TLE
                try:
                    self.ephem.tle_refresh(force=False)
                    groups = self.ephem.tle_groups() or []
                    if not groups and isinstance(self.settings, dict):
                        groups = self.settings.get("TLE_GROUPS", []) or []
                    if not groups:
                        groups = ["stations", "active", "amateur", "weather"]

                    self.tle_group_dropdown.blockSignals(True)
                    self.tle_group_dropdown.clear()
                    self.tle_group_dropdown.addItems(groups)
                    self.tle_group_dropdown.blockSignals(False)

                    # Peupler satellites du 1er groupe
                    self._populate_tle_satellites_for_current_group()
                except Exception as e:
                    self.logger.error(f"update_secondary_dropdown TLE init error: {e}")

            elif is_rs:
                # Catalogues RadioSource
                try:
                    self.ephem.rs_refresh(force=False)
                    groups = self.ephem.rs_groups() or []

                    self.rs_group_dropdown.blockSignals(True)
                    self.rs_group_dropdown.clear()
                    self.rs_group_dropdown.addItems(groups)
                    self.rs_group_dropdown.blockSignals(False)

                    # Peupler sources du 1er catalogue
                    self._populate_rs_sources_for_current_group()
                except Exception as e:
                    self.logger.error(f"update_secondary_dropdown RS init error: {e}")

            # ---------- Message d’info ----------
            try:
                if is_sat:
                    sel_obj = "(sélectionnez un satellite)"
                elif is_rs:
                    sel_obj = "(sélectionnez une source radio)"
                else:
                    sel_obj = self.specific_object_dropdown.currentText()
                self.status_bar.showMessage(
                    f"Sélection: {sel} / {sel_obj} — cliquez 'Appliquer la sélection' pour démarrer.",
                    3000
                )
            except Exception:
                pass

            # ---------- État du bouton Appliquer (à la fin) ----------
            try:
                if is_sat:
                    ok = bool(self._current_sat_query())
                elif is_rs:
                    ok = bool(self._current_rs_query())
                else:
                    ok = self.specific_object_dropdown.count() > 0
                self.apply_target_btn.setEnabled(ok)
            except Exception:
                pass

        except Exception as e:
            self.logger.error(f"Erreur update_secondary_dropdown: {e}")

    def _on_tle_group_changed(self):
        """Slot: changement du groupe TLE → recharge la liste des satellites."""
        try:
            self._populate_tle_satellites_for_current_group()
        except Exception as e:
            self.logger.error(f"_on_tle_group_changed error: {e}")

    def _populate_tle_satellites_for_current_group(self):
        """Remplit le dropdown des satellites pour le groupe TLE courant."""
        try:
            if not hasattr(self, "tle_group_dropdown") or not hasattr(self, "tle_sat_dropdown"):
                return
            grp = (self.tle_group_dropdown.currentText() or "").strip()
            if not grp:
                self.tle_sat_dropdown.clear()
                self.tle_sat_dropdown.addItem("(aucun groupe)")
                return

            # rafraîchit le cache TLE si nécessaire
            try:
                self.ephem.tle_refresh(force=False)
            except Exception:
                pass

            # récupère [(name, norad), ...] trié par nom
            rows = []
            try:
                rows = self.ephem.tle_list_satellites(group=grp) or []
            except Exception as e:
                self.logger.error(f"tle_list_satellites error: {e}")

            self.tle_sat_dropdown.blockSignals(True)
            self.tle_sat_dropdown.clear()
            if not rows:
                self.tle_sat_dropdown.addItem("(vide)")
            else:
                for name, norad in rows:
                    label = f"{name} [{norad}]" if isinstance(norad, int) and norad >= 0 else name
                    self.tle_sat_dropdown.addItem(label)
            self.tle_sat_dropdown.blockSignals(False)

            # ... après avoir rempli self.tle_sat_dropdown
            try:
                name = self._parse_sat_label(self.tle_sat_dropdown.currentText() or "")
                ok = bool(name) or bool((self.tle_query_edit.text() or "").strip())
                self.apply_target_btn.setEnabled(ok)
            except Exception:
                pass

        except Exception as e:
            self.logger.error(f"_populate_tle_satellites_for_current_group error: {e}")
            try:
                self.tle_sat_dropdown.blockSignals(True)
                self.tle_sat_dropdown.clear()
                self.tle_sat_dropdown.addItem("(erreur)")
                self.tle_sat_dropdown.blockSignals(False)
            except Exception:
                pass

    def _parse_rs_label(self, label: str) -> str:
        lab = (label or "").strip()
        if lab in ("(vide)", "(erreur)", "(aucun groupe)"):
            return ""
        return lab

    def _current_rs_query(self) -> str:
        q = (self.rs_query_edit.text() if hasattr(self, "rs_query_edit") else "") or ""
        q = q.strip()
        if q:
            return q
        label = (self.rs_source_dropdown.currentText() if hasattr(self, "rs_source_dropdown") else "") or ""
        return self._parse_rs_label(label)

    def _on_rs_group_changed(self):
        try:
            self._populate_rs_sources_for_current_group()
        except Exception as e:
            self.logger.error(f"_on_rs_group_changed error: {e}")

    def _populate_rs_sources_for_current_group(self):
        try:
            grp = (self.rs_group_dropdown.currentText() or "").strip()
            self.ephem.rs_refresh(force=False)
            names = self.ephem.rs_list_sources(group=grp) or []
            self.rs_source_dropdown.blockSignals(True)
            self.rs_source_dropdown.clear()
            if not names:
                self.rs_source_dropdown.addItem("(vide)")
            else:
                for n in names:
                    self.rs_source_dropdown.addItem(n)
            self.rs_source_dropdown.blockSignals(False)
        except Exception as e:
            self.logger.error(f"_populate_rs_sources_for_current_group error: {e}")
            self.rs_source_dropdown.blockSignals(True)
            self.rs_source_dropdown.clear()
            self.rs_source_dropdown.addItem("(erreur)")
            self.rs_source_dropdown.blockSignals(False)

    def _on_pose_updated(self, key: str, payload: dict):
        """
        Reçoit les positions calculées par EphemerisService.
        ⚠️ Ne met à jour le tracker et l'onglet Target que pour la clé 'primary'.
        """
        try:
            if key != "primary":
                # Les MultiTrackCard recevront aussi ce signal et se mettent à jour
                # elles-mêmes sans modifier la sélection de tracking.
                return

            az = payload.get('az');
            el = payload.get('el')
            dist_km = payload.get('dist_km');
            dist_au = payload.get('dist_au')
            ra_hms = payload.get('ra_hms');
            dec_dms = payload.get('dec_dms')

            if isinstance(az, (int, float)):
                self.tracked_object.az_set = az
            if isinstance(el, (int, float)):
                self.tracked_object.el_set = el
            if isinstance(dist_km, (int, float)):
                self.tracked_object.distance_km = dist_km
            if isinstance(dist_au, (int, float)):
                self.tracked_object.distance_au = dist_au
            if isinstance(ra_hms, tuple):
                h, m, s = ra_hms
                self.tracked_object.ra_set.decimal_hours = (h + m / 60.0 + s / 3600.0)
            if isinstance(dec_dms, tuple):
                d, dm, ds = dec_dms
                sign = -1 if d < 0 else 1
                self.tracked_object.dec_set.decimal_degrees = (abs(d) + dm / 60.0 + ds / 3600.0) * sign

            # UI Target (seulement pour l'objet sélectionné)
            self.ui_update_tracked_display(payload)

            az_ok = isinstance(self.tracked_object.az_set, (int, float))
            el_ok = isinstance(self.tracked_object.el_set, (int, float))
            if self.telemetry_ready and az_ok and el_ok and hasattr(self, "pushButton_antenna_track"):
                self.pushButton_antenna_track.setEnabled(True)

            try:
                if hasattr(self, "calib_plots") and self.calib_plots is not None:
                    self.calib_plots.update_current(az, el)
            except Exception:
                pass

        except Exception as e:
            self.logger.error(f"Erreur _on_pose_updated: {e}")

    def has_connection(self) -> bool:
        return bool(getattr(self, "axis_client", None) and self.axis_client.is_connected())

    def has_setpoints(self) -> bool:
        az = getattr(self.tracked_object, "az_set", None)
        el = getattr(self.tracked_object, "el_set", None)
        return isinstance(az, (int, float)) and isinstance(el, (int, float))


    ##########
    # Tracking
    ##########
    def start_object_selection_tracking(self):
        """
        Démarre (ou redémarre) le calcul périodique des positions pour l'objet sélectionné (clé 'primary').
        """
        try:
            sel_type = self.object_dropdown.currentText() if hasattr(self, "object_dropdown") else "Select Type"
            sel_obj = self.specific_object_dropdown.currentText() if hasattr(self, "specific_object_dropdown") else ""
            self.ephem.start_object("primary", sel_type, sel_obj, interval=0.1)
        except Exception as e:
            self.logger.error(f"Erreur start_object_selection_tracking: {e}")


    def on_target_selection_changed(self):
        """
        Changement de sélection : ne pas démarrer automatiquement.
        Informer l'utilisateur d'utiliser 'Appliquer la sélection'.
        """
        try:
            sel_type = self.object_dropdown.currentText() if hasattr(self, "object_dropdown") else "?"
            sel_obj = self.specific_object_dropdown.currentText() if hasattr(self, "specific_object_dropdown") else "?"
            self.status_bar.showMessage(
                f"Sélection: {sel_type} / {sel_obj} — cliquez 'Appliquer la sélection' pour démarrer l'actualisation.",
                3000
            )
        except Exception as e:
            self.logger.error(f"Erreur on_target_selection_changed: {e}")

    def track_object(self):
        """
        Obsolète: conservée pour compatibilité, ne fait plus que démarrer le tracking si nécessaire.
        """
        try:
            self.start_object_selection_tracking()
        except Exception as e:
            self.logger.error(f"Erreur track_object (deprecated): {e}")

    def ui_update_tracked_display(self, payload: dict):
        """
        Met à jour l'onglet Target depuis le worker d'éphémérides.
        Affiche az/el/distance, RA/DEC, AU, visibilité et infos AOS/LOS/DUR/MAX EL/EL NOW.
        """
        try:
            name = payload.get('name') or "Unknown"
            az = payload.get('az')
            el = payload.get('el')
            dist_km = payload.get('dist_km')
            ra_hms = payload.get('ra_hms')  # tuple (h,m,s)
            dec_dms = payload.get('dec_dms')  # tuple (d,m,s)
            dist_au = payload.get('dist_au')

            # Titre objet (si tu as un label dédié dans ton .ui principal)
            if hasattr(self, "label_tracked_object"):
                self.label_tracked_object.setText(str(name))
                self.label_tracked_object.setStyleSheet(orange_label_color)

            # --- Base temps réel déjà présente ---
            if hasattr(self, "azimuth_label"):
                self.azimuth_label.setText(f"Azimuth: {az:.2f}°" if isinstance(az, (int, float)) else "Azimuth: N/A")
            if hasattr(self, "elevation_label"):
                self.elevation_label.setText(f"Elevation: {el:.2f}°" if isinstance(el, (int, float)) else "Elevation: N/A")
            if hasattr(self, "distance_label"):
                self.distance_label.setText(f"Distance: {dist_km:.0f} km" if isinstance(dist_km, (int, float)) else "Distance: N/A")

            running = bool(getattr(self, "tracker", None) and self.tracker.is_running())
            if not running:
                if hasattr(self, "label_antenna_az_set_deg"):
                    self.label_antenna_az_set_deg.setText(f"{az:.2f}°" if isinstance(az, (int, float)) else "---.--°")
                if hasattr(self, "label_antenna_el_set_deg"):
                    self.label_antenna_el_set_deg.setText(f"{el:.2f}°" if isinstance(el, (int, float)) else "---.--°")

            # --- RA/DEC/AU (nouveaux labels dans l'onglet) ---
            if isinstance(ra_hms, tuple) and hasattr(self, "target_ra_label"):
                h, m, s = ra_hms
                self.target_ra_label.setText(f"{int(h)}h {int(m)}m {s:04.1f}s")
            if isinstance(dec_dms, tuple) and hasattr(self, "target_dec_label"):
                d, dm, ds = dec_dms
                self.target_dec_label.setText(f"{int(d)}° {int(dm)}' {ds:04.1f}\"")
            if isinstance(dist_au, (int, float)) and hasattr(self, "target_dist_au_label"):
                self.target_dist_au_label.setText(f"{dist_au:.3f}")

            # (si tu as aussi les labels globaux existants pour RA/DEC/AU, tu peux garder ça)
            if isinstance(ra_hms, tuple) and hasattr(self, "label_antenna_ra_set"):
                h, m, s = ra_hms
                self.label_antenna_ra_set.setText(f"{int(h)}h {int(m)}m {s:04.1f}s")
                self.label_antenna_ra_set.setStyleSheet(standard_label_color)
            if isinstance(dec_dms, tuple) and hasattr(self, "label_antenna_dec_set"):
                d, dm, ds = dec_dms
                self.label_antenna_dec_set.setText(f"{int(d)}° {int(dm)}' {ds:04.1f}\"")
                self.label_antenna_dec_set.setStyleSheet(standard_label_color)
            if isinstance(dist_au, (int, float)) and hasattr(self, "label_object_distance_au"):
                self.label_object_distance_au.setText(f"{dist_au:.3f}")
                self.label_object_distance_au.setStyleSheet(standard_label_color)

            # --- Pass info (AOS/LOS/DUR/MAX EL/EL NOW/Visible Now) ---
            vis = payload.get('visible_now', None)
            if hasattr(self, "target_visible_now_label"):
                if vis is True:
                    self.target_visible_now_label.setText("YES")
                    self.target_visible_now_label.setStyleSheet(green_label_color)
                elif vis is False:
                    self.target_visible_now_label.setText("NO")
                    self.target_visible_now_label.setStyleSheet(red_label_color)
                else:
                    self.target_visible_now_label.setText("—")
                    self.target_visible_now_label.setStyleSheet(standard_label_color)

            if hasattr(self, "target_aos_label"):
                self.target_aos_label.setText(payload.get('aos_utc') or "—")
            if hasattr(self, "target_los_label"):
                self.target_los_label.setText(payload.get('los_utc') or "—")

            if hasattr(self, "target_dur_label"):
                dur_str = payload.get('dur_str')
                if dur_str:
                    self.target_dur_label.setText(dur_str)
                else:
                    self.target_dur_label.setText("—")

            if hasattr(self, "target_max_el_label"):
                max_el = payload.get('max_el_deg')
                self.target_max_el_label.setText(f"{max_el:.1f}°" if isinstance(max_el, (int, float)) else "—")
            if hasattr(self, "target_max_el_time_label"):
                self.target_max_el_time_label.setText(payload.get('max_el_time_utc') or "—")

            if hasattr(self, "target_el_now_label"):
                el_now = payload.get('el_now_deg', None)
                if not isinstance(el_now, (int, float)):
                    el_now = el if isinstance(el, (int, float)) else None
                self.target_el_now_label.setText(f"{el_now:.2f}°" if isinstance(el_now, (int, float)) else "—")

        except Exception as e:
            self.logger.error(f"Erreur ui_update_tracked_display: {e}")


    def start_tracking_ui_timer(self):
        try:
            if getattr(self, "_tracking_ui_timer", None) is None:
                self._tracking_ui_timer = QTimer(self)
                self._tracking_ui_timer.setInterval(int(100))  # 100 ms
                self._tracking_ui_timer.timeout.connect(self._update_tracking_ui)
            if not self._tracking_ui_timer.isActive():
                self._tracking_ui_timer.start()
        except Exception as e:
            self.logger.error(f"Erreur start_tracking_ui_timer: {e}")


    def prime_axis_motion(self):
        """
        Séquence de remise en état côté contrôleur: STOP des axes + réapplication des vitesses.
        Exécutée via l'AxisCoreLoop avec des timeouts courts pour ne pas bloquer l'UI.
        """
        try:
            # Récupérer des vitesses de référence
            ant = {}
            if isinstance(self.settings, dict):
                ant = self.settings.get("ANTENNA", self.settings.get("antenna", {}))
            az_far = float(ant.get("az_speed_far_tracking", 500))
            el_far = float(ant.get("el_speed_far_tracking", 500))

            # STOP AZ/EL
            try:
                self.thread_manager.run_coro("AxisCoreLoop", self.axis_client.axisClient.stop_az, timeout=1.0)
            except Exception:
                pass
            try:
                self.thread_manager.run_coro("AxisCoreLoop", self.axis_client.axisClient.stop_el, timeout=1.0)
            except Exception:
                pass

            # Réappliquer vitesses
            try:
                self.thread_manager.run_coro("AxisCoreLoop", lambda: self.axis_client.axisClient.set_az_speed(az_far), timeout=1.0)
                # Mettre à jour le cache UI
                try:
                    self.axis_client.antenna.az_setrate = az_far
                except Exception:
                    pass
            except Exception as e:
                self.logger.debug(f"prime_axis_motion: set_az_speed erreur: {e}")
            try:
                self.thread_manager.run_coro("AxisCoreLoop", lambda: self.axis_client.axisClient.set_el_speed(el_far), timeout=1.0)
                try:
                    self.axis_client.antenna.el_setrate = el_far
                except Exception:
                    pass
            except Exception as e:
                self.logger.debug(f"prime_axis_motion: set_el_speed erreur: {e}")

        except Exception as e:
            self.logger.error(f"Erreur prime_axis_motion: {e}")

    def on_antenna_telemetry_ready(self, payload: dict):
        """
        Marque telemetry_ready quand az/el sont numériques.
        """
        try:
            if isinstance(payload, dict):
                az = payload.get("az")
                el = payload.get("el")
                if isinstance(az, (int, float)) and isinstance(el, (int, float)):
                    self.telemetry_ready = True
        except Exception:
            pass

    def stop_tracking_ui_timer(self):
        try:
            if getattr(self, "_tracking_ui_timer", None) and self._tracking_ui_timer.isActive():
                self._tracking_ui_timer.stop()
        except Exception as e:
            self.logger.error(f"Erreur stop_tracking_ui_timer: {e}")

    def _ui_show_tracking_stopped(self):
        """
        Affiche l'état 'Stopped' et réinitialise les erreurs en gris avec -.-- °
        """
        try:
            if hasattr(self, "label_antenna_status"):
                self.label_antenna_status.setText("Stopped")
                self.label_antenna_status.setStyleSheet(red_label_color)
            if hasattr(self, "label_antenna_az_error"):
                self.label_antenna_az_error.setText("-.-- °")
                self.label_antenna_az_error.setStyleSheet(lightgrey_label_color)
            self.g1.set_setpoint(None)
            self.g1.set_error(None)

            if hasattr(self, "label_antenna_el_error"):
                self.label_antenna_el_error.setText("-.-- °")
                self.label_antenna_el_error.setStyleSheet(lightgrey_label_color)
            self.g2.set_setpoint(None)
            self.g2.set_error(None)
        except Exception:
            pass

    def _start_tracker_when_ready(self, attempts_left: int = 20):
        """
        Attend que la télémétrie (az/el) et les setpoints (az_set/el_set) soient prêts, puis démarre le tracker.
        Retente toutes 250ms jusqu'à épuisement (5s).
        """
        try:
            tel_ok = bool(self.telemetry_ready)
            az_set = getattr(self.tracked_object, "az_set", None)
            el_set = getattr(self.tracked_object, "el_set", None)
            set_ok = isinstance(az_set, (int, float)) and isinstance(el_set, (int, float))

            if tel_ok and set_ok:
                # Purge éventuelle
                try:
                    self.thread_manager.stop_thread("TrackingLoop")
                except Exception:
                    pass

                # Démarrer
                self.tracker.start()

                # Refresh immédiat des setpoints dans l'UI (labels + jauges)
                try:
                    if hasattr(self, "label_antenna_az_set_deg"):
                        self.label_antenna_az_set_deg.setText(f"{az_set:.2f}°")
                    if hasattr(self, "label_antenna_el_set_deg"):
                        self.label_antenna_el_set_deg.setText(f"{el_set:.2f}°")
                    self.g1.set_setpoint(az_set)
                    self.g2.set_setpoint(el_set)
                except Exception:
                    pass

                # Premier update + timer
                try:
                    self._update_tracking_ui()
                except Exception:
                    pass
                self.start_tracking_ui_timer()
                try:
                    self.pushButton_antenna_track.setText("Stop")
                except Exception:
                    pass
            else:
                if attempts_left > 0:
                    if attempts_left == 20:
                        self.logger.info(f"[Tracking] Waiting for readiness "
                                         f"(telemetry_ready={self.telemetry_ready}, az_set={az_set}, el_set={el_set})")
                    QTimer.singleShot(250, lambda: self._start_tracker_when_ready(attempts_left - 1))
                else:
                    self.logger.warning("[Tracking] Abandon start: telemetry or setpoints still not ready")
                    QMessageBox.information(self, "Tracking",
                                            "Tracking non démarré: télémétrie ou setpoints indisponibles.")
        except Exception as e:
            self.logger.error(f"_start_tracker_when_ready error: {e}")

    def _update_tracking_ui(self):
        """
        Met à jour l'UI de suivi (statut, erreurs, jauges) en fonction de la télémétrie et des setpoints.
        Règle :
          - Tracking ON  : labels SET = valeurs, jauges set_setpoint(...), erreurs calculées.
          - Tracking OFF : labels SET gérés par ui_update_tracked_display, jauges sans setpoint/erreur.
        """
        try:
            # Heartbeat
            if not hasattr(self, "_ui_tick"):
                self._ui_tick = 0
            self._ui_tick += 1

            running = bool(getattr(self, "tracker", None) and self.tracker.is_running())
            if hasattr(self, "label_antenna_status"):
                if running:
                    self.label_antenna_status.setText("Tracking")
                    self.label_antenna_status.setStyleSheet(green_label_color)
                else:
                    self.label_antenna_status.setText("Stopped")
                    self.label_antenna_status.setStyleSheet(red_label_color)

            # Télémétrie actuelle (avec fallback cache)
            az_cur = getattr(getattr(self.axis_client, "antenna", None), "az", None) if hasattr(self, "axis_client") else None
            el_cur = getattr(getattr(self.axis_client, "antenna", None), "el", None) if hasattr(self, "axis_client") else None
            if az_cur is None:
                az_cur = getattr(self, "_last_tel_az", None)
            if el_cur is None:
                el_cur = getattr(self, "_last_tel_el", None)

            # Setpoints calculés par le worker
            az_set = getattr(self.tracked_object, "az_set", None)
            el_set = getattr(self.tracked_object, "el_set", None)

            if running and isinstance(az_set, (int, float)) and isinstance(el_set, (int, float)):
                # Labels SET
                if hasattr(self, "label_antenna_az_set_deg"):
                    self.label_antenna_az_set_deg.setText(f"{az_set:.2f}°")
                if hasattr(self, "label_antenna_el_set_deg"):
                    self.label_antenna_el_set_deg.setText(f"{el_set:.2f}°")
                # Jauges : setpoint
                try:
                    self.g1.set_setpoint(az_set)
                    self.g2.set_setpoint(el_set)
                except Exception:
                    pass
                # Erreurs
                if isinstance(az_cur, (int, float)):
                    az_err = az_cur - az_set
                    self.tracked_object.az_error = az_err
                    if hasattr(self, "label_antenna_az_error"):
                        self.label_antenna_az_error.setText(f"{az_err:.2f} °")
                        thr = self.settings.get("ANTENNA", {}).get("az_error_threshold", 0.05)
                        color = green_label_color if abs(az_err) <= float(thr) else red_label_color
                        self.label_antenna_az_error.setStyleSheet(color)
                    self.g1.set_error(az_err)
                if isinstance(el_cur, (int, float)):
                    el_err = el_cur - el_set
                    self.tracked_object.el_error = el_err
                    if hasattr(self, "label_antenna_el_error"):
                        self.label_antenna_el_error.setText(f"{el_err:.2f} °")
                        thr = self.settings.get("ANTENNA", {}).get("el_error_threshold", 0.05)
                        color = green_label_color if abs(el_err) <= float(thr) else red_label_color
                        self.label_antenna_el_error.setStyleSheet(color)
                    self.g2.set_error(el_err)
            else:
                # Tracking OFF → pas de setpoint/erreur sur les jauges (labels Set laissés à ui_update_tracked_display)
                try:
                    self.g1.set_setpoint(None);
                    self.g2.set_setpoint(None)
                    self.g1.set_error(None);
                    self.g2.set_error(None)
                except Exception:
                    pass

        except Exception as e:
            self.logger.error(f"Erreur _update_tracking_ui: {e}")


    def on_connect_button_clicked(self):
        if self._connect_toggle_in_progress:
            return
        self._connect_toggle_in_progress = True
        try:
            if self.has_connection():
                self.request_disconnect()
            else:
                self.request_connect()
        except Exception as e:
            self.logger.error(f"Erreur toggle connect/disconnect: {e}")
        finally:
            self._connect_toggle_in_progress = False


    def on_apply_target_clicked(self):
        try:
            sel_type = (self.object_dropdown.currentText() or "").strip()
            if sel_type == "Artificial Satellite":
                sel_obj = self._current_sat_query()
                if not sel_obj:
                    QMessageBox.information(self, "Target Object", "Choisissez un satellite ou saisissez un Nom/NORAD.")
                    return
            elif sel_type == "Radio Source":
                sel_obj = self._current_rs_query()
                if not sel_obj:
                    QMessageBox.information(self, "Target Object", "Choisissez une source radio ou tapez son nom (ex: 3C 273).")
                    return
            else:
                sel_obj = self.specific_object_dropdown.currentText()
                if not sel_obj:
                    QMessageBox.information(self, "Target Object", "Veuillez sélectionner un objet.")
                    return

            self.ephem.start_object("primary", sel_type, sel_obj, interval=0.1)
            self.status_bar.showMessage(f"Consignes démarrées pour: {sel_type} / {sel_obj}", 3000)
            # Met à jour les graphes Calibration pour le passage courant/prochain
            try:
                self.refresh_calibration_plots(step_s=2.0)  # échantillonnage 2 s (ajuste selon besoin)
            except Exception as e:
                self.logger.error(f"refresh_calibration_plots failed: {e}")

        except Exception as e:
            self.logger.error(f"Erreur on_apply_target_clicked: {e}")
            QMessageBox.warning(self, "Target Object", f"Impossible de démarrer les consignes:\n{e}")

    def on_track_button_clicked(self):
        """
        Démarre/arrête la boucle de tracking moteurs (Tracker) et la MAJ UI associée.
        La condition d'amorçage est la présence de setpoints valides (az_set/el_set),
        pas l'existence d'un thread spécifique.
        """
        try:
            # Vérifier la connexion Axis
            if not self.has_connection():
                QMessageBox.warning(self, "Tracking", "Veuillez d'abord vous connecter au serveur Axis.")
                return

            # Initialiser le tracker si nécessaire
            if self.tracker is None:
                try:
                    self.tracker = Tracker(self.axis_client, self.settings, self.thread_manager, self.tracked_object)
                except Exception as e:
                    self.logger.error(f"Impossible d'initialiser le tracker: {e}")
                    QMessageBox.warning(self, "Tracking", "Initialisation du tracker impossible.")
                    return

            if not self.tracker.is_running():
                # Condition d'amorçage : setpoints valides (issus d'ephemeris)
                az_set = getattr(self.tracked_object, "az_set", None)
                el_set = getattr(self.tracked_object, "el_set", None)

                if not self.has_setpoints():
                    QMessageBox.information(
                        self,
                        "Tracking",
                        "Sélectionnez un objet puis cliquez sur 'Appliquer la sélection' pour calculer les consignes."
                    )
                    return

                # Diagnostics avant démarrage
                try:
                    sel_type = self.object_dropdown.currentText() if hasattr(self, "object_dropdown") else "?"
                    sel_obj = self.specific_object_dropdown.currentText() if hasattr(self, "specific_object_dropdown") else "?"
                    az_cur = getattr(getattr(self, "axis_client", None), "antenna", None)
                    az_cur = getattr(az_cur, "az", None)
                    el_cur = getattr(getattr(self.axis_client, "antenna", None), "el", None) if hasattr(self, "axis_client") else None
                    self.logger.info(f"[Tracking] Selection type='{sel_type}' object='{sel_obj}' | tel az={az_cur} el={el_cur} | set az={az_set} el={el_set}")
                except Exception:
                    pass

                # Purger un éventuel thread 'TrackingLoop' résiduel
                try:
                    self.thread_manager.stop_thread("TrackingLoop")
                except Exception:
                    pass

                # Forcer réapplication vitesses et lancer quand tout est prêt
                try:
                    if hasattr(self.tracker, "mark_speeds_dirty"):
                        self.tracker.mark_speeds_dirty()
                except Exception:
                    pass
                self._auto_restart_tracking = True
                self._start_tracker_when_ready(attempts_left=20)

            else:
                # STOP
                self._auto_restart_tracking = False
                try:
                    self.logger.info("[UI] STOP tracking (user action)")
                except Exception:
                    pass
                self.tracker.stop()
                self.stop_tracking_ui_timer()
                self._ui_show_tracking_stopped()
                try:
                    self.pushButton_antenna_track.setText("Track")
                except Exception:
                    pass

        except Exception as e:
            self.logger.error(f"Erreur on_track_button_clicked: {e}")


    def stop_polling_threads(self):
        """
        Arrête les threads de polling Axis et les workers d'éphémérides.
        """
        try:
            if hasattr(self, "thread_manager") and self.thread_manager:
                for name in ("AxisPositionPoller", "AxisStatusPoller"):
                    try:
                        self.thread_manager.stop_thread(name)
                    except Exception:
                        pass
            if hasattr(self, "ephem"):
                try:
                    self.ephem.stop_all()
                except Exception:
                    pass
        except Exception as e:
            self.logger.error(f"Erreur stop_polling_threads: {e}")

    def request_connect(self):
        """
        Démarre la connexion au serveur Axis dans un thread séparé
        """
        if not self.connection_ready:
            self.logger.info("Démarrage de la connexion au serveur Axis depuis un thread séparé")
            self._user_requested_disconnect = False

            # Créer le worker dans un thread séparé
            worker = self.thread_manager.start_thread(
                "AxisConnection",
                self.connect_to_axis_server,
                self.ip_address,
                self.port
            )

            # Connecter les signaux du worker
            worker.status.connect(lambda msg: self.status_bar.showMessage(msg))
            worker.error.connect(self.on_connection_error)
            worker.result.connect(self.on_connection_success)

            self.connection_ready = True

    def request_disconnect(self):
        self.logger.info("[UI] request_disconnect: begin")
        try:
            self._user_requested_disconnect = True
            self._auto_restart_tracking = False

            # UI immédiate pour voir qu’on est bien entré
            try:
                self.pushButton_server_connect.setEnabled(False)
                self.pushButton_server_connect.setText("DISCONNECTING…")
                self.status_bar.showMessage("Déconnexion…")
            except Exception:
                pass

            # Arrêter UI/trackers/pollers d’abord (côté UI = thread main)
            try:
                self.stop_tracking_ui_timer()
            except Exception:
                pass
            try:
                if getattr(self, "tracker", None):
                    self.tracker.stop()
            except Exception:
                pass
            self.stop_polling_threads()
            self.axis_polling = None

            # Couper toute remontée de signaux TOUT DE SUITE
            try:
                if getattr(self, "axis_client", None):
                    try:
                        self.axis_client.blockSignals(True)
                    except Exception:
                        pass
                    for sig, slot in (
                            ("connection_state_changed", self.on_axis_connection_state_changed),
                            ("connection_failed", self.on_axis_connection_failed),
                            ("antenna_telemetry_updated", self.ui_display_antenna_status),
                            ("antenna_telemetry_updated", self.on_antenna_telemetry_ready),
                            ("versions_updated", self.ui_display_versions),
                    ):
                        try:
                            if hasattr(self.axis_client, sig):
                                getattr(self.axis_client, sig).disconnect(slot)
                        except Exception:
                            pass
                    # Désactiver tout auto-reconnect coté client
                    try:
                        if hasattr(self.axis_client, "set_auto_reconnect"):
                            self.axis_client.set_auto_reconnect(False)
                        elif hasattr(self.axis_client, "auto_reconnect"):
                            self.axis_client.auto_reconnect = False
                    except Exception:
                        pass
            except Exception:
                pass

            # Débrancher le client AVANT d’éteindre la loop core
            if getattr(self, "axis_client", None):
                try:
                    self.axis_client.disconnect()
                except Exception as e:
                    self.logger.error(f"axis_client.disconnect error: {e}")
                finally:
                    # Laisser Qt nettoyer proprement
                    try:
                        self.axis_client.deleteLater()
                    except Exception:
                        pass
                    self.axis_client = None

            # Puis seulement maintenant arrêter watchdog/loop
            try:
                self.thread_manager.stop_thread("AxisConnWatchdog")
            except Exception:
                pass
            try:
                self.thread_manager.stop_asyncio_loop("AxisCoreLoop")
            except Exception:
                pass

            # État UI final
            self.ui_set_default_state()
            self.set_server_status("DISCONNECTED")
            self.pushButton_server_connect.setText("CONNECT")
            self.connection_ready = False
            self.telemetry_ready = False
            self.status_bar.showMessage("Déconnecté du serveur Axis")
        except Exception as e:
            self.logger.error(f"Erreur de déconnexion: {e}")
        finally:
            try:
                self.pushButton_server_connect.setEnabled(True)
            except Exception:
                pass
            self.logger.info("[UI] request_disconnect: end")


    def connect_to_axis_server(self, ip_address, port):
        """
        Fonction qui sera exécutée dans un thread séparé pour connecter au serveur Axis
        """
        try:
            self.logger.info(f"Tentative de connexion au serveur Axis {ip_address}:{port}")

            # Créer et configurer le client Axis

            axis_client = AxisClientQt(ip_address, port)
            # Donner accès au thread_manager
            axis_client.thread_manager = self.thread_manager

            # Utiliser connect() au lieu de connect_socket() pour initialiser le timer keep-alive
            connected = axis_client.connect()

            if connected:
                self.logger.info(f"Connexion établie avec le serveur Axis: {ip_address}:{port}")
                return axis_client
            else:
                raise ConnectionError(f"Impossible de se connecter au serveur {ip_address}:{port}")

        except Exception as e:
            self.logger.error(f"Erreur de connexion au serveur Axis: {e}")
            raise


    def on_connection_success(self, axis_client):
        """
        Appelé lorsque la connexion au serveur est établie avec succès
        """
        if getattr(self, "_user_requested_disconnect", False):
            self.logger.info("Connexion établie mais l’utilisateur a demandé la déconnexion → teardown immédiat.")
            try:
                axis_client.disconnect()
            except Exception:
                pass
            try:
                axis_client.deleteLater()
            except Exception:
                pass
            return
        self.axis_client = axis_client
        self.status_bar.showMessage("Connecté au serveur Axis")

        # Connecter les signaux du client
        # Nouveaux signaux de connexion
        if hasattr(self.axis_client, "connection_state_changed"):
            self.axis_client.connection_state_changed.connect(self.on_axis_connection_state_changed)
        if hasattr(self.axis_client, "connection_failed"):
            self.axis_client.connection_failed.connect(self.on_axis_connection_failed)
        if hasattr(self.axis_client, "antenna_telemetry_updated"):
            self.axis_client.antenna_telemetry_updated.connect(self.ui_display_antenna_status)
            # Basculer telemetry_ready à la réception de télémétrie complète
            try:
                self.axis_client.antenna_telemetry_updated.connect(self.on_antenna_telemetry_ready)
            except Exception:
                pass
        # Initialiser l'état telemetry_ready selon l'état courant (si déjà dispo)
        try:
            az0 = getattr(getattr(self.axis_client, "antenna", None), "az", None)
            el0 = getattr(getattr(self.axis_client, "antenna", None), "el", None)
            self.telemetry_ready = isinstance(az0, (int, float)) and isinstance(el0, (int, float))
        except Exception:
            self.telemetry_ready = False
        if hasattr(self.axis_client, "versions_updated"):
            self.axis_client.versions_updated.connect(self.ui_display_versions)
            # Demander une émission immédiate des versions après branchement du slot
            try:
                self.axis_client.emit_versions()
            except Exception as e:
                self.logger.error(f"Impossible de déclencher l'émission des versions: {e}")

        # UI: bouton et label statut
        self.pushButton_server_connect.setText("DISCONNECT")
        self.set_server_status("CONNECTED")
        # Réactiver les labels de données
        self.set_data_labels_enabled(True)

        # Démarrer le polling des données via l'adaptateur core
        self.start_polling()

        # Primer le contrôleur après reconnexion (STOP + vitesses)
        try:
            self.prime_axis_motion()
        except Exception as e:
            self.logger.error(f"prime_axis_motion après reconnexion a échoué: {e}")

        # Assainir un éventuel thread 'TrackingLoop' résiduel (déconnexion précédente)
        try:
            self.thread_manager.stop_thread("TrackingLoop")
        except Exception:
            pass

        # Préparer le tracker moteur
        try:
            self.tracker = Tracker(self.axis_client, self.settings, self.thread_manager, self.tracked_object)
            # Forcer la réapplication des vitesses au prochain cycle
            try:
                if hasattr(self.tracker, "mark_speeds_dirty"):
                    self.tracker.mark_speeds_dirty()
            except Exception:
                pass
            if hasattr(self, "pushButton_antenna_track"):
                self.pushButton_antenna_track.setEnabled(True)
                self.pushButton_antenna_track.setText("Track")

        except Exception as e:
            self.logger.error(f"Impossible d'initialiser le tracker: {e}")

        # Redémarrer automatiquement si c'était actif avant la coupure (et non stoppé par l'utilisateur)
        try:
            if self._auto_restart_tracking:
                self.logger.info("[Tracking] Auto-restart après reconnexion")
                self._start_tracker_when_ready(attempts_left=20)
        except Exception as e:
            self.logger.error(f"Auto-restart tracking error: {e}")


    def on_connection_error(self, error_message):
        """
        Appelé en cas d'erreur de connexion
        """
        self.logger.error(f"Erreur de connexion: {error_message}")
        self.status_bar.showMessage(f"Erreur: {error_message}")
        # Autoriser un nouvel essai en réarmant le flag
        self.connection_ready = False
        QMessageBox.critical(self, "Erreur de connexion",
                           f"Impossible de se connecter au serveur: {error_message}")

    def on_axis_connection_failed(self, message: str):
        """
        Déconnexion/échec signalé par AxisClientQt (p. ex. coupure serveur).
        """
        if getattr(self, "_user_requested_disconnect", False):
            # L’utilisateur a demandé la déconnexion: ne rien relancer.
            self.logger.info("Déconnexion demandée par l'utilisateur: aucune reconnexion automatique.")
            return

        try:
            self.logger.error(f"AxisClient: {message}")
            try:
                self.logger.info("[UI] STOP tracking (server disconnect/watchdog)")
            except Exception:
                pass
            self.telemetry_ready = False
            self.stop_polling_threads()
            self.axis_polling = None
            # Stopper aussi le tracking moteur et le timer UI
            try:
                if getattr(self, "tracker", None):
                    self.tracker.stop()
            except Exception:
                pass
            try:
                self.stop_tracking_ui_timer()
            except Exception:
                pass
            try:
                if hasattr(self, "pushButton_antenna_track"):
                    self.pushButton_antenna_track.setText("Track")
            except Exception:
                pass

            self.pushButton_server_connect.setText("CONNECT")
            self.set_server_status("DISCONNECTED")
            self.ui_set_default_state()
            self.connection_ready = False
            self.status_bar.showMessage("Connexion interrompue")
            QMessageBox.warning(self, "Connexion interrompue", message or "La connexion au serveur a été interrompue.")
        except Exception as e:
            self.logger.error(f"Erreur on_axis_connection_failed: {e}")


    def on_axis_connection_state_changed(self, state: str):
        """
        Met à jour l'UI selon l'état de connexion ('CONNECTED' / 'DISCONNECTED')
        """
        if getattr(self, "_user_requested_disconnect", False):
            return
        try:
            s = (state or "").upper()
            self.set_server_status(s)
            if s == "CONNECTED":
                self.pushButton_server_connect.setText("DISCONNECT")
                self.set_data_labels_enabled(True)
            else:
                self.pushButton_server_connect.setText("CONNECT")
                # Stopper le tracking moteur et le timer UI
                try:
                    self.logger.info("[UI] STOP tracking (connection state changed to DISCONNECTED)")
                except Exception:
                    pass
                try:
                    if getattr(self, "tracker", None):
                        self.tracker.stop()
                except Exception:
                    pass
                try:
                    self.stop_tracking_ui_timer()
                except Exception:
                    pass
                # Réinitialiser la télémétrie prête
                self.telemetry_ready = False
                # État par défaut lors du passage à DISCONNECTED
                self.ui_set_default_state()
        except Exception as e:
            self.logger.error(f"Erreur on_axis_connection_state_changed: {e}")


    def set_server_status(self, state: str):
        """
        Met à jour le label de statut serveur avec texte et style unifiés.
        """
        s = (state or "").upper()
        if s == "CONNECTED":
            self.label_antenna_server_status.setText("CONNECTED")
            self.label_antenna_server_status.setStyleSheet(green_label_color)
        elif s == "DISCONNECTED":
            self.label_antenna_server_status.setText("DISCONNECTED")
            self.label_antenna_server_status.setStyleSheet(red_label_color)
        else:
            self.label_antenna_server_status.setText(s or "UNKNOWN")
            self.label_antenna_server_status.setStyleSheet(standard_label_color)


    def set_data_labels_enabled(self, enabled: bool):
        """
        Enable/disable data labels (versions, position, rates, set rates, endstops).
        """
        try:
            for lbl in (
                self.label_axisapp_version,
                self.label_axisaz_version,
                self.label_axisel_version,
                self.label_antenna_az_deg,
                self.label_antenna_el_deg,
                self.label_antenna_az_rate,
                self.label_antenna_el_rate,
                self.label_antenna_az_setrate,
                self.label_antenna_el_setrate,
                self.label_antenna_endstop_az,
                self.label_antenna_endstop_el,
            ):
                lbl.setEnabled(enabled)
        except Exception as e:
            self.logger.error(f"Erreur set_data_labels_enabled: {e}")


    def start_polling(self):
        """
        Démarre les threads de polling via l'adaptateur core (AxisClientPollingAdapter)
        Toujours rebinder l'adaptateur sur l'instance Axis courante pour éviter tout 'stale client' après reconnexion.
        """
        try:
            if hasattr(self, "axis_polling") and self.axis_polling is not None:
                # Arrêter proprement l'ancien polling (et son client potentiellement déconnecté)
                try:
                    self.axis_polling.stop()
                except Exception:
                    pass
            # Recréer l'adaptateur avec l'instance Axis actuelle
            self.axis_polling = AxisClientPollingAdapter(self.axis_client, self.thread_manager)
            self.axis_polling.start(pos_interval=0.2, status_interval=1.0)
        except Exception as e:
            try:
                self.logger.error(f"Erreur start_polling: {e}")
            except Exception:
                pass


    def ui_set_default_state(self):
        """
        Apply the default UI state (disconnected).
        """
        try:
            # Server/app and driver versions
            self.label_axisapp_version.setText("")
            self.label_axisaz_version.setText("")
            self.label_axisel_version.setText("")
            # Antenna position
            self.label_antenna_az_deg.setText("---.--°")
            self.label_antenna_el_deg.setText("---.--°")
            self.label_antenna_az_rate.setText("0.00 °/s")
            self.label_antenna_el_rate.setText("0.00 °/s")
            self.label_antenna_az_setrate.setText("--")
            self.label_antenna_el_setrate.setText("--")
            self.label_antenna_endstop_az.setText("-")
            self.label_antenna_endstop_el.setText("-")

            self.g1.set_setpoint(None)
            self.g1.set_angle(None)
            self.g1.set_error(None)
            self.g2.set_setpoint(None)
            self.g2.set_angle(None)
            self.g2.set_error(None)

            # Réinitialiser aussi les labels du tab Target
            for attr in (
                    "target_ra_label", "target_dec_label", "target_dist_au_label",
                    "target_visible_now_label", "target_aos_label", "target_los_label",
                    "target_dur_label", "target_max_el_label", "target_max_el_time_label",
                    "target_el_now_label",
            ):
                try:
                    if hasattr(self, attr):
                        getattr(self, attr).setText("—")
                except Exception:
                    pass

            # Statut serveur: DISCONNECTED (rouge) au lancement
            self.set_server_status("DISCONNECTED")

            # Statut Tracking et erreurs en mode arrêté
            try:
                self.logger.info("[UI] Apply default STOP state (ui_set_default_state)")
            except Exception:
                pass
            self._ui_show_tracking_stopped()

            # Désactiver les éléments principaux
            self.set_data_labels_enabled(False)

            if hasattr(self, "pushButton_antenna_track"):
                self.pushButton_antenna_track.setEnabled(False)
                self.pushButton_antenna_track.setText("Track")

        except Exception as e:
            self.logger.error(f"Erreur ui_set_default_state: {e}")

    def ui_display_versions(self, versions: dict):
        """
        Met à jour les labels des versions serveur/drivers lors de la connexion.
        versions: {'server_version','driver_version_az','driver_version_el'}
        """
        try:
            if not isinstance(versions, dict):
                return
            self.label_axisapp_version.setText(str(versions.get('server_version') or ""))
            self.label_axisaz_version.setText(str(versions.get('driver_version_az') or ""))
            self.label_axisel_version.setText(str(versions.get('driver_version_el') or ""))
        except Exception as e:
            self.logger.error(f"Erreur ui_display_versions: {e}")


    def ui_display_antenna_status(self, data: dict):
        """
        Mise à jour unifiée (rapide) des labels antenne: az, el, rates (calculés), setrates, endstops.
        """
        try:
            if not isinstance(data, dict):
                return
            # Position
            az = data.get('az')
            el = data.get('el')
            self.label_antenna_az_deg.setText(f"{az:.2f}°" if isinstance(az, (int, float)) else "---.--°")
            self.g1.set_angle(az)
            self.label_antenna_el_deg.setText(f"{el:.2f}°" if isinstance(el, (int, float)) else "---.--°")
            self.g2.set_angle(el)
            # Rates
            az_rate = data.get('az_rate')
            el_rate = data.get('el_rate')
            self.label_antenna_az_rate.setText(f"{az_rate:.2f} °/s" if isinstance(az_rate, (int, float)) else "0.00 °/s")
            self.label_antenna_el_rate.setText(f"{el_rate:.2f} °/s" if isinstance(el_rate, (int, float)) else "0.00 °/s")

            # Setrates (afficher AZ/EL sur un seul label selon mapping fourni)
            self.label_antenna_az_setrate.setText(f"{data.get('az_setrate'):.0f}")
            self.label_antenna_el_setrate.setText(f"{data.get('el_setrate'):.0f}")

            # Endstops
            end_az = data.get('endstop_az')
            end_el = data.get('endstop_el')
            self.label_antenna_endstop_az.setText(str(end_az) if end_az is not None else "-")
            self.label_antenna_endstop_el.setText(str(end_el) if end_el is not None else "-")

            # Mettre à jour les caches télémétrie valides (pour l'affichage des erreurs au redémarrage)
            try:
                if isinstance(az, (int, float)):
                    self._last_tel_az = az
                if isinstance(el, (int, float)):
                    self._last_tel_el = el
            except Exception:
                pass

            # Statut des moteurs
            try:
                status = getattr(self.axis_client.axisClient, "axis_status", None) if hasattr(self, "axis_client") else None
                if isinstance(status, dict):
                    az_state = status.get('azimuth')
                    el_state = status.get('elevation')
                    az_text = None
                    el_text = None
                    if az_state is not None:
                        az_text = getattr(az_state, "display_name", None) or getattr(az_state, "name", str(az_state))
                        self.label_antenna_az_status.setText(az_text)
                        az_status_color = green_label_color if az_text != "STOP" else orange_label_color
                        self.label_antenna_az_status.setStyleSheet(az_status_color)
                    if el_state is not None:
                        el_text = getattr(el_state, "display_name", None) or getattr(el_state, "name", str(el_state))
                        self.label_antenna_el_status.setText(el_text)
                        el_status_color = green_label_color if el_text != "STOP" else orange_label_color
                        self.label_antenna_el_status.setStyleSheet(el_status_color)
                    # (log UI supprimé pour alléger la verbosité)
                    pass
            except Exception:
                pass
        except Exception as e:
            self.logger.error(f"Erreur ui_display_antenna_status: {e}")

    # multi track card
    def setup_multi_tracking_tab_in_tabwidget3(self):
        """
        Ajoute la barre MultiTrack dans tabWidget_3 → onglet 'Solar System'.
        Si l’onglet est absent, on log et on s’arrête silencieusement.
        """
        tw = getattr(self, "tabWidget_3", None)
        if tw is None:
            self.logger.warning("tabWidget_3 introuvable dans l'UI.")
            return

        # cherche la page dont le titre est 'Solar System' (insensible à la casse/espaces)
        page = None
        for i in range(tw.count()):
            if tw.tabText(i).strip().lower() == "solar system":
                page = tw.widget(i)
                break
        if page is None:
            self.logger.warning("Onglet 'Solar System' introuvable dans tabWidget_3.")
            return

        # récupère/pose un layout et insère notre strip en bas
        from PyQt5.QtWidgets import QVBoxLayout, QGridLayout
        lay = page.layout()
        if lay is None:
            lay = QVBoxLayout(page)
            page.setLayout(lay)

        self.multi_strip = MultiTrackStrip(self.ephem, on_pick=self._on_multitrack_pick, parent=page)

        # si c'est un GridLayout, on l'ajoute sur une nouvelle ligne qui span toutes les colonnes
        if isinstance(lay, QGridLayout):
            row = lay.rowCount()
            col_span = max(1, lay.columnCount())
            lay.addWidget(self.multi_strip, row, 0, 1, col_span)
        else:
            lay.addWidget(self.multi_strip)

        # cartes par défaut
        # self.multi_strip.add_target("Solar System", "Sun")
        # self.multi_strip.add_target("Solar System", "Moon")
        # self.multi_strip.add_target("Artificial Satellite", "ISS")
        # self.multi_strip.add_target("Artificial Satellite", "NOAA 20")
        # tu peux en ajouter d'autres :
        # self.multi_strip.add_target("Solar System", "Jupiter")

    def _on_multitrack_pick(self, obj_type: str, name: str):
        """
        Cliquer une carte = sélectionner l’objet comme dans l’onglet Target + lancer 'Appliquer la sélection'.
        """
        try:
            if hasattr(self, "object_dropdown"):
                self.object_dropdown.setCurrentText(obj_type)
                # met à jour la liste secondaire
                self.update_secondary_dropdown()

            if hasattr(self, "specific_object_dropdown"):
                idx = -1
                for i in range(self.specific_object_dropdown.count()):
                    if self.specific_object_dropdown.itemText(i).lower() == name.lower():
                        idx = i;
                        break
                if idx >= 0:
                    self.specific_object_dropdown.setCurrentIndex(idx)
                else:
                    self.specific_object_dropdown.addItem(name)
                    self.specific_object_dropdown.setCurrentText(name)

            # exactement le même effet que ton bouton "Appliquer la sélection"
            self.on_apply_target_clicked()
            self.status_bar.showMessage(f"Objet sélectionné: {obj_type} / {name}", 3000)
        except Exception as e:
            self.logger.error(f"_on_multitrack_pick error: {e}")


    def _on_multi_pick(self, obj_type: str, name: str):
        try:
            self.object_dropdown.setCurrentText(obj_type)
            self.update_secondary_dropdown()
            for i in range(self.specific_object_dropdown.count()):
                if self.specific_object_dropdown.itemText(i).lower() == name.lower():
                    self.specific_object_dropdown.setCurrentIndex(i)
                    break
            self.on_apply_target_clicked()
        except Exception as e:
            self.logger.error(f"_on_multi_pick error: {e}")


    def closeEvent(self, e):
        try:
            if hasattr(self, "multi_cards"):
                self.multi_cards.stop_all()
        except Exception:
            pass
        super().closeEvent(e)

    def _parse_sat_label(self, label: str) -> str:
        """
        Extrait le nom de satellite depuis un label comme 'ISS [25544]'.
        Si le label est vide ou un placeholder '(vide)/(erreur)', retourne ''.
        """
        if not label:
            return ""
        lab = label.strip()
        if lab in ("(vide)", "(erreur)", "(aucun groupe)"):
            return ""
        # retire " [12345]" final s'il existe
        # ex: "NOAA 19 [33591]" -> "NOAA 19"
        p = lab.rfind('[')
        if p > 0 and lab.endswith(']'):
            return lab[:p].strip()
        return lab

    def _current_sat_query(self) -> str:
        """
        Récupère la requête satellite à envoyer à Ephemeris:
        - priorité au champ libre (peut être un NORAD ou un nom exact)
        - sinon, nom extrait du dropdown
        """
        q = (self.tle_query_edit.text() if hasattr(self, "tle_query_edit") else "") or ""
        q = q.strip()
        if q:
            return q
        label = (self.tle_sat_dropdown.currentText() if hasattr(self, "tle_sat_dropdown") else "") or ""
        return self._parse_sat_label(label)

### POWERMETER
    def _on_powermeter_value(self, val_dbm: float):
        # TODO: envoyez la mesure vers votre séquenceur, UI, logs, etc.
        try:
            self.status_bar.showMessage(f"Powermeter: {val_dbm:.2f} dBm", 3000)
            if hasattr(self, "label_powermeter_dbm"):
                self.label_powermeter_dbm.setText(f"{val_dbm:.2f} dBm")
        except Exception:
            pass

    def start_powermeter_read(self):
        """
        Démarre une mesure powermeter en thread séparé.
        Vous pouvez l'appeler depuis un séquenceur ou un bouton.
        """
        worker = self.thread_manager.start_thread(
            "PowermeterRead",
            self.powermeter.read_power
        )
        # Récupérer le résultat directement via Worker.result si vous préférez (en plus des signaux Qt)
        worker.result.connect(self._on_powermeter_value)  # float
        worker.error.connect(lambda m: self.logger.error(f"[PowermeterRead] {m}"))
        worker.status.connect(lambda s: self.logger.info(f"[PowermeterRead] {s}"))

    def _setup_calibration_tab(self):
        """
        Insère le widget de plots dans l'onglet 'Calibration' et lui fait prendre tout l'espace.
        Fonctionne même si l'objet ne s'appelle pas 'tab_Calibration' dans le .ui.
        """
        from PyQt5.QtWidgets import QWidget, QVBoxLayout, QGridLayout, QSizePolicy

        tw = getattr(self, "tabWidget_3", None)
        if tw is None:
            self.logger.warning("tabWidget_3 introuvable dans l'UI.")
            return

        # 1) Essayer par objectName fourni par le .ui (optionnel)
        target_container = getattr(self, "tab_Calibration", None)

        # 2) Sinon, chercher par titre d’onglet existant
        if target_container is None:
            target_container, idx = self._find_tab_by_title(tw, "Calibration")
        else:
            idx = tw.indexOf(target_container)

        # 3) Sinon, créer un nouvel onglet "Calibration"
        created = False
        if target_container is None:
            target_container = QWidget()
            target_container.setObjectName("tab_Calibration")
            tw.addTab(target_container, "Calibration")
            idx = tw.indexOf(target_container)
            created = True

        # 4) S’assurer d’un layout
        lay = target_container.layout()
        if lay is None:
            lay = QVBoxLayout(target_container)
            lay.setContentsMargins(6, 6, 6, 6)
            lay.setSpacing(6)
            target_container.setLayout(lay)

        # 5) Créer le widget de plots s’il n’existe pas encore
        if not hasattr(self, "calib_plots") or self.calib_plots is None:
            self.calib_plots = CalibrationPlots(target_container)
            self.calib_plots.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

            if isinstance(lay, QGridLayout):
                row = lay.rowCount()
                col_span = max(1, lay.columnCount())
                lay.addWidget(self.calib_plots, row, 0, 1, col_span)
                lay.setRowStretch(row, 1)
                for c in range(col_span or 1):
                    lay.setColumnStretch(c, 1)
            else:
                # QVBoxLayout / QHBoxLayout
                try:
                    lay.addWidget(self.calib_plots, 1)  # stretch
                except TypeError:
                    lay.addWidget(self.calib_plots)

        # Petit log utile
        try:
            self.logger.info(f"Calibration tab ready (created={created}, index={idx})")
        except Exception:
            pass

    def refresh_calibration_plots(self, step_s: float = 2.0):
        """
        Construit la trace AOS→LOS pour la clé 'primary' et alimente les graphes.
        Exécuté sur clic 'Appliquer la sélection', et réutilisable ailleurs.
        """
        try:
            # 1) Service dispo ?
            if not getattr(self, "ephem", None):
                if hasattr(self, "logger"):
                    self.logger.warning("refresh_calibration_plots: ephem indisponible → abort")
                return

            # 2) Widget de plots prêt ? sinon on le crée ici
            if not getattr(self, "calib_plots", None):
                if hasattr(self, "logger"):
                    self.logger.debug("refresh_calibration_plots: calib_plots manquant → création…")
                try:
                    self._setup_calibration_tab()
                except Exception as e:
                    if hasattr(self, "logger"):
                        self.logger.error(f"_setup_calibration_tab a échoué: {e}")

            if not getattr(self, "calib_plots", None):
                if hasattr(self, "logger"):
                    self.logger.error("refresh_calibration_plots: calib_plots toujours None après setup")
                return

            # 3) Construire le track
            if hasattr(self.ephem, "build_pass_track_for_key"):
                if hasattr(self, "logger"):
                    self.logger.info("refresh_calibration_plots: construction du pass track…")
                track = self.ephem.build_pass_track_for_key("primary", step_s=step_s)
            else:
                # Défense si la méthode n'existe pas dans ta version d'EphemerisService
                raise AttributeError("EphemerisService.build_pass_track_for_key est introuvable")

            # 4) Normaliser les clés attendues par le widget
            if track and "az" not in track and "az_deg" in track:
                track = {**track, "az": track.get("az_deg"), "el": track.get("el_deg")}

            # 5) Vérifications et affichage
            el_series = (track or {}).get("el") or []
            if not track or len(el_series) < 2:
                self.calib_plots.clear()
                try:
                    self.status_bar.showMessage("Calibration: no pass to plot", 3000)
                except Exception:
                    pass
                return

            self.calib_plots.update_from_track(track)
            try:
                self.status_bar.showMessage("Calibration: pass track updated", 3000)
            except Exception:
                pass

        except Exception as e:
            if hasattr(self, "logger"):
                self.logger.error(f"Calibration plots: {e}")
            try:
                if getattr(self, "calib_plots", None):
                    self.calib_plots.clear()
            except Exception:
                pass
            try:
                self.status_bar.showMessage("Calibration: error", 3000)
            except Exception:
                pass

        # --- Variante threadée (si nécessaire pour Spacecraft lourds) ---
        # def _work():
        #     return self.ephem.build_pass_track_for_key("primary", step_s=step_s)
        # def _on_result(track):
        #     try:
        #         self.calib_plots.update_from_track(track)
        #         self.status_bar.showMessage("Calibration: pass track updated", 3000)
        #     except Exception as e2:
        #         self.logger.error(f"Calibration plots (result): {e2}")
        #         self.calib_plots.clear()
        # w = self.thread_manager.start_thread("CalibPassTrack", _work)
        # w.result.connect(_on_result)



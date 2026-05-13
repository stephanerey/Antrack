"""Calibration and pass-preview UI orchestration helpers."""

from __future__ import annotations

from PyQt5.QtWidgets import QGridLayout, QSizePolicy, QVBoxLayout, QWidget

from antrack.gui.widgets.calibration import CalibrationPlots


class CalibrationUiMixin:
    """Keep calibration tab setup out of the main composition root."""

    _CALIBRATION_REFRESH_THREAD = "CalibrationPassTrack"

    def _find_tab_by_title(self, tab_widget, title: str):
        wanted = (title or "").strip().lower()
        for index in range(tab_widget.count()):
            if tab_widget.tabText(index).strip().lower() == wanted:
                return tab_widget.widget(index), index
        return None, -1

    def _setup_calibration_tab(self):
        """
        Insert the plot widget in the calibration tab and make it fill the space.
        Works even if the object is not named 'tab_Calibration' in the .ui file.
        """
        tw = getattr(self, "tabWidget_3", None)
        if tw is None:
            self.logger.warning("tabWidget_3 introuvable dans l'UI.")
            return

        target_container = getattr(self, "tab_Calibration", None)
        if target_container is None:
            target_container, index = self._find_tab_by_title(tw, "Calibration")
        else:
            index = tw.indexOf(target_container)

        created = False
        if target_container is None:
            target_container = QWidget()
            target_container.setObjectName("tab_Calibration")
            tw.addTab(target_container, "Calibration")
            index = tw.indexOf(target_container)
            created = True

        layout = target_container.layout()
        if layout is None:
            layout = QVBoxLayout(target_container)
            layout.setContentsMargins(6, 6, 6, 6)
            layout.setSpacing(6)
            target_container.setLayout(layout)

        if not hasattr(self, "calib_plots") or self.calib_plots is None:
            self.calib_plots = CalibrationPlots(target_container)
            self.calib_plots.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

            if isinstance(layout, QGridLayout):
                row = layout.rowCount()
                col_span = max(1, layout.columnCount())
                layout.addWidget(self.calib_plots, row, 0, 1, col_span)
                layout.setRowStretch(row, 1)
                for column in range(col_span or 1):
                    layout.setColumnStretch(column, 1)
            else:
                try:
                    layout.addWidget(self.calib_plots, 1)
                except TypeError:
                    layout.addWidget(self.calib_plots)

        try:
            self.logger.info(f"Calibration tab ready (created={created}, index={index})")
        except Exception:
            pass

    def refresh_calibration_plots(self, step_s: float = 2.0):
        """
        Build the AOS->LOS track for the 'primary' key and feed the plots.
        """
        try:
            track = self._build_calibration_track(step_s=step_s)
            self._apply_calibration_track(track)
        except Exception as exc:
            self.logger.error(f"Erreur refresh_calibration_plots: {exc}")

    def request_calibration_plot_refresh(self, step_s: float = 2.0):
        """Build the pass track in a worker thread, then update the plots on the UI thread."""
        try:
            if not getattr(self, "ephem", None):
                if hasattr(self, "logger"):
                    self.logger.warning("request_calibration_plot_refresh: ephem indisponible -> abort")
                return

            if not getattr(self, "thread_manager", None):
                self.refresh_calibration_plots(step_s=step_s)
                return

            if hasattr(self, "logger"):
                self.logger.info("request_calibration_plot_refresh: scheduling pass track build...")
            try:
                self.status_bar.showMessage("Calibration: construction de la trace...", 2000)
            except Exception:
                pass

            worker = self.thread_manager.start_thread(
                self._CALIBRATION_REFRESH_THREAD,
                self._build_calibration_track,
                float(step_s),
            )
            try:
                worker.result.connect(self._apply_calibration_track)
            except Exception:
                pass
            try:
                worker.error.connect(self._on_calibration_track_error)
            except Exception:
                pass
        except Exception as exc:
            self.logger.error(f"Erreur request_calibration_plot_refresh: {exc}")

    def _build_calibration_track(self, step_s: float = 2.0):
        if not getattr(self, "ephem", None):
            raise RuntimeError("Service d'ephemerides indisponible")
        if not hasattr(self.ephem, "build_pass_track_for_key"):
            raise AttributeError("EphemerisService.build_pass_track_for_key est introuvable")
        if hasattr(self, "logger"):
            self.logger.info("refresh_calibration_plots: construction du pass track...")
        track = self.ephem.build_pass_track_for_key("primary", step_s=step_s)
        if track and "az" not in track and "az_deg" in track:
            track = {**track, "az": track.get("az_deg"), "el": track.get("el_deg")}
        return track

    def _ensure_calibration_plots_ready(self) -> bool:
        if getattr(self, "calib_plots", None):
            return True
        if hasattr(self, "logger"):
            self.logger.debug("_ensure_calibration_plots_ready: calib_plots manquant -> creation...")
        try:
            self._setup_calibration_tab()
        except Exception as exc:
            if hasattr(self, "logger"):
                self.logger.error(f"_setup_calibration_tab a echoue: {exc}")
        if not getattr(self, "calib_plots", None):
            if hasattr(self, "logger"):
                self.logger.error("_ensure_calibration_plots_ready: calib_plots toujours None apres setup")
            return False
        return True

    def _apply_calibration_track(self, track):
        try:
            if not self._ensure_calibration_plots_ready():
                return

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
        except Exception as exc:
            self.logger.error(f"Erreur _apply_calibration_track: {exc}")

    def _on_calibration_track_error(self, message: str):
        try:
            self.logger.error(f"Calibration track build failed: {message}")
            try:
                self.status_bar.showMessage(f"Calibration: echec de construction ({message})", 4000)
            except Exception:
                pass
        except Exception:
            pass

# src/tracking/tracking.py
# Tracking components: tracked target (TrackedObject) and tracking controller (Tracker)

from dataclasses import dataclass
from typing import Optional, Tuple
import math
import time
import logging
# Reduce Tracker logger verbosity globally (keep WARNING and above)
logging.getLogger("Tracker").setLevel(logging.WARNING)

from antrack.core.axis_client import AxisStatus


# --- Utilitaires simples ---
def convert_float_to_hms(decimal_hours: float) -> Tuple[int, int, float]:
    """Convert decimal hours to (hours, minutes, seconds)."""
    if decimal_hours is None or not isinstance(decimal_hours, (int, float)):
        return 0, 0, 0.0
    h = int(decimal_hours)
    m_float = abs(decimal_hours - h) * 60.0
    m = int(m_float)
    s = (m_float - m) * 60.0
    return h, m, s


def decimal_degrees_to_dms(decimal_degrees: float) -> Tuple[int, int, float]:
    """Convert decimal degrees to (degrees, minutes, seconds)."""
    if decimal_degrees is None or not isinstance(decimal_degrees, (int, float)):
        return 0, 0, 0.0
    sign = -1 if decimal_degrees < 0 else 1
    dd = abs(decimal_degrees)
    d = int(dd)
    m_float = (dd - d) * 60.0
    m = int(m_float)
    s = (m_float - m) * 60.0
    return sign * d, m, s


# --- Minimal RA/DEC structures ---
@dataclass
class Ra:
    decimal_hours: float = 0.0
    h: int = 0
    m: int = 0
    s: float = 0.0


@dataclass
class Dec:
    decimal_degrees: float = 0.0
    d: int = 0
    m: int = 0
    s: float = 0.0


# --- Tracked target ---
class TrackedObject:
    def __init__(self):
        self.az_set: float = 0.0
        self.el_set: float = 0.0
        self.az_error: float = 0.0
        self.el_error: float = 0.0
        self.distance_km: float = 0.0
        self.ra_set: Ra = Ra()
        self.dec_set: Dec = Dec()
        self.ra_error: Ra = Ra()
        self.dec_error: Dec = Dec()
        self.distance_au: float = 0.0


# --- Tracking controller ---
class Tracker:
    """
    Non-blocking tracking loop executed in a QThread (via ThreadManager).
    Interacts with the Axis core using AxisClientQt.thread_manager.run_coro(...).
    """
    def __init__(self, axis_client_qt, settings, thread_manager, tracked_object: Optional[TrackedObject] = None):
        self.axis_client_qt = axis_client_qt
        self.settings = settings or {}
        self.thread_manager = thread_manager
        self.tracked_object = tracked_object or TrackedObject()
        self._thread_name = "TrackingLoop"
        # Force speed re-application on next cycle (useful after reconnection)
        self._must_apply_speeds = True
        # Execute a STOP sequence + re-apply speeds on the first effective cycle
        self._kickstart_pending = True

        # Remember last motion command (to issue STOP before reversing direction)
        self._last_az_cmd = "STOP"
        self._last_el_cmd = "STOP"

        # Throttling: re-emit a motion command at most every X seconds if direction is unchanged
        self._last_az_cmd_ts = 0.0
        self._last_el_cmd_ts = 0.0
        self._move_refresh_interval = 1.0  # seconds

        # Diagnostics: remember telemetry/setpoints state transitions
        self._last_tel_ok = None
        self._last_set_ok = None

    def mark_speeds_dirty(self):
        """Request re-applying AZ/EL speeds on the next cycle."""
        self._must_apply_speeds = True
        self._kickstart_pending = True

    def is_running(self) -> bool:
        """
        Return True only if the worker exists, the QThread is running, and the worker is not aborted.
        """
        if not self.thread_manager:
            return False
        w = self.thread_manager.get_worker(self._thread_name)
        t = getattr(self.thread_manager, "threads", {}).get(self._thread_name)
        return bool(w and t and getattr(t, "isRunning", lambda: False)() and not getattr(w, "abort", False))

    def start(self):
        """Start the tracking loop in a QThread (idempotent and robust to stale workers)."""
        if not self.thread_manager:
            return
        # Purge a stale worker (aborted or QThread not running)
        w = self.thread_manager.get_worker(self._thread_name)
        t = getattr(self.thread_manager, "threads", {}).get(self._thread_name)
        if w and (getattr(w, "abort", False) or not (t and getattr(t, "isRunning", lambda: False)())):
            try:
                self.thread_manager.stop_thread(self._thread_name)
            except Exception:
                pass
        if self.is_running():
            return
        self.thread_manager.start_thread(self._thread_name, self._loop)

    def stop(self):
        """Stop the tracking loop and stop the motors."""
        try:
            self.thread_manager.stop_thread(self._thread_name)
        finally:
            # Stop motors via the core
            try:
                self._stop_motors()
            except Exception:
                pass

    # --- Implémentation interne de la boucle ---
    def _loop(self, interval: Optional[float] = None):
        """
        Boucle principale: calcule l'erreur AZ/EL, ajuste la vitesse et commande les mouvements.
        Exécutée dans un QThread (Worker.abort coopératif).
        """
        worker = self.thread_manager.get_worker(self._thread_name)
        try:
            # Section ANTENNA (fallback en lowercase si besoin)
            ant = {}
            if isinstance(self.settings, dict):
                ant = self.settings.get('ANTENNA', self.settings.get('antenna', {}))

            az_err_th = float(ant.get('az_error_threshold', 0.05))
            el_err_th = float(ant.get('el_error_threshold', 0.05))
            approach_deg = float(ant.get('approach_tracking_degrees', 5))
            close_deg = float(ant.get('close_tracking_degrees', 1))
            min_move_duration = float(ant.get('min_move_duration', 0.1))

            az_speed_far = float(ant.get('az_speed_far_tracking', 500))
            az_speed_approach = float(ant.get('az_speed_approach_tracking', 100))
            az_speed_close = float(ant.get('az_speed_close_tracking', 20))

            el_speed_far = float(ant.get('el_speed_far_tracking', 500))
            el_speed_approach = float(ant.get('el_speed_approach_tracking', 100))
            el_speed_close = float(ant.get('el_speed_close_tracking', 20))

            interval = float(interval or min_move_duration)
            log = logging.getLogger("Tracker")
            hb_last = time.monotonic()
            hb_ticks = 0
            # Compteurs diagnostics
            none_tel_streak = 0
            none_set_streak = 0
            tel_state_prev = None
            set_state_prev = None

            while worker and not worker.abort:
                # Lire la position actuelle depuis AxisClientQt.antenna
                az_cur = getattr(getattr(self.axis_client_qt, 'antenna', None), 'az', None)
                el_cur = getattr(getattr(self.axis_client_qt, 'antenna', None), 'el', None)

                # Laisser les pollers alimenter la télémétrie après reconnexion
                # (ne pas doubler les requêtes avec un get_position ici)

                # Exiger des consignes valides (définies par l’onglet cible)
                if self.tracked_object.az_set is None or self.tracked_object.el_set is None:
                    # Pas encore de cible, attendre
                    none_set_streak += 1
                    cur_set_state = False
                    if set_state_prev is None or set_state_prev != cur_set_state:
                        log.info("SET_STATE change -> set_ok=False (az_set=%s, el_set=%s)", self.tracked_object.az_set, self.tracked_object.el_set)
                        set_state_prev = cur_set_state
                    if none_set_streak % 10 == 1:
                        log.info("Tracker: setpoints missing (streak=%d) az_set=%s el_set=%s", none_set_streak, self.tracked_object.az_set, self.tracked_object.el_set)
                    time.sleep(interval)
                    worker = self.thread_manager.get_worker(self._thread_name)
                    continue
                else:
                    if set_state_prev is None or set_state_prev is False:
                        log.info("SET_STATE change -> set_ok=True (az_set=%.3f, el_set=%.3f)", self.tracked_object.az_set, self.tracked_object.el_set)
                        set_state_prev = True
                    none_set_streak = 0

                # Si pas de télémétrie, temporiser
                if az_cur is None or el_cur is None:
                    none_tel_streak += 1
                    cur_tel_state = False
                    if tel_state_prev is None or tel_state_prev != cur_tel_state:
                        log.info("TEL_STATE change -> tel_ok=False (az_cur=%s, el_cur=%s)", az_cur, el_cur)
                        tel_state_prev = cur_tel_state
                    if none_tel_streak % 10 == 1:
                        log.info("Tracker: telemetry missing (streak=%d) az_cur=%s el_cur=%s", none_tel_streak, az_cur, el_cur)
                    time.sleep(interval)
                    worker = self.thread_manager.get_worker(self._thread_name)
                    continue
                else:
                    if tel_state_prev is None or tel_state_prev is False:
                        log.info("TEL_STATE change -> tel_ok=True (az_cur=%.3f, el_cur=%.3f)", az_cur, el_cur)
                        tel_state_prev = True
                    none_tel_streak = 0

                # Calcul des erreurs
                self.tracked_object.az_error = (az_cur - self.tracked_object.az_set) if self.tracked_object.az_set is not None else 0.0
                self.tracked_object.el_error = (el_cur - self.tracked_object.el_set) if self.tracked_object.el_set is not None else 0.0

                # RA/DEC (si disponibles via un état antenne/astro ailleurs)
                try:
                    ant_state = getattr(self.axis_client_qt, 'antenna', None)
                    ra_cur = getattr(getattr(ant_state, 'ra', None), 'decimal_hours', None)
                    dec_cur = getattr(getattr(ant_state, 'dec', None), 'decimal_degrees', None)
                    if ra_cur is not None and hasattr(self.tracked_object.ra_set, 'decimal_hours'):
                        self.tracked_object.ra_error.decimal_hours = ra_cur - self.tracked_object.ra_set.decimal_hours
                        self.tracked_object.ra_error.h, self.tracked_object.ra_error.m, self.tracked_object.ra_error.s = convert_float_to_hms(self.tracked_object.ra_error.decimal_hours)
                    if dec_cur is not None and hasattr(self.tracked_object.dec_set, 'decimal_degrees'):
                        self.tracked_object.dec_error.decimal_degrees = dec_cur - self.tracked_object.dec_set.decimal_degrees
                        self.tracked_object.dec_error.d, self.tracked_object.dec_error.m, self.tracked_object.dec_error.s = decimal_degrees_to_dms(self.tracked_object.dec_error.decimal_degrees)
                except Exception:
                    pass

                # Seuils de correction
                need_az = abs(self.tracked_object.az_error) > az_err_th
                need_el = abs(self.tracked_object.el_error) > el_err_th

                # Préparer les décisions pour le log diagnostic (1/s)
                desired_az = "STOP"
                if need_az:
                    desired_az = "CCW" if self.tracked_object.az_error > 0 else "CW"
                desired_el = "STOP"
                if need_el:
                    desired_el = "DOWN" if self.tracked_object.el_error > 0 else "UP"
                # Log de décision synthétique
                try:
                    # décision: logs verbeux supprimés
                    pass
                except Exception:
                    pass

                # Ajuster vitesses + mouvements via le core (Axis) en utilisant l'event loop asyncio
                if need_az or need_el:
                    # AZ speed
                    try:
                        if abs(self.tracked_object.az_error) > approach_deg:
                            rate_az = az_speed_far
                        elif abs(self.tracked_object.az_error) > close_deg:
                            rate_az = az_speed_approach
                        else:
                            rate_az = az_speed_close
                        if getattr(self.axis_client_qt.antenna, 'az_setrate', None) != rate_az:
                            log.debug("CMD set_az_speed -> %.1f", rate_az)
                            self.axis_client_qt.antenna.az_setrate = rate_az
                            self.thread_manager.run_coro("AxisCoreLoop", lambda: self.axis_client_qt.axisClient.set_az_speed(rate_az), timeout=1.0)
                    except Exception as e:
                        log.info("CMD set_az_speed error: %s", e)

                    # EL speed
                    try:
                        if abs(self.tracked_object.el_error) > approach_deg:
                            rate_el = el_speed_far
                        elif abs(self.tracked_object.el_error) > close_deg:
                            rate_el = el_speed_approach
                        else:
                            rate_el = el_speed_close
                        if getattr(self.axis_client_qt.antenna, 'el_setrate', None) != rate_el:
                            self.axis_client_qt.antenna.el_setrate = rate_el
                            self.thread_manager.run_coro("AxisCoreLoop", lambda: self.axis_client_qt.axisClient.set_el_speed(rate_el), timeout=1.0)
                    except Exception:
                        pass

                    # AZ move (throttling)
                    try:
                        now_ts = time.monotonic()
                        if need_az:
                            if self.tracked_object.az_error > 0:
                                # CCW
                                if self._last_az_cmd != "CCW" or (now_ts - self._last_az_cmd_ts) >= self._move_refresh_interval:
                                    self.thread_manager.run_coro("AxisCoreLoop", self.axis_client_qt.axisClient.move_ccw, timeout=1.0)
                                    self.axis_client_qt.axisClient.axis_status['azimuth'] = AxisStatus.MOTION_AZ_CCW
                                    self._last_az_cmd = "CCW"
                                    self._last_az_cmd_ts = now_ts
                            else:
                                # CW
                                if self._last_az_cmd != "CW" or (now_ts - self._last_az_cmd_ts) >= self._move_refresh_interval:
                                    self.thread_manager.run_coro("AxisCoreLoop", self.axis_client_qt.axisClient.move_cw, timeout=1.0)
                                    self.axis_client_qt.axisClient.axis_status['azimuth'] = AxisStatus.MOTION_AZ_CW
                                    self._last_az_cmd = "CW"
                                    self._last_az_cmd_ts = now_ts
                        else:
                            if self._last_az_cmd != "STOP" or (now_ts - self._last_az_cmd_ts) >= self._move_refresh_interval:
                                self.thread_manager.run_coro("AxisCoreLoop", self.axis_client_qt.axisClient.stop_az, timeout=1.0)
                                self.axis_client_qt.axisClient.axis_status['azimuth'] = AxisStatus.MOTION_AZ_STOP
                                self._last_az_cmd = "STOP"
                                self._last_az_cmd_ts = now_ts
                    except Exception:
                        pass

                    # EL move (throttling)
                    try:
                        now_ts = time.monotonic()
                        if need_el:
                            if self.tracked_object.el_error > 0:
                                # DOWN
                                if self._last_el_cmd != "DOWN" or (now_ts - self._last_el_cmd_ts) >= self._move_refresh_interval:
                                    self.thread_manager.run_coro("AxisCoreLoop", self.axis_client_qt.axisClient.move_down, timeout=1.0)
                                    self.axis_client_qt.axisClient.axis_status['elevation'] = AxisStatus.MOTION_EL_DOWN
                                    self._last_el_cmd = "DOWN"
                                    self._last_el_cmd_ts = now_ts
                            else:
                                # UP
                                if self._last_el_cmd != "UP" or (now_ts - self._last_el_cmd_ts) >= self._move_refresh_interval:
                                    self.thread_manager.run_coro("AxisCoreLoop", self.axis_client_qt.axisClient.move_up, timeout=1.0)
                                    self.axis_client_qt.axisClient.axis_status['elevation'] = AxisStatus.MOTION_EL_UP
                                    self._last_el_cmd = "UP"
                                    self._last_el_cmd_ts = now_ts
                        else:
                            if self._last_el_cmd != "STOP" or (now_ts - self._last_el_cmd_ts) >= self._move_refresh_interval:
                                self.thread_manager.run_coro("AxisCoreLoop", self.axis_client_qt.axisClient.stop_el, timeout=1.0)
                                self.axis_client_qt.axisClient.axis_status['elevation'] = AxisStatus.MOTION_EL_STOP
                                self._last_el_cmd = "STOP"
                                self._last_el_cmd_ts = now_ts
                    except Exception:
                        pass
                else:
                    # Arrêt si dans la tolérance
                    try:
                        self._stop_motors()
                    except Exception:
                        pass

                # Heartbeat 1/s
                hb_ticks += 1
                now = time.monotonic()
                try:
                    tel_ok = isinstance(az_cur, (int, float)) and isinstance(el_cur, (int, float))
                    set_ok = isinstance(self.tracked_object.az_set, (int, float)) and isinstance(self.tracked_object.el_set, (int, float))

                    # Log des transitions tel/set (une seule fois par changement)
                    if self._last_tel_ok is None or self._last_tel_ok != tel_ok:
                        log.info(f"TEL_STATE change -> tel_ok={tel_ok} (az_cur={az_cur}, el_cur={el_cur})")
                        self._last_tel_ok = tel_ok
                    if self._last_set_ok is None or self._last_set_ok != set_ok:
                        log.info(f"SET_STATE change -> set_ok={set_ok} (az_set={self.tracked_object.az_set}, el_set={self.tracked_object.el_set})")
                        self._last_set_ok = set_ok

                    if now - hb_last >= 1.0:
                        # Infos statut/fin de course/vitesses
                        end_az = getattr(getattr(self.axis_client_qt, 'antenna', None), 'endstop_az', None)
                        end_el = getattr(getattr(self.axis_client_qt, 'antenna', None), 'endstop_el', None)
                        az_setrate = getattr(getattr(self.axis_client_qt, 'antenna', None), 'az_setrate', None)
                        el_setrate = getattr(getattr(self.axis_client_qt, 'antenna', None), 'el_setrate', None)
                        az_rate = getattr(getattr(self.axis_client_qt, 'antenna', None), 'az_rate', None)
                        el_rate = getattr(getattr(self.axis_client_qt, 'antenna', None), 'el_rate', None)
                        server_st = getattr(getattr(self.axis_client_qt, 'axisClient', None), 'server_status', None)
                        log.info(
                            "DECIDE tel_ok=%s set_ok=%s | az_cur=%.3f el_cur=%.3f | az_set=%.3f el_set=%.3f | "
                            "err=(%.2f, %.2f) thr=(%.2f, %.2f) need=(%s,%s) desired=(%s,%s) | "
                            "endstop=(%s,%s) setrate=(%s,%s) rate=(%s,%s) server=%s",
                            tel_ok, set_ok,
                            az_cur if isinstance(az_cur, (int, float)) else float('nan'),
                            el_cur if isinstance(el_cur, (int, float)) else float('nan'),
                            self.tracked_object.az_set if isinstance(self.tracked_object.az_set, (int, float)) else float('nan'),
                            self.tracked_object.el_set if isinstance(self.tracked_object.el_set, (int, float)) else float('nan'),
                            self.tracked_object.az_error, self.tracked_object.el_error,
                            az_err_th, el_err_th, need_az, need_el, desired_az, desired_el,
                            end_az, end_el, az_setrate, el_setrate, az_rate, el_rate, getattr(server_st, "name", server_st)
                        )
                        hb_last = now
                        hb_ticks = 0
                except Exception:
                    pass

                time.sleep(interval)
                worker = self.thread_manager.get_worker(self._thread_name)

        except Exception as e:
            # Laisser remonter: Worker.error sera émis par ThreadManager
            raise

    def _stop_motors(self):
        """Arrête AZ et EL proprement via le core."""
        try:
            logging.getLogger("Tracker").info("FORCE STOP motors (Tracker._stop_motors)")
            self.thread_manager.run_coro("AxisCoreLoop", self.axis_client_qt.axisClient.stop_az)
            self.thread_manager.run_coro("AxisCoreLoop", self.axis_client_qt.axisClient.stop_el)
            self.axis_client_qt.axisClient.axis_status['azimuth'] = AxisStatus.MOTION_AZ_STOP
            self.axis_client_qt.axisClient.axis_status['elevation'] = AxisStatus.MOTION_EL_STOP
        except Exception as e:
            logging.getLogger("Tracker").info(f"FORCE STOP motors error: {e}")
            pass

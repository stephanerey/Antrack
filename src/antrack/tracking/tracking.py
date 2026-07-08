# src/tracking/tracking.py
# Tracking components: tracked target (TrackedObject) and tracking controller (Tracker)

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple
import math
import time
import logging

from antrack.core.axis.axis_client import AxisStatus
from antrack.tracking.tracking_diagnostics import (
    TrackingDiagnosticsSession,
    compute_reaction_latency,
    compute_telemetry_age,
    load_tracking_diagnostics_config,
    measure_command_latency,
)
from antrack.tracking.motion_constraints import (
    constrained_azimuth_error,
    constrained_elevation_error,
    parse_forbidden_ranges,
)
from antrack.tracking.motion_refresh import (
    configured_move_refresh_interval,
    effective_motion_refresh_interval,
    should_emit_move,
    should_emit_stop,
)


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
        self.az_theoretical_deg: float = 0.0
        self.el_theoretical_deg: float = 0.0
        self.az_error: float = 0.0
        self.el_error: float = 0.0
        self.snr_db: float = float("nan")
        self.snr_mode: str = "relative"
        self.scan_offset_az_deg: float = 0.0
        self.scan_offset_el_deg: float = 0.0
        self.scan_probe_offset_az_deg: float = 0.0
        self.scan_probe_offset_el_deg: float = 0.0
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
    Interacts with the antenna controller facade from a QThread.
    """
    def __init__(self, axis_client_qt, settings, thread_manager, tracked_object: Optional[TrackedObject] = None):
        self.axis_client_qt = axis_client_qt
        self.settings = settings or {}
        self.thread_manager = thread_manager
        self.tracking_manager = getattr(thread_manager, "tracking_manager", None) if thread_manager is not None else None
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
        self._last_target_command_ts = 0.0
        self._last_target_command = (None, None)

        # Diagnostics: remember telemetry/setpoints state transitions
        self._last_tel_ok = None
        self._last_set_ok = None
        self._hb_last = time.monotonic()
        self._hb_ticks = 0
        self._none_tel_streak = 0
        self._none_set_streak = 0
        self._tel_state_prev = None
        self._set_state_prev = None
        self._last_step_started_monotonic: float | None = None
        self._tracking_diag = TrackingDiagnosticsSession(
            load_tracking_diagnostics_config(settings),
            logger=logging.getLogger("TrackingDiagnostics"),
        )
        self._tracking_diag_enabled = self._tracking_diag.enabled
        self._repeated_command_timeouts = 0

    def mark_speeds_dirty(self):
        """Request re-applying AZ/EL speeds on the next cycle."""
        self._must_apply_speeds = True
        self._kickstart_pending = True

    @property
    def diagnostics_enabled(self) -> bool:
        return self._tracking_diag_enabled

    @staticmethod
    def _to_bool(value) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        return str(value).strip().lower() in {"1", "true", "yes", "on"}

    def _antenna_settings(self) -> dict:
        if not isinstance(self.settings, dict):
            return {}
        return self.settings.get("ANTENNA", self.settings.get("antenna", {})) or {}

    def _performance_settings(self) -> dict:
        if not isinstance(self.settings, dict):
            return {}
        return self.settings.get("PERFORMANCE", self.settings.get("performance", {})) or {}

    def _effective_tracking_interval(self) -> float:
        antenna = self._antenna_settings()
        perf = self._performance_settings()
        interval = float(antenna.get("min_move_duration", 0.1))
        if self._to_bool(perf.get("cpu_optimized", False)):
            interval = max(interval, float(perf.get("min_move_duration", interval)))
        return float(max(0.01, interval))

    def get_loop_interval(self) -> float:
        return self._effective_tracking_interval()

    def _backend_snapshot(self) -> dict:
        backend = getattr(self.axis_client_qt, "backend", None)
        snapshot = getattr(backend, "get_diagnostics_snapshot", None)
        if callable(snapshot):
            try:
                value = snapshot()
                if isinstance(value, dict):
                    return value
            except Exception:
                pass
        return {}

    def _thread_running(self, thread_name: str) -> bool:
        if not self.thread_manager:
            return False
        worker = self.thread_manager.get_worker(thread_name)
        thread = getattr(self.thread_manager, "threads", {}).get(thread_name)
        return bool(worker and thread and getattr(thread, "isRunning", lambda: False)() and not getattr(worker, "abort", False))

    def _tracking_loop_active_count(self) -> int:
        if not self.thread_manager:
            return 0
        count = 0
        for name, thread in getattr(self.thread_manager, "threads", {}).items():
            if not str(name).startswith("TrackingLoop"):
                continue
            if getattr(thread, "isRunning", lambda: False)():
                count += 1
        return count

    def _record_command(self, command_name: str, func):
        if not self._tracking_diag_enabled:
            return func(), None
        record: dict[str, object] = {}
        result = measure_command_latency(command_name, func, record.update)
        return result, record

    @staticmethod
    def _safe_float(value):
        return float(value) if isinstance(value, (int, float)) else None

    def _emit_tracking_warning(self, key: str, message: str, *args) -> None:
        if self._tracking_diag_enabled:
            self._tracking_diag.warning(key, message, *args)

    def _emit_diag_rows(self, rows: list[dict]) -> None:
        if self._tracking_diag_enabled and rows:
            self._tracking_diag.emit_rows(rows)

    def _axis_diag_row(
        self,
        *,
        axis: str,
        step_started: float,
        loop_dt_s: float | None,
        expected_loop_interval_s: float,
        telemetry_age_s: float | None,
        target_deg: float | None,
        actual_deg: float | None,
        error_deg: float | None,
        threshold_deg: float,
        approach_deg: float,
        close_deg: float,
        current_axis_state,
        last_axis_command: str,
        decision: str,
        command_to_send: str | None,
        command_reason: str,
        speed_requested: float | None,
        command_record: dict | None,
        backend_snapshot: dict,
        worker_abort: bool,
    ) -> dict:
        antenna = getattr(self.axis_client_qt, "antenna", None)
        backend_state = backend_snapshot.get("backend_state")
        backend_last_error = backend_snapshot.get("last_error") or backend_snapshot.get("modbus_last_error")
        configured_polling = getattr(self.axis_client_qt, "polling_intervals", (None, None))
        configured_position_interval = backend_snapshot.get("configured_position_interval_s")
        configured_status_interval = backend_snapshot.get("configured_status_interval_s")
        if not isinstance(configured_position_interval, (int, float)) and isinstance(configured_polling, tuple):
            configured_position_interval = configured_polling[0]
        if not isinstance(configured_status_interval, (int, float)) and isinstance(configured_polling, tuple):
            configured_status_interval = configured_polling[1]
        position_last_update_monotonic = backend_snapshot.get("position_last_update_monotonic_s")
        command_start_monotonic = None if not command_record else command_record.get("command_start_monotonic_s")
        command_end_monotonic = None if not command_record else command_record.get("command_end_monotonic_s")
        reaction_latency_s = compute_reaction_latency(
            command_start_monotonic if isinstance(command_start_monotonic, (int, float)) else step_started,
            position_last_update_monotonic,
        )
        reaction_complete_latency_s = compute_reaction_latency(command_end_monotonic, position_last_update_monotonic)
        call_counts = backend_snapshot.get("call_counts") or {}
        call_last_latency = backend_snapshot.get("call_last_latency_s") or {}
        call_avg_latency = backend_snapshot.get("call_avg_latency_s") or {}
        fallback_request_count = sum(v for v in call_counts.values() if isinstance(v, int))
        fallback_latency_values = [v for v in call_avg_latency.values() if isinstance(v, (int, float))]
        fallback_latency_avg = (
            sum(fallback_latency_values) / len(fallback_latency_values)
            if fallback_latency_values
            else None
        )
        return {
            "timestamp_iso": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime()),
            "monotonic_s": step_started,
            "backend_name": getattr(self.axis_client_qt, "backend_name", ""),
            "connection_mode": getattr(getattr(self.axis_client_qt, "current_mode", lambda: None)(), "value", None),
            "axis": axis,
            "loop_dt_s": loop_dt_s,
            "expected_loop_interval_s": expected_loop_interval_s,
            "configured_position_interval_s": configured_position_interval,
            "configured_status_interval_s": configured_status_interval,
            "telemetry_age_s": telemetry_age_s,
            "reaction_latency_s": reaction_latency_s,
            "reaction_complete_latency_s": reaction_complete_latency_s,
            "target_deg": target_deg,
            "actual_deg": actual_deg,
            "error_deg": error_deg,
            "abs_error_deg": abs(error_deg) if isinstance(error_deg, (int, float)) else None,
            "threshold_deg": threshold_deg,
            "approach_deg": approach_deg,
            "close_deg": close_deg,
            "current_axis_state": getattr(current_axis_state, "name", current_axis_state),
            "last_axis_command": last_axis_command,
            "decision": decision,
            "command_to_send": command_to_send,
            "command_reason": command_reason,
            "speed_requested": speed_requested,
            "az_setrate": getattr(antenna, "az_setrate", None),
            "el_setrate": getattr(antenna, "el_setrate", None),
            "move_refresh_interval_s": self._effective_motion_refresh_interval(),
            "min_move_duration_s": self.get_loop_interval(),
            "command_start_monotonic_s": None if not command_record else command_record.get("command_start_monotonic_s"),
            "command_end_monotonic_s": None if not command_record else command_record.get("command_end_monotonic_s"),
            "command_latency_s": None if not command_record else command_record.get("command_latency_s"),
            "command_result": None if not command_record else command_record.get("command_result"),
            "command_exception": None if not command_record else command_record.get("command_exception"),
            "position_last_update_monotonic_s": backend_snapshot.get("position_last_update_monotonic_s"),
            "status_last_update_monotonic_s": backend_snapshot.get("status_last_update_monotonic_s"),
            "backend_state": backend_state,
            "backend_last_error": backend_last_error,
            "thread_name": self._thread_name,
            "worker_abort": worker_abort,
            "position_poller_running": self._thread_running("AxisPositionPoller"),
            "status_poller_running": self._thread_running("AxisStatusPoller"),
            "tracking_loop_active_count": self._tracking_loop_active_count(),
            "backend_diag_requests": backend_snapshot.get("modbus_requests", fallback_request_count),
            "backend_diag_fc03": backend_snapshot.get("modbus_fc03"),
            "backend_diag_fc06": backend_snapshot.get("modbus_fc06"),
            "backend_diag_failures": backend_snapshot.get("modbus_failures"),
            "backend_diag_timeouts": backend_snapshot.get("modbus_timeouts"),
            "backend_diag_latency_last_s": backend_snapshot.get("modbus_latency_last_s", call_last_latency.get(command_to_send or "")),
            "backend_diag_latency_min_s": backend_snapshot.get("modbus_latency_min_s"),
            "backend_diag_latency_avg_s": backend_snapshot.get("modbus_latency_avg_s", fallback_latency_avg),
            "backend_diag_latency_max_s": backend_snapshot.get("modbus_latency_max_s"),
            "backend_diag_position_interval_last_s": backend_snapshot.get("position_interval_last_s"),
            "backend_diag_position_interval_min_s": backend_snapshot.get("position_interval_min_s"),
            "backend_diag_position_interval_avg_s": backend_snapshot.get("position_interval_avg_s"),
            "backend_diag_position_interval_max_s": backend_snapshot.get("position_interval_max_s"),
            "backend_diag_status_interval_last_s": backend_snapshot.get("status_interval_last_s"),
            "backend_diag_status_interval_min_s": backend_snapshot.get("status_interval_min_s"),
            "backend_diag_status_interval_avg_s": backend_snapshot.get("status_interval_avg_s"),
            "backend_diag_status_interval_max_s": backend_snapshot.get("status_interval_max_s"),
        }

    def _refresh_runtime_tuning(self) -> None:
        self._move_refresh_interval = configured_move_refresh_interval(
            self.settings,
            default_s=self._move_refresh_interval,
        )

    def _effective_motion_refresh_interval(self) -> float:
        return effective_motion_refresh_interval(
            self.axis_client_qt,
            self.settings,
            default_s=self._move_refresh_interval,
        )

    def is_running(self) -> bool:
        """
        Return True only if the worker exists, the QThread is running, and the worker is not aborted.
        """
        if self.tracking_manager is not None:
            return bool(self.tracking_manager.is_tracker_active(self))
        if not self.thread_manager:
            return False
        w = self.thread_manager.get_worker(self._thread_name)
        t = getattr(self.thread_manager, "threads", {}).get(self._thread_name)
        return bool(w and t and getattr(t, "isRunning", lambda: False)() and not getattr(w, "abort", False))

    def start(self):
        """Start the tracking loop in a QThread (idempotent and robust to stale workers)."""
        if self.tracking_manager is not None:
            self._must_apply_speeds = True
            self._kickstart_pending = True
            self.tracking_manager.register_tracker(self)
            return
        if not self.thread_manager:
            return
        self._must_apply_speeds = True
        self._kickstart_pending = True
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
            if self.tracking_manager is not None:
                self.tracking_manager.unregister_tracker(self)
            elif self.thread_manager is not None:
                self.thread_manager.stop_thread(self._thread_name)
        finally:
            # Stop motors via the core
            try:
                self._stop_motors(force=True)
            except Exception:
                pass
            try:
                self._tracking_diag.close()
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
            while worker and not worker.abort:
                self.step(interval=interval)
                time.sleep(float(interval or self.get_loop_interval()))
                worker = self.thread_manager.get_worker(self._thread_name)

        except Exception as e:
            # Laisser remonter: Worker.error sera émis par ThreadManager
            raise

    def step(self, interval: Optional[float] = None) -> None:
        """Run one cooperative tracking iteration."""
        ant = self._antenna_settings()
        az_err_th = float(ant.get('az_error_threshold', 0.05))
        el_err_th = float(ant.get('el_error_threshold', 0.05))
        approach_deg = float(ant.get('approach_tracking_degrees', 5))
        close_deg = float(ant.get('close_tracking_degrees', 1))
        az_speed_far = float(ant.get('az_speed_far_tracking', 500))
        az_speed_approach = float(ant.get('az_speed_approach_tracking', 100))
        az_speed_close = float(ant.get('az_speed_close_tracking', 20))
        el_speed_far = float(ant.get('el_speed_far_tracking', 500))
        el_speed_approach = float(ant.get('el_speed_approach_tracking', 100))
        el_speed_close = float(ant.get('el_speed_close_tracking', 20))
        az_forbidden = parse_forbidden_ranges(
            ant.get("az_forbidden_ranges"),
            default=[(45.0, 90.0), (270.0, 300.0)],
        )
        el_forbidden = parse_forbidden_ranges(
            ant.get("el_forbidden_ranges"),
            default=[(-10.0, 0.0), (95.0, 100.0)],
        )
        self._refresh_runtime_tuning()
        log = logging.getLogger("Tracker")
        step_started = time.monotonic()
        move_refresh_interval_s = self._effective_motion_refresh_interval()
        expected_loop_interval_s = float(interval or self.get_loop_interval())
        loop_dt_s = None
        if self._last_step_started_monotonic is not None:
            loop_dt_s = max(0.0, step_started - self._last_step_started_monotonic)
        self._last_step_started_monotonic = step_started
        antenna_state = getattr(self.axis_client_qt, "antenna", None)
        telemetry_age_s = compute_telemetry_age(step_started, getattr(antenna_state, "last_update_monotonic", None))
        polling_intervals = getattr(self.axis_client_qt, "polling_intervals", (None, None))
        position_interval_s = polling_intervals[0] if isinstance(polling_intervals, tuple) else None
        worker = self.thread_manager.get_worker(self._thread_name) if self.thread_manager else None
        worker_abort = bool(getattr(worker, "abort", False))

        if self._tracking_diag_enabled:
            if loop_dt_s is not None and loop_dt_s > (2.0 * expected_loop_interval_s):
                self._emit_tracking_warning(
                    "loop_dt",
                    "Tracking diagnostics: loop dt drift %.3fs > 2x expected %.3fs",
                    loop_dt_s,
                    expected_loop_interval_s,
                )
            if isinstance(position_interval_s, (int, float)) and telemetry_age_s is not None and telemetry_age_s > (2.0 * float(position_interval_s)):
                self._emit_tracking_warning(
                    "telemetry_age",
                    "Tracking diagnostics: telemetry age %.3fs > 2x position interval %.3fs",
                    telemetry_age_s,
                    float(position_interval_s),
                )
            backend_state = getattr(getattr(self.axis_client_qt, "backend", None), "state", None)
            if getattr(backend_state, "value", backend_state) == "degraded":
                self._emit_tracking_warning(
                    "backend_degraded",
                    "Tracking diagnostics: backend degraded while tracking is active",
                )
            if not self._thread_running("AxisPositionPoller"):
                self._emit_tracking_warning(
                    "missing_position_poller",
                    "Tracking diagnostics: AxisPositionPoller is not running while tracking is active",
                )
            if not self._thread_running("AxisStatusPoller"):
                self._emit_tracking_warning(
                    "missing_status_poller",
                    "Tracking diagnostics: AxisStatusPoller is not running while tracking is active",
                )
            active_count = self._tracking_loop_active_count()
            if active_count > 1:
                self._emit_tracking_warning(
                    "duplicate_tracking_loop",
                    "Tracking diagnostics: %d tracking loops appear active",
                    active_count,
                )

        az_cur = getattr(getattr(self.axis_client_qt, 'antenna', None), 'az', None)
        el_cur = getattr(getattr(self.axis_client_qt, 'antenna', None), 'el', None)
        az_events: list[dict] = []
        el_events: list[dict] = []

        if self.tracked_object.az_set is None or self.tracked_object.el_set is None:
            self._none_set_streak += 1
            cur_set_state = False
            if self._set_state_prev is None or self._set_state_prev != cur_set_state:
                log.info("SET_STATE change -> set_ok=False (az_set=%s, el_set=%s)", self.tracked_object.az_set, self.tracked_object.el_set)
                self._set_state_prev = cur_set_state
            if self._none_set_streak % 10 == 1:
                log.info("Tracker: setpoints missing (streak=%d) az_set=%s el_set=%s", self._none_set_streak, self.tracked_object.az_set, self.tracked_object.el_set)
            if self._tracking_diag_enabled:
                backend_snapshot = self._backend_snapshot()
                self._emit_diag_rows(
                    [
                        self._axis_diag_row(
                            axis="AZ",
                            step_started=step_started,
                            loop_dt_s=loop_dt_s,
                            expected_loop_interval_s=expected_loop_interval_s,
                            telemetry_age_s=telemetry_age_s,
                            target_deg=self._safe_float(self.tracked_object.az_set),
                            actual_deg=self._safe_float(az_cur),
                            error_deg=None,
                            threshold_deg=az_err_th,
                            approach_deg=approach_deg,
                            close_deg=close_deg,
                            current_axis_state=getattr(self.axis_client_qt, "axis_status", {}).get("azimuth"),
                            last_axis_command=self._last_az_cmd,
                            decision="MISSING_SETPOINT",
                            command_to_send=None,
                            command_reason="tracked_object setpoint unavailable",
                            speed_requested=None,
                            command_record=None,
                            backend_snapshot=backend_snapshot,
                            worker_abort=worker_abort,
                        ),
                        self._axis_diag_row(
                            axis="EL",
                            step_started=step_started,
                            loop_dt_s=loop_dt_s,
                            expected_loop_interval_s=expected_loop_interval_s,
                            telemetry_age_s=telemetry_age_s,
                            target_deg=self._safe_float(self.tracked_object.el_set),
                            actual_deg=self._safe_float(el_cur),
                            error_deg=None,
                            threshold_deg=el_err_th,
                            approach_deg=approach_deg,
                            close_deg=close_deg,
                            current_axis_state=getattr(self.axis_client_qt, "axis_status", {}).get("elevation"),
                            last_axis_command=self._last_el_cmd,
                            decision="MISSING_SETPOINT",
                            command_to_send=None,
                            command_reason="tracked_object setpoint unavailable",
                            speed_requested=None,
                            command_record=None,
                            backend_snapshot=backend_snapshot,
                            worker_abort=worker_abort,
                        ),
                    ]
                )
            return

        if self._set_state_prev is None or self._set_state_prev is False:
            log.info("SET_STATE change -> set_ok=True (az_set=%.3f, el_set=%.3f)", self.tracked_object.az_set, self.tracked_object.el_set)
            self._set_state_prev = True
        self._none_set_streak = 0

        if getattr(self.axis_client_qt, "supports_absolute_targets", lambda: False)():
            if self._kickstart_pending or self._must_apply_speeds:
                prime_events = self._prime_motion(
                    az_speed_far=az_speed_far,
                    el_speed_far=el_speed_far,
                    logger=log,
                )
                az_events.extend(prime_events["AZ"])
                el_events.extend(prime_events["EL"])
            now_ts = time.monotonic()
            target = (
                round(float(self.tracked_object.az_set), 3),
                round(float(self.tracked_object.el_set), 3),
            )
            if (
                target != self._last_target_command
                or (now_ts - self._last_target_command_ts) >= move_refresh_interval_s
            ):
                try:
                    if self._tracking_diag_enabled:
                        _, target_record = self._record_command(
                            "set_target_position",
                            lambda: self.axis_client_qt.set_target_position(
                                self.tracked_object.az_set,
                                self.tracked_object.el_set,
                            ),
                        )
                    else:
                        target_record = None
                        self.axis_client_qt.set_target_position(
                            self.tracked_object.az_set,
                            self.tracked_object.el_set,
                        )
                    self._last_target_command = target
                    self._last_target_command_ts = now_ts
                    az_events.append(
                        {
                            "decision": "ABSOLUTE_TARGET",
                            "command_to_send": "set_target_position",
                            "command_reason": "target changed or refresh interval reached",
                            "speed_requested": None,
                            "command_record": target_record,
                        }
                    )
                    el_events.append(dict(az_events[-1]))
                except Exception as exc:
                    if self._tracking_diag_enabled and "timed out" in str(exc).lower():
                        self._repeated_command_timeouts += 1
                    log.warning("CMD set_target_position error: %s", exc)
                    az_events.append(
                        {
                            "decision": "ABSOLUTE_TARGET_ERROR",
                            "command_to_send": "set_target_position",
                            "command_reason": str(exc),
                            "speed_requested": None,
                            "command_record": {
                                "command_exception": str(exc),
                            },
                        }
                    )
                    el_events.append(dict(az_events[-1]))
            if self._tracking_diag_enabled:
                backend_snapshot = self._backend_snapshot()
                self._emit_diag_rows(
                    [
                        self._axis_diag_row(
                            axis="AZ",
                            step_started=step_started,
                            loop_dt_s=loop_dt_s,
                            expected_loop_interval_s=expected_loop_interval_s,
                            telemetry_age_s=telemetry_age_s,
                            target_deg=self._safe_float(self.tracked_object.az_set),
                            actual_deg=self._safe_float(az_cur),
                            error_deg=None,
                            threshold_deg=az_err_th,
                            approach_deg=approach_deg,
                            close_deg=close_deg,
                            current_axis_state=getattr(self.axis_client_qt, "axis_status", {}).get("azimuth"),
                            last_axis_command=self._last_az_cmd,
                            decision=az_events[0]["decision"] if az_events else "ABSOLUTE_TARGET_HOLD",
                            command_to_send=az_events[0]["command_to_send"] if az_events else None,
                            command_reason=az_events[0]["command_reason"] if az_events else "absolute target backend",
                            speed_requested=None,
                            command_record=az_events[0]["command_record"] if az_events else None,
                            backend_snapshot=backend_snapshot,
                            worker_abort=worker_abort,
                        ),
                        self._axis_diag_row(
                            axis="EL",
                            step_started=step_started,
                            loop_dt_s=loop_dt_s,
                            expected_loop_interval_s=expected_loop_interval_s,
                            telemetry_age_s=telemetry_age_s,
                            target_deg=self._safe_float(self.tracked_object.el_set),
                            actual_deg=self._safe_float(el_cur),
                            error_deg=None,
                            threshold_deg=el_err_th,
                            approach_deg=approach_deg,
                            close_deg=close_deg,
                            current_axis_state=getattr(self.axis_client_qt, "axis_status", {}).get("elevation"),
                            last_axis_command=self._last_el_cmd,
                            decision=el_events[0]["decision"] if el_events else "ABSOLUTE_TARGET_HOLD",
                            command_to_send=el_events[0]["command_to_send"] if el_events else None,
                            command_reason=el_events[0]["command_reason"] if el_events else "absolute target backend",
                            speed_requested=None,
                            command_record=el_events[0]["command_record"] if el_events else None,
                            backend_snapshot=backend_snapshot,
                            worker_abort=worker_abort,
                        ),
                    ]
                )
            return

        if az_cur is None or el_cur is None:
            self._none_tel_streak += 1
            cur_tel_state = False
            if self._tel_state_prev is None or self._tel_state_prev != cur_tel_state:
                log.info("TEL_STATE change -> tel_ok=False (az_cur=%s, el_cur=%s)", az_cur, el_cur)
                self._tel_state_prev = cur_tel_state
            if self._none_tel_streak % 10 == 1:
                log.info("Tracker: telemetry missing (streak=%d) az_cur=%s el_cur=%s", self._none_tel_streak, az_cur, el_cur)
            if self._tracking_diag_enabled:
                backend_snapshot = self._backend_snapshot()
                self._emit_diag_rows(
                    [
                        self._axis_diag_row(
                            axis="AZ",
                            step_started=step_started,
                            loop_dt_s=loop_dt_s,
                            expected_loop_interval_s=expected_loop_interval_s,
                            telemetry_age_s=telemetry_age_s,
                            target_deg=self._safe_float(self.tracked_object.az_set),
                            actual_deg=None,
                            error_deg=None,
                            threshold_deg=az_err_th,
                            approach_deg=approach_deg,
                            close_deg=close_deg,
                            current_axis_state=getattr(self.axis_client_qt, "axis_status", {}).get("azimuth"),
                            last_axis_command=self._last_az_cmd,
                            decision="MISSING_TELEMETRY",
                            command_to_send=None,
                            command_reason="antenna telemetry unavailable",
                            speed_requested=None,
                            command_record=None,
                            backend_snapshot=backend_snapshot,
                            worker_abort=worker_abort,
                        ),
                        self._axis_diag_row(
                            axis="EL",
                            step_started=step_started,
                            loop_dt_s=loop_dt_s,
                            expected_loop_interval_s=expected_loop_interval_s,
                            telemetry_age_s=telemetry_age_s,
                            target_deg=self._safe_float(self.tracked_object.el_set),
                            actual_deg=None,
                            error_deg=None,
                            threshold_deg=el_err_th,
                            approach_deg=approach_deg,
                            close_deg=close_deg,
                            current_axis_state=getattr(self.axis_client_qt, "axis_status", {}).get("elevation"),
                            last_axis_command=self._last_el_cmd,
                            decision="MISSING_TELEMETRY",
                            command_to_send=None,
                            command_reason="antenna telemetry unavailable",
                            speed_requested=None,
                            command_record=None,
                            backend_snapshot=backend_snapshot,
                            worker_abort=worker_abort,
                        ),
                    ]
                )
            return

        if self._tel_state_prev is None or self._tel_state_prev is False:
            log.info("TEL_STATE change -> tel_ok=True (az_cur=%.3f, el_cur=%.3f)", az_cur, el_cur)
            self._tel_state_prev = True
        self._none_tel_streak = 0

        az_route_error = constrained_azimuth_error(az_cur, self.tracked_object.az_set, az_forbidden)
        el_route_error = constrained_elevation_error(el_cur, self.tracked_object.el_set, el_forbidden)
        az_blocked = az_route_error is None
        el_blocked = el_route_error is None

        self.tracked_object.az_error = float(az_route_error or 0.0)
        self.tracked_object.el_error = float(el_route_error or 0.0)

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

        need_az = (not az_blocked) and abs(self.tracked_object.az_error) > az_err_th
        need_el = (not el_blocked) and abs(self.tracked_object.el_error) > el_err_th

        if self._kickstart_pending or self._must_apply_speeds:
            prime_events = self._prime_motion(
                az_speed_far=az_speed_far,
                el_speed_far=el_speed_far,
                logger=log,
            )
            az_events.extend(prime_events["AZ"])
            el_events.extend(prime_events["EL"])

        desired_az = "STOP"
        if need_az:
            desired_az = "CCW" if self.tracked_object.az_error > 0 else "CW"
        desired_el = "STOP"
        if need_el:
            desired_el = "DOWN" if self.tracked_object.el_error > 0 else "UP"
        prev_last_az_cmd = self._last_az_cmd
        prev_last_el_cmd = self._last_el_cmd

        if need_az or need_el or az_blocked or el_blocked:
            try:
                if abs(self.tracked_object.az_error) > approach_deg:
                    rate_az = az_speed_far
                elif abs(self.tracked_object.az_error) > close_deg:
                    rate_az = az_speed_approach
                else:
                    rate_az = az_speed_close
                if getattr(self.axis_client_qt.antenna, 'az_setrate', None) != rate_az:
                    log.debug("CMD set_az_speed -> %.1f", rate_az)
                    ack, command_record = self._record_command("set_az_speed", lambda: self.axis_client_qt.set_az_speed(rate_az))
                    if ack is not None:
                        self.axis_client_qt.antenna.az_setrate = rate_az
                    az_events.append(
                        {
                            "decision": "SET_SPEED",
                            "command_to_send": "set_az_speed",
                            "command_reason": "tracking speed bucket update",
                            "speed_requested": rate_az,
                            "command_record": command_record,
                        }
                    )
            except Exception as e:
                if self._tracking_diag_enabled and "timed out" in str(e).lower():
                    self._repeated_command_timeouts += 1
                log.warning("CMD set_az_speed error: %s", e)
                az_events.append(
                    {
                        "decision": "SET_SPEED_ERROR",
                        "command_to_send": "set_az_speed",
                        "command_reason": str(e),
                        "speed_requested": rate_az if 'rate_az' in locals() else None,
                        "command_record": {"command_exception": str(e)},
                    }
                )

            try:
                if abs(self.tracked_object.el_error) > approach_deg:
                    rate_el = el_speed_far
                elif abs(self.tracked_object.el_error) > close_deg:
                    rate_el = el_speed_approach
                else:
                    rate_el = el_speed_close
                if getattr(self.axis_client_qt.antenna, 'el_setrate', None) != rate_el:
                    ack, command_record = self._record_command("set_el_speed", lambda: self.axis_client_qt.set_el_speed(rate_el))
                    if ack is not None:
                        self.axis_client_qt.antenna.el_setrate = rate_el
                    el_events.append(
                        {
                            "decision": "SET_SPEED",
                            "command_to_send": "set_el_speed",
                            "command_reason": "tracking speed bucket update",
                            "speed_requested": rate_el,
                            "command_record": command_record,
                        }
                    )
            except Exception as e:
                if self._tracking_diag_enabled and "timed out" in str(e).lower():
                    self._repeated_command_timeouts += 1
                log.warning("CMD set_el_speed error: %s", e)
                el_events.append(
                    {
                        "decision": "SET_SPEED_ERROR",
                        "command_to_send": "set_el_speed",
                        "command_reason": str(e),
                        "speed_requested": rate_el if 'rate_el' in locals() else None,
                        "command_record": {"command_exception": str(e)},
                    }
                )

            try:
                now_ts = time.monotonic()
                if need_az:
                    if self.tracked_object.az_error > 0:
                        emit_move, hold_decision, hold_reason = should_emit_move(
                            self.axis_client_qt,
                            self.settings,
                            last_cmd=self._last_az_cmd,
                            desired_cmd="CCW",
                            elapsed_s=max(0.0, now_ts - self._last_az_cmd_ts),
                            default_refresh_interval_s=self._move_refresh_interval,
                        )
                        if emit_move:
                            _result, command_record = self._record_command("move_ccw", self.axis_client_qt.move_ccw)
                            self.axis_client_qt.axis_status['azimuth'] = AxisStatus.MOTION_AZ_CCW
                            self._last_az_cmd = "CCW"
                            self._last_az_cmd_ts = now_ts
                            az_events.append(
                                {
                                    "decision": "MOVE",
                                    "command_to_send": "move_ccw",
                                    "command_reason": "positive azimuth error above threshold",
                                    "speed_requested": rate_az if 'rate_az' in locals() else None,
                                    "command_record": command_record,
                                }
                            )
                        elif self._tracking_diag_enabled:
                            az_events.append(
                                {
                                    "decision": hold_decision,
                                    "command_to_send": "move_ccw",
                                    "command_reason": hold_reason,
                                    "speed_requested": rate_az if 'rate_az' in locals() else None,
                                    "command_record": None,
                                }
                            )
                    else:
                        emit_move, hold_decision, hold_reason = should_emit_move(
                            self.axis_client_qt,
                            self.settings,
                            last_cmd=self._last_az_cmd,
                            desired_cmd="CW",
                            elapsed_s=max(0.0, now_ts - self._last_az_cmd_ts),
                            default_refresh_interval_s=self._move_refresh_interval,
                        )
                        if emit_move:
                            _result, command_record = self._record_command("move_cw", self.axis_client_qt.move_cw)
                            self.axis_client_qt.axis_status['azimuth'] = AxisStatus.MOTION_AZ_CW
                            self._last_az_cmd = "CW"
                            self._last_az_cmd_ts = now_ts
                            az_events.append(
                                {
                                    "decision": "MOVE",
                                    "command_to_send": "move_cw",
                                    "command_reason": "negative azimuth error above threshold",
                                    "speed_requested": rate_az if 'rate_az' in locals() else None,
                                    "command_record": command_record,
                                }
                            )
                        elif self._tracking_diag_enabled:
                            az_events.append(
                                {
                                    "decision": hold_decision,
                                    "command_to_send": "move_cw",
                                    "command_reason": hold_reason,
                                    "speed_requested": rate_az if 'rate_az' in locals() else None,
                                    "command_record": None,
                                }
                            )
                else:
                    emit_stop, stop_decision, stop_reason = should_emit_stop(
                        self.axis_client_qt,
                        self.settings,
                        last_cmd=self._last_az_cmd,
                        elapsed_s=max(0.0, now_ts - self._last_az_cmd_ts),
                        default_refresh_interval_s=self._move_refresh_interval,
                    )
                    if emit_stop:
                        _result, command_record = self._record_command("stop_az", self.axis_client_qt.stop_az)
                        self.axis_client_qt.axis_status['azimuth'] = AxisStatus.MOTION_AZ_STOP
                        self._last_az_cmd = "STOP"
                        self._last_az_cmd_ts = now_ts
                        az_events.append(
                            {
                                "decision": "STOP",
                                "command_to_send": "stop_az",
                                "command_reason": stop_reason,
                                "speed_requested": None,
                                "command_record": command_record,
                            }
                        )
                    elif self._tracking_diag_enabled:
                        az_events.append(
                            {
                                "decision": stop_decision,
                                "command_to_send": "stop_az",
                                "command_reason": stop_reason,
                                "speed_requested": None,
                                "command_record": None,
                            }
                        )
            except Exception as e:
                if self._tracking_diag_enabled and "timed out" in str(e).lower():
                    self._repeated_command_timeouts += 1
                log.warning("CMD az motion error: %s", e)
                az_events.append(
                    {
                        "decision": "MOTION_ERROR",
                        "command_to_send": desired_az,
                        "command_reason": str(e),
                        "speed_requested": rate_az if 'rate_az' in locals() else None,
                        "command_record": {"command_exception": str(e)},
                    }
                )

            try:
                now_ts = time.monotonic()
                if need_el:
                    if self.tracked_object.el_error > 0:
                        emit_move, hold_decision, hold_reason = should_emit_move(
                            self.axis_client_qt,
                            self.settings,
                            last_cmd=self._last_el_cmd,
                            desired_cmd="DOWN",
                            elapsed_s=max(0.0, now_ts - self._last_el_cmd_ts),
                            default_refresh_interval_s=self._move_refresh_interval,
                        )
                        if emit_move:
                            _result, command_record = self._record_command("move_down", self.axis_client_qt.move_down)
                            self.axis_client_qt.axis_status['elevation'] = AxisStatus.MOTION_EL_DOWN
                            self._last_el_cmd = "DOWN"
                            self._last_el_cmd_ts = now_ts
                            el_events.append(
                                {
                                    "decision": "MOVE",
                                    "command_to_send": "move_down",
                                    "command_reason": "positive elevation error above threshold",
                                    "speed_requested": rate_el if 'rate_el' in locals() else None,
                                    "command_record": command_record,
                                }
                            )
                        elif self._tracking_diag_enabled:
                            el_events.append(
                                {
                                    "decision": hold_decision,
                                    "command_to_send": "move_down",
                                    "command_reason": hold_reason,
                                    "speed_requested": rate_el if 'rate_el' in locals() else None,
                                    "command_record": None,
                                }
                            )
                    else:
                        emit_move, hold_decision, hold_reason = should_emit_move(
                            self.axis_client_qt,
                            self.settings,
                            last_cmd=self._last_el_cmd,
                            desired_cmd="UP",
                            elapsed_s=max(0.0, now_ts - self._last_el_cmd_ts),
                            default_refresh_interval_s=self._move_refresh_interval,
                        )
                        if emit_move:
                            _result, command_record = self._record_command("move_up", self.axis_client_qt.move_up)
                            self.axis_client_qt.axis_status['elevation'] = AxisStatus.MOTION_EL_UP
                            self._last_el_cmd = "UP"
                            self._last_el_cmd_ts = now_ts
                            el_events.append(
                                {
                                    "decision": "MOVE",
                                    "command_to_send": "move_up",
                                    "command_reason": "negative elevation error above threshold",
                                    "speed_requested": rate_el if 'rate_el' in locals() else None,
                                    "command_record": command_record,
                                }
                            )
                        elif self._tracking_diag_enabled:
                            el_events.append(
                                {
                                    "decision": hold_decision,
                                    "command_to_send": "move_up",
                                    "command_reason": hold_reason,
                                    "speed_requested": rate_el if 'rate_el' in locals() else None,
                                    "command_record": None,
                                }
                            )
                else:
                    emit_stop, stop_decision, stop_reason = should_emit_stop(
                        self.axis_client_qt,
                        self.settings,
                        last_cmd=self._last_el_cmd,
                        elapsed_s=max(0.0, now_ts - self._last_el_cmd_ts),
                        default_refresh_interval_s=self._move_refresh_interval,
                    )
                    if emit_stop:
                        _result, command_record = self._record_command("stop_el", self.axis_client_qt.stop_el)
                        self.axis_client_qt.axis_status['elevation'] = AxisStatus.MOTION_EL_STOP
                        self._last_el_cmd = "STOP"
                        self._last_el_cmd_ts = now_ts
                        el_events.append(
                            {
                                "decision": "STOP",
                                "command_to_send": "stop_el",
                                "command_reason": stop_reason,
                                "speed_requested": None,
                                "command_record": command_record,
                            }
                        )
                    elif self._tracking_diag_enabled:
                        el_events.append(
                            {
                                "decision": stop_decision,
                                "command_to_send": "stop_el",
                                "command_reason": stop_reason,
                                "speed_requested": None,
                                "command_record": None,
                            }
                        )
            except Exception as e:
                if self._tracking_diag_enabled and "timed out" in str(e).lower():
                    self._repeated_command_timeouts += 1
                log.warning("CMD el motion error: %s", e)
                el_events.append(
                    {
                        "decision": "MOTION_ERROR",
                        "command_to_send": desired_el,
                        "command_reason": str(e),
                        "speed_requested": rate_el if 'rate_el' in locals() else None,
                        "command_record": {"command_exception": str(e)},
                    }
                )
        else:
            try:
                stop_events = self._stop_motors(force=False)
                az_events.extend(stop_events["AZ"] or [{
                    "decision": "STOP_ALL",
                    "command_to_send": "stop_az",
                    "command_reason": "both axes within threshold",
                    "speed_requested": None,
                    "command_record": None,
                }])
                el_events.extend(stop_events["EL"] or [{
                    "decision": "STOP_ALL",
                    "command_to_send": "stop_el",
                    "command_reason": "both axes within threshold",
                    "speed_requested": None,
                    "command_record": None,
                }])
            except Exception:
                pass

        self._hb_ticks += 1
        now = time.monotonic()
        try:
            tel_ok = isinstance(az_cur, (int, float)) and isinstance(el_cur, (int, float))
            set_ok = isinstance(self.tracked_object.az_set, (int, float)) and isinstance(self.tracked_object.el_set, (int, float))

            if self._last_tel_ok is None or self._last_tel_ok != tel_ok:
                log.info(f"TEL_STATE change -> tel_ok={tel_ok} (az_cur={az_cur}, el_cur={el_cur})")
                self._last_tel_ok = tel_ok
            if self._last_set_ok is None or self._last_set_ok != set_ok:
                log.info(f"SET_STATE change -> set_ok={set_ok} (az_set={self.tracked_object.az_set}, el_set={self.tracked_object.el_set})")
                self._last_set_ok = set_ok

            if now - self._hb_last >= 1.0:
                end_az = getattr(getattr(self.axis_client_qt, 'antenna', None), 'endstop_az', None)
                end_el = getattr(getattr(self.axis_client_qt, 'antenna', None), 'endstop_el', None)
                az_setrate = getattr(getattr(self.axis_client_qt, 'antenna', None), 'az_setrate', None)
                el_setrate = getattr(getattr(self.axis_client_qt, 'antenna', None), 'el_setrate', None)
                az_rate = getattr(getattr(self.axis_client_qt, 'antenna', None), 'az_rate', None)
                el_rate = getattr(getattr(self.axis_client_qt, 'antenna', None), 'el_rate', None)
                server_st = getattr(self.axis_client_qt, 'server_status', None)
                log.debug(
                    "DECIDE tel_ok=%s set_ok=%s | az_cur=%.3f el_cur=%.3f | az_set=%.3f el_set=%.3f | "
                    "err=(%.2f, %.2f) blocked=(%s,%s) thr=(%.2f, %.2f) need=(%s,%s) desired=(%s,%s) | "
                    "endstop=(%s,%s) setrate=(%s,%s) rate=(%s,%s) server=%s",
                    tel_ok, set_ok,
                    az_cur if isinstance(az_cur, (int, float)) else float('nan'),
                    el_cur if isinstance(el_cur, (int, float)) else float('nan'),
                    self.tracked_object.az_set if isinstance(self.tracked_object.az_set, (int, float)) else float('nan'),
                    self.tracked_object.el_set if isinstance(self.tracked_object.el_set, (int, float)) else float('nan'),
                    self.tracked_object.az_error, self.tracked_object.el_error,
                    az_blocked, el_blocked,
                    az_err_th, el_err_th, need_az, need_el, desired_az, desired_el,
                    end_az, end_el, az_setrate, el_setrate, az_rate, el_rate, getattr(server_st, "name", server_st)
                )
                self._hb_last = now
                self._hb_ticks = 0
        except Exception:
            pass

        if self._tracking_diag_enabled:
            if not az_events:
                az_events.append(
                    {
                        "decision": "HOLD",
                        "command_to_send": desired_az,
                        "command_reason": "no az command emitted this cycle",
                        "speed_requested": rate_az if 'rate_az' in locals() else None,
                        "command_record": None,
                    }
                )
            if not el_events:
                el_events.append(
                    {
                        "decision": "HOLD",
                        "command_to_send": desired_el,
                        "command_reason": "no el command emitted this cycle",
                        "speed_requested": rate_el if 'rate_el' in locals() else None,
                        "command_record": None,
                    }
                )

            for event in az_events + el_events:
                record = event.get("command_record") or {}
                latency = record.get("command_latency_s")
                if isinstance(latency, (int, float)) and latency > expected_loop_interval_s:
                    self._emit_tracking_warning(
                        f"command_latency_{event.get('command_to_send')}",
                        "Tracking diagnostics: command %s latency %.3fs > expected loop %.3fs",
                        event.get("command_to_send"),
                        latency,
                        expected_loop_interval_s,
                    )
                if record.get("command_result", "__missing__") is None and event.get("command_to_send"):
                    self._emit_tracking_warning(
                        f"command_none_{event.get('command_to_send')}",
                        "Tracking diagnostics: command %s returned None",
                        event.get("command_to_send"),
                    )
                if record.get("command_exception") and "timed out" in str(record.get("command_exception")).lower():
                    self._emit_tracking_warning(
                        "command_timeout",
                        "Tracking diagnostics: command timeout detected (%s)",
                        record.get("command_exception"),
                    )

            backend_snapshot = self._backend_snapshot()
            rows = []
            for event in az_events:
                rows.append(
                    self._axis_diag_row(
                        axis="AZ",
                        step_started=step_started,
                        loop_dt_s=loop_dt_s,
                        expected_loop_interval_s=expected_loop_interval_s,
                        telemetry_age_s=telemetry_age_s,
                        target_deg=self._safe_float(self.tracked_object.az_set),
                        actual_deg=self._safe_float(az_cur),
                        error_deg=self._safe_float(self.tracked_object.az_error),
                        threshold_deg=az_err_th,
                        approach_deg=approach_deg,
                        close_deg=close_deg,
                        current_axis_state=getattr(self.axis_client_qt, "axis_status", {}).get("azimuth"),
                        last_axis_command=prev_last_az_cmd,
                        decision=event["decision"],
                        command_to_send=event["command_to_send"],
                        command_reason=event["command_reason"],
                        speed_requested=event["speed_requested"],
                        command_record=event["command_record"],
                        backend_snapshot=backend_snapshot,
                        worker_abort=worker_abort,
                    )
                )
            for event in el_events:
                rows.append(
                    self._axis_diag_row(
                        axis="EL",
                        step_started=step_started,
                        loop_dt_s=loop_dt_s,
                        expected_loop_interval_s=expected_loop_interval_s,
                        telemetry_age_s=telemetry_age_s,
                        target_deg=self._safe_float(self.tracked_object.el_set),
                        actual_deg=self._safe_float(el_cur),
                        error_deg=self._safe_float(self.tracked_object.el_error),
                        threshold_deg=el_err_th,
                        approach_deg=approach_deg,
                        close_deg=close_deg,
                        current_axis_state=getattr(self.axis_client_qt, "axis_status", {}).get("elevation"),
                        last_axis_command=prev_last_el_cmd,
                        decision=event["decision"],
                        command_to_send=event["command_to_send"],
                        command_reason=event["command_reason"],
                        speed_requested=event["speed_requested"],
                        command_record=event["command_record"],
                        backend_snapshot=backend_snapshot,
                        worker_abort=worker_abort,
                    )
                )
            self._emit_diag_rows(rows)

    def _prime_motion(self, *, az_speed_far: float, el_speed_far: float, logger) -> dict[str, list[dict]]:
        """Prime the controller with a STOP and fresh rates before the first tracking move."""
        events = {"AZ": [], "EL": []}
        try:
            if self._kickstart_pending:
                _result, az_record = self._record_command("stop_az", self.axis_client_qt.stop_az)
                _result, el_record = self._record_command("stop_el", self.axis_client_qt.stop_el)
                self.axis_client_qt.axis_status["azimuth"] = AxisStatus.MOTION_AZ_STOP
                self.axis_client_qt.axis_status["elevation"] = AxisStatus.MOTION_EL_STOP
                self._last_az_cmd = "STOP"
                self._last_el_cmd = "STOP"
                now_ts = time.monotonic()
                self._last_az_cmd_ts = now_ts
                self._last_el_cmd_ts = now_ts
                self._kickstart_pending = False
                events["AZ"].append(
                    {
                        "decision": "PRIME_STOP",
                        "command_to_send": "stop_az",
                        "command_reason": "kickstart pending",
                        "speed_requested": None,
                        "command_record": az_record,
                    }
                )
                events["EL"].append(
                    {
                        "decision": "PRIME_STOP",
                        "command_to_send": "stop_el",
                        "command_reason": "kickstart pending",
                        "speed_requested": None,
                        "command_record": el_record,
                    }
                )
            if self._must_apply_speeds:
                ack, az_record = self._record_command("set_az_speed", lambda: self.axis_client_qt.set_az_speed(az_speed_far))
                if ack is not None:
                    self.axis_client_qt.antenna.az_setrate = az_speed_far
                ack, el_record = self._record_command("set_el_speed", lambda: self.axis_client_qt.set_el_speed(el_speed_far))
                if ack is not None:
                    self.axis_client_qt.antenna.el_setrate = el_speed_far
                self._must_apply_speeds = False
                events["AZ"].append(
                    {
                        "decision": "PRIME_SPEED",
                        "command_to_send": "set_az_speed",
                        "command_reason": "apply tracking speeds after reconnect/start",
                        "speed_requested": az_speed_far,
                        "command_record": az_record,
                    }
                )
                events["EL"].append(
                    {
                        "decision": "PRIME_SPEED",
                        "command_to_send": "set_el_speed",
                        "command_reason": "apply tracking speeds after reconnect/start",
                        "speed_requested": el_speed_far,
                        "command_record": el_record,
                    }
                )
        except Exception as exc:
            logger.warning("Tracker prime motion failed: %s", exc)
        return events

    def _stop_motors(self, *, force: bool = True):
        """Arrête AZ et EL proprement via le core."""
        events = {"AZ": [], "EL": []}
        try:
            logging.getLogger("Tracker").debug("FORCE STOP motors (Tracker._stop_motors)")
            now_ts = time.monotonic()
            emit_az = force
            emit_el = force
            az_reason = "force stop motors"
            el_reason = "force stop motors"
            if not force:
                emit_az, _decision, az_reason = should_emit_stop(
                    self.axis_client_qt,
                    self.settings,
                    last_cmd=self._last_az_cmd,
                    elapsed_s=max(0.0, now_ts - self._last_az_cmd_ts),
                    default_refresh_interval_s=self._move_refresh_interval,
                )
                emit_el, _decision, el_reason = should_emit_stop(
                    self.axis_client_qt,
                    self.settings,
                    last_cmd=self._last_el_cmd,
                    elapsed_s=max(0.0, now_ts - self._last_el_cmd_ts),
                    default_refresh_interval_s=self._move_refresh_interval,
                )
            if emit_az:
                _result, az_record = self._record_command("stop_az", self.axis_client_qt.stop_az)
                self.axis_client_qt.axis_status['azimuth'] = AxisStatus.MOTION_AZ_STOP
                self._last_az_cmd = "STOP"
                self._last_az_cmd_ts = now_ts
                events["AZ"].append(
                    {
                        "decision": "STOP_ALL",
                        "command_to_send": "stop_az",
                        "command_reason": az_reason,
                        "speed_requested": None,
                        "command_record": az_record,
                    }
                )
            if emit_el:
                _result, el_record = self._record_command("stop_el", self.axis_client_qt.stop_el)
                self.axis_client_qt.axis_status['elevation'] = AxisStatus.MOTION_EL_STOP
                self._last_el_cmd = "STOP"
                self._last_el_cmd_ts = now_ts
                events["EL"].append(
                    {
                        "decision": "STOP_ALL",
                        "command_to_send": "stop_el",
                        "command_reason": el_reason,
                        "speed_requested": None,
                        "command_record": el_record,
                    }
                )
        except Exception as e:
            logging.getLogger("Tracker").debug(f"FORCE STOP motors error: {e}")
        return events

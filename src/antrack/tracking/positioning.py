"""Fixed-target positioning controller for park/goto operations."""

from __future__ import annotations

import logging
import time
from typing import Optional

from antrack.core.axis.axis_client import AxisStatus
from antrack.tracking.motion_constraints import (
    constrained_azimuth_error,
    constrained_elevation_error,
    parse_forbidden_ranges,
)


class PositioningController:
    """Drive the antenna toward fixed AZ/EL setpoints and stop on arrival."""

    def __init__(self, axis_client_qt, settings, thread_manager, tracked_object) -> None:
        self.axis_client_qt = axis_client_qt
        self.settings = settings or {}
        self.thread_manager = thread_manager
        self.tracked_object = tracked_object
        self._thread_name = "PositioningLoop"
        self._move_refresh_interval = 1.0
        self._last_az_cmd = "STOP"
        self._last_el_cmd = "STOP"
        self._last_az_cmd_ts = 0.0
        self._last_el_cmd_ts = 0.0

    def is_running(self) -> bool:
        if not self.thread_manager:
            return False
        worker = self.thread_manager.get_worker(self._thread_name)
        thread = getattr(self.thread_manager, "threads", {}).get(self._thread_name)
        return bool(worker and thread and getattr(thread, "isRunning", lambda: False)() and not getattr(worker, "abort", False))

    def start(self) -> None:
        if not self.thread_manager:
            return
        worker = self.thread_manager.get_worker(self._thread_name)
        thread = getattr(self.thread_manager, "threads", {}).get(self._thread_name)
        if worker and (getattr(worker, "abort", False) or not (thread and getattr(thread, "isRunning", lambda: False)())):
            try:
                self.thread_manager.stop_thread(self._thread_name)
            except Exception:
                pass
        if self.is_running():
            return
        self.thread_manager.start_thread(self._thread_name, self._loop)

    def stop(self) -> None:
        try:
            self.thread_manager.stop_thread(self._thread_name)
        finally:
            try:
                self._stop_motors()
            except Exception:
                pass

    def _loop(self, interval: Optional[float] = None) -> None:
        worker = self.thread_manager.get_worker(self._thread_name)
        ant = {}
        if isinstance(self.settings, dict):
            ant = self.settings.get("ANTENNA", self.settings.get("antenna", {}))

        az_err_th = float(
            ant.get(
                "positioning_az_error_threshold",
                ant.get("az_error_threshold", 0.05),
            )
        )
        el_err_th = float(
            ant.get(
                "positioning_el_error_threshold",
                ant.get("el_error_threshold", 0.05),
            )
        )
        approach_deg = float(ant.get("approach_tracking_degrees", 5))
        close_deg = float(ant.get("close_tracking_degrees", 1))
        min_move_duration = float(ant.get("min_move_duration", 0.1))
        stable_cycles_required = max(2, int(ant.get("positioning_stable_cycles", 3)))

        az_speed_far = float(ant.get("az_speed_far_tracking", 500))
        az_speed_approach = float(ant.get("az_speed_approach_tracking", 100))
        az_speed_close = float(ant.get("az_speed_close_tracking", 20))
        el_speed_far = float(ant.get("el_speed_far_tracking", 500))
        el_speed_approach = float(ant.get("el_speed_approach_tracking", 100))
        el_speed_close = float(ant.get("el_speed_close_tracking", 20))
        az_forbidden = parse_forbidden_ranges(
            ant.get("az_forbidden_ranges"),
            default=[(45.0, 90.0), (270.0, 300.0)],
        )
        el_forbidden = parse_forbidden_ranges(
            ant.get("el_forbidden_ranges"),
            default=[(-10.0, 0.0), (95.0, 100.0)],
        )

        interval = float(interval or min_move_duration)
        stable_cycles = 0
        log = logging.getLogger("Positioning")

        while worker and not worker.abort:
            az_cur = getattr(getattr(self.axis_client_qt, "antenna", None), "az", None)
            el_cur = getattr(getattr(self.axis_client_qt, "antenna", None), "el", None)
            az_set = getattr(self.tracked_object, "az_set", None)
            el_set = getattr(self.tracked_object, "el_set", None)

            if not isinstance(az_set, (int, float)) or not isinstance(el_set, (int, float)):
                time.sleep(interval)
                worker = self.thread_manager.get_worker(self._thread_name)
                continue
            if not isinstance(az_cur, (int, float)) or not isinstance(el_cur, (int, float)):
                time.sleep(interval)
                worker = self.thread_manager.get_worker(self._thread_name)
                continue

            az_route_error = constrained_azimuth_error(az_cur, az_set, az_forbidden)
            el_route_error = constrained_elevation_error(el_cur, el_set, el_forbidden)
            az_blocked = az_route_error is None
            el_blocked = el_route_error is None

            self.tracked_object.az_error = float(az_route_error or 0.0)
            self.tracked_object.el_error = float(el_route_error or 0.0)

            need_az = (not az_blocked) and abs(self.tracked_object.az_error) > az_err_th
            need_el = (not el_blocked) and abs(self.tracked_object.el_error) > el_err_th

            if az_blocked or el_blocked:
                stable_cycles = 0
                self._stop_motors()
            elif not need_az and not need_el:
                stable_cycles += 1
                self._stop_motors()
                if stable_cycles >= stable_cycles_required:
                    log.info(
                        "Position reached: az=%.3f el=%.3f set=(%.3f, %.3f)",
                        az_cur,
                        el_cur,
                        az_set,
                        el_set,
                    )
                    break
            else:
                stable_cycles = 0
                self._apply_axis_motion(
                    az_error=self.tracked_object.az_error,
                    el_error=self.tracked_object.el_error,
                    need_az=need_az,
                    need_el=need_el,
                    approach_deg=approach_deg,
                    close_deg=close_deg,
                    az_speed_far=az_speed_far,
                    az_speed_approach=az_speed_approach,
                    az_speed_close=az_speed_close,
                    el_speed_far=el_speed_far,
                    el_speed_approach=el_speed_approach,
                    el_speed_close=el_speed_close,
                )

            time.sleep(interval)
            worker = self.thread_manager.get_worker(self._thread_name)

        self._stop_motors()

    def _apply_axis_motion(
        self,
        *,
        az_error: float,
        el_error: float,
        need_az: bool,
        need_el: bool,
        approach_deg: float,
        close_deg: float,
        az_speed_far: float,
        az_speed_approach: float,
        az_speed_close: float,
        el_speed_far: float,
        el_speed_approach: float,
        el_speed_close: float,
    ) -> None:
        now_ts = time.monotonic()

        try:
            if abs(az_error) > approach_deg:
                rate_az = az_speed_far
            elif abs(az_error) > close_deg:
                rate_az = az_speed_approach
            else:
                rate_az = az_speed_close
            if getattr(self.axis_client_qt.antenna, "az_setrate", None) != rate_az:
                self.axis_client_qt.antenna.az_setrate = rate_az
                self.thread_manager.run_coro("AxisCoreLoop", lambda: self.axis_client_qt.axisClient.set_az_speed(rate_az), timeout=1.0)
        except Exception:
            pass

        try:
            if abs(el_error) > approach_deg:
                rate_el = el_speed_far
            elif abs(el_error) > close_deg:
                rate_el = el_speed_approach
            else:
                rate_el = el_speed_close
            if getattr(self.axis_client_qt.antenna, "el_setrate", None) != rate_el:
                self.axis_client_qt.antenna.el_setrate = rate_el
                self.thread_manager.run_coro("AxisCoreLoop", lambda: self.axis_client_qt.axisClient.set_el_speed(rate_el), timeout=1.0)
        except Exception:
            pass

        try:
            if need_az:
                if az_error > 0:
                    if self._last_az_cmd != "CCW" or (now_ts - self._last_az_cmd_ts) >= self._move_refresh_interval:
                        self.thread_manager.run_coro("AxisCoreLoop", self.axis_client_qt.axisClient.move_ccw, timeout=1.0)
                        self.axis_client_qt.axisClient.axis_status["azimuth"] = AxisStatus.MOTION_AZ_CCW
                        self._last_az_cmd = "CCW"
                        self._last_az_cmd_ts = now_ts
                else:
                    if self._last_az_cmd != "CW" or (now_ts - self._last_az_cmd_ts) >= self._move_refresh_interval:
                        self.thread_manager.run_coro("AxisCoreLoop", self.axis_client_qt.axisClient.move_cw, timeout=1.0)
                        self.axis_client_qt.axisClient.axis_status["azimuth"] = AxisStatus.MOTION_AZ_CW
                        self._last_az_cmd = "CW"
                        self._last_az_cmd_ts = now_ts
            else:
                if self._last_az_cmd != "STOP" or (now_ts - self._last_az_cmd_ts) >= self._move_refresh_interval:
                    self.thread_manager.run_coro("AxisCoreLoop", self.axis_client_qt.axisClient.stop_az, timeout=1.0)
                    self.axis_client_qt.axisClient.axis_status["azimuth"] = AxisStatus.MOTION_AZ_STOP
                    self._last_az_cmd = "STOP"
                    self._last_az_cmd_ts = now_ts
        except Exception:
            pass

        try:
            if need_el:
                if el_error > 0:
                    if self._last_el_cmd != "DOWN" or (now_ts - self._last_el_cmd_ts) >= self._move_refresh_interval:
                        self.thread_manager.run_coro("AxisCoreLoop", self.axis_client_qt.axisClient.move_down, timeout=1.0)
                        self.axis_client_qt.axisClient.axis_status["elevation"] = AxisStatus.MOTION_EL_DOWN
                        self._last_el_cmd = "DOWN"
                        self._last_el_cmd_ts = now_ts
                else:
                    if self._last_el_cmd != "UP" or (now_ts - self._last_el_cmd_ts) >= self._move_refresh_interval:
                        self.thread_manager.run_coro("AxisCoreLoop", self.axis_client_qt.axisClient.move_up, timeout=1.0)
                        self.axis_client_qt.axisClient.axis_status["elevation"] = AxisStatus.MOTION_EL_UP
                        self._last_el_cmd = "UP"
                        self._last_el_cmd_ts = now_ts
            else:
                if self._last_el_cmd != "STOP" or (now_ts - self._last_el_cmd_ts) >= self._move_refresh_interval:
                    self.thread_manager.run_coro("AxisCoreLoop", self.axis_client_qt.axisClient.stop_el, timeout=1.0)
                    self.axis_client_qt.axisClient.axis_status["elevation"] = AxisStatus.MOTION_EL_STOP
                    self._last_el_cmd = "STOP"
                    self._last_el_cmd_ts = now_ts
        except Exception:
            pass

    def _stop_motors(self) -> None:
        try:
            self.thread_manager.run_coro("AxisCoreLoop", self.axis_client_qt.axisClient.stop_az)
            self.thread_manager.run_coro("AxisCoreLoop", self.axis_client_qt.axisClient.stop_el)
            self.axis_client_qt.axisClient.axis_status["azimuth"] = AxisStatus.MOTION_AZ_STOP
            self.axis_client_qt.axisClient.axis_status["elevation"] = AxisStatus.MOTION_EL_STOP
            self._last_az_cmd = "STOP"
            self._last_el_cmd = "STOP"
            self._last_az_cmd_ts = time.monotonic()
            self._last_el_cmd_ts = self._last_az_cmd_ts
        except Exception:
            pass

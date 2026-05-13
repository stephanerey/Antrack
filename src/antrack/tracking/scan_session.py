"""Scan orchestration driven by ThreadManager-compatible background execution."""

from __future__ import annotations

import csv
import inspect
import logging
import threading
import time
from pathlib import Path
from typing import Callable, Optional

from PyQt5.QtCore import QObject, pyqtSignal

from antrack.tracking.scan_cross import estimate_cross_offset, generate_cross_points
from antrack.tracking.scan_grid import generate_grid_points
from antrack.tracking.scan_peak import estimate_four_point_divergence_peak
from antrack.tracking.scan_results import make_peak_estimate, make_scan_result, make_scan_sample
from antrack.tracking.scan_spiral import generate_spiral_points, spiral_samples_to_grid


class ScanSession(QObject):
    """Coordinate antenna moves and SDR measurements for a scan strategy."""

    progress_updated = pyqtSignal(dict)
    point_measured = pyqtSignal(dict)
    completed = pyqtSignal(dict)
    state_changed = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(
        self,
        *,
        thread_manager=None,
        move_to: Optional[Callable[[float, float], None]] = None,
        measure: Optional[Callable[[dict], float]] = None,
        wait_for_settle: Optional[Callable[[float, float, float], None]] = None,
        center_provider: Optional[Callable[[], object]] = None,
        telemetry_provider: Optional[Callable[[], object]] = None,
        logger: Optional[logging.Logger] = None,
        export_dir: Optional[Path] = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.thread_manager = thread_manager
        self.move_to = move_to
        self.measure = measure
        self.wait_for_settle = wait_for_settle
        self.center_provider = center_provider
        self.telemetry_provider = telemetry_provider
        self.logger = logger or logging.getLogger("ScanSession")
        self.export_dir = export_dir
        self._thread_name = "ScanSession"
        self._pause_event = threading.Event()
        self._pause_event.set()
        self._stop_event = threading.Event()
        self._latest_result = None

    @property
    def latest_result(self):
        return self._latest_result

    def start(self, config: dict) -> None:
        self._pause_event.set()
        self._stop_event.clear()
        self.state_changed.emit("running")
        if self.thread_manager is not None:
            self.thread_manager.start_thread(self._thread_name, self._run, config)
        else:
            thread = threading.Thread(target=self._run, args=(config,), name=self._thread_name, daemon=True)
            thread.start()

    def pause(self) -> None:
        self._pause_event.clear()
        self.state_changed.emit("paused")

    def resume(self) -> None:
        self._pause_event.set()
        self.state_changed.emit("running")

    def stop(self) -> None:
        self._stop_event.set()
        self._pause_event.set()
        if self.thread_manager is not None:
            try:
                self.thread_manager.stop_thread(self._thread_name)
            except Exception:
                pass
        self.state_changed.emit("stopped")

    def export_csv(self, samples: list[dict], path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=[
                    "az",
                    "el",
                    "value",
                    "timestamp",
                    "phase",
                    "theoretical_az",
                    "theoretical_el",
                    "offset_az",
                    "offset_el",
                ],
            )
            writer.writeheader()
            for sample in samples:
                writer.writerow(
                    {
                        "az": sample.get("az"),
                        "el": sample.get("el"),
                        "value": sample.get("value"),
                        "timestamp": sample.get("timestamp"),
                        "phase": sample.get("phase"),
                        "theoretical_az": sample.get("theoretical_az"),
                        "theoretical_el": sample.get("theoretical_el"),
                        "offset_az": sample.get("offset_az"),
                        "offset_el": sample.get("offset_el"),
                    }
                )
        return path

    def _wait_if_paused_or_stopped(self) -> None:
        while not self._pause_event.is_set():
            if self._stop_event.is_set():
                raise InterruptedError("Scan stop requested.")
            time.sleep(0.05)
        if self._stop_event.is_set():
            raise InterruptedError("Scan stop requested.")

    @staticmethod
    def _uses_dynamic_center(config: dict) -> bool:
        mode = str(config.get("center_mode", "")).strip().lower()
        return bool(config.get("follow_theoretical_center")) or mode in {
            "dynamic",
            "follow",
            "orbit",
            "theoretical",
            "tracking",
            "tracking_relative",
        }

    @staticmethod
    def _coerce_center(value: object, fallback_az: float, fallback_el: float) -> tuple[float, float]:
        if isinstance(value, dict):
            az = value.get("az", value.get("az_deg", value.get("az_set", fallback_az)))
            el = value.get("el", value.get("el_deg", value.get("el_set", fallback_el)))
            return float(az), float(el)
        if isinstance(value, (tuple, list)) and len(value) >= 2:
            return float(value[0]), float(value[1])
        return float(fallback_az), float(fallback_el)

    def _current_theoretical_center(self, config: dict, fallback_az: float, fallback_el: float) -> tuple[float, float]:
        if not self._uses_dynamic_center(config) or self.center_provider is None:
            return float(fallback_az), float(fallback_el)
        try:
            return self._coerce_center(self.center_provider(), fallback_az, fallback_el)
        except Exception as exc:
            self.logger.warning("Unable to read dynamic scan center: %s", exc)
            return float(fallback_az), float(fallback_el)

    def _materialize_point(self, point: dict, config: dict) -> dict:
        fallback_az = float(config.get("center_az_deg", point.get("az", 0.0)))
        fallback_el = float(config.get("center_el_deg", point.get("el", 0.0)))
        theoretical_az, theoretical_el = self._current_theoretical_center(config, fallback_az, fallback_el)
        materialized = dict(point)
        if self._uses_dynamic_center(config) and "relative_az_deg" in point and "relative_el_deg" in point:
            materialized["az"] = theoretical_az + float(point["relative_az_deg"])
            materialized["el"] = theoretical_el + float(point["relative_el_deg"])
        materialized["theoretical_az"] = theoretical_az
        materialized["theoretical_el"] = theoretical_el
        return materialized

    def _telemetry_snapshot(self) -> dict:
        if self.telemetry_provider is None:
            return {}
        try:
            snapshot = self.telemetry_provider()
        except Exception as exc:
            self.logger.debug("Unable to read scan telemetry snapshot: %s", exc)
            return {}
        if not isinstance(snapshot, dict):
            return {}
        return dict(snapshot)

    @staticmethod
    def _signed_az_delta_deg(value_deg: float, reference_deg: float) -> float:
        return float(((float(value_deg) - float(reference_deg) + 180.0) % 360.0) - 180.0)

    @staticmethod
    def _estimate_peak(samples: list[dict], config: dict) -> dict | None:
        estimator = str(config.get("peak_estimator", "best_sample")).strip().lower()
        if estimator in {"4point", "four_point", "four_point_divergence", "divergence"}:
            return estimate_four_point_divergence_peak(samples)
        return None

    def _measure_point(self, point: dict, config: dict) -> dict:
        self._wait_if_paused_or_stopped()
        point = self._materialize_point(point, config)
        az = float(point["az"])
        el = float(point["el"])
        progress_snapshot = dict(point)
        current = int(config.get("_progress_current", 0))
        total = int(config.get("_progress_total", 0))
        self.progress_updated.emit({"current": current, "total": total, "point": progress_snapshot, "stage": "move"})
        self.logger.info(
            "[ScanPoint] idx=%d/%d target_az=%.3f target_el=%.3f theo_az=%.3f theo_el=%.3f offset_az=%.3f offset_el=%.3f phase=move",
            current,
            total,
            az,
            el,
            float(point.get("theoretical_az", az)),
            float(point.get("theoretical_el", el)),
            float(point.get("relative_az_deg", point.get("offset_az", 0.0))),
            float(point.get("relative_el_deg", point.get("offset_el", 0.0))),
        )
        if self.move_to is not None:
            try:
                self.move_to(point=point, config=config)
            except TypeError as exc:
                try:
                    signature = inspect.signature(self.move_to)
                    signature.bind_partial(point=point, config=config)
                except Exception:
                    self.move_to(az, el)
                else:
                    raise exc
        settle_s = float(config.get("settle_s", 0.2))
        if self.wait_for_settle is not None:
            self.progress_updated.emit({"current": current, "total": total, "point": progress_snapshot, "stage": "settle"})
            try:
                self.wait_for_settle(point=point, config=config, settle_s=settle_s)
            except TypeError as exc:
                try:
                    signature = inspect.signature(self.wait_for_settle)
                    signature.bind_partial(point=point, config=config, settle_s=settle_s)
                except Exception:
                    self.wait_for_settle(az, el, settle_s)
                else:
                    raise exc
        else:
            time.sleep(settle_s)
        if self.measure is None:
            raise RuntimeError("No measure callback configured for ScanSession.")
        self.progress_updated.emit({"current": current, "total": total, "point": progress_snapshot, "stage": "measure"})
        telemetry = self._telemetry_snapshot()
        requested_offset_az = float(point.get("relative_az_deg", point.get("offset_az", 0.0)))
        requested_offset_el = float(point.get("relative_el_deg", point.get("offset_el", 0.0)))
        actual_offset_az = None
        actual_offset_el = None
        offset_error_az = None
        offset_error_el = None
        actual_az = telemetry.get("actual_az")
        actual_el = telemetry.get("actual_el")
        theoretical_az_live = telemetry.get("theoretical_az_live", point.get("theoretical_az"))
        theoretical_el_live = telemetry.get("theoretical_el_live", point.get("theoretical_el"))
        if all(isinstance(value, (int, float)) for value in (actual_az, theoretical_az_live)):
            actual_offset_az = self._signed_az_delta_deg(float(actual_az), float(theoretical_az_live))
            offset_error_az = float(actual_offset_az) - requested_offset_az
        if all(isinstance(value, (int, float)) for value in (actual_el, theoretical_el_live)):
            actual_offset_el = float(actual_el) - float(theoretical_el_live)
            offset_error_el = float(actual_offset_el) - requested_offset_el
        self.logger.info(
            "[ScanPoint] idx=%d/%d target_az=%.3f target_el=%.3f theo_az=%.3f theo_el=%.3f offset_az=%.3f offset_el=%.3f actual_az=%s actual_el=%s set_az=%s set_el=%s actual_offset_az=%s actual_offset_el=%s offset_error_az=%s offset_error_el=%s phase=measure",
            current,
            total,
            az,
            el,
            float(point.get("theoretical_az", az)),
            float(point.get("theoretical_el", el)),
            float(point.get("relative_az_deg", point.get("offset_az", 0.0))),
            float(point.get("relative_el_deg", point.get("offset_el", 0.0))),
            telemetry.get("actual_az"),
            telemetry.get("actual_el"),
            telemetry.get("set_az"),
            telemetry.get("set_el"),
            actual_offset_az,
            actual_offset_el,
            offset_error_az,
            offset_error_el,
        )
        value = float(self.measure(config))
        sample = make_scan_sample(
            point,
            value,
            theoretical_az_deg=float(point.get("theoretical_az", config.get("center_az_deg", az))),
            theoretical_el_deg=float(point.get("theoretical_el", config.get("center_el_deg", el))),
        )
        sample.update(telemetry)
        sample["requested_offset_az"] = requested_offset_az
        sample["requested_offset_el"] = requested_offset_el
        sample["actual_offset_az"] = actual_offset_az
        sample["actual_offset_el"] = actual_offset_el
        sample["offset_error_az"] = offset_error_az
        sample["offset_error_el"] = offset_error_el
        self.point_measured.emit(sample)
        self.logger.info(
            "[ScanPoint] idx=%d/%d value=%.3f target_az=%.3f target_el=%.3f theo_az=%.3f theo_el=%.3f offset_az=%.3f offset_el=%.3f actual_az=%s actual_el=%s actual_offset_az=%s actual_offset_el=%s offset_error_az=%s offset_error_el=%s",
            current,
            total,
            value,
            az,
            el,
            float(point.get("theoretical_az", az)),
            float(point.get("theoretical_el", el)),
            float(point.get("relative_az_deg", point.get("offset_az", 0.0))),
            float(point.get("relative_el_deg", point.get("offset_el", 0.0))),
            telemetry.get("actual_az"),
            telemetry.get("actual_el"),
            actual_offset_az,
            actual_offset_el,
            offset_error_az,
            offset_error_el,
        )
        self.progress_updated.emit({"current": current, "total": total, "point": sample, "stage": "done"})
        return sample

    def _run(self, config: dict) -> None:
        try:
            strategy = str(config.get("strategy", "grid")).strip().lower()
            center_az = float(config.get("center_az_deg", 0.0))
            center_el = float(config.get("center_el_deg", 0.0))
            samples: list[dict] = []

            if strategy == "cross":
                curves = generate_cross_points(
                    center_az,
                    center_el,
                    float(config.get("span_deg", 2.0)),
                    float(config.get("step_deg", 0.5)),
                )
                total = len(curves["azimuth"]) + len(curves["elevation"])
                for index, point in enumerate(curves["azimuth"] + curves["elevation"], start=1):
                    point_config = dict(config)
                    point_config["_progress_current"] = index
                    point_config["_progress_total"] = total
                    sample = self._measure_point(point, point_config)
                    samples.append(sample)
                az_curve = [sample for sample in samples if sample.get("axis") == "az"]
                el_curve = [sample for sample in samples if sample.get("axis") == "el"]
                cross_result = estimate_cross_offset(az_curve, el_curve, center_az, center_el)
                best_point = max(samples, key=lambda point: point["value"])
                peak_point = {
                    "az": center_az + float(cross_result.get("az_offset_deg", 0.0)),
                    "el": center_el + float(cross_result.get("el_offset_deg", 0.0)),
                    "value": float(best_point["value"]),
                    "timestamp": float(best_point["timestamp"]),
                }
                result = make_scan_result(
                    strategy=strategy,
                    samples=samples,
                    center_az_deg=center_az,
                    center_el_deg=center_el,
                    best_point=best_point,
                    peak_estimate=make_peak_estimate(
                        peak_point,
                        method="cross_max",
                        theoretical_az_deg=center_az,
                        theoretical_el_deg=center_el,
                    ),
                )
                result.update(
                    {
                        "best_az_point": cross_result.get("best_az_point"),
                        "best_el_point": cross_result.get("best_el_point"),
                    }
                )
            else:
                if strategy == "spiral":
                    points = generate_spiral_points(
                        center_az,
                        center_el,
                        float(config.get("span_deg", 2.0)),
                        float(config.get("radial_step_deg", config.get("step_deg", 0.25))),
                        turns=int(config.get("turns", 0) or 0) or None,
                    )
                elif strategy == "adaptive":
                    coarse_points = generate_grid_points(
                        center_az,
                        center_el,
                        float(config.get("coarse_span_deg", config.get("span_deg", 2.0))),
                        float(config.get("coarse_span_deg", config.get("span_deg", 2.0))),
                        float(config.get("coarse_step_deg", config.get("step_deg", 0.5))),
                        order=str(config.get("order", "zigzag")),
                        phase="coarse",
                    )
                    total = len(coarse_points)
                    for index, point in enumerate(coarse_points, start=1):
                        point_config = dict(config)
                        point_config["_progress_current"] = index
                        point_config["_progress_total"] = total
                        sample = self._measure_point(point, point_config)
                        samples.append(sample)
                    coarse_best = max(samples, key=lambda point: point["value"])
                    fine_points = generate_grid_points(
                        float(coarse_best["az"]),
                        float(coarse_best["el"]),
                        float(config.get("fine_span_deg", max(0.2, float(config.get("span_deg", 2.0)) / 5.0))),
                        float(config.get("fine_span_deg", max(0.2, float(config.get("span_deg", 2.0)) / 5.0))),
                        float(config.get("fine_step_deg", max(0.05, float(config.get("step_deg", 0.5)) / 5.0))),
                        order=str(config.get("order", "zigzag")),
                        phase="fine",
                    )
                    for point in fine_points:
                        relative_az = float(point["az"]) - center_az
                        relative_el = float(point["el"]) - center_el
                        point["relative_az_deg"] = relative_az
                        point["relative_el_deg"] = relative_el
                        point["scan_offset_az_deg"] = relative_az
                        point["scan_offset_el_deg"] = relative_el
                    start_index = len(samples)
                    total = start_index + len(fine_points)
                    for offset, point in enumerate(fine_points, start=1):
                        point_config = dict(config)
                        point_config["_progress_current"] = start_index + offset
                        point_config["_progress_total"] = total
                        sample = self._measure_point(point, point_config)
                        samples.append(sample)
                    points = []
                else:
                    points = generate_grid_points(
                        center_az,
                        center_el,
                        float(config.get("span_az_deg", config.get("span_deg", 2.0))),
                        float(config.get("span_el_deg", config.get("span_deg", 2.0))),
                        float(config.get("step_deg", 0.5)),
                        order=str(config.get("order", "zigzag")),
                    )

                if strategy in {"grid", "spiral"}:
                    total = len(points)
                    for index, point in enumerate(points, start=1):
                        point_config = dict(config)
                        point_config["_progress_current"] = index
                        point_config["_progress_total"] = total
                        sample = self._measure_point(point, point_config)
                        samples.append(sample)

                result = make_scan_result(
                    strategy=strategy,
                    samples=samples,
                    center_az_deg=center_az,
                    center_el_deg=center_el,
                    peak_estimate=self._estimate_peak(samples, config),
                )
                if strategy == "spiral":
                    result["heatmap"] = spiral_samples_to_grid(samples, float(config.get("grid_step_deg", config.get("step_deg", 0.25))))

            if self.export_dir is not None and config.get("export_name"):
                export_path = self.export_csv(samples, Path(self.export_dir) / str(config["export_name"]))
                result["export_path"] = str(export_path)

            self._latest_result = result
            self.completed.emit(result)
            self.state_changed.emit("completed")
        except InterruptedError:
            self.state_changed.emit("stopped")
        except Exception as exc:
            self.logger.exception("Scan session failed: %s", exc)
            self.error.emit(str(exc))
            self.state_changed.emit("error")

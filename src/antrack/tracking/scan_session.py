"""Scan orchestration driven by ThreadManager-compatible background execution."""

from __future__ import annotations

import csv
import logging
import threading
import time
from pathlib import Path
from typing import Callable, Optional

from PyQt5.QtCore import QObject, pyqtSignal

from antrack.tracking.scan_cross import estimate_cross_offset, generate_cross_points
from antrack.tracking.scan_grid import generate_grid_points
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
        logger: Optional[logging.Logger] = None,
        export_dir: Optional[Path] = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.thread_manager = thread_manager
        self.move_to = move_to
        self.measure = measure
        self.wait_for_settle = wait_for_settle
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
            writer = csv.DictWriter(handle, fieldnames=["az", "el", "value", "timestamp", "phase"])
            writer.writeheader()
            for sample in samples:
                writer.writerow(
                    {
                        "az": sample.get("az"),
                        "el": sample.get("el"),
                        "value": sample.get("value"),
                        "timestamp": sample.get("timestamp"),
                        "phase": sample.get("phase"),
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

    def _measure_point(self, point: dict, config: dict) -> dict:
        self._wait_if_paused_or_stopped()
        az = float(point["az"])
        el = float(point["el"])
        if self.move_to is not None:
            self.move_to(az, el)
        settle_s = float(config.get("settle_s", 0.2))
        if self.wait_for_settle is not None:
            self.wait_for_settle(az, el, settle_s)
        else:
            time.sleep(settle_s)
        if self.measure is None:
            raise RuntimeError("No measure callback configured for ScanSession.")
        value = float(self.measure(config))
        sample = {
            "az": az,
            "el": el,
            "value": value,
            "timestamp": time.time(),
            "phase": point.get("phase", "main"),
            "axis": point.get("axis"),
        }
        self.point_measured.emit(sample)
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
                    sample = self._measure_point(point, config)
                    samples.append(sample)
                    self.progress_updated.emit({"current": index, "total": total, "point": sample})
                az_curve = [sample for sample in samples if sample.get("axis") == "az"]
                el_curve = [sample for sample in samples if sample.get("axis") == "el"]
                result = estimate_cross_offset(az_curve, el_curve, center_az, center_el)
                result.update({"strategy": strategy, "samples": samples, "best_point": max(samples, key=lambda point: point["value"])})
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
                        sample = self._measure_point(point, config)
                        samples.append(sample)
                        self.progress_updated.emit({"current": index, "total": total, "point": sample})
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
                    start_index = len(samples)
                    total = start_index + len(fine_points)
                    for offset, point in enumerate(fine_points, start=1):
                        sample = self._measure_point(point, config)
                        samples.append(sample)
                        self.progress_updated.emit({"current": start_index + offset, "total": total, "point": sample})
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
                        sample = self._measure_point(point, config)
                        samples.append(sample)
                        self.progress_updated.emit({"current": index, "total": total, "point": sample})

                best_point = max(samples, key=lambda point: point["value"])
                result = {
                    "strategy": strategy,
                    "samples": samples,
                    "best_point": best_point,
                    "az_offset_deg": float(best_point["az"]) - center_az,
                    "el_offset_deg": float(best_point["el"]) - center_el,
                }
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

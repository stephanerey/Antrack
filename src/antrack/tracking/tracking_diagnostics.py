from __future__ import annotations

import csv
import logging
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable

from antrack.utils.paths import get_logs_dir


TRACKING_DIAGNOSTIC_COLUMNS = [
    "timestamp_iso",
    "monotonic_s",
    "backend_name",
    "connection_mode",
    "axis",
    "loop_dt_s",
    "expected_loop_interval_s",
    "telemetry_age_s",
    "target_deg",
    "actual_deg",
    "error_deg",
    "abs_error_deg",
    "threshold_deg",
    "approach_deg",
    "close_deg",
    "current_axis_state",
    "last_axis_command",
    "decision",
    "command_to_send",
    "command_reason",
    "speed_requested",
    "az_setrate",
    "el_setrate",
    "move_refresh_interval_s",
    "min_move_duration_s",
    "command_start_monotonic_s",
    "command_end_monotonic_s",
    "command_latency_s",
    "command_result",
    "command_exception",
    "position_last_update_monotonic_s",
    "status_last_update_monotonic_s",
    "backend_state",
    "backend_last_error",
    "thread_name",
    "worker_abort",
    "position_poller_running",
    "status_poller_running",
    "tracking_loop_active_count",
    "backend_diag_requests",
    "backend_diag_fc03",
    "backend_diag_fc06",
    "backend_diag_failures",
    "backend_diag_timeouts",
    "backend_diag_latency_last_s",
    "backend_diag_latency_min_s",
    "backend_diag_latency_avg_s",
    "backend_diag_latency_max_s",
]


@dataclass(frozen=True)
class TrackingDiagnosticsConfig:
    enabled: bool = False
    log_to_csv: bool = True
    log_to_console: bool = False
    csv_prefix: str = "tracking_diagnostics"
    include_backend_transactions: bool = True
    rate_limit_warnings_s: float = 1.0


def _to_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def load_tracking_diagnostics_config(settings: dict[str, Any] | None) -> TrackingDiagnosticsConfig:
    if not isinstance(settings, dict):
        return TrackingDiagnosticsConfig()
    section = settings.get("TRACKING_DIAGNOSTICS", settings.get("tracking_diagnostics", {})) or {}
    if not isinstance(section, dict):
        return TrackingDiagnosticsConfig()
    enabled = _to_bool(section.get("ENABLED", section.get("enabled")), False)
    if not enabled:
        return TrackingDiagnosticsConfig()
    return TrackingDiagnosticsConfig(
        enabled=True,
        log_to_csv=_to_bool(section.get("LOG_TO_CSV", section.get("log_to_csv")), True),
        log_to_console=_to_bool(section.get("LOG_TO_CONSOLE", section.get("log_to_console")), False),
        csv_prefix=str(section.get("CSV_PREFIX", section.get("csv_prefix", "tracking_diagnostics")) or "tracking_diagnostics"),
        include_backend_transactions=_to_bool(
            section.get("INCLUDE_BACKEND_TRANSACTIONS", section.get("include_backend_transactions")),
            True,
        ),
        rate_limit_warnings_s=max(
            0.1,
            float(section.get("RATE_LIMIT_WARNINGS_S", section.get("rate_limit_warnings_s", 1.0)) or 1.0),
        ),
    )


def compute_telemetry_age(now_monotonic: float, last_update_monotonic: float | None) -> float | None:
    if last_update_monotonic is None:
        return None
    return max(0.0, float(now_monotonic) - float(last_update_monotonic))


def measure_command_latency(
    command_name: str,
    func: Callable[[], Any],
    recorder: Callable[[dict[str, Any]], None],
    *,
    clock: Callable[[], float] = time.monotonic,
) -> Any:
    start = float(clock())
    result = None
    exception = None
    try:
        result = func()
        return result
    except Exception as exc:
        exception = exc
        raise
    finally:
        end = float(clock())
        recorder(
            {
                "command_name": command_name,
                "command_start_monotonic_s": start,
                "command_end_monotonic_s": end,
                "command_latency_s": max(0.0, end - start),
                "command_result": result,
                "command_exception": str(exception) if exception is not None else None,
            }
        )


class TrackingDiagnosticsCsvLogger:
    def __init__(
        self,
        config: TrackingDiagnosticsConfig,
        *,
        log_dir: Path | None = None,
        columns: Iterable[str] | None = None,
    ) -> None:
        self.config = config
        self.log_dir = Path(log_dir or get_logs_dir())
        self.columns = list(columns or TRACKING_DIAGNOSTIC_COLUMNS)
        self.path: Path | None = None
        self._writer = None
        self._file_handle = None

    def log_row(self, row: dict[str, Any]) -> None:
        if not (self.config.enabled and self.config.log_to_csv):
            return
        if self._writer is None:
            self.log_dir.mkdir(parents=True, exist_ok=True)
            filename = f"{self.config.csv_prefix}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
            self.path = self.log_dir / filename
            self._file_handle = self.path.open("w", newline="", encoding="utf-8")
            self._writer = csv.DictWriter(self._file_handle, fieldnames=self.columns, extrasaction="ignore")
            self._writer.writeheader()
        payload = {column: row.get(column) for column in self.columns}
        self._writer.writerow(payload)
        self._file_handle.flush()

    def close(self) -> None:
        if self._file_handle is not None:
            self._file_handle.close()
        self._file_handle = None
        self._writer = None


class RateLimitedWarningLogger:
    def __init__(self, logger: logging.Logger, interval_s: float) -> None:
        self.logger = logger
        self.interval_s = max(0.1, float(interval_s))
        self._last_emitted: dict[str, float] = {}

    def warning(self, key: str, message: str, *args: Any) -> bool:
        now = time.monotonic()
        last = self._last_emitted.get(key)
        if last is not None and (now - last) < self.interval_s:
            return False
        self._last_emitted[key] = now
        self.logger.warning(message, *args)
        return True


class TrackingDiagnosticsSession:
    def __init__(
        self,
        config: TrackingDiagnosticsConfig,
        *,
        logger: logging.Logger | None = None,
        log_dir: Path | None = None,
    ) -> None:
        self.config = config
        self.logger = logger or logging.getLogger("TrackingDiagnostics")
        self.csv_logger = TrackingDiagnosticsCsvLogger(config, log_dir=log_dir)
        self.warning_logger = RateLimitedWarningLogger(self.logger, config.rate_limit_warnings_s)

    @property
    def enabled(self) -> bool:
        return bool(self.config.enabled)

    def emit_rows(self, rows: Iterable[dict[str, Any]]) -> None:
        if not self.enabled:
            return
        for row in rows:
            self.csv_logger.log_row(row)
            if self.config.log_to_console:
                self.logger.info("Tracking diagnostics row: %s", row)

    def warning(self, key: str, message: str, *args: Any) -> bool:
        if not self.enabled:
            return False
        return self.warning_logger.warning(key, message, *args)

    def close(self) -> None:
        self.csv_logger.close()

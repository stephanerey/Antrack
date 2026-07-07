from pathlib import Path

import pytest

from antrack.tracking.tracking_diagnostics import (
    TRACKING_DIAGNOSTIC_COLUMNS,
    TrackingDiagnosticsConfig,
    TrackingDiagnosticsCsvLogger,
    compute_telemetry_age,
    load_tracking_diagnostics_config,
    measure_command_latency,
)


def test_tracking_diagnostics_config_absent_is_disabled():
    config = load_tracking_diagnostics_config({})

    assert config.enabled is False
    assert config.csv_prefix == "tracking_diagnostics"


def test_tracking_diagnostics_config_enabled_parses_prefix():
    config = load_tracking_diagnostics_config(
        {
            "TRACKING_DIAGNOSTICS": {
                "ENABLED": True,
                "CSV_PREFIX": "diag_custom",
                "LOG_TO_CSV": True,
                "CSV_FLUSH_EVERY_ROWS": 12,
                "CSV_FLUSH_INTERVAL_S": 2.5,
            }
        }
    )

    assert config.enabled is True
    assert config.csv_prefix == "diag_custom"
    assert config.log_to_csv is True
    assert config.csv_flush_every_rows == 12
    assert config.csv_flush_interval_s == pytest.approx(2.5)


def test_tracking_diagnostics_csv_logger_creates_file_and_header(tmp_path: Path):
    logger = TrackingDiagnosticsCsvLogger(
        TrackingDiagnosticsConfig(enabled=True, log_to_csv=True),
        log_dir=tmp_path,
    )

    logger.log_row({"timestamp_iso": "2026-07-07T12:00:00", "axis": "AZ", "decision": "MOVE"})
    logger.close()

    files = list(tmp_path.glob("tracking_diagnostics_*.csv"))
    assert len(files) == 1
    content = files[0].read_text(encoding="utf-8")
    assert TRACKING_DIAGNOSTIC_COLUMNS[0] in content
    assert "MOVE" in content


def test_tracking_diagnostics_disabled_creates_no_file(tmp_path: Path):
    logger = TrackingDiagnosticsCsvLogger(
        TrackingDiagnosticsConfig(enabled=False, log_to_csv=True),
        log_dir=tmp_path,
    )

    logger.log_row({"timestamp_iso": "2026-07-07T12:00:00"})
    logger.close()

    assert list(tmp_path.iterdir()) == []


def test_tracking_diagnostics_csv_logger_flushes_buffer_on_close(tmp_path: Path):
    logger = TrackingDiagnosticsCsvLogger(
        TrackingDiagnosticsConfig(
            enabled=True,
            log_to_csv=True,
            csv_flush_every_rows=999,
            csv_flush_interval_s=3600.0,
        ),
        log_dir=tmp_path,
    )

    logger.log_row({"timestamp_iso": "2026-07-07T12:00:01", "axis": "EL", "decision": "HOLD"})
    logger.close()

    files = list(tmp_path.glob("tracking_diagnostics_*.csv"))
    assert len(files) == 1
    content = files[0].read_text(encoding="utf-8")
    assert "HOLD" in content


def test_measure_command_latency_preserves_result_and_records_latency():
    records = []

    result = measure_command_latency(
        "move_cw",
        lambda: 123,
        records.append,
        clock=iter([10.0, 10.2]).__next__,
    )

    assert result == 123
    assert records[0]["command_name"] == "move_cw"
    assert records[0]["command_latency_s"] == pytest.approx(0.2)


def test_measure_command_latency_preserves_exception_behavior():
    records = []

    with pytest.raises(RuntimeError, match="boom"):
        measure_command_latency(
            "stop_az",
            lambda: (_ for _ in ()).throw(RuntimeError("boom")),
            records.append,
            clock=iter([20.0, 20.3]).__next__,
        )

    assert records[0]["command_exception"] == "boom"
    assert records[0]["command_latency_s"] == pytest.approx(0.3)


def test_compute_telemetry_age_handles_timestamp_and_missing_value():
    assert compute_telemetry_age(12.5, 12.0) == pytest.approx(0.5)
    assert compute_telemetry_age(12.5, None) is None

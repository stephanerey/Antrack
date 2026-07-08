from __future__ import annotations

from antrack.core.antenna.types import AntennaConnectionMode

DEFAULT_MOVE_REFRESH_INTERVAL_S = 1.0


def _section(settings: dict | None, *names: str) -> dict:
    if not isinstance(settings, dict):
        return {}
    for name in names:
        value = settings.get(name)
        if isinstance(value, dict):
            return value
    return {}


def _to_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def configured_move_refresh_interval(settings: dict | None, default_s: float = DEFAULT_MOVE_REFRESH_INTERVAL_S) -> float:
    perf = _section(settings, "PERFORMANCE", "performance")
    interval_s = float(default_s)
    if _to_bool(perf.get("cpu_optimized", perf.get("CPU_OPTIMIZED", False))):
        configured = perf.get("move_refresh_interval", perf.get("MOVE_REFRESH_INTERVAL", interval_s))
        interval_s = float(configured)
    return float(max(0.0, interval_s))


def _connection_mode_value(axis_client_qt) -> str | None:
    getter = getattr(axis_client_qt, "current_mode", None)
    if callable(getter):
        try:
            mode = getter()
        except Exception:
            mode = None
        raw_value = getattr(mode, "value", mode)
        if raw_value is not None:
            return str(raw_value).strip().lower()
    return None


def axis_driver_motion_refresh_policy(settings: dict | None) -> tuple[str, float | None]:
    axis_driver = _section(settings, "AXIS_DRIVER", "axis_driver")
    mode = str(axis_driver.get("move_refresh_mode", axis_driver.get("MOVE_REFRESH_MODE", "edge_only"))).strip().lower()
    if mode not in {"edge_only", "interval"}:
        mode = "edge_only"
    refresh_value = axis_driver.get("move_refresh_interval_s", axis_driver.get("MOVE_REFRESH_INTERVAL_S", 0.0))
    try:
        refresh_interval_s = float(refresh_value)
    except (TypeError, ValueError):
        refresh_interval_s = 0.0
    if mode == "edge_only" or refresh_interval_s <= 0.0:
        return mode, None
    return mode, float(max(0.05, refresh_interval_s))


def effective_motion_refresh_interval(
    axis_client_qt,
    settings: dict | None,
    default_s: float = DEFAULT_MOVE_REFRESH_INTERVAL_S,
) -> float | None:
    interval_s = configured_move_refresh_interval(settings, default_s=default_s)
    if getattr(axis_client_qt, "supports_absolute_targets", lambda: False)():
        return interval_s

    mode_value = _connection_mode_value(axis_client_qt)
    if mode_value != AntennaConnectionMode.AXIS_DRIVER.value:
        return interval_s

    _mode, axis_driver_interval_s = axis_driver_motion_refresh_policy(settings)
    return axis_driver_interval_s


def _axis_driver_edge_only(axis_client_qt, settings: dict | None) -> bool:
    mode_value = _connection_mode_value(axis_client_qt)
    if mode_value != AntennaConnectionMode.AXIS_DRIVER.value:
        return False
    mode, interval_s = axis_driver_motion_refresh_policy(settings)
    return mode == "edge_only" or interval_s is None


def should_emit_move(
    axis_client_qt,
    settings: dict | None,
    *,
    last_cmd: str,
    desired_cmd: str,
    elapsed_s: float,
    default_refresh_interval_s: float,
) -> tuple[bool, str, str]:
    if desired_cmd == "STOP":
        return False, "HOLD", "desired motion is STOP"
    if _axis_driver_edge_only(axis_client_qt, settings):
        if last_cmd == desired_cmd:
            return False, "HOLD_EDGE_ONLY", "same AxisDriver motion state, MOVE refresh disabled"
        return True, "MOVE", "AxisDriver motion state transition"

    refresh_interval_s = effective_motion_refresh_interval(
        axis_client_qt,
        settings,
        default_s=default_refresh_interval_s,
    )
    if last_cmd != desired_cmd or refresh_interval_s is None or elapsed_s >= refresh_interval_s:
        return True, "MOVE", "motion changed or refresh interval reached"
    return False, "HOLD", "same motion state and refresh interval not reached"


def should_emit_stop(
    axis_client_qt,
    settings: dict | None,
    *,
    last_cmd: str,
    elapsed_s: float,
    default_refresh_interval_s: float,
) -> tuple[bool, str, str]:
    if _axis_driver_edge_only(axis_client_qt, settings):
        if last_cmd == "STOP":
            return False, "HOLD_EDGE_ONLY", "same AxisDriver STOP state, STOP refresh disabled"
        return True, "STOP", "AxisDriver STOP transition"

    refresh_interval_s = effective_motion_refresh_interval(
        axis_client_qt,
        settings,
        default_s=default_refresh_interval_s,
    )
    if last_cmd != "STOP" or refresh_interval_s is None or elapsed_s >= refresh_interval_s:
        return True, "STOP", "within threshold or blocked"
    return False, "HOLD", "same STOP state and refresh interval not reached"

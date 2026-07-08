from types import SimpleNamespace

from antrack.core.antenna.types import AntennaConnectionMode
from antrack.tracking.motion_refresh import (
    DEFAULT_MOVE_REFRESH_INTERVAL_S,
    configured_move_refresh_interval,
    effective_motion_refresh_interval,
    should_emit_move,
    should_emit_stop,
)


def _client(*, mode=None, absolute=False):
    return SimpleNamespace(
        current_mode=lambda: mode,
        supports_absolute_targets=lambda: absolute,
    )


def test_configured_move_refresh_interval_defaults_to_one_second():
    assert configured_move_refresh_interval({}) == DEFAULT_MOVE_REFRESH_INTERVAL_S


def test_configured_move_refresh_interval_uses_cpu_optimized_override():
    settings = {
        "PERFORMANCE": {
            "cpu_optimized": True,
            "move_refresh_interval": 0.4,
        }
    }

    assert configured_move_refresh_interval(settings) == 0.4


def test_axis_driver_edge_only_refresh_disables_periodic_refresh_interval():
    settings = {
        "AXIS_DRIVER": {
            "move_refresh_mode": "edge_only",
            "move_refresh_interval_s": 0.0,
        }
    }

    interval = effective_motion_refresh_interval(
        _client(mode=AntennaConnectionMode.AXIS_DRIVER),
        settings,
    )

    assert interval is None


def test_axis_driver_edge_only_move_policy_emits_only_on_state_change():
    client = _client(mode=AntennaConnectionMode.AXIS_DRIVER)
    settings = {"AXIS_DRIVER": {"move_refresh_mode": "edge_only", "move_refresh_interval_s": 0.0}}

    assert should_emit_move(
        client,
        settings,
        last_cmd="STOP",
        desired_cmd="CW",
        elapsed_s=2.0,
        default_refresh_interval_s=1.0,
    ) == (True, "MOVE", "AxisDriver motion state transition")

    assert should_emit_move(
        client,
        settings,
        last_cmd="CW",
        desired_cmd="CW",
        elapsed_s=2.0,
        default_refresh_interval_s=1.0,
    ) == (False, "HOLD_EDGE_ONLY", "same AxisDriver motion state, MOVE refresh disabled")

    assert should_emit_move(
        client,
        settings,
        last_cmd="CW",
        desired_cmd="CCW",
        elapsed_s=0.01,
        default_refresh_interval_s=1.0,
    ) == (True, "MOVE", "AxisDriver motion state transition")


def test_axis_driver_edge_only_stop_policy_emits_only_on_transition():
    client = _client(mode=AntennaConnectionMode.AXIS_DRIVER)
    settings = {"AXIS_DRIVER": {"move_refresh_mode": "edge_only", "move_refresh_interval_s": 0.0}}

    assert should_emit_stop(
        client,
        settings,
        last_cmd="CW",
        elapsed_s=0.01,
        default_refresh_interval_s=1.0,
    ) == (True, "STOP", "AxisDriver STOP transition")

    assert should_emit_stop(
        client,
        settings,
        last_cmd="STOP",
        elapsed_s=2.0,
        default_refresh_interval_s=1.0,
    ) == (False, "HOLD_EDGE_ONLY", "same AxisDriver STOP state, STOP refresh disabled")


def test_absolute_target_backend_keeps_configured_motion_refresh_interval():
    settings = {
        "PERFORMANCE": {
            "cpu_optimized": True,
            "move_refresh_interval": 0.3,
        },
        "AXIS_DRIVER": {
            "move_refresh_mode": "edge_only",
            "move_refresh_interval_s": 0.0,
        },
    }

    interval = effective_motion_refresh_interval(
        _client(mode=AntennaConnectionMode.PST_ROTATOR, absolute=True),
        settings,
    )

    assert interval == 0.3

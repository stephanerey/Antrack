from antrack.core.antenna.operational_status import (
    AxisIndexState,
    AxisOperationalState,
    decode_axis_index_state,
    decode_axis_operational_status,
    evaluate_tracking_permission,
    status_stale_timeout,
)
from antrack.gui.connection_ui import ConnectionUiMixin
from antrack.gui.tracking_ui import TrackingUiMixin
from antrack.tracking.tracking import Tracker


def _status(axis: str, *, endstop=0, alarm=0, modbus=1, updated=99.0, now=100.0):
    return decode_axis_operational_status(
        axis,
        endstop=endstop,
        motor_alarm=alarm,
        modbus_status=modbus,
        updated_monotonic=updated,
        updated_timestamp=1_700_000_000.0,
        now_monotonic=now,
        stale_timeout_s=2.0,
    )


def _permission(az_index, el_index, az_status=None, el_status=None):
    return evaluate_tracking_permission(
        "axis_driver",
        az_index=az_index,
        el_index=el_index,
        az_status=az_status or _status("AZ"),
        el_status=el_status or _status("EL"),
    )


def test_axis_operational_status_decodes_normal_alarm_and_unknown():
    assert _status("AZ").state == AxisOperationalState.OK

    alarm = _status("EL", endstop=4, alarm=20)
    assert alarm.state == AxisOperationalState.ALARM
    assert alarm.active_flags == (
        "Endstop active (raw code 4)",
        "Motor alarm active (raw code 20)",
    )

    assert _status("AZ", modbus=2).state == AxisOperationalState.UNKNOWN
    assert _status("EL", updated=90.0).state == AxisOperationalState.UNKNOWN
    assert _status("EL", alarm=None).state == AxisOperationalState.UNKNOWN


def test_axis_index_state_requires_fresh_status_and_session_reference():
    assert decode_axis_index_state(None, referenced_latched=False, status_is_fresh=True) == AxisIndexState.UNKNOWN
    assert decode_axis_index_state(0, referenced_latched=False, status_is_fresh=True) == AxisIndexState.NOT_INDEXED
    assert decode_axis_index_state(1, referenced_latched=False, status_is_fresh=True) == AxisIndexState.INDEXED
    assert decode_axis_index_state(2, referenced_latched=False, status_is_fresh=True) == AxisIndexState.INDEXED
    assert decode_axis_index_state(0, referenced_latched=True, status_is_fresh=True) == AxisIndexState.INDEXED
    assert decode_axis_index_state(1, referenced_latched=True, status_is_fresh=False) == AxisIndexState.UNKNOWN


def test_tracking_permission_allows_only_two_indexed_healthy_axes():
    allowed = _permission(AxisIndexState.INDEXED, AxisIndexState.INDEXED)
    assert allowed.allowed is True
    assert allowed.reasons == ()

    assert _permission(AxisIndexState.NOT_INDEXED, AxisIndexState.INDEXED).allowed is False
    assert _permission(AxisIndexState.INDEXED, AxisIndexState.NOT_INDEXED).allowed is False
    assert _permission(AxisIndexState.UNKNOWN, AxisIndexState.INDEXED).allowed is False
    assert _permission(AxisIndexState.INDEXED, AxisIndexState.UNKNOWN).allowed is False
    assert _permission(
        AxisIndexState.INDEXED,
        AxisIndexState.INDEXED,
        az_status=_status("AZ", updated=90.0),
    ).allowed is False
    assert _permission(
        AxisIndexState.INDEXED,
        AxisIndexState.INDEXED,
        el_status=_status("EL", alarm=7),
    ).allowed is False


def test_tracking_permission_reports_multiple_causes_once():
    permission = _permission(
        AxisIndexState.NOT_INDEXED,
        AxisIndexState.UNKNOWN,
        az_status=_status("AZ", alarm=3),
        el_status=_status("EL", updated=90.0),
    )

    assert permission.allowed is False
    assert permission.reasons == (
        "AZ axis not indexed",
        "EL index state unknown",
        "AZ axis alarm: Motor alarm active (raw code 3)",
        "EL status unknown",
    )
    assert permission.message().count("Tracking unavailable:") == 1


def test_non_axis_backend_keeps_existing_tracking_permission():
    permission = evaluate_tracking_permission(
        "pst_rotator",
        az_index=AxisIndexState.UNKNOWN,
        el_index=AxisIndexState.UNKNOWN,
        az_status=_status("AZ", updated=90.0),
        el_status=_status("EL", updated=90.0),
    )
    assert permission.allowed is True


def test_axis_server_uses_the_same_tracking_safety_gate():
    permission = evaluate_tracking_permission(
        "axis_server",
        az_index=AxisIndexState.NOT_INDEXED,
        el_index=AxisIndexState.INDEXED,
        az_status=_status("AZ"),
        el_status=_status("EL"),
    )
    assert permission.allowed is False
    assert permission.reasons == ("AZ axis not indexed",)


def test_status_stale_timeout_uses_multiple_poll_periods():
    assert status_stale_timeout(0.25) == 2.0
    assert status_stale_timeout(1.0) == 4.0


def test_central_start_gate_rejects_internal_tracking_start():
    class Harness:
        _auto_restart_tracking = True

        def __init__(self):
            self.validations = 0

        def validate_tracking_start(self, *, show_message=True):
            self.validations += 1
            assert show_message is False
            return False

    harness = Harness()

    TrackingUiMixin._start_tracker_when_ready(harness)

    assert harness.validations == 1
    assert harness._auto_restart_tracking is False


def test_invalid_status_stops_active_tracking_once_without_auto_restart():
    class Tracker:
        running = True

        def is_running(self):
            return self.running

    class StatusBar:
        def __init__(self):
            self.messages = []

        def showMessage(self, message, _duration):
            self.messages.append(message)

    class Logger:
        def warning(self, _message):
            pass

    class Harness:
        def __init__(self):
            self.tracker = Tracker()
            self.status_bar = StatusBar()
            self.logger = Logger()
            self._auto_restart_tracking = True
            self._last_tracking_inhibit_signature = None
            self.stop_calls = 0

        def _stop_tracking_loop_from_ui(self):
            self.stop_calls += 1
            self.tracker.running = False

    permission = _permission(
        AxisIndexState.NOT_INDEXED,
        AxisIndexState.INDEXED,
    )
    harness = Harness()

    ConnectionUiMixin._handle_active_tracking_permission(harness, permission)
    ConnectionUiMixin._handle_active_tracking_permission(harness, permission)

    assert harness.stop_calls == 1
    assert harness._auto_restart_tracking is False
    assert len(harness.status_bar.messages) == 1


def test_tracker_worker_cannot_bypass_cached_safety_inhibit():
    class Manager:
        def __init__(self):
            self.registrations = 0

        def register_tracker(self, _tracker):
            self.registrations += 1

    tracker = Tracker.__new__(Tracker)
    tracker.axis_client_qt = type(
        "Client",
        (),
        {
            "tracking_permission_allowed": False,
            "tracking_permission_reasons": ("AZ axis not indexed",),
        },
    )()
    tracker.tracking_manager = Manager()
    tracker.thread_manager = None

    tracker.start()
    tracker.step()

    assert tracker.tracking_manager.registrations == 0

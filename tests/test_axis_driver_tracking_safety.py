import time
from types import SimpleNamespace

from antrack.core.antenna.types import AntennaConnectionMode
from antrack.tracking.tracking import Tracker, TrackedObject


class DummyThreadManager:
    def __init__(self):
        self.tracking_manager = None
        self.threads = {}

    def get_worker(self, _name):
        return None


class AxisDriverClient:
    def __init__(self):
        now = time.monotonic()
        self.antenna = SimpleNamespace(
            az=10.0,
            el=20.0,
            az_setrate=0.0,
            el_setrate=0.0,
            az_rate=0.0,
            el_rate=0.0,
            endstop_az=0,
            endstop_el=0,
            last_update_monotonic=now,
        )
        self.axis_status = {"azimuth": "STOP", "elevation": "STOP"}
        self.server_status = "CONNECTED"
        self.polling_intervals = (0.1, 0.25)
        self.commands = []

    def current_mode(self):
        return AntennaConnectionMode.AXIS_DRIVER

    def supports_absolute_targets(self):
        return False

    def set_az_speed(self, speed):
        self.commands.append(("set_az_speed", speed))
        return speed

    def set_el_speed(self, speed):
        self.commands.append(("set_el_speed", speed))
        return speed

    def move_cw(self):
        self.commands.append("move_cw")
        return 100

    def move_ccw(self):
        self.commands.append("move_ccw")
        return 101

    def move_up(self):
        self.commands.append("move_up")
        return 100

    def move_down(self):
        self.commands.append("move_down")
        return 101

    def stop_az(self):
        self.commands.append("stop_az")
        return 10

    def stop_el(self):
        self.commands.append("stop_el")
        return 10


def _tracked_object():
    tracked = TrackedObject()
    tracked.az_set = 50.0
    tracked.el_set = 30.0
    return tracked


def _tracking_settings():
    return {
        "ANTENNA": {
            "az_tracking_error_threshold": 0.05,
            "el_tracking_error_threshold": 0.05,
            "az_approach_error_threshold": 5,
            "el_approach_error_threshold": 5,
            "az_close_error_threshold": 1,
            "el_close_error_threshold": 1,
            "az_speed_far_tracking": 500,
            "az_speed_approach_tracking": 100,
            "az_speed_close_tracking": 10,
            "el_speed_far_tracking": 500,
            "el_speed_approach_tracking": 100,
            "el_speed_close_tracking": 10,
            "az_forbidden_ranges": "",
            "el_forbidden_ranges": "",
        }
    }


def test_tracker_axis_driver_stale_telemetry_safety_stop():
    client = AxisDriverClient()
    client.antenna.last_update_monotonic = time.monotonic() - 2.0
    client.axis_status["azimuth"] = "CW"
    client.axis_status["elevation"] = "UP"
    tracker = Tracker(client, settings={}, thread_manager=DummyThreadManager(), tracked_object=_tracked_object())
    tracker._last_az_cmd = "CW"
    tracker._last_el_cmd = "UP"
    tracker._stop_motors = lambda force=False: client.commands.append("stale_stop") or {"AZ": [], "EL": []}

    tracker.step()

    assert "stale_stop" in client.commands


def test_tracker_axis_driver_tolerates_short_position_poll_delay_while_moving():
    client = AxisDriverClient()
    client.antenna.last_update_monotonic = time.monotonic() - 0.6
    client.axis_status["azimuth"] = "CW"
    client.axis_status["elevation"] = "UP"
    tracker = Tracker(client, settings=_tracking_settings(), thread_manager=DummyThreadManager(), tracked_object=_tracked_object())
    tracker._kickstart_pending = False
    tracker._must_apply_speeds = False
    tracker._last_az_cmd = "CW"
    tracker._last_el_cmd = "UP"
    tracker._stop_motors = lambda force=False: client.commands.append("stale_stop") or {"AZ": [], "EL": []}

    tracker.step()

    assert "stale_stop" not in client.commands


def test_tracker_axis_driver_endstop_stop_on_affected_axis():
    client = AxisDriverClient()
    client.antenna.endstop_az = 1
    tracker = Tracker(client, settings={}, thread_manager=DummyThreadManager(), tracked_object=_tracked_object())
    tracker._last_az_cmd = "CW"
    tracker._last_az_cmd_ts = time.monotonic() - 1.0

    tracker.step()

    assert "stop_az" in client.commands
    assert "move_cw" not in client.commands


def test_tracker_reapplies_far_speed_when_internal_rate_bucket_changes_from_close():
    client = AxisDriverClient()
    client.antenna.az = 0.0
    client.antenna.el = 0.0
    client.antenna.az_setrate = 500.0
    client.antenna.el_setrate = 500.0
    tracked = TrackedObject()
    tracked.az_set = 20.0
    tracked.el_set = 20.0
    tracker = Tracker(client, settings=_tracking_settings(), thread_manager=DummyThreadManager(), tracked_object=tracked)
    tracker._kickstart_pending = False
    tracker._must_apply_speeds = False
    tracker._last_az_speed_requested = 10.0
    tracker._last_el_speed_requested = 10.0

    tracker.step()

    assert ("set_az_speed", 500.0) in client.commands
    assert ("set_el_speed", 500.0) in client.commands


def test_tracker_reapplies_far_speed_after_large_target_jump_even_when_rate_cached():
    client = AxisDriverClient()
    client.antenna.az = 0.0
    client.antenna.el = 0.0
    client.antenna.az_setrate = 500.0
    client.antenna.el_setrate = 500.0
    tracked = TrackedObject()
    tracked.az_set = 20.0
    tracked.el_set = 20.0
    tracker = Tracker(client, settings=_tracking_settings(), thread_manager=DummyThreadManager(), tracked_object=tracked)
    tracker._kickstart_pending = False
    tracker._must_apply_speeds = False
    tracker._last_az_speed_requested = 500.0
    tracker._last_el_speed_requested = 500.0
    tracker._last_rate_target = (0.0, 0.0)

    tracker.step()

    assert ("set_az_speed", 500.0) in client.commands
    assert ("set_el_speed", 500.0) in client.commands


def test_tracker_does_not_rewrite_speed_for_axis_already_on_target():
    client = AxisDriverClient()
    client.antenna.az = 50.0
    client.antenna.el = 0.0
    tracked = TrackedObject()
    tracked.az_set = 50.01
    tracked.el_set = 20.0
    tracker = Tracker(client, settings=_tracking_settings(), thread_manager=DummyThreadManager(), tracked_object=tracked)
    tracker._kickstart_pending = False
    tracker._must_apply_speeds = False

    tracker.step()

    assert not any(command[0] == "set_az_speed" for command in client.commands if isinstance(command, tuple))
    assert ("set_el_speed", 500.0) in client.commands


def test_tracker_uses_axis_specific_approach_thresholds():
    client = AxisDriverClient()
    client.antenna.az = 0.0
    client.antenna.el = 0.0
    tracked = TrackedObject()
    tracked.az_set = 20.0
    tracked.el_set = 20.0
    settings = _tracking_settings()
    settings["ANTENNA"]["az_approach_error_threshold"] = 10
    settings["ANTENNA"]["el_approach_error_threshold"] = 30
    settings["ANTENNA"]["az_close_error_threshold"] = 2
    settings["ANTENNA"]["el_close_error_threshold"] = 2
    tracker = Tracker(client, settings=settings, thread_manager=DummyThreadManager(), tracked_object=tracked)
    tracker._kickstart_pending = False
    tracker._must_apply_speeds = False

    tracker.step()

    assert ("set_az_speed", 500.0) in client.commands
    assert ("set_el_speed", 100.0) in client.commands

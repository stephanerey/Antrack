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


def test_tracker_axis_driver_stale_telemetry_safety_stop():
    client = AxisDriverClient()
    client.antenna.last_update_monotonic = time.monotonic() - 1.0
    tracker = Tracker(client, settings={}, thread_manager=DummyThreadManager(), tracked_object=_tracked_object())
    tracker._last_az_cmd = "CW"
    tracker._last_el_cmd = "UP"

    tracker.step()

    assert "stop_az" in client.commands
    assert "stop_el" in client.commands


def test_tracker_axis_driver_endstop_stop_on_affected_axis():
    client = AxisDriverClient()
    client.antenna.endstop_az = 1
    tracker = Tracker(client, settings={}, thread_manager=DummyThreadManager(), tracked_object=_tracked_object())
    tracker._last_az_cmd = "CW"
    tracker._last_az_cmd_ts = time.monotonic() - 1.0

    tracker.step()

    assert "stop_az" in client.commands
    assert "move_cw" not in client.commands

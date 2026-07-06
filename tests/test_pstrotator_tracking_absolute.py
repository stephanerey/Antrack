from antrack.tracking.tracking import Tracker, TrackedObject


class DummyThreadManager:
    def __init__(self):
        self.tracking_manager = None

    def get_worker(self, _name):
        return None


class AbsoluteTargetClient:
    def __init__(self):
        self.antenna = type("Antenna", (), {"az": None, "el": None, "az_setrate": 0.0, "el_setrate": 0.0})()
        self.axis_status = {"azimuth": "STOP", "elevation": "STOP"}
        self.commands = []
        self.server_status = "CONNECTED"

    def supports_absolute_targets(self):
        return True

    def set_target_position(self, az, el, timeout=None):
        self.commands.append((float(az), float(el), timeout))

    def stop_az(self, timeout=None):
        self.commands.append(("stop_az", timeout))

    def stop_el(self, timeout=None):
        self.commands.append(("stop_el", timeout))

    def set_az_speed(self, speed, timeout=None):
        self.commands.append(("set_az_speed", speed, timeout))
        return speed

    def set_el_speed(self, speed, timeout=None):
        self.commands.append(("set_el_speed", speed, timeout))
        return speed


def test_tracker_sends_absolute_target_without_local_telemetry():
    client = AbsoluteTargetClient()
    tracked = TrackedObject()
    tracked.az_set = 123.4
    tracked.el_set = 45.6
    tracker = Tracker(client, settings={}, thread_manager=DummyThreadManager(), tracked_object=tracked)

    tracker.step()

    assert (123.4, 45.6, None) in client.commands

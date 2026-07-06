from types import SimpleNamespace

from antrack.tracking.tracking import Tracker


class DummyTrackingManager:
    def __init__(self):
        self.active = set()
        self.registered = []
        self.unregistered = []

    def register_tracker(self, tracker):
        self.active.add(tracker)
        self.registered.append(tracker)

    def unregister_tracker(self, tracker):
        self.active.discard(tracker)
        self.unregistered.append(tracker)

    def is_tracker_active(self, tracker):
        return tracker in self.active


class DummyThreadManager:
    def __init__(self):
        self.tracking_manager = DummyTrackingManager()

    def run_coro(self, *_args, **_kwargs):
        return None


def _axis_client_qt():
    return SimpleNamespace(
        antenna=SimpleNamespace(az=None, el=None),
        axis_status={"azimuth": None, "elevation": None},
        stop_az=lambda *args, **kwargs: None,
        stop_el=lambda *args, **kwargs: None,
        set_az_speed=lambda *args, **kwargs: None,
        set_el_speed=lambda *args, **kwargs: None,
        move_cw=lambda *args, **kwargs: None,
        move_ccw=lambda *args, **kwargs: None,
        move_up=lambda *args, **kwargs: None,
        move_down=lambda *args, **kwargs: None,
        supports_absolute_targets=lambda: False,
        server_status="DISCONNECTED",
    )


def test_tracker_delegates_lifecycle_to_tracking_manager():
    thread_manager = DummyThreadManager()
    tracker = Tracker(_axis_client_qt(), settings={}, thread_manager=thread_manager)

    assert not tracker.is_running()

    tracker.start()

    assert thread_manager.tracking_manager.registered == [tracker]
    assert tracker.is_running()

    tracker.stop()

    assert thread_manager.tracking_manager.unregistered == [tracker]
    assert not tracker.is_running()

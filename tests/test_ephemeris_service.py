import threading
from types import SimpleNamespace

from antrack.tracking.ephemeris_service import EphemerisService, SimpleSignal


class RecordingThreadManager:
    def __init__(self):
        self.started = []
        self.stopped = []
        self._running = set()

    def start_thread(self, name, func, *args, **kwargs):
        if name not in self._running:
            self._running.add(name)
            self.started.append(name)
        return None

    def stop_thread(self, name):
        self._running.discard(name)
        self.stopped.append(name)


def _lightweight_ephem_service(thread_manager):
    service = EphemerisService.__new__(EphemerisService)
    service.thread_manager = thread_manager
    service.observer = SimpleNamespace(timescale=SimpleNamespace(now=lambda: SimpleNamespace(tt=1.0)))
    service.planets = object()
    service.logger = None
    service.pose_updated = SimpleSignal()
    service._thread_name = "EphemerisLoop"
    service._state_lock = threading.RLock()
    service._wakeup = threading.Event()
    service._workers = {}
    service._targets = {}
    service._next_run_at = {}
    service._hip_df = None
    service._hip_lock = threading.Lock()
    service._star_cache = {}
    service._radio_cache = {}
    service._pass_cache = {}
    service._tle_repo = None
    service._rs_catalog = None
    service._sc_repo = None
    return service


def test_ephemeris_service_reuses_single_worker_for_multiple_objects():
    thread_manager = RecordingThreadManager()
    service = _lightweight_ephem_service(thread_manager)

    service.start_object("one", "Solar System", "Sun", interval=0.5)
    service.start_object("two", "Star", "Polaris", interval=0.5)
    service.start_object("three", "Artificial Satellite", "ISS", interval=0.5)

    assert thread_manager.started == ["EphemerisLoop"]
    assert sorted(service._targets.keys()) == ["one", "three", "two"]


def test_ephemeris_service_stop_all_stops_shared_worker():
    thread_manager = RecordingThreadManager()
    service = _lightweight_ephem_service(thread_manager)

    service.start_object("primary", "Solar System", "Sun", interval=0.1)
    service.start_object("secondary", "Star", "Sirius", interval=0.5)
    service.stop_all()

    assert service._targets == {}
    assert service._workers == {}
    assert thread_manager.stopped == ["EphemerisLoop"]

import threading
from types import SimpleNamespace

import pytest

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


def test_ephemeris_service_compute_wakeup_timeout_uses_true_next_due():
    thread_manager = RecordingThreadManager()
    service = _lightweight_ephem_service(thread_manager)

    assert service._compute_wakeup_timeout(None, now_monotonic=10.0) == 0.5
    assert service._compute_wakeup_timeout(10.4, now_monotonic=10.0) == pytest.approx(0.4)
    assert service._compute_wakeup_timeout(12.0, now_monotonic=10.0) == 0.5


def test_ephemeris_service_step_object_emits_single_merged_payload_when_pass_info_due():
    thread_manager = RecordingThreadManager()
    service = _lightweight_ephem_service(thread_manager)
    received = []
    service.pose_updated.connect(lambda key, payload: received.append((key, payload)))
    service._compute_payload = lambda obj_type, name, t_now: {"az": 1.0, "el": 2.0}
    service._compute_pass_info = lambda obj_type, name, t_now: {"next_event": "AOS"}

    service._step_object(
        "primary",
        {"obj_type": "Solar System", "name": "Sun"},
        t_now=SimpleNamespace(tt=10.0),
    )

    assert received == [(
        "primary",
        {
            "az": 1.0,
            "el": 2.0,
            "name": "Sun",
            "visible_now": True,
            "el_now_deg": 2.0,
            "next_event": "AOS",
        },
    )]


def test_ephemeris_service_reuses_cached_pass_info_until_event_boundary():
    thread_manager = RecordingThreadManager()
    service = _lightweight_ephem_service(thread_manager)
    received = []
    compute_pass_calls = []
    service.pose_updated.connect(lambda key, payload: received.append((key, payload)))
    service._compute_payload = lambda obj_type, name, t_now: {"az": 1.0, "el": 2.0}

    def _compute_pass_info(obj_type, name, t_now):
        compute_pass_calls.append((obj_type, name, float(t_now.tt)))
        return {
            "visible_now": False,
            "el_now_deg": -1.0,
            "aos_tt": 20.0,
            "los_tt": 30.0,
            "dur_str": "10m",
            "max_el_deg": 42.0,
            "max_el_time_utc": "2026-07-07 12:00:00",
            "aos_utc": "2026-07-07 11:50:00",
            "los_utc": "2026-07-07 12:00:00",
            "max_tt": 25.0,
        }

    service._compute_pass_info = _compute_pass_info

    service._step_object("primary", {"obj_type": "Solar System", "name": "Sun"}, t_now=SimpleNamespace(tt=10.0))
    service._step_object("primary", {"obj_type": "Solar System", "name": "Sun"}, t_now=SimpleNamespace(tt=15.0))

    assert len(compute_pass_calls) == 1
    assert received[-1][1]["visible_now"] is True
    assert received[-1][1]["el_now_deg"] == 2.0
    assert received[-1][1]["los_tt"] == 30.0

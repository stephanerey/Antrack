import types

from antrack.core.axis.axis_client import AxisClientPollingAdapter


class _SignalRecorder:
    def __init__(self):
        self.calls = []

    def emit(self, *args):
        self.calls.append(args)


class _TelemetryPayload:
    def __init__(self, payload):
        self._payload = dict(payload)

    def to_dict(self):
        return dict(self._payload)


class _SnapshotPayload:
    def __init__(self, payload):
        self._payload = dict(payload)

    def to_dict(self):
        return dict(self._payload)


class _BackendStub:
    def __init__(self):
        self.telemetry_calls = 0
        self.snapshot_calls = 0

    def get_telemetry(self):
        self.telemetry_calls += 1
        return _TelemetryPayload({"az": 12.5, "el": 34.5, "endstop_az": 1})

    def snapshot(self):
        self.snapshot_calls += 1
        return _SnapshotPayload({"antenna": {"az": 12.5}, "server": {"connection": "CONNECTED"}})


class _WorkerStub:
    def __init__(self):
        self.abort = False


class _ThreadManagerStub:
    def __init__(self, worker):
        self._worker = worker

    def get_worker(self, _thread_name):
        return self._worker


class _ClientStub:
    def __init__(self):
        self.backend = _BackendStub()
        self.antenna_position_updated = _SignalRecorder()
        self.antenna_telemetry_updated = _SignalRecorder()
        self.status_updated = _SignalRecorder()
        self.telemetry_updated = _SignalRecorder()
        self.get_antenna_telemetry_calls = 0
        self.snapshot_calls = 0

    def poll_position(self):
        return 12.5, 34.5

    def poll_status(self):
        return {"endstop_az": 1, "endstop_el": 0}

    def get_antenna_telemetry(self):
        self.get_antenna_telemetry_calls += 1
        raise AssertionError("Polling adapter should reuse cached telemetry instead of refreshing it")

    def snapshot(self):
        self.snapshot_calls += 1
        raise AssertionError("Polling adapter should reuse cached snapshot instead of refreshing it")


def test_poll_position_loop_reuses_cached_backend_telemetry(monkeypatch):
    worker = _WorkerStub()
    client = _ClientStub()
    adapter = AxisClientPollingAdapter(client, _ThreadManagerStub(worker))

    def _sleep(_interval):
        worker.abort = True

    monkeypatch.setattr("time.sleep", _sleep)

    adapter._poll_position_loop(interval=0.2)

    assert client.backend.telemetry_calls == 1
    assert client.get_antenna_telemetry_calls == 0
    assert client.antenna_position_updated.calls == [((12.5, 34.5))]
    assert client.antenna_telemetry_updated.calls == [({"az": 12.5, "el": 34.5, "endstop_az": 1},)]


def test_poll_status_loop_reuses_cached_backend_snapshot(monkeypatch):
    worker = _WorkerStub()
    client = _ClientStub()
    adapter = AxisClientPollingAdapter(client, _ThreadManagerStub(worker))

    def _sleep(_interval):
        worker.abort = True

    monkeypatch.setattr("time.sleep", _sleep)

    adapter._poll_status_loop(interval=1.0)

    assert client.backend.snapshot_calls == 1
    assert client.snapshot_calls == 0
    assert client.status_updated.calls == [({"endstop_az": 1, "endstop_el": 0},)]
    assert client.telemetry_updated.calls == [({"antenna": {"az": 12.5}, "server": {"connection": "CONNECTED"}},)]

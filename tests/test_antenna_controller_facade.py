import asyncio

from antrack.core.antenna.config import AxisDriverConnectionConfig
from antrack.core.antenna.backend import BaseAntennaBackend
from antrack.core.antenna.controller_qt import AntennaControllerQt, _polling_intervals_for_config
from antrack.core.axis.axis_driver_backend import AxisDriverBackend
from antrack.core.antenna.types import AntennaConnectionMode, AntennaConnectionState


class DummyThreadManager:
    def run_coro(self, _loop_name, coro_or_factory, timeout=None):
        coro = coro_or_factory() if callable(coro_or_factory) else coro_or_factory
        return asyncio.run(coro)


class RecordingThreadManager:
    def __init__(self):
        self.timeouts = []

    def run_coro(self, _loop_name, coro_or_factory, timeout=None):
        self.timeouts.append(timeout)
        coro = coro_or_factory() if callable(coro_or_factory) else coro_or_factory
        return asyncio.run(coro)


class FakeBackend(BaseAntennaBackend):
    def __init__(self):
        super().__init__("FakeBackend")
        self.calls = []

    def is_connected(self) -> bool:
        return self.state == AntennaConnectionState.CONNECTED

    async def connect(self) -> None:
        self.calls.append("connect")
        self.state = AntennaConnectionState.CONNECTED
        self.telemetry.az = 12.5
        self.telemetry.el = 34.5

    async def disconnect(self) -> None:
        self.calls.append("disconnect")
        self.state = AntennaConnectionState.DISCONNECTED

    async def set_az_speed(self, speed: float) -> int | None:
        self.calls.append(("set_az_speed", speed))
        self.telemetry.az_setrate = speed
        return int(speed)

    async def set_el_speed(self, speed: float) -> int | None:
        self.calls.append(("set_el_speed", speed))
        self.telemetry.el_setrate = speed
        return int(speed)

    async def move_cw(self) -> int | None:
        self.calls.append("move_cw")
        return 1

    async def move_ccw(self) -> int | None:
        self.calls.append("move_ccw")
        return 1

    async def move_up(self) -> int | None:
        self.calls.append("move_up")
        return 1

    async def move_down(self) -> int | None:
        self.calls.append("move_down")
        return 1

    async def stop_az(self) -> int | None:
        self.calls.append("stop_az")
        return 1

    async def stop_el(self) -> int | None:
        self.calls.append("stop_el")
        return 1

    async def get_position(self):
        self.calls.append("get_position")
        return 12.5, 34.5

    async def get_status(self):
        self.calls.append("get_status")
        return {"endstop_az": 1, "endstop_el": 0}

    async def get_versions(self):
        self.calls.append("get_versions")
        self.versions.server_version = "fake"
        return self.versions


def test_controller_emits_connection_state_changes():
    backend = FakeBackend()
    controller = AntennaControllerQt(backend, thread_manager=DummyThreadManager())
    states = []
    controller.connection_state_changed.connect(states.append)

    assert controller.connect() is True
    controller.disconnect()

    assert states[0] == "CONNECTING"
    assert "CONNECTED" in states
    assert states[-1] == "DISCONNECTED"


def test_controller_emits_telemetry_and_status_payloads():
    backend = FakeBackend()
    controller = AntennaControllerQt(backend, thread_manager=DummyThreadManager())
    telemetry = []
    statuses = []
    controller.connect()
    controller.antenna_telemetry_updated.connect(telemetry.append)
    controller.status_updated.connect(statuses.append)

    controller.get_position()
    controller.get_status()

    assert telemetry[-1]["az"] == 12.5
    assert statuses[-1]["endstop_az"] == 1


def test_controller_telemetry_payload_includes_index_fields():
    backend = FakeBackend()
    backend.telemetry.index_az = 1
    backend.telemetry.index_el = 2
    controller = AntennaControllerQt(backend, thread_manager=DummyThreadManager())
    controller.connect()

    payload = controller.get_antenna_telemetry()

    assert payload["index_az"] == 1
    assert payload["index_el"] == 2


def test_controller_methods_map_to_backend_calls():
    backend = FakeBackend()
    controller = AntennaControllerQt(backend, thread_manager=DummyThreadManager())
    controller.connect()

    controller.set_az_speed(42, timeout=1.0)
    controller.move_cw(timeout=1.0)
    controller.stop_az(timeout=1.0)

    assert ("set_az_speed", 42) in backend.calls
    assert "move_cw" in backend.calls
    assert "stop_az" in backend.calls


def test_axis_driver_controller_uses_extended_poll_timeouts():
    backend = AxisDriverBackend(
        AxisDriverConnectionConfig(
            comport="COM7",
            command_timeout_s=0.5,
            serial_timeout_s=0.15,
        ),
        serial_factory=lambda **_kwargs: None,
    )
    backend.state = AntennaConnectionState.CONNECTED
    backend.telemetry.az = 12.5
    backend.telemetry.el = 34.5
    thread_manager = RecordingThreadManager()
    controller = AntennaControllerQt(backend, thread_manager=thread_manager)

    async def fake_get_position():
        return 12.5, 34.5

    async def fake_get_status():
        return {"endstop_az": 1, "endstop_el": 0}

    backend.get_position = fake_get_position
    backend.get_status = fake_get_status

    controller.get_position()
    controller.get_status()

    assert thread_manager.timeouts[0] >= 2.0
    assert thread_manager.timeouts[1] >= 5.0


def test_axis_driver_controller_uses_extended_motion_timeout():
    backend = AxisDriverBackend(
        AxisDriverConnectionConfig(
            comport="COM7",
            command_timeout_s=0.5,
            serial_timeout_s=0.15,
        ),
        serial_factory=lambda **_kwargs: None,
    )
    backend.state = AntennaConnectionState.CONNECTED
    thread_manager = RecordingThreadManager()
    controller = AntennaControllerQt(backend, thread_manager=thread_manager)

    async def fake_set_az_speed(_speed):
        return 42

    backend.set_az_speed = fake_set_az_speed

    controller.set_az_speed(42)

    assert thread_manager.timeouts[0] >= 6.0


def test_axis_driver_polling_intervals_are_clamped_for_slow_rs485():
    config = AxisDriverConnectionConfig(
        comport="COM7",
        position_interval_s=0.1,
        status_interval_s=0.5,
    )
    wrapped = type(
        "Cfg",
        (),
        {
            "mode": AntennaConnectionMode.AXIS_DRIVER,
            "selected_config": config,
        },
    )()

    polling = _polling_intervals_for_config(wrapped)

    assert polling == (0.1, 0.5)


def test_axis_driver_polling_intervals_apply_only_busy_loop_floor():
    config = AxisDriverConnectionConfig(
        comport="COM7",
        position_interval_s=0.0,
        status_interval_s=0.0,
    )
    wrapped = type(
        "Cfg",
        (),
        {
            "mode": AntennaConnectionMode.AXIS_DRIVER,
            "selected_config": config,
        },
    )()

    polling = _polling_intervals_for_config(wrapped)

    assert polling == (0.05, 0.1)

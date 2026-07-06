import asyncio

import pytest

from antrack.core.antenna.config import PstRotatorConnectionConfig
from antrack.core.pstrotator.pstrotator_backend import PstRotatorBackend


class FakeUdpSocket:
    def __init__(self, responses):
        self.responses = responses
        self.sent = []
        self.timeout = None
        self.bound = None
        self.closed = False
        self._last_payload = None
        self._queued = []

    def settimeout(self, timeout):
        self.timeout = timeout

    def gettimeout(self):
        return self.timeout

    def bind(self, addr):
        self.bound = addr

    def sendto(self, data, addr):
        payload = data.decode("ascii")
        self.sent.append((payload, addr))
        self._last_payload = payload
        if payload in self.responses:
            response = self.responses[payload]
            if isinstance(response, (list, tuple)):
                self._queued.extend(response)
            else:
                self._queued.append(response)
        return len(data)

    def recvfrom(self, _size):
        if self._queued:
            return self._queued.pop(0), ("127.0.0.1", 12000)
        raise TimeoutError("timeout")

    def close(self):
        self.closed = True


def _backend_and_socket():
    responses = {
        "<PST>AZ?</PST>": b"AZ:123.4\r",
        "<PST>EL?</PST>": b"EL:45.6\r",
    }
    fake_socket = FakeUdpSocket(responses)
    backend = PstRotatorBackend(
        PstRotatorConnectionConfig(),
        socket_factory=lambda *_args: fake_socket,
    )
    return backend, fake_socket


def test_pstrotator_queries_and_parses_position():
    backend, fake_socket = _backend_and_socket()

    asyncio.run(backend.connect())
    az, el = asyncio.run(backend.get_position())

    assert az == pytest.approx(123.4)
    assert el == pytest.approx(45.6)
    assert fake_socket.sent[0][0] == "<PST>AZ?</PST>"


def test_pstrotator_sends_combined_target_and_stop():
    backend, fake_socket = _backend_and_socket()
    asyncio.run(backend.connect())
    fake_socket.sent.clear()

    asyncio.run(backend.set_target_position(123.4, 45.6))
    asyncio.run(backend.stop_az())

    assert fake_socket.sent[0][0] == "<PST><AZIMUTH>123.4</AZIMUTH><ELEVATION>45.6</ELEVATION></PST>"
    assert fake_socket.sent[1][0] == "<PST><STOP>1</STOP></PST>"


def test_pstrotator_timeout_raises_cleanly():
    fake_socket = FakeUdpSocket({})
    backend = PstRotatorBackend(
        PstRotatorConnectionConfig(),
        socket_factory=lambda *_args: fake_socket,
    )

    with pytest.raises(Exception):
        asyncio.run(backend.connect())


def test_pstrotator_accepts_blank_angle_responses():
    responses = {
        "<PST>AZ?</PST>": b"AZ:\r",
        "<PST>EL?</PST>": b"EL:\r",
    }
    fake_socket = FakeUdpSocket(responses)
    backend = PstRotatorBackend(
        PstRotatorConnectionConfig(),
        socket_factory=lambda *_args: fake_socket,
    )

    asyncio.run(backend.connect())
    az, el = asyncio.run(backend.get_position())

    assert az is None
    assert el is None


def test_pstrotator_preserves_async_position_reports_when_queries_are_blank():
    responses = {
        "<PST>AZ?</PST>": [b"AZ:123.4\r", b"AZ:\r"],
        "<PST>EL?</PST>": [b"EL:45.6\r", b"EL:\r"],
    }
    fake_socket = FakeUdpSocket(responses)
    backend = PstRotatorBackend(
        PstRotatorConnectionConfig(),
        socket_factory=lambda *_args: fake_socket,
    )

    asyncio.run(backend.connect())
    az, el = asyncio.run(backend.get_position())

    assert az == pytest.approx(123.4)
    assert el == pytest.approx(45.6)

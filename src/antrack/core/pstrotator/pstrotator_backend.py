"""PstRotator UDP backend."""

from __future__ import annotations

import asyncio
import logging
import socket
import time
from typing import Callable

from antrack.core.antenna.backend import BaseAntennaBackend
from antrack.core.antenna.config import PstRotatorConnectionConfig
from antrack.core.antenna.types import AntennaConnectionState, AntennaVersions


class PstRotatorBackend(BaseAntennaBackend):
    """PstRotator backend using the UDP control protocol."""

    def __init__(
        self,
        config: PstRotatorConnectionConfig,
        *,
        socket_factory: Callable[[], socket.socket] | None = None,
    ) -> None:
        super().__init__("PstRotator")
        self.config = config
        self.logger = logging.getLogger("PstRotatorBackend")
        self.socket_factory = socket_factory or socket.socket
        self.sock = None
        self._async_loop = None
        self._io_lock = None
        self._response_timeout_s = float(max(0.05, self.config.command_timeout_s))

    async def _ensure_async_primitives(self) -> None:
        loop = asyncio.get_running_loop()
        if self._async_loop is loop and self._io_lock is not None:
            return
        self._async_loop = loop
        self._io_lock = asyncio.Lock()

    def is_connected(self) -> bool:
        return self.sock is not None

    def supports_manual_jog(self) -> bool:
        return False

    def supports_absolute_targets(self) -> bool:
        return True

    async def connect(self) -> None:
        await self._ensure_async_primitives()
        if self.is_connected():
            return
        self.state = AntennaConnectionState.CONNECTING
        self.last_error = None
        try:
            self.sock = self.socket_factory(socket.AF_INET, socket.SOCK_DGRAM)
            self.sock.settimeout(self.config.command_timeout_s)
            self.sock.bind(("", self.config.response_port))
            self.state = AntennaConnectionState.CONNECTED
            await self.get_versions()
            await self.get_position()
        except Exception as exc:
            self.state = AntennaConnectionState.ERROR
            self.last_error = str(exc)
            self.logger.error("PstRotator connect failed: %s", exc)
            self._close_socket()
            raise

    async def disconnect(self) -> None:
        await self._ensure_async_primitives()
        self.state = AntennaConnectionState.DISCONNECTING
        try:
            if self.is_connected():
                try:
                    await self.stop_all()
                except Exception:
                    pass
        finally:
            self._close_socket()
            self.state = AntennaConnectionState.DISCONNECTED

    async def set_target_position(self, azimuth: float, elevation: float) -> None:
        payload = (
            f"<PST><AZIMUTH>{float(azimuth):.1f}</AZIMUTH>"
            f"<ELEVATION>{float(elevation):.1f}</ELEVATION></PST>"
        )
        await self._send(payload)

    async def set_az_speed(self, speed: float) -> int | None:
        self.telemetry.az_setrate = float(speed)
        return int(speed)

    async def set_el_speed(self, speed: float) -> int | None:
        self.telemetry.el_setrate = float(speed)
        return int(speed)

    async def move_cw(self) -> int | None:
        raise NotImplementedError("Manual jog is not supported in PstRotator mode")

    async def move_ccw(self) -> int | None:
        raise NotImplementedError("Manual jog is not supported in PstRotator mode")

    async def move_up(self) -> int | None:
        raise NotImplementedError("Manual jog is not supported in PstRotator mode")

    async def move_down(self) -> int | None:
        raise NotImplementedError("Manual jog is not supported in PstRotator mode")

    async def stop_az(self) -> int | None:
        await self._send("<PST><STOP>1</STOP></PST>")
        return 1

    async def stop_el(self) -> int | None:
        await self._send("<PST><STOP>1</STOP></PST>")
        return 1

    async def get_position(self) -> tuple[float | None, float | None]:
        self._drain_pending_reports()
        az = await self._query_angle("AZ")
        el = await self._query_angle("EL")
        self._drain_pending_reports()
        if az is not None:
            self.telemetry.az = az
        else:
            az = self.telemetry.az
        if el is not None:
            self.telemetry.el = el
        else:
            el = self.telemetry.el
        self.telemetry.last_update_monotonic = time.monotonic()
        return az, el

    async def get_status(self) -> dict:
        self._drain_pending_reports()
        return {
            "endstop_az": self.telemetry.endstop_az,
            "endstop_el": self.telemetry.endstop_el,
            "modbus_az": self.telemetry.modbus_status_az,
            "modbus_el": self.telemetry.modbus_status_el,
            "signal": self.telemetry.signal,
        }

    async def get_versions(self) -> AntennaVersions:
        self.versions.server_version = "PstRotator"
        self.versions.driver_version_az = None
        self.versions.driver_version_el = None
        return self.versions

    async def _query_angle(self, axis: str) -> float | None:
        payload = f"<PST>{axis.upper()}?</PST>"
        response = await self._send(payload, expect_response=True, response_prefix=f"{axis.upper()}:")
        prefix = f"{axis.upper()}:"
        if not response.startswith(prefix):
            raise ValueError(f"Unexpected PstRotator response: {response!r}")
        value_text = response[len(prefix):].strip()
        if not value_text:
            return None
        return float(value_text)

    async def _send(
        self,
        payload: str,
        *,
        expect_response: bool = False,
        response_prefix: str | None = None,
    ) -> str:
        await self._ensure_async_primitives()
        async with self._io_lock:
            self._ensure_socket()
            try:
                self.sock.sendto(payload.encode("ascii"), (self.config.host, self.config.udp_port))
                if not expect_response:
                    return ""
                return self._receive_response(response_prefix=response_prefix)
            except Exception as exc:
                self.state = AntennaConnectionState.DEGRADED
                self.last_error = str(exc)
                raise

    def _receive_response(self, *, response_prefix: str | None = None) -> str:
        deadline = time.monotonic() + self._response_timeout_s
        while time.monotonic() < deadline:
            remaining = max(0.01, deadline - time.monotonic())
            self.sock.settimeout(remaining)
            response, _addr = self.sock.recvfrom(1024)
            text = response.decode("ascii", errors="ignore").strip()
            self._update_telemetry_from_report(text)
            if response_prefix is None or text.startswith(response_prefix):
                return text
        raise TimeoutError(f"Timed out waiting for PstRotator response prefix {response_prefix!r}")

    def _drain_pending_reports(self) -> None:
        if self.sock is None:
            return
        try:
            previous_timeout = self.sock.gettimeout()
        except Exception:
            previous_timeout = self._response_timeout_s
        try:
            self.sock.settimeout(0.0)
            while True:
                try:
                    response, _addr = self.sock.recvfrom(1024)
                except BlockingIOError:
                    break
                except TimeoutError:
                    break
                except OSError:
                    break
                text = response.decode("ascii", errors="ignore").strip()
                self._update_telemetry_from_report(text)
        finally:
            try:
                self.sock.settimeout(previous_timeout)
            except Exception:
                pass

    def _update_telemetry_from_report(self, text: str) -> None:
        if not text:
            return
        updated = False
        for raw_line in text.replace("\n", "\r").split("\r"):
            line = raw_line.strip()
            if not line or ":" not in line:
                continue
            key, value_text = line.split(":", 1)
            key = key.strip().upper()
            value_text = value_text.strip()
            if key not in {"AZ", "EL", "TGA", "TGE"}:
                continue
            if not value_text:
                continue
            try:
                value = float(value_text)
            except ValueError:
                continue
            if key == "AZ":
                self.telemetry.az = value
                updated = True
            elif key == "EL":
                self.telemetry.el = value
                updated = True
        if updated:
            self.telemetry.last_update_monotonic = time.monotonic()

    def _ensure_socket(self) -> None:
        if self.sock is None:
            raise ConnectionError("PstRotator socket is not open")

    def _close_socket(self) -> None:
        if self.sock is None:
            return
        try:
            self.sock.close()
        except Exception:
            pass
        self.sock = None

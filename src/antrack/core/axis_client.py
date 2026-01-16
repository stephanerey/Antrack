# axis_client.py
# Author: Stephane Rey
# Description: Core TCP client for antenna azimuth/elevation control (non-GUI)

import asyncio
import datetime
import struct
import logging
from enum import Enum


class ServerStatus(Enum):
    DISCONNECTED = 1
    CONNECTED = 2
    CONNECTING = 3
    DISCONNECTING = 4
    ERROR = 5


class AxisStatus(Enum):
    PARKED = 1
    STOPPED = 2
    ERROR = 3
    MOTORS_OFF = 4
    TRACKING = 5

    MOTION_AZ_STOP = 6
    MOTION_AZ_CW = 7
    MOTION_AZ_CCW = 8

    MOTION_EL_STOP = 9
    MOTION_EL_DOWN = 10
    MOTION_EL_UP = 11

    @property
    def display_name(self):
        name_map = {
            AxisStatus.MOTION_AZ_STOP: 'STOP',
            AxisStatus.MOTION_AZ_CW: 'CW',
            AxisStatus.MOTION_AZ_CCW: 'CCW',
            AxisStatus.MOTION_EL_STOP: 'STOP',
            AxisStatus.MOTION_EL_DOWN: 'DOWN',
            AxisStatus.MOTION_EL_UP: 'UP',
        }
        return name_map.get(self, self.name)


class AxisCommand(Enum):
    MOVE_CW = 1
    MOVE_CCW = 2
    MOVE_UP = 3
    MOVE_DOWN = 4
    STOP_AZ = 5
    STOP_EL = 6
    SPEED_AZ = 7
    SPEED_EL = 8

    QUERY_AZ = 20
    QUERY_EL = 21
    QUERY_MVT_AZ = 22
    QUERY_MVT_EL = 23
    QUERY_SPEED_AZ = 24
    QUERY_SPEED_EL = 25
    QUERY_ENDSTOP_AZ = 26
    QUERY_ENDSTOP_EL = 27
    QUERY_SIGNAL = 28
    QUERY_MODBUS_STATUS_AZ = 29
    QUERY_MODBUS_STATUS_EL = 30

    QUERY_AXIS_SERVER_VER = 50
    QUERY_AXIS_DRIVER_VER_AZ = 51
    QUERY_AXIS_DRIVER_VER_EL = 52

    CLOCK = 200
    END = 255


class AntennaStatus:
    def __init__(self):
        self.az = None
        self.el = None
        self.previous_az = None
        self.previous_el = None
        self.az_rate = 0
        self.el_rate = 0
        self.az_setrate = 0
        self.el_setrate = 0
        self.endstop_az = None
        self.endstop_el = None
        self.modbus_status_az = None
        self.modbus_status_el = None
        self.signal = None
        self.last_update = None


class ServerInfo:
    def __init__(self):
        self.connection: ServerStatus = ServerStatus.DISCONNECTED
        self.server_version: str = None
        self.driver_version_az: str = None
        self.driver_version_el: str = None
        self.last_update = None


class TelemetrySnapshot:
    """
    Snapshot aggregating antenna state and server state.
    """
    def __init__(self, antenna: AntennaStatus, server: ServerInfo):
        # Copie des valeurs pour éviter les effets de bord
        self.antenna = {
            'az': antenna.az,
            'el': antenna.el,
            'az_rate': antenna.az_rate,
            'el_rate': antenna.el_rate,
            'endstop_az': antenna.endstop_az,
            'endstop_el': antenna.endstop_el,
            'modbus_az': antenna.modbus_status_az,
            'modbus_el': antenna.modbus_status_el,
            'signal': antenna.signal,
            'last_update': antenna.last_update,
        }
        self.server = {
            'connection': server.connection.name if server.connection else None,
            'server_version': server.server_version,
            'driver_version_az': server.driver_version_az,
            'driver_version_el': server.driver_version_el,
            'last_update': server.last_update,
        }

    def to_dict(self):
        return {'antenna': self.antenna, 'server': self.server}



class Axis:
    def __init__(self, ip_address: str, port: int):
        self.ip_address = ip_address
        self.port = port
        self.server_status = ServerStatus.DISCONNECTED
        self.logger = logging.getLogger("AxisCore")
        # Identifiant d'instance (utile si plusieurs Axis coexistent)
        try:
            self.instance_id = f"{id(self):x}"
        except Exception:
            self.instance_id = "unknown"

        self.reader = None
        self.writer = None
        self.antenna = AntennaStatus()
        self.server_info = ServerInfo()
        self.axis_status = {
            'antenna': AxisStatus.STOPPED,
            'azimuth': AxisStatus.MOTION_AZ_STOP,
            'elevation': AxisStatus.MOTION_EL_STOP,
        }

        self.command_futures = {}
        self.command_locks = {cmd: asyncio.Lock() for cmd in AxisCommand}
        self._send_lock = asyncio.Lock()
        self.read_lock = asyncio.Lock()
        # Tâche asyncio pour le keep-alive périodique
        self._keep_alive_task = None
        # Callbacks de notification en cas de déconnexion détectée côté core
        self._disconnect_callbacks = []

    async def connect(self):
        if self.server_status != ServerStatus.DISCONNECTED:
            return
        self.server_status = ServerStatus.CONNECTING
        try:
            self.reader, self.writer = await asyncio.open_connection(self.ip_address, self.port)
            self.server_status = ServerStatus.CONNECTED
            self.server_info.connection = ServerStatus.CONNECTED
            try:
                self.logger.info("Axis connect: instance=%s CONNECTED (reader=%s writer=%s)", self.instance_id, bool(self.reader), bool(self.writer))
            except Exception:
                pass

            # Réinitialiser tout état d'attente précédent
            try:
                for fut in list(self.command_futures.values()):
                    try:
                        if fut and not fut.done():
                            fut.set_result(None)
                    except Exception:
                        pass
                self.command_futures.clear()
            except Exception:
                pass

            # 1) Démarrer la tâche de lecture AVANT tout envoi
            asyncio.create_task(self._process_responses())
            # Laisser l'event loop planifier la tâche de lecture
            try:
                await asyncio.sleep(0)
            except Exception:
                pass

            # 2) Handshake léger: CLOCK puis une paire de QUERY avec timeouts courts
            try:
                import asyncio as _aio
                try:
                    await _aio.wait_for(self.send_command(AxisCommand.CLOCK, 0), timeout=0.7)
                except Exception as e:
                    self.logger.info(f"Handshake: CLOCK timeout/err: {e}")
                try:
                    az0 = await _aio.wait_for(self.send_command(AxisCommand.QUERY_AZ), timeout=0.7)
                    el0 = await _aio.wait_for(self.send_command(AxisCommand.QUERY_EL), timeout=0.7)
                    self.logger.info(f"Handshake: initial AZ={az0} EL={el0}")
                except Exception as e:
                    self.logger.info(f"Handshake: QUERY timeout/err: {e}")
            except Exception as e:
                self.logger.info(f"Handshake: exception {e}")

            # 3) Start keep-alive (reduced load)
            try:
                asyncio.create_task(self.start_keep_alive(1.0))
            except Exception as e:
                self.logger.warning(f"Unable to start keep-alive: {e}")

            # 4) Fetch versions in background (non-blocking)
            try:
                asyncio.create_task(self.get_versions())
            except Exception as e:
                self.logger.warning(f"Failed to fetch versions: {e}")

        except Exception as e:
            self.logger.error(f"Connection failed: {e}")
            self.server_status = ServerStatus.ERROR
            self.server_info.connection = ServerStatus.ERROR

    async def disconnect(self):
        # Arrêter la tâche de keep-alive si active
        await self.stop_keep_alive()
        if self.writer:
            self.writer.close()
            await self.writer.wait_closed()
        # Nettoyer les futures en attente
        try:
            for fut in list(self.command_futures.values()):
                try:
                    if fut and not fut.done():
                        fut.set_result(None)
                except Exception:
                    pass
            self.command_futures.clear()
        except Exception:
            pass
        self.server_status = ServerStatus.DISCONNECTED

    async def start_keep_alive(self, interval: float = 0.5):
        """
        Start an asyncio task that periodically sends CLOCK to keep the connection alive.
        """
        if self._keep_alive_task and not self._keep_alive_task.done():
            self.logger.debug("Keep-alive already running, skipping.")
            return

        self.logger.info(f"Starting keep-alive (interval {interval}s)")
        async def _keep_alive_loop():
            try:
                while self.server_status == ServerStatus.CONNECTED:
                    try:
                        res = await self.send_command(AxisCommand.CLOCK, 0)
                        self.logger.debug(f"Keep-alive CLOCK sent (response: {res})")
                    except Exception as e:
                        self.logger.warning(f"Keep-alive intermittent error: {e}")
                    await asyncio.sleep(interval)
            except asyncio.CancelledError:
                self.logger.debug("Keep-alive cancelled cleanly")
                raise

        self._keep_alive_task = asyncio.create_task(_keep_alive_loop())

    async def stop_keep_alive(self):
        """
        Stop the keep-alive asyncio task if it is running.
        """
        task = self._keep_alive_task
        self._keep_alive_task = None
        if task:
            self.logger.info("Stopping keep-alive")
            try:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    self.logger.debug("Keep-alive task cancelled")
            except Exception as e:
                self.logger.warning(f"Error while stopping keep-alive: {e}")

    # --- Callbacks de déconnexion ---
    def set_disconnect_callback(self, callback):
        """
        Enregistre un callback (callable sans argument) qui sera appelé lorsque
        le core détecte une déconnexion du serveur.
        """
        try:
            if callback not in self._disconnect_callbacks:
                self._disconnect_callbacks.append(callback)
        except Exception:
            pass

    def clear_disconnect_callbacks(self):
        """Supprime tous les callbacks de déconnexion enregistrés."""
        self._disconnect_callbacks.clear()

    def _notify_disconnected(self):
        """Appelle en toute sécurité les callbacks de déconnexion."""
        for cb in list(self._disconnect_callbacks):
            try:
                cb()
            except Exception as e:
                self.logger.warning(f"Erreur lors de l'appel du callback de déconnexion: {e}")

    async def send_command(self, command: AxisCommand, data=0):
        if self.server_status != ServerStatus.CONNECTED:
            try:
                self.logger.warning("send_command refused: instance=%s status=%s cmd=%s", getattr(self, "instance_id", "unknown"), getattr(self.server_status, "name", self.server_status), getattr(command, "name", command))
            except Exception:
                pass
            return None
        async with self._send_lock:
            future = asyncio.get_event_loop().create_future()
            self.command_futures[command] = future
            try:
                # S'assurer que 'data' est un entier non signé 16-bit pour le pack
                val = data
                if not isinstance(val, int):
                    try:
                        val = int(round(float(val)))
                    except Exception:
                        val = 0
                if val < 0:
                    val = 0
                if val > 65535:
                    val = 65535
                msg = struct.pack('B3xH2x', command.value, val)
                self.writer.write(msg)
                await self.writer.drain()
                return await future
            except Exception as e:
                # Clean future if sending failed
                self.command_futures.pop(command, None)
                try:
                    self.logger.error(f"Send command error: {e}")
                except Exception:
                    pass
                return None

    async def _process_responses(self):
        while self.server_status == ServerStatus.CONNECTED:
            try:
                async with self.read_lock:
                    response = await self.reader.readexactly(8)
                    command_type, val = self._parse_response(response)
                    future = self.command_futures.pop(command_type, None)
                    if future and not future.done():
                        future.set_result(val)
            except asyncio.IncompleteReadError:
                self.logger.warning("Response error: connection closed while reading (incomplete frame)")
                self.server_status = ServerStatus.DISCONNECTED
                # Arrêter le keep-alive et notifier
                try:
                    await self.stop_keep_alive()
                except Exception:
                    pass
                # Résoudre toutes les futures en attente pour éviter des blocages/None répétés
                try:
                    for fut in list(self.command_futures.values()):
                        try:
                            if fut and not fut.done():
                                fut.set_result(None)
                        except Exception:
                            pass
                    self.command_futures.clear()
                except Exception:
                    pass
                self._notify_disconnected()
                break
            except Exception as e:
                self.logger.error(f"Response error: {e}")
                # Toute erreur IO critique doit rompre la boucle et notifier
                self.server_status = ServerStatus.DISCONNECTED
                try:
                    await self.stop_keep_alive()
                except Exception:
                    pass
                try:
                    for fut in list(self.command_futures.values()):
                        try:
                            if fut and not fut.done():
                                fut.set_result(None)
                        except Exception:
                            pass
                    self.command_futures.clear()
                except Exception:
                    pass
                self._notify_disconnected()
                break

    def _parse_response(self, response: bytes):
        if not response or len(response) < 8:
            print("Response parsing error: incomplete frame received")
            return None, None
        command_id = response[0]
        command_type = next((cmd for cmd in AxisCommand if cmd.value == command_id), None)
        if command_type is None:
            print(f"Response parsing warning: unknown command id received: {command_id}")
            return None, None
        try:
            val = int(struct.unpack('<i', response[4:])[0])
        except struct.error as e:
            print(f"Response parsing error: invalid payload for command {command_type}: {e}")
            return None, None
        return command_type, val


    # Control commands (avec logs d'ACK)
    async def set_az_speed(self, speed):
        ack = await self.send_command(AxisCommand.SPEED_AZ, speed)
        try:
            if ack is None:
                self.logger.error(f"set_az_speed({speed}) -> ACK=None")
            else:
                self.logger.debug(f"set_az_speed({speed}) -> ACK={ack}")
        except Exception:
            pass
        return ack

    async def set_el_speed(self, speed):
        ack = await self.send_command(AxisCommand.SPEED_EL, speed)
        try:
            if ack is None:
                self.logger.error(f"set_el_speed({speed}) -> ACK=None")
            else:
                self.logger.debug(f"set_el_speed({speed}) -> ACK={ack}")
        except Exception:
            pass
        return ack

    async def move_cw(self):
        ack = await self.send_command(AxisCommand.MOVE_CW)
        try:
            if ack is None:
                self.logger.error("move_cw -> ACK=None")
            else:
                self.logger.debug(f"move_cw -> ACK={ack}")
        except Exception:
            pass
        return ack

    async def move_ccw(self):
        ack = await self.send_command(AxisCommand.MOVE_CCW)
        try:
            if ack is None:
                self.logger.error("move_ccw -> ACK=None")
            else:
                self.logger.debug(f"move_ccw -> ACK={ack}")
        except Exception:
            pass
        return ack

    async def move_up(self):
        ack = await self.send_command(AxisCommand.MOVE_UP)
        try:
            if ack is None:
                self.logger.error("move_up -> ACK=None")
            else:
                self.logger.debug(f"move_up -> ACK={ack}")
        except Exception:
            pass
        return ack

    async def move_down(self):
        ack = await self.send_command(AxisCommand.MOVE_DOWN)
        try:
            if ack is None:
                self.logger.error("move_down -> ACK=None")
            else:
                self.logger.debug(f"move_down -> ACK={ack}")
        except Exception:
            pass
        return ack

    async def stop_az(self):
        ack = await self.send_command(AxisCommand.STOP_AZ)
        try:
            if ack is None:
                self.logger.error("stop_az -> ACK=None")
            else:
                self.logger.debug(f"stop_az -> ACK={ack}")
        except Exception:
            pass
        return ack

    async def stop_el(self):
        ack = await self.send_command(AxisCommand.STOP_EL)
        try:
            if ack is None:
                self.logger.error("stop_el -> ACK=None")
            else:
                self.logger.debug(f"stop_el -> ACK={ack}")
        except Exception:
            pass
        return ack


    # status commands - returns true or false
    def isServerConnected(self): return self.server_status == ServerStatus.CONNECTED
    def isServerConnecting(self): return self.server_status == ServerStatus.CONNECTING
    def isServerDisconnected(self): return self.server_status == ServerStatus.DISCONNECTED
    def isServerDisconnecting(self): return self.server_status == ServerStatus.DISCONNECTING
    def isServerError(self): return self.server_status == ServerStatus.ERROR

    async def get_versions(self):
        """
        Interroge les versions côté serveur et drivers, et met à jour ServerInfo.
        """
        try:
            sv = await self.send_command(AxisCommand.QUERY_AXIS_SERVER_VER)
            vz = await self.send_command(AxisCommand.QUERY_AXIS_DRIVER_VER_AZ)
            ve = await self.send_command(AxisCommand.QUERY_AXIS_DRIVER_VER_EL)
            # Conversion simple en chaîne si non None
            self.server_info.server_version = str(sv) if sv is not None else None
            self.server_info.driver_version_az = str(vz) if vz is not None else None
            self.server_info.driver_version_el = str(ve) if ve is not None else None
            self.server_info.connection = self.server_status
            self.server_info.last_update = datetime.datetime.now()
        except Exception as e:
            self.logger.warning(f"get_versions error: {e}")

    async def get_server_info(self):
        """
        Retourne ServerInfo en s'assurant de sa mise à jour minimale.
        """
        if self.server_info.server_version is None and self.server_status == ServerStatus.CONNECTED:
            await self.get_versions()
        return self.server_info

    def snapshot(self) -> TelemetrySnapshot:
        """
        Construit un instantané TelemetrySnapshot (antenne + serveur).
        """
        # Actualiser l'état de connexion
        self.server_info.connection = self.server_status
        return TelemetrySnapshot(self.antenna, self.server_info)

    # high-level queries
    async def get_position(self):
        """
        Récupère l'azimut et l'élévation auprès du serveur et met à jour l'état antenne.
        Retourne un tuple (az, el) de float si disponible, sinon (None, None).
        """
        try:
            az_raw = await self.send_command(AxisCommand.QUERY_AZ)
            el_raw = await self.send_command(AxisCommand.QUERY_EL)

            # Convertir les valeurs brutes 16-bit (0..65535) en degrés (0..360)
            az_f = (az_raw / 65535.0 * 360.0) % 360.0 if az_raw is not None else None
            el_f = (el_raw / 65535.0 * 360.0) % 360.0 if el_raw is not None else None

            # Calcul des vitesses
            now = datetime.datetime.now()
            if self.antenna.last_update and az_f is not None and self.antenna.az is not None:
                dt = max(1e-6, (now - self.antenna.last_update).total_seconds())
                self.antenna.az_rate = (az_f - self.antenna.az) / dt
            if self.antenna.last_update and el_f is not None and self.antenna.el is not None:
                dt = max(1e-6, (now - self.antenna.last_update).total_seconds())
                self.antenna.el_rate = (el_f - self.antenna.el) / dt

            # Mettre à jour l'état local
            if az_f is not None:
                self.antenna.previous_az = self.antenna.az
                self.antenna.az = az_f
            if el_f is not None:
                self.antenna.previous_el = self.antenna.el
                self.antenna.el = el_f
            self.antenna.last_update = now
            return az_f, el_f
        except Exception:
            return None, None

    async def get_status(self):
        """
        Récupère les statuts endstop/modbus/signal et retourne un dict:
        {'endstop_az': int|None, 'endstop_el': int|None, 'modbus_az': int|None, 'modbus_el': int|None, 'signal': int|None}
        """
        status = {'endstop_az': None, 'endstop_el': None, 'modbus_az': None, 'modbus_el': None, 'signal': None}
        try:
            endstop_az = await self.send_command(AxisCommand.QUERY_ENDSTOP_AZ)
            endstop_el = await self.send_command(AxisCommand.QUERY_ENDSTOP_EL)
            modbus_az = await self.send_command(AxisCommand.QUERY_MODBUS_STATUS_AZ)
            modbus_el = await self.send_command(AxisCommand.QUERY_MODBUS_STATUS_EL)
            signal = await self.send_command(AxisCommand.QUERY_SIGNAL)

            if endstop_az is not None:
                self.antenna.endstop_az = endstop_az
            if endstop_el is not None:
                self.antenna.endstop_el = endstop_el
            if modbus_az is not None:
                self.antenna.modbus_status_az = modbus_az
            if modbus_el is not None:
                self.antenna.modbus_status_el = modbus_el
            if signal is not None:
                self.antenna.signal = signal

            status['endstop_az'] = self.antenna.endstop_az
            status['endstop_el'] = self.antenna.endstop_el
            status['modbus_az'] = self.antenna.modbus_status_az
            status['modbus_el'] = self.antenna.modbus_status_el
            status['signal'] = self.antenna.signal

        except Exception:
            pass
        return status


class AxisClientPollingAdapter:
    """
    Encapsulates polling loops (positions and statuses) for an Axis client.
    - client: instance providing get_position() and get_status(), and optional Qt signals
              'antenna_position_updated' and 'status_updated'
    - thread_manager: ThreadManager instance to launch/stop threads
    """
    def __init__(self, client, thread_manager):
        self.client = client
        self.thread_manager = thread_manager

    def start(self, pos_interval: float = 0.2, status_interval: float = 1.0):
        # Position poller (AZ/EL)
        worker_azel = self.thread_manager.start_thread(
            "AxisPositionPoller",
            self._poll_position_loop,
            interval=pos_interval
        )
        worker_azel.error.connect(lambda msg: self._log_error(f"Position polling error: {msg}"))

        # Status poller
        worker_status = self.thread_manager.start_thread(
            "AxisStatusPoller",
            self._poll_status_loop,
            interval=status_interval
        )
        worker_status.error.connect(lambda msg: self._log_error(f"Status polling error: {msg}"))

    def stop(self):
        try:
            self.thread_manager.stop_thread("AxisPositionPoller")
        except Exception:
            pass
        try:
            self.thread_manager.stop_thread("AxisStatusPoller")
        except Exception:
            pass

    def _poll_position_loop(self, interval: float = 0.2):
        import time
        worker = self.thread_manager.get_worker("AxisPositionPoller")
        logger = logging.getLogger("AxisPoller.Position")
        last_hb = time.monotonic()
        ticks = 0
        try:
            while worker and not worker.abort:
                az, el = self.client.get_position()
                ticks += 1
                if az is not None and el is not None:
                    try:
                        # Emit Qt signal if available (position only)
                        self.client.antenna_position_updated.emit(az, el)
                    except Exception:
                        pass
                # Emit unified “antenna telemetry” payload (positions + endstops + rates)
                try:
                    if hasattr(self.client, "antenna_telemetry_updated") and hasattr(self.client, "get_antenna_telemetry"):
                        payload = self.client.get_antenna_telemetry()
                        self.client.antenna_telemetry_updated.emit(payload)
                except Exception:
                    pass
                time.sleep(interval)
        except Exception:
            # Laisser remonter pour que Worker.error émette le signal d'erreur
            raise

    def _poll_status_loop(self, interval: float = 1.0):
        import time
        worker = self.thread_manager.get_worker("AxisStatusPoller")
        logger = logging.getLogger("AxisPoller.Status")
        ticks = 0
        try:
            while worker and not worker.abort:
                status = self.client.get_status()
                ticks += 1
                if isinstance(status, dict):
                    try:
                        if hasattr(self.client, "status_updated"):
                            self.client.status_updated.emit(status)
                    except Exception:
                        pass
                # Emit a full snapshot if available
                try:
                    if hasattr(self.client, "telemetry_updated") and hasattr(self.client, "snapshot"):
                        self.client.telemetry_updated.emit(self.client.snapshot())
                except Exception:
                    pass
                time.sleep(interval)
        except Exception:
            # Laisser remonter pour que Worker.error émette le signal d'erreur
            raise

    def _log_error(self, msg: str):
        logger = getattr(self.client, "logger", None)
        if logger:
            try:
                logger.error(msg)
            except Exception:
                pass

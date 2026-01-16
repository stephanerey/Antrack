# powermeter.py
# Lecteur RS232 pour powermeter avec parsing du champ Power=... [dBm]
# Auteur: Stéphane Rey (structure orientée ThreadManager)

import re
import time
import logging
from typing import Optional

from PyQt5.QtCore import QObject, pyqtSignal

try:
    import serial  # pyserial
    from serial import Serial, SerialException
except Exception as e:
    serial = None
    Serial = object  # type: ignore
    SerialException = Exception


_PWR_REGEX = re.compile(
    r"Power\s*=\s*([+-]?\d+(?:\.\d+)?)\s*\[\s*dBm\s*\]",
    re.IGNORECASE
)


class Powermeter(QObject):
    """
    Driver minimal pour un powermeter RS232.
    - Port série défini dans settings['POWERMETER']['comport'] (ex: 'COM7' ou '/dev/ttyUSB0')
    - Paramètres optionnels:
        settings['POWERMETER'] = {
            'baudrate': 9600,
            'bytesize': 8,
            'parity': 'N',          # 'N','E','O','M','S'
            'stopbits': 1,
            'timeout_s': 0.5,       # timeout lecture RS232
            'read_cmd': 'READ?\r',  # commande à envoyer (optionnelle). Si absent: lecture directe
            'encoding': 'ascii',
            'inter_read_delay_s': 0.05,  # delai avant la lecture après l'envoi commande
            'overall_timeout_s': 1.5     # timeout global pour récupérer une mesure parsable
        }
    - Méthode principale threadable: read_power() → float (dBm)
    - Signaux (si vous voulez les consommer côté Qt): power_ready, error, status
    """

    power_ready = pyqtSignal(float)
    error = pyqtSignal(str)
    status = pyqtSignal(str)

    def __init__(self, settings: dict, logger: Optional[logging.Logger] = None, parent=None):
        super().__init__(parent)
        self.settings = settings or {}
        self.logger = logger or logging.getLogger("Powermeter")
        self._ser: Optional[Serial] = None

    # ---------- Public API (threadable) ----------

    def read_power(self) -> float:
        """
        Lecture one-shot d'une mesure en dBm.
        Conçu pour être lancé dans un QThread via ThreadManager.start_thread(..., self.read_power).
        Retourne un float (ex: -105.26) ou lève une exception en cas d'échec.
        """
        self._emit_status("read_power: start")
        try:
            self._ensure_serial_open()
            self._flush_input()

            # --- Paramètres / défauts robustes ---
            enc = self._pm_get("encoding", "ascii")
            inter_delay = float(self._pm_get("inter_read_delay_s", 0.05))
            overall_timeout = float(self._pm_get("overall_timeout_s", 1.5))

            # Commande par défaut: 'power?\r' si rien n'est fourni
            read_cmd = self._pm_get("read_cmd", "power?\r")
            if isinstance(read_cmd, str) and read_cmd:
                # Ajoute un CR si pas de fin de ligne fournie (certains instruments exigent CR ou CRLF)
                if not read_cmd.endswith(("\r", "\n")):
                    read_cmd += "\r"

                try:
                    self._ser.write(read_cmd.encode(enc, errors="ignore"))
                except Exception as e:
                    raise IOError(f"Échec d'émission commande '{read_cmd}': {e}")

                if inter_delay > 0:
                    time.sleep(inter_delay)

            # --- Réception/parse avec timeout global ---
            t0 = time.time()
            buff = ""
            while time.time() - t0 < overall_timeout:
                line = self._read_available_line(enc)
                if line:
                    buff += line
                    val = self._try_parse_power(buff)
                    if val is not None:
                        self._emit_status(f"read_power: parsed {val:.2f} dBm")
                        try:
                            self.power_ready.emit(val)
                        except Exception:
                            pass
                        return val
                else:
                    time.sleep(0.01)  # évite busy-wait

            # Dernière tentative sur le buffer agrégé
            val = self._try_parse_power(buff)
            if val is not None:
                self._emit_status(f"read_power: parsed (late) {val:.2f} dBm")
                try:
                    self.power_ready.emit(val)
                except Exception:
                    pass
                return val

            raise TimeoutError("Timeout: aucune valeur 'Power=... [dBm]' parsable reçue")

        except Exception as e:
            msg = f"read_power: ERROR: {e}"
            self.logger.error(msg)
            try:
                self.error.emit(str(e))
            except Exception:
                pass
            raise
        finally:
            self._emit_status("read_power: finish")

    # ---------- Helpers / Parsing ----------

    @staticmethod
    def extract_power_from_text(text: str) -> Optional[float]:
        """
        Extraction robuste de la puissance depuis un texte.
        Format attendu par défaut: 'Power=-105.26[dBm]      Ref=   0.00[dBm]'
        Retourne un float ou None si non trouvé.
        """
        if not text:
            return None
        m = _PWR_REGEX.search(text)
        if m:
            try:
                return float(m.group(1))
            except Exception:
                return None
        # Fallback ultra permissif: premier float suivi de [dBm]
        m2 = re.search(r"([+-]?\d+(?:\.\d+)?)\s*\[\s*dBm\s*\]", text, re.IGNORECASE)
        if m2:
            try:
                return float(m2.group(1))
            except Exception:
                return None
        return None

    def _try_parse_power(self, text: str) -> Optional[float]:
        val = self.extract_power_from_text(text)
        if val is not None:
            self.logger.debug(f"Powermeter parse OK: {val:.5f} dBm")
        return val

    # ---------- RS232 ----------

    def _ensure_serial_open(self):
        if serial is None:
            raise RuntimeError("pyserial n'est pas installé. Installez-le: pip install pyserial")
        if self._ser and getattr(self._ser, "is_open", False):
            return

        pm = self.settings.get("POWERMETER", {}) if isinstance(self.settings, dict) else {}
        port = pm.get("comport")
        if not port:
            raise ValueError("settings['POWERMETER']['comport'] non défini")

        baudrate = int(pm.get("baudrate", 9600))
        bytesize = int(pm.get("bytesize", 8))
        parity = str(pm.get("parity", "N")).upper()
        stopbits = int(pm.get("stopbits", 1))
        timeout_s = float(pm.get("timeout_s", 0.5))

        try:
            self._ser = serial.Serial(
                port=port,
                baudrate=baudrate,
                bytesize=self._to_bytesize(bytesize),
                parity=self._to_parity(parity),
                stopbits=self._to_stopbits(stopbits),
                timeout=timeout_s,
                write_timeout=timeout_s
            )
            self._emit_status(f"Serial OPEN on {port} @ {baudrate} bps")
        except SerialException as e:
            raise ConnectionError(f"Ouverture série échouée sur {port}: {e}")

    def close(self):
        try:
            if self._ser and getattr(self._ser, "is_open", False):
                self._ser.close()
                self._emit_status("Serial CLOSED")
        except Exception as e:
            self.logger.warning(f"Fermeture série: {e}")

    def _flush_input(self):
        try:
            if self._ser:
                self._ser.reset_input_buffer()
                self._ser.reset_output_buffer()
        except Exception:
            pass

    def _read_available_line(self, encoding: str) -> str:
        """
        Lit ce qui est disponible (ligne ou chunk), renvoie str (sans garantie de fin de ligne).
        Supporte des instruments qui ne terminent pas aux \r\n.
        """
        if not self._ser:
            return ""
        try:
            # Lire ce qui est disponible
            waiting = self._ser.in_waiting if hasattr(self._ser, "in_waiting") else 0
            if waiting <= 0:
                # tenter une lecture ligne (au cas où l'instrument fait du \n)
                raw = self._ser.readline()
            else:
                raw = self._ser.read(waiting)
            if not raw:
                return ""
            return raw.decode(encoding, errors="ignore")
        except Exception:
            return ""

    # ---------- Utils ----------

    def _pm_get(self, key: str, default=None):
        pm = self.settings.get("POWERMETER", {}) if isinstance(self.settings, dict) else {}
        return pm.get(key, default)

    def _emit_status(self, msg: str):
        try:
            self.logger.info(msg)
            self.status.emit(msg)
        except Exception:
            pass

    @staticmethod
    def _to_bytesize(n: int):
        from serial import FIVEBITS, SIXBITS, SEVENBITS, EIGHTBITS
        return {5: FIVEBITS, 6: SIXBITS, 7: SEVENBITS, 8: EIGHTBITS}.get(n, EIGHTBITS)

    @staticmethod
    def _to_parity(p: str):
        from serial import PARITY_NONE, PARITY_EVEN, PARITY_ODD, PARITY_MARK, PARITY_SPACE
        return {
            "N": PARITY_NONE, "NONE": PARITY_NONE,
            "E": PARITY_EVEN, "EVEN": PARITY_EVEN,
            "O": PARITY_ODD,  "ODD": PARITY_ODD,
            "M": PARITY_MARK, "MARK": PARITY_MARK,
            "S": PARITY_SPACE, "SPACE": PARITY_SPACE,
        }.get(p.upper(), PARITY_NONE)

    @staticmethod
    def _to_stopbits(n: int):
        from serial import STOPBITS_ONE, STOPBITS_ONE_POINT_FIVE, STOPBITS_TWO
        return {1: STOPBITS_ONE, 2: STOPBITS_TWO}.get(n, STOPBITS_ONE)

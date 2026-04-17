"""SDR backend adapted from the RSPdx controller for Antrack."""

from __future__ import annotations

import logging
import math
import threading
import time
from typing import Any, Optional

import numpy as np
from PyQt5.QtCore import QObject, pyqtSignal

from antrack.core.data_storage import DataStorage
from antrack.core.dsp import (
    WINDOW_ENBW_FACTORS,
    apply_ema,
    compute_power_spectrum_db,
    compute_snr,
    fft_max_for_sample_rate,
    frequency_axis,
    make_window,
    select_fft_size,
)

try:
    import SoapySDR  # type: ignore
    from SoapySDR import SOAPY_SDR_CF32, SOAPY_SDR_RX  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    SoapySDR = None
    SOAPY_SDR_CF32 = None
    SOAPY_SDR_RX = None


SAMPLE_RATE_PRESETS_HZ = [
    500_000.0,
    1_000_000.0,
    2_000_000.0,
    4_000_000.0,
    8_000_000.0,
    10_000_000.0,
]

SMOOTHING_PRESETS = {
    "off": 1.0,
    "light": 0.65,
    "medium": 0.35,
    "strong": 0.18,
}


class SdrClient(QObject):
    """Single-source SDR client with Soapy enumeration and RSPdx-style control state."""

    iq_block = pyqtSignal(object)
    spectrum_updated = pyqtSignal(object)
    snr_updated = pyqtSignal(float, str)
    status = pyqtSignal(str)
    error = pyqtSignal(str)
    mode_changed = pyqtSignal(str)
    settings_changed = pyqtSignal(dict)
    perf_updated = pyqtSignal(dict)
    started = pyqtSignal()
    stopped = pyqtSignal()

    def __init__(
        self,
        settings: dict | None = None,
        *,
        thread_manager=None,
        logger: Optional[logging.Logger] = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.settings = settings or {}
        self.thread_manager = thread_manager
        self.logger = logger or logging.getLogger("SdrClient")
        self._thread_name = "SdrStream"
        self._spectrum_thread_name = "SdrSpectrum"
        self._thread = None
        self._spectrum_thread = None
        self._thread_stop = threading.Event()
        self._stream_reconfigure_requested = threading.Event()
        self._spectrum_wakeup = threading.Event()
        self._read_timeout_us = 100_000
        self._state_lock = threading.RLock()
        self._latest_iq: np.ndarray | None = None
        self._iq_history: np.ndarray | None = None
        self._iq_history_capacity = 0
        self._iq_history_size = 0
        self._iq_history_write_pos = 0
        self._latest_freqs: np.ndarray | None = None
        self._latest_spectrum_db: np.ndarray | None = None
        self._latest_timestamp = 0.0
        self._dummy_phase = 0.0
        self._read_frame_counter = 0
        self._display_frame_counter = 0
        self._perf_timeouts = 0
        self._perf_overflows = 0
        self._perf_stream_errors = 0
        self._perf_fft_s = 0.0
        self._perf_storage_s = 0.0
        self._perf_emit_s = 0.0
        self._perf_emit_interval_s = 1.0
        self._last_perf_emit = 0.0
        self._last_refresh_cap_log_key = None
        self._stream = None
        self._sdr = None
        self._read_buffer: np.ndarray | None = None
        self._timeout_code = int(getattr(SoapySDR, "SOAPY_SDR_TIMEOUT", -1)) if SoapySDR else -1
        self._overflow_code = int(getattr(SoapySDR, "SOAPY_SDR_OVERFLOW", -4)) if SoapySDR else -4
        self._last_overflow_log_t = 0.0
        self._setting_keys: list[str] = []
        self._gain_names: list[str] = []
        self._available_source_records: list[dict[str, Any]] = []
        self.mode = "dummy"
        self.hwinfo: dict[str, Any] = {}

        cfg = self._sdr_settings()
        self.sample_rate = float(cfg.get("sample_rate_hz", 2_000_000.0))
        self.center_freq = float(cfg.get("center_freq_hz", 137_000_000.0))
        self.receiver_freq_hz = float(cfg.get("receiver_freq_hz", self.center_freq))
        self.buff_size = int(cfg.get("buffer_size", 16_384))
        self.fft_fps = float(cfg.get("fft_fps", 20.0))
        self.fft_size = int(cfg.get("fft_size", select_fft_size(self.sample_rate, self.buff_size)))
        self.if_gain = int(cfg.get("if_gain", 40))
        self.rf_gain = int(cfg.get("rf_gain", 4))
        self.agc = bool(self._to_bool(cfg.get("agc", False)))
        self.antenna = str(cfg.get("antenna", "Antenna A"))
        self.noise_floor_ref_db = float(cfg.get("noise_floor_ref_db", -110.0))
        self.snr_mode = str(cfg.get("snr_mode", "relative")).strip().lower() or "relative"
        self.fft_size_mode = self._normalize_fft_size_mode(cfg.get("fft_size_mode", "auto"))
        self.plot_refresh_fps = self._normalize_plot_refresh_fps(cfg.get("plot_refresh_fps", "auto"))
        self.auto_table_enabled = bool(self._to_bool(cfg.get("auto_table", True)))
        self.bias_tee = bool(self._to_bool(cfg.get("bias_tee", False)))
        self.fm_notch = bool(self._to_bool(cfg.get("fm_notch", False)))
        self.dab_notch = bool(self._to_bool(cfg.get("dab_notch", False)))
        self.ppm = float(cfg.get("ppm", 0.0))
        self.bandwidth_hz = float(cfg.get("bandwidth_hz", 25_000.0))
        self.smoothing = self._normalize_smoothing(cfg.get("smoothing", "light"))
        self.fft_window = str(cfg.get("fft_window", "blackman")).strip().lower()
        if self.fft_window not in WINDOW_ENBW_FACTORS:
            self.fft_window = "blackman"
        self.if_mode = str(cfg.get("if_mode", "auto")).strip().lower()
        if self.if_mode not in {"auto", "2048", "450", "zero_if"}:
            self.if_mode = "auto"
        self.source_index = int(cfg.get("source_index", 0))
        self.source_key = str(cfg.get("source_key", "")).strip()
        self._heal_frequency_state()
        self._plot_interval_s = 1.0 / max(1.0, self.fft_fps)
        self._fft_ema_alpha = float(SMOOTHING_PRESETS[self.smoothing])
        self._fft_db_ema: np.ndarray | None = None
        self._fft_win = None
        self._fft_win_size = 0
        self._fft_win_power = 1.0
        self._freq_axis = None
        self._freq_axis_key = None
        self.data_storage = DataStorage(max_history_size=int(cfg.get("history_size", 150)))
        self.data_storage.set_compute_average_enabled(False)
        self.data_storage.set_compute_peak_max_enabled(False)
        self.data_storage.set_compute_peak_min_enabled(False)
        self._refresh_plot_interval()
        self.refresh_sources(open_selected=True)

    def _heal_frequency_state(self) -> None:
        center_hz = float(self.center_freq)
        receiver_hz = float(self.receiver_freq_hz)
        sample_rate_hz = float(max(1.0, self.sample_rate))
        max_offset_hz = sample_rate_hz * 0.5
        if not math.isfinite(center_hz) or center_hz <= 0.0:
            self.center_freq = float(max(1.0, receiver_hz))
            return
        if not math.isfinite(receiver_hz) or receiver_hz <= 0.0:
            self.receiver_freq_hz = float(center_hz)
            return
        if abs(receiver_hz - center_hz) > max_offset_hz:
            self.logger.warning(
                "Healing inconsistent SDR frequency state: center=%.0f Hz receiver=%.0f Hz sample_rate=%.0f Hz. Recentering on receiver.",
                center_hz,
                receiver_hz,
                sample_rate_hz,
            )
            self.center_freq = float(receiver_hz)

    @staticmethod
    def _normalize_fft_size_mode(value: Any) -> str:
        return "manual" if str(value or "auto").strip().lower() == "manual" else "auto"

    @staticmethod
    def _normalize_plot_refresh_fps(value: Any) -> float | None:
        text = str(value or "auto").strip().lower()
        if text in {"", "auto", "none"}:
            return None
        try:
            fps = float(value)
        except Exception:
            return None
        return float(max(1.0, fps))

    def _effective_fft_fps(self) -> float:
        if self.plot_refresh_fps is not None:
            return float(max(1.0, self.plot_refresh_fps))
        base_fps = float(max(1.0, self.fft_fps))
        size_ratio = max(1.0, float(self.fft_size) / 32768.0)
        effective_fps = base_fps / size_ratio
        if self.fft_size >= 262144:
            effective_fps = min(effective_fps, 1.0)
        elif self.fft_size >= 131072:
            effective_fps = min(effective_fps, 2.0)
        elif self.fft_size >= 65536:
            effective_fps = min(effective_fps, 5.0)
        return float(max(1.0, effective_fps))

    def _refresh_plot_interval(self) -> None:
        self._plot_interval_s = 1.0 / self._effective_fft_fps()
        self._spectrum_wakeup.set()

    def _clamp_fft_size(self, fft_size: int) -> int:
        max_fft = int(max(1024, fft_max_for_sample_rate(self.sample_rate, self.buff_size)))
        if fft_size >= max_fft:
            return max_fft
        exponent = int(np.floor(np.log2(max(1024, int(fft_size)))))
        return int(max(1024, min(max_fft, 2**exponent)))

    def _ensure_iq_history_capacity_locked(self) -> None:
        capacity = int(max(1024, fft_max_for_sample_rate(self.sample_rate, self.buff_size)))
        if self._iq_history is not None and self._iq_history_capacity == capacity:
            return
        previous = self._latest_iq_window_copy_locked(min(self._iq_history_size, capacity))
        self._iq_history = np.zeros(capacity, dtype=np.complex64)
        self._iq_history_capacity = capacity
        self._iq_history_size = 0
        self._iq_history_write_pos = 0
        if previous is not None and previous.size > 0:
            self._append_iq_history_locked(previous)

    def _append_iq_history_locked(self, iq: np.ndarray) -> None:
        samples = np.asarray(iq, dtype=np.complex64)
        if samples.size == 0:
            return
        self._ensure_iq_history_capacity_locked()
        if self._iq_history is None or self._iq_history_capacity <= 0:
            return
        capacity = int(self._iq_history_capacity)
        if samples.size >= capacity:
            tail = samples[-capacity:]
            self._iq_history[:] = tail
            self._iq_history_size = capacity
            self._iq_history_write_pos = 0
            return
        end_space = capacity - self._iq_history_write_pos
        first = int(min(samples.size, end_space))
        self._iq_history[self._iq_history_write_pos : self._iq_history_write_pos + first] = samples[:first]
        remaining = int(samples.size - first)
        if remaining > 0:
            self._iq_history[:remaining] = samples[first:]
        self._iq_history_write_pos = (self._iq_history_write_pos + samples.size) % capacity
        self._iq_history_size = int(min(capacity, self._iq_history_size + samples.size))

    def _latest_iq_window_copy_locked(self, count: int) -> np.ndarray | None:
        if self._iq_history is None or self._iq_history_size <= 0 or self._iq_history_capacity <= 0:
            return None
        count = int(max(1, min(int(count), self._iq_history_size)))
        end = int(self._iq_history_write_pos)
        start = (end - count) % self._iq_history_capacity
        if start < end:
            return self._iq_history[start:end].copy()
        return np.concatenate((self._iq_history[start:], self._iq_history[:end]), axis=0)

    def available_fft_sizes(self) -> list[int]:
        max_fft = int(max(1024, fft_max_for_sample_rate(self.sample_rate, self.buff_size)))
        preferred = [
            1024,
            2048,
            4096,
            8192,
            16384,
            32768,
            65536,
            131072,
            262144,
            524288,
            1048576,
            2097152,
            4194304,
            8388608,
        ]
        return [int(size) for size in preferred if int(size) <= max_fft]

    @staticmethod
    def _to_bool(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        return str(value).strip().lower() in {"1", "true", "yes", "on"}

    @staticmethod
    def _normalize_smoothing(value: Any) -> str:
        normalized = str(value or "light").strip().lower()
        return normalized if normalized in SMOOTHING_PRESETS else "light"

    def _sdr_settings(self) -> dict:
        if not isinstance(self.settings, dict):
            return {}
        return self.settings.get("SDR", self.settings.get("sdr", {})) or {}

    def _emit_status(self, message: str) -> None:
        self.logger.info(message)
        try:
            self.status.emit(message)
        except Exception:
            pass

    def _emit_error(self, message: str) -> None:
        self.logger.error(message)
        try:
            self.error.emit(message)
        except Exception:
            pass

    def _looks_like_sdrplay(self, dev: Any) -> bool:
        if not isinstance(dev, dict):
            return False
        text = " ".join(str(v).lower() for v in dev.values())
        return "sdrplay" in text or "rsp" in text

    def _build_source_key(self, args: dict[str, Any]) -> str:
        return "|".join(f"{key}={value}" for key, value in sorted(args.items()))

    def _build_source_label(self, args: dict[str, Any], index: int) -> str:
        driver = str(args.get("driver", "soapy")).strip() or "soapy"
        label = str(args.get("label") or args.get("device") or args.get("name") or "").strip()
        serial = str(args.get("serial") or args.get("serialNumber") or "").strip()
        if label and serial:
            return f"{label} [{driver}] ({serial})"
        if label:
            return f"{label} [{driver}]"
        if serial:
            return f"{driver} ({serial})"
        return f"{driver} #{index + 1}"

    def _list_modules(self) -> list[str]:
        if SoapySDR is None:
            return []
        try:
            list_modules = getattr(SoapySDR, "listModules", None)
            if callable(list_modules):
                modules = list_modules()
                if isinstance(modules, (list, tuple)):
                    return [str(module) for module in modules]
        except Exception:
            pass
        return []

    def _enumerate_devices(self) -> list[dict[str, Any]]:
        if SoapySDR is None:
            return []
        devices: list[dict[str, Any]] = []

        def _coerce_kwargs_map(value: Any) -> dict[str, Any] | None:
            if isinstance(value, dict):
                return dict(value)
            asdict = getattr(value, "asdict", None)
            if callable(asdict):
                try:
                    result = asdict()
                    if isinstance(result, dict):
                        return dict(result)
                except Exception:
                    pass
            items = getattr(value, "items", None)
            if callable(items):
                try:
                    return {str(key): item for key, item in items()}
                except Exception:
                    pass
            try:
                return dict(value)
            except Exception:
                return None

        try:
            raw_devices = SoapySDR.Device_enumerate()
        except Exception as exc:
            self.logger.warning("Soapy enumerate failed: %s", exc)
            raw_devices = []
        for args in raw_devices:
            mapped_args = _coerce_kwargs_map(args)
            if mapped_args:
                devices.append(mapped_args)
        if devices:
            return devices
        for fallback_args in ({"driver": "sdrplay"}, {"driver": "sdrplay_api"}):
            try:
                raw_devices = SoapySDR.Device_enumerate(fallback_args)
            except Exception:
                raw_devices = []
            for args in raw_devices:
                mapped_args = _coerce_kwargs_map(args)
                if mapped_args:
                    devices.append(mapped_args)
            if devices:
                break
        return devices

    def _select_default_source_index(self, records: list[dict[str, Any]]) -> int:
        if not records:
            return 0
        if self.source_key:
            for index, record in enumerate(records):
                if str(record.get("key", "")) == self.source_key:
                    return index
        preferred_index = max(0, min(int(self.source_index), len(records) - 1))
        if preferred_index < len(records):
            return preferred_index
        for index, record in enumerate(records):
            if self._looks_like_sdrplay(record.get("args", {})):
                return index
        return 0

    def _extract_setting_keys(self) -> list[str]:
        if self._sdr is None:
            return []
        result: list[str] = []
        try:
            entries = self._sdr.getSettingInfo()
        except Exception:
            entries = []
        for entry in entries or []:
            key = getattr(entry, "key", None)
            if key is None and isinstance(entry, dict):
                key = entry.get("key")
            if key:
                result.append(str(key))
        return result

    def _extract_gain_names(self) -> list[str]:
        if self._sdr is None:
            return []
        try:
            names = self._sdr.listGains(SOAPY_SDR_RX, 0)
        except Exception:
            return []
        return [str(name) for name in names or []]

    def _ensure_valid_antenna(self) -> None:
        antennas = [str(item) for item in self.hwinfo.get("antennas", []) if str(item).strip()]
        if not antennas:
            antennas = ["Antenna A", "Antenna B", "Antenna C"]
            self.hwinfo["antennas"] = antennas
        if self.antenna not in antennas:
            self.antenna = antennas[0]

    def refresh_sources(self, *, open_selected: bool = False) -> None:
        self.hwinfo = {"modules": self._list_modules(), "devices": [], "sources": []}
        self._available_source_records = []
        self._setting_keys = []
        self._gain_names = []

        if SoapySDR is None:
            self.mode = "dummy"
            self.hwinfo["sources"] = [{"label": "Dummy source", "key": "dummy"}]
            self.hwinfo["antennas"] = ["Antenna A", "Antenna B", "Antenna C"]
            self.hwinfo["sample_rates"] = list(SAMPLE_RATE_PRESETS_HZ)
            if open_selected:
                self._sdr = None
            self._emit_status("SoapySDR not available; SDR client running in dummy mode.")
            self.mode_changed.emit(self.mode)
            self._emit_settings_changed()
            return

        devices = self._enumerate_devices()
        self.hwinfo["devices"] = list(devices)
        for index, args in enumerate(devices):
            record = {
                "args": dict(args),
                "key": self._build_source_key(dict(args)),
                "label": self._build_source_label(dict(args), index),
            }
            self._available_source_records.append(record)

        if not self._available_source_records:
            self.mode = "dummy"
            self.hwinfo["sources"] = [{"label": "Dummy source", "key": "dummy"}]
            self.hwinfo["antennas"] = ["Antenna A", "Antenna B", "Antenna C"]
            self.hwinfo["sample_rates"] = list(SAMPLE_RATE_PRESETS_HZ)
            if open_selected:
                self._close_stream()
                self._sdr = None
            self._emit_status("No Soapy SDR source detected; SDR client running in dummy mode.")
            self.mode_changed.emit(self.mode)
            self._emit_settings_changed()
            return

        self.source_index = self._select_default_source_index(self._available_source_records)
        self.source_key = str(self._available_source_records[self.source_index]["key"])
        self.hwinfo["sources"] = [
            {"label": record["label"], "key": record["key"]}
            for record in self._available_source_records
        ]
        if open_selected:
            self._open_selected_source()
        else:
            self._emit_settings_changed()

    def _open_selected_source(self) -> None:
        self._close_stream()
        self._sdr = None
        self._setting_keys = []
        self._gain_names = []

        if not self._available_source_records:
            self.mode = "dummy"
            self.hwinfo["antennas"] = ["Antenna A", "Antenna B", "Antenna C"]
            self.hwinfo["sample_rates"] = list(SAMPLE_RATE_PRESETS_HZ)
            self.mode_changed.emit(self.mode)
            self._emit_settings_changed()
            return

        record = self._available_source_records[max(0, min(self.source_index, len(self._available_source_records) - 1))]
        self.source_key = str(record.get("key", ""))
        args = dict(record.get("args", {}))
        try:
            self._sdr = SoapySDR.Device(args)
        except Exception as exc:
            self.logger.warning("Unable to open SDR source %s: %s", record.get("label", "?"), exc)
            self.mode = "dummy"
            self.hwinfo["antennas"] = ["Antenna A", "Antenna B", "Antenna C"]
            self.hwinfo["sample_rates"] = list(SAMPLE_RATE_PRESETS_HZ)
            self._emit_error(f"Unable to open source {record.get('label', '?')}: {exc}")
            self.mode_changed.emit(self.mode)
            self._emit_settings_changed()
            return

        self.mode = "hardware"
        try:
            antennas = self._sdr.listAntennas(SOAPY_SDR_RX, 0)
        except Exception:
            antennas = []
        try:
            sample_rates = self._sdr.listSampleRates(SOAPY_SDR_RX, 0)
        except Exception:
            sample_rates = []

        self.hwinfo["selected_device"] = args
        self.hwinfo["antennas"] = list(antennas) or ["Antenna A", "Antenna B", "Antenna C"]
        self.hwinfo["sample_rates"] = list(sample_rates) or list(SAMPLE_RATE_PRESETS_HZ)
        supported_rates = self._supported_sample_rates()
        if supported_rates:
            self.sample_rate = self._coerce_sample_rate(self.sample_rate, supported_rates)
        self._ensure_valid_antenna()
        self._setting_keys = self._extract_setting_keys()
        self._gain_names = self._extract_gain_names()
        self._emit_status(f"SDR source ready: {record.get('label', 'hardware')}")
        self.mode_changed.emit(self.mode)
        self._emit_settings_changed()

    @property
    def running(self) -> bool:
        if self.thread_manager is not None:
            worker = self.thread_manager.get_worker(self._thread_name)
            thread = getattr(self.thread_manager, "threads", {}).get(self._thread_name)
            return bool(worker and thread and thread.isRunning() and not getattr(worker, "abort", False))
        return bool(self._thread and self._thread.is_alive() and not self._thread_stop.is_set())

    def _wait_for_named_thread_stopped(self, thread_name: str, local_thread, timeout_s: float = 1.5) -> bool:
        if self.thread_manager is None:
            deadline = time.monotonic() + max(0.0, float(timeout_s))
            while local_thread and local_thread.is_alive() and time.monotonic() < deadline:
                local_thread.join(timeout=0.05)
            return not bool(local_thread and local_thread.is_alive())

        deadline = time.monotonic() + max(0.0, float(timeout_s))
        while time.monotonic() < deadline:
            thread = getattr(self.thread_manager, "threads", {}).get(thread_name)
            if thread is None or not thread.isRunning():
                return True
            thread.wait(50)
        thread = getattr(self.thread_manager, "threads", {}).get(thread_name)
        return bool(thread is None or not thread.isRunning())

    def _wait_for_stream_thread_stopped(self, timeout_s: float = 1.5) -> bool:
        return self._wait_for_named_thread_stopped(self._thread_name, self._thread, timeout_s)

    def _wait_for_spectrum_thread_stopped(self, timeout_s: float = 1.5) -> bool:
        return self._wait_for_named_thread_stopped(self._spectrum_thread_name, self._spectrum_thread, timeout_s)

    def start(self) -> None:
        if self.running:
            return
        if not self._wait_for_stream_thread_stopped(timeout_s=1.5) or not self._wait_for_spectrum_thread_stopped(timeout_s=1.5):
            self._emit_error("SDR stream restart blocked: previous worker thread is still running.")
            return
        if self.mode == "hardware":
            try:
                self._sdr = None
                gc.collect()
                self._open_selected_source()
                time.sleep(0.1)
            except Exception:
                pass
        self._thread_stop.clear()
        self._stream_reconfigure_requested.clear()
        self._spectrum_wakeup.clear()
        self._read_frame_counter = 0
        self._display_frame_counter = 0
        self._perf_timeouts = 0
        self._perf_overflows = 0
        self._perf_stream_errors = 0
        self._perf_fft_s = 0.0
        self._perf_storage_s = 0.0
        self._perf_emit_s = 0.0
        self._last_perf_emit = time.monotonic()
        self._started_emitted = False
        if self.thread_manager is not None:
            self.thread_manager.start_thread(self._thread_name, self._stream_loop)
            self.thread_manager.start_thread(self._spectrum_thread_name, self._spectrum_loop)
        else:
            self._thread = threading.Thread(target=self._stream_loop, name="SdrStream", daemon=True)
            self._spectrum_thread = threading.Thread(target=self._spectrum_loop, name="SdrSpectrum", daemon=True)
            self._thread.start()
            self._spectrum_thread.start()

    def stop(self) -> None:
        self._thread_stop.set()
        self._stream_reconfigure_requested.clear()
        self._spectrum_wakeup.set()
        if self.thread_manager is not None:
            try:
                self.thread_manager.stop_thread(self._thread_name)
            except Exception:
                pass
            try:
                self.thread_manager.stop_thread(self._spectrum_thread_name)
            except Exception:
                pass
        else:
            if self._thread and self._thread.is_alive():
                self._thread.join(timeout=1.0)
            if self._spectrum_thread and self._spectrum_thread.is_alive():
                self._spectrum_thread.join(timeout=1.0)
        self._wait_for_stream_thread_stopped(timeout_s=1.5)
        self._wait_for_spectrum_thread_stopped(timeout_s=1.5)
        if not self.running:
            self._close_stream()
        if getattr(self, "_started_emitted", False):
            self.stopped.emit()
            self._emit_status("SDR stream stopped.")
        self._started_emitted = False

    def close(self) -> None:
        self.stop()
        if not self.running:
            self._close_stream()
            self._sdr = None

    def _stream_loop(self) -> None:
        worker = self.thread_manager.get_worker(self._thread_name) if self.thread_manager is not None else None
        try:
            if self.mode == "hardware":
                self._open_stream_with_recovery()
            while not self._thread_stop.is_set():
                if worker is not None and getattr(worker, "abort", False):
                    break
                if self._stream_reconfigure_requested.is_set():
                    self._apply_pending_stream_reconfigure()
                try:
                    iq = self._read_next_block()
                except Exception as exc:
                    self._perf_stream_errors += 1
                    self._emit_error(f"SDR read failure: {exc}")
                    time.sleep(0.05)
                    continue
                self._cache_iq_block(iq)
        except Exception as exc:
            self.logger.exception("SDR runtime error: %s", exc)
            self._emit_error(f"SDR runtime error: {exc}")
        finally:
            self._close_stream()

    def _spectrum_loop(self) -> None:
        worker = self.thread_manager.get_worker(self._spectrum_thread_name) if self.thread_manager is not None else None
        last_plot_push = 0.0
        while not self._thread_stop.is_set():
            if worker is not None and getattr(worker, "abort", False):
                break
            interval_s = float(max(0.01, self._plot_interval_s))
            now = time.monotonic()
            remaining = max(0.0, interval_s - (now - last_plot_push))
            wait_s = min(0.1, remaining) if remaining > 0.0 else 0.0
            self._spectrum_wakeup.wait(timeout=wait_s)
            self._spectrum_wakeup.clear()
            if self._thread_stop.is_set():
                break
            now = time.monotonic()
            if now - last_plot_push < interval_s:
                continue
            self._publish_spectrum(timestamp=now)
            if now - self._last_perf_emit >= self._perf_emit_interval_s:
                self._emit_perf_snapshot(now)
            last_plot_push = now

    def _apply_pending_stream_reconfigure(self) -> None:
        if not self._stream_reconfigure_requested.is_set():
            return
        self._stream_reconfigure_requested.clear()
        if self.mode != "hardware":
            return
        self._close_stream()
        if not self._thread_stop.is_set():
            self._open_stream_with_recovery()

    def _open_stream_with_recovery(self) -> None:
        try:
            self._open_stream()
            return
        except Exception as first_exc:
            self.logger.warning(
                "SDR stream open failed (freq=%.0f Hz, rate=%.0f Hz, antenna=%s): %s. Retrying after source reopen.",
                float(self.center_freq),
                float(self.sample_rate),
                str(self.antenna),
                first_exc,
            )
            self._close_stream()
            try:
                self._open_selected_source()
                time.sleep(0.2)
            except Exception:
                pass
            self._open_stream()

    def _open_stream(self) -> None:
        if self.mode != "hardware" or self._sdr is None or self._stream is not None:
            return
        self._apply_hardware_settings()
        self._stream = self._sdr.setupStream(SOAPY_SDR_RX, SOAPY_SDR_CF32)
        activate_result = self._sdr.activateStream(self._stream, 0)
        if isinstance(activate_result, (int, float)) and int(activate_result) < 0:
            self.logger.error(
                "activateStream failed with code %s (freq=%.0f Hz, rate=%.0f Hz, antenna=%s, agc=%s, if_gain=%s, rf_gain=%s).",
                int(activate_result),
                float(self.center_freq),
                float(self.sample_rate),
                str(self.antenna),
                bool(self.agc),
                int(self.if_gain),
                int(self.rf_gain),
            )
            try:
                self._sdr.closeStream(self._stream)
            except Exception:
                pass
            self._stream = None
            raise RuntimeError(f"activateStream returned {int(activate_result)}")
        if not getattr(self, "_started_emitted", False):
            self._started_emitted = True
            self.started.emit()
            self._emit_status(f"SDR stream started in {self.mode} mode.")

    def _close_stream(self) -> None:
        if self._sdr is None or self._stream is None:
            return
        try:
            self._sdr.deactivateStream(self._stream)
        except Exception:
            pass
        try:
            self._sdr.closeStream(self._stream)
        except Exception:
            pass
        self._stream = None

    def _resolve_setting_key(self, candidates: list[str]) -> str | None:
        lookup = {str(key).lower(): str(key) for key in self._setting_keys}
        for candidate in candidates:
            exact = lookup.get(str(candidate).lower())
            if exact:
                return exact
        return None

    def _write_setting(self, candidates: list[str], value: Any) -> bool:
        if self.mode != "hardware" or self._sdr is None:
            return False
        key = self._resolve_setting_key(candidates)
        if key is None:
            return False
        try:
            self._sdr.writeSetting(key, str(value).lower() if isinstance(value, bool) else str(value))
            return True
        except Exception:
            return False

    def _apply_component_gain(self, name: str, value: int) -> bool:
        if self.mode != "hardware" or self._sdr is None:
            return False
        if name in self._gain_names:
            try:
                self._sdr.setGain(SOAPY_SDR_RX, 0, name, int(value))
                return True
            except Exception:
                return False
        return False

    def _apply_frequency_correction(self) -> None:
        if self.mode != "hardware" or self._sdr is None:
            return
        try:
            self._sdr.setFrequencyCorrection(SOAPY_SDR_RX, 0, float(self.ppm))
        except Exception:
            self._write_setting(["freqCorrection", "ppm", "ppm_corr", "corr_ppm"], self.ppm)

    def _apply_hardware_settings(self) -> None:
        if self.mode != "hardware" or self._sdr is None:
            return
        self._sdr.setSampleRate(SOAPY_SDR_RX, 0, self.sample_rate)
        self._sdr.setFrequency(SOAPY_SDR_RX, 0, self.center_freq)
        try:
            self._sdr.setGainMode(SOAPY_SDR_RX, 0, self.agc)
        except Exception:
            pass
        if not self._apply_component_gain("IFGR", self.if_gain):
            try:
                self._sdr.setGain(SOAPY_SDR_RX, 0, float(self.if_gain))
            except Exception:
                pass
        self._apply_component_gain("RFGR", self.rf_gain)
        if self.antenna:
            try:
                self._sdr.setAntenna(SOAPY_SDR_RX, 0, self.antenna)
            except Exception:
                pass
        self._apply_frequency_correction()
        self._write_setting(["biasT_ctrl", "biasTEnable", "biasT"], self.bias_tee)
        self._write_setting(["rfnotch_ctrl", "rfNotch_ctrl", "rf_notch", "fmnotch_ctrl"], self.fm_notch)
        self._write_setting(["dabnotch_ctrl", "dabNotch_ctrl", "dab_notch"], self.dab_notch)
        if self.if_mode != "auto":
            self._write_setting(["if_mode", "ifMode", "if_khz", "ifKhz"], self.if_mode)

    def _read_next_block(self) -> np.ndarray:
        if self.mode != "hardware":
            return self._generate_dummy_iq_block()
        if self._sdr is None:
            self._open_selected_source()
            if self._sdr is None:
                raise RuntimeError("hardware SDR device is not open")
        if self._stream is None:
            self._open_stream()
        if self._read_buffer is None or int(self._read_buffer.size) != int(self.buff_size):
            self._read_buffer = np.zeros(self.buff_size, dtype=np.complex64)
        buffer = self._read_buffer
        try:
            stream_result = self._sdr.readStream(self._stream, [buffer], self.buff_size, timeoutUs=int(self._read_timeout_us))
        except TypeError:
            stream_result = self._sdr.readStream(self._stream, [buffer], self.buff_size)
        if stream_result.ret > 0:
            return buffer[: int(stream_result.ret)].copy()
        if stream_result.ret == self._timeout_code:
            self._perf_timeouts += 1
            time.sleep(0.001)
            return np.zeros(self.buff_size, dtype=np.complex64)
        if stream_result.ret == self._overflow_code:
            self._perf_overflows += 1
            now = time.monotonic()
            if now - self._last_overflow_log_t >= 2.0:
                self.logger.warning("SDR overflow reported by driver (readStream=%s); dropping samples.", stream_result.ret)
                self._last_overflow_log_t = now
            time.sleep(0.001)
            return np.zeros(self.buff_size, dtype=np.complex64)
        raise RuntimeError(f"readStream returned {stream_result.ret}")

    def _generate_dummy_iq_block(self) -> np.ndarray:
        fs = float(max(1.0, self.sample_rate))
        n = np.arange(self.buff_size, dtype=np.float32)
        phase = float(self._dummy_phase)
        tone1 = np.exp(1j * (phase + 2.0 * np.pi * 120_000.0 * n / fs))
        tone2 = np.exp(1j * (0.3 * phase + 2.0 * np.pi * -260_000.0 * n / fs))
        noise = 0.08 * (np.random.randn(self.buff_size) + 1j * np.random.randn(self.buff_size))
        iq = (0.55 * tone1 + 0.30 * tone2 + noise).astype(np.complex64)
        self._dummy_phase = float((phase + 2.0 * np.pi * self.buff_size / fs) % (2.0 * np.pi))
        time.sleep(min(0.02, self.buff_size / fs))
        return iq

    def _cache_iq_block(self, iq: np.ndarray) -> None:
        iq_copy = np.asarray(iq, dtype=np.complex64).copy()
        with self._state_lock:
            self._latest_iq = iq_copy
            self._latest_timestamp = time.monotonic()
            self._append_iq_history_locked(iq_copy)
        self._read_frame_counter += 1
        self._spectrum_wakeup.set()

    def _ensure_fft_window(self) -> None:
        n = int(max(8, self.fft_size))
        if self._fft_win is not None and self._fft_win_size == n:
            return
        win = make_window(n, self.fft_window)
        self._fft_win = win
        self._fft_win_size = n
        coherent_gain = float(np.sum(win))
        self._fft_win_power = (coherent_gain * coherent_gain) or 1.0
        self._fft_db_ema = None
        self._freq_axis = None
        self._freq_axis_key = None

    def _get_freq_axis(self, n_bins: int) -> np.ndarray:
        key = (int(n_bins), float(self.sample_rate), float(self.center_freq))
        if self._freq_axis is not None and self._freq_axis_key == key:
            return self._freq_axis
        self._freq_axis = frequency_axis(int(n_bins), self.sample_rate, self.center_freq)
        self._freq_axis_key = key
        return self._freq_axis

    def _compute_spectrum_snapshot(self, iq_data: np.ndarray, *, smooth_trace: bool) -> np.ndarray:
        self._ensure_fft_window()
        spectrum_db = compute_power_spectrum_db(
            iq_data,
            self.fft_size,
            window=self._fft_win,
            window_power=self._fft_win_power,
        )
        if smooth_trace and self._fft_ema_alpha < 0.9999:
            self._fft_db_ema = apply_ema(spectrum_db, self._fft_db_ema, alpha=self._fft_ema_alpha)
            return self._fft_db_ema.astype(np.float32, copy=False)
        self._fft_db_ema = spectrum_db.astype(np.float32, copy=False)
        return self._fft_db_ema

    def _compute_raw_spectrum_snapshot(self, iq_data: np.ndarray) -> np.ndarray:
        self._ensure_fft_window()
        return compute_power_spectrum_db(
            iq_data,
            self.fft_size,
            window=self._fft_win,
            window_power=self._fft_win_power,
        )

    def _latest_iq_for_fft(self) -> np.ndarray | None:
        with self._state_lock:
            return self._latest_iq_window_copy_locked(self.fft_size)

    def compute_spectrum(self, iq_data: np.ndarray) -> np.ndarray:
        """Compute the display spectrum in dB with the selected smoothing preset."""
        return self._compute_spectrum_snapshot(iq_data, smooth_trace=self.smoothing != "off")

    def _publish_spectrum(self, *, timestamp: float) -> None:
        fft_iq = self._latest_iq_for_fft()
        if fft_iq is None or fft_iq.size == 0:
            return
        start_t = time.perf_counter()
        raw_spectrum_db = self._compute_raw_spectrum_snapshot(fft_iq)
        after_fft_t = time.perf_counter()
        if self.smoothing != "off" and self._fft_ema_alpha < 0.9999:
            self._fft_db_ema = apply_ema(raw_spectrum_db, self._fft_db_ema, alpha=self._fft_ema_alpha)
            spectrum_db = self._fft_db_ema.astype(np.float32, copy=False)
        else:
            self._fft_db_ema = raw_spectrum_db.astype(np.float32, copy=False)
            spectrum_db = self._fft_db_ema
        freqs = self._get_freq_axis(len(raw_spectrum_db))
        with self._state_lock:
            self._latest_spectrum_db = spectrum_db.copy()
            self._latest_freqs = freqs.copy()
        self.data_storage.update({"timestamp": timestamp, "x": freqs, "y": spectrum_db, "history_y": raw_spectrum_db})
        after_storage_t = time.perf_counter()
        snr_db = compute_snr(spectrum_db, self.snr_mode, self.noise_floor_ref_db)
        try:
            self.spectrum_updated.emit(self.data_storage)
            self.snr_updated.emit(float(snr_db), self.snr_mode)
        except Exception:
            pass
        after_emit_t = time.perf_counter()
        self._display_frame_counter += 1
        self._perf_fft_s += max(0.0, after_fft_t - start_t)
        self._perf_storage_s += max(0.0, after_storage_t - after_fft_t)
        self._perf_emit_s += max(0.0, after_emit_t - after_storage_t)

    def _emit_perf_snapshot(self, now: float) -> None:
        elapsed = max(1e-6, now - self._last_perf_emit)
        display_count = int(self._display_frame_counter)
        perf_snapshot = {
            "mode": self.mode,
            "read_frames": int(self._read_frame_counter),
            "display_frames": display_count,
            "sample_rate_hz": float(self.sample_rate),
            "center_freq_hz": float(self.center_freq),
            "fft_size": int(self.fft_size),
            "fft_window_ms": float((float(self.fft_size) / max(1.0, float(self.sample_rate))) * 1000.0),
            "buffer_size": int(self.buff_size),
            "timeouts": int(self._perf_timeouts),
            "overflows": int(self._perf_overflows),
            "stream_errors": int(self._perf_stream_errors),
            "read_fps": float(self._read_frame_counter / elapsed),
            "display_fps": float(display_count / elapsed),
            "fft_ms_avg": float((self._perf_fft_s / max(1, display_count)) * 1000.0),
            "storage_ms_avg": float((self._perf_storage_s / max(1, display_count)) * 1000.0),
            "emit_ms_avg": float((self._perf_emit_s / max(1, display_count)) * 1000.0),
        }
        self._last_perf_emit = now
        self._read_frame_counter = 0
        self._display_frame_counter = 0
        self._perf_fft_s = 0.0
        self._perf_storage_s = 0.0
        self._perf_emit_s = 0.0
        try:
            self.perf_updated.emit(perf_snapshot)
        except Exception:
            pass

    def update_fft_for_view(self, visible_span_hz: float, pixel_width: int) -> None:
        if self.fft_size_mode != "auto":
            return
        fs = float(max(1.0, self.sample_rate))
        span = float(max(1.0, min(abs(visible_span_hz), fs)))
        width_px = int(max(128, pixel_width))
        base_fft = select_fft_size(fs, self.buff_size)
        max_fft = fft_max_for_sample_rate(fs, self.buff_size)
        zoom_target = fs * float(width_px) * 6.0 / span
        zoom_fft = int(2 ** np.ceil(np.log2(max(1024.0, zoom_target))))
        desired = int(min(max_fft, max(base_fft, zoom_fft)))
        if desired == int(self.fft_size):
            return
        self.fft_size = desired
        self._fft_win = None
        self._fft_win_size = 0
        self._fft_db_ema = None
        self._freq_axis = None
        self._freq_axis_key = None
        self._emit_settings_changed()

    def _restart_if_needed(self) -> None:
        if not self.running:
            if self.mode == "hardware":
                self._close_stream()
            return
        if self.mode == "hardware":
            self.stop()
            self.start()

    def set_source_index(self, source_index: int) -> None:
        if not self._available_source_records:
            return
        source_index = int(max(0, min(int(source_index), len(self._available_source_records) - 1)))
        if source_index == self.source_index and self.mode == "hardware":
            return
        was_running = self.running
        if was_running:
            self.stop()
        self.source_index = source_index
        self._open_selected_source()
        if was_running:
            self.start()
        self._emit_settings_changed()

    def set_frequency(self, frequency_hz: float) -> None:
        self.center_freq = float(frequency_hz)
        self.receiver_freq_hz = float(frequency_hz)
        self._freq_axis = None
        self._freq_axis_key = None
        if self.mode == "hardware" and self._sdr is not None and self._stream is not None:
            self._sdr.setFrequency(SOAPY_SDR_RX, 0, self.center_freq)
        self._emit_settings_changed()

    def set_receiver_frequency(self, frequency_hz: float) -> None:
        self.receiver_freq_hz = float(frequency_hz)
        self._emit_settings_changed()

    def set_center_frequency(self, frequency_hz: float) -> None:
        self.center_freq = float(frequency_hz)
        self._freq_axis = None
        self._freq_axis_key = None
        if self.mode == "hardware" and self._sdr is not None and self._stream is not None:
            self._sdr.setFrequency(SOAPY_SDR_RX, 0, self.center_freq)
        self._emit_settings_changed()

    def set_sample_rate(self, sample_rate_hz: float) -> None:
        requested_rate = float(sample_rate_hz)
        supported_rates = self._supported_sample_rates()
        reconfigure_stream = bool(self.mode == "hardware" and self.running)
        self.sample_rate = self._coerce_sample_rate(requested_rate, supported_rates)
        self.bandwidth_hz = float(min(self._max_bandwidth_hz(), max(100.0, float(self.bandwidth_hz))))
        if self.fft_size_mode == "auto":
            self.fft_size = select_fft_size(self.sample_rate, self.buff_size)
        else:
            self.fft_size = self._clamp_fft_size(self.fft_size)
        self._refresh_plot_interval()
        self._fft_win = None
        self._fft_win_size = 0
        self._fft_db_ema = None
        self._freq_axis = None
        self._freq_axis_key = None
        with self._state_lock:
            self._ensure_iq_history_capacity_locked()
        if reconfigure_stream:
            self._stream_reconfigure_requested.set()
        elif self.mode == "hardware" and self._sdr is not None:
            try:
                self._sdr.setSampleRate(SOAPY_SDR_RX, 0, self.sample_rate)
            except Exception:
                self._restart_if_needed()
        self._emit_settings_changed()

    def set_fft_window(self, window_name: str) -> None:
        normalized = str(window_name or "blackman").strip().lower()
        if normalized not in WINDOW_ENBW_FACTORS:
            normalized = "blackman"
        if normalized == self.fft_window:
            return
        self.fft_window = normalized
        self._fft_win = None
        self._fft_win_size = 0
        self._fft_db_ema = None
        self._emit_settings_changed()

    def set_if_mode(self, if_mode: str) -> None:
        normalized = str(if_mode or "auto").strip().lower()
        if normalized not in {"auto", "2048", "450", "zero_if"}:
            normalized = "auto"
        if normalized == self.if_mode:
            return
        self.if_mode = normalized
        if self.mode == "hardware" and self._sdr is not None and normalized != "auto":
            self._write_setting(["if_mode", "ifMode", "if_khz", "ifKhz"], normalized)
        self._emit_settings_changed()

    def _supported_sample_rates(self) -> list[float]:
        supported: list[float] = []
        for raw_rate in self.hwinfo.get("sample_rates", []):
            try:
                rate = float(raw_rate)
            except (TypeError, ValueError):
                continue
            if rate > 0.0:
                supported.append(rate)
        return sorted(set(supported))

    def _coerce_sample_rate(self, sample_rate_hz: float, supported_rates: list[float] | None = None) -> float:
        target = float(sample_rate_hz)
        rates = supported_rates if supported_rates is not None else self._supported_sample_rates()
        if not rates:
            return target
        nearest = min(rates, key=lambda rate: abs(rate - target))
        if abs(nearest - target) > 1.0:
            self.logger.info("Adjusting sample rate from %.0f Hz to supported %.0f Hz.", target, nearest)
        return float(nearest)

    def set_fft_size(self, fft_size: int | None) -> None:
        if fft_size is None:
            mode = "auto"
            next_size = int(select_fft_size(self.sample_rate, self.buff_size))
        else:
            mode = "manual"
            next_size = int(self._clamp_fft_size(int(fft_size)))
        if mode == self.fft_size_mode and next_size == int(self.fft_size):
            return
        self.fft_size_mode = mode
        self.fft_size = next_size
        self._refresh_plot_interval()
        self._fft_win = None
        self._fft_win_size = 0
        self._fft_db_ema = None
        self._freq_axis = None
        self._freq_axis_key = None
        with self._state_lock:
            self._ensure_iq_history_capacity_locked()
        self._emit_settings_changed()

    def set_plot_refresh_fps(self, fps: float | None) -> None:
        normalized = None if fps is None else float(max(1.0, fps))
        if normalized == self.plot_refresh_fps:
            return
        self.plot_refresh_fps = normalized
        self._refresh_plot_interval()
        self._emit_settings_changed()

    def update_if_gain(self, value: int) -> None:
        self.if_gain = int(value)
        if self.mode == "hardware" and self._sdr is not None:
            if not self._apply_component_gain("IFGR", self.if_gain):
                try:
                    self._sdr.setGain(SOAPY_SDR_RX, 0, float(self.if_gain))
                except Exception:
                    pass
        self._emit_settings_changed()

    def update_rf_gain(self, value: int) -> None:
        self.rf_gain = int(value)
        if self.mode == "hardware" and self._sdr is not None:
            self._apply_component_gain("RFGR", self.rf_gain)
        self._emit_settings_changed()

    def update_agc(self, enabled: bool) -> None:
        self.agc = bool(enabled)
        if self.mode == "hardware" and self._sdr is not None:
            try:
                self._sdr.setGainMode(SOAPY_SDR_RX, 0, self.agc)
            except Exception:
                pass
        self._emit_settings_changed()

    def set_antenna(self, antenna: str) -> None:
        self.antenna = str(antenna or "")
        if self.mode == "hardware" and self._sdr is not None and self.antenna:
            try:
                self._sdr.setAntenna(SOAPY_SDR_RX, 0, self.antenna)
            except Exception:
                self.logger.warning("Unable to set antenna '%s'", self.antenna)
        self._emit_settings_changed()

    def set_snr_mode(self, mode: str) -> None:
        normalized = str(mode or "relative").strip().lower()
        self.snr_mode = "absolute" if normalized == "absolute" else "relative"
        self._emit_settings_changed()

    def set_noise_floor_ref_db(self, value: float) -> None:
        self.noise_floor_ref_db = float(value)
        self._emit_settings_changed()

    def set_spectrum_trace_alpha(self, alpha: float) -> None:
        clipped = float(np.clip(alpha, 0.01, 1.0))
        self._fft_ema_alpha = clipped
        if clipped >= 0.9999:
            self.smoothing = "off"
        else:
            self.smoothing = min(SMOOTHING_PRESETS, key=lambda key: abs(SMOOTHING_PRESETS[key] - clipped))
        self._fft_db_ema = None
        self._emit_settings_changed()

    def set_smoothing(self, level: str) -> None:
        self.smoothing = self._normalize_smoothing(level)
        self._fft_ema_alpha = float(SMOOTHING_PRESETS[self.smoothing])
        self._fft_db_ema = None
        self._emit_settings_changed()

    def set_auto_table_enabled(self, enabled: bool) -> None:
        self.auto_table_enabled = bool(enabled)
        self._emit_settings_changed()

    def set_bias_tee(self, enabled: bool) -> None:
        self.bias_tee = bool(enabled)
        self._write_setting(["biasT_ctrl", "biasTEnable", "biasT"], self.bias_tee)
        self._emit_settings_changed()

    def set_fm_notch(self, enabled: bool) -> None:
        self.fm_notch = bool(enabled)
        self._write_setting(["rfnotch_ctrl", "rfNotch_ctrl", "rf_notch", "fmnotch_ctrl"], self.fm_notch)
        self._emit_settings_changed()

    def set_dab_notch(self, enabled: bool) -> None:
        self.dab_notch = bool(enabled)
        self._write_setting(["dabnotch_ctrl", "dabNotch_ctrl", "dab_notch"], self.dab_notch)
        self._emit_settings_changed()

    def set_ppm(self, value: float) -> None:
        self.ppm = float(value)
        self._apply_frequency_correction()
        self._emit_settings_changed()

    def set_bandwidth(self, bandwidth_hz: float) -> None:
        self.bandwidth_hz = float(min(self._max_bandwidth_hz(), max(100.0, abs(float(bandwidth_hz)))))
        self._emit_settings_changed()

    def _max_bandwidth_hz(self) -> float:
        half_rate = float(max(100.0, float(self.sample_rate) * 0.5))
        return float(max(100.0, np.nextafter(half_rate, 0.0)))

    def _emit_settings_changed(self) -> None:
        snapshot = self.snapshot_state()
        try:
            self.settings_changed.emit(snapshot)
        except Exception:
            pass

    def snapshot_state(self) -> dict[str, Any]:
        sources = list(self.hwinfo.get("sources", []))
        source_label = ""
        if sources and 0 <= int(self.source_index) < len(sources):
            source_label = str(sources[int(self.source_index)].get("label", ""))
        return {
            "mode": self.mode,
            "sample_rate_hz": float(self.sample_rate),
            "center_freq_hz": float(self.center_freq),
            "receiver_freq_hz": float(self.receiver_freq_hz),
            "bandwidth_hz": float(self.bandwidth_hz),
            "buffer_size": int(self.buff_size),
            "fft_size": int(self.fft_size),
            "fft_size_mode": str(self.fft_size_mode),
            "fft_window": str(self.fft_window),
            "plot_refresh_fps": None if self.plot_refresh_fps is None else float(self.plot_refresh_fps),
            "if_gain": int(self.if_gain),
            "rf_gain": int(self.rf_gain),
            "agc": bool(self.agc),
            "antenna": str(self.antenna),
            "snr_mode": str(self.snr_mode),
            "noise_floor_ref_db": float(self.noise_floor_ref_db),
            "antennas": list(self.hwinfo.get("antennas", [])),
            "sample_rates": list(self.hwinfo.get("sample_rates", [])),
            "frames": int(self._read_frame_counter),
            "running": bool(self.running),
            "smoothing": str(self.smoothing),
            "auto_table": bool(self.auto_table_enabled),
            "bias_tee": bool(self.bias_tee),
            "fm_notch": bool(self.fm_notch),
            "dab_notch": bool(self.dab_notch),
            "ppm": float(self.ppm),
            "if_mode": str(self.if_mode),
            "bin_width_hz": float(max(1e-12, float(self.sample_rate) / max(1, int(self.fft_size)))),
            "rbw_hz": float(
                max(
                    1e-12,
                    (float(self.sample_rate) / max(1, int(self.fft_size)))
                    * float(WINDOW_ENBW_FACTORS.get(self.fft_window, 1.73)),
                )
            ),
            "sources": sources,
            "source_index": int(self.source_index),
            "source_key": str(self.source_key),
            "source_label": source_label,
        }

    def _latest_iq_copy(self) -> np.ndarray | None:
        with self._state_lock:
            latest = self._latest_iq_window_copy_locked(self.fft_size)
            if latest is not None and latest.size > 0:
                return latest
            if self._latest_iq is None:
                return None
            return self._latest_iq.copy()

    def measure_band_power(
        self,
        center_offset_hz: float,
        bandwidth_hz: float,
        integration_s: float,
    ) -> float:
        """Measure mean in-band power in dB over the requested integration duration."""
        integration_s = float(max(0.01, integration_s))
        bandwidth_hz = float(max(1.0, abs(bandwidth_hz)))
        deadline = time.monotonic() + integration_s
        accum_linear = 0.0
        accum_bins = 0
        frames = 0
        while time.monotonic() < deadline or frames == 0:
            iq = self._latest_iq_copy()
            if iq is None or iq.size == 0:
                iq = self._read_next_block()
            spectrum_db = self._compute_spectrum_snapshot(iq, smooth_trace=False)
            freqs = self._get_freq_axis(len(spectrum_db))
            band_center = float(self.center_freq + center_offset_hz)
            half_bw = bandwidth_hz / 2.0
            mask = (freqs >= band_center - half_bw) & (freqs <= band_center + half_bw)
            if not np.any(mask):
                nearest = int(np.argmin(np.abs(freqs - band_center)))
                mask = np.zeros_like(freqs, dtype=bool)
                mask[nearest] = True
            band_db = spectrum_db[mask]
            band_linear = np.power(10.0, band_db / 10.0, dtype=np.float64)
            accum_linear += float(np.sum(band_linear))
            accum_bins += int(band_linear.size)
            frames += 1
            if integration_s > 0.02:
                time.sleep(min(0.01, integration_s / 10.0))
        if accum_bins <= 0:
            raise RuntimeError("No FFT bins were available for band-power measurement.")
        return float(10.0 * np.log10((accum_linear / accum_bins) + 1e-12))

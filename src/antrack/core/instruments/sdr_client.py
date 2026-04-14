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
    apply_ema,
    blackman_window,
    compute_power_spectrum_db,
    compute_snr,
    fft_max_for_sample_rate,
    frequency_axis,
    select_fft_size,
)

try:
    import SoapySDR  # type: ignore
    from SoapySDR import SOAPY_SDR_CF32, SOAPY_SDR_RX  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    SoapySDR = None
    SOAPY_SDR_CF32 = None
    SOAPY_SDR_RX = None


class SdrClient(QObject):
    """Minimal SDR client with dummy fallback, continuous spectrum, and band-power APIs."""

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
        self._thread = None
        self._thread_stop = threading.Event()
        self._state_lock = threading.RLock()
        self._latest_iq: np.ndarray | None = None
        self._latest_freqs: np.ndarray | None = None
        self._latest_spectrum_db: np.ndarray | None = None
        self._latest_timestamp = 0.0
        self._dummy_phase = 0.0
        self._frame_counter = 0
        self._perf_timeouts = 0
        self._perf_stream_errors = 0
        self._last_perf_emit = 0.0

        cfg = self._sdr_settings()
        self.sample_rate = float(cfg.get("sample_rate_hz", 2_000_000.0))
        self.center_freq = float(cfg.get("center_freq_hz", 1_420_000_000.0))
        self.buff_size = int(cfg.get("buffer_size", 16_384))
        self.fft_fps = float(cfg.get("fft_fps", 20.0))
        self.fft_size = int(cfg.get("fft_size", select_fft_size(self.sample_rate, self.buff_size)))
        self.if_gain = int(cfg.get("if_gain", 20))
        self.rf_gain = int(cfg.get("rf_gain", 0))
        self.agc = bool(self._to_bool(cfg.get("agc", False)))
        self.antenna = str(cfg.get("antenna", ""))
        self.noise_floor_ref_db = float(cfg.get("noise_floor_ref_db", -110.0))
        self.snr_mode = str(cfg.get("snr_mode", "relative")).strip().lower() or "relative"
        self._plot_interval_s = 1.0 / max(1.0, self.fft_fps)
        self._fft_ema_alpha = float(cfg.get("spectrum_trace_alpha", 0.35))
        self._fft_db_ema: np.ndarray | None = None
        self._fft_win = None
        self._fft_win_size = 0
        self._fft_win_power = 1.0
        self._freq_axis = None
        self._freq_axis_key = None
        self._stream = None
        self._sdr = None
        self._timeout_code = int(getattr(SoapySDR, "SOAPY_SDR_TIMEOUT", -1)) if SoapySDR else -1
        self.mode = "dummy"
        self.hwinfo: dict[str, Any] = {}
        self.data_storage = DataStorage(max_history_size=int(cfg.get("history_size", 150)))
        self.data_storage.set_compute_average_enabled(True)
        self.data_storage.set_compute_peak_max_enabled(True)
        self.data_storage.set_compute_peak_min_enabled(True)
        self._init_device()

    @staticmethod
    def _to_bool(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        return str(value).strip().lower() in {"1", "true", "yes", "on"}

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

    def _init_device(self) -> None:
        self.hwinfo = {"modules": [], "devices": []}
        if SoapySDR is None:
            self.mode = "dummy"
            self._emit_status("SoapySDR not available; SDR client running in dummy mode.")
            self.mode_changed.emit(self.mode)
            self._sdr = None
            self.hwinfo["antennas"] = ["Dummy A"]
            self.hwinfo["sample_rates"] = [self.sample_rate]
            return

        try:
            list_modules = getattr(SoapySDR, "listModules", None)
            if callable(list_modules):
                modules = list_modules()
                if isinstance(modules, (list, tuple)):
                    self.hwinfo["modules"] = [str(module) for module in modules]
        except Exception:
            self.hwinfo["modules"] = []

        try:
            devices = SoapySDR.Device_enumerate()
        except Exception as exc:
            devices = []
            self.logger.warning("Soapy enumerate failed: %s", exc)

        if not devices:
            for args in ({"driver": "sdrplay"}, {"driver": "sdrplay_api"}):
                try:
                    devices = SoapySDR.Device_enumerate(args)
                except Exception:
                    devices = []
                if devices:
                    break
        self.hwinfo["devices"] = devices

        def looks_like_sdrplay(dev: Any) -> bool:
            if not isinstance(dev, dict):
                return False
            text = " ".join(str(v).lower() for v in dev.values())
            return "sdrplay" in text or "rsp" in text

        open_errors: list[str] = []
        opened = None
        for dev in devices:
            if not looks_like_sdrplay(dev):
                continue
            try:
                opened = SoapySDR.Device(dev)
                self.hwinfo["selected_device"] = dict(dev)
                break
            except Exception as exc:
                open_errors.append(f"enum-open {dev}: {exc}")

        if opened is None:
            for args in ({"driver": "sdrplay"}, {"driver": "sdrplay_api"}):
                try:
                    opened = SoapySDR.Device(args)
                    self.hwinfo["selected_device"] = dict(args)
                    break
                except Exception as exc:
                    open_errors.append(f"driver-open {args}: {exc}")

        self._sdr = opened
        if self._sdr is None:
            self.mode = "dummy"
            self._emit_status("No SDRplay device opened; falling back to dummy mode.")
            for error_msg in open_errors[:8]:
                self.logger.warning("SDR open attempt failed: %s", error_msg)
            self.hwinfo["antennas"] = ["Dummy A"]
            self.hwinfo["sample_rates"] = [self.sample_rate]
        else:
            self.mode = "hardware"
            try:
                antennas = self._sdr.listAntennas(SOAPY_SDR_RX, 0)
            except Exception:
                antennas = []
            try:
                sample_rates = self._sdr.listSampleRates(SOAPY_SDR_RX, 0)
            except Exception:
                sample_rates = []
            self.hwinfo["antennas"] = list(antennas) or ["Antenna A"]
            self.hwinfo["sample_rates"] = list(sample_rates) or [self.sample_rate]
            self._emit_status("SDRplay device detected and ready.")
        try:
            self.mode_changed.emit(self.mode)
        except Exception:
            pass
        self._emit_settings_changed()

    @property
    def running(self) -> bool:
        if self.thread_manager is not None:
            worker = self.thread_manager.get_worker(self._thread_name)
            thread = getattr(self.thread_manager, "threads", {}).get(self._thread_name)
            return bool(worker and thread and thread.isRunning() and not getattr(worker, "abort", False))
        return bool(self._thread and self._thread.is_alive() and not self._thread_stop.is_set())

    def start(self) -> None:
        if self.running:
            return
        self._thread_stop.clear()
        self._frame_counter = 0
        self._perf_timeouts = 0
        self._perf_stream_errors = 0
        self._last_perf_emit = time.monotonic()
        if self.thread_manager is not None:
            self.thread_manager.start_thread(self._thread_name, self._stream_loop)
        else:
            self._thread = threading.Thread(target=self._stream_loop, name="SdrStream", daemon=True)
            self._thread.start()
        self.started.emit()
        self._emit_status(f"SDR stream started in {self.mode} mode.")

    def stop(self) -> None:
        self._thread_stop.set()
        if self.thread_manager is not None:
            try:
                self.thread_manager.stop_thread(self._thread_name)
            except Exception:
                pass
        elif self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        self._close_stream()
        self.stopped.emit()
        self._emit_status("SDR stream stopped.")

    def close(self) -> None:
        self.stop()

    def _stream_loop(self) -> None:
        worker = self.thread_manager.get_worker(self._thread_name) if self.thread_manager is not None else None
        last_plot_push = 0.0
        try:
            if self.mode == "hardware":
                self._open_stream()
            while not self._thread_stop.is_set():
                if worker is not None and getattr(worker, "abort", False):
                    break
                try:
                    iq = self._read_next_block()
                except Exception as exc:
                    self._perf_stream_errors += 1
                    self._emit_error(f"SDR read failure: {exc}")
                    time.sleep(0.05)
                    continue
                self._cache_iq_block(iq)
                now = time.monotonic()
                if now - last_plot_push >= self._plot_interval_s:
                    self._publish_spectrum(iq, timestamp=now)
                    self._emit_perf_snapshot(now)
                    last_plot_push = now
        except Exception as exc:
            self.logger.exception("SDR runtime error: %s", exc)
            self._emit_error(f"SDR runtime error: {exc}")
        finally:
            self._close_stream()

    def _open_stream(self) -> None:
        if self.mode != "hardware" or self._sdr is None or self._stream is not None:
            return
        self._apply_hardware_settings()
        self._stream = self._sdr.setupStream(SOAPY_SDR_RX, SOAPY_SDR_CF32)
        self._sdr.activateStream(self._stream, 0)

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

    def _apply_hardware_settings(self) -> None:
        if self.mode != "hardware" or self._sdr is None:
            return
        self._sdr.setSampleRate(SOAPY_SDR_RX, 0, self.sample_rate)
        self._sdr.setFrequency(SOAPY_SDR_RX, 0, self.center_freq)
        self._sdr.setGainMode(SOAPY_SDR_RX, 0, self.agc)
        self._sdr.setGain(SOAPY_SDR_RX, 0, "IFGR", self.if_gain)
        self._sdr.setGain(SOAPY_SDR_RX, 0, "RFGR", self.rf_gain)
        if self.antenna:
            try:
                self._sdr.setAntenna(SOAPY_SDR_RX, 0, self.antenna)
            except Exception:
                pass

    def _read_next_block(self) -> np.ndarray:
        if self.mode != "hardware" or self._sdr is None:
            return self._generate_dummy_iq_block()
        if self._stream is None:
            self._open_stream()
        buffer = np.zeros(self.buff_size, dtype=np.complex64)
        stream_result = self._sdr.readStream(self._stream, [buffer], self.buff_size)
        if stream_result.ret > 0:
            return buffer[: int(stream_result.ret)].copy()
        if stream_result.ret == self._timeout_code:
            self._perf_timeouts += 1
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
        with self._state_lock:
            self._latest_iq = np.asarray(iq, dtype=np.complex64).copy()
            self._latest_timestamp = time.monotonic()
        self._frame_counter += 1
        try:
            self.iq_block.emit(iq.copy())
        except Exception:
            pass

    def _ensure_fft_window(self) -> None:
        n = int(max(8, self.fft_size))
        if self._fft_win is not None and self._fft_win_size == n:
            return
        win = blackman_window(n)
        self._fft_win = win
        self._fft_win_size = n
        self._fft_win_power = float(np.sum(win * win)) or 1.0
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
        if smooth_trace:
            self._fft_db_ema = apply_ema(spectrum_db, self._fft_db_ema, alpha=self._fft_ema_alpha)
            return self._fft_db_ema.astype(np.float32, copy=False)
        return spectrum_db.astype(np.float32, copy=False)

    def compute_spectrum(self, iq_data: np.ndarray) -> np.ndarray:
        """Compute the smoothed display spectrum in dB."""
        return self._compute_spectrum_snapshot(iq_data, smooth_trace=True)

    def _publish_spectrum(self, iq_data: np.ndarray, *, timestamp: float) -> None:
        spectrum_db = self.compute_spectrum(iq_data)
        freqs = self._get_freq_axis(len(spectrum_db))
        with self._state_lock:
            self._latest_spectrum_db = spectrum_db.copy()
            self._latest_freqs = freqs.copy()
        self.data_storage.update({"timestamp": timestamp, "x": freqs, "y": spectrum_db})
        snr_db = compute_snr(spectrum_db, self.snr_mode, self.noise_floor_ref_db)
        try:
            self.spectrum_updated.emit(self.data_storage)
            self.snr_updated.emit(float(snr_db), self.snr_mode)
        except Exception:
            pass

    def _emit_perf_snapshot(self, now: float) -> None:
        elapsed = max(1e-6, now - self._last_perf_emit)
        perf_snapshot = {
            "mode": self.mode,
            "frames": int(self._frame_counter),
            "sample_rate_hz": float(self.sample_rate),
            "center_freq_hz": float(self.center_freq),
            "fft_size": int(self.fft_size),
            "buffer_size": int(self.buff_size),
            "timeouts": int(self._perf_timeouts),
            "stream_errors": int(self._perf_stream_errors),
            "fps": float(self._frame_counter / elapsed),
        }
        self._last_perf_emit = now
        try:
            self.perf_updated.emit(perf_snapshot)
        except Exception:
            pass

    def update_fft_for_view(self, visible_span_hz: float, pixel_width: int) -> None:
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

    def set_frequency(self, frequency_hz: float) -> None:
        self.center_freq = float(frequency_hz)
        self._freq_axis = None
        self._freq_axis_key = None
        if self.mode == "hardware" and self._sdr is not None and self._stream is not None:
            self._sdr.setFrequency(SOAPY_SDR_RX, 0, self.center_freq)
        self._emit_settings_changed()

    def set_sample_rate(self, sample_rate_hz: float) -> None:
        self.sample_rate = float(sample_rate_hz)
        self.fft_size = select_fft_size(self.sample_rate, self.buff_size)
        self._plot_interval_s = 1.0 / max(1.0, self.fft_fps)
        self._fft_win = None
        self._fft_win_size = 0
        self._fft_db_ema = None
        self._freq_axis = None
        self._freq_axis_key = None
        self._restart_if_needed()
        self._emit_settings_changed()

    def update_if_gain(self, value: int) -> None:
        self.if_gain = int(value)
        if self.mode == "hardware" and self._sdr is not None:
            self._sdr.setGain(SOAPY_SDR_RX, 0, "IFGR", self.if_gain)
        self._emit_settings_changed()

    def update_rf_gain(self, value: int) -> None:
        self.rf_gain = int(value)
        if self.mode == "hardware" and self._sdr is not None:
            self._sdr.setGain(SOAPY_SDR_RX, 0, "RFGR", self.rf_gain)
        self._emit_settings_changed()

    def update_agc(self, enabled: bool) -> None:
        self.agc = bool(enabled)
        if self.mode == "hardware" and self._sdr is not None:
            self._sdr.setGainMode(SOAPY_SDR_RX, 0, self.agc)
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
        self._fft_ema_alpha = float(np.clip(alpha, 0.01, 1.0))
        self._emit_settings_changed()

    def _emit_settings_changed(self) -> None:
        snapshot = self.snapshot_state()
        try:
            self.settings_changed.emit(snapshot)
        except Exception:
            pass

    def snapshot_state(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "sample_rate_hz": float(self.sample_rate),
            "center_freq_hz": float(self.center_freq),
            "buffer_size": int(self.buff_size),
            "fft_size": int(self.fft_size),
            "if_gain": int(self.if_gain),
            "rf_gain": int(self.rf_gain),
            "agc": bool(self.agc),
            "antenna": str(self.antenna),
            "snr_mode": str(self.snr_mode),
            "noise_floor_ref_db": float(self.noise_floor_ref_db),
            "antennas": list(self.hwinfo.get("antennas", [])),
            "sample_rates": list(self.hwinfo.get("sample_rates", [])),
            "frames": int(self._frame_counter),
            "running": bool(self.running),
        }

    def _latest_iq_copy(self) -> np.ndarray | None:
        with self._state_lock:
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

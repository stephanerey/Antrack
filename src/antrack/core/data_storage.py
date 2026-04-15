"""Spectrum and waterfall history storage adapted from the RSPdx design."""

from __future__ import annotations

import logging

import numpy as np
from PyQt5 import QtCore

from antrack.core.dsp.snr import db_to_linear_power, linear_power_to_db


class HistoryBuffer:
    """Circular history buffer preserving the most recent traces."""

    def __init__(self, data_size: int, max_history_size: int, dtype=float) -> None:
        self.data_size = int(data_size)
        self.max_history_size = int(max_history_size)
        self.history_size = 0
        self.counter = 0
        self.write_pos = 0
        self.buffer = np.empty(shape=(self.max_history_size, self.data_size), dtype=dtype)

    def append(self, data: np.ndarray) -> None:
        self.counter += 1
        self.buffer[self.write_pos] = data
        self.write_pos = (self.write_pos + 1) % self.max_history_size
        if self.history_size < self.max_history_size:
            self.history_size += 1

    def get_recent(self, count: int) -> np.ndarray:
        count = int(max(0, min(int(count), self.history_size)))
        if count == 0:
            return self.buffer[:0]
        if self.history_size < self.max_history_size:
            return self.buffer[self.history_size - count : self.history_size]
        end = self.write_pos
        start = (end - count) % self.max_history_size
        if start < end:
            return self.buffer[start:end]
        return np.concatenate((self.buffer[start:], self.buffer[:end]), axis=0)

    def get_buffer(self) -> np.ndarray:
        return self.get_recent(self.history_size)

    def __getitem__(self, key):
        return self.get_buffer()[key]


class DataStorage(QtCore.QObject):
    """Shared plot data model for spectrum and waterfall widgets."""

    history_updated = QtCore.pyqtSignal(object)
    data_updated = QtCore.pyqtSignal(object)
    history_recalculated = QtCore.pyqtSignal(object)
    data_recalculated = QtCore.pyqtSignal(object)
    average_updated = QtCore.pyqtSignal(object)
    baseline_updated = QtCore.pyqtSignal(object)
    peak_hold_max_updated = QtCore.pyqtSignal(object)
    peak_hold_min_updated = QtCore.pyqtSignal(object)

    def __init__(
        self,
        max_history_size: int = 100,
        *,
        waterfall_max_bins: int = 2048,
        waterfall_time_stride: int = 1,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.logger = logging.getLogger("Antrack.DataStorage")
        self.max_history_size = int(max_history_size)
        self.waterfall_max_bins = int(max(64, waterfall_max_bins))
        self.waterfall_time_stride = int(max(1, waterfall_time_stride))
        self.smooth = False
        self.smooth_length = 11
        self.smooth_window = "hanning"
        self.subtract_baseline = False
        self.compute_average_enabled = False
        self.compute_peak_max_enabled = False
        self.compute_peak_min_enabled = False
        self.prev_baseline = None
        self.baseline = None
        self.baseline_x = None
        self.reset()

    def reset(self) -> None:
        self.x = None
        self.x_wf = None
        self.history = None
        self.waterfall_history = None
        self._waterfall_frame_counter = 0
        self._waterfall_accumulator = None
        self._waterfall_accumulator_count = 0
        self.reset_data()

    def reset_data(self) -> None:
        self.y = None
        self.average_counter = 0
        self.average = None
        self.peak_hold_max = None
        self.peak_hold_min = None

    def update(self, data: dict) -> None:
        y_in = np.asarray(data["y"], dtype=np.float32)
        history_y_in = np.asarray(data.get("history_y", data["y"]), dtype=np.float32)
        if self.y is not None and len(y_in) != len(self.y):
            self.logger.info(
                "%d bins coming from backend, expected %d; resetting storage",
                len(y_in),
                len(self.y),
            )
            self.reset()

        x_in = np.asarray(data["x"], dtype=np.float64)
        if self.x is None or self.x.shape != x_in.shape or float(self.x[0]) != float(x_in[0]) or float(self.x[-1]) != float(x_in[-1]):
            self.x = x_in.copy()
            self.x_wf = self._decimate_x_for_waterfall(self.x)

        data["y"] = y_in
        history_data = {"y": history_y_in}
        if self.subtract_baseline and self.baseline is not None:
            if len(data["y"]) == len(self.baseline):
                data["y"] = (data["y"] - self.baseline).astype(np.float32, copy=False)
            if len(history_data["y"]) == len(self.baseline):
                history_data["y"] = (history_data["y"] - self.baseline).astype(np.float32, copy=False)

        if self.compute_average_enabled:
            self.average_counter += 1

        self.update_history(history_data)
        self.update_data(data)

    def update_data(self, data: dict) -> None:
        y = self.smooth_data(data["y"]) if self.smooth else data["y"]
        self.y = np.asarray(y, dtype=np.float32)
        self.data_updated.emit(self)
        if self.compute_average_enabled:
            self.update_average({"y": self.y})
        if self.compute_peak_max_enabled:
            self.update_peak_hold_max({"y": self.y})
        if self.compute_peak_min_enabled:
            self.update_peak_hold_min({"y": self.y})

    def update_history(self, data: dict) -> None:
        y = np.asarray(data["y"], dtype=np.float32)
        if self.history is None:
            self.history = HistoryBuffer(len(y), self.max_history_size, dtype=np.float32)
        self.history.append(y)
        if self.x is not None:
            self.x_wf = self._decimate_x_for_waterfall(self.x)
        self._append_waterfall_trace(y, emit_signal=True)

    def _decimate_x_for_waterfall(self, x: np.ndarray) -> np.ndarray:
        x = np.asarray(x, dtype=np.float64)
        n = int(x.size)
        if n <= self.waterfall_max_bins:
            return x.copy()
        step = int(np.ceil(n / float(self.waterfall_max_bins)))
        m = int(np.ceil(n / float(step)))
        pad = m * step - n
        x_pad = np.pad(x, (0, pad), mode="edge") if pad > 0 else x
        x_r = x_pad.reshape(m, step)
        return np.mean(x_r, axis=1).astype(np.float64, copy=False)

    def _decimate_for_waterfall(self, y: np.ndarray) -> np.ndarray:
        y = np.asarray(y, dtype=np.float32)
        n = int(y.size)
        if n <= self.waterfall_max_bins:
            return y.copy()
        step = int(np.ceil(n / float(self.waterfall_max_bins)))
        m = int(np.ceil(n / float(step)))
        pad = m * step - n
        y_pad = np.pad(y, (0, pad), mode="edge") if pad > 0 else y
        y_r = y_pad.reshape(m, step)
        y_linear = db_to_linear_power(y_r)
        return np.asarray(linear_power_to_db(np.mean(y_linear, axis=1)), dtype=np.float32)

    def _reset_waterfall_accumulator(self) -> None:
        self._waterfall_accumulator = None
        self._waterfall_accumulator_count = 0

    def _append_waterfall_trace(self, y: np.ndarray, *, emit_signal: bool) -> None:
        y_wf = self._decimate_for_waterfall(y)
        y_wf_linear = db_to_linear_power(y_wf)
        if self._waterfall_accumulator is None or self._waterfall_accumulator.shape != y_wf_linear.shape:
            self._waterfall_accumulator = np.zeros_like(y_wf_linear, dtype=np.float64)
            self._waterfall_accumulator_count = 0
        self._waterfall_accumulator += y_wf_linear
        self._waterfall_accumulator_count += 1
        if self._waterfall_accumulator_count < self.waterfall_time_stride:
            return
        y_wf_avg = np.asarray(
            linear_power_to_db(self._waterfall_accumulator / float(max(1, self._waterfall_accumulator_count))),
            dtype=np.float32,
        )
        if self.waterfall_history is None or self.waterfall_history.data_size != len(y_wf_avg):
            self.waterfall_history = HistoryBuffer(len(y_wf_avg), self.max_history_size, dtype=np.float32)
        self.waterfall_history.append(y_wf_avg)
        self._reset_waterfall_accumulator()
        if emit_signal:
            self.history_updated.emit(self)

    def set_compute_average_enabled(self, enabled: bool) -> None:
        self.compute_average_enabled = bool(enabled)
        if not self.compute_average_enabled:
            self.average = None
            self.average_counter = 0

    def set_compute_peak_max_enabled(self, enabled: bool) -> None:
        self.compute_peak_max_enabled = bool(enabled)
        if not self.compute_peak_max_enabled:
            self.peak_hold_max = None

    def set_compute_peak_min_enabled(self, enabled: bool) -> None:
        self.compute_peak_min_enabled = bool(enabled)
        if not self.compute_peak_min_enabled:
            self.peak_hold_min = None

    def update_average(self, data: dict) -> None:
        y = np.asarray(data["y"], dtype=np.float32)
        if self.average is None:
            self.average = y.copy()
        else:
            n = float(max(1, self.average_counter))
            self.average += (y - self.average) / n
        self.average_updated.emit(self)

    def update_peak_hold_max(self, data: dict) -> None:
        y = np.asarray(data["y"], dtype=np.float32)
        if self.peak_hold_max is None:
            self.peak_hold_max = y.copy()
        else:
            self.peak_hold_max = np.maximum(self.peak_hold_max, y)
        self.peak_hold_max_updated.emit(self)

    def update_peak_hold_min(self, data: dict) -> None:
        y = np.asarray(data["y"], dtype=np.float32)
        if self.peak_hold_min is None:
            self.peak_hold_min = y.copy()
        else:
            self.peak_hold_min = np.minimum(self.peak_hold_min, y)
        self.peak_hold_min_updated.emit(self)

    def smooth_data(self, y: np.ndarray) -> np.ndarray:
        y = np.asarray(y, dtype=np.float32)
        length = int(max(3, self.smooth_length))
        if length % 2 == 0:
            length += 1
        if y.size < length:
            return y
        kernel = np.ones(length, dtype=np.float32) / float(length)
        return np.convolve(y, kernel, mode="same").astype(np.float32, copy=False)

    def set_smooth(self, toggle: bool, length: int = 11, window: str = "hanning") -> None:
        if toggle != self.smooth or length != self.smooth_length or window != self.smooth_window:
            self.smooth = bool(toggle)
            self.smooth_length = int(length)
            self.smooth_window = str(window)
            self.recalculate_data()

    def set_waterfall_time_stride(self, stride: int) -> None:
        stride = int(max(1, stride))
        if stride == self.waterfall_time_stride:
            return
        self.waterfall_time_stride = stride
        self._reset_waterfall_accumulator()
        if self.history is not None and int(self.history.history_size) > 0:
            self.recalculate_history()

    def recalculate_history(self) -> None:
        if self.history is None:
            return
        history = self.history.get_buffer().copy()
        if history.size == 0:
            return
        if self.prev_baseline is not None and len(history[-1]) == len(self.prev_baseline):
            history += self.prev_baseline
            self.prev_baseline = None
        if self.subtract_baseline and self.baseline is not None and len(history[-1]) == len(self.baseline):
            history -= self.baseline
        self.waterfall_history = None
        self._reset_waterfall_accumulator()
        if self.x is not None:
            self.x_wf = self._decimate_x_for_waterfall(self.x)
        for row in history:
            self._append_waterfall_trace(row, emit_signal=False)
        self.history_recalculated.emit(self)

    def recalculate_data(self) -> None:
        if self.history is None:
            return
        history = self.history.get_buffer()
        if history.size == 0:
            return
        if self.smooth:
            smoothed = np.asarray([self.smooth_data(row) for row in history], dtype=np.float32)
            self.y = smoothed[-1]
            self.average = np.average(smoothed, axis=0).astype(np.float32, copy=False)
            self.peak_hold_max = np.max(smoothed, axis=0).astype(np.float32, copy=False)
            self.peak_hold_min = np.min(smoothed, axis=0).astype(np.float32, copy=False)
        else:
            self.y = history[-1].copy()
            self.average = np.average(history, axis=0).astype(np.float32, copy=False)
            self.peak_hold_max = np.max(history, axis=0).astype(np.float32, copy=False)
            self.peak_hold_min = np.min(history, axis=0).astype(np.float32, copy=False)
        self.average_counter = self.history.history_size
        self.data_recalculated.emit(self)

"""Coordinate multiple tracker instances on a single managed worker."""

from __future__ import annotations

import logging
import threading
import time
from typing import Dict


class TrackingManager:
    """Run tracker iterations cooperatively inside a single ThreadManager worker."""

    def __init__(self, thread_manager, settings=None, *, logger=None) -> None:
        self.thread_manager = thread_manager
        self.settings = settings or {}
        self.logger = logger or logging.getLogger("TrackingManager")
        self._thread_name = "TrackingManagerLoop"
        self._state_lock = threading.RLock()
        self._trackers: Dict[object, float] = {}
        self._wakeup = threading.Event()

    def register_tracker(self, tracker) -> None:
        with self._state_lock:
            self._trackers[tracker] = 0.0
        self._wakeup.set()
        self.thread_manager.start_thread(self._thread_name, self._loop)

    def unregister_tracker(self, tracker) -> None:
        with self._state_lock:
            self._trackers.pop(tracker, None)
        self._wakeup.set()

    def is_tracker_active(self, tracker) -> bool:
        with self._state_lock:
            return tracker in self._trackers

    def active_tracker_count(self) -> int:
        with self._state_lock:
            return len(self._trackers)

    def _snapshot(self) -> dict:
        with self._state_lock:
            return dict(self._trackers)

    def _loop(self) -> None:
        worker = self.thread_manager.get_worker(self._thread_name)
        while worker and not worker.abort:
            scheduled = self._snapshot()
            if not scheduled:
                self._wakeup.wait(timeout=0.05)
                self._wakeup.clear()
                worker = self.thread_manager.get_worker(self._thread_name)
                if not self._snapshot():
                    break
                continue

            now = time.monotonic()
            next_due = None
            for tracker, due_at in scheduled.items():
                if not self.is_tracker_active(tracker):
                    continue
                tracker_interval = float(max(0.01, tracker.get_loop_interval()))
                if now + 1e-6 < float(due_at):
                    next_due = float(due_at) if next_due is None else min(next_due, float(due_at))
                    continue
                try:
                    tracker.step(interval=tracker_interval)
                except Exception:
                    self.logger.exception("Tracker iteration failed")
                next_run_at = time.monotonic() + tracker_interval
                with self._state_lock:
                    if tracker in self._trackers:
                        self._trackers[tracker] = next_run_at
                next_due = next_run_at if next_due is None else min(next_due, next_run_at)

            timeout_s = 0.05
            if next_due is not None:
                timeout_s = min(0.05, max(0.0, next_due - time.monotonic()))
            self._wakeup.wait(timeout=timeout_s)
            self._wakeup.clear()
            worker = self.thread_manager.get_worker(self._thread_name)

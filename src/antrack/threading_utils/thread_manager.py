"""ThreadManager utilities for background tasks and diagnostics."""

from __future__ import annotations

import logging
import time
import traceback
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Deque, Dict, List, Optional

from PyQt5.QtCore import QObject, QThread, pyqtSignal


class TaskStatus(Enum):
    QUEUED = "queued"
    RUNNING = "running"
    FINISHED = "finished"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class TaskRecord:
    name: str
    description: str
    status: TaskStatus = TaskStatus.QUEUED
    tags: List[str] = field(default_factory=list)
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    last_duration_s: Optional[float] = None
    total_runtime_s: float = 0.0
    start_count: int = 0
    last_error: Optional[str] = None
    last_traceback: Optional[str] = None
    last_status: Optional[str] = None
    is_asyncio_loop: bool = False
    cancel_requested: bool = False


class Worker(QObject):
    """Worker that executes a function in a dedicated QThread."""

    status = pyqtSignal(str)
    error = pyqtSignal(str)
    result = pyqtSignal(object)
    finished = pyqtSignal()

    def __init__(self, func: Callable, *args, **kwargs) -> None:
        super().__init__()
        self.func = func
        self.args = args
        self.kwargs = kwargs
        self.abort = False
        self.last_exception: Optional[BaseException] = None
        self.last_traceback: Optional[str] = None

    def run(self) -> None:
        """Execute the function and emit status, result, and errors."""
        logger = logging.getLogger("ThreadManager.Worker")
        try:
            msg = f"START func={getattr(self.func, '__name__', str(self.func))}"
            self.status.emit(msg)
            logger.info(msg)
        except Exception:
            pass

        try:
            if not self.abort:
                result = self.func(*self.args, **self.kwargs)
                self.result.emit(result)
        except Exception as exc:
            self.last_exception = exc
            self.last_traceback = traceback.format_exc()
            try:
                logger.error(
                    "ERROR func=%s: %s",
                    getattr(self.func, "__name__", str(self.func)),
                    exc,
                )
            except Exception:
                pass
            self.error.emit(str(exc))
        finally:
            try:
                msg = f"FINISH func={getattr(self.func, '__name__', str(self.func))}"
                self.status.emit(msg)
                logger.info(msg)
            except Exception:
                pass
            self.finished.emit()


class ThreadManager:
    """Background task manager with diagnostics and clean shutdown."""

    def __init__(self) -> None:
        self.threads: Dict[str, QThread] = {}
        self.workers: Dict[str, Worker] = {}
        self.logger = logging.getLogger("ThreadManager")
        self.asyncio_loops: Dict[str, Any] = {}
        self._tasks: Dict[str, TaskRecord] = {}
        self._history: Deque[str] = deque(maxlen=200)
        self._shutdown_started = False

    def start_thread(self, thread_name: str, func: Callable, *args, **kwargs) -> Worker:
        """Start a function in a dedicated QThread.

        Args:
            thread_name: Unique task/thread identifier.
            func: Callable to execute in the thread.
            *args: Positional args for the callable.
            **kwargs: Keyword args for the callable.

        Returns:
            The Worker instance associated with the task.

        Raises:
            RuntimeError: If shutdown has started.
        """
        if self._shutdown_started:
            raise RuntimeError("ThreadManager is shutting down; new tasks are rejected")

        if thread_name in getattr(self, "asyncio_loops", {}) and self.asyncio_loops.get(thread_name) is not None:
            self.logger.info("'%s' is a persistent asyncio loop; reusing", thread_name)
            return self.workers.get(thread_name)

        if thread_name in self.threads:
            th = self.threads[thread_name]
            if th.isRunning():
                self.logger.info("start_thread('%s'): thread still running; reuse", thread_name)
                return self.workers[thread_name]
            self.logger.info("start_thread('%s'): previous thread finished; recreating", thread_name)
            try:
                th.quit()
                th.wait(500)
            except Exception:
                pass
            self.threads.pop(thread_name, None)
            self.workers.pop(thread_name, None)

        thread = QThread()
        worker = Worker(func, *args, **kwargs)
        worker.moveToThread(thread)

        record = self._ensure_task(thread_name, description=getattr(func, "__name__", str(func)))
        record.status = TaskStatus.RUNNING
        record.started_at = time.time()
        record.finished_at = None
        record.start_count += 1
        record.last_error = None
        record.last_traceback = None
        record.last_status = None
        record.cancel_requested = False

        thread.started.connect(worker.run)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(lambda: self._cleanup_thread(thread_name))
        try:
            worker.error.connect(lambda msg, name=thread_name: self._record_error(name, msg))
        except Exception:
            pass
        try:
            worker.status.connect(lambda msg, name=thread_name: self._record_status(name, msg))
        except Exception:
            pass

        self.threads[thread_name] = thread
        self.workers[thread_name] = worker

        thread.start()
        self.logger.info("Thread '%s' started", thread_name)

        return worker

    def _cleanup_thread(self, thread_name: str) -> None:
        """Finalize task state after thread completion."""
        record = self._tasks.get(thread_name)
        if record and record.status in (TaskStatus.RUNNING, TaskStatus.FAILED, TaskStatus.CANCELLED):
            record.finished_at = time.time()
            if record.started_at:
                record.last_duration_s = max(0.0, record.finished_at - record.started_at)
                record.total_runtime_s += record.last_duration_s or 0.0
            if record.status == TaskStatus.RUNNING:
                if record.cancel_requested:
                    record.status = TaskStatus.CANCELLED
                else:
                    record.status = TaskStatus.FINISHED
            self._retain_history(thread_name)

        if thread_name in self.threads:
            self.threads.pop(thread_name, None)
            self.workers.pop(thread_name, None)
            self.logger.debug("Thread '%s' cleaned", thread_name)

    def stop_thread(self, thread_name: str) -> None:
        """Request cancellation of a specific thread."""
        if hasattr(self, "asyncio_loops") and thread_name in getattr(self, "asyncio_loops", {}):
            try:
                self.stop_asyncio_loop(thread_name)
            except Exception:
                pass

        record = self._tasks.get(thread_name)
        if record:
            record.cancel_requested = True
            if record.status == TaskStatus.RUNNING:
                record.status = TaskStatus.CANCELLED

        if thread_name in self.workers:
            self.workers[thread_name].abort = True
            self.threads[thread_name].quit()
            self.threads[thread_name].wait(1000)
            self.logger.info("Thread '%s' stopped", thread_name)

    def stop_all_threads(self) -> None:
        """Request cancellation of all threads (legacy API)."""
        self.shutdown(graceful=True, timeout_s=5.0)

    def shutdown(self, graceful: bool = True, timeout_s: float = 5.0) -> None:
        """Shutdown all tasks with a bounded timeout.

        Args:
            graceful: If True, request cancellation and wait up to timeout.
            timeout_s: Total timeout for shutdown.
        """
        if self._shutdown_started:
            return
        self._shutdown_started = True

        if hasattr(self, "asyncio_loops"):
            for loop_name in list(self.asyncio_loops.keys()):
                try:
                    self.stop_asyncio_loop(loop_name)
                except Exception:
                    pass

        start = time.time()
        for thread_name in list(self.threads.keys()):
            if graceful:
                self._request_cancel(thread_name)

        for thread_name, thread in list(self.threads.items()):
            remaining = max(0.0, timeout_s - (time.time() - start))
            if remaining <= 0:
                break
            try:
                thread.quit()
                thread.wait(int(remaining * 1000))
            except Exception:
                pass

        for thread_name, thread in list(self.threads.items()):
            if thread.isRunning():
                record = self._tasks.get(thread_name)
                if record:
                    record.last_status = "shutdown timeout"
                self.logger.warning("Thread '%s' still running after shutdown timeout", thread_name)

    def _request_cancel(self, thread_name: str) -> None:
        record = self._tasks.get(thread_name)
        if record:
            record.cancel_requested = True
        worker = self.workers.get(thread_name)
        if worker:
            worker.abort = True

    def get_worker(self, thread_name: str) -> Optional[Worker]:
        """Retrieve a worker by name."""
        return self.workers.get(thread_name)

    def _ensure_task(self, thread_name: str, description: str) -> TaskRecord:
        record = self._tasks.get(thread_name)
        if record is None:
            record = TaskRecord(name=thread_name, description=description)
            self._tasks[thread_name] = record
        else:
            record.description = description
        return record

    def _record_error(self, thread_name: str, msg: str) -> None:
        record = self._tasks.get(thread_name)
        if record:
            record.last_error = msg
            record.status = TaskStatus.FAILED
            worker = self.workers.get(thread_name)
            if worker and worker.last_traceback:
                record.last_traceback = worker.last_traceback
        try:
            self.logger.error("[%s] Worker error: %s", thread_name, msg)
        except Exception:
            pass

    def _record_status(self, thread_name: str, msg: str) -> None:
        record = self._tasks.get(thread_name)
        if record:
            record.last_status = msg

    def _retain_history(self, thread_name: str) -> None:
        if thread_name in self._history:
            return
        oldest = self._history[0] if len(self._history) == self._history.maxlen else None
        self._history.append(thread_name)
        if oldest and oldest in self._tasks:
            self._tasks.pop(oldest, None)

    def get_diagnostics(self) -> Dict[str, Any]:
        """Return task diagnostics keyed by task name."""
        out: Dict[str, Any] = {}
        for name, rec in self._tasks.items():
            out[name] = {
                "status": rec.status,
                "description": rec.description,
                "tags": list(rec.tags),
                "is_asyncio_loop": rec.is_asyncio_loop,
                "start_count": rec.start_count,
                "last_duration_s": rec.last_duration_s,
                "total_runtime_s": rec.total_runtime_s,
                "started_at": rec.started_at,
                "finished_at": rec.finished_at,
                "last_error": rec.last_error,
                "last_traceback": rec.last_traceback,
                "last_status": rec.last_status,
                "cancel_requested": rec.cancel_requested,
            }
        return out

    def get_running_tasks(self) -> Dict[str, Dict[str, Any]]:
        """Return running tasks with diagnostic fields."""
        diag = self.get_diagnostics()
        return {name: info for name, info in diag.items() if info["status"] == TaskStatus.RUNNING}

    def get_task_exceptions(self) -> Dict[str, Dict[str, Any]]:
        """Return tasks that have errors with traceback and timestamp."""
        out: Dict[str, Dict[str, Any]] = {}
        for name, rec in self._tasks.items():
            if rec.last_error:
                out[name] = {
                    "exception": rec.last_error,
                    "traceback": rec.last_traceback,
                    "time": rec.finished_at or rec.started_at,
                }
        return out

    def clear_history(self, keep_running: bool = True) -> None:
        """Clear completed task history, optionally preserving running tasks."""
        if keep_running:
            running = {name for name, rec in self._tasks.items() if rec.status == TaskStatus.RUNNING}
            for name in list(self._tasks.keys()):
                if name not in running:
                    self._tasks.pop(name, None)
        else:
            self._tasks.clear()
        self._history.clear()

    def diagnostics_summary(self) -> str:
        """Return a textual summary of task diagnostics."""
        lines = []
        for name, rec in self._tasks.items():
            state = rec.status.value
            last = f"{rec.last_duration_s:.3f}s" if isinstance(rec.last_duration_s, (int, float)) else "-"
            total = f"{rec.total_runtime_s:.3f}s" if isinstance(rec.total_runtime_s, (int, float)) else "0.000s"
            err = rec.last_error or "-"
            lines.append(
                f"- {name} [{state}] starts={rec.start_count} last={last} total={total} last_error={err}"
            )
        if not lines:
            return "Aucune statistique de thread disponible."
        return "Diagnostics des threads:\n" + "\n".join(lines)

    # ---- Support asyncio ----
    def ensure_asyncio_loop(self, loop_name: str = "AxisCoreLoop", timeout: float = 5.0) -> None:
        """Ensure a persistent asyncio loop runs in a managed thread."""
        if loop_name in getattr(self, "asyncio_loops", {}) and self.asyncio_loops[loop_name] is not None:
            return

        if loop_name in self.threads:
            try:
                self.stop_thread(loop_name)
            except Exception:
                pass
            self.threads.pop(loop_name, None)
            self.workers.pop(loop_name, None)

        import threading
        import asyncio as _asyncio

        ready_event = threading.Event()

        def loop_entry():
            loop = _asyncio.new_event_loop()
            _asyncio.set_event_loop(loop)
            self.asyncio_loops[loop_name] = loop
            try:
                ready_event.set()
            except Exception:
                pass
            try:
                loop.run_forever()
            finally:
                try:
                    loop.close()
                finally:
                    self.asyncio_loops.pop(loop_name, None)

        self.start_thread(loop_name, loop_entry)
        try:
            record = self._ensure_task(loop_name, description="asyncio loop")
            record.is_asyncio_loop = True
        except Exception:
            pass
        ready = ready_event.wait(timeout=timeout)
        if not ready:
            self.logger.error("Asyncio loop '%s' did not init in time", loop_name)

    def run_coro(self, loop_name: str, coro_or_factory, timeout: float = None):
        """Run a coroutine on a persistent asyncio loop and return its result."""
        import asyncio as _asyncio

        self.ensure_asyncio_loop(loop_name)
        loop = self.asyncio_loops.get(loop_name)
        if loop is None:
            raise RuntimeError(f"Asyncio loop '{loop_name}' not initialized")
        try:
            coro = coro_or_factory() if callable(coro_or_factory) else coro_or_factory
        except Exception:
            raise
        fut = _asyncio.run_coroutine_threadsafe(coro, loop)
        return fut.result(timeout=timeout) if timeout is not None else fut.result()

    def stop_asyncio_loop(self, loop_name: str) -> None:
        """Request shutdown of a persistent asyncio loop."""
        loop = self.asyncio_loops.get(loop_name)
        if loop is not None:
            try:
                loop.call_soon_threadsafe(loop.stop)
            except Exception:
                pass

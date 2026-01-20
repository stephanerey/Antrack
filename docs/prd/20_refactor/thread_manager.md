# ThreadManager Specification

## Goal
Provide a simple, robust API to run background work with strong diagnostics, while keeping the existing codebase easy to understand.

## Constraints
- Phase 1 MUST remain behavior-preserving.
- Phase 1 MUST NOT add any new third-party dependency.
- Prefer incremental changes over a full rewrite.

## Minimum capabilities
ThreadManager MUST provide:
- state tracking (queued/running/finished/failed/cancelled)
- duration timing (start/end/duration)
- progress reporting (string messages are sufficient)
- exception capture including traceback (not just `str(e)`)
- a snapshot suitable for a diagnostics UI
- deterministic shutdown (no orphan threads)

## API strategy for Phase 1 (pragmatic)
The current v2.01 code uses `start_thread(...)`, `stop_thread(...)`, `stop_all_threads(...)`, and diagnostics helpers.

Phase 1 MUST:
- keep the existing API working (backward compatibility)
- MAY add a more structured API (`run()`, `cancel()`, `shutdown()`) as thin wrappers
- add a `shutdown()` method with clear semantics (see below)

### Recommended minimal public API
- `start_thread(thread_name: str, func: Callable, *args, **kwargs) -> Worker`  (existing)
- `stop_thread(thread_name: str)` (existing)
- `stop_all_threads()` (existing)
- `get_diagnostics() -> dict` (existing)
- `shutdown(graceful: bool = True, timeout_s: float = 5.0)` (NEW in Phase 1)

Optionally (wrapper-level):
- `run(name, fn, *, on_done=None, on_error=None, on_progress=None, tags=None) -> TaskHandle`
- `cancel(handle_or_id)`

## Ownership & task registry (MANDATORY)
- ThreadManager is the single owner of task lifecycle state.
- It MUST maintain an internal registry (e.g. dict keyed by `task_id` or by `thread_name` in Phase 1) holding:
  - immutable metadata (name, tags)
  - runtime state (queued/running/finished/failed/cancelled)
  - timestamps (started_at, ended_at)
  - last duration and total runtime
  - last error and last traceback (if any)
  - backend details required for cancellation/join (QThread, Worker)

### Retention policy (MANDATORY)
- Keep all active tasks.
- Keep only the last N completed tasks (default N=200) to avoid unbounded growth.

## Error capture (MANDATORY)
- Worker exceptions MUST be captured with full traceback.
- Diagnostics MUST surface both the message and the traceback.

## Shutdown contract (MANDATORY)
ThreadManager MUST provide deterministic shutdown that leaves no running threads.

### `shutdown(graceful: bool = True, timeout_s: float = 5.0)` semantics
- Shutdown MUST be idempotent (calling it multiple times is safe).
- Once shutdown starts, ThreadManager MUST reject new tasks (raise RuntimeError or no-op with log).

If `graceful=True`:
1) Request cancellation for all active tasks.
2) Wait for tasks/threads to finish, up to `timeout_s` total.
3) Tasks still running after timeout MUST be flagged explicitly in diagnostics (timeout reason).

If `graceful=False`:
1) Request immediate cancellation for all active tasks.
2) Wait a bounded time (still using `timeout_s`).
3) Surface any non-terminated tasks explicitly in diagnostics.

### Join/wait requirements
- Shutdown MUST attempt to `quit()+wait()` all underlying QThreads.
- It MUST never block indefinitely.

## Threading boundary
- Workers MUST NOT access UI objects.
- Communication back to UI MUST use Qt signals (preferred) or thread-safe callbacks.

## Diagnostics UI requirements
The diagnostics view MUST be able to display:
- active tasks list (name/state/duration)
- last completed tasks (history)
- last errors with traceback
- controls: cancel selected, cancel all, copy traceback

## Acceptance criteria
- Tasks execute reliably.
- Cancellation works (at least best-effort in Phase 1).
- Exceptions are captured and shown with traceback.
- App exit does not leave running threads.
- Existing `start_thread/stop_all_threads/get_diagnostics` usage in the GUI remains functional.

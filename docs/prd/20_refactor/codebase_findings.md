# Codebase Findings (v2.01)

## Goal
Capture the most relevant observations from the existing v2.01 codebase so Phase 1 refactor work is targeted and behavior-preserving.

## Key observations
### 1) `gui/main_ui.py` is monolithic
- The main UI module is very large and mixes:
  - UI wiring and widget lifecycle
  - tracking computations
  - path resolution for runtime assets
  - thread/task start/stop logic
  - log viewer utilities
- This is the main driver for the UI modularization work in `ui_modularization.md`.

### 2) Multiple path strategies exist
- The code uses a mix of:
  - repo-root `data/...` assumptions (via `Path(__file__).resolve().parents[...]`)
  - package-relative `.../../data/...` assumptions
- This causes ambiguity for ephemerides/BSP, TLE files, and logs.
- Phase 1 MUST unify all runtime paths via `antrack/utils/paths.py`.

### 3) Logging location is not canonical
- `antrack/main.py` initializes logging under a relative logs directory.
- Some UI components read log files via their own relative logic.
- Phase 1 MUST make all log access go through `get_logs_dir()` and MUST avoid multiple independent implementations.

### 4) ThreadManager exists but needs hardening
- A ThreadManager based on `QThread` exists (good baseline).
- Missing/weak points typically observed in v2.01:
  - no internal task registry/history with retention
  - no explicit shutdown contract with timeout/idempotence
  - exception handling often lacks full traceback propagation
  - diagnostics are minimal compared to what is needed for maintenance
- Phase 1 MUST harden it per `thread_manager.md` while keeping the API stable where feasible.

### 5) Boundary enforcement needs to be precise
- v2.01 uses Qt in multiple places.
- The boundary rule MUST distinguish QtWidgets (GUI-only) from QtCore (allowed in threading_utils).

## Targeted Phase 1 hotspots (non-exhaustive)
Codex should expect to touch these areas during Phase 1:
- `src/antrack/gui/main_ui.py`
- `src/antrack/threading_utils/thread_manager.py`
- `src/antrack/main.py`
- `src/antrack/utils/*` (add/complete `paths.py`, ensure config usage is clean)
- `src/antrack/tracking/*` (only to fix path usage, not to add features)

## Non-goals reminder
- Do not implement anything under `30_features/` in Phase 1.
- Do not add new dependencies.
- Do not redesign UX.

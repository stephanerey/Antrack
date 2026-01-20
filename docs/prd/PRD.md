# Antrack — Product & Technical Requirements (Refactor v2.01 + Roadmap)

**Document version:** 3.0.1  
**Date:** 2026-01-19  
**Baseline code:** Antrack `2.01`  
**Implementation agent:** Codex 5.2

---

## 1) Context
Antrack is a Python desktop application that controls a parabolic antenna in real time and supports tracking workflows for satellites and celestial objects.

The repository already contains a working v2.01 codebase, but it requires a refactor to improve maintainability and to prepare upcoming tracking and calibration work.

### Current capabilities (v2.01)
- Real-time antenna positioning control (motor drive via hardware client).
- Tracking computations (target position azimuth/elevation over time).
- Predictive plotting for pass/trajectory planning.
- Diagnostics and logging (basic).

### Planned capabilities (post-refactor)
- Calibration workflow using solar noise measurements with the receiver chain mounted on the dish.

---

## 2) Goals
### Phase 1 — Refactor (this PRD’s main scope)
Phase 1 MUST be behavior-preserving and focuses on:
1) Clean architectural boundaries (GUI vs core/tracking/utils).
2) UI modularization to reduce the size/complexity of `gui/main_ui.py`.
3) A robust ThreadManager with clear lifecycle, diagnostics, and clean shutdown.
4) Deterministic, canonical runtime paths for data and logs.
5) Consistent docstrings and readable code (no over-engineering).

### Phase 2+ — Features (explicitly out of Phase 1)
- Tracking enhancements (object catalogs, pass prediction improvements, plotting improvements).
- Calibration workflow (sun scan, acquisition, persistence, result display).

---

## 3) Non-goals (Phase 1)
- No new user-facing features beyond what is required to preserve current behavior.
- No major UX redesign.
- No hardware protocol redesign unless required for correctness.
- No new third-party dependencies.
- No "framework" adoption or architectural rewrites.

---

## 4) Technical environment
### Runtime
- Python: `>=3.12,<3.13`
- GUI: PyQt5 + qasync
- Baseline dependencies are defined in `pyproject.toml` (see `10_architecture/runtime_environment.md`).

### Entry point
- Console script: `antrack = antrack.main:main`
- The application MUST start correctly from any working directory.

### Supported platforms
- Primary target: Windows
- Code MUST remain OS-neutral (paths, encoding, line endings).

### Development workflow (recommended)
- Editable install:
  - `pip install -e .[dev]`
- Run:
  - `antrack`

---

## 5) Repository layout (desired target)
This PRD assumes a standard `src/` layout:
- Package code: `src/antrack/`
- Repository-local runtime resources (dev mode):
  - Data: `src/data/`
  - Logs: `src/logs/`

Data sub-structure (canonical, dev mode):
- Ephemerides / large binary assets: `src/data/ephemeris/`
- TLE files: `src/data/tle/`
- Radio sources: `src/data/radiosources/`
- Spacecraft definitions: `src/data/spacecrafts/`

Notes:
- v2.01 currently contains multiple path strategies; Phase 1 unifies them via a single helper (`antrack/utils/paths.py`).
- Root-level `data/` and `logs/` duplicates (if present) MUST be removed after verifying no unique assets exist (or migrated to `src/data` / `src/logs`).

---

## 6) Phase 1 requirements (implementation contract)
Phase 1 MUST comply with:
- `00_conventions.md`
- `10_architecture/*` (stable contracts)
- `20_refactor/*` (Phase 1 specs)
- `90_quality/*` (testing + DoD)

### 6.1 Deterministic paths
- Add/complete `antrack/utils/paths.py` to provide:
  - `get_repo_root()` (dev mode only)
  - `get_src_root()`
  - `get_data_dir()` (canonical: `src/data`)
  - `get_logs_dir()` (canonical: `src/logs`)
- Remove ad-hoc `Path(__file__).parents[...]` guesses outside `paths.py`.
- Provide a single source of truth for ephemerides/BSP/TLE/log file locations.

### 6.2 Ephemeris assets (download-on-demand)

### 6.3 Configuration (settings.txt + overrides)
Owner decision: configuration file is `settings.txt`, and environment overrides are allowed.

Phase 1 requirements:
- Add a canonical config path resolver (recommended in `utils/paths.py`):
  - If an environment variable `ANTRACK_CONFIG_PATH` is set: use it.
  - Else, use the repo-local `settings.txt` if present at repo root.
  - Optionally support fallback to `src/data/settings.txt` if it already exists in v2.01, but do not relocate files in Phase 1.
- The settings loader MUST document the supported env overrides (at least config path).


Owner decision: keep large binaries under `src/data/ephemeris/`, but allow download on demand.

Phase 1 requirements:
- If a required ephemeris file exists locally under `src/data/ephemeris/`, the app MUST use it.
- If it does not exist:
  - The app SHOULD attempt to download it using existing library capabilities (e.g., Skyfield loaders) **without adding new dependencies**.
  - If download fails (offline, blocked), the app MUST fail gracefully with a clear error message explaining:
    - which file is missing,
    - where it was expected,
    - whether an automatic download was attempted,
    - how to fix it (e.g., retry with internet or place file manually).

Scope limitation:
- Phase 1 only needs to implement download-on-demand for the ephemeris assets that are currently used by v2.01 (do not add new ephemeris datasets).

### 6.3 Logging
- Logging MUST be initialized in `antrack/main.py`.
- Default log directory MUST be `src/logs/` (dev mode).
- Default log file name MUST be: `antrack.log`.
- Rotation MUST be enabled using **stdlib only** with a time-based policy: **retain 7 days**.
  - Recommended implementation: `logging.handlers.TimedRotatingFileHandler(when='D', interval=1, backupCount=7)`.


### 6.4 UI modularization
- `gui/main_ui.py` remains the orchestration/composition root.
- Cohesive UI blocks SHOULD move to `gui/*_ui.py` modules.
- GUI code MUST not embed tracking computations; it calls services.

### 6.5 ThreadManager hardening
- ThreadManager MUST be robust and diagnosable.
- The refactor SHOULD preserve existing API where feasible (incremental evolution).
- Shutdown MUST be deterministic (cancel + wait + timeout + idempotence). Default shutdown timeout MUST be **5 seconds** unless explicitly overridden.
- Exceptions MUST be captured with traceback and shown in diagnostics.

### 6.6 Tests
- Add minimal unit tests for non-UI layers:
  - `utils/paths.py`
  - `tracking` pure computations
  - ThreadManager state/registry behavior (without QtWidgets)

---

## 7) Acceptance criteria (Phase 1)
- The application starts via the `antrack` entry point.
- No `PyQt5.QtWidgets` import exists outside `src/antrack/gui/`.
- Data/logs are resolved through `utils/paths.py` and match the canonical locations.
- Clean shutdown: no orphan threads (no `QThread: Destroyed while thread is still running`).
- Thread diagnostics panel reports running/completed/failed tasks with tracebacks.
- PR includes a reproducible “How to test” section.

---

## 8) Decisions & open items
### Resolved (owner-confirmed)
- Log file name: `antrack.log`.
- Log rotation: time-based, retain **7 days**.
- Phase 1 targets repo-local dev mode: keep runtime files in `src/data` and `src/logs`.
- Ephemeris binaries live in `src/data/ephemeris` and are downloaded on demand when missing (best-effort, existing deps only).
- Configuration: `settings.txt` is the source of truth and environment override is allowed (support at least `ANTRACK_CONFIG_PATH`).
- ThreadManager shutdown timeout default: **5 seconds**.
- No “do not touch” restrictions for Phase 1 modules.

### Still open (owner input may be needed)
1) TLE source URL(s) and refresh cadence (only if not already encoded in v2.01). Phase 1 should preserve current behavior.

---

## 9) Document map (source of truth)
- Start here:
  - `README.md` (index)
  - `PRD.md` (this document)
- Conventions:
  - `00_conventions.md`
- Stable architecture contracts:
  - `10_architecture/overview.md`
  - `10_architecture/module_boundaries.md`
  - `10_architecture/data_and_paths.md`
  - `10_architecture/runtime_environment.md`
- Phase 1 specs:
  - `20_refactor/v2_01_plan.md`
  - `20_refactor/ui_modularization.md`
  - `20_refactor/thread_manager.md`
  - `20_refactor/migration_notes.md`
  - `20_refactor/codebase_findings.md`
  - `20_refactor/open_questions.md`
- Phase 2+ features (NOT Phase 1):
  - `30_features/*`
- Quality:
  - `90_quality/testing.md`
  - `90_quality/definition_of_done.md`

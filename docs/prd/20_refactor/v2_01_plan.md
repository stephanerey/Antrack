# Refactor Plan - v2.01 (Phase 1)

## Goal
Refactor the existing codebase to enforce clean boundaries, modularize UI code, and harden the ThreadManager.

## Non-goals
- No major UX redesign.
- No new functional features unless required to keep existing behavior.
- Do not implement anything from `30_features/` (Phase 2).
- No protocol changes for hardware unless required for correctness.
- No new third-party dependencies.

## Observations (current v2.01)
- `main_ui.py` is monolithic and mixes UI, orchestration, and non-UI logic.
- Path resolution is inconsistent for some assets: some code guesses project root via `parents[...]` and references `data/...` at repo root, while other modules already use `src/data/...`.
- The current ThreadManager is functional but minimal: it tracks basic stats and errors, but lacks a strict shutdown contract and full traceback capture.

## Hard constraints (Phase 1)
- Phase 1 MUST remain behavior-preserving.
- Phase 1 MUST NOT add any third-party dependencies.
- Phase 1 MUST NOT implement anything from `30_features/`.
- Keep the code simple: avoid new abstraction layers unless strictly necessary.

## Requirements

### 1) Canonicalize folders and paths
- Canonical locations MUST be:
  - Data assets: `src/data/`
  - Logs: `src/logs/`
- Root-level duplicates (e.g. `data/`, `logs/`) MUST be removed or ignored **after** verifying there are no unique assets.
  - If unique assets exist, they MUST be moved under the canonical locations first.
- All runtime path resolution MUST go through `antrack/utils/paths.py`.
  - Remove ad-hoc `Path(...).parents[...]` project-root guesses.

### 2) Enforce boundaries
- No PyQt widgets outside `src/antrack/gui/`.
- Tracking computations MUST stay in `tracking/`.
- Hardware I/O MUST stay in `core/`.

### 3) UI modularization (without new features)
- `gui/main_ui.py` remains the orchestration layer.
- Feature-specific UI logic SHOULD be split into modules (examples):
  - `gui/tracking_ui.py`
  - `gui/calibration_ui.py` (UI only; no new calibration logic in Phase 1)
  - `gui/plots_ui.py`
  - `gui/diagnostics_ui.py` (integration layer)
- The goal is readability and separation, not feature addition.

### 4) ThreadManager hardening

### 5) Logging + config normalization
- Standardize default log file to `src/logs/antrack.log`.
- Enable log rotation (stdlib only) with **7 days** retention (time-based).
- Standardize configuration resolution to `settings.txt` with env override support:
  - `ANTRACK_CONFIG_PATH` MUST override the default config location.


- Must meet requirements in `thread_manager.md`.
- Add deterministic `shutdown(graceful=True, timeout_s=5.0)` and improve diagnostics.

## Deliverables
- Updated folder usage and path helpers
- `main_ui.py` split into cohesive UI modules
- ThreadManager compliant with spec and wired to the diagnostics panel
- Updated docs and smoke tests

## Verification (how to check Phase 1)
- Run unit tests (non-UI): `pytest -q`
- Ensure no Qt widgets imports outside `gui/` (examples):
  - `python -c "import pkgutil, antrack; print('ok')"`
  - `rg -n "from PyQt5\.QtWidgets|import PyQt5\.QtWidgets" src/antrack | rg -v "src/antrack/gui"`
- Ensure canonical path usage (no project-root guessing):
  - `rg -n "parents\[[0-9]+\]|Path\(__file__\)\.resolve\(\)\.parents" src/antrack`
- Manual smoke test: see `90_quality/testing.md`

## Acceptance criteria
- App starts via entry point and still behaves as before.
- No runtime usage of root-level `data/` or `logs/`.
- Paths resolve correctly regardless of current working directory.
- Clean shutdown: no orphan QThreads.

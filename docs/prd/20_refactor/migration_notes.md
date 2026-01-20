# Migration Notes (v2.01 -> refactored)

## Goal
Provide safe steps to migrate the repository layout without breaking runtime.

## Folder duplicates
- Before removing root `data/` and `logs/`:
  - verify no unique files exist
  - if unique assets exist: move them under `src/data/` or `src/logs/` and commit

## Path unification
- Replace any project-root guessing (e.g. `Path(__file__).resolve().parents[...]`) with `antrack/utils/paths.py`.
- Pay special attention to ephemeris/BSP loading: it MUST point to `src/data/...` after Phase 1.

## ThreadManager migration
- Keep existing `start_thread/stop_thread/stop_all_threads/get_diagnostics` usage working.
- Add `shutdown(graceful, timeout_s)` and wire it into app exit.

## Import updates
- Use absolute imports within the package.
- Keep public API surface stable where possible.

## Backward compatibility
- If any scripts relied on old paths, provide a short transition note in release notes.

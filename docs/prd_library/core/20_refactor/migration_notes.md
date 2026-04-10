# Migration Notes

> **PRD Policy:** **PROJECT (editable)** — Fill and update this file for the current project.

**Last updated:** 2026-04-10

Track breaking changes and how to migrate.

## Current refactor intent
- no user-facing migration is expected during the behavior-preserving refactor
- internal imports and file locations may change as long as the application behavior remains stable

## Anticipated migration points
- imports from old monolithic GUI code to extracted UI modules
- gradual architecture naming cleanup from legacy powermeter wording toward instrument wording
- possible move of some calibration orchestration out of `main_ui.py`
- later replacement of the old powermeter path by the SDR path in the future feature phase

## Rules
- record every non-trivial import move here once implementation starts
- if a temporary compatibility alias is added, record it here with removal conditions

## 2026-04-10 refactor notes
- `src/antrack/gui/main_ui.py` is now the composition/lifecycle root instead of the primary home for tracking, connection, calibration, diagnostics, and instrument detail methods.
- New Python-side GUI modules were introduced without changing the `.ui` layout contract:
  - `src/antrack/gui/connection_ui.py`
  - `src/antrack/gui/tracking_ui.py`
  - `src/antrack/gui/calibration_ui.py`
  - `src/antrack/gui/instrument_ui.py`
  - `src/antrack/gui/diagnostics_ui.py`
  - `src/antrack/gui/ui_styles.py`
- Transitional compatibility remains in the instrument path:
  - `InstrumentUiMixin` uses instrument-oriented entry points (`setup_instrument_ui`, `start_instrument_read`).
  - A compatibility alias keeps the existing powermeter backend and widget names working for now.
  - Removal condition: replace the powermeter backend/widgets during the future SDR phase.
- Shutdown handling was tightened in `MainUi.closeEvent` to stop polling/ephemeris work before GUI teardown.
- No `.ui` redesign was required for this phase.

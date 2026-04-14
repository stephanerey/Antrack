# Tasks Now

> **PRD Policy:** **PROJECT (editable)** - Live task board for the current project.

**Last updated:** 2026-04-14

## Current phase focus
Behavior-preserving extensibility refactor.

## Active tasks
| ID | Type | Title | Status | Owner | Depends on | Main ref | Validation | Notes |
|---|---|---|---|---|---|---|---|---|
| `T-1001` | `foundation` | Confirm canonical runtime paths, config, and logs against actual repo state | DONE | Codex |  | `10_architecture/data_and_paths.md` | `VAL-002` | Confirmed `antrack.utils.paths`, repo-local `settings.txt`, `src/data/`, and `src/logs/` remain the canonical runtime paths used by the application |
| `T-1002` | `foundation` | Confirm ThreadManager expectations and diagnostics hooks | DONE | Codex | `T-1001` | `10_architecture/logging_and_errors.md` | `VAL-003`, `VAL-004` | Confirmed `ThreadManager.start_thread/stop_thread/get_diagnostics` contract and reused it for SDR and scan background loops |
| `T-2001` | `architecture` | Freeze target package layout for future modules | DONE | Codex / Stéphane | `T-1001` | `10_architecture/package_layout.md` | `VAL-005` | New GUI split now matches the documented package target for future SDR insertion |
| `T-2002` | `architecture` | Freeze extracted UI module boundaries | DONE | Codex | `T-2001` | `10_architecture/module_boundaries.md` | `VAL-006` | `main_ui.py` reduced to composition/lifecycle; extracted Python-side UI modules added |
| `T-2003` | `architecture` | Apply instrument naming rules to forward-looking architecture docs | DONE | Stéphane / Codex | `T-2001` | `01_product/decisions.md` | Review | Owner decision recorded |
| `T-2004` | `architecture` | Prepare project-specific Codex handoff and phase prompts | DONE | Codex | `T-2002` | `05_coding_agent/ANTRACK_AGENT_ENTRYPOINT.md` | Review | SDR phase entrypoint and overlay prompts are now in place and were used to drive the current implementation |
| `T-3001` | `refactor` | Extract tracking-related UI block from `main_ui.py` | DONE | Codex | `T-2002` | `20_refactor/R10_extensibility_refactor.md` | `VAL-007` | `gui/tracking_ui.py` added and wired; `main_ui.py` no longer owns tracking detail methods |
| `T-3002` | `refactor` | Extract calibration/pass-preview UI block from `main_ui.py` | DONE | Codex | `T-2002` | `20_refactor/R10_extensibility_refactor.md` | `VAL-008` | `gui/calibration_ui.py` added; calibration tab setup remains Python-side with no `.ui` redesign |
| `T-3003` | `refactor` | Extract diagnostics and log-viewer integration from `main_ui.py` | DONE | Codex | `T-2002` | `20_refactor/R10_extensibility_refactor.md` | `VAL-009` | `gui/diagnostics_ui.py` added; existing diagnostics widget package retained |
| `T-3004` | `refactor` | Extract instrument UI block and remove forward-looking powermeter-centric wording from architecture-facing code paths | DONE | Codex | `T-2002`, `T-2003` | `20_refactor/R10_extensibility_refactor.md` | `VAL-005`, `VAL-006` | `gui/instrument_ui.py` added with transitional powermeter backend and instrument-oriented entry points |
| `T-3005` | `refactor` | Extract connection UI block from `main_ui.py` | DONE | Codex | `T-2002` | `20_refactor/R10_extensibility_refactor.md` | `VAL-007` | `gui/connection_ui.py` added; shutdown path tightened in `MainUi.closeEvent` |
| `T-4001` | `integration` | Run smoke tests on startup, connect, track, and preview flows | BLOCKED | Codex / Stéphane | `T-3001`, `T-3002`, `T-3003`, `T-3004`, `T-3005` | `90_quality/testing.md` | `VAL-010` | `pytest` is still unavailable in the current interpreter; offscreen startup/init-close validation passed and live UI/hardware smoke was exercised manually during positioning/manual control iteration |
| `T-4002` | `integration` | Review migration notes and remaining debt | DONE | Codex / Stéphane | `T-4001` | `20_refactor/migration_notes.md` | Review | Migration notes updated for manual positioning, time-mode synchronization, and forbidden-range enforcement |

## Done (recent)
| ID | Type | Title | Date | Verification | Notes |
|---|---|---|---|---|---|
| `T-0001` | `doc` | Instantiate stronger PRD template for Antrack | 2026-04-04 | Review | Initial version prepared from current repo state |
| `T-4003` | `integration` | Separate fixed-position motion from continuous tracking for Park and manual goto | 2026-04-14 | Compile + live smoke | `PositioningController` introduced; `Park` and manual goto now stop on arrival instead of reusing the continuous tracker loop |
| `T-4004` | `integration` | Synchronize event timestamps with the Time display mode across cards and selected target | 2026-04-14 | Compile + live smoke | `Local` and `UTC` modes now drive AOS/LOS/Max EL time formatting; `Sidereal` keeps event timestamps in local time |
| `T-4005` | `safety` | Enforce forbidden antenna ranges from settings during tracking and positioning | 2026-04-14 | Compile + live smoke | Forbidden AZ/EL ranges moved to `settings.txt`, rendered on gauges, and enforced by both tracking and fixed positioning logic |
| `T-4006` | `integration` | Add manual antenna control mode with jog and goto actions | 2026-04-14 | Compile + live smoke | `Auto/Manual` toggle now gates jog controls and manual goto without redesigning the `.ui` workflow |

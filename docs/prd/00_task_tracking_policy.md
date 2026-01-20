# Task tracking policy

This repository uses a simple task tracking strategy for development phases.

## Files
- `tasks_current.md`: **the only file Codex updates** during the active phase.
- `tasks_phase*.md`: archived snapshots at the end of each phase:
  - `tasks_phase1.md`
  - `tasks_phase1_1.md`
  - `tasks_phase2.md`
  - ...

## Workflow
1) Start of a phase:
   - create or reset `tasks_current.md` with the phase tasks.
2) During the phase:
   - update statuses in `tasks_current.md` (TODO / IN_PROGRESS / DONE / BLOCKED).
3) End of the phase:
   - copy `tasks_current.md` to the corresponding archive name (owner action).

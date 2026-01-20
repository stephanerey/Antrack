# Codex Phase 1.1 Instruction Contract — Antrack (Architecture cleanup)

**Audience:** Codex 5.2  
**Scope:** Phase 1.1 only (architecture cleanup, behavior-preserving)  
**Location:** `docs/prd/` (commit this file to the repo)

---

## IMPORTANT WORKFLOW (MANDATORY)

### Step 0 — Read-first / Questions-first
1) Read **ALL** PRD documents listed below **before editing any code**.
2) Produce a short **Readiness report**:
   - what you understood as Phase 1.1 scope (3–6 bullets)
   - the exact files/modules you expect to touch
   - remaining questions/ambiguities (if any)
3) If any question is blocking: **STOP** and ask first.

### Task tracking (MANDATORY)
- Create or update: `docs/prd/tasks_current.md` (Phase 1.1 tasks list)
- Keep it updated (TODO / IN_PROGRESS / DONE / BLOCKED) during the work.

---

## MISSION

**PHASE 1.1 ONLY — architecture cleanup.**
- Behavior must remain the same.
- Do NOT implement Phase 2 features.

---

## READ THESE DOCUMENTS FIRST
- `docs/prd/PRD.md`
- `docs/prd/00_conventions.md`
- `docs/prd/10_architecture/overview.md`
- `docs/prd/10_architecture/module_boundaries.md`
- `docs/prd/10_architecture/data_and_paths.md`
- `docs/prd/10_architecture/package_layout.md`
- `docs/prd/20_refactor/phase_1_1_arch_cleanup.md`
- `docs/prd/90_quality/testing.md`
- `docs/prd/90_quality/definition_of_done.md`

---

## HARD CONSTRAINTS
- No new third-party dependencies.
- Keep code simple; avoid fancy abstractions.
- QtWidgets only in `src/antrack/gui`.
- Core drivers/clients must not depend on QtWidgets.

---

## PHASE 1.1 SCOPE (WHAT TO DO)

1) **Create the target package structure**
- Add subpackages and `__init__.py` files as needed:
  - `src/antrack/core/instruments/`
  - `src/antrack/gui/axis/`
  - `src/antrack/gui/instruments/`
  - `src/antrack/gui/widgets/`
  - `src/antrack/gui/dialogs/`
  - `src/antrack/gui/diagnostics/`

2) **Split Powermeter**
- Move serial/protocol logic to:
  - `src/antrack/core/instruments/powermeter_client.py`
- Keep Qt wrapper as:
  - `src/antrack/gui/instruments/powermeter_qt.py`
- The GUI must continue to work unchanged from a user perspective.

3) **Move any other non-UI code out of gui/**
- Do a discovery pass:
  - anything that performs IO/protocol/parsing should live in `core/`
  - GUI keeps only Qt wrapper and UI code.

4) **Update imports + wiring**
- Update `main_ui.py` and related modules to use new paths.
- Keep canonical directories via `utils/paths.py`.

5) **Tests + verification**
- Update or add minimal unit tests for moved core modules (no Qt).
- Run the manual smoke tests from `90_quality/testing.md`.

---

## DELIVERABLES
- One PR for Phase 1.1, incremental commits.
- Updated `docs/prd/tasks_current.md` with final statuses and short notes per task.
- No functional changes beyond refactoring/moves/splits.

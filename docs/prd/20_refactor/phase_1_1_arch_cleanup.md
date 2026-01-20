# Phase 1.1 — Architecture cleanup (post-refactor 2.1.0)

**Date:** 2026-01-20  
**Goal:** Make the codebase structure consistent and scalable for Phase 2 features.

This phase is intentionally *non-functional*: behavior must remain the same.
The objective is to cleanly separate:
- **core** (hardware/instruments/IO),
- **tracking** (domain computations),
- **gui** (Qt widgets and Qt wrappers),
- **threading_utils** (background execution infrastructure),
- **utils** (paths/config helpers).

See also: `docs/prd/10_architecture/package_layout.md`.

---

## 1) Why this phase exists
After Phase 1, some modules are still located in `gui/` even though they contain
device/protocol logic. This increases coupling and makes Phase 2 harder to implement safely.

Example: `powermeter.py` currently mixes serial driver logic with Qt signals.

---

## 2) Scope (Phase 1.1)

### In scope
1) **Restructure packages** under `src/antrack/` to match the target tree:
   - add subpackages: `gui/axis/`, `gui/instruments/`, `gui/widgets/`, `gui/dialogs/`, `gui/diagnostics/`
   - add `core/instruments/` (and keep `core/axis/`)

2) **Split drivers from Qt wrappers**
   - `powermeter.py` -> split into:
     - `core/instruments/powermeter_client.py` (pure serial/protocol + parsing)
     - `gui/instruments/powermeter_qt.py` (QObject + signals + ThreadManager usage)

3) **Move additional non-UI logic out of gui/**
   - For every file in `gui/`:
     - If it performs IO/protocol/parsing without UI, move to `core/` and leave a thin Qt wrapper in `gui/`.

4) **Update imports and keep public behavior identical**
   - UI should still expose the same functionality.
   - ThreadManager usage must remain correct.
   - Paths must continue to come from `utils/paths.py`.

5) **Minimal tests**
   - Add/adjust unit tests for moved `core` modules (no Qt required).
   - Ensure existing tests still pass.

### Out of scope
- No new features (Phase 2 only).
- No UI redesign.
- No new dependencies.

---

## 3) Acceptance criteria
- `gui/` contains only:
  - QtWidgets UI code,
  - Qt wrappers (QObject + signals),
  - reusable widgets/dialogs.
- `core/` contains pure drivers/clients (no UI).
- `tracking/` remains a domain package (Option A), unchanged unless a file is incorrectly placed.
- App starts, connects, tracks, shows diagnostics/log viewer, and exits cleanly.
- Imports are clean and cyclic imports are avoided.

---

## 4) Migration steps (recommended order)
1) Create missing packages and `__init__.py` files.
2) Move files into their target packages (no code changes yet).
3) Split `powermeter` into client + Qt wrapper.
4) Update `main_ui.py` imports and wiring.
5) Run tests and basic manual smoke tests.
6) Update `docs/prd/tasks_current.md` with actual changes and status.

---

## 5) Discovery checklist (what to look for in gui/)
- Direct usage of:
  - sockets / serial ports,
  - parsing instrument responses,
  - file IO for catalogs or caches,
  - device protocols / command building.
These belong in `core/` (with a Qt wrapper in `gui/` only if needed).

# Codex Phase 1 Instruction Contract — Antrack (Refactor v2.01)

**Audience:** Codex 5.2  
**Scope:** Phase 1 only (behavior-preserving refactor)  
**Location:** `docs/prd/` (this file is intended to be committed to the repo)

---

## IMPORTANT WORKFLOW (MANDATORY)

### Step 0 — Read-first / Questions-first
1) Read **ALL** PRD documents listed in the **“READ THESE DOCUMENTS FIRST”** section below **before editing any code**.  
2) Produce a short **Readiness report** that includes:
   - What you understood as **Phase 1 scope** (3–6 bullets)
   - The **exact files/modules** you expect to touch (bullet list)
   - A list of **remaining questions or ambiguities** (if any)
3) If **ANY** question is blocking or could lead to a wrong architectural decision:
   - **STOP** and ask the questions first.
   - **Do not start coding** until the owner answers.
4) If everything is clear:
   - Explicitly say: **“Ready to implement Phase 1”** and then start coding.

### Task tracking (MANDATORY)
- Before coding, create (or update) a file at: `docs/prd/tasks.md`
- `tasks.md` MUST contain a checklist of all Phase 1 tasks you plan to do, with:
  - an ID (`T1`, `T2`, …)
  - a short title
  - status (`TODO` / `IN_PROGRESS` / `DONE` / `BLOCKED`)
  - a brief note for each change (what/where) and links to commits/PR sections if available
- Update `tasks.md` continuously as you work:
  - set tasks to `IN_PROGRESS` when started
  - set to `DONE` when completed
  - set to `BLOCKED` with the question if owner input is needed
- The final PR MUST include the final `tasks.md` reflecting what was actually done.

---

## MISSION

**PHASE 1 ONLY — behavior-preserving refactor.**  
Do **NOT** implement anything from `docs/prd/30_features/` (Phase 2).

---

## READ THESE DOCUMENTS FIRST (in this order)

- `docs/prd/PRD.md`
- `docs/prd/00_conventions.md`
- `docs/prd/10_architecture/overview.md`
- `docs/prd/10_architecture/runtime_environment.md`
- `docs/prd/10_architecture/module_boundaries.md`
- `docs/prd/10_architecture/data_and_paths.md`
- `docs/prd/20_refactor/v2_01_plan.md`
- `docs/prd/20_refactor/codebase_findings.md`
- `docs/prd/20_refactor/ui_modularization.md`
- `docs/prd/20_refactor/thread_manager.md`
- `docs/prd/20_refactor/migration_notes.md`
- `docs/prd/20_refactor/open_questions.md`
- `docs/prd/90_quality/testing.md`
- `docs/prd/90_quality/definition_of_done.md`

---

## HARD CONSTRAINTS

- **No new third-party dependencies.**
- **No technological overreach:** keep code simple, avoid fancy patterns and unnecessary helper sprawl.
- **QtWidgets imports ONLY** in `src/antrack/gui`. `QtCore` allowed in `threading_utils` only.
- Phase 1 must preserve behavior and MUST NOT add new user-facing features.

---

## OWNER CONFIRMED DECISIONS

- Logs: `src/logs/antrack.log`, **time-based rotation**, retain **7 days** (**stdlib only**).
- Phase 1 targets **repo-local dev mode**: runtime files remain in `src/data` and `src/logs`.
- Ephemeris binaries live in `src/data/ephemeris` and are **downloaded on-demand** when missing (best-effort, existing dependencies only).
- TLE files are **downloaded at runtime** and stored under `src/data/tle` (**preserve existing v2.01 URL/cadence if already implemented**).
- Configuration: `settings.txt` is the source of truth; allow env override (support at least `ANTRACK_CONFIG_PATH`).
- ThreadManager shutdown timeout default: **5 seconds**.
- No “do not touch” restrictions; modify what is necessary.

---

## PHASE 1 SCOPE

1) **Canonical paths**
   - Implement/complete `antrack/utils/paths.py` (repo/src/data/logs/config helpers).
   - Remove ad-hoc `Path(__file__).resolve().parents[...]` guesses outside `paths.py`.
   - Unify BSP/TLE/log/config paths through `paths.py`.

2) **Logging**
   - Centralize logging initialization in `antrack/main.py`.
   - Use stdlib time-based rotation with 7-day retention.
   - Ensure UI log viewer reads `src/logs/antrack.log` via `paths.py`.

3) **Configuration**
   - Standardize config resolution to `settings.txt`.
   - Support `ANTRACK_CONFIG_PATH` override.
   - Preserve v2.01 behavior (do not relocate config files in Phase 1).

4) **Ephemeris + TLE download-on-demand**
   - If required ephemeris/TLE file missing: best-effort download using existing libs/stdlib only.
   - If download fails: fail gracefully with clear message + log.

5) **UI modularization**
   - Keep `gui/main_ui.py` as orchestration/composition.
   - Extract cohesive UI blocks into `gui/*_ui.py` modules.
   - Keep business logic in core/tracking/services.

6) **ThreadManager hardening**
   - Keep existing API working.
   - Add internal registry + retention + traceback capture.
   - Implement `shutdown(graceful=True, timeout_s=5.0)`, idempotent, no orphan QThreads.

---

## DELIVERABLES

- One PR with incremental commits.
- PR description includes: spec references + How to test + risks.
- Minimal tests for `utils/paths.py` and non-UI logic.

---

## STOP CONDITIONS

- If TLE URL/cadence is not clearly encoded in v2.01 and a choice is required:
  - preserve current behavior and ask owner in the Readiness report instead of inventing new sources.

# Data, Logs, and Paths (Stable Contract)

## Goal
Make runtime file access deterministic and OS-neutral, regardless of current working directory.

## Canonical locations (Phase 1)
Phase 1 targets **repo-local development mode** (editable install / running from repository).

- Data assets base directory: `src/data/`
  - Ephemeris binaries: `src/data/ephemeris/`
  - TLE: `src/data/tle/`
  - Radio sources: `src/data/radiosources/` (or current repo structure if different)
  - Spacecraft catalog: `src/data/spacecrafts/` (or current repo structure if different)
- Logs directory: `src/logs/`
  - Default log file name: `antrack.log`

## Development vs installed mode
Rules:
- In Phase 1, canonical paths resolve relative to the repository `src/` directory.
- Code MUST NOT write into the installed package directory.
- If the application is later distributed as a package, path resolution MUST be revisited to use a user-data directory.

## Current codebase note (v2.01)
The current code uses multiple path strategies (mix of repo-root `data/...` and `src/data/...`).
Phase 1 MUST unify all path usage through a single helper (below) and MUST stop using ad-hoc `Path(...).parents[...]` project-root guesses.

## Rules
- Code MUST NOT assume the current working directory.
- Code MUST use `pathlib.Path`.
- Provide a single helper module: `antrack/utils/paths.py` exposing:
  - `get_repo_root()` (dev mode only)
  - `get_src_root()`
  - `get_data_dir()` (returns `src/data`)
  - `get_logs_dir()` (returns `src/logs`)
- Any direct usage of `Path(__file__).resolve().parents[...]` to guess the repo root is forbidden outside `paths.py`.

## Ephemeris binaries (BSP) — download on demand

## TLE files — download policy
Policy (owner-confirmed):
- TLE files are downloaded at runtime (not treated as authoritative versioned assets).
- Downloaded TLE files MUST be stored under `src/data/tle/` (dev mode).
- Phase 1 MUST preserve the current v2.01 behavior regarding:
  - which source URL(s) are used,
  - when refresh happens (only download when missing unless v2.01 already refreshes).
- If download fails, the app MUST fail gracefully with a clear message and a log entry.


Policy (owner-confirmed):
- BSP binaries live under `src/data/ephemeris/`.
- If the required file does not exist locally, the app SHOULD attempt a best-effort download using **existing dependencies only**.
- If download fails (offline, blocked, etc.), the app MUST:
  - log a clear error,
  - surface a user-friendly message (UI) indicating the missing file and expected location.

Phase 1 scope limitation:
- Phase 1 only needs to implement download-on-demand for the ephemeris assets that are currently used by v2.01.
- Do not add new ephemeris datasets.

## Logging
- Default log file MUST be `src/logs/antrack.log`.
- Log rotation MUST be enabled (stdlib only) with **7 days** retention.
  - Recommended: `TimedRotatingFileHandler` with `backupCount=7`.


## Acceptance criteria
- The application runs from different working directories.
- Data and logs resolve to the canonical locations.
- No module directly references `.../data/...` via `parents[...]` heuristics.
- If the ephemeris file is missing, the app attempts download and fails gracefully with a clear error if it cannot.


## Configuration (settings.txt)
Policy (owner-confirmed):
- Default configuration file is `settings.txt` (repo-local).
- Environment override is allowed via `ANTRACK_CONFIG_PATH`.
- Phase 1 MUST NOT relocate configuration; it should only canonicalize resolution order via `utils/paths.py`.

# Repository Guidelines - Antrack (Antenna Noise Tracker)

## Project Overview
Antrack is a Python 3.12 desktop app (PyQt5 + qasync) for antenna tracking.

## Project Structure & Module Organization
- `pyproject.toml` metadata, dependencies, `antrack` entry point.
- `src/antrack/` main package: `core/` (hardware I/O), `gui/` (Qt UI), `tracking/` (tracking logic), `threading_utils/`, `utils/`.
- `settings.txt` repo-local configuration file (override with `ANTRACK_CONFIG_PATH`).
- `src/data/` assets (ephemeris `*.bsp`, TLEs `*.tle`, catalogs `*.csv`).
- `tests/` pytest tests; `logs/` runtime logs; `docs/` docs.

## Build, Install, and Run
### Environment
- Python 3.12.x only
- Windows target; keep paths neutral

### Commands (PowerShell)
```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -U pip setuptools wheel
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

```powershell
antrack
# Alternative:
python -m antrack.main
```

```powershell
python -m pytest -q
```

## Coding Style & Import Rules
- Use 4-space indentation, `snake_case` for modules/functions, `CamelCase` for classes.
- Internal imports must be absolute under `antrack`; do not rely on `PYTHONPATH`.

## Path, Data, and Configuration
- Build paths via `pathlib.Path`; never assume the current working directory.
- `settings.txt` is the default; avoid hard-coded IPs, ports, or device IDs.
- Large files under `src/data/` must never be auto-downloaded or overwritten without explicit user intent.

## Testing Guidelines
- Use pytest; name tests `test_*.py` under `tests/`.
- Run a manual UI smoke test for GUI changes.

## Refactoring Guidelines
- Refactoring must be incremental, behavior-preserving, and validated by manual UI tests.

## Qt & Threading Rules
- Qt widgets must only be created and accessed from the main thread.
- Worker threads must communicate with the GUI via signals/slots only.

## Commit & Pull Request Guidelines
- If this folder is part of a parent Git repo, follow its commit and PR standards.
- Include a clear summary and how-to-test notes in PRs or change descriptions.

## Context Rules (Cost Control)
- Do not read or paste entire directories or do a full repo review without asking.
- Before reading a file over 1000 lines, ask and propose a targeted `rg` first.
- Open at most 5 files per iteration; if more is needed, propose a staged plan.
- Avoid: `cat` on large files, global `git diff`, raw log dumps.
- Prefer: `rg "pattern" -n`, then open only relevant files.
- Summarize long outputs; suggest `/compact` if context grows long.

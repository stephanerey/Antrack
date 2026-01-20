# Runtime Environment (Stable Contract)

## Goal
Provide a precise description of the technical environment so development remains consistent and reproducible.

## Python & packaging
- Python version: `>=3.12,<3.13`
- Build system: `setuptools` (`setuptools.build_meta`)
- Source layout: `src/` layout, package under `src/antrack/`
- Console entry point: `antrack = antrack.main:main`

## Baseline dependencies (already in repo)
Phase 1 MUST NOT add new dependencies. The baseline set (from `pyproject.toml`) is:
- UI/event loop: `PyQt5`, `qasync`
- Data: `numpy`, `pandas`
- Tracking: `skyfield`, `spiceypy`
- Plotting: `matplotlib`, `pyqtgraph`
- Hardware I/O: `pyserial`

## Dev dependencies
Optional dev extras:
- Tests: `pytest`

## Operating systems
- Primary target: Windows
- The code MUST remain cross-platform where reasonably possible:
  - Use `pathlib.Path`
  - No assumptions on current working directory
  - No hard-coded absolute paths

## Event loop integration
- The GUI event loop is Qt.
- Async tasks (if any) are integrated via `qasync`.
- Long-running work MUST NOT block the GUI thread; it MUST run through the ThreadManager.

## Recommended developer workflow
- Create a virtual environment (or use the repo-managed environment).
- Install in editable mode:
  - `pip install -e .[dev]`
- Run the application:
  - `antrack`

## Logging expectations
- Logging MUST be initialized in `antrack/main.py`.
- Default log location is canonicalized via `antrack/utils/paths.py` and the default file name is `antrack.log`.
- Log rotation MUST be enabled using stdlib only with **7 days** retention (time-based).


## Notes on packaging
Phase 1 targets development (editable install) as the primary mode.
If a packaged/distributed mode is needed later, path resolution MUST be revisited to avoid writing logs inside the installed package directory.


## Configuration expectations
- Default configuration file is `settings.txt` (repo-local).
- Environment override MUST be supported at least for config path: `ANTRACK_CONFIG_PATH`.

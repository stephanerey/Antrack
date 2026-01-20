# Phase 1 Tasks (Refactor v2.01)

- [ ] T1 - Canonical paths and data/log consolidation (DONE): added `src/antrack/utils/paths.py`, removed root `data/`, and aligned assets under `src/data` + `src/logs`.
- [ ] T2 - Logging initialization (DONE): centralized logging in `src/antrack/main.py` with 7-day time-based rotation and UI log viewer via `LogViewerDialog`.
- [ ] T3 - Configuration resolution (DONE): standardized on repo-root `settings.txt` with `ANTRACK_CONFIG_PATH` override.
- [ ] T4 - Ephemeris and TLE download-on-demand (DONE): added `load_planets` download fallback and resilient TLE refresh with cache fallback.
- [ ] T5 - UI modularization (DONE): moved diagnostics and log viewer into `gui/*_ui.py` modules; `main_ui.py` now orchestrates.
- [ ] T6 - ThreadManager hardening (DONE): added task registry/retention, traceback capture, and `shutdown(graceful=True, timeout_s=5.0)` while keeping legacy APIs.
- [ ] T7 - Tests (DONE): added pytest coverage for `utils/paths.py` and ThreadManager behaviors; skipped legacy manual plot in automated runs.
- [ ] T8 - Documentation and verification (DONE): updated migration notes and kept manual UI smoke tests in `90_quality/testing.md`.

# Architecture Overview

## Target runtime
- Python: 3.12
- UI: PyQt5 + qasync
- Source layout: `src/` layout (package code under `src/antrack/`)

## High-level layering
- `antrack/core/`: hardware I/O and low-level clients
- `antrack/tracking/`: computations (ephemeris, az/el, pass prediction)
- `antrack/threading_utils/`: background execution orchestration + diagnostics
- `antrack/utils/`: configuration and cross-cutting helpers
- `antrack/gui/`: PyQt5 UI only

## Entry point
- Console entry point MUST start the app via `antrack.main:main`.
- `main.py` responsibilities:
  - initialize logging
  - load config
  - resolve paths
  - start Qt event loop and show main window

## Technology guardrails
- The project prioritizes simplicity and long-term maintainability.
- **No new dependency / library / framework may be introduced without explicit owner approval.**
- Phase 1 MUST NOT add any third-party dependencies.

## Stability
Files under `10_architecture/` are treated as **stable contracts**. Feature work MUST comply.

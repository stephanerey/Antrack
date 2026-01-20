# UI Modularization

## Goal
Reduce `main_ui.py` complexity by extracting cohesive UI blocks into dedicated modules, while keeping `main_ui.py` as the composition root.

## Rules
- UI modules MUST live under `src/antrack/gui/`.
- UI modules MUST NOT embed business logic; they call services in `core/` and `tracking/`.

## Proposed module pattern
Each UI module SHOULD expose a class with:
- `__init__(self, main_window, services, thread_manager)`
- `connect_signals(self)`
- `shutdown(self)`

## Suggested split
- Tracking panel: object selection, scheduling, pass preview => `tracking_ui.py`
- Calibration panel: workflow and results display => `calibration_ui.py`
- Plots: plot widgets init, bindings, shared crosshair/time sync => `plots_ui.py`
- Thread diagnostics view: list tasks, cancel, show traceback => `diagnostics_ui.py`

## Acceptance criteria
- `main_ui.py` size reduced and focused on wiring.
- Feature modules are testable via minimal integration smoke tests.

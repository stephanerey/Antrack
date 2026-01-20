# Module Boundaries (Stable Contract)

## Goal
Keep a clean separation between GUI, computations, and hardware I/O.

## Allowed dependencies
- `antrack/gui/` MAY use all PyQt5 modules (QtWidgets, QtCore, QtGui) and any UI-specific helpers.
- `antrack/threading_utils/` MAY use PyQt5 **QtCore only** (QObject, QThread, signals) for safe background execution.
- `antrack/core/`, `antrack/tracking/`, `antrack/utils/` MUST NOT depend on PyQt5 QtWidgets.

## Rules
1. **No widgets outside GUI**
   - Only modules under `src/antrack/gui/` may import `PyQt5.QtWidgets`.
   - Importing `PyQt5.QtCore` in `threading_utils/` is allowed.

2. **No business logic in GUI**
   - GUI modules MUST NOT implement tracking computations.
   - GUI modules MUST call into `tracking/` and `core/` services.

3. **No hardware I/O in tracking**
   - `tracking/` MUST remain computation-only.

4. **Single source of truth for paths**
   - Runtime paths MUST be obtained via `antrack/utils/paths.py`.

## Acceptance criteria
- A grep over the codebase shows no `PyQt5.QtWidgets` import outside `src/antrack/gui/`.
- The GUI remains responsive while background tasks run.

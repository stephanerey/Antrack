# Package layout (Option A — domain packages)

**Date:** 2026-01-20  
**Applies to:** Phase 1.1 (Architecture cleanup) and onward

This document defines the *target* repo and package structure after Phase 1.1.
We keep **Option A**: `tracking/` remains a domain package (separate from `core/`).

---

## Target repository tree (high level)

```
repo_root/
  pyproject.toml
  settings.txt
  src/
    antrack/
      main.py
      __init__.py

      core/
        axis/
          __init__.py
          axis_client.py
          # (future) protocol / DTOs related to axis
        instruments/
          __init__.py
          powermeter_client.py
          # (future) receiver_client.py, spectrum_analyzer_client.py, etc.
        transports/
          __init__.py
          # (optional future) serial_transport.py, tcp_transport.py

      tracking/
        __init__.py
        ephemeris/
          __init__.py
          ephemeris_service.py
          ephemeris_qt_adapter.py   # if this is QtCore-only adapter; if QObject/signals, keep in gui/
        tle/
          __init__.py
          satellites.py
          tle_repository.py
        radiosources/
          __init__.py
          radiosources.py
        spacecrafts/
          __init__.py
          spacecrafts.py
        passes/
          __init__.py
          pass_prediction.py
        coords/
          __init__.py
          conversions.py
          frames.py

      gui/
        __init__.py
        main_ui.py              # composition root for UI
        app_runtime.py
        axis/
          __init__.py
          axis_client_qt.py
        instruments/
          __init__.py
          powermeter_qt.py
        widgets/
          __init__.py
          angle_gauge_widget.py
          multi_track_card.py
          calibration_plots.py   # rename from calibration.py if desired
        dialogs/
          __init__.py
          log_viewer_ui.py
        diagnostics/
          __init__.py
          diagnostics_ui.py

      threading_utils/
        __init__.py
        thread_manager.py

      utils/
        __init__.py
        paths.py
        settings_loader.py

    data/
      ephemeris/
        de440s.bsp
        # other kernels (downloaded on demand)
      tle/
        # downloaded on demand
      radiosources/
      spacecrafts/

    logs/
      antrack.log
      antrack.log.1
      ...
```

---

## Placement rules (source of truth)

### gui/
- QtWidgets only.
- Qt wrappers (QObject + signals) live here (e.g. `axis_client_qt.py`, `powermeter_qt.py`).

### core/
- Pure I/O and hardware clients/drivers.
- No QtWidgets.
- Prefer no Qt at all; if QtCore is unavoidable, justify explicitly.

### tracking/
- Computation and catalogs (ephemerides, passes, coordinate conversions).
- No GUI code.

### threading_utils/
- ThreadManager + background task infrastructure (QtCore allowed).

### src/data + src/logs
- Canonical runtime directories in repo-local dev mode.
- TLE and ephemeris may be downloaded on demand if missing.

---

## Notes
- Names above are a *target*. If some modules already exist with different names, Phase 1.1 may keep names but must place them in the correct package.
- Do not change behavior in Phase 1.1; only move/split code and update imports.

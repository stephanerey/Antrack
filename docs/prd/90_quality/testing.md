# Testing

## Goal
Define a minimal, robust testing strategy.

## Unit tests (pytest)
- `tracking/`: pure computations (no Qt)
- `utils/paths`: path resolution
- `threading_utils`: state machine behaviors (no widget dependency)

## Integration smoke tests (manual)
1. Start application
2. Open main window and verify no exceptions
3. Trigger one background task and verify it appears in diagnostics
4. Exit and verify clean shutdown

## Acceptance criteria
- CI (or local) can run unit tests without GUI.
- Manual smoke test is documented and reproducible.

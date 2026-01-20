# Tasks — Phase 1.1 (Architecture cleanup)

Statuses: TODO / IN_PROGRESS / DONE / BLOCKED

- [x] T1 (DONE) Create target package subdirectories + __init__.py files (core/instruments, gui/axis, gui/instruments, gui/widgets, gui/dialogs, gui/diagnostics)
- [x] T2 (DONE) Split Powermeter into core client + Qt wrapper (powermeter_client.py + powermeter_qt.py)
- [x] T3 (DONE) Move any remaining non-UI modules out of gui/ into core/ (discovery pass + moves)
- [x] T4 (DONE) Update imports + wiring in main_ui.py and other GUI modules
- [x] T5 (DONE) Update/extend unit tests for moved core modules (no Qt)
- [x] T6 (DONE) Run manual smoke test checklist and update notes (owner confirmed ok)
- [x] T7 (DONE) Update this tasks_current.md with actual status and notes

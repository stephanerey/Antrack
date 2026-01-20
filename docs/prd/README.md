# PRD — Antrack (Antenna Tracker)

This folder contains the Product Requirements and Technical Specs that drive:
- **Phase 1**: behavior-preserving refactor of **v2.01**
- **Phase 2+**: feature development (tracking + calibration)

## Start here
1) [PRD.md](PRD.md)
2) [00_conventions.md](00_conventions.md) (hard constraints for Codex)
3) `10_architecture/*` (stable contracts)
4) `20_refactor/*` (Phase 1 only)

## Codex workflow (expected)
1. Read **PRD.md** and **00_conventions.md**.
2. Read **10_architecture/** (non-negotiable architectural rules).
3. Execute **20_refactor/v2_01_plan.md** first (Phase 1).
4. Only after Phase 1 is accepted, implement **30_features/** (Phase 2+).
5. Always validate against **90_quality/** (DoD + tests).

## Table of contents

### Master document
- [PRD.md](PRD.md)

### Conventions
- [00_conventions.md](00_conventions.md)

### Architecture (stable contracts)
- [10_architecture/overview.md](10_architecture/overview.md)
- [10_architecture/runtime_environment.md](10_architecture/runtime_environment.md)
- [10_architecture/module_boundaries.md](10_architecture/module_boundaries.md)
- [10_architecture/data_and_paths.md](10_architecture/data_and_paths.md)

### Refactor (Phase 1)
- [20_refactor/v2_01_plan.md](20_refactor/v2_01_plan.md)
- [20_refactor/codebase_findings.md](20_refactor/codebase_findings.md)
- [20_refactor/open_questions.md](20_refactor/open_questions.md)
- [20_refactor/ui_modularization.md](20_refactor/ui_modularization.md)
- [20_refactor/thread_manager.md](20_refactor/thread_manager.md)
- [20_refactor/migration_notes.md](20_refactor/migration_notes.md)

### Features (Phase 2+)
- Tracking
  - [30_features/tracking/overview.md](30_features/tracking/overview.md)
  - [30_features/tracking/pass_prediction.md](30_features/tracking/pass_prediction.md)
  - [30_features/tracking/plots.md](30_features/tracking/plots.md)
- Calibration
  - [30_features/calibration/overview.md](30_features/calibration/overview.md)
  - [30_features/calibration/solar_noise.md](30_features/calibration/solar_noise.md)

### Quality
- [90_quality/testing.md](90_quality/testing.md)
- [90_quality/definition_of_done.md](90_quality/definition_of_done.md)

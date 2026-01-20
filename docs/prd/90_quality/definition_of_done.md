# Definition of Done (DoD)

## Phase 1 (Refactor)
- Boundaries respected: no Qt widgets outside `gui/`
- Canonical data/log locations enforced
- ThreadManager meets spec and provides diagnostics
- Application starts and shuts down cleanly
- Minimal unit tests added for non-UI layers
- Docs updated (this PRD + smoke tests)

## Phase 2+ (Features)
- Feature spec file exists and is referenced by the PR
- Unit tests for computations
- UI remains responsive (background compute via ThreadManager)
- Logging and error handling implemented

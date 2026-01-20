# Conventions

## Language
- The PRD is written in **English**.
- Discussion may happen in French, but the spec text consumed by agents MUST remain English.

## Normative keywords
Use RFC-style keywords:
- **MUST / MUST NOT**: hard requirements
- **SHOULD / SHOULD NOT**: strong recommendations
- **MAY**: optional

## Scope control
Each spec file MUST contain:
- **Goal**: what is being achieved
- **Non-goals**: what is explicitly out of scope
- **Constraints**: environment, versions, performance, safety
- **Acceptance criteria**: testable outcomes

## Agent working agreement
- Changes MUST be incremental and reviewable.
- Behavior-preserving refactor first (Phase 1). New features come later (Phase 2+).
- UI objects MUST only be accessed from the Qt main thread.
- Background work MUST be executed through the ThreadManager.
- Paths MUST be OS-neutral and MUST NOT assume the current working directory.
- Any worker exception MUST be captured and surfaced (no silent failure).

## Technology guardrails (MANDATORY)
- No technological overreach. Prefer the simplest solution that meets the requirements.
- The refactor MUST NOT introduce “fancy” patterns, frameworks, or meta-programming.
- Avoid creating new layers (factories, registries, service-locators, event-buses) unless explicitly required by this PRD.

## Dependencies policy (MANDATORY)
- **Phase 1 MUST NOT introduce any new third-party dependency.**
- More generally: NO new dependency / library / framework may be added without the owner’s explicit approval.
- If a dependency seems necessary, STOP and:
  1) document the exact need,
  2) propose a standard-library alternative,
  3) ask for approval in the PR description before adding anything.

## Code style & complexity guardrails (MANDATORY)

### Refactoring rule of thumb
- Refactor ONLY when it improves clarity, boundaries, or testability.
- Prefer small, incremental refactors with behavior preserved.

### Core principles
- The codebase MUST remain simple, readable, and maintainable.
- Prefer straightforward Python over “clever” abstractions.
- Do not create extra helper modules/classes “just in case”.

### When helpers are allowed
Create a helper ONLY if at least one of these is true:
1) the same logic is duplicated in 3+ places,
2) it significantly improves testability,
3) it encapsulates a risky/complex operation (I/O, threading, parsing) with clear boundaries.

If a helper is created, it MUST have a clear name, minimal surface area, and be located close to the feature that uses it.

### Coding style expectations
- Use explicit types (type hints) for public APIs and non-trivial functions.
- Keep functions short and cohesive. Prefer early returns over deep nesting.
- Avoid premature optimization; optimize only with evidence.

### Documentation & comments (MANDATORY)
- Every public module, class, and method/function MUST be documented.
- Each method/function MUST include a docstring describing:
  - purpose (what it does),
  - parameters (name, type, meaning),
  - return value (type, meaning),
  - raised exceptions (if any),
  - side effects (I/O, network, threading, hardware calls).

Recommended docstring format (Google style):

```python
def compute_passes(tle_path: Path, start: datetime, hours: int) -> list[Pass]:
    """Compute predicted passes over the observer site.

    Args:
        tle_path: Path to the TLE file to use.
        start: Start datetime for the prediction window (timezone-aware).
        hours: Prediction horizon in hours.

    Returns:
        A list of predicted passes sorted by AOS time.

    Raises:
        FileNotFoundError: If the TLE file does not exist.
        ValueError: If the input time range is invalid.
    """
```

## File naming
- Architecture files: stable names under `10_architecture/`.
- Refactor plans: versioned filenames under `20_refactor/`.
- Features: one file per feature under `30_features/<domain>/`.

## Traceability
When implementing a PR:
- Reference the exact spec files in the PR description.
- Provide a short “How to test” section.

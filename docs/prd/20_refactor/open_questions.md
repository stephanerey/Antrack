# Open Questions (owner decisions)

## Goal
List decisions that may impact the refactor. Codex MUST NOT make assumptions on these without explicit confirmation.

If answers are not provided, Phase 1 SHOULD choose the least risky option that preserves current behavior and document it in the PR.

---

## A) Runtime paths & packaging
1) **Repo-local vs installed mode**
   - Phase 1 canonicalizes `src/data` and `src/logs` as repo-local locations (dev mode).
   - Is it acceptable that packaged/installed mode is not fully supported in Phase 1, or do you require a fallback to a user-data directory?

2) **Ephemeris/BSP assets location and download policy**
   - Should BSP files live under `src/data/ephemeris/`?
   - If a required BSP is missing locally, should the app download it on demand (best-effort) or fail with a clear error?

3) **TLE storage policy**
   - Should TLE files be versioned in the repo under `src/data/tle/`, or downloaded at runtime?
   - If downloaded: which URL(s) and update cadence?

## B) Logging
4) **Log filename**
   - Keep `antenna_tracker.log` or standardize to `antrack.log`?

5) **Retention/rotation**
   - Is rotation required? If yes: size-based or time-based, and what retention?

## C) Configuration
6) **Config source of truth**
   - Where is the configuration stored today (file name, location)?
   - Should Phase 1 enforce a single location (repo-local) or allow override via environment variables?

7) **User overrides**
   - Do you want environment variables for paths (data/logs/config) in Phase 1?

## D) Threading expectations
8) **Cancellation semantics**
   - Should cancellation be best-effort (cooperative) or do you require hard abort?
   - If cooperative: what is an acceptable timeout during shutdown?

9) **Diagnostics surface**
   - In Phase 1, do you want an in-app diagnostics panel (recommended), or is logging-only acceptable?

## E) Hardware integration scope (Phase 1)
10) **Hardware I/O stability**
    - Are there any known hardware communication edge cases that MUST NOT change during Phase 1?
    - Any files/modules that should be treated as “do not touch”?

---

## Answers (owner-confirmed)
**A1 (Repo-local vs installed mode):** Phase 1 targets repo-local development mode. Keep runtime files in `src/data` and `src/logs`. No packaged/installed-mode fallback required in Phase 1.

**A2 (Ephemeris/BSP):** BSP binaries live in `src/data/ephemeris/`. If a required file is missing, the app should attempt a best-effort download using existing dependencies only. This may not apply to every possible dataset; Phase 1 should implement it for the datasets actually used by v2.01, and fail gracefully with a clear message for others.

**A3 (TLE policy):** TLE files are downloaded at runtime and stored under `src/data/tle/` (dev mode). Phase 1 should preserve existing v2.01 behavior for URL(s) and refresh cadence (download on demand when missing unless v2.01 already refreshes).

**B4 (Log filename):** Standardize to `antrack.log`.

**B5 (Rotation/retention):** Enable time-based log rotation with **7 days** retention.

**C6-C7 (Config + overrides):** Config source of truth is `settings.txt`. Environment override is allowed; Phase 1 MUST support at least `ANTRACK_CONFIG_PATH`.

**D8 (Shutdown timeout):** Cooperative cancellation is acceptable. Default shutdown timeout is **5 seconds**.

**D9 (Diagnostics surface):** In-app diagnostics panel is required (keep or improve existing).

**E10 (Do not touch):** No restrictions; Phase 1 may modify any modules as necessary.

## Still open
- TLE source URL(s) and refresh cadence (only if not already encoded in v2.01). Phase 1 should preserve current behavior.

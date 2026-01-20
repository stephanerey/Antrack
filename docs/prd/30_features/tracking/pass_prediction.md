# Feature: Pass Prediction

## Goal
Compute upcoming passes for selected objects and present them in the UI.

## Inputs
- Observer coordinates
- Target object definition (TLE / ephemeris / catalog)
- Time window (start/end)

## Outputs
- List of passes with:
  - AOS/LOS times
  - max elevation time + value
  - az/el at key points

## Constraints
- Computation MUST be non-UI (tracking layer).
- UI MUST schedule computations via ThreadManager.

## Acceptance criteria
- Given a known TLE and observer, pass list matches expected results (unit test).
- UI displays passes and allows selecting one for plotting.

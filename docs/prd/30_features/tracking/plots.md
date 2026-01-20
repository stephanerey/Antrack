# Feature: Predictive Plots

## Goal
Plot az/el vs time for predicted passes and show key markers.

## Requirements
- Plot MUST be updated when:
  - object changes
  - time window changes
  - observer changes
- Plot SHOULD include:
  - AOS/LOS markers
  - max elevation marker

## Constraints
- Plotting logic belongs to GUI; data generation belongs to tracking.

## Acceptance criteria
- Plots render without blocking UI (compute in background).
- Markers correspond to pass prediction output.

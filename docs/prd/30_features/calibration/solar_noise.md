# Feature: Solar Noise Calibration

## Goal
Calibrate the antenna pointing by measuring solar noise with the receiver mounted on the dish.

## Workflow (high level)
1. Compute sun position and define scan grid around nominal az/el
2. Move antenna along scan pattern
3. Acquire noise power measurements per point
4. Fit peak and estimate pointing offset
5. Store calibration result and apply correction

## Requirements
- All motor moves and acquisition MUST be orchestrated safely (abort/cancel supported).
- UI MUST remain responsive; long steps run via ThreadManager.
- Results MUST be logged and persisted.

## Acceptance criteria
- A full scan can be executed and cancelled.
- Peak detection returns consistent offsets for repeat runs.
- Calibration offset is applied to subsequent tracking.

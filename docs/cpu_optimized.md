# CPU-Optimized Mode

`cpu_optimized` is intended for low-power Windows PCs that need to keep tracking and SDR acquisition responsive without saturating the CPU.

## Settings

Add or update the `[PERFORMANCE]` section in `settings.txt`:

```ini
[PERFORMANCE]
CPU_OPTIMIZED = true
MIN_MOVE_DURATION = 0.2
MOVE_REFRESH_INTERVAL = 1.2
FFT_FPS = 5.0
PLOT_REFRESH_FPS = 4.0
MAX_FFT_SIZE = 2048
MAX_WORKERS = 4
```

## What Changes

- Tracking loops are coordinated by a shared `TrackingManager` instead of one dedicated tracking thread per target.
- SDR IQ reading and spectrum publication now run inside one worker instead of two background workers.
- When `CPU_OPTIMIZED` is enabled:
  - tracking uses at least `MIN_MOVE_DURATION`
  - motion command refresh uses `MOVE_REFRESH_INTERVAL`
  - FFT work is capped by `FFT_FPS`
  - spectrum repaint cadence is capped by `PLOT_REFRESH_FPS`
  - effective FFT size is capped by `MAX_FFT_SIZE`

## Recommended Validation

1. Launch Antrack with `CPU_OPTIMIZED = false`.
2. Start tracking and an SDR session, then note CPU usage and UI responsiveness.
3. Enable `CPU_OPTIMIZED = true` and relaunch.
4. Repeat the same scenario on the target PC, ideally the Intel N100 machine.
5. Confirm:
   - CPU usage stays below the target budget
   - tracking precision remains acceptable
   - spectrum and SNR updates still behave correctly
   - closing the app leaves no stray worker running

## Notes

- The new limits are conservative defaults, not mandatory values.
- If spectrum detail is too coarse, increase `MAX_FFT_SIZE` gradually and retest CPU usage.
- Manual GUI validation is still required for final acceptance.

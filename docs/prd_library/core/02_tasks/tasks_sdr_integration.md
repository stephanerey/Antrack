---
title: Tasks - Integration SDR
last_updated: 2026-04-14
---

## Contexte

Ce tableau liste les taches realisees pour integrer le backend SDR, les widgets de spectre/waterfall et la mesure de SNR dans Antrack. Les identifiants suivent la numerotation 5000+, distincte des phases precedentes.

## Taches actives

| ID | Type | Titre | Status | Owner | Depends on | Main ref | Validation | Notes |
|---|---|---|---|---|---|---|---|---|
| `T-5001` | `backend` | Creer `core/instruments/sdr_client.py` a partir de `src/core/sdr.py` de RSPdx | DONE | Codex | `T-2004` | `05_sdr_integration.md` | `VAL-SDR-001` | `sdr_client.py` added with SoapySDR discovery, dummy fallback, safe settings updates, IQ streaming, FFT handling, and perf/status signals |
| `T-5002` | `backend` | Implementer `measure_band_power` et `compute_snr` dans `core/dsp` | DONE | Codex | `T-5001` | `05_sdr_integration.md` | `VAL-SDR-002` | Added `core/dsp/{fft,filters,snr}.py`; `measure_band_power` returns an integrated in-band power in dB and `compute_snr` supports relative/absolute modes |
| `T-5003` | `frontend` | Creer `gui/widgets/spectrum_plot.py` et `gui/widgets/waterfall_plot.py` inspires de RSPdx | DONE | Codex | `T-5001` | `05_sdr_integration.md` | `VAL-SDR-003` | PyQtGraph widgets implemented around shared `DataStorage`, with spectrum overlays and waterfall history decimation |
| `T-5004` | `frontend` | Creer `gui/instruments/sdr_ui.py` et l'onglet SDR dans MainUi | DONE | Codex | `T-5003` | `05_sdr_integration.md` | `VAL-SDR-004` | SDR tab now builds dynamically in `MainUi` with controls, live status, spectrum, waterfall, and managed background streaming |
| `T-5005` | `integration` | Afficher le SNR courant dans `Selected Target` | DONE | Codex | `T-5004` | `05_sdr_integration.md` | `VAL-SDR-005` | `Selected Target` now exposes an SNR row updated from the SDR backend in relative or absolute mode |
| `T-5006` | `test` | Ajouter tests unitaires pour `sdr_client`, `measure_band_power`, `compute_snr` | DONE | Codex | `T-5002` | `05_sdr_integration.md` | `VAL-SDR-006` | Added dummy-mode unit tests for spectrum, band-power stability, and SNR; validated with `pytest` in the project venv |

## Validation

Les validations specifiques aux taches SDR sont definies dans `90_quality/validation_matrix.md` sous les identifiants `VAL-SDR-001` a `VAL-SDR-006`.

---
title: Tasks - Scan et calibration
last_updated: 2026-04-14
---

## Contexte

Ce tableau definit les taches necessaires pour implementer les strategies de scan decrites dans `06_scan.md`. Les identifiants 6000+ sont reserves a cette phase.

## Taches actives

| ID | Type | Titre | Status | Owner | Depends on | Main ref | Validation | Notes |
|---|---|---|---|---|---|---|---|---|
| `T-6001` | `backend` | Implementer `scan_grid.py` pour generer les points d'un scan en grille et supporter une passe grossiere + fine | DONE | Codex | `T-5002` | `06_scan.md` | `VAL-SCAN-001` | Added grid generation with zigzag ordering and reusable coarse/fine helpers |
| `T-6002` | `backend` | Implementer `scan_cross.py` pour realiser un cross-scan 1D orthogonal | DONE | Codex | `T-5002` | `06_scan.md` | `VAL-SCAN-002` | Added orthogonal cut generation and direct offset estimation from measured curves |
| `T-6003` | `backend` | Implementer `scan_spiral.py` pour generer une trajectoire spirale et interpolation sur grille | DONE | Codex | `T-5002` | `06_scan.md` | `VAL-SCAN-003` | Added Archimedean spiral generation and sample-to-grid projection for heatmap display |
| `T-6004` | `backend` | Implementer `scan_session.py` pour orchestrer les scans et communiquer les resultats | DONE | Codex | `T-6001`, `T-6002`, `T-6003` | `06_scan.md` | `VAL-SCAN-004` | `ScanSession` now orchestrates move/settle/measure loops via `ThreadManager`, emits progress/results, and supports export |
| `T-6005` | `frontend` | Creer `gui/widgets/heatmap_widget.py` et graphiques 1D pour afficher les mesures du scan | DONE | Codex | `T-6004` | `06_scan.md` | `VAL-SCAN-005` | Added `HeatmapWidget` and cross-scan curves for live visualization |
| `T-6006` | `frontend` | Creer `gui/scan_ui.py` pour la configuration, le controle et l'affichage des scans | DONE | Codex | `T-6005` | `06_scan.md` | `VAL-SCAN-006` | Scan tab now builds dynamically with strategy selection, parameters, control buttons, progress, and result displays |
| `T-6007` | `integration` | Ajouter l'offset calcule au modele de calibration et au panneau Selected Target | DONE | Codex | `T-6006` | `06_scan.md` | `VAL-SCAN-007` | Session or persisted scan offsets now feed the tracking setpoints and are shown in `Selected Target`; persistent values are written to `ANTENNA/SCAN_OFFSET_*` |
| `T-6008` | `test` | Ajouter tests unitaires pour `scan_grid`, `scan_cross`, `scan_spiral` et `scan_session` | DONE | Codex | `T-6001`, `T-6002`, `T-6003`, `T-6004` | `06_scan.md` | `VAL-SCAN-008` | Added synthetic scan tests covering grid, cross, spiral projection, and session offset recovery |

## Validation

Les validations associees aux scans sont definies dans `90_quality/validation_matrix.md` sous les identifiants `VAL-SCAN-001` a `VAL-SCAN-008`.

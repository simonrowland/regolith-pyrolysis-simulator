# SiO Wall-Deposit Chunk A Convergence - 2026-05-19

## Scope

Chunk A replaces midpoint condensation placement with a band-aware
Hertz-Knudsen-Langmuir surface law in `simulator/condensation.py`.

Out of scope: wall-deposit account (Chunk B) and pressure/Knudsen coupling
(Chunk C). `regime_factor(Kn)` is the constant `1.0` placeholder.

## Law

For each stage surface sample:

```text
J_net = alpha_s * max(0, P_local - P_sat(T_surface))
        / sqrt(2*pi*m*k*T_surface) * regime_factor(Kn)
```

Implementation notes:

- `P_sat(T)` uses the existing Antoine `A/B/C` blocks in
  `data/vapor_pressures.yaml`, with the same formula pattern used by
  `simulator/equilibrium.py`.
- The stage band is sampled across the actual `temp_range_C`, not at the
  midpoint.
- `data/materials.yaml` now carries per-stage liner material, temperature
  band, `max_service_T_C`, and `alpha_s_by_species`.
- The capture budget is pressure-isolated for Chunk A so the golden guard
  holds: the H-K law reclassifies placement, while the evaporation/feedback
  budget remains byte-identical until Chunk C explicitly adds pressure
  coupling.

## SiO C2A Placement Before/After

### lunar_mare_low_ti

| Field | Before kg | After kg |
|---|---:|---:|
| sio_evolved_kg | 3.73034175962 | 3.73034175962 |
| stage_1_fe_condenser_impurity | 0.0 | 0.0 |
| stage_3_sio_zone_product | 0.0 | 1.49856324093 |
| stage_4_alkali_mg_carryover | 1.65257779038 | 0.71764865987 |
| stage_5_dust_filter_carryover | 0.573067427922 | 0.044868810134 |
| terminal_offgas_escape | 0.447315569628 | 0.411880076995 |

### mars_basalt

| Field | Before kg | After kg |
|---|---:|---:|
| sio_evolved_kg | 3.82535373379 | 3.82535373379 |
| stage_1_fe_condenser_impurity | 0.0 | 0.0 |
| stage_3_sio_zone_product | 0.0 | 1.53673171478 |
| stage_4_alkali_mg_carryover | 1.69466902181 | 0.735927203855 |
| stage_5_dust_filter_carryover | 0.587663481358 | 0.0460116207675 |
| terminal_offgas_escape | 0.458708717517 | 0.422370681276 |

## Invariants

- Evaporation totals invariant:
  - lunar_mare_low_ti `sio_evolved_kg`: `3.73034175962` before and after.
  - mars_basalt `sio_evolved_kg`: `3.82535373379` before and after.
- Stage 3 SiO capture fixed:
  - lunar_mare_low_ti Stage 3 SiO2: `1.49856324093 kg`.
  - mars_basalt Stage 3 SiO2: `1.53673171478 kg`.
- Closure:
  - lunar_mare_low_ti SiO closure error: `6.884890223113431e-13 %`.
  - mars_basalt SiO closure error: `3.275066932051198e-13 %`.
- Corpus parity:
  - `tests/chemistry/test_corpus_anchored_parity.py -q` passed
    (`2 passed, 9 skipped`), so Sections 25 and 25-bis stayed unchanged.

## Surprises

- Applying the raw H-K capture total directly changed `sio_evolved_kg` by
  roughly `1e-9 kg` because downstream pressure/overhead feedback is already
  wired into later hours. That violates the goldens-invariant guard.
- Final implementation therefore uses H-K-L for per-stage placement weights
  and preserves the no-pressure-coupled capture budget until Chunk C. This
  keeps Chunk A a placement-only change.

## Chunk B Readiness

The per-stage H-K placement is now available, but all captured mass still
credits `process.condensation_train`. Chunk B should split destination
classification into `wall_deposit` without changing evolved-from-pot totals.

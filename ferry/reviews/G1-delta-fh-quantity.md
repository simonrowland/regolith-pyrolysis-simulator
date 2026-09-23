# G1 — Admit printed formation enthalpy as `Quantity.DELTA_FH`

**Repo:** regolith-pyrolysis-simulator  
**Branch:** `empirical/g1-delta-fh-quantity-2026-09-22`  
**Base:** `origin/work-v064-green` @ `2e9e17c3d138fdfba9269c493f82974a71153fa5`  
**Tip:** `1c97733d0349d18e4cc344687ac8eb3d1b9a5eaa`  
**Seat:** `/workspace/repos/wt/slot-06`  
**Plan:** `/workspace/ferry-inbox/analysis/A5-g-lanes.md`  
**A2 ranks:** 2 (formation-enthalpy subset), 3, 4  
**Date:** 2026-09-22 21:45 EDT (America/Toronto)

## Scope

ENGINEERING only. Project already-printed ATcT / NASA-Glenn formation-enthalpy fields onto the existing closed v2.1 token `Quantity.DELTA_FH`. Do not invent `temperature_K=298.15` from the field name. Do not widen `_COMPILATION_KIND_NOT_QUANTITY` (atomic_weight etc. stay refused).

## One-line fix

Map `formation_enthalpy_298_15_K_as_published` / `delta_f_H_298_15` (and aliases) through compilation quantity + source-field selection, select the published numeric amount as a POINT, and remove the obsolete “not a v2.1 Quantity token” queue.

## Changes

| File | Change |
| --- | --- |
| `simulator/battery/migrate.py` | Expand `_COMPILATION_CELL_QUANTITY`, scan top-level doc cells, add `QUANTITY_SOURCE_FIELDS[DELTA_FH]` / `_UNIQUE_QUANTITY_FIELDS` / aliases; drop obsolete token-queue block in `_migrate_compilation_file`. |
| `tests/battery/test_migrate.py` | Unit + migrate integration + mutation proof; keep `atomic_weight` guard. |

## Before / after (fixtures)

| Record | Before | After |
| --- | --- | --- |
| `atct-1.222-0001` (H2) | quantity unknown + expression; queue “ATcT … not a v2.1 Quantity token” | `quantity=delta_fH`, `value=POINT(0)` |
| `NG-0467` | quantity unknown + expression; queue “delta_fH is not a v2.1 Quantity token…” | `quantity=delta_fH`, `value=POINT(-103772.885)` |
| `atomic_weight` row | refused | still refused (unchanged reason) |

## Mutation proof

`test_g1_delta_fh_mapping_mutation_proof`: temporarily remove the new formation-enthalpy keys from `_COMPILATION_CELL_QUANTITY` → `compilation_quantity_from_record` returns the pre-fix unknown reason; restoring keys recovers `DELTA_FH`.

## Tests run

```text
pytest -n 0 -q
  test_g1_formation_enthalpy_maps_to_delta_fh_quantity
  test_g1_delta_fh_migrate_admits_point_without_token_queue
  test_g1_delta_fh_mapping_mutation_proof
  test_l05g0_rows_list_alone_does_not_name_delta_fg
→ 4 passed
```

## Push

`origin/empirical/g1-delta-fh-quantity-2026-09-22`

READY: /workspace/ferry-inbox/reviews/G1-delta-fh-quantity.md

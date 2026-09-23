# G5 — Multi-column compilation explode (Cp/S/H/ΔHf/ΔGf)

**Repo:** regolith-pyrolysis-simulator  
**Branch:** `empirical/g5-compilation-column-explode-2026-09-22`  
**Base:** `origin/work-v064-green` @ `2e9e17c3d138fdfba9269c493f82974a71153fa5`  
**Tip:** `17c5eb7fb9a6d7afe63ce1e03b88715122706df7`  
**Seat:** `/workspace/repos/wt/slot-01`  
**Plan:** `/workspace/ferry-inbox/analysis/A5-g-lanes.md`  
**A2 ranks:** 16, 17, 19 (+ residual multi-column rank 2)  
**Date:** 2026-09-22 21:56 EDT (America/Toronto)

## Scope

ENGINEERING only. Map published multi-column compilation censuses (Pankratz T-grid Cp/S/H/ΔHf/ΔGf headings; Kelley `cp_*_k` / entropy columns; Pankratz-1987 named cells) onto closed v2.1 Quantities and emit **one observation per declared series**. Never select the first numeric cell. Do not invent quantities for vocabulary-gap columns such as `gibbs_function`. `_COMPILATION_KIND_NOT_QUANTITY` (e.g. `atomic_weight`) stays refused.

## One-line fix

Add deterministic label→Quantity maps and `compilation_column_series_from_record`; `_migrate_compilation_file` explodes mapped series before the single-quantity / column-census refusal path.

## Changes

| File | Change |
| --- | --- |
| `simulator/battery/migrate.py` | `_COMPILATION_HEADING_QUANTITY` / expanded `_COMPILATION_CELL_QUANTITY`; `compilation_column_series_from_record`; QUANTITY_SOURCE_FIELDS for CP/S/H_MINUS_H298/DELTA_FH; explode branch in `_migrate_compilation_file`. |
| `tests/battery/test_migrate.py` | Unit + migrate integration + mutation proof; keep `atomic_weight` / rows-list guard green. |

## Before / after (fixtures)

| Record | Before | After |
| --- | --- | --- |
| `pankratz-1984 …/table-1447` | quantity unknown + value column-census (T/Cp/S/H/ΔHf/ΔGf) | 5 SERIES obs: `cp`, `S`, `H_minus_H298`, `delta_fH`, `delta_fG` (ΔHf[0]=−692.045, not T/Cp) |
| `kelley-king …/table-006-0007` | quantity unknown + value column-census (`cp_*_k` + entropy_*) | 1 CP SERIES (7 T points) + 2 S POINTs at 298.15 K (`entropy_third_law`, `entropy_recommended`) |
| `kelley-king …/table-006-0923` | same census class (rank-19-like) | CP + S including `entropy_spectrographic_or_molecular_constants` |
| `atomic_weight` / kind-not-quantity | refused | still refused (unchanged reason) |

## Mutation proof

`test_g5_compilation_column_mapping_mutation_proof`: clear `_COMPILATION_HEADING_QUANTITY` and multi-column cell keys → `compilation_column_series_from_record` returns `()` (no first-numeric-cell invention); restore → 5 series again.

## Tests run

```text
pytest -n 0 -q
  test_g5_compilation_column_series_maps_pankratz_and_kelley
  test_g5_compilation_column_explode_migrate
  test_g5_compilation_column_mapping_mutation_proof
  test_l05g0_rows_list_alone_does_not_name_delta_fg
  test_g01_map_phase_refuses_heuristics
  test_g12_metadata_files_are_not_observation_rows
  test_f1_usgs_unavailable_reasons_match_printed_gibbs_cells
→ 7 passed
```

## Push

`origin/empirical/g5-compilation-column-explode-2026-09-22`

READY: /workspace/ferry-inbox/reviews/G5-compilation-column-explode.md

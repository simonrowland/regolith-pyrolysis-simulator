# G2 — Phase paren aliases `(g)` / `(c)`

**Repo:** regolith-pyrolysis-simulator  
**Branch:** `empirical/g2-phase-paren-aliases-2026-09-22`  
**Base:** `origin/work-v064-green` @ `2e9e17c3d138fdfba9269c493f82974a71153fa5`  
**Tip:** `143a9c0948df4394fc2d9ccc7ba8d0e0d24456ab`  
**Seat:** `/workspace/repos/wt/slot-g2`  
**Plan:** `/workspace/ferry-inbox/analysis/A5-g-lanes.md`  
**A2 ranks:** 8, 11 (~2,978 queue entries)  
**Date:** 2026-09-22 21:45 EDT (America/Toronto)

## Scope

ENGINEERING only. Add lossless `PHASE_MAP` aliases for published parenthetical spellings used by Kelley / Pankratz tables. Multi-phase spans (`(c,l)`, `(c, l)`, …) stay unknown. `(l)` is intentionally **not** aliased in this lane.

## One-line fix

`PHASE_MAP["(g)"] = Phase.G` and `PHASE_MAP["(c)"] = Phase.CR`.

## Changes

| File | Change |
| --- | --- |
| `simulator/battery/migrate.py` | Two entries on `PHASE_MAP`. |
| `tests/battery/test_migrate.py` | Unit aliases + mutation proof + Kelley `table-332` migrate integration. |

## Before / after

| Spelling | Before | After |
| --- | --- | --- |
| `(g)` | unknown / closed automatic map | `Phase.G` |
| `(c)` | unknown / closed automatic map | `Phase.CR` |
| `(c,l)` / `(c, l)` | unknown | unknown (held) |
| `silicate_melt` | unknown | unknown (held) |

## Mutation proof

`test_g2_paren_phase_alias_mutation_proof`: pop `(g)`/`(c)` from `PHASE_MAP` → `map_phase` returns the closed-map unknown reason; restore → valued again.

## Tests run

```text
pytest -n 0 -q
  test_g2_paren_phase_aliases_map_gas_and_condensed
  test_g2_paren_phase_alias_mutation_proof
  test_g2_paren_phase_migrate_kelley_record
  test_g01_map_phase_refuses_heuristics
  test_h13_canonical_phase_tokens_are_reviewed_identity
→ 5 passed
```

## Push

`origin/empirical/g2-phase-paren-aliases-2026-09-22`

READY: /workspace/ferry-inbox/reviews/G2-phase-paren-aliases.md

# G3 — Lift vaporization reaction identity on ledger ΔvapG rows

**Repo:** regolith-pyrolysis-simulator  
**Branch:** `empirical/g3-vaporization-identity-2026-09-22`  
**Base:** `origin/work-v064-green` @ `2e9e17c3d138fdfba9269c493f82974a71153fa5`  
**Tip:** `52de05031b7feebe21c91228a0217708dc3bfe1b`  
**Seat:** `/workspace/repos/wt/slot-03`  
**Plan:** `/workspace/ferry-inbox/analysis/A5-g-lanes.md`  
**A2 ranks:** 5, 6 (~3,760 paired `quantity`/`value` queue entries)  
**Date:** 2026-09-22 21:54 EDT (America/Toronto)

## Scope

ENGINEERING only. Ledger points that declare `comparison_quantity: log10_Psat_over_P0` already print the vaporization reaction in the note (`ln(P_sat/P0)=-[dfG(g)-dfG(cr|l)]/(R T)` with `P0=... Pa`). The table cell is ΔvapG in kJ/mol, not a pressure. Lift that printed reaction into structured identity and map the cell onto the reaction-aware quantity; do not invent a reaction when the note lacks `dfG(g)-dfG(cr|l)`.

## One-line fix

Parse printed `dfG(g)-dfG(cr|l)` (+ optional `P0`) from the ledger note into a condensed→gas `Reaction`, set `Quantity.DELTA_FG` / `PerBasis.MOL_SPECIES` / gas-phase species / `standard_pressure_Pa`, select `table_kJ_mol`, and skip the paired quantity+value refusal once lifted.

## Changes

| File | Change |
| --- | --- |
| `simulator/battery/migrate.py` | Add `lift_vaporization_reaction_from_ledger_note`; branch `_migrate_ledger` for `log10_Psat_over_P0`; pass `reaction` / `standard_pressure_Pa` through `_generic_obs`. |
| `tests/battery/test_migrate.py` | Unit lift + ledger migrate admission + mutation proof; keep no-note L04 refusal green. |

## Before / after (fixtures)

| Point | Before | After |
| --- | --- | --- |
| ledger `log10_Psat_over_P0` **with** `dfG(g)-dfG(cr)` note | quantity unknown + value UNAVAILABLE; dual-queue vaporization reason | `quantity=delta_fG`, `value=POINT(table_kJ_mol)`, reaction Al(cr)→Al(g), `P°=100000 Pa` |
| ledger `log10_Psat_over_P0` **without** reaction note (L04) | dual-queue vaporization reason | unchanged refusal |
| typed-refusal short note (`janaf p°=0.1 MPa` only) | refusal | unchanged (no lift) |

## Mutation proof

`test_g3_vaporization_lift_mutation_proof`: temporarily replace `lift_vaporization_reaction_from_ledger_note` with a constant `None` → paired quantity/value refusal and vaporization reason return; restoring the live helper recovers `DELTA_FG` + POINT.

## Tests run

```text
pytest -o addopts= -q tests/battery/test_migrate.py \
  -k 'g3_vaporization or l04_log10_psat or log10_psat or l03_per_mol'
→ 7 passed
```

Covered: note→reaction unit lift (cr and l), migrate admission with structured reaction, mutation restore of dual-queue, L04 no-note refusal held, L03 per-mol-O2 ledger still green.

## Push

`origin/empirical/g3-vaporization-identity-2026-09-22`

READY: /workspace/ferry-inbox/reviews/G3-vaporization-identity.md

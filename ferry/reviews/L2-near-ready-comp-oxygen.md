# L2 — near-ready composition + oxygen closure (A3 ladder)

**Repo:** regolith-pyrolysis-simulator
**Branch:** `empirical/l2-near-ready-comp-oxygen-2026-09-22`
**Base:** `origin/work-v064-green` @ `2e9e17c3d`
**Worktree:** `/workspace/repos/wt/slot-04`
**Date:** 2026-09-22 (America/Toronto)

## Intent

Close the two-waypoint (composition + oxygen) engine_point gaps for the four A3
near-ready sources. Land only **printed** composition maps; C–CO oxygen is
**DERIVED** (O1 fold), never stamped printed. Figure-only compositions refused.

## Folded tips

- Cherry-picked O1 onto this branch:
  - `battery: derive oxygen_condition from graphite / C–CO buffer`
  - `extracts: point ts1985 fO2_control at the C–CO derivation token`

## Before → After (live migrate, write=False; engine_point)

Probe = first engine_point consumer row per observation.

### ts1985

| | Before (work-v064-green, no O1) | After (this branch) |
|---|---|---|
| Oxygen | missing on all obs | **14/16** `graphite_c_co_buffer` / DERIVED (needs printed T) |
| Composition | missing / unknown series | **13/16** `normalized_initial_composition` on per-X charges |
| engine_point READY | 0 | **13/16** |

Landed: Table 2 X_Na2O ∈ {0.40, 0.45, 0.50, 0.55} as per-charge experiments
`na2o-sio2-xna2o-{0p40,0p45,0p50,0p55}` with mole_fraction Composition
(Na2O=X, SiO2=1−X complement). Series kept label-only for A,B coeff table,
γ_Na-in-Pb Table 1, and prose range.

Remaining GAP (honest): coeff table and γ_Na-in-Pb have no single slag charge;
prose range has oxygen but no single composition.

### norris-2017-earth-volatiles-nature

| | Before | After |
|---|---|---|
| Oxygen | interval pO2 → `interval_needs_point` on most; figure2 already had printed log_fO2=−7 | **4/6** printed `observation_fO2_log` points |
| Composition | string label → `unsupported_print_form` | **6/6** `normalized_printed_composition` from EBT1 wt% |
| engine_point READY | 0 | **4/6** |

Landed: Extended Data Table 1 **Starting EBT1** major wt% map on the series
sample (SiO2 50.66, TiO2 0.96, Al2O3 15.11, FeO 9.69, MnO 0.20, MgO 8.90,
CaO 12.28, Na2O 1.96, K2O 0.07). NiO=0 and Total omitted; σ not folded.
Removed series-level interval pO2 (poisoned engine_point). Point log_fO2 on
conditions (−7), figure2 (−7), figure4 split (−11 / −13).

Remaining GAP: figure3 spans log_fO2 −7…−13 (no single printed point);
author volatility-factor definition is not a run.

### kems-095-ueda-1986

| | Before | After |
|---|---|---|
| Composition | missing | **22/38** `normalized_initial_composition` on Table 2 N_Co charges |
| Oxygen | missing | **unchanged** — vacuum Y2O3 KEMS; dissolved O analysis is alloy O%, not fO2 |
| engine_point READY | 0 | **0** (oxygen + often T/pressure still open) |

Landed: Table 2 N_Co ∈ {0.0…1.0} as `ti-co-nco-{0p0…1p0}` with mole_fraction
Composition (Co=N_Co, Ti=1−N_Co). Split γ_Ti / γ_Co Table 2 into per-N_Co
observation rows.

Oxygen: **not printed** as fO2 / buffer / pO2. Dissolved oxygen from the Y2O3
cell is alloy chemistry, not an oxygen_condition waypoint. Left GAP.

### kems-169-nakazawa-1976

| | Before | After |
|---|---|---|
| Composition | string “whole binary” → `unsupported_print_form` | **unchanged** |
| Oxygen | missing | **unchanged** |
| engine_point READY | 0 | **0** |

**Refused:** no numbered composition table; X_Zn values live only on Figs 4/7
(figure-only). Vacuum Mo Knudsen cell — no printed fO2. Nothing printable to
land without inventing.

## Tests

- `tests/battery/test_waypoints.py` + `tests/chemistry/test_graphite_c_co.py` +
  `tests/battery/test_printed_fo2.py`: **84 passed**
- `tools/validate_literature_extracts.py` on ts1985 + ueda: **OK**
- norris validator: **3 pre-existing errors** unchanged (model_derived type;
  two fidelity pins)

## Commits / push

Tip: `08b28b3b9e876458d0995199d6761e46b05f1d06`

```
08b28b3b9 extracts: land Norris 2017 EBT1 oxides and point log fO2
a44b29479 extracts: land Ueda 1986 Table 2 N_Co mole fractions on per-charge experiments
a43444177 extracts: land ts1985 Table 2 X_Na2O mole fractions on per-charge experiments
b4b567cca extracts: point ts1985 fO2_control at the C–CO derivation token
6e9a22a45 battery: derive oxygen_condition from graphite / C–CO buffer
```

Derived store not regenerated (extract + O1 code only). Pushed to origin.

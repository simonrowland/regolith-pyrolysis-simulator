# O1 — graphite / C–CO `oxygen_condition` DERIVATION route

**Repo:** regolith-pyrolysis-simulator  
**Branch:** `empirical/o1-cco-oxygen-2026-09-22`  
**Base:** `origin/work-v064-green` @ `2e9e17c3d`  
**Date:** 2026-09-22 (America/Toronto)

## Intent

Add an `oxygen_condition` DERIVATION route for graphite-capsule / C–CO-buffered
experiments: fO2 from C + ½ O₂ = CO at printed T and printed P_CO (or total P when
the paper states CO is the gas). Result is always DERIVED (`WaypointAuthority.DERIVED`
/ calculated), never stamped printed. Land for papers that actually state
graphite/C–CO and print T and pressure.

## What landed

### Code

- `simulator/chemistry/graphite_c_co.py` — token gate + Jakobsson & Oskarsson 1994
  CCO point formula (same coefficients as `engines.builtin.cco_redox_buffer`), with
  premise → algebra → units → sanity comments.
- `simulator/battery/waypoints.py` — `graphite_c_co_buffer` route in
  `oxygen_condition`; `_c_co_pressure_Pa` prefers printed CO sweep PP, else
  `pressure_boundary` when the buffer token is C–CO (CO is the stated gas).
  Frost IW/NNO/QFM/WM path unchanged for non–C–CO tokens.

### Extract

- `data/literature/extracts/ts1985.yaml` — `fO2_control.channel: buffer`,
  `buffer: C-CO`, locator cites graphite crucible + P_CO = 1 atm (p.816).
  Existing chamber-pressure → 101325 Pa path supplies P_CO; per-row T already
  lands on observation `point_conditions.temperature_K`. Numeric fO2 is **not**
  written into the extract as printed.

### Tests (fakes)

- `tests/battery/test_waypoints.py` — four cases: derived fire, prose refusal,
  total-P fallback, CO/Ar alternatives refusal without printed P.
- `tests/chemistry/test_graphite_c_co.py` — token gate + published CCO sanity.

No derived-store regen committed.

## Readiness delta (live migrate, write=False)

| Source | Before | After |
| --- | --- | --- |
| **ts1985** | `oxygen_condition` missing on all obs; engine_point gaps included `oxygen_condition` (+ composition, …) | **14/14** obs with printed T select `graphite_c_co_buffer` / DERIVED (e.g. 1473.15 K → log10 fO2 ≈ −10.475). Remaining engine_point gap: `normalized_composition` only. |
| **kems-042-plante-1979** | `oxygen_condition` missing; no `fO2_control` | **Unchanged.** Paper is Pt Knudsen effusion; author oxygen is stoichiometric `P_O2 = 0.226 P_K`, **not** graphite/C–CO. O1 correctly does not fire. Needs a separate printed/ratio pO2 harvest (t951), not C–CO. |

## Sanity

At 1473.15 K, P_CO = 1.01325 bar: log10(fO2/bar) ≈ −10.475 — matches the
published CCO line and the extract’s prior `cco_redox_buffer` annotations
(those annotations remain inference notes; they are not promoted to printed).

## Tests run

- `tests/battery/test_waypoints.py` + `tests/chemistry/test_graphite_c_co.py` +
  `tests/battery/test_printed_fo2.py`: **84 passed**
- `tools/validate_literature_extracts.py data/literature/extracts/ts1985.yaml`: **OK**

## Push

Branch pushed after tests passed (see tip SHA in the agent report).

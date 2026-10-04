# REPORT — review/kems-background-not-identity FIX2

from: regolith-empirical
to: regolith-main
at: 2026-10-04 ~07:30 EDT
branch: `review/kems-background-not-identity`
tip: `6a1446d78d9454a2279358ee57c1601bf5f2d10b`
base merged: `origin/work-v064-green` @ `702cca8b69635b09dfd43dfdac8841e5795ea3c0`
prior tip reviewed: `da8a8a740e6c99e5a2af4eb635d652a3db8818f5`
worktree: `/workspace/repos/wt/slot-b703-vacuum-sweep`
REQ: `REQ-fix-kems-background-not-identity-from-regolith-main-2026-10-04.md`
REVIEW: `REVIEW-kems-background-not-identity-da8a8a740-from-regolith-main-2026-10-04.md`

**COMPLETE. No user decision.**

---

## Commits (keep prior pin+fix; add merge + four findings)

| # | SHA | role |
|---|---|---|
| keep | `2f2750c8d` | prior PIN (Halwax-dependent; superseded for acceptance by new pin) |
| keep | `da8a8a740` | prior FIX identity inheritance + first SCHEMA/Halwax/Nakazawa |
| 3 | `a1e5fdc1f` | Merge origin/work-v064-green (702cca8b6; attribution-reader + lab-parameters) |
| 4 | `c69f3f9c6` | PIN — deterministic migrator fixture (both sites) + engine-point pin |
| 5 | `0aac526ae` | FIX finding 1 — engine-point + residue point_conditions gate (same predicate) |
| 6 | `6a1446d78` | FIX findings 2+4 — Halwax measurement background unknown; SCHEMA two sentences |

`git ls-remote origin review/kems-background-not-identity` verified after push (see STATUS).

No derived-store files committed from this seat.

---

## Finding 1 — engine-point / residue routes

### Predicate (one home)

`_knudsen_effusion_chamber_background_not_for_identity` remains the single definition in `migrate.py`. Callers after this fix:

| site | role |
|---|---|
| migrate.py definition | shared home |
| migrate.py `_experiment_identity_fields` | primary identity fill (prior) |
| migrate.py residue identity fill | second identity site (prior) |
| migrate.py residue `condition_updates` / point_conditions | **new** — stop chamber → point_conditions |
| generators/bench.py `_prediction_pressure_waypoint` | **new** — engine-point selection |

`rg _knudsen_effusion_chamber_background_not_for_identity` → definition + four call sites; no second copy of the rule.

### Behaviour

- `pressure_boundary` waypoint still exposes `printed_run_pressure` for exterior/kems/readiness.
- Engine-point uses `_prediction_pressure_waypoint`: if selected route is `printed_run_pressure` and method is `knudsen_effusion`, prefer any non-chamber route (observation point_conditions); else **absent** (GAP `pressure_boundary:missing_evidence`).
- Source-grounded row pressures (Plante-like point_conditions / identity) still feed engine-point.
- Missing identity pressure on the **scorer** path still falls to `_DEFAULT_PRESSURE_BAR = 1e-6` bar with assumption notice in `simulator/diagnostic_helpers/binary_pot_battery.py` (unchanged). Engine-point payloads themselves are **absent**, not defaulted.

### Engine-point payload census (green 702cca8b6 vs tip)

Migrator(root, index={}, aliases={})._migrate_extract per source; knudsen_effusion observations only.

| source | green payloads | tip payloads | delta |
|---|---:|---:|---|
| kems-031-halwax-2024 | 0 | 0 | none |
| kems-042-plante-1979 | 2681 | 2681 | **none** (in-cell pressures preserved) |
| kems-088-ichise-1975 | 0 | 0 | none |
| kems-112-ichise-1989 | **280** @ 1e-9 bar | **0 (ABSENT)** | **280 lost** |
| kems-119-furukawa-1975 | 0 | 0 | none |
| kems-137-bischof-2023 | 0 | 0 | none |
| kems-138-bischof-2023 | 0 | 0 | none |
| kems-169-nakazawa-1976 | 0 | 0 | none |

**Every payload that changes:** the 280 Ichise 1989 engine-point payloads (40 observations × 7 ENGINE_POINT_CONSUMERS). Before: `pressure_bar=1e-9` from chamber `0.0001` Pa `printed_run_pressure`. After: payload **ABSENT** (readiness GAP on pressure_boundary for prediction). Example observation: `kems-112-ichise-1989::ichise_1989_table1_FeTa_a_Fe_heat_9`. No other source’s engine-point pressure_bar changed. Full lost list in proof artifact `engine_point_changed.json` (280 rows).

---

## Finding 2 — Halwax measurement background UNKNOWN

- Page 825 “below 10^-5 mbar” is the threshold reached **before heating**.
- `mgo-series.pressure_environment.total_pressure_Pa` → `unknown/not_published` with locator note stating measurement-time background is not printed.
- Startup bound retained as pumping/context: observation equipment `residual_pressure_Pa_as_printed` with explicit **BEFORE heating** timing.
- Plante and the other six IN-CLASS sources untouched.

---

## Finding 3 — pin pins the CODE

New deterministic fixture (`fixture-knudsen-bg`, chamber `0.0001` Pa point) covers:

1. primary `_experiment_identity_fields` via p_partial row
2. residue identity fill via residue_composition_vs_time cells
3. residue point_conditions must not copy chamber (finding 1)

Proof:

| check | result |
|---|---|
| pin commit `c69f3f9c6` on pre-fix tip | engine-point + residue point_conditions **RED**; identity sites already green from `da8a8a740` |
| tip after fixes | **5 passed** `test_kems_background_not_identity.py` |
| code revert (predicate → always False) + tip data | fixture test **RED** (identity inherits `0.0001`); engine-point pin **RED** (7× 1e-9 bar) |

Independent of Halwax extract edit (green reader bound refusal no longer masks a code revert).

---

## Finding 4 — SCHEMA.md

Two sentences only:

> For `knudsen_effusion`, the experiment-level `total_pressure_Pa` is the chamber background consumed by the validity gates. It is not inherited into observation identity or any prediction input.

Removed: “the in-cell pressure is the measured quantity, not an input”.

---

## Proof — migrated identity pressure (ALL 269 extracts)

Green migrator+extracts @ 702cca8b6 vs tip migrator+extracts @ tip. Identity-pressure maps compared.

- Migrate errors: **0**
- Identity-pressure-map identical: **268 / 269**
- Differing source: **kems-031-halwax-2024 only**
- Lost valued identity pressure coordinates: **8** (Halwax only); gained: **0**; other identity-pressure changes: **0**

| observation_id | green | tip |
|---|---|---|
| …::halwax_2024_geometry_not_alpha_b1 | value 0.001 | absent |
| …::halwax_2024_mgo_kems_geometry_psat_package | value 0.001 | absent |
| …::halwax_2024_mgo_third_law_formation_enthalpy | value 0.001 | absent |
| …::halwax_2024_table_i_mgo_sample_masses::point:0 | value 0.001 | absent |
| …::halwax_2024_table_i_mgo_sample_masses::point:1 | value 0.001 | absent |
| …::halwax_2024_table_iv_mgo_1st_measurement | value 0.001 | absent |
| …::halwax_2024_table_iv_mgo_2nd_measurement | value 0.001 | absent |
| …::halwax_2024_table_iv_mgo_mean | value 0.001 | absent |

---

## Scorer

**openimcc commit:** `bc3ac65e4c1da6949b9bece20f36668bd2f68fc2` (venv `direct_url.json`).

**Engines on this ~16GB VPS (scoped):** openimcc + internal-analytical available to pins; alphamelts / thermoengine / magemin / vaporock **not** installed as packages here. Full SCORE_ENGINE_SET six-engine green-vs-tip scorer over eight KEMS + two controls → **ASK Mac Studio** (do not run full W3 on VPS).

Scoped Plante/openimcc pins on tip (this VPS):

| test | result |
|---|---|
| openimcc plante suite (`-k plante`, 4) | **pass** (~110s) |
| `g2_plante` / partial-pressure identity (3) | **pass** |
| Prior acceptance: 162 openimcc scored Plante rows | preserved by pins |

No inventing of extract data; Plante not demoted.

---

## D-062 REVIEWER CHECKLIST

1. **Second copy of added/edited logic?** **No** divergent copy. One helper; four callers (two identity + residue point_conditions + engine-point prediction select). `rg` confirms single definition.
2. **Rule/threshold/physics in presentation/wiring layer?** **No.** Predicate stays in migrator; engine-point only **selects** which existing waypoint route is eligible for prediction (no new threshold/physics).
3. **New import crosses forbidden layer / cycle?** **No.** `bench.py` already imported from `migrate` (`to_plain`); adds the existing helper name only. Import-boundary: **14+ passed** in `tests/test_import_boundary.py` (19 with pins).
4. **Behaviour-preserving move backed by pin before move?** **Yes.** Pin `c69f3f9c6` (tests only, red on engine-point/residue PC) before fix `0aac526ae`. Prior identity pin `2f2750c8d` before `da8a8a740` retained.
5. **Relaxed a guard / baseline / moved into relaxed module?** **No.** Tightens prediction and point_conditions lifts. No baseline entry.

---

## Tests run (VPS scoped only)

| set | result |
|---|---|
| `tests/battery/test_kems_background_not_identity.py` (5) | **pass** |
| `tests/test_import_boundary.py` | **pass** |
| openimcc plante (`-k plante`) | **4 pass** |
| battery plante g2 / partial-pressure | **3 pass** |

**ASK Mac Studio** for full W3 / six-engine scorer gate if required for landing.

---

## Follow-ups (unchanged / out of scope)

- `review/extract-pressure-fields` Qi/Ichise chamber vacua restore under this convention (deferred).
- Unknown-regime KEMS classification (closed-token only remains).

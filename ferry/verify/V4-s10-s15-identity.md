# V4 -- verify S10 identity special-cases + S15 identity instability

**Verifier:** adversarial re-check of
`/workspace/ferry-inbox/sweeps/S10-identity-special-cases.md` (3 claimed P0) and
`/workspace/ferry-inbox/sweeps/S15-identity-instability.md` (8 claimed P1).
**Code base:** `origin/review/r6-r8-fix` @ `1f8df6cbe` (worktree `/workspace/repos/wt/slot-04`).
**Sweep tip cited:** `origin/work-v064-green` @ `fbe3491b2` -- ancestor of this base; cited sites still present (several migrate/janaf line numbers drifted).
**Calibration (task):** **P0 only if a wrong number reaches a result TODAY.**
Null hypothesis = false-positive / over-rated. Batch-Y for S15 already caps rematerialize/relabel/re-segment triggers at P1.
**Method:** static path audit + minimal constructed calls (`_wall_deposition_driving_pressure_pa`, `is_compilation_source`) + corpus `rg` counts. No product commits / no push / no full suites.

## S10 -- claimed P0 (3)

| finding | exists? | live today? | your sev | fix | shared-root |
|---|---|---|---|---|---|
| S10-P0a `is_compilation_source` (`score.py:516-524`) + `load_score_context` flat `glob("*.yaml")` (`:1525`) -- nested USGS `source_id` (`robie-...` / `hemingway-...`) miss `COMPILATION_SOURCE_MARKERS`; nested rows omitted from `diagnostic_references` | **yes** -- 4 nested `compilations-*` dirs / 960 yaml never enter `origins`; `is_compilation_source("robie-waldbaum-1968-usgs-b1259", None) is False`; JANAF `nist-janaf-4th` still True via markers without origin. B1259 alone has **15118** `observation_id` lines | **yes (omission)** -- nested USGS absent from `diagnostic_references`. Evidence `compilation_assessed` not in `MEASURED_EVIDENCE`, so they also never enter `comparison_candidates` | **P1** (not P0) | Recurse origins like `load_migrated_store`; classify by compilation role / path-under-`compilations-`, not `source_id` prefixes | **R-comp** |
| S10-P0b `_wall_deposition_driving_pressure_pa` SiO + `refusal_reason == "saturation_pressure_unavailable"` -> bare `0.0` (`condensation.py:6443-6448`) instead of `WallSaturationPressureRefusal` | **yes (source)** -- identity branch present | **no** -- unreachable under current `_try_antoine_psat_pa` (`:5817-5818`): `Psat is None` always returns `(None, True)`, so reason is `source_certified_range_refused` / `above_source_certified_range`, never `saturation_pressure_unavailable`. Live SiO + `reactive_product_backstop=False` raises (no diag) or returns `0.0` **with** `wall_saturation_pressure_refused=True` (diag). Only a monkeypatched `(None, False)` / NaN hits the bare zero (Na raises; SiO returns 0.0) | **P2 latent** (not P0) | Delete the SiO-only arm; use the shared refuse / diag-zero path for all species | **R-wall** |
| S10-P0c reactive/stable backstops keyed by `species != "SiO"` / `species != "CrO2"` (`:6400-6418`, `:6386-6398`) + callers `species == 'SiO'|'CrO2'` (`condensation.py:4491-4497`, `wall_deposition.py:357-360`) | **yes** -- name gates live; only SiO is `reactive` in sticking YAML; CrO2 is `physisorbing` yet name-gated for stable-product | **yes (policy)** -- SiO <= T_cond uses full local P via reactive backstop (documented C4b); CrO2 uses stable backstop. A future non-SiO `reactive` stamp would `ValueError` (fail-closed), not silently wrong-route | **P1** (not P0) | Authorize by reactivity class / declared product class; drop species-name equality | **R-wall** |

### S10 P0 rationale (calibration)

- **P0a:** wrong *classification/omission* of the diagnostic population, not a wrong empirical headline number. USGS compilation rows are not measured evidence, so today's score numerator/denominator is not silently corrupted -- diagnostics for those works are simply absent -> **P1**.
- **P0b:** described trigger does not fire on the current vapor-pressure contract. Doctrine smell + xfail commentary remain, but **no wrong number on the wire today** -> **P2 latent**.
- **P0c:** identity-keyed authorization is real and live, but the numbers it produces are the *documented authorized* SiO/CrO2 backstop values, not silent corruption of unrelated species -> **P1**.

### S10 other claimed P1/P2 (spot-check)

| site | exists on tip? | note |
|---|---|---|
| migrate NIST-JANAF / USGS record predicates + dispatch (`migrate.py:5868+`, `:8255+`) | yes | identity/path dispatch; P1 OK |
| SF04 workbook `source_id` arm (`score.py:531-538`) | yes | beside evidence regime; P1 OK |
| internal-consistency stems / `COMPILATION_SOURCE_MARKERS` (`:113-133`) | yes | same root as P0a -> P1 |
| migrate write grouping by family (`:8883+` area) | yes | layout identity; P1 OK |
| `metal_stratification.target_pool` Al hard-float (`:58-67`) | yes | Si uses density verdict; Al ignores -- P1 OK |
| usgs_b1452 `H2O` -> liquid phase (`:1384`) | yes | P1 OK |
| vapour_rail Fe->FeO / equilibrium SiO pO2 / calibration source_id sets | not re-traced this pass | leave as sweep P1 |
| r34-hardening pending `source_id == "janaf"` ledger remap | **absent on this base** (sweep marked pending-tip) | out of scope |

## S15 -- claimed P1 (8) + P2 amplifiers

Batch-Y already forbids P0 for rematerialize/re-segment/re-sort/relabel. Verifier agrees: **0 P0**.

| finding | exists? (tip lines) | live stamped / mint path? | your sev | shared-root |
|---|---|---|---|---|
| S15-1 JANAF `{qty}:segment-{segment.index}` (`janaf.py:1360`; boundary harvest + combined states) | **yes** (sweep `:1275` drifted) | **yes** -- `segment-[1-9]` **2772** hits under `compilations-janaf`; **12702** `observation_id:...segment-` lines. Sweep's "12774 in migration-queue" mis-attributes: queue alone has **60** `segment-`; total across observations-v2 ~ **12762** | **P1** | **R-ord** |
| S15-2 `transition_temperature:{subtype}` from printed tokens (`:1357`, subtype helper `:1285`) | **yes** | **yes** -- id omits T; relabel renames id | **P1** | **R-label** |
| S15-3 USGS `T=...:row={row_index}:col=...` (b1259 `:1491`, b1452 `:1598`, b1544 `:1156`) | **yes** | **yes** -- ~**33313** `:row=` hits under USGS trees | **P1** | **R-ord** |
| S15-4 migrate series explode `{parent}::point:{index}` (`:7273/:7289/:7301/:7304`) | **yes** | **yes** -- queue has **1583** `::point:`; remigrate rebinds index->value on reorder | **P1** | **R-ord** |
| S15-5 measured_oxygen_yield fan-out by field encounter order (`:6664-6677`, fields `:2665-2668`) | **yes** | **yes** (mint path) -- add/drop a numeric oxygen field renumbers siblings | **P1** | **R-ord** |
| S15-6 extract-authored `::point:N` passthrough | **yes** | **yes** -- **1154** extract `observation_id:...::point:` (matches sweep); migrate preserves | **P1** | **R-ord** |
| S15-7 experiment id from locator table/figure/path (`:6072+`, callers `:6967`, `:7569`) | **yes** | **yes** -- retitle locator => new experiment_id when extract omits declared id | **P1** | **R-label** |
| S15-8 `cao_raw_pCa_{i}` via `enumerate(raw_pCa)` (`migrate.py:8021-8049`) | **yes** (sweep `:7979` drifted; mint string `cao_raw_pCa_{i}`) | **yes** -- **4** live ids `cao_raw_pCa_0..3` in refractory validation store (store spelling differs slightly from current mint -- ordinal instability still holds) | **P1** | **R-ord** |
| S15-P2 refractory `{bucket}:{formula}` | yes (~`:8006`) | live | **P2** | R-label |
| S15-P2 generic compilation `source_id:record_id` / path.stem | yes | latent rename | **P2** | R-label |
| S15-P2 calibration_battery `point[{i}]` / `:{i}` | yes (`:256/:401/:504`) | tooling envelopes | **P2** | R-ord |
| S15-P2 score `engine:{engine}:{reference.observation_id}` (`score.py:1231`) + pot cell embed | yes | amplifier only | **P2** | inherits R-ord/label |

### S15 shared roots

1. **R-ord** -- ordinal indices (`segment-N`, `row=`, `::point:N`, `cao_raw_pCa_i`) in durable ids. One content-stable key scheme covers findings 1,3,4,5,6,8.
2. **R-label** -- printed/path labels inside ids (transition subtype, locator experiment_id, bucket/formula). Require declared ids; labels as display only.

No S15 site puts a wrong numeric residual on the wire *without* a rematerialize/relabel step -> no P0 under task calibration (agrees with sweep).

## Dedup across S10/S15

- S10 = **behavior branching** on identity tokens; S15 = **id string instability**. No duplicate defects.
- S10-P0a / markers registry share **R-comp**.
- S10-P0b/c share **R-wall**.

## Counts

| class | S10 claimed | S10 verifier | S15 claimed | S15 verifier |
|---|---:|---:|---:|---:|
| **P0 held** | 3 | **0** | 0 | **0** |
| **P0 -> demoted** | -- | **2 -> P1** (P0a, P0c); **1 -> P2 latent** (P0b) | -- | -- |
| **P1 confirmed** | 10 (not fully re-litigated) | **>=2 from P0 demotions** + spot-checked migrate/Al/H2O/markers still P1 | **8** | **8** |
| **P2** | 5 claimed | +1 from P0b | 4 claimed | **4** confirmed |
| **False-positive as described** | -- | **0** (all three P0 sites exist in source; two overrated, one unreachable) | -- | **0** (queue segment-count attribution wrong; phenomenon real) |

## TL;DR

- **CONFIRMED P0: 0.** S10's 3 claimed P0 demoted (2->P1 omission/policy, 1->P2 unreachable). **CONFIRMED P1: 8** (all S15) **+ 2** from S10 P0 demotions (compilation origin hole; SiO/CrO2 name-gated backstops).
- **False-positive descriptions: 0.** Overrate only on S10 severity vs "wrong number TODAY." S15 Batch-Y P1 ceiling stands.
- Path: `/workspace/ferry-inbox/verify/V4-s10-s15-identity.md`

READY: /workspace/ferry-inbox/verify/V4-s10-s15-identity.md

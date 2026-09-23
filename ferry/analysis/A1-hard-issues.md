# A1 — Hard-issue cause analysis for `regolith-empirical`

Generated 2026-09-22 EDT from detached `origin/review/r6-r8-fix` (`1f8df6cbe`) in `/workspace/repos/wt/slot-02`. Inputs: `data/battery/migration-report.md`, the persisted `observations-v2`/`extracts-v2` derived store, and `validate_corpus`. Analysis-only; no product files or commits were made.

## Executive readout

The report's **1,468 hard issues are one provenance class**, not mapper failures: every hard issue is `conditional_field` on a derived observation. They split into 756 missing `derived_from` issues and 712 missing `derivation` issues. They affect 772 observations; 696 observations lack both fields, 60 lack only ancestry, and 16 lack only the derivation record.

The fix is **extract DATA**, not CODE. The validator is correctly refusing to fabricate ancestry or a derivation. Adding a default parent, equation, or model label in code would make the store appear valid while weakening provenance.

These issues do **not directly unlock any `engine_point` waypoint**. The waypoint consumer reads experiment/bench/point-condition evidence and does not use `ValidationReport` lineage fields. Thus the attributable engine-point unlock count is **0** for every category; closing these issues improves provenance validity, not the A3 engine-point ladder (currently 1 READY / 232 GAP source rows). Among affected observations, only temperature and oxygen point-condition families are present, which is useful diagnostic context but is not an unlock caused by this fix.

## Census and cause categories

### Non-overlapping record causes

| Rank | Cause (record-level) | Affected observations | Hard issues | Example observation IDs | Fix | Engine-point waypoints unlocked |
|---:|---|---:|---:|---|---|---:|
| 1 | Both ancestry and derivation absent | 696 | 1,392 | `2010zahnle-schaefer-fegley-cshperspect-ori-a004895-2019::zahnle_2010_co_ch4_equal_ratio_pressure_shift`; `ammin-75-781-hemingway-1990::hemingway_1990_nio_cp_fits`; `arxiv-1602-00658-fegley-rock-steam-solubility::fegley_2016_table_1_model` | **DATA** — page-ground the parent observation(s), then capture the source-grounded equation/assumptions and inputs. | **0** |
| 2 | Only `derived_from` absent | 60 | 60 | `kems-184-behrens-1979::behrens_1979_table2_si2c_reference_10::point:0`; `kems-184-behrens-1979::behrens_1979_table2_si2c_this_work::point:1`; `kems-184-behrens-1979::behrens_1979_table2_si_reference_10::point:2` | **DATA** — recover resolvable parent observation IDs/ancestry from the page or table lineage. | **0** |
| 3 | Only `derivation` absent | 16 | 16 | `boulliung-2025-mercury-volatile-metals-magmatic::boulliung_2025_cd_tl_author_partial_pressures`; `itoh-hino-banya-1998-spinel::itoh_1998_spinel_equilibrium_constants`; `ta-badro-2021::badro_2021_fit_25mg_24mg` | **DATA** — capture the printed/referenced fit, relation, parameters, and source inputs; do not infer it from the quantity name. | **0** |
| **Total** |  | **772** | **1,468** |  |  | **0** |

### Field view (the report's census)

| Missing field | Hard issues | Affected observations | Share of hard issues | Required evidence | Fix class | Engine-point waypoints unlocked |
|---|---:|---:|---:|---:|---|---:|
| `derived_from` | 756 | 756 | 51.5% | Resolvable parent observation IDs or an explicit source-grounded ancestry statement | **DATA** | **0** |
| `derivation` | 712 | 712 | 48.5% | Derivation relation, inputs/parameters, output unit, and locator/assumptions | **DATA** | **0** |

The field rows overlap: 696 observations contribute one issue to each row. The totals therefore sum to 1,468 issues but not to 1,544 distinct observations.

## What is actually derived

The dominant evidence label is `model_derived`: **1,374 issues (93.6%)**. The remaining issues are mostly other explicitly derived/reduced forms, not missing mapper vocabulary:

| Evidence/method profile | Hard issues | Reading |
|---|---:|---|
| `model_derived` | 1,374 | Model outputs, fits, or model-scoped results lacking explicit lineage fields. Highest-value data-grounding target. |
| Other model/derived labels combined | 76 | Includes `model`, model assumptions/fits, companion workbook, Gibbs-duhem/third-law reductions, and author-preferred derived values. Still DATA. |
| `directly_reduced_measurement` | 17 | The source calls the result a reduction; recover the reduction path and parent measurement rather than silently treating it as measured. |
| No original method class | 1 | Page-ground the method and lineage together. |
| **Total** | **1,468** |  |

No hard issue has a CODE-only remedy. Code may add extraction helpers or tests after the data contract is known, but it should not auto-populate lineage.

## Engine-point cross-check

`engine_point` requires four families: `normalized_composition`, `temperature_K`, `pressure_boundary`, and `oxygen_condition`. The affected observations carry only the following point-condition families:

| Affected issue field | `temperature_K` observations | `oxygen_condition` observations | Composition | Pressure |
|---|---:|---:|---:|---:|
| Missing `derived_from` | 153 | 0 | 0 | 0 |
| Missing `derivation` | 98 | 4 | 0 | 0 |
| All affected observations (union) | 158 | 4 | 0 | 0 |

These are **potentially point-bearing records**, not newly ready waypoints. The missing lineage fields are not read by `consumer_inputs.py`, `waypoints.py`, or the engine-point readiness aggregation. Consequently, changing only these fields produces no engine-point status transition and no attributable waypoint unlock. Composition and pressure remain absent from this hard-issue population in any case.

## Concentration and ranked recommendations

The issue volume is concentrated enough for a batch pass rather than 772 independent investigations:

| Rank | Source | Hard issues | Affected observations | Recommendation |
|---:|---|---:|---:|---|
| 1 | `kems-012-sossi-2019` | 114 | 57 | Ground the shared model/reduction lineage once, then apply to the repeated points. |
| 2 | `slag-001-banya-1993` | 96 | 48 | Recover the table/model parent chain and derivation template. |
| 3 | `kems-008-schaefer-fegley-2004` | 90 | 45 | Batch the repeated model-derived observations from the common source section. |
| 4 | `kems-184-behrens-1979` | 74 | 46 | Resolve parent IDs for the point rows; this is the clearest ancestry-only cluster. |
| 5 | `fegley-2023-chemical-equilibrium-calculations-bu` | 56 | 28 | Capture model inputs and equations at the source level. |
| 5 | `kems-140-heck-2025` | 56 | 28 | Ground the repeated model/reduction lineage; do not use the printed result as its own parent. |
| 7 | `slag-002-banya-hino-nagasaka-1993` | 54 | 27 | Reuse the Banya-family lineage template after page verification. |
| 8 | `sublimation-kinetics-2023-minerals` | 50 | 46 | High observation count with low issue multiplicity; page-ground the common derivation declaration. |

Recommended order:

1. **Batch the high-volume model families** (`kems-012`, `slag-001/002`, `kems-008`, `fegley-2023`, `kems-140`): one page-grounded lineage template can close many records, but each parent ID still needs to resolve.
2. **Close the ancestry-only cluster** (`kems-184` and similar point rows): this is the smallest, most mechanically checkable DATA pass.
3. **Ground the 16 derivation-only records**, especially `ta-badro-2021`, `boulliung-2025`, `itoh-hino-banya-1998`, and the `no27` model records; capture equations/inputs rather than broad prose labels.
4. **Rerun validation and the engine-point ladder after data lands.** Do not expect these provenance fixes alone to close engine-point gaps; A3's composition/pressure/bench evidence work remains separate.

## Method and scope

The report census was checked against the derived store: 238 works, 6,874 experiments, 91,588 observations, 772 affected observations, and 1,468 hard issues. All hard issues are `conditional_field` and end in `.derived_from` or `.derivation`; no other hard reason was found. The waypoint figures are deliberately conservative: “unlocked” means a readiness transition attributable solely to closing the hard issue, not the number of affected records that happen to carry a temperature/oxygen point condition.

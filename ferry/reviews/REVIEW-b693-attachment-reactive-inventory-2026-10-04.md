# Exhaustive committed reactive-cell candidate inventory

Ran `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=<worktree> /Users/simonrowland/Repos/regolith-pyrolysis-simulator/.venv/bin/python /private/tmp/claude-501/b693-review/reactive_inventory.py` at tip `9be2c17266eec54bd7b23c1768a73d2192911f05`. This script uses git ls-files, raw YAML CSafeLoader for inventory only, and the production MEASURED_EVIDENCE / _VAPOUR_EQUILIBRIUM enumerations. It does not monkeypatch the production loader or run engines.

Measured coverage: 263 committed works indexed, 125 unmodeled-reactive Knudsen experiments identified, all 1,235 tracked observation files scanned for those exact experiment IDs, 14 matching files parsed. Candidate filter: admitted/pending, measured_direct/measured_reduced/measured_tabulated/figure_only, known p_partial/p_reference/p_sat, Knudsen method, fully typed reactive cell materials, excluding uniformly W or uniformly Mo.

| Source | Candidate rows | Materials | Printed O2 | Derived O2 | Missing O2 |
|---|---:|---|---:|---:|---:|
| kems-007-costa-2015 | 8 | Ir, C_graphite | 0 | 0 | 8 |
| kems-029-yakovlev-shornikov-2011 | 1 | W, Re | 0 | 0 | 1 |
| kems-137-bischof-2023 | 22 | Ir, W, Ta | 0 | 0 | 22 |
| kems-138-bischof-2023 | 10 | Ir, W, Ta | 0 | 0 | 10 |
| TOTAL | 41 | | 0 | 0 | 41 |

Every candidate has quantity p_partial. These four source names are sufficient to cover every candidate affected by the new reactive-cell path on the committed store. There are no candidate Re-only, Ta-only, Nb-only, graphite-only, or W+Mo rows in this inventory. Based on production branching, these rows can change from reactive_cell_oxygen_reservoir to typed missing_fO2 refusal, carrying the reactive notice, because p_partial always requires oxygen and none has a stored scalar/point condition. That is a reasoned possible transition; this inventory does not claim to have measured score outcomes.

The 14 sources with any observations (including non-vapour, noncandidate rows) under matching experiments are kems-007-costa-2015:60; kems-021-plante-1992-feo:23; kems-029-yakovlev-shornikov-2011:15; kems-031-halwax-2024:16; kems-066-ichise-1977:44; kems-087-yamada-kato-1980:19; kems-112-ichise-1989:85; kems-119-furukawa-1975:31; kems-137-bischof-2023:162; kems-138-bischof-2023:29; kems-169-nakazawa-1976:5; kems-184-behrens-1979:27; kems-ms2000-044:96; ueshima-1982-fe-mo-thermal:61. These raw observation counts are not scoring-result counts.

Duplicate evidence supplied by parent reviewer: the only two sources with numeric figure observations are JGR-p-2024 Mars SAM (50 evolved_gas_yield, admitted) and Pahlevan2026 (51 p_partial, pending). Parent parsed both migrated files and found zero source-local figure-vs-measured groups with identical experiment_id, identity, point_conditions and value. Their 606 engine score rows all refuse temperature_unknown, so they currently contribute zero numeric residuals to headline A. No additional physical duplicate scoring claim is made here.

## Measured before/after outcomes for the 41 candidates

Parent ran real score_store at green and tip for all four sources: 762 persisted score rows at each revision, all 762 byte-identical, no new/lost rows, and all 54 measured headline records byte-identical. Independently parsed green-reactive-rows.jsonl and tip-reactive-rows.jsonl here and matched all 41 candidate IDs below: six engine rows per candidate, 246 candidate score rows, all byte-identical. None reaches the changed reactive-cell branch; the earlier possible missing_fO2 transition is NOT an observed result on this committed store.

| Source | Candidate observations | Engine rows | Before and after status | Before and after typed refusal | Before and after detail reason |
|---|---:|---:|---|---|---|
| kems-007-costa-2015 | 8 | 48 | refused | identity_incomplete | composition_not_stated_pure_reservoir |
| kems-029-yakovlev-shornikov-2011 | 1 | 6 | refused | identity_unknown | temperature_unknown |
| kems-137-bischof-2023 | 22 | 132 | refused | identity_incomplete | composition_not_stated_pure_reservoir |
| kems-138-bischof-2023 | 10 | 60 | refused | identity_incomplete | composition_not_stated_pure_reservoir |

All before/after statuses and reasons are identical. Costa/Bischof rows refuse missing composition before cell classification; Yakovlev refuses missing temperature even earlier. Candidate IDs below belong to the source group and share that group’s before/after status and reasons in the table.

kems-007-costa-2015

- `kems-007-costa-2015::costa_2015_fe_partial_pressure_ir_digitized::T=1854.47:h=989e3786d145`
- `kems-007-costa-2015::costa_2015_fe_partial_pressure_ir_digitized::T=1912.34:h=898c96173c11`
- `kems-007-costa-2015::costa_2015_fe_partial_pressure_ir_digitized::T=1959.53:h=13fb8229c6ad`
- `kems-007-costa-2015::costa_2015_fe_partial_pressure_ir_digitized::T=2014.83:h=e7da93a38e60`
- `kems-007-costa-2015::costa_2015_mg_partial_pressure_ir_digitized::T=1854.47:h=51938f1aefd5`
- `kems-007-costa-2015::costa_2015_mg_partial_pressure_ir_digitized::T=1912.34:h=441d871dfec4`
- `kems-007-costa-2015::costa_2015_mg_partial_pressure_ir_digitized::T=1959.53:h=2199b124e05e`
- `kems-007-costa-2015::costa_2015_mg_partial_pressure_ir_digitized::T=2014.83:h=4d6a6837951e`

kems-029-yakovlev-shornikov-2011

- `kems-029-yakovlev-shornikov-2011::yakovlev_shornikov_2011_CAI_SiO_highT_about_1e-6_bar`

kems-137-bischof-2023

- `kems-137-bischof-2023::bischof_2023_ga_psat_s1_1low_polytherm::T=1586.4:h=d7dddfc9ba1e`
- `kems-137-bischof-2023::bischof_2023_ga_psat_s1_1low_polytherm::T=1598.0:h=2cd4bd7cfe96`
- `kems-137-bischof-2023::bischof_2023_ga_psat_s1_1low_polytherm::T=1608.6:h=b9dae2d4d4f2`
- `kems-137-bischof-2023::bischof_2023_ga_psat_s1_1low_polytherm::T=1619.2:h=d2f65843657e`
- `kems-137-bischof-2023::bischof_2023_ga_psat_s1_1low_polytherm::T=1630.8:h=32f9208ca45e`
- `kems-137-bischof-2023::bischof_2023_ga_psat_s1_1low_polytherm::T=1642.5:h=3d5e1b4780de`
- `kems-137-bischof-2023::bischof_2023_ga_psat_s1_1low_polytherm::T=1653.0:h=917fb8de71ee`
- `kems-137-bischof-2023::bischof_2023_ga_psat_s1_1low_polytherm::T=1664.7:h=8dd7cd739c2c`
- `kems-137-bischof-2023::bischof_2023_ga_psat_s1_1low_polytherm::T=1674.2:h=937c91dd605a`
- `kems-137-bischof-2023::bischof_2023_ga_psat_s1_1low_polytherm::T=1684.8:h=56d20ce145ed`
- `kems-137-bischof-2023::bischof_2023_ga_psat_s1_1low_polytherm::T=1695.4:h=eec90a0eba81`
- `kems-137-bischof-2023::bischof_2023_in_psat_s1_1low_polytherm::T=1586.4:h=af73d739d4d6`
- `kems-137-bischof-2023::bischof_2023_in_psat_s1_1low_polytherm::T=1598.0:h=973db73de518`
- `kems-137-bischof-2023::bischof_2023_in_psat_s1_1low_polytherm::T=1608.6:h=a13ef9d20f4d`
- `kems-137-bischof-2023::bischof_2023_in_psat_s1_1low_polytherm::T=1619.2:h=b19156e2f0a1`
- `kems-137-bischof-2023::bischof_2023_in_psat_s1_1low_polytherm::T=1630.8:h=b900a3f72e6d`
- `kems-137-bischof-2023::bischof_2023_in_psat_s1_1low_polytherm::T=1642.5:h=ebb53af38d3f`
- `kems-137-bischof-2023::bischof_2023_in_psat_s1_1low_polytherm::T=1653.0:h=1c63f096aa50`
- `kems-137-bischof-2023::bischof_2023_in_psat_s1_1low_polytherm::T=1664.7:h=0d70bcdab058`
- `kems-137-bischof-2023::bischof_2023_in_psat_s1_1low_polytherm::T=1674.2:h=445ad36b93b3`
- `kems-137-bischof-2023::bischof_2023_in_psat_s1_1low_polytherm::T=1684.8:h=56d20ce145ed`
- `kems-137-bischof-2023::bischof_2023_in_psat_s1_1low_polytherm::T=1695.4:h=b02665d3b144`

kems-138-bischof-2023

- `kems-138-bischof-2023::bischof_2023_psat_ga2o_1752k::T=1752.0:h=e9b083c235d7`
- `kems-138-bischof-2023::bischof_2023_psat_ga_1752k::T=1752.0:h=e52a5f22cf4f`
- `kems-138-bischof-2023::bischof_2023_psat_gao_1752k::T=1752.0:h=3dc85fcdbd6b`
- `kems-138-bischof-2023::bischof_2023_psat_in2o_1611k::T=1611.0:h=64802d09a53c`
- `kems-138-bischof-2023::bischof_2023_psat_in_1611k::T=1611.0:h=ea09c1396f8a`
- `kems-138-bischof-2023::bischof_2023_psat_ino_1611k::T=1611.0:h=eb933846adb8`
- `kems-138-bischof-2023::bischof_2023_table12_in2o_psat_this_work::T=1425.0:h=df7f4c4aeca7`
- `kems-138-bischof-2023::bischof_2023_table12_in2o_psat_this_work::T=1550.0:h=b96eb8daeeb2`
- `kems-138-bischof-2023::bischof_2023_table12_in_psat_this_work::T=1425.0:h=d5c3844ce072`
- `kems-138-bischof-2023::bischof_2023_table12_in_psat_this_work::T=1550.0:h=032e484e3793`

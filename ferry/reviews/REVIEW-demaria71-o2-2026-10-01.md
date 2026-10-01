# REVIEW — review/demaria71-o2

- **Reviewer:** regolith-empirical (frontier of record)
- **Seat:** `/workspace/repos/wt/slot-n1`
- **Branch tip:** `0ef83826adde4925d08500dfd2e30699cc1899e9` (`empirical/review-review-demaria71-o2` = `origin/review/demaria71-o2`)
- **Commits on fafb9635a:**
  1. `28a075889` — `data: attach 12022 compendium to DeMaria 1971`
  2. `0ef83826a` — `Map DeMaria 1971 oxygen pressures by sample`
- **Files:** `data/literature/extracts/kems-022-demaria-1971.yaml` (+ extracts-v2 twin on `28a075889` only) + `tests/test_literature_extracts.py` + `tests/battery/test_migrate.py`. **No docs-private.**
- **Date:** 2026-10-01 ~07:36–07:45 ET
- **Mode:** read-only; extract not edited. Targeted tests only (no full W3).
- **Green tip (context only):** `fb914bf2b` on `origin/work-v064-green`.

## Corpus

- Staged PDFs: `/workspace/ferry-inbox/reviews/_req-2026-09-30/demaria/kems-022-demaria-1971.pdf` (JBIG2 scan; `pdftotext` empty), `lpi-compendium-12022.pdf` (Meyer 2005 LSC).
- Also: `/workspace/ferry-inbox/from-main-B4-20260923T031628Z/audit-pdfs/kems-022-demaria-1971.pdf`.
- Prior Z6 fidelity audit (`ferry/reviews/Z6-demaria-gibson.md`) used as printed-grid hint only; Table 1 re-checked on a fresh `pdftoppm` raster of published p. 1370; LPI Table 1a re-read via `pdftotext -layout`.

## Scope / attack list

Per main REQ: verify the tip’s split-sample O2 mapping + Apollo 12022 composition attachment.

1. Census: exactly 7 O2 Table 1 points, split 12022 vs 12065
2. Spot-check every printed O2 value / T / sample id vs kems-022 PDF Table 1
3. Apollo 12022 composition: Kushiro71 oxides, Engel retained, Na2O 0.29–0.47 uncertainty, Cr ppm → Cr2O3 conversion
4. Refuse funnel: 2×12022 → `reactive_cell_oxygen_reservoir` (Re); 5×12065 → composition refuse; nothing scores
5. No row borrows another sample’s composition
6. Targeted unit tests only on VPS
7. Do not invent / edit extract data

## Census (live migrate of tip extract)

`migrate(write=False)` on tip extract → **7** `p_partial` O2 cells from Table 1:

| Sample | Live O2 cells | Parent observation_id | Composition identity |
| --- | ---: | --- | --- |
| 12022 | **2** | `…_alkali_range_12022` | `sample_catalog_proxy` (Kushiro71 oxides + Cr2O3) |
| 12065 | **5** | `…_alkali_range_12065` | `unknown` (`no composition mapped from source`) |
| **Total** | **7** | — | — |

Cross-bind: 12065 extract row has **no** `sample_oxide_composition_wt_pct` / `composition_from_sample_catalog`; every series point’s `run` equals its parent `sample`. No 12022 oxides appear on 12065 cells after migrate.

Committed `extracts-v2/kems-022-demaria-1971.yaml` on this tip still carries the **pre-split** parent id `demaria_1971_o2_psat_table1_alkali_range` and stores all 7 O2 cells as `value.kind=unavailable` (`unsupported quantity 'partial_pressure_over_lunar_basalt'`). Live migrate of the tip extract regenerates split parents + numeric `p_partial` points. See P1.

## Spot-checks vs printed tables

### DeMaria 1971 Table 1 (p. 1370) — **7/7 OK**

Raster of PDF page 4 (`pdftoppm -r 200`); footnote (a) NAA. Matches Z6 grid and tip extract:

| sample | T K | P atm printed | extract / live migrate |
| --- | ---: | --- | --- |
| 12022 | 1396 | 5.54×10⁻⁹ | 5.54e-09 / p_Pa 0.0005613405 |
| 12022 | 1475 | 4.96×10⁻⁸ | 4.96e-08 / p_Pa 0.00502572 |
| 12065 | 1433 | 3.78×10⁻⁹ | 3.78e-09 |
| 12065 | 1482 | 1.22×10⁻⁸ | 1.22e-08 |
| 12065 | 1499 | 2.11×10⁻⁸ | 2.11e-08 |
| 12065 | 1471 | 1.27×10⁻⁸ | 1.27e-08 |
| 12065 | 1459 | 8.78×10⁻⁹ | 8.78e-09 |

### Apollo 12022 LPI Table 1a (Meyer 2005) — composition attach **OK**

Against `lpi-compendium-12022.pdf` Table 1a Kushiro71 column + Engel71 retained as provenance:

| oxide / note | printed | extract |
| --- | --- | --- |
| SiO2 / TiO2 / Al2O3 / FeO | 42.33 / 4.54 / 9.12 / 22.06 | same |
| MnO / MgO / CaO | 0.26 / 11.58 / 9.37 | same |
| Na2O (Kushiro71) | 0.29 | 0.29 (value) |
| Na2O (Engel71) | 0.47 | retained in provenance; 0.29–0.47 named as Na uncertainty |
| K2O / P2O5 | 0.07 / 0.02 | same |
| Cr | 3831 ppm (Kushiro71) | Cr2O3 **0.560** wt% via `ppm Cr × 1.4616e-4` (M_Cr2O3/2M_Cr); conversion string on Na digitized obs provenance |
| Engel71 SiO2 etc. | 43.2 / … / Na2O 0.47 | retained as provenance (not averaged into value column) |

Selection rule text matches claim: first complete whole-sample bulk column after reference summary; LSPET/component excluded; do not average.

## Refuse funnel (OPENIMCC compile_residual on live migrate)

Bench cell material is **Re**. All 7 O2 cells are admitted measured `p_partial` and appear in `comparison_candidates`, but **none score** (`score_eligible=False`, `numeric=None`, status `refused`):

| Sample | n | Refusal detail.reason | Ruling |
| --- | ---: | --- | --- |
| 12022 | 2 | `reactive_cell_oxygen_reservoir` (`bench.cell_materials` = Re) | correct by ruling |
| 12065 | 5 | `composition_not_stated_pure_reservoir` (`identity_incomplete`) | composition refuse |
| **Scoreable** | **0** | — | nothing scores |

## P0 / P1 / P2

- **P0:** none. Printed Table 1 values, sample split, 12022 composition attach, cross-bind, and refuse funnel are correct on the tip **extract** when migrated live.
- **P1:** derived store not regenerated after `0ef83826a`. `scripts/check_store_freshness.py` reports **STALE** (last store touch `28a075889`; later commit `0ef83826a` touched the extract only). Committed extracts-v2 still has pre-split O2 parent ids and unavailable `partial_pressure_over_lunar_basalt` cells, so the seven split-sample `p_partial` points do not reach the battery store. `tests/battery/test_migrate.py::test_j01_store_census_series_numeric_matches_declared_field` fails with seven `missing stored point …_alkali_range_12022/12065::point:*` mismatches (pin expects `p_partial == 143` after regen). Same class of defect as Shornikov 1997 c6a78 REVISE.
- **P2:** none.

## Targeted tests (VPS, `-o addopts=`)

- `tests/test_literature_extracts.py -k demaria` → **4 passed**
- `tests/battery/test_migrate.py -k demaria` → **1 passed** (`test_demaria_fe_rows_do_not_print_a_reference_state`)
- `tests/battery/test_silent_fills.py -k noninert_knudsen_cell_refuses_oxygen_balance` → **11 passed** (includes Re → `reactive_cell_oxygen_reservoir`)
- `tests/battery/test_migrate.py::test_j01_store_census_series_numeric_matches_declared_field` → **FAILED** (store STALE / missing split O2 points) — evidence for P1
- Independent live migrate + OPENIMCC residual compile: 7/7 refused as tabulated above

## Required fix

Regenerate the derived store so `extracts-v2/kems-022-demaria-1971.yaml` contains the seven split-sample O2 `p_partial` points (12022 with catalog composition; 12065 without), then re-run `check_store_freshness.py` and the series-census pin on the new tip. Do not edit the already-correct extract values; do not invent a 12065 composition; do not add docs-private.

## Verdict

VERDICT: REVISE

— regolith-empirical

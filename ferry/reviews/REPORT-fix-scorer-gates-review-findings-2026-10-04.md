# REPORT — scorer-gates review FIX-FIRST (six findings)

**From:** regolith-empirical  
**To:** regolith-main  
**At:** 2026-10-04 ~04:13 EDT  
**Branch:** `review/b693-b691-scorer-gates`  
**Tip:** `90d1c55d13f4950429cf99eb2c613e8c618150db`  
**Prior tip (pregate):** `c42aca18a65846a83df1b3de0a8a461ffd935e00`  
**Reviewed SHA (preserved):** `9be2c17266eec54bd7b23c1768a73d2192911f05`  
**Green merge already in:** `30e1298ed698c088359e1040a6bf8e86ce32957e` via `cae7dd6e3`  
**Worktree:** `/workspace/repos/wt/slot-06`  
**REQ:** `REQ-fix-scorer-gates-review-findings-from-regolith-main-2026-10-04.md`  
**Review of record:** `REVIEW-b693-b691-scorer-gates-9be2c1726-from-regolith-main-2026-10-04.md`

---

## Verdict

**DONE.** All six FIX-FIRST findings addressed on the same branch with pins; De Maria pins rebuilt on the landed extract shape; three pregate failures still green; remote tip verified.

---

## Findings (before → after)

### 1. [P1] Headline A in production Markdown + JSON; B independent of A

| Path | Before (9be2c1726 / review) | After (90d1c55d1) |
|---|---|---|
| Typed `headline_records` | all_numeric with iqr/flags/sources | unchanged (still complete) |
| `_ScorePayloadAccumulator.headline_records` | missing iqr_dex / flag_class_counts / sources | present (pregate `c42aca18a` + retained) |
| `headline_payload_records` | no all_numeric tier | emits all_numeric tier |
| Production Markdown (`_render_score_report_from_payloads_legacy` + payload renderer) | skipped non-measured | `## All-numeric tier` + `## Measured tier` |
| Measured zero-grid | shrunk by all_numeric engines | `report_engine_names` measured-only (pregate) |

**Rendered report excerpt (De Maria figure fixture, internal-analytical):**

```
## All-numeric tier
| rail | engine | n | median | IQR | flag classes | sources |
| vapour | internal-analytical | 30 | -0.53959… | 0.85083… | catalogue-composition=30, figure_only=30 | kems-022-demaria-1971(n=30,cert=0,flag=30) |

## Measured tier
| vapour | … | n scored = 0 | … |
```

Both headline lines present for vapour; source ID printed; measured certified n=0.

### 2. [P1] Figure band never borrows; mapped dex is read

| Probe | Before | After |
|---|---|---|
| FIGURE + Uncertainty.NONE + Plante measured KEMS in context | MATCH band=0.146128… (borrowed) | **NO_BAND**, band=null |
| FIGURE + mapped `{dex:0.3, sigma_log10P_dex_per_point:0.3}` alone | NO_BAND | **MATCH**, band=**0.3** |
| FIGURE + mapped + typed value/basis=dex | ignored mapping | reads mapped/typed dex |

Root fix: figure branch no longer falls back to `cell_band`; `_printed_uncertainty_band` accepts mapped PRINTED verbatim (`dex` / `sigma_log10P_dex_per_point` / nested `figure_reading_estimate.log10_p_atm_absolute_uncertainty_dex`) and typed dex value+basis.

### 3. [P1] Catalogue composition does not erase figure reading band

| Probe | Before | After |
|---|---|---|
| FIGURE + PRINTED/mapped band + catalogue proxy composition | band stripped → NO_BAND | band **kept** (0.3); `score_eligible=False`; both figure_only + catalogue-composition flags |

### 4. [P2] All-numeric non-DEX median/IQR

| Probe | Before | After |
|---|---|---|
| Two RELATIVE residue residuals 0.1, 0.3 on all_numeric | n=2, median_dex=None, iqr_dex=None | n=2, **median_dex=0.2**, iqr set |
| Measured tier on same residuals | DEX-filtered medians None | **unchanged** (schema/values preserved) |

### 5. [P2] Injected-predictor contract restored

**Choice stated:** restore the prior contract (do **not** pass `bench=` to caller-supplied predictors).

| Probe | Before (9be2c1726) | After |
|---|---|---|
| `predict=` stub taking only handles/experiment | TypeError unexpected keyword `bench` | no `bench` in kwargs |
| Production `predict is None` path | — | still forwards `bench=` to `predict_with_engine` |

`test_admitted_model_derived_rows_emit_residuals_per_imcc_engine` green. Measured-empty assert updated to filter `tier=="measured"` because payload records now also emit all_numeric.

### 6. [P2] figure_only notice when quantity unknown

| Probe | Before | After |
|---|---|---|
| FIGURE_ONLY + quantity unknown → refused quantity_unknown | no figure_only notice | **FIGURE_ONLY notice attached** |
| Notice.affected_quantities | required nonempty | empty allowed **only** for FIGURE_ONLY |

Committed-store reactive group (tip vs pregate parent): 2 of 127 rows changed — both Bischof figure `quantity_unknown` keys — now carry figure_only (intentional finding 6). Measured headline SHA identical.

---

## De Maria pin rebuild

Fixture `_demaria_shape_figure_rows` now matches landed extract shape:

| Trait | Pin |
|---|---|
| Counts | 20 Na + 10 K = 30 figure_only |
| Samples | 12022 (6 Na) + 12065 (14 Na) |
| Instruments | BC / NAA series labels |
| Cell | typed Re bench `demaria-1971-re-kems` |
| Uncertainty | mapped PRINTED `{dex, sigma_log10P_dex_per_point}` |
| Composition | catalogue proxy on every figure row |
| Reactive pin | prediction-with-flag **or** typed refusal naming missing fO2 — not “anything but old reservoir” |

---

## Pregate three still green

```
pytest -n0 \
  tests/battery/test_score.py::test_streamed_scoring_is_byte_identical_to_legacy_fixture \
  tests/battery/test_score.py::test_streamed_unrailed_measured_tier_matches_legacy_zero_grid \
  tests/battery/test_score.py::test_admitted_model_derived_rows_emit_residuals_per_imcc_engine
→ 3 passed
```

---

## Test commands + counts (VPS, targeted only)

```
pytest -n0 tests/battery/test_b693_b691_scorer_gates.py
→ 15 passed in ~2.6 s

pytest -n0 \
  tests/battery/test_b693_b691_scorer_gates.py \
  tests/battery/test_score.py::test_streamed_scoring_is_byte_identical_to_legacy_fixture \
  tests/battery/test_score.py::test_streamed_unrailed_measured_tier_matches_legacy_zero_grid \
  tests/battery/test_score.py::test_admitted_model_derived_rows_emit_residuals_per_imcc_engine \
  tests/battery/test_score.py::test_catalogue_composition_is_flagged_and_excluded_from_headline \
  tests/battery/test_score.py::test_kems_band_uses_source_printed_pressure_uncertainty
→ 20 passed in ~24 s

pytest -n0 tests/battery/test_silent_fills.py -k 'reactive or Re or cell_material or missing_fO2 or graphite or rhenium'
→ 46 passed, 7 deselected
```

No full W3 / full pytest on this 16GB box. **ASK for Mac Studio** if a full battery green gate is required.

---

## Committed-store byte-identity (scoped; same method as review)

Compared tip `90d1c55d1` vs pregate parent `c42aca18a` on production `load_score_context` + `score_store` (INTERNAL_ANALYTICAL), reactive source group:

| Source group | Sources |
|---|---|
| Reactive (review §4) | kems-007-costa-2015, kems-029-yakovlev-shornikov-2011, kems-137-bischof-2023, kems-138-bischof-2023 |

| Metric | Result |
|---|---|
| Observations | 328 / 328 |
| Engine rows | 127 / 127 |
| Common existing rows identical | **125 / 127** |
| Changed | **2** — both Bischof figure `quantity_unknown` rows gaining figure_only (finding 6) |
| New / lost | 0 / 0 |
| Measured headline records SHA | **identical** `f0a1892ec5acd43a6744ddb98ce3d5a68780dfc36c9cc49f9fd526bf56a26b14` (9 records; empty certified as expected for this reactive set) |

Property kept: measured/certified comparison on NONEMPTY path is preserved for this scorer-only diff relative to pregate; intentional notice attachment on unknown-quantity figures is the only residual body change in the reactive group.

Stale `data/battery/score-report.md` / `score-summary.json` were **not** used as evidence.

---

## Commits

| SHA | Summary |
|---|---|
| `c42aca18a` | pregate: streamed all_numeric parity + measured zero-grid |
| `90d1c55d1` | **this:** six review findings + De Maria pin rebuild |

---

## Remote

```
git ls-remote origin refs/heads/review/b693-b691-scorer-gates
90d1c55d13f4950429cf99eb2c613e8c618150db	refs/heads/review/b693-b691-scorer-gates
```

Local HEAD == remote tip.

# REVIEW — uncalibrated Knudsen / effusion-regime stratum (owner ruling d-055)

- **Reviewer:** regolith-empirical (frontier of record)
- **Seat:** `/workspace/repos/wt/slot-z14` @ tip (`.slot-busy` cleared after this review)
- **Tip:** `874ea78e34bfb421b436a484646f6122a88fbabb` (parent / green `72cb7d960a71e7bb81430ed733a15f99edfa330b`)
- **Branch:** `review/effusion-stratum`
- **Current green (merges clean):** `696299350e98b67f786c334b4b4ccc8856149379` — validity.py / score.py unchanged vs `72cb7d960` on that range
- **Commit:** `Flag uncalibrated KEMS partial pressures` — only
  `simulator/battery/validity.py`, `simulator/battery/score.py`,
  `tests/battery/test_score.py`, `tests/battery/test_mechanisms.py`
- **Date:** 2026-10-01 ~21:05 ET
- **Mode:** read-only on tip; no extract edits; review tip not pushed to green
- **REQ:** `REQ-review-effusion-stratum-874ea78e-2026-10-01.md` — d-055: calibration-absent P_PARTIAL scored flagged (own stratum), over-limit still refuses

## Tip shape

| check | result |
| --- | --- |
| HEAD | `874ea78e34bfb421b436a484646f6122a88fbabb` |
| Parent | `72cb7d960a71e7bb81430ed733a15f99edfa330b` (exactly one commit) |
| Files vs parent | **4** claimed files only (`+347 / −19`) |
| Merge onto `696299350` | clean (no validity/score conflict) |

**PASS.**

## Attack (1) — ordering: pressure sum before calibration branch vs grounded rows

**Code (tip `effusion_regime_unverified`):** when calibration is **not** grounded and quantity is `P_PARTIAL`, the gate now computes `_printed_in_cell_pressure_sum` **before** the calibration-flag pass; `pressure_sum > pressure_limit` still `_fail`s `EFFUSION_REGIME_UNVERIFIED`. Grounded calibration never enters that branch (same post-block path as green).

**Store-wide empirical compare** (same 1273 `P_PARTIAL` rows; tip code @ seat vs green code @ detached `72cb7d960` worktree `/tmp/effusion-green-cmp`):

| cohort | n | tip vs green gate outcomes |
| --- | ---: | --- |
| calibration-**grounded** | **645** | **0 mismatches** (passed/reason/primary_check/flag identical) |
| grounded tip/green outcome counters | 535 pass + 110 refuse `in_cell_partial_pressure_sum` | **identical** |

Ungrounded transitions (not required to be identical): 370 green `effusion_regime_unverified`/`in_cell_partial_pressure_sum` → tip pass + `calibration_not_grounded`; 1 orifice `p/d` over-limit still refuses both sides.

**PASS.** Ordering does not change any grounded-row gate outcome.

## Attack (2) — band exclusion + own stratum with counts

| mechanism | evidence |
| --- | --- |
| KEMS printed/replicate band population | `_derive_kems_partial_pressure_band` / `_kems_replicate_groups` skip via `_observation_flagged_strata` (runs gates → `_flagged_stratum_notices` → `calibration-not-grounded`) |
| Derived 2×MAD / per-family pool | `family_residuals` population **continues** past any `_is_flagged_stratum_notice` (UNVERIFIED_APPARATUS covers `calibration_not_grounded:` reasons); same skip in compilation derived_n |
| Headline / measured rollups | `_measured_residuals` drops `flagged_strata(notices)`; `EligibleConjuncts.not_flagged_stratum` ⇒ `score_eligible=False` |
| Own stratum naming | `FLAGGED_STRATUM_CALIBRATION_NOT_GROUNDED = "calibration-not-grounded"`; split out from generic `unverified-apparatus` when reason starts with `calibration_not_grounded:` |
| Summary/report | `flagged_stratum_rows` + `render_score_report_from_payloads` emit stratum table with n / median / RMS |

Live on five focus sources (`score_store`, OpenIMCC, diagnostics):

- `derive_kems_partial_pressure_band(...)` → **None** (only flagged candidates)
- Report lines: `calibration-not-grounded | SiO_evolution | openimcc | 23` and `calibration-not-grounded | vapour | openimcc | 44`
- All 149 scored residuals among the 199: `score_eligible=False`

Named mutation tests (seat venv, `-o addopts=''`):  
`test_uncalibrated_kems_partial_pressure_scores_with_calibration_notice`,  
`test_uncalibrated_kems_partial_pressure_is_excluded_from_band_population`,  
`test_mf_f04_uncalibrated_in_cell_pressure_sum_above_limit_still_refuses` (+ related) → **passed**.

**PASS.**

## Attack (3) — scorer product on the 199 (not just the gate)

`load_score_context(sources=five)` → 199 `P_PARTIAL`; `score_store(..., engines=(Engine.OPENIMCC,), include_diagnostics=True)`.

### Gate transition confirmation (five sources)

| source | green refuse effusion | tip pass+flagged |
| --- | ---: | ---: |
| kems-053-stolyarova-1991 | 84 | 84 |
| kems-023-demaria-1973 | 61 | 61 |
| kems-137-bischof-2023 | 22 | 22 |
| kems-028-yakovlev-1984 | 22 | 22 |
| kems-138-bischof-2023 | 10 | 10 |
| **total** | **199** | **199** |

ichise-1989 / allibert-1981 / ms2000-044: **0** P_PARTIAL either way (see §4).

### Scorer outcomes for those 199

| bucket | n | notes |
| --- | ---: | --- |
| HAS residual (OpenIMCC) | 149 | |
| NO residual | 50 | 31 Demaria `pending`+`quoted_unattributed`; 11 Yakovlev `pending`+`model_derived`; 8 Stolyarova `rejected`+`model_derived` |
| status `no_band` + numeric dex | **67** | all Stolyarova 1991; median **−2.226** dex; min −4.076; max −0.413 |
| status `refused` | 82 | see reasons below |
| `score_eligible` true | **0** | |
| visible `calibration_not_grounded:` notice | 75 | all 67 numeric Stolyarova + 8 Demaria catalogue-composition combo |

**Refusals by reason (82):**

| reason | detail | n | sources |
| --- | --- | ---: | --- |
| `identity_incomplete` | `composition_not_stated_pure_reservoir` | 61 | Demaria 22, Bischof-137 22, Yakovlev 7, Bischof-138 10 |
| `identity_incomplete` | `cell_material_unknown` | 8 | Demaria |
| `identity_unknown` | (incl. `temperature_unknown` ×4) | 13 | Stolyarova 9 + Yakovlev 4 |

**Stolyarova “next gate” note (REQ known claim):** on this tip, Stolyarova 1991 does **not** mostly refuse for `identity_unknown` on reaction/reference_state/reservoir. **67/84** produce numeric OpenIMCC residuals (`no_band`) with the calibration notice and `calibration-not-grounded` stratum (often plus source-internally-inconsistent / imcc_complex_saturation). Only **9** refuse `identity_unknown`; **8** rejected model_derived rows produce no residual. Identity/composition refusals **do** dominate Demaria / Bischof / Yakovlev after the effusion gate opens.

**PASS** (scorer evidence recorded; Stolyarova behavior corrected vs the “known” sketch).

## Attack (4) — why 199 and not ~261

1. **Inside the five named sources there is no 62-row hole:** each source’s P_PARTIAL count equals its transition count (199/199). No rows in that set stay refused at effusion for a different first cause.
2. **ichise-1989 / allibert-1981 / ms2000-044 are quantity-missing, not first-cause refusals:** extracts-v2 are activity-heavy (`kems-112-ichise-1989` 40 activity / 0 p_partial; `kems-051-allibert-1981` 71 activity; `kems-ms2000-044` 96 activity). Worker “0 either way” confirmed.
3. **Store-wide** ungrounded `in_cell` effusion refuse → tip pass-flagged = **370** (199 in the five + 171 elsewhere: e.g. ohara-1987 60, stolyarova-1995 50, plante-hastie-1983 25, hastie-1981 12, yakovlev-shornikov-2011 12, …). A casual ~261 (e.g. 199+50+12) would be five + stolyarova-1995 + one 12-row source — those extras **do** transition on tip but were **outside** the worker’s five-source counter, not identity/composition losses inside it.
4. Separately, 147 ungrounded P_PARTIAL still refuse `method_unknown` both sides (not in the 199).

**PASS** (difference explained; not an undercount of the five).

## Attack (5) — NOT-FIXED lens

This tip does **not**:

- Ground or invent calibration / orifice geometry / cell facts in extracts
- Fix identity_incomplete (`composition_not_stated_pure_reservoir`, `cell_material_unknown`) or identity_unknown that refuse after the effusion gate opens
- Admit pending/quoted/model_derived/rejected rows into comparison candidates
- Change Drowart / orifice p/d over-limit refusals (synthetic 12 Pa still refuses; live orifice over-limit still refuses)
- Touch activity-only sources (ichise/allibert/ms2000)
- Alter band numerics for unflagged rows beyond excluding the new flagged stratum
- Regenerate the derived store or push to green

**PASS** (scope honest).

## Targeted tests

Seat `.venv`, `-o addopts=''`, focused battery score/mechanisms (uncalibrated / in_cell / calibration notice / band exclusion): **9 passed** in the broader `-k` slice; named mutation trio **passed**. No full W3 suite.

## P0 / P1 / P2

- **P0:** none
- **P1:** none
- **P2:** none (Stolyarova scorer product differs from the REQ’s “known” sketch — reported, not a tip defect)

VERDICT: LAND 874ea78e34bfb421b436a484646f6122a88fbabb

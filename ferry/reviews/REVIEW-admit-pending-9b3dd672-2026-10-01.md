# REVIEW — d-056 admit pending literature rows by default, flagged

- **Reviewer:** regolith-empirical (frontier of record)
- **Seat:** `/workspace/repos/wt/slot-b565` @ `review/admit-pending` (VPS shell OOM mid-review; checks completed on Mac Studio-1 detached worktree at the same SHA)
- **Tip:** `9b3dd672723e02b976c07e38e4a8eb1f7afe4058` (parent / green `72cb7d960a71e7bb81430ed733a15f99edfa330b`)
- **Branch:** `review/admit-pending`
- **Commit:** `Default pending literature admissions` — code in `simulator/battery/{enums,migrate,validate}.py` + targeted tests; 61 regenerated store files (effect reviewed via `load_migrated_store` / `observation_from_plain`, not byte diff)
- **Date:** 2026-10-01 ~23:20 ET
- **Mode:** review CODE + regeneration EFFECT at stated SHA; tip not rebased onto later green `c9b6e545d`
- **REQ:** `REQ-review-admit-pending-9b3dd672-2026-10-01.md`

## Tip shape

| check | result |
| --- | --- |
| HEAD | `9b3dd672723e02b976c07e38e4a8eb1f7afe4058` |
| Parent | `72cb7d960a71e7bb81430ed733a15f99edfa330b` (exactly one commit; green is ancestor) |
| Code | `NoticeKind.ADMISSION_DEFAULTED`; `_admission_from_plain` / `admission_for` / Migrator default numeric pending→admitted with notice; validate allows admitted-without-decided_by when defaulted notice present |
| Green tip note | origin/work-v064-green may be at `c9b6e545d`; **not rebased** per REQ |

**PASS.**

## Check (1) — Scope (only no-decision / pending / pending_validation)

Store-wide via `load_migrated_store` (typed API; d-056 applied at deserialize):

| | parent `72cb7d960` | tip `9b3dd6727` |
| --- | ---: | ---: |
| total | 120847 | 120847 |
| admitted | 27913 | 69275 |
| pending | 91418 | 50056 |
| rejected | **266** | **266** |
| superseded | **1250** | **1250** |
| `admission_defaulted:` reasons | 0 | 41362 |

Original non-measurement tokens (reason contains `source admission_status=<token>`), **before = after**, all still **pending**:

| original token | parent | tip |
| --- | ---: | ---: |
| figure_only | 185 pending | 185 pending |
| model_output_not_measurement | 252 pending | 252 pending |
| qualitative_not_numeric | 4 pending | 4 pending |
| measured_not_tabulated | 17 pending | 17 pending |

**PASS.** Rejected/superseded and the four explicit non-measurement tokens are untouched.

## Check (2) — Headline safety

`comparison_candidates` still requires `evidence.class_ ∈ MEASURED_EVIDENCE` and status ∈ {admitted, pending}.

| | parent | tip |
| --- | ---: | ---: |
| comparison_candidates (measured∩{adm,pend}) | **3306** | **3306** |
| of which admitted | 483 | 1381 |
| defaulted with measured evidence | 0 | 898 |
| defaulted with compilation_assessed | 0 | 39754 |

- **species-rail-differential / ledger / compilation rows:** 20589 species-rail + pankratz/kelley-king/atct/nasa-glenn/… defaulted under `compilation_assessed` (or non-MEASURED). They **cannot** enter the MEASURED headline candidate set. Confirmed: `defaulted_in_comparison_candidates` = 898 = measured-only.
- **Compilation-tier scoring:** descriptive compilation residuals still come from the compilation/diagnostic path; `reference_measured_evidence` is forced false for compilation so they never become fully `score_eligible`. Effect beyond the new notice is the `admission_admitted` conjunct only (still blocked by measured-evidence conjunct). **Unchanged except notice** for compilation descriptive path.
- **FLAG (band / score_eligible population):** `score_eligible` requires `admission_admitted`. Moving 898 previously-pending **measured** rows to admitted lifts that exclusion (candidate *set* unchanged at 3306; admitted_measured 483→1381). KEMS `derive_kems_partial_pressure_band` also requires ADMITTED — 125 defaulted Knudsen `p_partial` rows are newly eligible for band membership; **probe showed identical band width** before vs after (`0.146128…` dex, printed-envelope path). Pin files not rewritten by this tip.

**PASS with FLAG** (intentional d-056 effect on measured score_eligible / band eligibility; compilation MEASURED-headline exclusion holds; live KEMS band value unchanged).

## Check (3) — Notice visibility

| population | tip count |
| --- | ---: |
| defaulted rows with `NoticeKind.ADMISSION_DEFAULTED` | **41362 / 41362** |
| defaulted missing notice | **0** |
| explicitly admitted (non-defaulted) with admission_defaulted notice | **0** |

`score_store` residuals for newly defaulted scored rows carry the notice (sossi 344/344; bischof measured-defaulted 24/24).

**PASS.**

## Check (4) — `score_store(engines=(OPENIMCC,), include_diagnostics=True)`

### kems-012-sossi-2019

- loaded 612 obs; defaulted 344 (all `measured_direct` / `residue_component_composition`); all 344 ∈ comparison_candidates
- residuals 348; candidates 0
- **newly admitted outcomes:** 344× `refused:unsupported` (no numeric residuals)
- admission_defaulted notice on 344/344 residuals

### kems-137-bischof-2023

- loaded 164 obs; defaulted 152 (24 measured: 22 `p_partial` + 2 reduced; 128 UNKNOWN evidence / mostly `activity_coefficient`)
- only the 24 measured defaulted enter comparison_candidates; 128 neither CC nor diagnostic_references → **no residual produced** (admitted-but-unscored)
- **newly admitted measured outcomes:** 24× `refused:effusion_regime_unverified` (gates still apply; no numeric residuals)
- notice on 24/24 scored defaulted residuals

**PASS.**

## Check (5) — NOT-FIXED lens

This tip does **not**:

- invent extract numbers, figure digitizations, or reviewer `decided_by` decisions (reason text states “not a reviewer decision”)
- admit the four explicit non-measurement tokens, rejected, or superseded rows
- put non-MEASURED / compilation_assessed rows into MEASURED `comparison_candidates`
- clear scorer mechanical gates (`unsupported`, `effusion_regime_unverified`, identity gates, etc.)
- rebase onto later green `c9b6e545d` or rewrite pin YAML
- regenerate the store again onto current green (landing merge will)

**PASS.**

## Targeted tests (Studio-1, scoped; not full W3)

→ **56 passed**, 3014 deselected (`test_score` / `test_migrate` / `test_schema_admission` / `test_migrate_r7` admission-default filters).

## Verdict

All five REQ checks answered with evidence. One intentional FLAG on measured score_eligible / KEMS band *eligibility* (band *value* unchanged in probe). No P0/P1 defect against d-056.

**VERDICT: LAND 9b3dd672723e02b976c07e38e4a8eb1f7afe4058**

— regolith-empirical

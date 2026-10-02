# REVIEW — default pending literature admissions (owner ruling d-056)

- **Reviewer:** regolith-empirical (frontier of record)
- **Seat:** `/workspace/repos/wt/slot-z20` @ tip
- **Tip:** `9b3dd672723e02b976c07e38e4a8eb1f7afe4058` (parent / green-at-branch `72cb7d960a71e7bb81430ed733a15f99edfa330b`)
- **Branch:** `review/admit-pending`
- **Current green (context only; not merged):** `696299350e98b67f786c334b4b4ccc8856149379` — code paths for enums/migrate/validate do not conflict with tip; one extracts-v2 data file (`stolyarova-1996-…`) is “changed in both” and will be regenerated on land
- **Commit:** `Default pending literature admissions` — code
  `simulator/battery/enums.py` (+2), `migrate.py` (+167/−?), `validate.py` (+11);
  tests `test_migrate.py`, `test_migrate_r7.py`, `test_schema_admission.py`, `test_score.py`;
  plus 61 regenerated data files (review CODE + regeneration EFFECT, not YAML bytes)
- **Date:** 2026-10-01 ~23:23 ET
- **Mode:** read-only on tip; no extract edits; review tip not pushed to green
- **REQ:** `REQ-review-admit-pending-9b3dd672-2026-10-01.md` — d-056: admit pending / no-decision literature rows by default, flagged

## Tip shape

| check | result |
| --- | --- |
| HEAD | `9b3dd672723e02b976c07e38e4a8eb1f7afe4058` |
| Parent | `72cb7d960a71e7bb81430ed733a15f99edfa330b` (exactly one commit; green-at-branch is ancestor) |
| Code vs parent | **7** files (`+353 / −44` on enums/migrate/validate + four test modules) |
| Store headline (regen effect) | admitted **27,913 → 69,054** (+41,141); pending **91,418 → 50,277** (−41,141); rejected **266**; superseded **1,250**; total **120,847**; hard issues **3,235** |

**PASS.**

## Attack (1) — scope: only no-decision / pending / pending_validation

Store-wide census of `extracts-v2` + `observations-v2` on parent `72cb7d960` vs tip `9b3dd6727` (same 120,847 observation ids):

| cohort | parent | tip | Δ |
| --- | ---: | ---: | ---: |
| admitted | 27,913 | 69,054 | **+41,141** |
| pending | 91,418 | 50,277 | **−41,141** |
| rejected | 266 | 266 | **0** |
| superseded | 1,250 | 1,250 | **0** |
| `admission_defaulted:` reason | 0 | 41,141 | +41,141 |
| `admission_defaulted` notice | 0 | 41,141 | +41,141 |

**Four explicit non-measurement tokens (status breakdown unchanged):**

| original token | parent | tip | statuses |
| --- | ---: | ---: | --- |
| `model_output_not_measurement` | 274 | 274 | pending 252 + rejected 22 |
| `figure_only` | 189 | 189 | pending 189 |
| `qualitative_not_numeric` | 4 | 4 | pending 4 |
| `measured_not_tabulated` | 17 | 17 | pending 17 |

Token counts for `pending` (59) and `pending_validation` (108) as *source* tokens are preserved under the defaulted reason string (`admission_defaulted: source admission_status=…`). Rejected-ish tokens (`rejected_no_figure_reading` 14, `rejected_no_complete_figure_digitization` 15, `rejected_model_output_not_measurement` 9, …) unchanged.

Code: `admission_for` defaults only `None`/empty, `pending`, `pending_validation`; closed non-pending tokens and `rejected*` keep mapped status; Migrator reverts defaulted → pending when `value.kind` ∉ `{point,series,bound,interval,relative_series}` (no-number rows stay pending). Parametrized unit test `test_source_admission_default_scope` covers figure_only / model_output_not_measurement / rejected_* / admitted.

**PASS.**

## Attack (2) — headline safety (ledgers vs MEASURED)

**Are ledger/compilation rows measurements?** No for species-rail-differential:

| | n | evidence | admitted (defaulted) | measured |
| --- | ---: | --- | ---: | ---: |
| `species_rail_differential_ledger.yaml` | 23,673 | **all** `compilation_assessed` | 20,589 | **0** |

Other large defaulted ledgers (pankratz-1984 7,061; kelley-king-1961 3,670; atct 3,422; …) are likewise `compilation_assessed` in the defaulted evidence rollup (**39,754** of 41,141 defaulted rows).

**MEASURED headline filter:** `comparison_candidates` (`score.py`) requires `evidence.class_ ∈ MEASURED_EVIDENCE` (`measured_direct|tabulated|reduced`) **before** admission. Probe on mixed load: CC evidence was only measured_*; **0** defaulted non-measured rows entered CC. Defaulted MEASURED cohort that *can* enter CC: **820** store-wide (kems-012 **344**, kems-015 120, kems-042 59, kems-137 **24**, …).

**Compilation-tier / diagnostic path:** `diagnostic_references` selects by origin/source (compilation / internal consistency / SF04 workbook), not by flipping MEASURED evidence. Unit test `test_default_admission_notice_is_visible_without_changing_score` asserts residual status / `score_eligible` unchanged vs notice-free baseline; notice is present on the residual.

**Band / pin population (must say so):** KEMS vapour `p_partial` band candidates require `AdmissionStatus.ADMITTED` (`_derive_kems_partial_pressure_band` / `_kems_replicate_groups`). Tip vs parent-simulated (defaulted → pending):

| | parent-sim | tip | Δ |
| --- | ---: | ---: | ---: |
| vapour KEMS band candidates (plante+bischof+sossi load) | **162** (all plante) | **243** | **+81** |
| newly admitted into band set | — | 59 plante + 22 bischof | |
| band **value** | 0.146128… | 0.146128… | **equal** |
| band **rule** text | identical | identical | |

The +81 all have `uncertainty.kind=none` and no composition value, so they do **not** move the printed-envelope RMS that sets the band width. Pins file has no `admission_defaulted` / plante `s1214` keys. **Admission does enlarge the admitted band-candidate set; the live Plante envelope value is unchanged on this tip.**

**PASS** (headline MEASURED filter holds; band-candidate growth disclosed; value unchanged).

## Attack (3) — notice visibility

- Every defaulted row: reason starts with `admission_defaulted:` **and** carries `NoticeKind.ADMISSION_DEFAULTED` (41,141 / 41,141 store-wide).
- Explicit admitted (no defaulted reason): **0** with that notice (27,913 plain admitted remain notice-free for this kind).
- Residuals: OpenIMCC `score_store` on kems-012 + kems-137 — defaulted MEASURED residuals **344/344** and **24/24** carry the notice; explicit admitted residuals **0** with it.
- `validate_observation` allows ADMITTED without `decided_by` only when the matching defaulted notice is present.

**PASS.**

## Attack (4) — scorer product on kems-012 / kems-137 (not gate counts)

`load_score_context(sources=['kems-012-sossi-2019','kems-137-bischof-2023'])` → 776 obs; `score_store(..., engines=(Engine.OPENIMCC,), include_diagnostics=True)`.

Newly admitted **MEASURED** rows (defaulted notice + MEASURED_EVIDENCE):

### kems-012-sossi-2019 — 344 rows

| bucket | n | detail |
| --- | ---: | --- |
| quantity | 344 | all `residue_component_composition` |
| status `refused` | **344** | |
| numeric residual | **0** | |
| notice on residual | 344 / 344 | |

**Refusals by reason:** `unsupported` / `quantity_not_predicted` (`residue_component_composition`) × **344**.

### kems-137-bischof-2023 — 24 rows

| bucket | n | detail |
| --- | ---: | --- |
| quantity | 22 `p_partial` + 2 `activity_coefficient` | |
| status `refused` | **24** | |
| numeric residual | **0** | |
| notice on residual | 24 / 24 | |

**Refusals by reason:** `effusion_regime_unverified` / primary `in_cell_partial_pressure_sum` × **24** (tip is on `72cb7d960` **without** the later d-055 calibration-flag stratum; over-limit / unverified effusion still refuses here).

Defaulted **non-measured** rows from these sources (e.g. kems-137’s extra defaulted beyond 24) do **not** enter `comparison_candidates` (0 non-measured defaulted in CC for this load).

**PASS** (product recorded; no numeric residuals among newly admitted MEASURED on these two sources).

## Attack (5) — NOT-FIXED lens

This tip does **not**:

- Invent extract admission decisions, `decided_by`, or reviewer rationale (reason text says “not a reviewer decision”)
- Admit the four explicit non-measurement tokens, or rejected / superseded rows
- Promote `compilation_assessed` / ledger rows into the MEASURED headline (`comparison_candidates` still filters on `MEASURED_EVIDENCE`)
- Implement OpenIMCC (or any engine) prediction for `residue_component_composition` (344 kems-012 refusals remain)
- Clear Bischof `effusion_regime_unverified` / `in_cell_partial_pressure_sum` (needs apparatus / d-055 stratum on a later green, not this commit)
- Freeze regenerated YAML bytes (landing merge re-migrates onto current green)
- Change mechanical score gates beyond admission default + notice plumbing

**PASS.**

## Targeted tests (VPS, scoped)

```
.venv/bin/python -m pytest tests/battery/test_schema_admission.py \
  tests/battery/test_score.py tests/battery/test_migrate.py \
  tests/battery/test_migrate_r7.py \
  -k 'admission_default or default_admission or source_admission_default or missing_admission or plain_pending_without or figure_only or g2_plante_source or defaulted' \
  -o addopts='' -q
```

→ **14 passed** (no full W3). OpenIMCC installed only into a throwaway `/tmp` venv for the two-source score probe.

## Verdict

All five REQ checks PASS. Band-candidate set growth (+81) disclosed; band value unchanged; no P0/P1 defect against d-056.

**VERDICT: LAND 9b3dd672723e02b976c07e38e4a8eb1f7afe4058**

— regolith-empirical

# REVIEW — residue-batch: Sossi residue identity fix under R1a/R1b

- **Reviewer:** regolith-empirical (frontier of record)
- **Seat:** `/workspace/repos/wt/slot-b565`
- **Tip:** `9e434da92623a4c4c6c887bd1659e3a876906d91` on `review/residue-batch`
- **Parents:** `69f5828c4ec70877a6d390d712e50ff5909ece49` (R1b LAND) + `138be92af702fea2cc37a0c1b2983e96214434a3` (R0 identity fix)
- **R1a LAND ancestor:** `802b69ad7b6197046b27286e98e42bed91322fbc` (confirmed)
- **Green tip context:** `191960ce8a657a25681aad624dd64670bf85df6a` on `work-v064-green` (via R0→R1a→R1b)
- **Bounced R0:** `53d71755a4be677d22e50d10a3275981424cc7bd` — regenerated store failed to load (elemental Gd inside oxide mass-% identity)
- **Range (delta under review):** fix `138be92af` merged under LANDed R1a/R1b via merge-tree tip `9e434da9`
- **REQ:** `/workspace/ferry-inbox/REQ-residue-batch-from-regolith-physics-2026-10-02.md`
- **Bundle:** `/workspace/ferry-inbox/regolith-physics-residue-2026-10-02/r0-fix-report.md`
- **Date:** 2026-10-02 ~16:24–16:50 ET
- **Mode:** read-only for product code; extracts not edited; no force-push; no Mac listen pools. Targeted VPS unit tests only (no full W3 / no full-store regen on this ~16GB box).

## Scope

DELTA REVIEW of the merge tip that folds the R0 bounce-fix under already-LANDed R1a (oxygen root) and R1b (residue inventory integrator).

Fix behavior: Sossi residue **identity** compositions carry **only** printed oxides (`basis="printed_oxides"`); elemental starting ppm is **not** injected into the mass-% identity map. Trace start remains source-located at extract `species.<El>.observations[..].values.starting_measured_ppm` and is migrated to `point_conditions.starting_component_ppm` via the run join.

Files (`git diff --stat` tip vs R1b parent `69f5828c4`):

| Path | Δ |
| --- | --- |
| `simulator/battery/migrate.py` | +1 / −8 |
| `tests/battery/test_migrate.py` | +1 / −4 |

No other product paths in the merge delta. Tip tree `git merge-tree --write-tree 69f5828c4 138be92af` = tip tree `6c7ff761…` (exit 0, no conflicts). Tip `migrate.py` / `test_migrate.py` blobs identical to fix commit.

Note: origin `review/residue-batch` later advanced to `d1179bbf9` (merge of KEMS parity green into this tip). Review of record is the **assigned** tip `9e434da9`; fix blobs are identical on that later tip.

## Attack results

### (1) Fix: no schema change; refusal intact; trace start reachable — **PASS**

- **Schema:** Fix commit touches only `migrate.py` + `test_migrate.py`. No enums/identity/schema edits in the fix. (R0 previously *tightened* `RESIDUE_COMPONENT_COMPOSITION` identity profile required fields — that is prior train work, not this fix.)
- **Refusal not weakened:** `_composition_from_plain` still raises `ValueError("mass-percent composition contains unsupported or ambiguous components")` when an elemental key (e.g. `Gd`) appears in a mass-% component map. `_mass_percent_components` still gates on `_OXIDE_COMPONENT_KEYS` (oxides only). Seat mutation: oxides+`Gd 0.10606` → raise; oxides-only → success.
- **Identity path:** residue `residue_ppm` branch no longer does `components[species.formula] = start_ppm / 10000`; basis is `"printed_oxides"` (was `"printed_oxides_plus_starting_element"`).
- **Trace start via run join:** `starting_measured_ppm` → `point_conditions.starting_component_ppm` still set when locator present (`migrate.py` ~11592–11604). Targeted Sossi migrate: **344/344** admitted point residue rows carry `starting_component_ppm`; Gd sample = `1060.6` (matches extract Table 1). All 344 have `experiment_id` ending `::experiment::sossi-2019-run-…`. All identity compositions `basis == printed_oxides` with **0** elemental keys in the mass-% map.
- Acceptance unit: `test_residue_cells_keep_run_identity_and_printed_conditions` **PASS** (asserts `printed_oxides`, composition == printed sample oxides only, `starting_component_ppm` present).

### (2) Merge clean — **PASS**

- Parents of `9e434da9`: `69f5828c4` (R1b) + `138be92af` (fix).
- `git merge-base --is-ancestor` : R1a, R1b, and fix are all ancestors of tip.
- `git merge-tree --write-tree 69f5828c4 138be92af` → `6c7ff76177320648017c622ba47ff7e9168e46da` exit 0; equals `9e434da9^{tree}`; no CONFLICT markers.
- Merge touches only the two fix files relative to R1b.

### (3) Whole-store regen + `load_migrated_store` — **GAP (VPS); targeted substitute PASS**

- On-disk `extracts-v2` / observations store at this worktree still predates R0 identity write-out (no `printed_oxides_plus_starting_element` in store YAML). A load of the **current** store therefore does **not** exercise the bounce condition.
- Full regen + load of tip on this ~16GB VPS was **not** completed: prior `load_migrated_store` probe climbed past ~1.6 GiB RSS / 10+ min without finishing; `test_validate_corpus_zero_hard_issues_on_migrated_store` **timed out at 300 s** inside YAML load (`pytest-timeout`), not on a hard-issue assertion.
- **Targeted substitute (this seat):** tip-code Sossi migrate → all residue identity compositions oxides-only; mutation re-adding Gd to an oxide identity makes `_composition_from_plain` raise (same refusal the full-store load hits). Matches worker null-hypothesis.
- **Mac Studio ASK:** full store regen on tip + `load_migrated_store` with zero raises, and `tests/battery/test_migrate.py::test_validate_corpus_zero_hard_issues_on_migrated_store` (~170 s on studio per train). Do **not** run full W3 here.

### (4) Train's three bounced tests — **2 PASS; 1 TIMEOUT (load), not a gate fail**

| Test | Result |
| --- | --- |
| `tests/battery/test_migrate.py::test_validate_corpus_zero_hard_issues_on_migrated_store` | **TIMEOUT >300 s** during store YAML load on VPS — **not** an observed hard-issue failure. Re-run on Mac Studio after regen. |
| `tests/battery/test_score.py::test_admitted_model_derived_rows_emit_residuals_per_imcc_engine` | **PASS** (~29 s) |
| `tests/battery/test_validity.py::test_full_store_validity_gates_do_not_raise[6]` | **PASS** (~59 s) |

## Targeted tests (VPS)

| Suite | Result |
| --- | --- |
| `tests/battery/test_migrate.py::test_residue_cells_keep_run_identity_and_printed_conditions` | **1 passed** (~57 s) |
| Sossi-targeted migrate + trace/mutation script (seat) | **PASS** (344 printed_oxides; 344/344 starting_component_ppm; Gd 1060.6; mutation raises) |
| `_composition_from_plain` Gd-in-oxides mutation | **raises as required** |
| `test_score.py::test_admitted_model_derived_rows_emit_residuals_per_imcc_engine` | **1 passed** (~29 s) |
| `test_validity.py::test_full_store_validity_gates_do_not_raise[6]` | **1 passed** (~59 s) |
| `test_migrate.py::test_validate_corpus_zero_hard_issues_on_migrated_store` | **TIMEOUT** (VPS load; Mac Studio ASK) |

Not run (policy/cost): full W3, full-store regen on tip, bulk pytest, worker's claimed 865/969 suites.

## Worker-claim cross-check

Worker (`r0-fix-report.md`): regenerated full store loads (263 / 3,518 / 121,085); census matches green `191960ce8` across 278 sources; 516/522 Sossi residue identities changed; mutation re-add Gd fails load. Seat independently confirms the **code** and **Sossi migrate** behavior and the mutation refusal; does **not** re-run full regen/census on VPS (gap → Mac Studio ASK).

## Verdict

**LAND `9e434da92623a4c4c6c887bd1659e3a876906d91`**

No P1 REVISE on the fix or merge. Residual operational gap only: whole-store regen + `test_validate_corpus_zero_hard_issues_on_migrated_store` deferred to Mac Studio (VPS timeout/memory), not a code blocker for LAND of this migrate-only fix under already-reviewed R1a/R1b.

— regolith-empirical

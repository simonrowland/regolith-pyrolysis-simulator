# REVIEW — residue-batch tip update: t1075 green under R0-fix+R1a/R1b

- **Reviewer:** regolith-empirical (frontier of record)
- **Seat:** `/workspace/repos/wt/slot-b565`
- **Tip:** `d1179bbf9da452d13334502210a1d4b3dbfd2fca` on `review/residue-batch`
- **Parents:** `9e434da92623a4c4c6c887bd1659e3a876906d91` (prior batch = R0 fix under R1a/R1b) + `a8cefc8ba67d82c3aa12a27f5f4cb513f8ccb891` (t1075: Assert KEMS parity band exclusion)
- **R1b / R1a / R0-fix ancestors:** `69f5828c4`, `802b69ad7`, `138be92af` (all `merge-base --is-ancestor` of tip)
- **Green tip context:** `a8cefc8ba` (= t1075; prior green was `191960ce8`)
- **Prior OF RECORD on older tip:** `REVIEW-residue-batch-9e434da9-2026-10-02.md` (LAND `9e434da9`); this review is the NOTICE tip-update delta on `d1179bbf9`
- **REQ:** `/workspace/ferry-inbox/REQ-residue-batch-from-regolith-physics-2026-10-02.md`
- **NOTICE:** `/workspace/ferry-inbox/NOTICE-residue-batch-tip-from-regolith-physics-2026-10-02.md`
- **Bundle:** `/workspace/ferry-inbox/regolith-physics-residue-2026-10-02/r0-fix-report.md`
- **Date:** 2026-10-02 ~16:40–17:00 ET
- **Mode:** read-only for product code; extracts not edited; no force-push of review branches; no Mac listen pools. Targeted VPS unit tests only (no full W3 / no full-store regen on this ~16GB box).

## Scope

DELTA REVIEW of tip `d1179bbf9` = merge of current green t1075 (`a8cefc8ba`, `test_score.py` only: +4 asserts that uncalibrated KEMS residuals carry `decision_band is None`) into prior batch tip `9e434da9` (R0 Sossi identity fix under LANDed R1a/R1b). NOTICE requires exercising full `tests/battery/test_score.py` so t1075 parity and R1b typed-absence land together.

Files (`git diff --stat` tip vs `9e434da9`):

| Path | Δ |
| --- | --- |
| `tests/battery/test_score.py` | +42 (t1075 parity test body from `e1b6f70bf` + `a8cefc8ba` band-exclusion asserts; R1b typed-absence already on `9e434da9`) |

Fix blobs (`migrate.py` / `test_migrate.py`) identical to `138be92af` on tip. No schema / extract edits in this tip delta.

## Attack results

### (1) R0 bounce fix fidelity — **PASS** (reconfirmed on tip)

- **No schema change in fix:** `138be92af` touches only `migrate.py` (+1/−8) and `test_migrate.py` (+1/−4). Tip vs R1b for product paths beyond that is R1a/R1b already-LANDed work.
- **Refusal not weakened:** `_composition_from_plain` still raises `ValueError("mass-percent composition contains unsupported or ambiguous components")` when elemental `Gd` appears in a mass-% map. Seat probe: oxides+`Gd 0.10606` → raise; oxides-only → success (`basis=printed_oxides`, mole_fraction conversion).
- **Identity path:** residue `residue_ppm` branch uses `basis="printed_oxides"` only; no `components[species.formula] = start_ppm/10000`; old `printed_oxides_plus_starting_element` string absent from tip `migrate.py`.
- **Trace start via run join:** `starting_measured_ppm` → `point_conditions.starting_component_ppm` still set when locator present (`migrate.py` ~11594–11604). Acceptance `test_residue_cells_keep_run_identity_and_printed_conditions` **PASS** (asserts `printed_oxides`, composition == printed sample oxides only, `starting_component_ppm` present).

### (2) Merge-tree clean for both merges — **PASS**

| Merge | Result |
| --- | --- |
| R0 fix `138be92af` under R1b `69f5828c4` | `git merge-tree --write-tree` → `6c7ff761…` exit 0; equals `9e434da9^{tree}`; no CONFLICT / no `changed in both` |
| R0 fix under R1a `802b69ad7` | write-tree exit 0; no CONFLICT |
| t1075 `a8cefc8ba` into batch `9e434da9` | write-tree → `13adf367…` exit 0; equals `d1179bbf9^{tree}`; Auto-merging `tests/battery/test_score.py` (both parents edit it; result has **both** R1b typed-absence and t1075 parity). No conflict markers in tip. |

### (3) Whole-store regen + `load_migrated_store` — **PARTIAL (VPS)**

- On-disk committed store predates R0 identity write-out; loading it does **not** re-exercise the bounce (elemental-in-oxides identity). Full regen on tip not completed on ~16GB VPS (policy).
- **`test_validate_corpus_zero_hard_issues_on_migrated_store`:** first attempt under default `--timeout=300` / xdist **TIMEOUT** during YAML load; retry **serial `-n0 --timeout=600`** → **1 passed in 417.89 s** (load+validate of current store succeeds with enough RAM/time). This is **not** a tip-code regen proof.
- **Mac Studio ASK (unchanged from prior batch OF RECORD):** full store regen on tip `d1179bbf9` + `load_migrated_store` with zero raises, and re-confirm validate after regen (~170 s studio). Do **not** run full W3 here.

### (4) Train bounced trio — **3 PASS** (validate on serial retry)

| Test | Result |
| --- | --- |
| `tests/battery/test_migrate.py::test_validate_corpus_zero_hard_issues_on_migrated_store` | **PASS** (~418 s, `-n0 --timeout=600`); earlier 300 s xdist attempt timed out on load only |
| `tests/battery/test_score.py::test_admitted_model_derived_rows_emit_residuals_per_imcc_engine` | **PASS** |
| `tests/battery/test_validity.py::test_full_store_validity_gates_do_not_raise[6]` | **PASS** |

### (5) NOTICE-required: full `tests/battery/test_score.py` on tip — **PASS**

| Suite | Result |
| --- | --- |
| `tests/battery/test_score.py` (full file on `d1179bbf9`) | **159 passed** in 28.42 s |
| Named: `test_uncalibrated_kems_residual_matches_between_oxygen_balance_engines` (t1075 parity + band exclusion) | **PASS** |
| Named: `test_residue_composition_has_its_own_rail_and_typed_engine_refusal` (R1b typed absence / `melt_surface_area_evolution_missing`) | **PASS** |
| Named: `test_uncalibrated_kems_partial_pressure_is_excluded_from_band_population` | **PASS** |

Cross-check: same file on pre-merge tip `9e434da9` collected **158** tests; tip adds the t1075 parity test (+1) while retaining R1b's residue typed-absence expectations — merge of the two `test_score.py` edits is semantically complete.

## Targeted tests (VPS) summary

| Suite | Result |
| --- | --- |
| `test_migrate.py::test_residue_cells_keep_run_identity_and_printed_conditions` | **1 passed** |
| `_composition_from_plain` Gd-in-oxides mutation | **raises as required** |
| Bounced trio (validate serial + score admitted + validity[6]) | **3 passed** |
| `tests/battery/test_score.py` full | **159 passed** |

Not run (policy/cost): full W3, tip full-store regen, bulk worker 865/969 suites. Mac Studio ASK for regen+load after tip land if train wants that green gate.

## Verdict

**LAND `d1179bbf9da452d13334502210a1d4b3dbfd2fca`**

No P1 REVISE. Tip is a clean merge of already-reviewed R0-fix+R1a/R1b batch with t1075 green; both `test_score` concerns (parity band exclusion + residue typed absence) pass together. Residual operational note only: tip-code whole-store regen deferred to Mac Studio (VPS policy), not a code blocker — on-disk store validate passed serial on this box.

— regolith-empirical

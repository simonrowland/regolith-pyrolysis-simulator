# REVIEW — residue-regen + residue-r3 combined (shared base)

- **Reviewer:** regolith-empirical (frontier of record)
- **Seat:** `/workspace/repos/wt/slot-y17`
- **Tips (COMBINED scope):**
  1. **regen:** `d9a06fcbb041f884e64c08017d5abb1d4bb0815b` on `review/residue-regen`
  2. **R3:** `50da4a5e9c300b03b770945640076e4d6ab709f6` on `review/residue-r3` (prior solo LAND retained; this review ADDS regen)
- **Shared base:** `b13a98ae04cc1bc8fb98ba3b5686f399ff66c325` — confirmed `git merge-base --is-ancestor` for both; both parents equal base
- **Merge offer:** `merge(d9a06fcbb, 50da4a5e9)` — `git merge-tree --write-tree` → `883e7e71c3cf27795855e1800e66260cffff026c`; no conflict markers; **no overlapping paths**
- **NOTICE:** `/workspace/ferry-inbox/NOTICE-residue-regen-from-regolith-physics-2026-10-02.md`
- **Regen report:** `/workspace/ferry-inbox/regolith-physics-residue-2026-10-02/regen-report.md`
- **Prior R3 REVIEW (context):** `REVIEW-residue-r3-50da4a5e9-2026-10-02.md` (LAND; attacks 1–5 PASS)
- **Date:** 2026-10-02 ~19:09–19:25 ET
- **Mode:** read-only for product code on review; no extract invention; no force-push; no Mac listen pools. Targeted VPS unit tests only (no full W3 / full-store / full pytest). Full regen not re-run on ~16GB VPS — sample-diff attack used instead.

## Scope

### regen (`d9a06fcbb`)

ONE commit on `b13a98ae0` regenerating derived siblings the R0 identity fix (`138be92af` Fix Sossi residue identity composition — touched only `simulator/battery/migrate.py` + `tests/battery/test_migrate.py`) never regenerated:

| Path | Δ (vs base) |
| --- | ---: |
| `data/literature/extracts-v2/kems-012-sossi-2019.yaml` | +22453 / −4783 (stat); seat measured 612 obs / 44 unique experiment_id unchanged |
| `data/literature/extracts-v2/kems-015-hashimoto-1983.yaml` | +31509 / −5253 (stat); seat measured 681 obs / 27 unique experiment_id unchanged |
| `data/battery/migration-report.md` | +2 / −2 (`identity_incomplete` census 216465 → 216801) |

### R3 (`50da4a5e9`) — spot-check only

Hashimoto time-refinement (N/2N step doubling). Files: `simulator/battery/residue.py`, `tests/battery/test_hashimoto_residue_predictions.py`. Prior LAND attacks not re-litigated; seat confirmed tip still at claimed SHA, constants intact, targeted suite green.

## Attack results — regen

### (1) Fetch + tip + merge-base + merge-tree — **PASS**

- Fetched `origin/review/residue-regen` → `d9a06fcbb041f884e64c08017d5abb1d4bb0815b`; `origin/review/residue-r3` → `50da4a5e9c300b03b770945640076e4d6ab709f6`.
- Merge-base both tips vs `b13a98ae0` = `b13a98ae0`; both one-commit children of base.
- `git merge-tree --write-tree d9a06fcbb 50da4a5e9` clean; `comm` of changed paths empty (yaml/report vs residue.py/tests).

### (2) Per-source counts + observation_ids unchanged — **PASS**

| Source | obs before/after | unique experiment_id | ids stable |
| --- | ---: | ---: | --- |
| Sossi (`kems-012`) | 612 / 612 | 44 / 44 | yes |
| Hashimoto (`kems-015`) | 681 / 681 | 27 / 27 | yes |

Zero observation_ids gained or lost. Content-hash suffixes (`:h=<hex>`) present on 652 Hashimoto + many Sossi ids; all ids unchanged (no re-keying).

### (3) Sample-diff: identity / sibling fills only; values/locators/point ids stable — **PASS**

Seat compared base↔regen observation dicts (Python yaml load of both revs).

**Sossi:** 90 unchanged; **522** changed. After stripping identity sibling keys (`composition`, `fO2_Pa`, `total_pressure_Pa`, `exposure`, `sample_mass_kg`) + `derivation`: **truly_other = 0**. All 522 are identity-sibling fills (composition null→`printed_oxides` mass_percent SiO2/Al2O3/MgO/FeO/CaO plus related identity env fields). `point_conditions` including `starting_component_ppm` **unchanged**. `observation_id` / `experiment_id` / `locator` / `value` / `uncertainty` unchanged on sampled residues.

Sample (3 Sossi residue obs, all with `:h=` suffixes):

| observation_id (short) | identity.composition before → after | starting_component_ppm |
| --- | --- | --- |
| `…T=1573.15:h=0508e4f70bb9` | null → printed_oxides (SiO2 40.6 … CaO 16.82) | same (1060.6 ppm Gd) |
| `…T=1573.15:h=20ab7a6c2ef9` | null → same printed_oxides | same |
| `…T=1573.15:h=43bdf92af791` | null → same printed_oxides | same |

**Hashimoto:** 33 unchanged; **648** changed. All 648 get identity.composition null→`printed_oxides` (mole_fraction FCMAS). Also regenerator fills/rewrites `point_conditions.VF_wt_pct` only: 528 newly added; 120 locator-note rewrites with **state.value unchanged** (e.g. VF 10.8→10.8). `starting_component_ppm` same wherever present. After stripping identity siblings + derivation + VF_wt_pct: **truly_other = 0**. No value/locator/observation_id churn on measured fields.

Sample (3 Hashimoto table-3 residue obs):

| observation_id (short) | composition | VF_wt_pct state.value | starting_component_ppm |
| --- | --- | --- | --- |
| `…row=c3-(2):h=5dc76e0bf59e` | null→printed_oxides mol frac | null→10.8 | same |
| `…row=c5-(1):h=a6be4fbae4e4` | null→printed_oxides | null→18.6 | same |
| `…row=c5-(3):h=7ca9353d79f4` | null→printed_oxides | null→19.7 | same |

NOTICE wording “printed oxides only; trace start in point_conditions.starting_component_ppm” holds for the identity.composition claim; Hashimoto also gains systematic VF_wt_pct point_condition fills from the same regenerator (values stable where previously present). Not a hand-edit of measured residue compositions.

### (4) No sibling hand-edited — **PASS**

- Commit touches **exactly** the three claimed derived files (no migrator/code/test edits in this tip).
- Changes are cohort-wide systematic fills (522 / 648), not cherry-picked row edits.
- observation_ids (including `:h=` content-hash suffixes) preserved → no re-key; consistent with regenerator rewriting derived identity siblings under stable ids.
- R0 (`138be92af`) only landed migrate.py + tests; this tip is the deferred sibling regenerate.

### (5) R2 predictions / starting compositions — **PASS (indirect)**

Predictor reads raw starting compositions. Seat confirmed `point_conditions.starting_component_ppm` unchanged on all sampled Sossi/Hashimoto residue observations. No prediction delta expected; aligns with regen-report “48 predicted rows did not move.” Full predictor re-score not run on VPS (policy).

### (6) Oxygen-balance env artefact / pin — **PASS**

Shared venv OpenIMCC `direct_url.json` commit_id = `afcb5d80abb16d931174a5a587e4ffaee6cbcadc` (matches `pyproject.toml` / `OPENIMCC_RECORDED_PIN`). Targeted oxygen-balance suite **PASS** on regen tip (see below). Earlier 0.8286-vs-0.7993 failure attributed to loading openimcc `be41a6d` is consistent with pin-vs-newer gas model; seat did not load `be41a6d`.

### (7) Full store load 263/3518/121085 — **NOT RE-RUN on VPS**

Claim accepted from regen-report; Mac Studio full-suite / corpus validate **ASK** noted in STATUS if physics wants a green gate. Sample-diff + targeted tests sufficient for frontier LAND under empirical VPS policy.

## Attack results — R3 spot-check (no regression)

| Check | Result |
| --- | --- |
| Tip still `50da4a5e9` on seat | **PASS** |
| `_HASHIMOTO_N_CAP=256`, `_HASHIMOTO_REFINEMENT_TOLERANCE_WT_PCT=0.05`, `residue_time_refinement_unconverged` / `unconverged_at_cap` present | **PASS** |
| `tests/battery/test_hashimoto_residue_predictions.py` + `test_residue_oxygen_balance.py` | **15 passed** in ~36 s |
| Prior LAND attacks (1)–(5) | not re-run; no cause to regress |

## Targeted tests (VPS, regen tip `d9a06fcbb`)

| Suite | Result |
| --- | --- |
| `test_residue_oxygen_balance.py` | PASS (incl. Hashimoto vacuum oxygen balance on pin) |
| `test_hashimoto_residue_predictions.py` | PASS |
| `test_printed_fo2.py` | PASS |
| `test_sweep_gas.py` | PASS |
| `test_validity.py` | PASS |
| `test_hashimoto_composition.py` | PASS |
| `test_printed_point_conditions.py` | PASS |
| **Total** | **110 passed** in ~156 s |

Not run (policy/cost): full W3, full-store score, Mac Studio full pytest, live full extract regenerate.

## Verdicts

- **LAND `d9a06fcbb041f884e64c08017d5abb1d4bb0815b`** (regen)
- **LAND `50da4a5e9c300b03b770945640076e4d6ab709f6`** (R3; prior LAND confirmed)

No P1 REVISE. Optional P2 awareness: (a) Hashimoto VF_wt_pct locator-note rewrites / fills are regenerator point_condition siblings beyond the printed-oxides identity headline — values stable; (b) full-store 263/3518/121085 and worker’s “83+9+1” suites not re-hosted here — Mac Studio ASK if a corpus green gate is required before merge; (c) merge tree clean for `merge(d9a06fcbb, 50da4a5e9)`.

— regolith-empirical

# REVIEW — delta re-review: review/stolyarova-1995-extract

- **Reviewer:** regolith-empirical (frontier of record)
- **Seat:** `/workspace/repos/wt/slot-z2` @ tip (`.slot-busy` cleared after this review)
- **Tip:** `b64efca6fffae933da35d5070f7fa09a4218571d` (parent merge on green `4b78ee02192dd15c922ff194c674a4931da5bb24`)
- **Commit:** `test: pin Stolyarova diagnostics by source id` — **TEST-ONLY**
  (`tests/battery/test_score.py` only; no extract YAML)
- **Date:** 2026-10-01 ~08:28 ET
- **Mode:** read-only; extract not edited; review tip not pushed to green
- **Corpus:** prior kit
  `/workspace/ferry-inbox/reviews/_req-2026-09-30/stolyarova-1995/`
  (PDF + MinerU OCR + `_audit/`)
- **Prior:** `REVIEW-stolyarova-1995-extract-2026-09-30.md` (LAND `dacb200ff…`; census 54 / 50 / 9)
- **REQ:** `REQ-delta-review-stolyarova-1995-b64efca6-2026-10-01.md` — attack census 26, assertion strength, diagnostic-set membership, migrate.py

## Diff scope

`git diff 4b78ee021 b64efca6f` → **1 file**, `tests/battery/test_score.py` (+40/−14).

- Selectors tightened from source-name **substring** → exact `obs.source_id`.
- `kems-053-stolyarova-1991` stays pinned at **9** admitted model-derived pressure rows.
- New exact set pins `stolyarova-1995-cao-alumina-kems` at **26**.
- Pre-1995 headline diagnostic union still asserts `== 91`; with 1995 `== 117` (91+26).
- Per-engine Stolyarova residual count `130 → 180` (= +50 = 24 measured + 26 derived from the new source under the same engine loop).
- Extract YAML unchanged (`data/literature/extracts{,-v2}/stolyarova-1995-cao-alumina-kems.yaml` identical at parent vs tip).

## Attack (1) — is 26 right vs prior 54 / 50 / 9?

Own v2 census of `stolyarova-1995-cao-alumina-kems` (read-only):

| Bucket | Count | Notes |
| --- | --- | --- |
| Eqn 1 `p_O` model_derived admitted | **9** | oid series `…_o_partial_pressure_eqn1_table2` |
| Eqn 2 `p_O` model_derived admitted | **8** | dash at x(CaO)=1.000 — absent, matches prior LAND |
| calculated `p_O2` model_derived admitted | **9** | |
| **Derived O / O2 total** | **26** | = 9+8+9 |
| Measured Ca / Al / AlO | 8+8+8 = **24** | |
| Admitted numeric Table 2 pressures | **50** | 24+26 |
| Em-dash cells absent | **4** | |
| Table 2 cells | **54** | 50+4 |
| Compositions | **9** | unchanged |

**Reconcile:** prior LAND “Derived O / O2” described the *typing* of calculated columns, not a count of 54. The **54 / 50 / 9** census is Table2-cells / numeric-admitted / compositions. The **26** are exactly the model-derived subset of those 50 numerics. REQ claim matches extract.

## Attack (2) — assertion weakened?

**No.** Every length pin remains exact `==` (9, 26, 91, 117, 54, 180, …). No equality→range. Selectors tightened to exact source ids (not loosened to counts-of-ids). Comment documents Eqn1/Eqn2/O2 split.

## Attack (3) — should the 26 sit in headline diagnostic references?

**Yes — and their presence does not mean the extract over-admits calculated values.**

- Extract types them `evidence.class = model_derived` with derivation parents on measured Ca/Al/AlO series (prior LAND already cleared this).
- They are **not** typed as measured pressures; table headers “Calculation using Eqn 1/2” match.
- Same diagnostic pattern as `kems-053`’s 9 derived pressures already in the set.
- Test asserts all 26 emit **REFUSED** OpenIMCC residuals and are **not** `score_eligible` — diagnostic references, outside the measured score-eligible headline.

Including them in `headline_diagnostic_references` is the correct residual-emission pin after the substring collision (35≠9) that motivated this tip.

## Attack (4) — migrate.py failures naming 1995?

Worker-reported: “unresolved experiment references”, “unavailable figure values missing from the queue”. Studio pre-gate did not flag them as **new** on this tip — agree.

On this tree (tip = LAND extract + TEST-ONLY):

1. **Unavailable figure values missing from queue — real queue gap, not extract digit defect.** Six `figure_only` / `typed_refusal` rows carry `value.kind: unavailable`. `data/battery/migration-queue.yaml` has **zero** `stolyarova-1995` mentions; none of those six oids are queued. That would fail `test_l05c5_store_unavailable_values_are_queued` if run against this store. Cause: migration-queue freshness lag after the LAND merge, **not** invented figure digits. Prior LAND explicitly kept Figs 1–4 as placeholders.

2. **Unresolved experiment references — placeholder-scope id form, not Table 2 grid.** Admitted 50 pressure rows use `…::experiment::stolyarova-1995-mo-cell`. The six figure_only rows use source-shaped `…::stolyarova-1995-cao-alumina-kems`. That mismatch can surface as unresolved experiment refs for placeholders only.

**Neither is introduced by this TEST-ONLY commit** (extract identical to parent merge). Not blocking P1/P2 for landing the pin. Queue regenerator / placeholder `experiment_id` hygiene are follow-ups outside this delta tip; do not edit the extract in this review.

## P1 / P2

- **P1:** none (for this tip)
- **P2:** none blocking this tip. Non-blocking notes only: regenerate migration-queue for the six figure_only unavailable rows; optionally align figure_only `experiment_id` to `experiment::stolyarova-1995-mo-cell`.

VERDICT: LAND b64efca6fffae933da35d5070f7fa09a4218571d

— regolith-empirical

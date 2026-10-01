# REVIEW — delta3 re-review: review/stolyarova-1996-extract

- **Reviewer:** regolith-empirical (frontier of record)
- **Seat:** `/workspace/repos/wt/slot-z15` @ tip (`.slot-busy` cleared after this review)
- **Tip:** `7f66b915a383c91c6242ada452836f80c90fe69a` (parent merge `c421ff2152becf6f4105d2a2c806f78d95a339a4`)
- **Commit:** `test: pin Stolyarova 1996 derived score rows` — **TEST-ONLY**
  (`tests/battery/test_score.py` only; no extract YAML)
- **Date:** 2026-10-01 ~12:51 ET
- **Mode:** read-only; extract not edited; review tip not pushed to green
- **Policy gate:** use-values-first / wrong-number guards; do not invent data
- **Corpus:** `/workspace/ferry-inbox/reviews/_req-2026-09-30/stolyarova-1996/` (PDF + MinerU OCR + `_audit/`)
- **Prior:** `REVIEW-stolyarova-1996-delta2-2026-10-01.md` (LAND `6bef95098…`)
- **Analogous:** `REVIEW-stolyarova-1995-delta-2026-10-01.md` (TEST-ONLY source-id pin pattern)
- **REQ:** `REQ-delta3-review-stolyarova-1996-7f66b915-2026-10-01.md` — pin 55 = 30 pO + 10 p′O + 15 p″O; exact equality; diagnostic-set membership

## Tip shape

| ancestor | role | present |
| --- | --- | --- |
| `0974bcea4` | green | **yes** (ancestor of tip) |
| `4ccee2108` | Stolyarova 1995 offer LAND (incl. exact-source-id rewrite; via `b64efca6f`) | **yes** |
| `6bef95098` | LAND extract tip (delta2) | **yes** |
| `c421ff215` | parent merge (1996 extract on 1995 offer) | **yes** (= tip^) |
| `7f66b915a` | ONE test-only commit | **yes** |

Shape matches REQ. **PASS.**

## Diff scope

`git diff c421ff2152 7f66b915a` → **1 file**, `tests/battery/test_score.py` (+57/−2).

- Extract YAMLs unchanged vs parent merge (`data/literature/extracts{,-v2}/stolyarova-1996-…` blob-identical; zero bytes in `git diff` under `data/`).
- No works sidecar / other sources touched.

**PASS.**

## Attack (1) — census 30 / 10 / 15 vs extract

Own v2 census of `stolyarova-1996-cao-alumina-silica-kems` at tip (read-only; 312 exploded observations):

| Bucket | Count | Notes |
| --- | --- | --- |
| `stolyarova_1996_table2_pO_1933k` model_derived admitted | **30** | species O(g), quantity `p_partial`, T=1933 K |
| `stolyarova_1996_table2_pO_prime_1933k` model_derived admitted | **10** | same |
| `stolyarova_1996_table2_pO_double_prime_1933k` model_derived admitted | **15** | same |
| **Derived O / O′ / O″ total** | **55** | = 30+10+15 |
| `source_id` | all 55 | exact `stolyarova-1996-cao-alumina-silica-kems` |
| Non-model_derived among these series | **0** | |

**Reconcile:** REQ claim 30 p_O + 10 p′_O + 15 p″_O (Table 2, 1933 K) matches extract exactly. Test pin `== [30, 10, 15]` and series-id set match. **PASS.**

## Attack (2) — assertion weakened?

**No.** Every prior length pin remains exact `==`:

- allibert 16 / rejected 55; kems-053 activities 54; kems-053 derived pressures 9; 1995 derived 26; kems_model_derived 28; pre-1995 union 91 — **unchanged**.
- Headline diagnostic union `117 → 172` (= 117+55). Stolyarova residual count `180 → 235` (= 180+55). Both exact `==`, not ranges.
- New pins: `len(stolyarova_1996_derived_pressures) == 55`, series-id set equality, per-series `[30, 10, 15]`, per-engine `stolyarova_1996_rows == 55`.

Selectors are exact `obs.source_id == "stolyarova-1996-cao-alumina-silica-kems"` (same tightening pattern as 1995). **PASS.**

## Attack (3) — diagnostic-reference set + REFUSED residuals

**Yes — same pattern as 1995.**

- Extract types all 55 `evidence.class = model_derived` (not measured). Identity quantity `p_partial`, species O(g), temperature_K 1933.
- Tip adds them to `headline_diagnostic_references` alongside 1991/1995 derived pressures.
- Test asserts all 55 emit **REFUSED** OpenIMCC residuals, are **not** `score_eligible`, and refusal reasons ⊆ `{IDENTITY_INCOMPLETE, EFFUSION_REGIME_UNVERIFIED}` — diagnostic references outside the measured score-eligible headline.

Including them in the headline diagnostic set is the correct residual-emission pin after the extract LAND. **PASS.**

## Attack (4) — policy / invent-data

- TEST-ONLY commit; no extract numbers invented or moved.
- Wrong-number guards not implicated (values unchanged vs parent; prior delta2 already cleared measured Mo ions and O-block values).
- Full W3 / store residual recompute not run on this VPS (~16GB); census + pin arithmetic + diff review suffice for this tip gate.

**PASS.**

## P0 / P1 / P2

- **P0:** none
- **P1 (wrong-number):** none
- **P2:** none blocking this tip

VERDICT: LAND 7f66b915a383c91c6242ada452836f80c90fe69a

— regolith-empirical

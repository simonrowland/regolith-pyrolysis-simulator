# Z3 — FIDELITY AUDIT kems-042-plante-1979 — 2026-09-22

**Repo:** regolith-pyrolysis-simulator  
**Branch:** `empirical/z3-kems-042-plante-1979-2026-09-22`  
**Base:** `2e9e17c3d` (shared Z tip)  
**Tip (post-fix):** `0e2b2dd6993de14a6848eef1d57f6a5913f0a58f`  
**Worktree:** `/workspace/repos/wt/slot-z3`  
**PDF:** `/workspace/ferry-inbox/from-main-B4-20260923T031628Z/audit-pdfs/kems-042-plante-1979.pdf` (NBS SP 561, 17 PDF pp = published 265–281). **Never committed.**  
**Extract:** `data/literature/extracts/kems-042-plante-1979.yaml`  
**Date:** 2026-09-22 ~23:35 EDT (America/Toronto)

## Verdict

**P0: 1** — sample-start composition trap (same class as E5/X5) — **fixed and pushed**.  
**Numeric Table 2 fidelity: PASS** — sampled ≥108 cells (95 full p.276 GT + 13 later-page targets + 221 quote↔values self-checks); **0 wrong numbers**.  
**Coverage:** Table 2 complete (221/221 vs Table 3 No. Pts.); Table 1/3 intentionally not landed as measurements.  
**READY for V-style verify** on tip `0e2b2dd69`.

## Inventory

| Layer | Count |
|---|---|
| Nested `species.*.observations` | **383** (K: 221 + K2O: 162) |
| `species.K` | 221 Table 2 `P_K(atm)` rows (162 homogeneous + 59 two-phase `a`) |
| `species.K2O` | 162 vapor-reference activities (eq. 7); homogeneous only |
| Experiments | 1 (`k2o-sio2-effusion-series`) — correct (one K2Si2O5 charge; series are analysis groups) |
| Tables on locators | Table 2 pp. 276–278 |
| Series | 1104, 1110, 1115, 1122, 1123, 1126, 1129, 1214 |

## Method

1. `pdftotext -layout/-raw` + `pdftoppm` 250–400 dpi of PDF pp. 12–14 (published Table 2 pp. 276–278) and Table 3 (p. 278).
2. Census YAML: 383 observations; series counts; quantity/unit/locator maps.
3. Build ground-truth for **all of p.276 readable rows** (1104×28 + 1110×38 + 1115×26 + 1122×3 = 95) from raw OCR left columns + visual right columns; cell-match vs YAML `T`, `Wt % K2O`, `P_K`.
4. Spot-check ≥13 rows across pp. 277–278 (1123 / 1126 / 1129 / 1214 incl. two-phase `a` and series ends).
5. Verify every K `quote` field ↔ `values` (221/221); Table 3 No. Pts. ↔ series lengths; activity `A = P_K²·(0.226·P_K)^{½}` for all 162 K2O rows.
6. Check sample-start vs §3 Procedure / abstract / Table 2 running-oxide trap → P0 fix.

## P0 summary

**P0 count: 1** — fixed on branch.

| # | Finding | Fix |
| --- | --- | --- |
| P0-1 | Pre-fix tip: experiment `sample.printed_composition` = abstract “43.9 to 11.9 wt% K2O”; **324** observation `equipment.sample.printed_composition` maps = Table 2 Series 1104 first-row `K2O: 43.94` / invented binary `SiO2: 56.06` with locator `table: '2'`. Table 2 `Wt % K2O` is **running** composition from integrated ³⁹K ion current (abstract + §3), not start. Printed start is crystalline K2Si2O5 (solid-state @ 800 °C; XRD), p. 271. | Replaced experiment + 324 observation bags with `crystalline K2Si2O5`; locator → p. 271 §3 Procedure with explicit Table 2 / abstract refusal. Per-row `values.composition_wt_pct` **unchanged**. Commit `0e2b2dd69` (byte-identical to E5 `906147d3e` extract). |

## Sampled checks (≥20)

### Table 3 series census — **8/8 OK**

| Series | Table 3 No. Pts. | YAML K rows | K2O wt% range (Table 3) |
|---|---:|---:|---|
| 1104 | 28 | 28 | 43.9–40.4 |
| 1110 | 38 | 38 | 40.3–34.4 |
| 1115 | 26 | 26 | 34.4–29.4 |
| 1122 | 18 | 18 | 29.3–26.5 |
| 1123 | 31 | 31 | 26.5–21.1 |
| 1126 | 23 | 23 | 21.1–16.6 |
| 1129 | 20 | 20 | 16.6–11.9 |
| 1214 | 37 | 37 | 11.9–6.8 |
| **Total** | **221** | **221** | |

Master row 1104–1129 = 184 = 221 − 37 (1214) — consistent with author grouping.

### Table 2 p.276 — **95/95 cells OK** (full-page GT)

| Series | n checked | First printed | YAML | Last printed | YAML |
|---|---:|---|---|---|---|
| 1104 | 28 | 1302 / 43.94 / 6.91E-7 | same | 1396 / 40.36 / 2.16E-6 | same |
| 1110 | 38 | 1352 / 40.34 / 7.58E-7 | same | 1289 / 34.36 / 1.43E-7 | same |
| 1115 | 26 | 1259 / 34.36 / 1.00E-7 | same | 1292 / 29.35 / 1.22E-7 | same |
| 1122 | 3 (page start) | 1294 / 29.34 / 9.48E-8 | same | 1369 / 29.34 / 3.00E-7 | same |

Mid samples also OK (e.g. 1115 @ 1404 / 34.34 / 1.53E-6 — matches deepening parent-discrepancy quote; 1110 @ 1720 / 36.00 / 1.77E-4).

### Table 2 pp.277–278 spot sample — **13/13 OK**

| note | printed | YAML | two-phase |
|---|---|---|---|
| 1123 start | 1337 / 26.50 / 2.62E-7 | same | — |
| 1123 mid | 1770 / 22.55 / 1.30E-4 | same | — |
| 1123 end | 1298a / 21.14 / 1.04E-7 | same | `a` |
| 1126 start | 1368a / 21.13 / 3.33E-7 | same | `a` |
| 1126 mid | 1783 / 19.19 / 1.37E-4 | same | — |
| 1126 end | 1310a / 16.62 / 9.59E-8 | same | `a` |
| 1129 start | 1373a / 16.60 / 3.64E-7 | same | `a` |
| 1129 mid | 1800 / 13.93 / 1.65E-4 | same | — |
| 1129 end | 1608a / 11.92 / 1.47E-5 | same | `a` |
| 1214 start | 1315a / 11.86 / 1.29E-7 | same | `a` |
| 1214 mid | 1672a / 10.16 / 2.11E-5 | same | `a` |
| 1214 mid | 1738a / 7.88 / 4.00E-5 | same | `a` |
| 1214 last | 1335a / 6.76 / 1.01E-7 | same | `a` |

### Quote ↔ values integrity — **221/221 OK**

Every `species.K` `quote` (`T | K2O | P` / `Ta | …`) matches `T_K_as_published`, composition, `P_K_atm_as_published`, and `two_phase_marker`.

### Units / basis / sign / conditions

| Check | Result |
|---|---|
| `P_K` unit | `atm` as printed — OK |
| Composition basis | wt% K2O as printed column — OK |
| Sign | all P_K > 0; activities > 0 — OK |
| T | Kelvin as printed — OK |
| fO2 / P_O2 | not a printed Table 2 column; derived via stated `P_O2 = 0.226 P_K` (p. 279) on K2O rows — OK |
| Row→experiment | all 383 → `k2o-sio2-effusion-series` — OK (one charge; series = analysis groups per §3 / Table 3) |
| Locator | `published_page` 276–278, `table: '2'` — OK |

### K2O activity arithmetic — **162/162 OK**

`A = P_K² · (0.226·P_K)^{½}` (eq. 7 + stated ratio); `P_O2_atm = 0.226·P_K`. Example Series 1104 T=1302: P_K=6.91e-7 → A=1.886902e-16 — match. Homogeneous-only (59 two-phase K rows correctly lack K2O activity successors).

### Sample start (post-fix) — OK

| host | field | landed | printed |
|---|---|---|---|
| experiment sample | label | `crystalline K2Si2O5` | §3 p. 271 crystalline K2Si2O5 |
| 324× equipment.sample | label | `crystalline K2Si2O5` | same; Table 2 refused |
| 324× form | form | `crystalline K2Si2O5` | same (pre-existing) |
| per-row `values.composition_wt_pct` | running | Table 2 Wt % K2O (+ SiO2 complement on K2O parents) | running ion-current — **kept** |

## Coverage

| Printed asset | In extract? | Notes |
|---|---|---|
| Table 2 all series 1104–1214 | **Yes** — 221/221 | Deepening completed earlier omission of 1214 + two-phase |
| Table 2 two-phase `a` rows | **Yes** — 59 | Marker + pressure-only; no K2O activity |
| Table 1 mass-spec constants | metadata only | Not numeric observations (calibration) |
| Table 3 LS fit coeffs | **No** (intentional) | Model fits, not measurements; No. Pts. used as census oracle |
| Figure 3 log P vs 1/T | **No** | Same points as Table 2; not re-digitized |
| Eq. 7 activities | **Yes** — 162 | Derived from printed P_K + stated 0.226 ratio |
| Eq. 9 liquid-K2O scale | **No** | `K_eq` not tabulated — typed absence in extraction notes |

## Non-P0 notes (no fix)

1. **SiO2 complement** on 162 K2O `values.composition_wt_pct` (and historically on the bad start maps) is **not a Table 2 column** — binary remainder. Documented; left intact per E5/X5 policy.
2. **Anomalous printed row** Series 1123 `1469 | 24.46 | 2.64E-6` (breaks monotonic K2O trend between 26.48 and 26.40). pdftotext + visual agree on **24.46**; YAML matches print. Possible original typesetting typo — **not** an extract P0.
3. **59 two-phase** observations still lack `equipment.sample` bags entirely (structural; pre-existing). Not numeric P0.
4. **Experiment `form`** still “Dried K2O-SiO2 melt” (p. 269) while start label is crystalline K2Si2O5 — both appear in §3 (prepare crystal → dry/melt for run); left as-is.
5. File still **byte-identical** to E5 tip extract after this fix; Z3 re-grounds the same landing on the shared Z base for I2.

## Commits

| SHA | Message |
|---|---|
| `0e2b2dd69` | extracts: refuse Plante 1979 Table 2 running oxides as sample start |

PDF path used for audit only — **not** in the commit.

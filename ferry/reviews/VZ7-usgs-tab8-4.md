# VZ7 — independent VERIFY of Z23 USGS tab8-4 P0 fixes

**When:** 2026-09-23 ~00:54 EDT (America/Toronto)  
**Seat:** VZ7 — different agent from Z23 fixer (read-only on product tip; no PDF in git; no force-push; no rewrite of fix tip)  
**Null hypothesis:** claimed tab8-4 cell repairs are false positives (misread PDF / already correct / wrong replacement) → OVERTURN  
**Fix under verify:** `79ebc63c68dfdea43f01552665a751f1832eb511` on `origin/empirical/z23-usgs-tab8-1-4-2026-09-23`  
**Base / parent:** `origin/work-v064-green` = `2e9e17c3d138fdfba9269c493f82974a71153fa5` (= tip^; FF, single product commit)  
**Fixer write-up:** `/workspace/ferry-inbox/reviews/Z23-usgs-tab8-1-4.md`  
**PDFs (not in git):** `/workspace/ferry-inbox/from-main-B5-20260923T041100Z/audit-pdfs/`  
  - `usgs-lunar-sourcebook-tab8-4.pdf` sha256 `449159beaab4458f303140698b35587b4eba74dad092b6645da4d81cddb914fd`  
  - `usgs-lunar-sourcebook-tab8-1.pdf` sha256 `d3be9250240acbc52ca7731d155ad747c0828bc4cd20ab0c1ffa5a91cd15828a`  
**Scratch:** `/workspace/ferry-inbox/reviews/_vz7_audit/` (fresh `pdftotext -layout` + `pdftoppm` 200 dpi; not for commit)

| Claim | Tip | This seat |
| --- | --- | --- |
| tab8-4: **61** P0 cell repairs (bleed / left-shift / sparse swap / missing restore) | `79ebc63c6` | **CONFIRM 61/61** vs independent PDF GT |
| tab8-1: **781/781** clean P0=0 | unchanged on tip | **CONFIRM 14/14 spot — 0 overturns** |
| tab8-2 | Z18 / VZ6 | skipped (out of scope) |

**TL;DR:** **CONFIRM.** Tip is **I2-eligible**. No correcting commit on VZ7. Did not force-push / rewrite Z23 tip.

---

## Method

1. Fresh `pdftotext -layout` + `pdftoppm -png -r 200` of tab8-4 (3 pp) and tab8-1 (spot).
2. Tip vs parent YAML for `data/literature/extracts/usgs-lunar-sourcebook-tab8-4.yaml` (only product file in `79ebc63c6`).
3. Re-check **every named claim example** in Z23 write-up vs PDF (layout + PNG crops for sparse siderophile columns).
4. Stratified check across fix classes (`bleed_remove` / `column_bleed_or_misassign` / `missing_cell_restore`) and sample groups; independent PDF GT for **all 61** ledger ops (≥20 additional required; full ledger exceeded).
5. Spot ≥10 tab8-1 cells across MBAS / highland / S&RB / BX / AMET — expect 0 overturns.
6. `python3 tools/validate_literature_extracts.py` on both YAML paths — **OK**.
7. Did **not** amend / force-push `empirical/z23-usgs-tab8-1-4-2026-09-23`.

Ledger labels from fixer `_z23_audit/tab84/json/p0_fixes.json` (n=61); PDF authority is this seat’s layout + visual, not the fixer’s reparse alone.

---

## Named claim examples (all CONFIRM)

### 1. Apollo 12 MBAS Os←Ir|Au bleed (`2917`)

Layout N-row token `2917` sits in the Ir/Au region; Average/Std/Min/Max show **two** rightmost values under Ir/Au with **Os blank**. Au has full avg/std/min/max, so Au N cannot be absent → `2917` = Ir **29** | Au **17** glued. Os N/avg/std/min/max correctly cleared.

| locus | parent | tip | PDF |
| --- | --- | --- | --- |
| N Os | `2917` | absent | blank |
| N Ir | absent | `29` | 29 |
| N Au | absent | `17` | 17 |
| average Ir / Au | Ir=`0.024` (Au lost) | Ir=`0.060` Au=`0.024` | 0.060 / 0.024 |
| min Ir | `.0046` (shifted) | `0.00190` | 0.00190 (trailing layout period dropped — P1 glyph cleanup) |

### 2. Apollo 11 S&RB Ru blank left-shift

PNG + layout: Ru column entirely blank; Pd→Au print as Pd=4 / 10.5 / …, Re=4 / 0.74 / 0.07 / …, Os=3 / 7.83 / …, Ir=9 / 8.6 / …, Au=11 / 2.9 / …. Parent had Ru←Pd and lost Au. Tip restores.

### 3. Luna 24 MBAS Ir←W

W column N=`1` Average=`30`; Ir blank. Parent stored under Ir. Tip moves to W.

### 4. Pd/Re/Os sparse swaps

| group | repair | PDF |
| --- | --- | --- |
| Apollo 14 S&RB std | Pd `0.14` → Re `0.14` | Re std 0.14; Pd std blank |
| Luna 16 S&RB | Ir std `0.5` → Au `0.5`; Pd min/max `3.4`/`3.7` → Re | Au std 0.5; Re min/max 3.4/3.7; Pd min/max blank |
| Luna 20 S&RB | Pd N/avg/min/max → Re | Re N=2 avg/min/max=1.04; Pd blank |
| Luna 24 S&RB | Ir avg `7.7` → Os `7.7`, Ir `6.9`; Os N=`1` | Os=7.7 Ir=6.9 |

### 5. AMET S&RB W max `<130` restore

PDF Maximum row prints `<130` under W (Average already had `<130`). Parent missing max; tip restores. Fidelity pin on Average W `<130` still valid.

**Named examples: 17/17 CONFIRM.**

---

## Stratified / full ledger vs PDF

| reason (ledger) | n | PDF-checked | overturns |
| --- | ---: | ---: | ---: |
| `missing_cell_restore` | 24 | 24 | **0** |
| `column_bleed_or_misassign` | 20 | 20 | **0** |
| `bleed_remove` | 17 | 17 | **0** |
| **TOTAL** | **61** | **61** | **0** |

Groups covered: Apollo 11 S&RB (22), Apollo 12 MBAS (15), Luna 20 S&RB (8), Luna 16 S&RB (6), Luna 24 MBAS (4), Luna 24 S&RB (3), Apollo 14 S&RB (2), AMET S&RB (1).

Tip cell count **678** vs parent **671** (+7 net: restores minus bleed removals). All 61 tip values == ledger `new` == PDF GT. Validator **OK**.

Leading-dot as-printed forms retained (`.0046`, etc.) — match glyph, not digit-strip.

---

## tab8-1 spot (≥10) — expect P0=0

Tip == parent for tab8-1 (commit touches only tab8-4). **781** cells in extract. Independent spots vs layout:

| group | oxide / stat | tip | PDF | OK? |
| --- | --- | --- | --- | --- |
| Apollo 11 MBAS | SiO2 average | 40.46 | 40.46 | yes |
| Apollo 11 MBAS | Na2O maximum | 0.44 | 0.44 | yes |
| Apollo 12 MBAS | SiO2 average | 44.88 | 44.88 | yes |
| Apollo 15 MBAS | Al2O3 average | 10.21 | 10.21 | yes |
| Apollo 17 MBAS | TiO2 N | 26 | 26 | yes |
| Luna 24 MBAS | SiO2 average | 46.0 | 46.00 | yes |
| Luna 24 MBAS | SiO2 std | absent | blank (N=2) | yes |
| Anorthosite | Al2O3 average | 33.4 | 33.4 | yes |
| Norite | FeO average | 8.2 | 8.2 | yes |
| Troctolite | MgO maximum | 30.5 | 30.5 | yes |
| Apollo 11 S&RB | SiO2 average | 41.99 | 41.99 | yes |
| Apollo 12 S&RB | TiO2 average | 2.61 | 2.61 | yes |
| AMET S&RB | MgO N | 6 | 6 | yes |
| Apollo 16 BX | SiO2 average | 45.418 | 45.418 | yes |

**14/14 CONFIRM — 0 overturns.** Supports Z23 “tab8-1 P0=0”.

---

## Notes (not overturns)

- Local worktree branch name `empirical/z22-usgs-tab8-1-4-2026-09-23` also carries tip `79ebc63c6` (+ a later docs-only Z22 write-up commit). **Origin** product tip under verify is `empirical/z23-usgs-tab8-1-4-2026-09-23` @ `79ebc63c6`.
- Commit updates `data/literature/extracts/` only. `extracts-v2/` is a derived battery projection with unavailable closed-quantity values for this compilation (does not re-store Os:`2917`); product path under verify is `extracts/`.
- Apollo 12 N glyph can look like a single `2917` token under Ir; Au’s full statistic set forces the Ir|Au split. Same Z18 bleed mode.

---

## Verdict

**CONFIRM P0 → I2.** Fold tip **`79ebc63c68dfdea43f01552665a751f1832eb511`** into I2.

| Bucket | CONFIRM | OVERTURN |
| --- | ---: | ---: |
| Named claim examples | 17 | 0 |
| tab8-4 ledger vs PDF (all classes) | 61 | 0 |
| tab8-1 spot | 14 | 0 |
| **TOTAL unique checks** | **≥75** | **0** |

No overturn → **no correcting commit**. Did **not** rewrite / force-push Z23 tip. No PDFs in git.

# X5 — EXTRACTION AUDIT: E5 kems-037 / kems-042 / kems-046

**Lane:** E5 (`empirical/e5-kems-037-046-2026-09-22`)
**Worktree:** `/workspace/repos/wt/slot-09`
**Prior:** `/workspace/ferry-inbox/reviews/E5-kems-037-046.md`
**PDFs:** `/workspace/batch-z/pdfs/kems-037-richter-2002.pdf`, `kems-042-plante-1979.pdf`, `kems-046-van-limpt-2007.pdf`
**Tip (pre-fix):** `1a578d86fd4ea8668d9568f162222698fe5ef2e8`
**Tip (post-fix):** `906147d3e7c949b9d682ee7b345d9a375a24ddb6`
**Date:** 2026-09-22 (America/Toronto)

## Scope

Cell-by-cell check of every landed starting composition (value, unit/basis, row/experiment mapping, locator) against the printed PDF. Watch kems-042 starting-vs-running trap (Table 2 `Wt % K2O` is running ion-current composition, not start). Mismatch → P0 + fix on the E branch.

## P0 summary

**P0 count: 1** (kems-042 observation-level trap remnant) — fixed and pushed.

| # | Source | Finding | Fix |
| --- | --- | --- | --- |
| P0-1 | kems-042-plante-1979 | 324× `species.*.observations[].equipment.sample.printed_composition` still landed Table 2 Series 1104 first-row `K2O: 43.94` / invented binary-complement `SiO2: 56.06` as the sample start (locator `table: '2'`). Experiment-level sample was already `crystalline K2Si2O5`, but every observation equipment sample re-introduced the trap. | Replaced all 324 maps with `crystalline K2Si2O5`; locator → published p. 271 §3 Procedure with explicit Table 2 refusal. Per-row `values.composition_wt_pct` running compositions left intact. Commit `906147d3e`. |

## Per-source audit

### kems-037-richter-2002 — PASS

**Printed:** Table 1, GCA p. 525 (PDF p. 5), starting-glass rows `B-133-0` / `B-113-0` / `BCAI-0`, oxide wt%.
**Landed:** three experiments `b-133-evaporation`, `b-113-evaporation`, `bcai-evaporation` `sample.printed_composition`.

| experiment | oxide | landed | printed (row) | match |
| --- | --- | ---: | ---: | --- |
| b-133-evaporation | MgO | 11.94 | 11.94 (B-133-0) | yes |
| | SiO2 | 46.04 | 46.04 | yes |
| | CaO | 22.64 | 22.64 | yes |
| | Al2O3 | 19.30 | 19.30 | yes |
| b-113-evaporation | MgO | 12.67 | 12.67 (B-113-0) | yes |
| | SiO2 | 47.89 | 47.89 | yes |
| | CaO | 19.66 | 19.66 | yes |
| | Al2O3 | 19.78 | 19.78 | yes |
| bcai-evaporation | MgO | 17.84 | 17.84 (BCAI-0) | yes |
| | SiO2 | 37.04 | 37.04 | yes |
| | CaO | 24.61 | 24.61 | yes |
| | Al2O3 | 20.51 | 20.51 | yes |

- **Unit / basis:** oxide wt% as printed — OK.
- **Row mapping:** only `-0` starting-glass rows; residue / run rows unused — OK.
- **Locator:** `page: 525`, `table: '1'` — OK (published page).
- **Quoted spelling retained** (`19.30`, `17.84`, …) — OK.

### kems-042-plante-1979 — PASS after P0 fix

**Printed start:** §3 Procedure, published p. 271 — crystalline K2Si2O5 prepared by solid-state reaction at 800 °C; XRD pattern showed only K2Si2O5. No separate starting oxide wt% assay.
**Trap:** Table 2 `Wt % K2O` is the running composition from the integrated 39K ion current (abstract + §3), spanning ~43.9–11.9 wt% K2O — not a starting assay. SiO2 is not a Table 2 column.

| host | field | landed (post-fix) | printed | match |
| --- | --- | --- | --- | --- |
| `experiments[k2o-sio2-effusion-series].sample.printed_composition` | label | `crystalline K2Si2O5` | crystalline K2Si2O5 (§3) | yes |
| 324× `equipment.sample.printed_composition` | label | `crystalline K2Si2O5` (was `K2O:43.94`/`SiO2:56.06`) | crystalline K2Si2O5 (§3); Table 2 refused | yes (fixed) |
| 324× `equipment.sample.form` | form | `crystalline K2Si2O5` | same | yes (pre-existing) |

- **Not invented (correct):** stoichiometric oxide wt% from formula; abstract range as start.
- **Running compositions kept (correct):** `values.composition_wt_pct` per Table 2 row remains the observation-time running K2O (+ binary SiO2 complement already used in this extract) — distinct from start.
- **Locator (post-fix):** `published_page: 271`, `section: '3. Procedure'`, note refuses Table 2 / abstract range as start.
- **Validator:** still FAIL with pre-existing `equipment.sample: missing value (equipment field must carry value+locator)` on the 324 observation equipment bags (structural; unchanged by this composition fix; same class E5 reported).

### kems-046-van-limpt-2007 — PASS

**Printed:** Table 4.2 thesis p. 141 (PDF p. 142) float/tableware XRF mass%; Table 4.3 thesis p. 142 (PDF p. 143) borosilicate initial XRF/NAA mass%; §4.3.1 sodium-disilicate named melt (no binary wt% assay).

#### Table 4.2 — float / tableware

| experiment | oxide | landed | printed | match |
| --- | --- | ---: | ---: | --- |
| float-glass-without-so3 | SiO2 | 72.85 | 72.85 | yes |
| | Na2O | 13.98 | 13.98 | yes |
| | CaO | 7.46 | 7.46 | yes |
| | MgO | 4.52 | 4.52 | yes |
| | Al2O3 | 0.86 | 0.86 | yes |
| | K2O | 0.22 | 0.22 | yes |
| | Fe2O3 | 0.095 | 0.095 | yes |
| float-glass-with-so3 | SiO2 | 72.16 | 72.16 | yes |
| | Na2O | 14.18 | 14.18 | yes |
| | CaO | 7.88 | 7.88 | yes |
| | MgO | 4.32 | 4.32 | yes |
| | Al2O3 | 0.85 | 0.85 | yes |
| | K2O | 0.20 | 0.20 | yes |
| | Fe2O3 | 0.087 | 0.087 | yes |
| | SO3 | 0.25 | 0.25 | yes |
| tableware-glass-a | SiO2 | 70.1 | 70.1 | yes |
| | Na2O | 10.5 | 10.5 | yes |
| | CaO | 8.00 | 8.00 | yes |
| | Al2O3 | 1.09 | 1.09 | yes |
| | K2O | 4.98 | 4.98 | yes |
| | SO3 | 0.01 | 0.01 | yes |
| | Sb2O3 | 0.22 | 0.22 (not batch 0.30) | yes |
| | BaO | 5.07 | 5.07 | yes |
| tableware-glass-b | SiO2 | 67.2 | (67.2) footnote-1 only | yes |
| | Na2O | 10.0 | (10.0) | yes |
| | CaO | 8.00 | (8.00) | yes |
| | MgO | 0 | (0) | yes |
| | Al2O3 | 0.97 | (0.97) | yes |
| | K2O | 5.03 | (5.03) | yes |
| | Fe2O3 | 0 | (0) | yes |
| | SO3 | 0 | (0) | yes |
| | Cl | 0.42 | (0.42) | yes |
| | Sb2O3 | 0 | (0) | yes |
| | BaO | 8.41 | (8.41) | yes |
| tableware-glass-c | SiO2 | 70.4 | 70.4 | yes |
| | Na2O | 10.4 | 10.4 | yes |
| | CaO | 8.25 | 8.25 | yes |
| | Al2O3 | 1.06 | 1.06 | yes |
| | K2O | 4.67 | 4.67 | yes |
| | SO3 | 0.13 | 0.13 (not 0.40) | yes |
| | Cl | 0.03 | 0.03 (not 0.44) | yes |
| | Sb2O3 | 0.06 | 0.06 (not 0.10) | yes |
| | BaO | 4.98 | 4.98 | yes |

- **Omitted correctly:** cells printed `<0.01`; parenthetical batch duplicates beside measured values.
- **Tableware B:** footnote-1 theoretical parentheticals only — landed as printed theoretical; zeros retained — OK.

#### Table 4.3 — borosilicate

| experiment | B2O3 | Na2O | Al2O3 | SiO2 | CaO | match |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| borosilicate-glass-1 | 2.6 | 0.10 | 14.1 | 59.7 | 23.5 | yes |
| borosilicate-glass-2 | 5.0 | 0.10 | 13.7 | 58.8 | 22.4 | yes |
| borosilicate-glass-3 | 7.6 | 0.15 | 13.8 | 54.1 | 24.3 | yes |

#### sodium-disilicate-transpiration

- **Landed:** label `sodium-disilicate` only — OK (no binary wt% table printed; stoichiometric oxides not invented).

## Method notes

- Richter + van Limpt: `pdftotext -layout` of the cited pages + 150 dpi page rasters cross-check.
- Plante: `pdftotext` of §3 (p. 269–271) for crystalline start; Table 2 confirmed as running `Wt % K2O` via abstract + procedure text; observation equipment bags enumerated programmatically (324 identical trap maps).

## Validation

- `kems-037`, `kems-046`: `OK: 2 extract file(s) valid`
- `kems-042`: still FAIL on pre-existing 324 `equipment.sample` structural errors (value+locator shape); composition P0 fixed independently.

## Push

Pushed: yes — `906147d3e` on `origin/empirical/e5-kems-037-046-2026-09-22`.

## Report line

**P0: 1 (fixed) · tip: `906147d3e` · READY**

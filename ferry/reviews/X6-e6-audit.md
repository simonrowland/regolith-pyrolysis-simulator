# X6 — EXTRACTION AUDIT: E6 kems-slag

**Lane:** E6 (`empirical/e6-kems-slag-2026-09-22`)  
**Worktree:** `/workspace/repos/wt/slot-07` (reset to e6 tip)  
**Prior:** `/workspace/ferry-inbox/reviews/E6-kems-slag.md`  
**PDFs:** `/workspace/batch-z/pdfs/kems-057-kambayashi-1985.pdf`, `kems-088-ichise-1975.pdf`, `kems-112-ichise-1989.pdf`  
**Tip:** `ad781729ea4cd0ce1b162904f821b5955c6b1aec` (unchanged; no fix commit)  
**Date:** 2026-09-22 (America/Toronto)

## Scope

Cell-by-cell check of every landed starting composition (value, unit/basis, row/experiment mapping, locator) against the printed PDF table/caption. Traps called out by scout/E6: kems-088 Table 1 impurity assay; kems-112 Table 3 EPMA phase boundaries. Mismatch → P0 + fix on the E branch.

Landings live under `data/literature/extracts/` (v1), not `extracts-v2/`.

## P0 summary

**P0 count: 0.** No composition mismatches. No fix/push.

## Per-source audit

### kems-057-kambayashi-1985 — PASS

Printed post-run chemical analyses: Table 3 (journal p. 1913 / PDF p. 3) elemental Pb/P wt%; Table 4 (p. 1914 / PDF p. 4) oxide wt% P2O5/FeO/Fe2O3. X_P2O5, Total Fe, and `t` in FetO kept in locator notes only.

#### Table 3 — PbO–P2O5 (3 melts)

| experiment_id | Pb wt% landed | P wt% landed | printed Pb | printed P | X_P2O5 (note) | Match |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| pbo-p2o5-melt-1 | 63.84 | 10.68 | 63.84 | 10.68 | 0.359 | yes |
| pbo-p2o5-melt-2 | 66.84 | 11.28 | 66.84 | 11.28 | 0.361 | yes |
| pbo-p2o5-melt-3 | 64.79 | 12.0 | 64.79 | 12.00 | 0.382 | yes |

#### Table 4 — FetO–P2O5 (8 samples)

| experiment_id | P2O5 | FeO | Fe2O3 | printed P2O5/FeO/Fe2O3 | Total Fe / t / X (notes) | Match |
| --- | ---: | ---: | ---: | --- | --- | --- |
| feto-p2o5-sample-1 | 1.52 | 86.55 | 10.07 | 1.52 / 86.55 / 10.07 | 75.02 / 0.955 / 0.0075 | yes |
| feto-p2o5-sample-2 | 4.06 | 84.32 | 11.38 | 4.06 / 84.32 / 11.38 | 73.50 / 0.949 / 0.0202 | yes |
| feto-p2o5-sample-3 | 5.42 | 82.97 | 11.71 | 5.42 / 82.97 / 11.71 | 72.68 / 0.947 / 0.0271 | yes |
| feto-p2o5-sample-4 | 8.38 | 81.5 | 9.87 | 8.38 / 81.50 / 9.87 | 70.25 / 0.953 / 0.0427 | yes |
| feto-p2o5-sample-5 | 9.77 | 80.99 | 9.37 | 9.77 / 80.99 / 9.37 | 69.50 / 0.955 / 0.0499 | yes |
| feto-p2o5-sample-6 | 13.29 | 78.54 | 8.35 | 13.29 / 78.54 / 8.35 | 66.89 / 0.958 / 0.0691 | yes |
| feto-p2o5-sample-7 | 14.53 | 77.17 | 8.11 | 14.53 / 77.17 / 8.11 | 65.66 / 0.959 / 0.0764 | yes |
| feto-p2o5-sample-8 | 18.53 | 73.01 | 8.88 | 18.53 / 73.01 / 8.88 | 62.96 / 0.953 / 0.0991 | yes |

- **Hosts:** eleven per-melt `sample.printed_composition` maps (old `pbo-p2o5-series` / `feto-p2o5-series` gone)
- **Basis:** Table 3 elemental wt%; Table 4 oxide wt%; mole-fraction / Total Fe / `t` not folded into the value map — correct
- **Nature:** post-run chemical analysis (characterization states that); not a pre-run charge assay — noted
- **Locators:** `published_page` 1913/1914, `pdf_page_index` 3/4, tables `3`/`4` — correct
- **YAML float canonicalization** (`12.00`→`12.0`, `81.50`→`81.5`, `73.50`→`73.5` in notes) is representation only
- **Labels** retained in locator notes (reagent purities)

### kems-088-ichise-1975 — PASS (Table 2 only; Table 1 trap respected)

Printed Table 2 (Trans. ISIJ p. 117 / PDF p. 3), `%S` (Coulomatic), 8 alloy rows.

| experiment_id | S wt% landed | printed %S | Match |
| --- | ---: | ---: | --- |
| fe-s-0p31-wtpct | 0.31 | 0.31 | yes |
| fe-s-0p42-wtpct | 0.42 | 0.42 | yes |
| fe-s-0p65-wtpct | 0.65 | 0.65 | yes |
| fe-s-1p01-wtpct | 1.01 | 1.01 | yes |
| fe-s-1p59-wtpct | 1.59 | 1.59 | yes |
| fe-s-2p19-wtpct | 2.19 | 2.19 | yes |
| fe-s-3p37-wtpct | 3.37 | 3.37 | yes |
| fe-s-4p32-wtpct | 4.32 | 4.32 | yes |

- **Host:** eight per-alloy experiments (old `fes-kems-series` gone)
- **Basis:** elemental %S as printed; Fe balance not invented — correct
- **TRAP:** Table 1 (p. 116) is the pure-iron impurity assay — not landed as any experiment `printed_composition` (solute maps are S-only; locator notes name the trap)
- **Scout 9 vs printed 8:** landed 8; no invented ninth row — correct
- **Locator:** `published_page: 117`, `pdf_page_index: 3`, `table: "2"` — correct

### kems-112-ichise-1989 — PASS (Tables 1, 2, 5; Table 3 trap respected)

#### Table 1 — Fe–Ta molar fraction X_Ta (20 heats; journal p. 846 / PDF p. 4)

| heat | experiment_id | X_Ta landed | printed | Match |
| ---: | --- | ---: | ---: | --- |
| 9 | fe-ta-heat-9 | 0.048 | 0.048 | yes |
| 34 | fe-ta-heat-34 | 0.054 | 0.054 | yes |
| 12 | fe-ta-heat-12 | 0.094 | 0.094 | yes |
| 32 | fe-ta-heat-32 | 0.152 | 0.152 | yes |
| 21 | fe-ta-heat-21 | 0.153 | 0.153 | yes |
| 31 | fe-ta-heat-31 | 0.156 | 0.156 | yes |
| 25 | fe-ta-heat-25 | 0.239 | 0.239 | yes |
| 22 | fe-ta-heat-22 | 0.244 | 0.244 | yes |
| 27 | fe-ta-heat-27 | 0.255 | 0.255 | yes |
| 33 | fe-ta-heat-33 | 0.297 | 0.297 | yes |
| 17 | fe-ta-heat-17 | 0.319 | 0.319 | yes |
| 50 | fe-ta-heat-50 | 0.404 | 0.404 | yes |
| 49 | fe-ta-heat-49 | 0.406 | 0.406 | yes |
| 47 | fe-ta-heat-47 | 0.436 | 0.436 | yes |
| 51 | fe-ta-heat-51 | 0.444 | 0.444 | yes |
| 48 | fe-ta-heat-48 | 0.606 | 0.606 | yes |
| 53 | fe-ta-heat-53 | 0.727 | 0.727 | yes |
| 55 | fe-ta-heat-55 | 0.775 | 0.775 | yes |
| 54 | fe-ta-heat-54 | 0.81 | 0.810 | yes |
| 56 | fe-ta-heat-56 | 0.828 | 0.828 | yes |

#### Table 5 — Fe–Nb molar fraction X_Nb (20 heats; journal p. 848 / PDF p. 6)

| heat | experiment_id | X_Nb landed | printed | Match |
| ---: | --- | ---: | ---: | --- |
| 8 | fe-nb-heat-8 | 0.048 | 0.048 | yes |
| 4 | fe-nb-heat-4 | 0.091 | 0.091 | yes |
| 31 | fe-nb-heat-31 | 0.094 | 0.094 | yes |
| 59 | fe-nb-heat-59 | 0.139 | 0.139 | yes |
| 58 | fe-nb-heat-58 | 0.144 | 0.144 | yes |
| 11 | fe-nb-heat-11 | 0.192 | 0.192 | yes |
| 14 | fe-nb-heat-14 | 0.234 | 0.234 | yes |
| 12 | fe-nb-heat-12 | 0.242 | 0.242 | yes |
| 32 | fe-nb-heat-32 | 0.298 | 0.298 | yes |
| 15 | fe-nb-heat-15 | 0.32 | 0.320 | yes |
| 30 | fe-nb-heat-30 | 0.37 | 0.370 | yes |
| 29 | fe-nb-heat-29 | 0.384 | 0.384 | yes |
| 20 | fe-nb-heat-20 | 0.388 | 0.388 | yes |
| 19 | fe-nb-heat-19 | 0.394 | 0.394 | yes |
| 16 | fe-nb-heat-16 | 0.502 | 0.502 | yes |
| 26 | fe-nb-heat-26 | 0.548 | 0.548 | yes |
| 23 | fe-nb-heat-23 | 0.596 | 0.596 | yes |
| 24 | fe-nb-heat-24 | 0.671 | 0.671 | yes |
| 25 | fe-nb-heat-25 | 0.76 | 0.760 | yes |
| 27 | fe-nb-heat-27 | 0.856 | 0.856 | yes |

#### Table 2 — metallography alloy at% Ta (12 rows; journal p. 847 / PDF p. 5)

| experiment_id | Ta at% landed | printed | Match |
| --- | ---: | ---: | --- |
| fe-ta-metallography-1 | 11.1 | 11.1 | yes |
| fe-ta-metallography-2 | 17.5 | 17.5 | yes |
| fe-ta-metallography-3 | 25.2 | 25.2 | yes |
| fe-ta-metallography-4 | 29.0 | 29.0 | yes |
| fe-ta-metallography-5 | 34.0 | 34.0 | yes |
| fe-ta-metallography-6 | 37.5 | 37.5 | yes |
| fe-ta-metallography-7 | 40.4 | 40.4 | yes |
| fe-ta-metallography-8 | 41.0 | 41.0 | yes |
| fe-ta-metallography-9 | 43.0 | 43.0 | yes |
| fe-ta-metallography-10 | 46.0 | 46.0 | yes |
| fe-ta-metallography-11 | 51.0 | 51.0 | yes |
| fe-ta-metallography-12 | 64.0 | 64.0 | yes |

- **Hosts:** 20 + 20 + 12 per-heat/per-row experiments (old `fe-ta-nb-kems-series` gone); method `metallography` on the Table 2 set
- **Basis:** Tables 1/5 molar fraction; Table 2 at% Ta (notes say not mole fraction) — correct
- **TRAP:** Table 3 EPMA phase-boundary at% Ta (ε ≈ 29.4; μ ≈ 40.5/50.3; (Ta) ≈ 59.1/93.6) is **not** landed as any bulk-charge map; every locator notes the trap
- **Scout 21 Nb vs printed 20:** landed 20; no invented 21st — correct
- **Fe balance** not printed and not invented
- **Locators:** Table 1 → page 846 / pdf 4; Table 2 → 847 / pdf 5; Table 5 → 848 / pdf 6 — correct
- **YAML float canonicalization** (`0.810`→`0.81`, `0.320`→`0.32`, `0.370`→`0.37`, `0.760`→`0.76`) is representation only

## Method notes

- kems-057: rendered page PNGs at 200–400 dpi (Japanese scan; Table 3–4 numeric cells); cross-check of Table 3 against layout `pdftotext`
- kems-088: layout `pdftotext` of Table 2 on PDF p. 3 (journal 117) — clean %S column
- kems-112: rendered page PNGs at 250 dpi for Tables 1 / 2 / 5 (and Table 3 trap confirmation on p. 847)

## Validation

`uv`/venv `python tools/validate_literature_extracts.py` on the three `data/literature/extracts/` files → OK.

## Push

None. Tip remains `origin/empirical/e6-kems-slag-2026-09-22` @ `ad781729ea4cd0ce1b162904f821b5955c6b1aec`.

## Report line

**P0: 0 · tip: `ad781729e` · READY**

# X8 — EXTRACTION AUDIT: E8 kems-ms2000-044 / mendybaev-2017 / mendybaev-2021

**Lane:** E8 (`empirical/e8-mendybaev-ms2000-2026-09-22`)
**Worktree:** `/workspace/repos/wt/slot-09`
**Prior:** `/workspace/ferry-inbox/reviews/E8-mendybaev-ms2000.md`
**PDFs:** `/workspace/batch-z/pdfs/kems-ms2000-044.pdf`, `mendybaev-2017-fun-cai-lab-evaporation.pdf`, `mendybaev-2021-cai-low-pressure-h2-evap.pdf`
**Tip:** `a856a8c271247123aee4ecbda763dae89d27b6dd` (unchanged; no fix commit)
**Date:** 2026-09-22 (America/Toronto, EDT)

## Scope

Cell-by-cell check of every landed starting composition (value, unit/basis, row/experiment mapping, locator) against the printed PDF. Mendybaev 2021 watch: prose `16/36/27/21` (not a numbered oxide table). Mismatch → P0 + fix on the E branch.

## Verdict

**P0: 0** — every landed composition cell / prose string matches the printed source.
**READY.** No branch change; nothing to push.

## Method

- `pdftotext -layout` of cited pages + 120–150 dpi page rasters (`pdftoppm`) for Table 1 / §3.1.
- Enumerated every `printed_composition` / `initial_composition` host in the three extract YAMLs (22 + 1 + 3); no observation-equipment composition bags present.
- Complements for ms2000 checked as `Decimal(Me2O) == 1 − Decimal(SiO2)` with printed spelling retained.

## Per-source audit

### kems-ms2000-044 — PASS

**Printed:** Table 1, PDF p. 22, `x(SiO2)` column; Na2O-SiO2 block (12 rows) + K2O-SiO2 block (12 rows; x=0.722 and x=0.630 each appear at two T).
**Landed:** 22 per-melt experiments (12 Na + 10 unique-K); `printed_composition` label + `initial_composition` mole_fraction with printed x(SiO2) and binary complement.

#### Na2O-SiO2

| experiment | landed x(SiO2) | printed | x(Na2O)=1−x | match |
| --- | ---: | ---: | ---: | --- |
| na2o-sio2-xsio2-0p805 | 0.805 | 0.805 | 0.195 | yes |
| na2o-sio2-xsio2-0p753 | 0.753 | 0.753 | 0.247 | yes |
| na2o-sio2-xsio2-0p709 | 0.709 | 0.709 | 0.291 | yes |
| na2o-sio2-xsio2-0p671 | 0.671 | 0.671 | 0.329 | yes |
| na2o-sio2-xsio2-0p625 | 0.625 | 0.625 | 0.375 | yes |
| na2o-sio2-xsio2-0p573 | 0.573 | 0.573 | 0.427 | yes |
| na2o-sio2-xsio2-0p524 | 0.524 | 0.524 | 0.476 | yes |
| na2o-sio2-xsio2-0p477 | 0.477 | 0.477 | 0.523 | yes |
| na2o-sio2-xsio2-0p430 | 0.430 | 0.430 | 0.570 | yes |
| na2o-sio2-xsio2-0p405 | 0.405 | 0.405 | 0.595 | yes |
| na2o-sio2-xsio2-0p382 | 0.382 | 0.382 | 0.618 | yes |
| na2o-sio2-xsio2-0p349 | 0.349 | 0.349 | 0.651 | yes |

#### K2O-SiO2 (unique x; duplicate-T rows share one experiment)

| experiment | landed x(SiO2) | printed rows | x(K2O)=1−x | match |
| --- | ---: | --- | ---: | --- |
| k2o-sio2-xsio2-0p892 | 0.892 | 0.892 @ 1723 K | 0.108 | yes |
| k2o-sio2-xsio2-0p848 | 0.848 | 0.848 @ 1573 K | 0.152 | yes |
| k2o-sio2-xsio2-0p811 | 0.811 | 0.811 @ 1173 K | 0.189 | yes |
| k2o-sio2-xsio2-0p770 | 0.770 | 0.770 @ 1073 K | 0.230 | yes |
| k2o-sio2-xsio2-0p722 | 0.722 | 0.722 @ 1323 / 1673 K | 0.278 | yes |
| k2o-sio2-xsio2-0p674 | 0.674 | 0.674 @ 1373 K | 0.326 | yes |
| k2o-sio2-xsio2-0p630 | 0.630 | 0.630 @ 1323 / 1523 K | 0.370 | yes |
| k2o-sio2-xsio2-0p591 | 0.591 | 0.591 @ 1323 K | 0.409 | yes |
| k2o-sio2-xsio2-0p543 | 0.543 | 0.543 @ 1473 K | 0.457 | yes |
| k2o-sio2-xsio2-0p500 | 0.500 | 0.500 @ 1473 K | 0.500 | yes |

- **Unit / basis:** mole fraction as printed `x(SiO2)`; Me2O complement from binary identity, not renormalised — OK (note records identity).
- **Row mapping:** unique x only; activity/T duplicates share experiment — OK.
- **Locator:** `page: 22`, `table: '1'` — OK (PDF p. 22).
- **Not landed (correct):** Table 2/3 solid-silicate Gibbs rows as melt assays.

### mendybaev-2017-fun-cai-lab-evaporation — PASS

**Printed:** §3.1 prose (PDF p. 8): `37.9 wt% MgO, 11.6 wt% Al2O3, 42.9 wt% SiO2 and 7.6 wt% CaO`; Table 1 FUNC starting row (PDF p. 39): MgO 37.9 / Al2O3 11.6 / SiO2 42.9 / CaO 7.6.
**Landed:** `func-evaporation-series` `sample.printed_composition` oxide map.

| oxide | landed | prose §3.1 | Table 1 FUNC starting | match |
| --- | ---: | ---: | ---: | --- |
| MgO | 37.9 | 37.9 | 37.9 | yes |
| Al2O3 | 11.6 | 11.6 | 11.6 | yes |
| SiO2 | 42.9 | 42.9 | 42.9 | yes |
| CaO | 7.6 | 7.6 | 7.6 | yes |

- **Unit / basis:** oxide wt% as printed — OK.
- **Not used (correct):** residue rows B133R-10 + FUNC-* as start.
- **Locator:** `section: '3.1'`, `pdf_page_index: 8`, `table: '1'` — values agree on both prose and Table 1; Table 1 body is PDF p. 39 (locator hygiene only; composition correct).

### mendybaev-2021-cai-low-pressure-h2-evap — PASS

**Printed:** §3.1 prose (PDF / published p. 5): `A synthetic melt (labeled as CAI4B2) composed of 16 wt% MgO, 36% SiO2, 27% Al2O3, and 21% CaO`.
**Landed:** identical prose string on all three CAI4B2 experiments.

| experiment | landed prose | printed 16/36/27/21 | match |
| --- | --- | --- | --- |
| cai4b2-h2-2e-4-bar-series | `16 wt% MgO, 36% SiO2, 27% Al2O3, and 21% CaO` | 16 / 36 / 27 / 21 | yes |
| cai4b2-h2-2e-5-bar-series | same | same | yes |
| cai4b2-vacuum-series | same | same | yes |

- **Not structured** into an oxide map (lane: land as printed prose) — OK.
- **Locator:** `published_page: 5`, `section: '3.1'`; footer on PDF p. 5 is `5` — OK.
- **Table 1 refused as start (correct):** residue chemistry; no oxide columns for the starting melt assay.

## Non-P0 notes (no fix)

- mendybaev-2017 locator cites `pdf_page_index: 8` and `table: '1'` together; Table 1 body is PDF p. 39. Values identical on prose + Table 1 FUNC starting — composition P0 not implicated.
- mendybaev-2021 validator still FAIL with pre-existing `fidelity_samples` required error (unchanged; not a composition mismatch).

## Validation

- `kems-ms2000-044` + `mendybaev-2017`: `OK: 2 extract file(s) valid`
- `mendybaev-2021`: FAIL pre-existing `fidelity_samples` only (same as E8)

## Push

None. Tip remains `origin/empirical/e8-mendybaev-ms2000-2026-09-22` @ `a856a8c27`.

## Report line

**P0: 0 · tip: `a856a8c27` · READY**

# X3 — EXTRACTION AUDIT: E3 kems-comps

**Lane:** E3 (`empirical/e3-kems-comps-2026-09-22`)  
**Worktree:** `/workspace/repos/wt/slot-03` (reset to e3 tip; F1 already done)  
**Prior:** `/workspace/ferry-inbox/reviews/E3-kems-comps.md`  
**PDFs:** `/workspace/batch-z/pdfs/kems-012-sossi-2019.pdf`, `kems-015-hashimoto-1983.pdf`, `kems-021-plante-1992-feo.pdf`  
**Tip:** `f0a41b19698afe211c90cdbe544f4310a872337b` (unchanged; no fix commit)  
**Date:** 2026-09-22 (America/Toronto)

## Scope

Cell-by-cell check of every landed starting composition (value, unit/basis, row/experiment mapping, locator) against the printed PDF table. Mismatch → P0 + fix on the E branch.

## P0 summary

**P0 count: 0.** No composition mismatches. No fix/push.

## Per-source audit

### kems-012-sossi-2019 — PASS

| Field | Landed | Printed (Table 1 Measured, PDF p.53) | Match |
| --- | ---: | ---: | --- |
| SiO2 wt% | 40.60 | 40.60 | yes |
| Al2O3 wt% | 10.67 | 10.67 | yes |
| MgO wt% | 15.40 | 15.40 | yes |
| FeO wt% | 16.26 | 16.26 | yes |
| CaO wt% | 16.82 | 16.82 | yes |

- **Host:** `open-furnace-ferrobasalt-series.sample.printed_composition`
- **Basis:** major-element wt% (EPMA Measured column, N=8)
- **Locator:** `pdf_page_index: 53`, table `1` — correct for author manuscript of GCA 260
- **Not folded (correct):** Calculated column; trace-element ppm rows; no Calculated⊕Measured average
- **Row/experiment mapping:** single series starting mixture — correct

### kems-015-hashimoto-1983 — PASS

| Field | Landed | Printed (Table 1 row (1) Ave., PDF p.3 / journal p.113) | Match |
| --- | ---: | ---: | --- |
| SiO2 wt% | 35.43 | 35.43 | yes |
| Al2O3 wt% | 3.16 | 3.16 | yes |
| FeO wt% | 35.04 | 35.04 | yes |
| MgO wt% | 23.84 | 23.84 | yes |
| CaO wt% | 2.53 | 2.53 | yes |

- **Host:** `fcmas-evaporation-series.sample.printed_composition` (and matching `composition_wt_pct` under species harvest)
- **Basis:** wt% 100% normalized; paper note (1) designates Ave. as starting composition
- **Locator:** `published_page: 113`, `pdf_page_index: 3`, table `1`
- **Not used (correct):** replicate rows 1–7, σ, P.C., S.C. rows as the sample map
- **Row/experiment mapping:** Ave. → series starting composition — correct

### kems-021-plante-1992-feo — PASS

Printed Table 1 (PDF p.3 / journal p.1278), join caption `x(MgO)=x(SiO2)` / `x(MgO)/x(SiO2)=1`. SiO2 is not a Table 1 column; landed as `x(SiO2)=x(MgO)`.

| experiment_id | x(FeO) | x(MgO) | x(SiO2) landed | Printed x(FeO), x(MgO) | Join check | Match |
| --- | ---: | ---: | ---: | --- | --- | --- |
| feo-mgo-sio2-xfeo-0p15 | 0.15 | 0.425 | 0.425 | 0.15, 0.425 | 0.15+2×0.425=1 | yes |
| feo-mgo-sio2-xfeo-0p30 | 0.30 | 0.350 | 0.350 | 0.30, 0.350 | 1.0 | yes |
| feo-mgo-sio2-xfeo-0p40 | 0.40 | 0.300 | 0.300 | 0.40, 0.300 | 1.0 | yes |
| feo-mgo-sio2-xfeo-0p54 | 0.54 | 0.230 | 0.230 | 0.54, 0.230 | 1.0 | yes |
| feo-mgo-sio2-xfeo-0p70 | 0.70 | 0.150 | 0.150 | 0.70, 0.150 | 1.0 | yes |
| feo-mgo-sio2-xfeo-0p80 | 0.80 | 0.100 | 0.100 | 0.80, 0.100 | 1.0 | yes |
| feo-mgo-sio2-xfeo-0p90 | 0.90 | 0.050 | 0.050 | 0.90, 0.050 | 1.0 | yes |

- **Host per charge:** `sample.initial_composition` (`amount_basis: mole_fraction`); `printed_composition` retains label only
- **Series host:** `feo-mgo-sio2-series` — label only, no single-charge map (correct)
- **Locator:** page 1278, table `1`, `pdf_page_index: 3`
- **Basis:** mole fraction; not renormalised beyond printed join
- **Observation FK sanity (non-composition):** Table 1 ion-intensity rows FK to matching charge experiments; fidelity pin `plante_1992_table1_xfeo_0p15` / `I_Fe_over_I_Mg` = 7.04 matches printed

## Method notes

- Sossi + Hashimoto: `pdftotext -layout` + rendered page PNGs cross-check
- Plante: OCR commas/zeros noisy; Table 1 read from rendered page PNG at 200 dpi (values above)

## Push

No change. Tip remains `f0a41b196` on `origin/empirical/e3-kems-comps-2026-09-22`.

READY: /workspace/ferry-inbox/reviews/X3-e3-audit.md

# X7 — EXTRACTION AUDIT: E7 kems-118 / kems-137 / kems-200

**Lane:** E7 (`empirical/e7-kems-sn-anorthite-2026-09-22`)
**Worktree:** `/workspace/repos/wt/slot-05`
**Prior:** `/workspace/ferry-inbox/reviews/E7-kems-sn-anorthite.md`
**PDFs:** `/workspace/batch-z/pdfs/kems-118-yamamoto-1983.pdf`, `kems-137-bischof-2023.pdf`, `kems-200-ueshima-1983.pdf`
**Tip:** `07e2e77e911a1d2e7970a19e8b36d8c9d321825c`
**Date:** 2026-09-22 (America/Toronto, EDT)

## Scope

Cell-by-cell check of every landed starting composition (value, unit/basis, row/experiment mapping, locator) against the printed PDF. Watch kems-118 Table 1 impurity-assay trap; kems-137 End-vs-Start; kems-200 aimed-in-parentheses vs analysed. Mismatch → P0 + fix on the E branch.

## P0 summary

**P0 count: 0** — no composition mismatches; no fix commit; tip unchanged.

## Per-source audit

### kems-118-yamamoto-1983 — PASS

**Printed:** Table 4, Trans. ISIJ Vol. 23 (1983) p. 59 (PDF p. 5 / `pdf_page_index: 4`), column \(N_{\mathrm{Sn}}\) (mole fraction) for Fe–Sn alloys at 1600 °C.
**Trap avoided:** Table 1 is pure-Fe / pure-Sn impurity assay (wt%) — not used as charge.
**Landed:** ten dilute experiments `fe-sn-nsn-0p010` … `fe-sn-nsn-0p100` with `initial_composition` mole fractions; `fe-sn-series` / `fe-sn-cu-series` labels only (no invented single charge).

| experiment | Sn landed | Sn printed (Table 4) | Fe landed (=1−Sn) | match |
| --- | ---: | ---: | ---: | --- |
| fe-sn-nsn-0p010 | 0.010 | 0.010 | 0.990 | yes |
| fe-sn-nsn-0p020 | 0.020 | 0.020 | 0.980 | yes |
| fe-sn-nsn-0p030 | 0.030 | 0.030 | 0.970 | yes |
| fe-sn-nsn-0p040 | 0.040 | 0.040 | 0.960 | yes |
| fe-sn-nsn-0p050 | 0.050 | 0.050 | 0.950 | yes |
| fe-sn-nsn-0p060 | 0.060 | 0.060 | 0.940 | yes |
| fe-sn-nsn-0p070 | 0.070 | 0.070 | 0.930 | yes |
| fe-sn-nsn-0p080 | 0.080 | 0.080 | 0.920 | yes |
| fe-sn-nsn-0p089 | 0.089 | 0.089 | 0.911 | yes |
| fe-sn-nsn-0p100 | 0.100 | 0.100 | 0.900 | yes |

| host | field | landed | printed | match |
| --- | --- | --- | --- | --- |
| fe-sn-series | printed_composition (label) | Pure Fe/Sn weighed; Table 1 = assay not charge | Table 1 impurity assay; charges on Table 4 grid | yes |
| fe-sn-cu-series | printed_composition (label) | N_Sn held at 0.03; N_Cu stepped 0–0.08 | § Fe–Sn–Cu / Table 5 (N_Sn=0.03) | yes (no single map; correct) |

- **Unit / basis:** mole fraction Fe–Sn binary; N_Fe = 1 − N_Sn not renormalised — OK.
- **Not used (correct):** Table 1 impurity wt%; Figure 6 at% Sn legend (1.00…9.97) — more precise run labels, not the Table 4 charge grid landed here.
- **Locator:** `published_page: 59`, `pdf_page_index: 4`, `table: '4'` — OK.
- **Binary sum:** Sn+Fe = 1 for all ten charges — OK.
- **Obs-level equipment.sample compositions:** none — OK.

### kems-137-bischof-2023 — PASS

**Printed Start:** Table 6 Start columns, PDF p. 15; nominal oxides in §2.1 (PDF p. 4).
**Masses:** Table 2 initial weights, PDF p. 5.
**Landed:** `an-di-1-low`, `an-di-2-low`, `an-di-3-high` (Start EPMA) + `an-di-series` (nominal).

#### Table 6 Start EPMA (wt%)

| experiment | oxide | landed | printed (Start) | match |
| --- | --- | ---: | ---: | --- |
| an-di-1-low | SiO2 | 50.47 | 50.47 ± 0.09 | yes |
| | Al2O3 | 15.38 | 15.38 ± 0.08 | yes |
| | MgO | 10.42 | 10.42 ± 0.04 | yes |
| | CaO | 23.63 | 23.63 ± 0.09 | yes |
| an-di-2-low | SiO2 | 50.47 | 50.47 ± 0.09 (same glass as 1_low) | yes |
| | Al2O3 | 15.38 | 15.38 ± 0.08 | yes |
| | MgO | 10.42 | 10.42 ± 0.04 | yes |
| | CaO | 23.63 | 23.63 ± 0.09 | yes |
| an-di-3-high | SiO2 | 49.72 | 49.72 ± 0.09 | yes |
| | Al2O3 | 15.15 | 15.15 ± 0.08 | yes |
| | MgO | 10.27 | 10.27 ± 0.04 | yes |
| | CaO | 23.28 | 23.28 ± 0.09 | yes |

#### Nominal (§2.1) + masses (Table 2)

| host | field | landed | printed | match |
| --- | --- | --- | --- | --- |
| an-di-series | SiO2/Al2O3/MgO/CaO | 50.34 / 15.40 / 10.80 / 23.46 | SiO2=50.34, Al2O3=15.40, MgO=10.80, CaO=23.46 | yes |
| an-di-1-low | mass_kg | 0.0000791 | 79.1 mg | yes (unit conversion only) |
| an-di-2-low | mass_kg | 0.00007292 | 72.92 mg | yes |
| an-di-3-high | mass_kg | 0.00003869 | 38.69 mg | yes |

- **End columns not landed as start** — OK.
- **Ga2O3/In2O3** for 3_high explained in note (~1.1 / ~0.2 wt%); not invented into oxide map — OK (absent from Table 6 columns; Total 98.42).
- **Locator:** Start → `page: 15`, `table: '6'`; nominal → `page: 4`, §2.1; masses → `page: 5`, `table: '2'` — OK.
- **Obs-level equipment.sample compositions:** none — OK.

### kems-200-ueshima-1983 — PASS

**Printed:** Table 1, Tetsu-to-Hagané 69 (1983) p. 558 (PDF p. 3 / `pdf_page_index: 3`), column “Composition of sample (at % Mo)”. Parenthetical values = aimed (footnote b).
**Landed:** 42 per-sample experiments (`femo-sh-*` + `femo-as-melt`) mole fraction Mo = (at% Mo)/100; Fe = 1 − Mo; plus `femo-annealing-series` range label.

| sample | at% Mo printed | Mo landed | Fe landed | aimed? | match |
| --- | ---: | ---: | ---: | --- | --- |
| SH 1 | 38.2 | 0.382 | 0.618 | no | yes |
| SH 3 | 39.2 | 0.392 | 0.608 | no | yes |
| SH 2 | 47.5 | 0.475 | 0.525 | no | yes |
| SH 27 | (45) | 0.450 | 0.550 | yes | yes |
| SH 28 | (59) | 0.590 | 0.410 | yes | yes |
| SH 29 | 30.1 | 0.301 | 0.699 | no | yes |
| SH 30 | 34.0 | 0.340 | 0.660 | no | yes |
| SH 31 | 36.4 | 0.364 | 0.636 | no | yes |
| SH 8 | 38.8 | 0.388 | 0.612 | no | yes |
| SH 10 | 59.1 | 0.591 | 0.409 | no | yes |
| SH 11 | 76.5 | 0.765 | 0.235 | no | yes |
| SH 5 | 39.2 | 0.392 | 0.608 | no | yes |
| SH 6 | 43.4 | 0.434 | 0.566 | no | yes |
| SH 7 | 50.8 | 0.508 | 0.492 | no | yes |
| SH 12 | 38.8 | 0.388 | 0.612 | no | yes |
| SH 13 | 44.3 | 0.443 | 0.557 | no | yes |
| SH 14 | 59.1 | 0.591 | 0.409 | no | yes |
| SH 15 | 76.5 | 0.765 | 0.235 | no | yes |
| SH 16 | 38.6 | 0.386 | 0.614 | no | yes |
| SH 17 | 44.1 | 0.441 | 0.559 | no | yes |
| SH 18 | 87.8 | 0.878 | 0.122 | no | yes |
| SH 19 | 38.6 | 0.386 | 0.614 | no | yes |
| SH 20 | 44.1 | 0.441 | 0.559 | no | yes |
| SH 21 | 87.8 | 0.878 | 0.122 | no | yes |
| SH 48 | 55.4 | 0.554 | 0.446 | no | yes |
| SH 49 | (59) | 0.590 | 0.410 | yes | yes |
| SH 59 | (45) | 0.450 | 0.550 | yes | yes |
| SH 57 | 49.2 | 0.492 | 0.508 | no | yes |
| SH 56 | (45) | 0.450 | 0.550 | yes | yes |
| SH 51 | (59) | 0.590 | 0.410 | yes | yes |
| SH 55 | (45) | 0.450 | 0.550 | yes | yes |
| SH 54 | (50) | 0.500 | 0.500 | yes | yes |
| SH 53 | (59) | 0.590 | 0.410 | yes | yes |
| SH 23 | 76.5 | 0.765 | 0.235 | no | yes |
| SH 60 | 49.2 | 0.492 | 0.508 | no | yes |
| SH 52 | (59) | 0.590 | 0.410 | yes | yes |
| SH 37 | (45) | 0.450 | 0.550 | yes | yes |
| SH 38 | (59) | 0.590 | 0.410 | yes | yes |
| SH 24 | 38.8 | 0.388 | 0.612 | no | yes |
| SH 25 | 59.1 | 0.591 | 0.409 | no | yes |
| SH 26 | 76.5 | 0.765 | 0.235 | no | yes |
| As melt | 38.8 | 0.388 | 0.612 | no | yes |

| host | field | landed | printed | match |
| --- | --- | --- | --- | --- |
| femo-annealing-series | printed_composition (label) | Fe–Mo alloys, 30–88 at% Mo | prose range 30–88 at% Mo | yes |

- **Unit / basis:** mole fraction Fe–Mo; Mo = at%/100; Fe = 1 − Mo not renormalised — OK (Mo+Fe = 1 all 42).
- **Aimed retained and labelled** in printed_composition / locator note — OK (not dropped).
- **Locator:** `published_page: 558`, `pdf_page_index: 3`, `table: '1'` — OK.
- **Obs-level equipment.sample compositions:** none — OK.

## Method notes

- kems-118 Table 4: 200 dpi page raster (published p. 59) — \(N_{\mathrm{Sn}}\) column read directly.
- kems-137 Tables 2 + 6 + §2.1: `pdftotext -layout` (clean digital PDF).
- kems-200 Table 1: 400 dpi page raster of p. 558; composition column verified row-by-row against landed SH / As-melt set (OCR of scan is noisy; vision cross-check on cropped composition column).

## Validation

Not re-run this audit (composition-only scope). E7 prior: kems-118 and kems-200 validate OK; kems-137 retains pre-existing `equipment.sample` / `equipment.starting_glass_wt_pct` shape errors unchanged by this landing.

## Push

None. Tip remains `origin/empirical/e7-kems-sn-anorthite-2026-09-22` @ `07e2e77e9`.

## Report line

**P0: 0 · tip: `07e2e77e9` · READY**

# Pak, Ito, and Fruehan (1989), Table 1 audit

## Table 1 numeric cell audit

The printed table has 22 data rows. The MinerU HTML table and pdftotext were read first. MinerU table QA reported 47 numeric mismatches, one comma/dot flag, and 0.753 numeric agreement, so every numeric cell below was checked against the page 3 render. This resolves the OCR punctuation and column assignment, including the EMF and negative-log activity columns. Every stored quantity, temperature, composition, and lineage input is render-confirmed.

| Row | T (°C) | Na₂O (mol%) | CaO (mol%) | SiO₂ (mol%) | EMF (mV) | −log a(Na₂O) | Audit |
|---|---:|---:|---:|---:|---:|---:|---|
| A0 | 1400 | 60 | — | 40 | −437 | 3.241 | Page 3 render |
| A0 | 1500 | 60 | — | 40 | −450 | 2.963 | Page 3 render |
| B0 | 1400 | 50 | — | 50 | −183 | 4.771 | Page 3 render |
| B0 | 1500 | 50 | — | 50 | −188 | 4.454 | Page 3 render |
| C0 | 1400 | 33.3 | — | 66.7 | 102 | 6.488 | Page 3 render |
| C0 | 1500 | 33.3 | — | 66.7 | 115 | 6.177 | Page 3 render |
| D0 | 1400 | 20 | — | 80 | 317 | 7.784 | Page 3 render |
| D0 | 1500 | 20 | — | 80 | 353 | 7.528 | Page 3 render |
| A1 | 1400 | 52.03 | 13.29 | 34.68 | −130 | 2.458 | Page 3 render |
| A2 | 1400 | 54.79 | 8.68 | 36.53 | −100 | 2.639 | Page 3 render |
| B1 | 1400 | 39.30 | 21.40 | 39.30 | −171 | 3.740 | Page 3 render |
| B2 | 1400 | 41.94 | 16.12 | 41.94 | −116 | 4.072 | Page 3 render |
| B3 | 1400 | 44.60 | 10.80 | 44.60 | −63 | 4.391 | Page 3 render |
| C1 | 1400 | 22.76 | 31.71 | 45.53 | −136 | 5.669 | Page 3 render |
| C2 | 1400 | 24.49 | 26.53 | 48.98 | −98 | 5.898 | Page 3 render |
| C3 | 1400 | 26.58 | 20.26 | 53.16 | −61 | 6.120 | Page 3 render |
| C4 | 1400 | 28.69 | 13.93 | 57.38 | −28 | 6.320 | Page 3 render |
| C5 | 1400 | 30.96 | 7.11 | 61.93 | −12 | 6.416 | Page 3 render |
| D1 | 1400 | 11.63 | 41.83 | 46.54 | −167 | 6.781 | Page 3 render |
| D2 | 1400 | 13.93 | 30.38 | 55.70 | −93 | 7.227 | Page 3 render |
| D3 | 1400 | 15.30 | 23.72 | 60.98 | −56 | 7.445 | Page 3 render |
| D4 | 1400 | 16.76 | 16.20 | 67.04 | −23 | 7.648 | Page 3 render |

The source identifies Table 1 on absolute PDF page 3 (printed page 320). Table 1 gives binary reference melts and sample slag compositions in mol%; blank CaO cells in A0–D0 are zero by the printed binary system, retained as omitted rather than added as an unprinted numeric cell. Each activity output is the instructed reduction of its printed negative-log value. A support observation retains each negative-log input cell as typed-refused because no schema quantity names that logarithmic form; the activity observation links to that stored input. The reduction uncertainty is the half-last-digit rounding width, 0.0005 in x, propagated as relative uncertainty ln(10) × 0.0005 = 0.001151292546497023. Confidence level is not stated.

Table 1 anchors the 40 mol% Na₂O–60 mol% SiO₂ calibration to Yamaguchi et al. (ref. 4), with printed log activities −5.874 at 1400 °C and −5.521 at 1500 °C. These two explicitly attributed comparator values are retained as quoted_attributed observations. No pressure or numeric oxygen fugacity is printed; neither condition is stored. The paper describes the cell as open to air, but does not give a numeric fO₂.

The article does not state a pure-component standard state for Na₂O. The extract retains the table's reference-melt calibration in prose and leaves the typed identity reference state unknown; it does not assume a pure liquid standard state.

## Figure-read candidates (not digitized)

| Figure | Printed axes and units | Printed series / legend |
|---|---|---|
| 2 | x: Na₂O mole fraction; y: −log a(Na₂O), dimensionless; 1400 and 1500 °C | Yamaguchi et al. (square markers) and this work (circle markers) |
| 4 | x: CaO mole%; y: −log a(Na₂O), dimensionless; 1400 °C | Na₂O/SiO₂ molar ratios 1.5, 1.0, 0.5, 0.25 |
| 5 | x: Na₂O mole%; y: −log a(Na₂O), dimensionless; 1400 °C | CaO/SiO₂ ratios 0.544, 0.384, 0.241 |
| 8 | x: Na₂O wt%; y: −log a(Na₂O), dimensionless; 1500 °C | Na₂O–SiO₂; CaO–Na₂O–SiO₂ at CaO/SiO₂ = 0.5; 50% CaO–40% Al₂O₃–10% SiO₂ |

Figure 3 is a composition diagram, not an activity data source; it was not digitized.

## Migration evidence

Isolated migration produced 46 observations: 22 measured_reduced activity points, 2 quoted_attributed calibration points, and 22 typed-refused logarithmic input points. The 24 admitted activity points map to the `melt_activity` rail; the 22 typed-refused logarithmic input records have no rail. There are 0 hard issues and 24 identity-unknown findings, each solely for `reference_state`, because the source does not specify a standard state. All 24 activity points have known identity temperature and composition. The explicit activity parents preserve the exact printed logarithmic cells and their Table 1 row locators.

The row-aware MinerU compare report contains only its CSV header (`total=0`); the draft comparison found no candidate rows for this extract's lineage representation. The full Table 1 cell audit above therefore records the render check for every numeric table cell, and the focused regression test pins three rendered values and their page/table locators.

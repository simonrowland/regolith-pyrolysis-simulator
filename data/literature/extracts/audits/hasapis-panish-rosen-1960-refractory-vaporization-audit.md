# Hasapis, Panish & Rosen (1960): extraction audit

## Scope and source reads

The extracted table is printed Table 4, “Vaporization from a Mixture of Al2O3 and W,” PDF p. 40 / printed p. 34. It contains the eight requested temperature rows and mass-spectrometer signals for Al, AlO, Al2O, and WO2. The WO2 signal is retained as co-tabulated context from the alumina–tungsten study. Table 4 reports ion-current signals and their printed logarithmic forms, not absolute vapor pressures, mass-loss rates, or weight losses; none are inferred from these signals.

MinerU supplied the HTML table and surrounding text. The PDF text layer returned no usable text on the scanned pages, and table QA therefore skipped its numeric comparison. The render of PDF p. 40 was checked for every Table 4 body cell, the header-to-column alignment, row order, signs, decimal marks, approximation marks, and dashes. The relevant table cells and extracted values are transcribed below. The separate MinerU compare output contains 44 exact numeric signal cells, all matched to their Table 4 locations; the 10 approximate-form signal cells do not parse as decimals in compare and are listed separately below. Those ten were checked against the same page render.

The table has eight printed body rows (draft coordinate rows 2–9). Columns 1 and 2 are temperature and the redundant 10^4/T column; the latter was checked but not stored as a second temperature measurement. Columns 3–10 map to Al, AlO, Al2O, and WO2 signal/log pairs. Every numeric cell below is render-confirmed (method b); exact signal cells also appear as `matched` in the compare CSV.

| PDF page / draft row | T (K ± 10 K), c1 | 10^4/T, c2 | Al I+27, c3 | log I+27 T, c4 | AlO I+43, c5 | log I+43 T, c6 | Al2O I+70, c7 | log I+70 T, c8 | ΣI+WO2, c9 | log ΣI+WO2, c10 | Audit |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| p. 40 / r02 | 2126 | 4.70 | ≈ 0.4 | 2.92 | --- | --- | --- | --- | --- | --- | b, render |
| p. 40 / r03 | 2260 | 4.42 | ≈ 1.6 | 3.55 | --- | --- | ≈ 0.1 | 2.34 | 0.08 | 2.25 | b, render |
| p. 40 / r04 | 2273 | 4.40 | ≈ 1.5 | 3.52 | ≈ 0.08 | ≈ 2.25 | ≈ 0.10 | 2.35 | ≈ 0.12 | 2.43 | b, render |
| p. 40 / r05 | 2345 | 4.26 | 3.9 | 3.95 | ≈ 0.2 | 2.66 | ≈ 0.4 | 2.95 | 0.16 | 2.56 | b, render |
| p. 40 / r06 | 2360 | 4.24 | 4.1 | 3.98 | --- | --- | 0.36 | 2.92 | 0.42 | 2.99 | b, render |
| p. 40 / r07 | 2389 | 4.18 | 5.1 | 4.07 | 0.4 | 2.97 | 0.63 | 3.17 | 0.68 | 3.20 | b, render |
| p. 40 / r08 | 2441 | 4.09 | 7.3 | 4.24 | 0.78 | 3.30 | 0.88 | 3.32 | 0.84 | 3.30 | b, render |
| p. 40 / r09 | 2495 | 4.01 | 9.2 | 4.35 | 1.0 | 3.39 | 1.4 | 3.53 | 1.3 | 3.50 | b, render |

The nine approximate ion-current cells (all render-checked) are p. 40 r02c03; r03c03 and r03c07; r04c03, r04c05, r04c07, and r04c09; and r05c05 and r05c07. They are admitted with their printed strings retained and `approximate: true`. One additional approximate-form logarithm at r04c06 remains unavailable because the existing quantity vocabulary cannot represent it. The 10 dash cells are retained as blanks rather than observations. The remaining 44 numeric signal cells are the 44 `matched` rows in `hasapis-panish-rosen-1960-refractory-vaporization-mineru-compare.csv`.

The focused test pins three rendered cells: Al current ≈0.4 at r02c03, Al log value 3.52 at r04c04, and summed WO2 current 1.3 at r09c09. The test also pins all eight temperatures and checks that changing a fidelity-pinned value fails the fidelity check.

## Conditions and interpretation

- Table 4 prints T in kelvin with ±10 K. The confidence level for that width is not stated.
- The prose names a mixture of Al2O3 and tungsten powder, but no mixture proportions. The extract preserves the named mixture and marks numeric composition unknown.
- The table does not print total pressure or oxygen fugacity. These remain unknown; no vacuum, ambient pressure, or oxygen fugacity is assumed.
- The table and accompanying prose describe mass-spectrometer data from an effusion-cell study. The cell is described as a tungsten outer container; the footnote reads: “1/8-inch orifice in cylindrical cell of 3/16-inch height and inner diameter.”
- The authors estimate Al:AlO:Al2O:WO2 ≈ 100:4:3:1.6 using estimated electron cross sections attributed to Otvos and Stevenson. That estimate is not a Table 4 cell, depends on external cross-section estimates, and is not entered as an observation.
- The nine approximate ion-current values are admitted as numeric `ion_intensity` points with `approximate: true`; their raw printed strings remain in the extract. The other 18 direct ion-current points are exact-form values. The logarithmic columns remain unavailable because the existing quantity vocabulary has no token for the printed logarithmic signals; they are not converted into vapor pressures. For WO2, Table 4 prints `log ΣI+WO2` without T. No temperature multiplier is inferred.

## Other tables and figures in the report

- PDF p. 26, Table 1: rhodium vapor-pressure measurements; excluded because rhodium is outside the target oxide list.
- PDF pp. 29–30, Table 2 and continuation: iridium vapor-pressure measurements; excluded because iridium is outside the target oxide list.
- PDF p. 36, printed Table 3: thoria vaporization signals; not extracted because ThO2 is outside the requested regolith-relevant oxide list.
- PDF p. 51, printed Table 5: silica/glass viscosity data, not vaporization data.
- Figures 13–16 concern the other refractory studies. Figure 17 plots the Table 4 data; it is not digitized because the printed table is available.

The report's remaining sections describe apparatus, material compatibility, and preliminary refractory studies. No additional printed vaporization table for SiO2, MgO, CaO, iron oxides, TiO2, or alkali oxides was found in the MinerU table inventory.

## Isolated migration and expected store delta

The real migrator read 54 source rows and emitted 54 observations: 27 numeric ion-intensity points (nine approximate) and 27 unavailable logarithmic signal values. It reported 0 hard issues. All 54 printed temperatures reached `point_conditions.temperature_K` and the corresponding identity temperature. For three exact ion-current rows, identity composition remains unknown because proportions are not printed; pressure and fO2 are not numeric identity values because the paper does not report them and the ion-intensity profile does not use those axes. The source extract explicitly records total pressure and fO2 as unknown for every observation.

Expected store delta: one work, one experiment, and 54 observation records, of which 27 are numeric (nine approximate) and 27 unavailable. No battery-store files are regenerated or committed in this extraction.

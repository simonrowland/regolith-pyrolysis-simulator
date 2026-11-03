# Watne 1977 potassium-vapor-pressure extraction audit

## Coverage and transcription

The extract stores every data row in printed Tables 12, 14, 15, 16, and 17. The printed counts are 7, 22, 26, 26, and 15 rows (96 total). Absolute PDF pages are the extract locators; the printed folios are recorded separately in the cell audit because the page offset changes across the thesis.

| Printed table | Absolute PDF page | Printed folio | Rows | Reading and sample |
|---|---:|---:|---:|---|
| 12 | 112 | 88 | 7 | Absorbance over K2O·9Al2O3-alumina; platinum and graphite furnaces |
| 14 | 135 | 107 | 22 | Absorbance over old K2O·9Al2O3-alumina sample |
| 15 | 136 | 108 | 26 | Absorbance over renewed K2O·9Al2O3-alumina sample |
| 16 | 141 | 112 | 26 | Absorbance for 1.5A Al:K=3:2, Cup H lower-temperature series |
| 17 | 142 | 113 | 15 | Absorbance for 1.5A Al:K=3:2, Cup H higher-temperature series |

Each row is a potassium gas-phase absorbance measurement by atomic absorption in a platinum or graphite furnace. The composition and apparatus labels are retained per row. Tables 14-17 are associated with Ar-4%H2 in the Figure 18/19 captions; Table 12 has no row-specific gas mixture. No total pressure or absolute log fO2 is printed for any extracted row. The abstract's general ambient-pressure apparatus description is not promoted to an experiment condition.

Tables 12 and 14-17 do not print pointwise potassium pressure values: the measured numeric column is Absorbance. The thesis relates absorbance to potassium-vapor concentration and concentration to pressure, then says pressure is not exactly equal to absorbance and is only within a few percent. No pressure conversion was made. The data rows therefore retain their measured absorbance and are typed refusals for pressure.

## Schema gap and migration

Absorbance has no exact quantity token in `simulator/battery/enums.py::Quantity` or `simulator/battery/migrate.py::QUANTITY_ALIASES`. `tools/validate_literature_extracts.py::OBSERVATION_TYPES` permits the domain type `partial_pressure`, while `simulator/battery/migrate.py::TYPE_QUANTITY` has no exact absorbance token. Each row explicitly supplies the descriptive non-token `potassium_absorbance_as_printed`, so migration cannot infer a pressure quantity. The `gas` phase is an existing `simulator/battery/migrate.py::PHASE_MAP` token. Kido et al. (2004) likewise preserves absorbance without claiming a pressure quantity in `data/literature/extracts/kido-2004-mn-cr-high-temperature-glass-spectra.yaml`.

The isolated real migrator produced **96 observations, 0 hard issues, and 96 unavailable values**. All 96 migrated quantities are unknown exactly as intended for this schema gap; no phase is unknown. Printed temperature and composition reach each observation, and composition reaches identity. No pressure value is inferred. The extract records unknown pressure and fO2 states with source locators; migration leaves total pressure absent and fO2 unknown.

Identity axes from three rows after isolated migration:

| Observation | Quantity | Composition | Total pressure | fO2 | Temperature |
|---|---|---|---|---|---|
| Table 12, 1110 °C row | unknown absorbance quantity | K2O:Al2O3 = 1:9 | not set; not printed | not set; not printed | 1383 K (printed) |
| Table 14, 1004 °C row | unknown absorbance quantity | K2O:Al2O3 = 1:9 | not set; not printed | not set; not printed | 1277.15 K (migrated from printed °C) |
| Table 16, 870 °C row | unknown absorbance quantity | Al:K = 3:2 | not set; not printed | not set; not printed | 1143.15 K (migrated from printed °C) |

Table 12 retains its printed temperature uncertainties and absorbance uncertainties. Their confidence level is not stated. The starred absorbance values in Tables 14 and 15 retain the author’s note that the recorder was limited and those values were not used in calculations. Table 15's `>101` divisions and `>1.32*` absorbance remain greater-than bounds.

## Calibration and fits

Equation 2 retains the Plante [45] relation `log P_K(K2O·9Al2O3) = -21453/T°K + 7.4866`; the pressure unit is not stated, and it is not a pointwise pressure measurement from this thesis. Equations 3 and 4 retain the printed graphite and platinum log-absorbance fits; Equation 5 retains the NBS log-pressure relation attributed to [45]. Figure 15 also prints the corresponding slopes, intercepts, and correlation coefficients. Figure 18 fits use the Table 14 and 15 observations; Figure 19 fits use Tables 16 and 17. Fit context lists exactly the rows used as parents. Figure curves were not digitised.

## Full-cell audit

`audits/watne-1977-potassium-vapor-pressure-furnace.cells.csv` contains 412 cell records: 384 numeric cells in the five complete tables and 28 coefficients from the printed equations and figure fits. There are 312 `(a)` cells where MinerU and pdftotext agree in digits, sign, and exponent, and 100 `(b)` cells checked against page renders. Table layout and row/column assignment were checked against a render for every table.

The render checks include every Table 12 cell (temperature and reciprocal-temperature uncertainties include the ± glyph), all numeric cells in the Table 14 first data row (MinerU marked it as a header), Table 14 OCR mismatches and suspicious zero/peak-height cells, Table 15's first data row and censored row, Table 16's first data row plus the `7` and `8.75` OCR ambiguities, Table 17's `1`/`I` peak-height cell, and the printed equation/fit coefficients. The audit CSV retains both machine-read text and the render-selected cell where applicable.

The table QA pre-screen reported numeric agreement of 0.964 (two disagreements) for Table 12 and 0.976 (two disagreements) for Table 14; Tables 15-17 reported 1.000. Its Table 14 count excluded the first numeric data row because MinerU classified that row as a header. That row was render-checked, included, and reconciled against the printed count of 22.

The row-aware table-draft comparison matched 95/96 observations with zero locator errors. Its one mismatch is Table 15's censored `>1.32*` absorbance: the draft keeps the comparator in a nonnumeric cell, while the render confirms the printed bound. The extract preserves value `1.32`, qualifier `greater_than`, and raw cell `>1.32*`; this is a resolved comparator limitation, not a transcription disagreement.

## Scope notes

No sodium-vapor or sodium-pressure rows were found. Table 13 is a furnace-sensitivity table, not a vapor-pressure table. Tables 1-11 concern furnace apparatus and sensitivity. Figures 16 and 17 are candidates for a separate figure pass on opening the sample port to air and adding water vapor; their plotted points are not transcribed here.

Expected store delta after regeneration: 96 observations added, all unavailable typed refusals, and zero admitted scoreable potassium-pressure rows. No battery-store regeneration is committed.

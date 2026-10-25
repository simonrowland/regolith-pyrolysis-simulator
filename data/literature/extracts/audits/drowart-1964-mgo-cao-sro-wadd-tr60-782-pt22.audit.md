# Drowart et al. 1964 KEMS extraction audit

## Source and reading method

The scan has no text layer. The two machine reads were MinerU and the provided independent Tesseract OCR (`eng`, 300 dpi, page segmentation mode 6); the latter is used in place of `pdftotext-layout.txt`, which is empty for this scan. The rendered PDF pages are the deciding read for every numeric source cell in the tables below (audit method **b**). Each table's row and column assignment was checked once against its page render. The current-ratio cells also pass the table-tool comparison: 169 matched, 0 missing, 0 mismatched, 0 presence-only, 0 needing mapping. See the accompanying `*.compare.csv` for cell-level matches.

The locators below use printed table labels and absolute PDF page numbers. Internal MinerU block indices are only present in the mapping to connect machine-read cells to their table blocks.

## Numeric cell audit

Each page/table entry below covers every numeric cell in its stated rows and columns. Every cell in those ranges was read against the render; OCR agreement alone was not used as confirmation.

| Printed table | PDF page (printed page) | Printed data rows | Numeric source cells | Cells and render check |
|---|---:|---:|---:|---|
| 1 | 12 (6) | 13 | 44 | Rows 2–14, columns 2–5: relative ionization cross-sections, relative multiplier yields, measured and literature appearance potentials. All 44 numeric cells checked on the render; blank and nonnumeric cells excluded. |
| 2a | 15 (9) | 8 | 49 | Rows 2–9, T column 1, ion-current columns 2–5, and ΔH column 6; plus the printed mean. All cells checked on the render. |
| 2b | 15 (9) | 6 | 37 | Rows 2–7, T column 1, ion-current columns 2–5, ΔH column 6, and mean row. All cells checked on the render. |
| 3a | 16 (10) | 4 | 25 | Rows 2–5, T column 1, ion-current columns 2–5, ΔH column 6, and mean row. All cells checked on the render. |
| 3b | 16 (10) | 3 | 19 | Rows 2–4, T column 1, ion-current columns 2–5, ΔH column 6, and mean row. All cells checked on the render. |
| 3c | 16 (10) | 2 | 13 | Rows 2–3, T column 1, ion-current columns 2–5, ΔH column 6, and mean row. All cells checked on the render. |
| 4a | 17 (11) | 4 | 25 | Rows 2–5, T column 1, ion-current columns 2–5, ΔH column 6, and mean row. All cells checked on the render. |
| 4b | 17 (11) | 1 | 6 | Row 2, T column 1, ion-current columns 2–5, and ΔH column 6. No mean is printed. All cells checked on the render. |
| 4c | 17 (11) | 8 | 49 | Rows 2–9, T column 1, ion-current columns 2–5, ΔH column 6, and mean row. All cells checked on the render. |
| 5a | 18 (12) | 3 | 16 | Rows 2–4, T column 1, ion-current columns 2–4, ΔH column 5, and mean row. All cells checked on the render. |
| 5b | 18 (12) | 1 | 6 | Row 2, T column 1, ion-current columns 2–5, and ΔH column 6. No mean is printed. All cells checked on the render. |
| 5c | 18 (12) | 3 | 19 | Rows 2–4, T column 1, ion-current columns 2–5, ΔH column 6, and mean row. All cells checked on the render. |
| 6 | 19 (13) | 3 | 11 | Eight equilibrium-specific dissociation energies and three averages, checked on the render. |
| 7 | 20 (14) | 3 | 4 | Three equilibrium-specific Sr₂O atomization energies and the selected value, checked on the render. |
| **Total** |  |  | **323** | 44 calibration cells; 43 temperatures, 169 relative ion-current values, 43 pointwise reaction enthalpies and 9 means; 11 Table 6 energies; 4 Table 7 energies. |

Printed row counts for Tables 2a–5c total 43 measured-temperature rows. The table-tool draft initially fused a pointwise enthalpy with the mean in Tables 3c, 4a and 4c; those individual cells and means were separated from the render. No missing or dashed cells were turned into values.

Table 6's MinerU block placed the data in header text, so its numeric cells were hand-transcribed from PDF page 19: MgO row, O/O₂ = 85.9, WO₂/WO₃ = 84.0, average = 85.0 ± 5; CaO row, O/O₂ = 90.1, MoO₂/MoO₃ = 93.4, WO₂/WO₃ = 93.1, average = 92.2 ± 5; SrO row, O/O₂ = 102.5, MoO₂/MoO₃ = 101.1, WO₂/WO₃ = 102.0, average = 101.9 ± 5. Table 7's equilibrium rows on PDF page 20 are 195.8, 193.0 and 191.5; its selected value is 193.4 ± 6. The selected value is hand-transcribed because MinerU merged it with the surrounding cell text.

## Render resolutions

- Table 1, PDF page 12: MinerU read the WO₃ relative multiplier yield as 0.41; Tesseract read 0.42. The render confirms 0.41.
- Table 5c, PDF page 18: MinerU read the first temperature as 2149 K and Tesseract as 2243 K; the render confirms 2149 K. Tesseract read the first Sr⁺ current as `L.0`; the render confirms 1.0.
- Tables 3c, 4a and 4c: the render separates the final pointwise ΔH value from the adjacent mean that MinerU concatenated into the same cell. Both values are stored as printed.
- Apparatus/calibration prose was checked against renders of PDF pages 8 and 10. The report states that absolute pressures were not calibrated and that K was computed from relative ion currents; no numeric K table is printed.

## Extraction decisions and schema gaps

- Tables 2a–5c are relative ion-current ratios, stored as dimensionless `ion_intensity_ratio` observations. The parenthetical ionizing-electron energies remain annotations to the printed current, not uncertainties. The authors say no absolute partial pressures were calibrated; no pressure is inferred. Table 1's 44 numeric calibration cells are retained in an unscored source-context record with their printed labels and units.
- The 43 pointwise and 9 mean reaction enthalpies are copied as `quantity_unknown` with the printed ΔH label and typed refusal. Table 6's eight individual D₀ values and three averages, and Table 7's three equilibrium-specific and one selected Sr₂O atomization values, are also typed refusals because no quantity token represents these energies. The reported calculations are attributed to the authors: the manuscript does not provide all numeric equilibrium-constant and thermochemical inputs needed to independently reconstruct them. Table 2a–5c mean error widths are identified in the prose as statistical 95% probability limits. Table 6 and 7 uncertainty confidence is not stated.
- No numeric equilibrium constants or absolute partial pressures are tabulated. The pressure/calibration method is recorded in context, including the statement that the printed K method uses relative ion currents and the Table 1 cross-sections and multiplier yields.
- Table 6 and Table 7 do not print an experimental temperature; no temperature is added to their rows. In the migrated ratio observations the quantity profile uses temperature only. Composition, total pressure and fO₂ are not applicable to that profile; the unprinted total-pressure value is absent from migrated point conditions.
- Figure 1, the term scheme for alkaline-earth oxides, is a figure-read candidate for molecular-state energy placements. Its figure-only values are not transcribed. Tables 8–12 are literature/model comparison material rather than this study's KEMS measurement tables; they are outside this extraction's KEMS rows. The requested study results are in Tables 2–7.

## Isolated migration

The seat migrator produced 236 observations: 169 point-valued relative ion-current observations and 67 unavailable typed-refusal observations. Hard issues: 0. Registry issues: 0. The four focused render pins are in `tests/test_drowart_1964_mgo_cao_sro_extract.py`; the test also mutates one pin and confirms the render assertion fails.

For the first three migrated ratio rows, identity axes are: composition = not applicable; total pressure = not applicable; fO₂ = not applicable; temperature = 2225 K, 2026 K, and 2114 K respectively. Each row retains its table temperature condition and locator.

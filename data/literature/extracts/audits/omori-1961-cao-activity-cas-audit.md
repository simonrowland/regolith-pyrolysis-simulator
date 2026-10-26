# Omori and Sanbongi (1961) extraction audit

## Coverage and cell verification

The printed Table 1 on PDF page 3 (published page 141) has 12 data rows; Table 2 on PDF page 4 (published page 142) has 11. The extract retains all 23 rows, with 31 CaO activity values, 22 SiO2 activity inputs, and 10 Al2O3 activity inputs (63 observations total). Table 1's three-component compositions and Table 2's binary compositions are attached to each row as printed. Table 2 has no printed Al2O3 component, so none is added.

The MinerU table drafts contain 141 populated numeric cells in Table 1 and 85 in Table 2. QA found agreement with the pdftotext layer for 37 Table 1 cells and 4 Table 2 cells; the remaining 185 disagreed. The page renders were checked for every populated numeric body cell, including cells where the machine reads agreed. The cell-by-cell record is `omori-1961-cao-activity-cas-cell-audit.csv`; each row identifies the absolute PDF page, printed table, printed row, column, MinerU text, render transcription, and render verification method. Both table layouts and row/column assignments were checked against their rendered pages.

The table-tool compare result is in `omori-1961-cao-activity-cas-compare.csv`: 62 values matched, none were absent, and one was reported as a mismatch. The mismatch is Table 1 row 2 SiO2 activity: the rendered table prints `(0.0005)`. The compare parser does not treat a parenthesized number as numeric; the extract stores numeric activity `0.0005` and retains `(0.0005)` in `activity_as_printed` and `note_as_printed`. The render confirms the transcription.

## Evidence classes and conditions

- Table 1 CaO row 1 (`1.00`) is the paper's solid-lime standard-state anchor. Row 2 (`0.78`) is the CaO-Al2O3 curve anchor assigned from Figure 5. Both are `quoted_attributed`. Rows 3-12 are `measured_reduced`: the paper calculates them by Gibbs-Duhem diagram integration using the stored activity inputs and row 2 anchor (Eq. 11, PDF page 2 / published page 140). Lineage lists exactly those stored inputs.
- Table 2 CaO column A values are `quoted_attributed`: the paper says they were interpolated from Figure 8 and tabulates them. Column B row CB-10 (`0.0175`) is the stated reference value for Eq. 13; other B rows are `measured_reduced` from that reference and the stored SiO2 activities (Eq. 13, PDF page 4 / published page 142). Lineage lists exactly those stored inputs.
- Table 1 SiO2 and Al2O3 activities and Table 2 SiO2 activities are `quoted_attributed` prior e.m.f. double-cell results cited to Omori and Sanbongi references [1] and [2]. The paper does not identify which individual input cells came from which reference.
- CaO uses solid lime as its standard state; SiO2 uses beta-cristobalite and Al2O3 uses corundum. The paper gives 1630° for every table row. It does not report an experimental total pressure or oxygen fugacity for these tabulated values; both remain unknown. The rows are thermodynamic table calculations and do not report gas equilibration or crucible methods. No unprinted atmospheric condition is assigned.
- The 35 wt% Al2O3 series belongs to Table 1 only. It is not copied onto Table 2's binary rows. Additional values read only from Figures 5 and 8 were not extracted; those curves are figure-read candidates.

## Migration evidence

The isolated real migrator run produces 63 observations, 0 hard issues, and 0 unavailable observations. Each observation receives the printed 1630° temperature, its row composition, and unknown pressure and fO2 states. The focused test pins five rendered values across both printed tables and checks their PDF page/table/row/column locators, row counts, identity composition, migrated temperature, lineage, and absent pressure/fO2 values.

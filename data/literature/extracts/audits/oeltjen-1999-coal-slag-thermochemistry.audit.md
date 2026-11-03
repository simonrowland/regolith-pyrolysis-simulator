# Oeltjen 1999 extraction audit

## Scope and result classification

The extract keeps the six analyzed synthetic-slag compositions from Table 5.1 as six KEMS experiment records, and keeps the six Table 4.1 model compositions as model-input context. It records the relevant alkali-activity comparison and prediction figures without reading plotted points. No table in the dissertation tabulates numeric KEMS partial pressures, derived activities, or numeric author model predictions. The raw detector signals needed for the pressure calculation are not printed; own activities are shown only as symbols in Figures 6.21–6.22, and no per-point uncertainty is printed.

The plotted own KEMS activities are calculated from measured alkali pressures through equations 5.1–5.6, including the stated stoichiometric-evaporation assumption. If those numeric values were transcribed from a table, the activity values would be measured_reduced. They are not available in a printed numeric table, so no measured pressure/activity observation is manufactured from the figures. Figures 6.23–6.40 contain author model curves only and no numeric result table; they remain model-prediction figure records with no model_derived rows. Figure 6.1–6.4, 6.6–6.8, and 6.18–6.20 compare literature measurements with model curves; their points are also not digitized.

No unprinted total pressure or fO2 is assigned to a sample. The approximately 10^-5 Pa sample-room and 10^-6 Pa analyzer-room pressures are recorded as apparatus-background facts on the bench, not experiment total pressure. Each experiment carries the printed K interval for its plotted series and the measured composition, cell, orifice, mass, and KEMS method. The source states that the measured points are selected for liquid samples without phase changes; no numeric point temperatures are inferred from the plotted curves.

## Table 4.1 cell audit

Printed page 15 / PDF page 29; caption title “Zusammensetzung von kohlenaschenrelevanten Modellsystemen” (composition of model systems relevant to coal ashes). Columns SK1–SK3 and BK1–BK3 are the six model systems. Rows are component oxides. The heading “Stoffmengenanteil in Mol %” means amount fraction in mole percent. The table says these compositions correspond to means of component proportions in real ash analyses and that their standard deviations are substantial.

All values below retain the printed decimal spelling. A dash is the printed dash, not zero. The table has 6 printed component rows × 6 model-system columns = 36 cells: 22 numeric values and 14 dash cells. Every numeric cell was read in MinerU and pdftotext; the page 29 render was checked across the full table to confirm the row/column layout and resolve the single digit disagreement reported by MinerU QA. Species assignment was checked against each row label.

| Printed component row | SK1 | SK2 | SK3 | BK1 | BK2 | BK3 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| SiO2* | 75.2 | 69.6 | 64.8 | 85.4 | 61.3 | 74.9 |
| Al2O3* | 22.6 | 21.0 | 19.5 | 12.5 | 9.0 | 10.9 |
| CaO* | - | 7.3 | - | - | 28.2 | - |
| FeO* | - | - | 13.7 | - | - | 12.4 |
| Na2O* | - | - | - | 2.1 | 1.5 | 1.8 |
| K2O* | 2.2 | 2.1 | 1.9 | - | - | - |

Cell audit method: each numeric cell in rows SiO2*, Al2O3*, CaO*, FeO*, Na2O*, K2O* and columns SK1, SK2, SK3, BK1, BK2, BK3 is render-verified on PDF page 29 (method b). All 14 dashes were also checked against the machine text and render so that no dash was converted to zero.

## Table 5.1 cell audit

Printed page 27 / PDF page 41; caption title “Zusammensetzung der synthetischen Schlackenproben” (composition of synthetic slag samples). Columns 1–6 are the analyzed synthetic samples. Rows are component oxides. The unit line “Stoffmengenanteil in Mol %” means amount fraction in mole percent. The six Table 5.1 sample compositions are retained as printed sample maps; the values were not normalized or converted to fractions. The table has 6 printed component rows × 6 sample columns = 36 cells: 22 numeric values and 14 dash cells.

| Printed component row | 1 | 2 | 3 | 4 | 5 | 6 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| SiO2* | 72.9 | 69.6 | 62.9 | 73.5 | 59.6 | 72.5 |
| Al2O3* | 17.4 | 18.6 | 19.7 | 18.0 | 11.6 | 12.7 |
| CaO* | - | 6.3 | - | - | 24.3 | - |
| FeO* | - | - | 14.2 | - | - | 9.8 |
| Na2O* | - | - | - | 8.5 | 4.5 | 5.0 |
| K2O* | 9.7 | 5.5 | 3.2 | - | - | - |

All numeric cells match MinerU and the exact pdftotext layer. The page 41 render was checked across the full table to confirm every row and sample-column assignment. The row heading was checked for each oxide, and all 14 printed dashes remain dashes. No decimal-comma conversions are required in these two tables; their numeric cells use decimal points.

Cell audit method: each numeric cell in rows SiO2*, Al2O3*, CaO*, FeO*, Na2O*, K2O* and columns 1, 2, 3, 4, 5, 6 is confirmed by the two machine reads (method a), with the page 41 render confirming the complete layout. Table 5.1's analyzed compositions are kept distinct from Table 4.1's model-system compositions.

## Conditions and apparatus provenance

The sample-specific plotted temperature intervals are printed in §6.2 on PDF page 71 (printed page 57): sample 1 1790–1890 K; sample 2 1700–1840 K; sample 3 1760–2050 K; sample 4 1720–1890 K; sample 5 1530–1640 K; sample 6 1620–1810 K. §5.2 states that activity measurements used 50 mg for samples 2 and 5 and 100 mg for the other four samples. Unit conversion to kg is used only in the typed experiment-mass field.

The printed apparatus description on PDF page 42 (printed page 28) gives cylindrical iridium inner cells, a cylindrical tungsten outer cell, a 0.3 mm effusion orifice, cell height 8.5 mm, cell diameter 7.5 mm, electron-impact ionization at 70 eV, and temperature measurement by pyrometer and thermocouple. The Balzers QMG 421 is named on PDF page 44 (printed page 30). Pure-silver calibration follows each sample measurement on PDF page 45 (printed page 31). Typed metre values are unit conversions only; locators preserve the printed millimetre values.

The pressure statement on PDF page 43 (printed page 29) refers to the apparatus rooms: approximately 10^-5 Pa in the sample room and approximately 10^-6 Pa in the analyzer room. It is not assigned as a vapor pressure or sample total-pressure condition. The source does not print a run-specific total pressure, fO2, or gas mixture for these activity series. The figure-only activity curves therefore have no per-point pressure or fO2 coordinates in the extract.

## Figure inventory and excluded tables

Figure records are located at the absolute PDF page of the printed figure. Figures 6.1–6.4, 6.6–6.8 and 6.18–6.20 are literature comparisons for alkali-oxide activities in silicate and alumina systems. Figures 6.21–6.22 show the thesis's own KEMS-derived K2O and Na2O activities for samples 1–3 and 4–6, respectively, compared with modified-quasichemical calculations. Figures 6.23–6.40 are author model-prediction plots in KAS, NAS, Fe-bearing and Ca-bearing systems. None of the figures was digitized. Figure 6.5 is an FeO-activity comparison and is outside the alkali-oxide scope; Figures 6.9–6.17 concern other oxide activities.

The source review flags a sample 6 SiO2 discrepancy of 0.725/0.735. Table 5.1 is stored as printed (72.5 mol% SiO2), while the figure record preserves its plotted-sample label independently. It also flags a temperature-bound conflict in Figure 6.18 and a K2O/Na2O label conflict in the Na2O-study Figure 6.32. These figure values and labels were not used to create numeric rows or silently reconcile the conflicts.

Table 2.1 (PDF page 21) surveys brown- and hard-coal ashes, not alkali-oxide activities or KEMS results. MinerU QA reported 96.4% numeric agreement with three mismatches; the full-page render was checked, but the table is outside the extracted activity scope. Tables 6.1 and 6.2 (PDF pages 93 and 94) describe real coal samples and reduced coal-ash model compositions, respectively; neither tabulates alkali-oxide activities or KEMS output, so they are excluded.

## Tool comparison and isolated migration

`tools/mineru_table_draft.py compare` was run with a Table 4.1-only view against its raw MinerU index and a Table 5.1-only view against its text MinerU index. Both runs report zero locator issues. The comparer checks numeric observation points, while the retained table values live in experiment/context registries and the source result values occur only in figures. Both comparison CSVs therefore contain only the tool's header row; this is an empty comparison set, not evidence that any plotted value matched a draft table.

The real `Migrator` isolated run produced 1 work, 1 bench, 6 experiments, 36 context records, 0 observations, 0 unavailable observations, and 0 hard validation issues. The six experiments migrated their Table 5.1 printed composition, Table 5.1 locator (PDF page 41/table 5.1), and §6.2 temperature interval. The experiment pressure state remains unknown; no fO2 was added. The bench carries the printed apparatus facts, including chamber background pressures in the apparatus-only context. Expected regenerated store delta: +1 work, +1 bench, +6 experiments, +36 contexts, +0 observations. No battery store regeneration was committed.

## Verification status

The focused Oeltjen migration and fidelity test passed (1 test); it pins four Table 5.1 values to PDF page 41 and the printed 6-row count, checks Table 4.1 on PDF page 29, and verifies that changing a pinned value from 75.2 to 75.3 makes the fidelity check fail. `tests/test_yaml_flow_scalar_commas.py` passed (17 tests). In `tests/test_literature_extracts.py`, the Oeltjen parameter of `test_fidelity_sample_matches_extract` passed (1 test), the isolated repo-extract validator passed (1 test), and a selected run passed 158 tests (636 deselected). The complete file remains UNVERIFIED-local: store-wide pilot-count/merge checks did not finish within the 300-second per-call ceiling.

# Snyder and Carmichael (1992) FeO activity extraction audit

## Scope and counts

This extract preserves the Fe0.947O activities and activity coefficients in Table 4, their Table 2 run conditions, the post-run glass compositions in Table 3, and the printed FeO fit and calibrated range in Tables 5–6. Table 1 starting-material compositions are retained as context. Table 3 contains 44 phase analyses; all 21 glass rows are recorded, while the 23 olivine, leucite, and plagioclase analyses are not liquid-composition conditions for these activity values.

Table 4 has 14 run rows. Its Fe0.947O activity column contributes 14 numeric observations and its Fe0.947O activity-coefficient column contributes 14, for 28 numeric values. The Eq. (15) fit is one typed-refused observation with one unavailable migrated value because no exact quantity token describes this multivariate natural-log activity fit. No EMF measurement is reported. No plot-only values are digitized. The isolated migration produced 29 observations, 0 hard issues, 28 numeric values, and 1 unavailable value. Expected store delta without regeneration: 28 numeric observations and one unavailable typed refusal.

The companion cell audit lists 558 printed source cells: 519 numeric cells, 21 Table 2 phase strings, 14 blank Table 2 alloy cells, and 4 blank Table 5 coefficient/standard-error cells. Every numeric and absent cell in that audit is checked against a page render. Row/column assignment was checked against the render for each table.

## Cell-level provenance and render checks

| Printed table | PDF page / printed page | Printed rows and cells retained | Verification |
| --- | --- | --- | --- |
| 1 | 2 / 304 | Seven specimen columns; 13 printed composition and total cells in each; specimen footnote suffixes ¹–⁴ | (b) Page 2 render checked against the machine text and MinerU draft. |
| 2 | 3 / 305 | All 21 run rows; temperature, log10 fO2, duration, phase-product cell, and both alloy cells, including blanks | (b) Page 3 render checked against MinerU and pdftotext. Every phase string, including `gl+ol¹`, is pinned by the focused test. |
| 3 | 4 / 306; 5 / 307 | All 21 glass rows; n, 11 oxide mean cells with parenthetical standard deviations where printed, and sum | (b) Pages 4–5 renders checked cell by cell. The target rows are also pinned in the focused test. The page 5 heading reads “Table 3. (Continued).” |
| 4 | 7 / 309 | Fourteen run rows, activity column 5 and Fe0.947O gamma column 8 | (b) Page 7 render checked cell by cell. The mapping and compare output bind each printed row and column. |
| 5 | 9 / 311 | Fe0.947O fit coefficients, standard errors, regression statistics, and the blank dFe2O3 and dMgO value/error cells | (b) Page 9 render checked. The focused test pins the complete Fe0.947O block and absences. |
| 6 | 10 / 312 | Ten Fe0.947O/Fe2O3 temperature, log10 fO2, and liquid-composition ranges, minimum and maximum | (b) Page 10 render checked; values are pinned in the focused test. |

MinerU QA numeric agreement was 0.991 for Table 1, 0.985 for Table 2, 0.901 for the first Table 3 block, 0.784 for the Table 3 continuation block, 1.000 for Table 4, 0.920 for Table 5, and 0.955 for Table 6. The 22 and 70 Table 3 mismatches, 3 Table 5 mismatches, 2 Table 6 mismatches, and lower-count Table 1/2 mismatches were resolved against the page renders. The Table 3 continuation rows on PDF page 5 were read from the render and pdftotext; MinerU's continuation block did not provide caption evidence for that page.

The row and column checks include the assignment of every Table 4 value to its run identifier and target column, each Table 3 composition to the `-gl` glass row (not an olivine row), each Table 2 phase label to its printed run, and Table 5/6 numeric cells to the Fe0.947O/Fe2O3 columns. The focused test independently pins both Table 4 endpoints, an interior activity and gamma, all 28 migrated values, every linked run's conditions, and the printed 48-59 phase footnote.

The MinerU table-draft compare reports 28 matched values, 0 mismatches, 0 locator errors, and 4 unverifiable Table 3 caption checks on PDF page 5. Those four locators refer to the 83-58 glass analysis in the two Table 4 observations and the 83-58/83-66 rows in the Table 3 context. The page 5 render confirms the continued Table 3 caption and both row assignments. The compare output is committed as `snyder-carmichael-1992-olivine-liquid-feo-activity.mineru-compare.csv`.

## Conditions, reference state, and method

The Experimental Procedures state that all experiments were conducted at one atmosphere pressure. This is stored as 101325 Pa. The procedure says CO and CO2 were mixed to control fO2, but prints no gas ratio. Table 2 prints log10 fO2 to two decimal places and does not state its pressure reference. As permitted by the task, the migrator's bar default is used only to populate the identity fO2 axis for these two-decimal values; the missing pressure reference remains explicitly typed absent. Table 6's fO2 values are retained only as printed fit-domain bounds and are not assigned an identity pressure reference.

Table 2's reported-temperature uncertainty is within 3 K. Its oxygen-fugacity uncertainty is ±0.05 log10 units. The runs were quenched into water; the source does not state that redox was preserved or altered. The analyzed Table 3 rows are post-run glass compositions. Table 4 values are calculated liquid activities, so the observation phase is `l`; their composition conditions point to the printed glass analyses.

The paper states that activities are relative to a solid standard state at temperature, uses solid oxides as reference states, and explicitly chooses Fe0.947O rather than estimated stoichiometric FeO. The stored reference formula is exactly Fe0.947O. The paper does not name its polymorph, so the identity keeps polymorph unknown and does not label it wustite. Table 4 identifies the FeO activity coefficients as liquid-phase coefficients. The exact reference-state statements and equations (2) and (4) are in the extract's standard-state context. The paper's footnote says ln a_FeO is taken approximately equal to ln a_Fe0.947O in subsequent calculations, relative to the stated 20% activity uncertainty.

The Table 4 outputs are the authors' calculated values. Equation (4) relates liquid Fe0.947O activity to alloy Fe activity, fO2, and reaction free energy; the required alloy thermodynamic inputs are not all printed as per-run values in this source. These values therefore use `author_derived` and have no derivation parents. The fit coefficients are likewise the authors' reported fit outputs. No activity or fit value is recomputed in this extraction.

Equation (15) fits ln a_i as a function of T, log10 fO2, and oxide mole fractions. The extractor has no exact type token for this printed multivariate natural-log activity fit. The observation remains typed-refused with a descriptive quantity label; it is not assigned the near-miss activity-coefficient temperature-fit token. This gap is checked against `simulator/battery/enums.py::Quantity.ACTIVITY_COEFFICIENT_TEMPERATURE_FIT`, `simulator/battery/migrate.py::QUANTITY_ALIASES`, `simulator/battery/migrate.py::TYPE_QUANTITY`, and `tools/validate_literature_extracts.py::OBSERVATION_TYPES`.

Figures 3 and 4(a) are recorded as figure-only, undigitised Fe0.947O activity candidates. The marker/contour plots were not entered as point values.

## Verification

- Focused source test: 2 passed. It pins all 28 migrated numeric values and their conditions, all 21 Table 3 glass compositions, Table 1, Table 2 run markers/blanks, the Fe0.947O fit and ranges, and an interior mutation that changes the 48-60 activity from 0.27 to 0.28 and fails.
- Isolated migration in the focused test: 29 observations, 0 hard issues, 1 unavailable fit value, no quantity-unknown or phase-unknown observation other than the explicitly typed fit quantity.
- Table 4 compare: 28 matched, 0 mismatches; the four page 5 continuation-caption checks are render-resolved as recorded above.
- `tests/test_yaml_flow_scalar_commas.py`: 17 passed. The j01 store census test: 1 passed.
- `tests/test_literature_extracts.py`: all 883 collected cases passed in bounded selections: 159 initial cases, 56 general/policy cases, 3 corpus-census cases, 628 per-extract fidelity samples, and 37 trailing source/identity cases (14 + 19 + 4).

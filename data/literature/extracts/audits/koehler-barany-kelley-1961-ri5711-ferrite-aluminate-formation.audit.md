# Koehler, Barany, and Kelley (1961), USBM RI 5711 extraction audit

## Scope and stored values

The extract includes NaFeO2, CaFe2O4, and Ca2Fe2O5. It stores the Table 7 298.15 K heats of formation from oxides and elements, Table 8 standard entropies, and every target row in Tables 9, 10, 12, 20, 21, and 23. The complete printed temperature rows and both reaction-property columns are in the extract and the numeric cell audit. Tables 9 and 10 each contain 22 printed rows, including the repeated 1050 K row with marker 2; Table 12 contains 28 rows (14 per half-table), including both 1050 K rows. The companion `.target-values-comparison.csv` lists all 329 target property values with any other same-compound value cited in the report beside the relevant Table 7 entries. All source values and units remain in their printed units; no converted twin values were added.

| Compound | Table 7 from oxides, 298.15 K | Table 7 from elements, 298.15 K | Table 8 S298 | Other value cited in this report for same compound |
| --- | ---: | ---: | ---: | --- |
| Ca2Fe2O5(c) | -10560 cal/mol | -511240 cal/mol | 45.1 cal/(deg mol) | No other same-compound formation value cited |
| CaFe2O4(c) | -14580 ±180 cal/mol from oxides (reaction 17 prose: ±130) | -363470 cal/mol | 34.7 cal/(deg mol) | NBS oxide-basis prior: -49.5 kcal/mol; cited element values are not printed |
| NaFeO2(c) | -18470 cal/mol | -166720 cal/mol | 21.1 cal/(deg mol) | NBS oxide-basis prior: +22.5 kcal/mol; cited element values are not printed and signs disagree |

Table 8 attributes the entropy values to Kelley and King (1961). The authors state that high-temperature heat-content data came from Kelley (1960) and were not retabulated; there are no printed Cp equations or H(T)-H(298) target rows in this report. No separate transition or fusion enthalpy is printed, and the report contains no incongruent-melting statement for these target compounds.

| Compound | Printed transition / melting markers |
| --- | --- |
| Ca2Fe2O5(c,l) | Melting at 1750 K in Tables 9 and 20 |
| CaFe2O4(c,l) | Melting at 1510 K in Tables 10 and 21 |
| NaFeO2(c,l) | α→β at 870 K, β→γ at 1270 K, melting at 1620 K in Tables 12 and 23 |

## Full-cell audit and comparison-tool limits

The cell audit contains 777 numeric stored cells, including value, temperature, uncertainty, and marker fields (locator page coordinates are excluded). Table 7/8 and target rows in Tables 9/10/12/20/21/23 were checked against rendered PDF pages 16–19, including every stored digit, sign, uncertainty, duplicate transition row, and table-column assignment. Calorimetric rows in Tables 1–4 (PDF pages 11–13) and the two NBS comparison values (PDF page 20) were also checked against renders. In Table 7, MinerU's first compound label reads `Ca2Fe2O8`; pdftotext and the page render confirm `Ca2Fe2O5`, which is the stored species assignment. The rendered pages settled the OCR disagreements in the target thermochemistry tables. The printed row counts are: Table 7, 3 target compounds; Table 8, 3 target compounds; Tables 9 and 10, 22 rows each; Tables 20 and 21, 28 rows each; Table 12, 28 rows; Table 23, 32 rows. On Tables 9 and 10, the two 1050 K rows carry identical values; the left-column row bears footnote 2 (Fe2O3 β→γ transition) and the right-column row is unmarked. The marker-map temperature paths in `.cells.csv` now follow the current list indexes for Tables 9, 10, and 12.

`mineru_table_draft.py compare` was run with the table draft index and mapping. It reports zero comparable points because its numeric-point matcher accepts `value`, `pressure_*`, `activity`, `gamma`, and `T_m_K`, while this extract stores the printed thermochemical properties as `delta_fH` and `delta_fG`. The empty compare result is therefore not treated as cell verification; the full rendered-cell audit is in the companion `.cells.csv`.

The five direct calorimetric reaction-enthalpy source observations remain typed-refused schema gaps: the Ca2Fe2O5 and CaFe2O4 results, NaFeO2 reaction 25, and two separate reaction 30 results at 303.15 K (+7,640 ±40) and 298.15 K (+8,240 ±40). `simulator/battery/enums.py::Quantity` and `simulator/battery/migrate.py::QUANTITY_ALIASES` have no exact reaction-enthalpy token; each row has a descriptive non-token quantity. The Table 7 reductions retain their printed measured reaction lineage; reaction 30 is recorded separately at each reported temperature. The extract does not bind a total or standard pressure because none is printed. No schema or migrator changes were made.

The isolated migration produces 334 observations, zero hard issues, and zero registry issues. Its 48 unavailable entries comprise 38 missing-method axes and 10 quantity/value entries for the five typed-refused reaction-enthalpy source observations. Exactly those five observations have unknown quantities; no temperature axis or phase is unknown. The migration retains the printed 950/1050 K repeated NaFeO2 rows as distinct values, so the previous collision waiver is removed.

Three isolated-migration identity checks (quantity profile axes):

| Migrated source row | Phase | Quantity | Temperature (K) | Total pressure | fO2 |
| --- | --- | --- | ---: | --- | --- |
| Table 7 CaFe2O4 from elements | cr | delta_fH | 298.15 | unknown (not printed) | unknown (not printed) |
| Table 12 NaFeO2 oxide enthalpy | cr | delta_fH | 298.15 | unknown (not printed) | unknown (not printed) |
| Table 20 Ca2Fe2O5 liquid enthalpy | l | delta_fH | 1750 | unknown (not printed) | unknown (not printed) |

## Table cell audit files

- `.mapping.yaml` records table/page mapping, evidence decisions, source comparisons, and render checks.
- `.compare.csv` is the table-tool compare output described above.
- `.cells.csv` maps each numeric stored value to its table, page, row, field, and verification route.
- `.target-values-comparison.csv` lists the target thermochemical values and within-source comparisons.


## Fix-pass render audit

At 300 dpi, the full rendered PDF pages 11, 12, 13, 16, 17, 18, 19, and 20 were re-read for every numeric cell recorded in `.cells.csv`. This includes all target calorimetry values on Tables 1–4 (pages 11–13), Table 7 uncertainties and formation values (page 16), Table 8 entropies (page 17), every row/value/temperature/marker in Tables 9 and 10 (page 17), Table 12 (page 18), Tables 20, 21, and 23 (page 19), the Table 12 marker temperatures, and the NBS comparison values (page 20). Each `.cells.csv` row names the render page used.

The render resolves two prior digit errors: Table 7 CaFe2O4 from oxides is -14,580 ±180 cal/mol (Table 7, PDF page 16); page 13 reaction (17) separately prints -14,580 ±130, so the uncertainty disagreement is retained. Table 20 Ca2Fe2O5 ΔF° at 1,200 K is -392.3 kcal/mol (PDF page 19), not the pdftotext OCR reading -382.3. Table 12 has two 1,050 K rows; both properties retain both rows, and the first rows at 950 and 1,050 K carry markers 2 and 3 for Fe2O3 transitions. Its NaFeO2 melting marker is 6.

The NBS paragraph is PDF page 20 (published page 13). It explicitly converts NBS element values to heats of formation from oxides; the cited element values are not printed. The comparison therefore pairs -49.5 kcal/mol with Table 7's CaFe2O4 oxide value and +22.5 kcal/mol with Table 7's NaFeO2 oxide value.

For every (c,l) table series, the marked melting-temperature row remains `cr`; the following unmarked duplicate-temperature row and higher-temperature rows are split into a matching `phase: l` observation. The row boundary is the printed melting marker in Tables 9, 10, 12, 20, 21, and 23 (PDF pages 17–19).

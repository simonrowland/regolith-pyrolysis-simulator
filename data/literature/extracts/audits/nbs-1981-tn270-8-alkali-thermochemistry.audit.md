# NBS TN 270-8 alkali thermochemistry audit

## Scope and row reconciliation

Table 99 contributes 11 sodium formula/state rows and 33 populated cells in the requested 298.15 K columns. Table 100 contributes 7 potassium rows and 16 populated cells. The combined extract has 18 rows and 49 numeric cells: 42 exact quantity tokens and seven H°298−H°0 cells retained as typed quantity refusals. The separately printed 0 K formation-enthalpy column is excluded.

The Table 99 rows are NaO2 (c, g), Na2O (c, g), Na2O2 (c), Na2SiO3 (c), Na2Si2O5 (c, c2, c3), Na4SiO4 (c), and Na6Si2O7 (c). The three Na2Si2O5 crystal entries remain separate: c is formerly β, c2 is formerly α and is stable from 951 K to the 1147 K melting point, and c3 is printed as unstable. Na2SiO3 glass (gl) and its au form are outside the requested crystal/liquid/gas scope.

The Table 100 rows are KO2 (c), K2O (c, g), K2O2 (c, g), K2SiO3 (c), and K2Si2O5 (c). K4SiO4 and K6Si2O7 do not appear in the potassium table. Source state is preserved in `phase_as_printed`; c/c2/c3 map to the actual `cr` phase token and g maps to `g`.

## Cell audit and table comparison

The committed `.cells.csv` has one record for each of the 49 cells, including printed table number, absolute PDF page, MinerU draft file and row/column, actual MinerU cell text, Tesseract row/cell read, formula, printed state, and verification. Pages 41 and 61 contain the sodium rows; pages 72 and 88 contain the potassium rows. Row and column assignments for all four pages were checked against their renders.

MinerU and Tesseract agree on the digits/sign for 47 of 49 cells. The two OCR conflicts were rendered: Tesseract read NaO2(c) S° as `oll` (the cell is 27.7), and its K2O(c) sign was ambiguous (`~86.4`; the cell is −86.4). The KO2(c) ΔfH° read is −68.10 in MinerU and Tesseract; pdftotext produced `-68.1(1`, so the render was checked and confirms −68.10. The table parser flags the three printed trailing-decimal cells −868., −15., and −38. as nonnumeric; each was checked against the render and retains the exact spelling in `value_as_printed`.

The row/column-aware comparison reports 46 matched, 3 mismatches, 0 no-match, and 49 total. Its only mismatches are those three trailing-decimal cells, where the draft parser does not create a numeric candidate; the cell audit records the render resolution. The MinerU QA pre-screen found 136 table blocks, flagged 127, and did not rank these target tables among its 40 highest-scoring tables. Invented `<details>` blocks were ignored.

## Standard state and conditions

The source states that pure condensed substances use the standard state under one atmosphere and that gases use the hypothetical ideal gas at unit fugacity (page 10, “Conventions Regarding Pure Substances”). Its footnote states that one standard atmosphere equals 101325 pascal. Each condensed observation therefore carries `standard_pressure_Pa: 101325` with that page-10 locator; the migrator maps it to identity standard pressure. Gas rows retain “unit fugacity” as printed and carry no numeric pressure. No experiment total pressure, fO2, or composition is printed. Every observation carries T_K = 298.15 with a point-condition locator to its table heading.

After isolated migration, the printed identity axes are:

| Observation | composition | total_pressure_Pa | fO2_Pa | temperature_K | standard_pressure_Pa |
|---|---|---|---|---:|---:|
| Na2O(c) ΔfH°, page 41, Table 99 | not applicable | not applicable | not applicable | 298.15 | 101325 |
| NaO2(g) S°, page 41, Table 99 | not applicable | not applicable | not applicable | 298.15 | unknown |
| K2SiO3(c) S°, page 88, Table 100 | not applicable | not applicable | not applicable | 298.15 | 101325 |

The source's one-atmosphere standard-state definition is bound to `standard_pressure_Pa`; it is not recast as an experiment total pressure. For these quantity profiles, composition, total pressure, and fO2 axes are not applicable. The gas standard-state unit fugacity is not converted to a numeric pressure; its `standard_pressure_Pa` remains unknown.

## H°298−H°0 schema gap

The seven H°298−H°0 cells use the explicit descriptive non-token quantity `enthalpy_298K_minus_0K_as_printed`. `Quantity.H_MINUS_H298` is the existing enum member (`simulator/battery/enums.py:91`); the identity subtype parser rejects reference temperatures at or below 0 K (`simulator/battery/identity.py:800-805`), and the observation migrator adds only positive `reference_temperature_K` values (`simulator/battery/migrate.py:9151-9153`). The compilation-column alias `_COMPILATION_CELL_QUANTITY["enthalpy_increment"]` maps to `Quantity.H_MINUS_H298` (`simulator/battery/migrate.py:6770`), which is the near-match but cannot represent H°298−H°0.

The direct observation mapper accepts explicit `QUANTITY_ALIASES` keys or exact `Quantity` enum values (`simulator/battery/migrate.py:6641-6652`); no token represents this 298 K minus 0 K quantity. Its type, `heat_capacity_and_heat_content_series`, is not in the `TYPE_QUANTITY` closed map (`simulator/battery/migrate.py:442-451`), so omitting `values.quantity` would not describe the gap safely. The descriptive non-token label keeps these seven rows quantity-unknown and unavailable without assigning a nearby enthalpy-increment token. The focused test pins the exact seven-row unknown set.

## Units, migration, and deferred entries

Formation energies and H°298−H°0 are stored in printed kcal/mol. The printed S° and C°p unit is `cal/deg mol`; the extract stores the migrator token `cal/deg-mole` and retains the printed wording in `units_as_printed`. The isolated real migrator produced 49 observations, 0 hard issues, 42 point values, 7 unavailable typed refusals, and 0 phase-unknown observations. The focused test also checks migrated unit values and asserts that unprinted composition, total pressure, and fO2 do not enter point conditions or applicable identity axes.

Follow-up candidates in Tables 99–100 include Na2SO4, Na2CO3, NaF, NaCl, NaBr, NaI, K2SO4, K2CO3, KF, KCl, KBr, and KI, along with the tables' related mixed salts, hydrates, and derivative sulfate/carbonate/halide entries. They are not extracted in this round.

# NBS TN 270-8 alkali thermochemistry audit

## Table 99 — sodium (intermediate table-group commit)

The requested sodium portion contains 11 printed formula/state rows and 33 populated cells in the requested 298.15 K columns. The 298 K enthalpy-of-formation column is column 6; column 5 is the separately printed 0 K enthalpy-of-formation column and is excluded. The 33 stored cells comprise 28 exact quantity tokens and five H°298−H°0 cells retained as typed quantity refusals.

Row reconciliation: NaO2 (c, g), Na2O (c, g), Na2O2 (c), Na2SiO3 (c), Na2Si2O5 (c, c2, c3), Na4SiO4 (c), and Na6Si2O7 (c). The three Na2Si2O5 crystal rows remain separate; c is identified as formerly β, c2 as formerly α, and c3 as unstable. The source's gl and au Na2SiO3 rows are outside the requested crystal/liquid/gas scope.

The cells CSV records all 33 values against the MinerU table draft row and column, Tesseract row read, printed row, printed table number, and absolute PDF page. Table row and column assignment was checked on the page renders for PDF pages 41 and 61. MinerU and Tesseract agree except that Tesseract reads NaO2(c) S° as `oll`; the render resolves it as 27.7. The Na6Si2O7(c) value `-868.` is read by both machine methods, but the draft table parser flags its trailing decimal point as nonnumeric; the render confirms `-868.`. The table compare report is 32 matched, 1 mismatch; that mismatch is resolved by the render check recorded in the cells CSV.

The MinerU QA pre-screen reports 136 tables and 127 flagged tables for the source. The target tables were not among the 40 highest-scoring rows in its report; the cell-level audit and render checks were still completed. Invented `<details>` blocks were ignored.

## Standard states and conditions

The source defines pure solids and liquids as standard state under one atmosphere and gases as hypothetical ideal gas at unit fugacity (PDF page 10, “Conventions Regarding Pure Substances”). Condensed rows carry the printed 101325 Pa standard pressure with the page 10 locator. Gas rows preserve “unit fugacity” as the standard-state wording and do not acquire a numeric pressure. Every stored cell has T_K = 298.15 with its table locator. No fO2 or composition is printed for these rows.

## H°298−H°0 schema gap

The printed H°298−H°0 column is not mapped to the nearby enthalpy-increment quantity. `Quantity.H_MINUS_H298` is declared at `simulator/battery/enums.py:91`; `QUANTITY_ALIASES["enthalpy_increment"]` maps to it at `simulator/battery/migrate.py:6770`. The subtype parser rejects a reference temperature at or below 0 K at `simulator/battery/identity.py:804-805`, and the extract reader only records a positive `reference_temperature_K` at `simulator/battery/migrate.py:9151-9153`. The five sodium H°298−H°0 cells are therefore explicit descriptive non-token quantities and will be asserted as quantity-unknown after isolated migration.

## Deferred entries

The remaining sodium and potassium sulfates, carbonates, and halides are follow-up candidates and are not extracted in this round. Table 100 coverage and the final combined counts are appended with the potassium table-group commit.

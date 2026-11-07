# Stebbins, Carmichael & Moret 1984 extraction audit

The extraction uses the MinerU table drafts and markdown first, followed by `pdftotext-layout.txt`. The PDF was used only through rendered pages to resolve flagged cells and verify each table's row and column layout. No MinerU `<details>` chart reconstruction was used as data.

## Printed rows reconciled

| Printed table | PDF page | Printed rows | Extracted record count | Result |
|---|---:|---:|---:|---|
| 1 | 4 | 23 compositions | 23 composition records | Reconciled |
| 2 | 5 | 29 glass fit rows | 29 fit rows | Reconciled |
| 3 | 7 | 136 relative-enthalpy points | 136 points in 18 series | Reconciled |
| 4 | 8 | 17 linear-fit rows | 17 fit rows | Reconciled |
| 5 | 9 | 9 glass-component rows | 9 coefficient rows | Reconciled |
| 6 | 9 | liquid-volume fit parameters | excluded | Outside requested scope |
| 7 | 10 | 12 liquid-component rows | 12 coefficient rows | Reconciled |
| 8 | 12 | 7 fit-comparison rows | 7 context rows | Reconciled |
| 9 | 12 | 18 liquid Cv/3R rows | 18 rows | Reconciled |
| 10 | 13 | 17 silicate rows | 47 entropy observations plus the complete 17-row table context | Reconciled |

Appendix 1 on PDF page 15 contributes the MgSiO3 liquid enthalpy increment, solution enthalpies, and the three printed MgSiO3 fusion-enthalpy estimates. The equations quoted from Stout and Piwinskii are located on PDF page 2. Figure-read values are not extracted.

## Full-cell verification

`rb86-stebbins-etal-1984.cells.csv` lists every numeric cell in the extracted tables, the printed row and column, the draft text, and its verification method. The numeric cells in tables 1–5 and 7–10, including the table 10 footnote and uncertainty cells, were checked against the page renders; row and column assignments were checked against each rendered table. Stored prose numbers from pages 2 and 15 are separately located in the cell file.

The Table 3 render confirms three independent composition-heading panels: rows 1, 19, and 32. The middle and bottom panels have their own Ab-An-Di headings. Their values and composition locators are bound to those headings, rather than to the first panel. The observed relative-enthalpy values remain increments from the printed glass/solid reference.

MinerU QA digit agreement with `pdftotext-layout.txt` was: Table 1 0.992, Table 2 0.956, Table 3 0.939, Table 4 0.898, Table 5 1.000, Table 7 0.986, Table 8 0.978, Table 9 0.986, and Table 10 0.944. Flagged tables were resolved against the page renders; in particular, Table 2 decimal/LaTeX cells, Table 3 multi-panel headings and numeric cells, Table 4 digits, and Table 10 footnote/uncertainty cells were render-checked. The complete QA output is retained in the worker audit notes; the per-cell compare output is committed as `rb86-stebbins-etal-1984.compare.csv`.

The table compare tool reports 12 matched, 257 mismatch, and 6 no-match rows, with no locator errors or unverifiable rows. Inspection of `tools/mineru_table_draft.py::_phase_matches_column` shows it accepts `gas`, `liquid`, and `solid`, while the extract uses the migrator's `glass`, `l`, and `cr` phase tokens. Thus the mismatch statuses are phase-filter false negatives; the printed cells were resolved from the page renders as recorded in the cell audit. The six no-match rows are the two prose equations and other non-table values. No comparison-tool code was changed.

## Migration and schema decisions

The isolated migration produced 275 observations, 0 hard issues, 79 point values, and 196 typed-unavailable rows. All 275 phase tokens mapped. The 196 quantity-unknown observations are exactly the six typed-refusal groups listed in the extract's schema-gap note: the 136 Table 3 increments plus the Appendix 1 increment, glass Cp coefficient vectors, glass partial-molar Cp vectors, normalized Cv/3R values, and equation-valued Cp fits. `Quantity.H_MINUS_H298` is not the printed 300 K glass/solid increment, and `Quantity.CP` is not a coefficient vector or Cv/3R; see `simulator/battery/enums.py::Quantity.H_MINUS_H298`, `simulator/battery/enums.py::Quantity.CP`, `simulator/battery/migrate.py::QUANTITY_ALIASES`, and `simulator/battery/migrate.py::TYPE_QUANTITY`. Observation-type coverage was checked at `tools/validate_literature_extracts.py::OBSERVATION_TYPES`.

Tables 2–4 identify closed Ab-An-Di compositions. Table 1 prints the basis as mol %. The printed Ab and An coordinates are retained, Di is derived only by closure, and the identity oxide composition uses the printed formula-unit splits normalized to 100 mol %. For example, Ab 12.5 mol %, An 12.5 mol %, Di 75 mol % gives oxide mole percentages SiO2 53.125, Na2O 1.5625, Al2O3 4.6875, CaO 21.875, MgO 18.75, which sum to 100. The formula splits and locators are recorded in `composition_derivation`.

Glass 70 retains its printed Table 1 values SiO2 48.83, TiO2 8.51, Al2O3 8.29, MgO 34.34 mol %, sum 99.97. The base migrator refuses that identity composition with `mole-percent composition does not close to 100 (sum=99.97)`; all four values remain unchanged.

No row receives an unprinted pressure or absolute fO2. Glass rows record quench state `not_stated` at page 3. Fitted temperature intervals are retained verbatim; no midpoint is used. The paper's glass and liquid phase assignments use `simulator/battery/enums.py::Phase.GLASS` and `simulator/battery/enums.py::Phase.L`; phase roles and tokens were checked at `simulator/battery/enums.py::PhaseRole` and `simulator/battery/enums.py::PHASE_BASIS_TOKENS`.

The isolated migration's required profile axes and mapped identity states for three observations are:

| Observation | Profile-required axes | composition | total pressure | fO2 | temperature |
|---|---|---|---|---|---|
| Table 4 FeAb linear Cp fit | `per`, `standard_pressure_Pa`, `temperature_K` | not applicable | not applicable | not applicable | unknown: source interval `[1570, 1826]` K, no midpoint |
| Table 10 Fe2SiO4 fusion entropy | `subtype`, `temperature_K` | unknown: formula identifies substance, not bulk melt composition | not applicable | not applicable | 1490 K |
| Appendix 1 MgSiO3 fusion enthalpy, authors' solid data | `composition`, `subtype`, `temperature_K` | 50 mol % MgO + 50 mol % SiO2 | not applicable | not applicable | unknown: no single temperature mapped |

The physical locator for fayalite is Table 10, row 2, PDF page 13: the printed Fe2SiO4 fusion entropy is 8.56 ± 0.11 J g-atom^-1 K^-1 at 1490 K, attributed to Stebbins & Carmichael 1983. No FeAl2O4 row is printed.

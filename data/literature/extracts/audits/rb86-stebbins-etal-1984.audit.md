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

Appendix 1 contributes the MgSiO3 liquid enthalpy increment and solution enthalpies on PDF page 15, and three MgSiO3 fusion-enthalpy estimates on PDF page 16. The equations quoted from Stout and Piwinskii are located on PDF page 2. Figure-read values are not extracted.

## Full-cell verification

`rb86-stebbins-etal-1984.cells.csv` lists every numeric cell in the extracted tables, the printed row and column, the draft text, and its verification method. The numeric cells in tables 1–5 and 7–10, including the table 10 footnote and uncertainty cells, were checked against the page renders; row and column assignments were checked against each rendered table. Table 1's inline labels override the nominal header positions for Li2O 50.67, Rb2O 28.58, BaO 32.35, and SrO 1.01; these labels and all attached composition copies are corrected. Stored prose numbers from pages 2, 15, and 16 are separately located in the cell file.

The Table 3 render confirms three independent composition-heading panels: rows 1, 19, and 32. The middle and bottom panels have their own Ab-An-Di headings. Their values and composition locators are bound to those headings, rather than to the first panel. The observed relative-enthalpy values remain increments from the printed glass/solid reference.

MinerU QA digit agreement with `pdftotext-layout.txt` was: Table 1 0.992, Table 2 0.956, Table 3 0.939, Table 4 0.898, Table 5 1.000, Table 7 0.986, Table 8 0.978, Table 9 0.986, and Table 10 0.944. Flagged tables were resolved against the page renders; in particular, Table 2 decimal/LaTeX cells, Table 3 multi-panel headings and numeric cells, Table 4 digits, and Table 10 footnote/uncertainty cells were render-checked. The complete QA output is retained in the worker audit notes; the per-cell compare output is committed as `rb86-stebbins-etal-1984.compare.csv`.

The table compare tool reports 12 matched, 257 mismatch, and 6 no-match rows, with no locator errors or unverifiable rows. Inspection of `tools/mineru_table_draft.py::_phase_matches_column` shows it accepts `gas`, `liquid`, and `solid`, while the extract uses the migrator's `glass`, `l`, and `cr` phase tokens. Thus the mismatch statuses are phase-filter false negatives; the printed cells were resolved from the page renders as recorded in the cell audit. The six no-match rows are the two prose equations and other non-table values. No comparison-tool code was changed.

## Review corrections

The review at `ror-sm84cp-review` identified nine issues. All nine are corrected in this revision, with the printed cells retained.

1. **Table 10 entropy basis:** all 47 fusion-entropy rows retain their printed `J g-atom^-1 K^-1` unit and `per: mol_atom` basis, including fayalite's `8.56 ± 0.11`. They are typed-refused because the current fusion score path in `simulator/battery/compilation_tier.py::predict_thermo_attempt` admits the value without preserving the atom basis. No formula-mole twin is created.
2. **Table 1 inline component labels:** the page 4 render confirms Li2O 50.67 for Li2SiO3, Rb2O 28.58 for Rb2Si2O5, and BaO 32.35 plus SrO 1.01 for BaSi2O5. `table_1_compositions` and every attached copy now use those labels. The focused test pins the three source rows and their Table 4 copies.
3. **Table 7 quantity:** all 12 liquid oxide-component values remain stored with their printed ranges, ± values, normalized Cp/3R and Cv/3R columns, but are typed-refused as partial-molar oxide-component Cp in silicate liquid. `Quantity.CP` is not an exact partial-molar component quantity, and `simulator/battery/migrate.py::QUANTITY_ALIASES` and `simulator/battery/migrate.py::TYPE_QUANTITY` provide no exact alias for it. Their units remain `J gfw^-1 K^-1`; no `mol_species` basis is claimed.
4. **Table 4 gfw basis:** all 17 fitted b values remain as printed but are typed-refused because Table 4 defines b per oxide-mixture gram formula weight. `simulator/battery/enums.py::PerBasis` has no mixture-gfw token, so the unsupported `mol_species` basis is removed and the unit field retains `J gfw^-1 K^-1`. The MgSiO3 cross-check is consistent: `125940 J gfw^-1 × 2 oxide moles/gfw = 251.88 kJ mol^-1`, which agrees with Appendix 1's rounded `251.8 ± 1.5 kJ mol^-1`. The Table 4 column locator is corrected to printed `b`.
5. **Fusion phase:** all 47 Table 10 entropy observations and all three Appendix 1 fusion-enthalpy observations use phase `cr`, matching the crystal side required by `simulator/battery/compilation_tier.py::predict_thermo_attempt`. No polymorph is added where the paper names none.
6. **Appendix unit and locator:** the three Appendix values 77.4, 81.2, and 66.0 are recorded literally as `J mol^-1` on PDF page 16 / printed page 146. The magnitude may indicate a source unit error: `8.4 × 5 × 1840 = 77.28 kJ mol^-1`; that interpretation is kept separate and is not substituted. The three rows are typed-refused, and the `66.0` authors' calculation remains `measured_reduced`.
7. **Ternary composition provenance:** all 103 Ab-An-Di-derived condition compositions (12 Table 2 rows, 82 Table 3 points, and 9 Table 4 fits) now carry the located `inference` read by `simulator/battery/migrate.py::_composition_located_from_plain`. It records the printed Ab/An coordinates, the printed Di coordinate where present or the closure `100 - Ab - An` where omitted, the Table 1 mol-percent basis, the three printed endmember formulae, their stoichiometric oxide splits, and normalization to 100 mol %. Example: Ab 12.5, An 12.5, Di 75 mol % gives oxide mol % SiO2 53.125, Na2O 1.5625, Al2O3 4.6875, CaO 21.875, MgO 18.75; these sum to 100. Table 3's printed Di coordinates remain printed rather than being relabeled as derived.
8. **Excluded Table 7 components:** BaO, Li2O, and Rb2O no longer claim a 58-composition regression or list its 15 inputs. Each keeps the paper's page 8 derivation from the corresponding silicate Cp and the SiO2 component Cp derived from regression, with exactly those two stored parent rows. Their printed ± widths remain; the source gives no separate uncertainty calculation for the excluded-component derivation, so the test pins that provenance as unspecified.
9. **Fusion composition test:** the test checks fayalite's unknown experimental bulk composition at `point_conditions.composition`. Because the quantity is now typed-refused, it does not assert an identity profile for the descriptive quantity label.

## Migration and schema decisions

The isolated migration at the review tip had 275 observations, 0 hard issues, 79 point values, and 196 typed-unavailable rows. After the corrections it has 275 observations, 0 hard issues, 0 point values, and 275 typed-unavailable rows. All phase tokens map. Quantity-unknown rows are exactly the ten typed-refusal groups in the extract: 137 relative-enthalpy increments; 29 glass Cp coefficient vectors; 18 normalized Cv/3R values; 9 glass partial-molar Cp vectors; 2 equation-valued Cp fits; 1 relative-enthalpy fit equation; 17 mixture-gfw Table 4 coefficients; 12 Table 7 partial-molar oxide-component Cp rows; 47 per-gram-atom Table 10 fusion entropies; and 3 Appendix fusion enthalpies with the literal J/mol discrepancy. Observation-type coverage was checked at `tools/validate_literature_extracts.py::OBSERVATION_TYPES`. `Quantity.H_MINUS_H298` is not the printed 300 K glass/solid increment, and `Quantity.CP` is not a coefficient vector, normalized Cv/3R, or partial-molar oxide-component Cp; see `simulator/battery/enums.py::Quantity.H_MINUS_H298`, `simulator/battery/enums.py::Quantity.CP`, `simulator/battery/enums.py::Quantity.ENTROPY_OF_FUSION`, `simulator/battery/enums.py::PerBasis`, `simulator/battery/migrate.py::QUANTITY_ALIASES`, and `simulator/battery/migrate.py::TYPE_QUANTITY`.

| Migration state | POINT | UNAVAILABLE | Total | Hard issues |
|---|---:|---:|---:|---:|
| Review tip `d7b260303cd69279184c7b738fc3817a670ff226` | 79 | 196 | 275 | 0 |
| Corrected extract | 0 | 275 | 275 | 0 |

Tables 2–4 identify compositions in the closed Ab-An-Di system; Table 1 states the mol-percent basis. The printed Ab and An coordinates remain unchanged, printed Di values are retained where present, and omitted Di values are derived only by closure. Formula splits are derived from the endmember formulas printed in the Table 2 caption. The structured inference is attached to each condition composition and survives migration through `simulator/battery/migrate.py::_composition_located_from_plain`; the focused test checks the migrated located inference and all nine operands. These observations remain typed-refused, so the descriptive quantity does not create a scoring identity profile. The narrative `composition_derivation` is retained as supporting context.

Glass 70 retains its printed Table 1 values SiO2 48.83, TiO2 8.51, Al2O3 8.29, MgO 34.34 mol %, sum 99.97. The base migrator refuses that identity composition with `mole-percent composition does not close to 100 (sum=99.97)`; all four values remain unchanged.

No row receives an unprinted pressure or absolute fO2. Glass rows record quench state `not_stated` at page 3. Fitted temperature intervals are retained verbatim; no midpoint is used. The paper's glass and liquid phase assignments use `simulator/battery/enums.py::Phase.GLASS` and `simulator/battery/enums.py::Phase.L`; phase roles and tokens were checked at `simulator/battery/enums.py::PhaseRole` and `simulator/battery/enums.py::PHASE_BASIS_TOKENS`.

The isolated migration's original identity-profile sample, before the review refusals, was:

| Observation | Profile-required axes | composition | total pressure | fO2 | temperature |
|---|---|---|---|---|---|
| Table 4 FeAb linear Cp fit | `per`, `standard_pressure_Pa`, `temperature_K` | not applicable | not applicable | not applicable | unknown: source interval `[1570, 1826]` K, no midpoint |
| Table 10 Fe2SiO4 fusion entropy | `subtype`, `temperature_K` | not applicable by fusion profile | not applicable | not applicable | 1490 K |
| Appendix 1 MgSiO3 fusion enthalpy, authors' solid data | `composition`, `subtype`, `temperature_K` | 50 mol % MgO + 50 mol % SiO2 at the point-condition and identity paths | not applicable | not applicable | unknown: no single temperature mapped |

After the typed refusals, these three migrated rows have no quantity profile. Their actual identity fields are pinned by the focused test:

| Observation | Quantity | composition | total pressure | fO2 | temperature |
|---|---|---|---|---|---|
| Table 10 Fe2SiO4 fusion entropy | unknown | unknown | absent | absent | 1490 K |
| Table 10 Fe2SiO4 entropy at 1600 K | unknown | unknown | absent | absent | 1600 K |
| Table 10 MgSiO3 fusion entropy | unknown | unknown | absent | absent | 1840 K |

The physical locator for fayalite is Table 10, row 2, PDF page 13: the printed Fe2SiO4 fusion entropy is 8.56 ± 0.11 J g-atom^-1 K^-1 at 1490 K, attributed to Stebbins & Carmichael 1983. It remains stored but typed-refused; no FeAl2O4 row is printed.

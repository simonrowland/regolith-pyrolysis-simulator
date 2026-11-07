# Hastie & Bonnell (1980) extraction audit

## Machine reads and full-cell audit

MinerU table blocks and Markdown were read first, then checked against the born-digital `pdftotext-layout.txt`. The seat's table draft and QA tools were used. The complete cell ledger is `hastie-bonnell-1980-nbsir-80-2169-molecular-chemistry.cells.csv`: 509 nonblank stored cell instances across the included tables and comparison rows. Each listed cell was checked against its rendered PDF page, with row and column assignment checked once per table. The count includes duplicated stored representations where a full table context and a separate typed or admitted observation both retain printed cells.

The table QA pre-screen found 18 table blocks, nine flagged blocks, no scanned-table pages, and 63 invented `<details>` blocks. The invented blocks were excluded. The flagged readings included fused-reference-decimal and LaTeX marks on PDF page 22; numeric mismatches on PDF pages 122, 163, 167, 192, 197, 199, and 201; and low numeric agreement on PDF pages 32 and 39. Every included table was checked cell by cell against its render. The rendered read resolved Appendix D.2 O2 row 5 as `6.74(-3)` and PO row 5 as `2.63(-4)`; Appendix E.4 PO ΔHf,2000 as `-26.8` where the text layer reads `-26.3`; and the Appendix B Table 2 footer as printed page 1130. The audit ledger records the source row, column, absolute PDF page, printed table, cell text, and render method.

The table comparer output is committed as `hastie-bonnell-1980-nbsir-80-2169-molecular-chemistry.compare.csv`. It emits two locator errors and no numeric mismatches or unverifiable rows. PDF pages 114 and 122 carry appended report page numbers 1122 and 1130; their table captions and footers were verified on the render. The comparer applies the ordinary PDF offset and flags those folios. Appendix E captions E.1 through E.4 and the Appendix B page numbers were verified from the renders; the draft index caption evidence was corrected in the scratch comparison input.

## Printed row census

| Printed locator | PDF page | Printed body rows | Stored record |
| --- | ---: | ---: | --- |
| Table 2 | 22 | 10 | Main table context plus separate measured and predicted pressure records |
| Table 3 | 32 | 15 | Full typed-refusal reaction parameter table |
| Table 4 | 39 | 10 | Full typed-refusal model reaction-rate table |
| Appendix B Table 2 | 122 | 6 | Full typed-refusal flame-temperature comparison |
| Appendix D Table D.1 | 163 | 3 | Full typed-refusal pressure ratios, Kp, and enthalpy table |
| Appendix D Table D.2 | 167 | 7 | Full table, modeled PO/O2 pressures, and relative-to-PO refusals |
| Appendix E Table E.1 | 192 | 4 | Full typed-refusal thermal-function coefficient table |
| Appendix E unnumbered PO comparison | 193 | 3 | Three separately attributed formation enthalpies |
| Appendix E Table E.2 | 197 | 6 | Full comparison table, bond-energy refusal, separate entropy rows |
| Appendix E Table E.3 | 199 | 4 | Full table, entropy values, POH estimate, and relative-frame refusals |
| Appendix E Table E.4 | 201 | 8 | Full table, entropy and elemental-reference formation enthalpies |

The census matches the printed table bodies. Rows are assigned to the printed species or reaction labels. The duplicate Table D.2 image on PDF page 169 was checked as a duplicate and stored once. Tables on PDF pages 21, 114, and 143 were rendered and excluded as qualitative or apparatus/flame-property inventories outside the requested vapor and thermochemistry results.

## Evidence, units, and schema

Table 2 measured partial pressures are `measured_reduced`: the report gives pressure coefficients normalized from ion signals using estimated ionization cross sections and the mass-balance assumption. Parenthesized predictions are separate `model_derived` rows, with the CEC-76/JANAF method and references. Table D.2 calculated PO and O2 pressures are `model_derived`, with method and references. The present-study PO formation-enthalpy estimate is `measured_reduced`; attributed Drowart and JANAF values are `quoted_attributed`. Appendix E formation enthalpies computed at 2000 K are `model_derived` from the printed thermodynamic data base and cited references. The 18 admitted S observations store the supported unit token `cal/mol/K` and retain the source spelling `cal/(deg mol)` in `values.units_as_printed`; the p. 197, 199, and 201 renders confirm the printed form. No raw signal values or cross sections were tabulated; where the authors' own reduction uses unpublished inputs, the extract locates that fact and method.

Printed Table 2 coefficients remain in the printed `10^-4 atm` scale. Predicted columns are not measurement uncertainties. TMP is preserved as printed, but is not mapped to total flame pressure. The source gives burner-input gas ratios rather than an fO2 value; these ratios are recorded as gas-mix conditions. No total pressure, room temperature, ambient condition, or vacuum condition was inferred. The tables' temperatures, reference temperatures, and ranges are represented at their printed row or series scope.

Schema-gap refusals are documented in the extract with code symbols. Tables 3 and 4 lack an exact reaction-rate or combined equilibrium/rate parameter token; `simulator/battery/enums.py::Quantity.MASS_LOSS_RATE` is not a chemical reaction-rate token, and `simulator/battery/enums.py::Quantity.LOG10_K_REACTION` is log-only. The relative gas-pressure ratios and linear Kp values in Tables D.1 and D.2 have no exact quantity token; they are not valence, ion-intensity, or distribution ratios. Table E.1's coefficient vectors and Table E.2's bond-dissociation energies are not CP, S, or elemental formation enthalpy values. Relative-to-PO formation values are not assigned an elemental-frame `delta_fH` token. Typed-refusal IDs and quantity-unknown states are pinned in the focused test.

## Isolated migration and verification

The isolated real-migrator run after correcting the 18 entropy unit tokens produced 86 observations and 20 experiment records, with 0 hard issues and 15 unavailable values: 14 quantity-unknown schema-gap records and one reaction log-K row whose printed unit does not establish `log10 K`. The 14 quantity-unknown IDs are exactly the rows listed by the extract's schema-gap audit. The prior 33-unavailable count included all 18 entropy rows failing on their printed unit spelling. The focused test pins PO S(2000 K) at printed `69.0 cal/(deg mol)`, migrated to `288.6960 J/(mol K)`, and checks all 18 entropy observations are available numeric points. No species-bearing observation has an unknown species phase; the reaction log-K row has no species identity. All migrated rows expose a `point_conditions.temperature_K` entry, with mixed-table temperatures retained on their printed rows rather than promoted to a single condition.

| Observation | Species phase | Composition | Total pressure | fO2 | Temperature |
| --- | --- | --- | --- | --- | --- |
| Table 2 PO measured row, PDF page 22 | PO gas | unknown | unknown | unknown | 2100 K |
| Table D.2 O2 calculated row 5, PDF page 167 | O2 gas | unknown | unknown | unknown | 2450 K |
| Table E.4 PO ΔHf,2000, PDF page 201 | PO gas | not applicable | not applicable | not applicable | 2000 K |

The focused extract test passed 2 tests. Its render mutation changes the Table D.2 O2 row 5 value from `6.74(-3)` and is rejected. The YAML flow-scalar guard passed 17 tests. All 928 collected `tests/test_literature_extracts.py` cases passed across bounded selections: 159 initial cases, 96 remaining non-fidelity cases, 672 per-extract fidelity cases, and the isolated pilot-count case. The first unfiltered attempt exceeded its cap after 159 passes; the remaining cases were completed in the split runs. No generated battery store was committed. Expected migrated store delta is 86 observations and 20 experiment records.

### Migration blocker: Table 2 upper limits

Eight measured Table 2 cells are printed as upper limits: two P2 values, three PO2 values, and three HPO values. They remain verbatim in the full table record, with the detection-limit footnote. `simulator/battery/enums.py::Quantity.P_PARTIAL` and `simulator/battery/records.py::ValueKind.BOUND` exist, but `simulator/battery/migrate.py::_selection_from_named_field` does not convert a bound-shaped pressure field. An isolated resolver check returned an unavailable value for structured bounds in `p_atm`, `pressure_atm`, and `partial_pressure_pa`. Mapping these cells as partial-pressure observations therefore requires a migrator change, which this extraction task forbids. The committed extract preserves the printed cells, but the upper limits are not migrated as bound observations. This lane is blocked on that migrator change and a follow-up extraction/test update.

## Figure-only values

No values were digitized from Figure 6 or Figures D.3–D.9. These remain figure-read candidates only; the plotted traces and error bars are not table values.

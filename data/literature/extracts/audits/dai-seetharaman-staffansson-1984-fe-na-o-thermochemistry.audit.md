# Extraction audit: Dai, Seetharaman & Staffansson (1984)

Source: “A thermodynamic study of the system Fe-Na-O,” *Scandinavian Journal of Metallurgy* 13(1), 32–38. The scanned PDF has no text layer. Transcription used MinerU table HTML/markdown and an independent Tesseract OCR, then rendered-page checks for every populated table cell and flagged disagreement. The PDF renders were used only for these checks.

## Printed-row reconciliation

| Printed table | PDF page | Printed rows | Stored observations | Confirmation and notes |
| --- | ---: | ---: | ---: | --- |
| I | 3 | 11 | 11 | All reaction-function cells checked against the render. MinerU classified the body as header rows; row assignment was checked against the rendered table. Tesseract misread the leading coefficient in the third Na₂O row; the render reads `-559460 + 264.14T`. |
| II | 4 | 12 substances; 18 populated numeric cells | 18 | Row and column assignments checked against the render. The 12 heat-evolution readings match the MinerU draft. The four reported averages and two mixing-enthalpy cells were checked in the render with their uncertainties. The four Na₂FeO₂ readings include `345600`; the printed average `307691` is retained as printed despite the discrepancy. The mixing enthalpies remain `-119106 ±3730` and `-147988 ±8210`; their reactions bind to (21) `Na₂O(s) + FeO(s) = Na₂FeO₂(s)` and (22) `2Na₂O(s) + FeO(s) = Na₄FeO₃(s)`. |
| III | 5 | 3 | 3 | All three equation cells checked against the render. Row 1’s table form `-86000 - 61.89T` differs from Eq. (16), `-86002 - 61.89T`; both are retained. Row 3’s table form `-147988 + 164.80T` differs from Eq. (34), `-147998 + 164.8T`; both are retained. |
| IV | 5 | 3 | 3 | All coefficient functions and minus signs checked against the render. Element-formation functions use the exact `delta_fG` token; they remain unavailable because the paper prints functions, not evaluated point values. Table IV row 1 is unchanged as printed (`-1323130 + 321.90T`); its stored parents sum to `-1313210 + 321.90T`, a 9920 J/mol constant-term difference recorded on the row. |
| V | 5 | 9 | 9 | All reaction equations and coefficient functions checked against the render. |
| VI | 6 | 11 | 11 | All combined log-pressure relations checked against the render; these are LaTeX cells in MinerU. |
| VII | 6 | 5 phase rows; 12 populated log-pressure cells | 12 | All populated cells checked against the render. MinerU reads the first O₂ value at 873 K as `-25.27`; Tesseract reads `-25.21`; the render confirms `-25.27`. The 1173 K columns are populated only for phase row 1. |
| **Total** |  |  | **67** | The table row/cell counts reconcile to the source. |

The cell-by-cell audit, including page, printed table, row, column, stored field, machine reads and confirmation method, is in `dai-seetharaman-staffansson-1984-fe-na-o-thermochemistry.cells.csv`. The table mapping and flagged render cells are in the sibling `.mapping.yaml`. The table-reader comparison is in the sibling `.compare.csv`: 12 matched, 18 mismatches, and no no-match, presence-only, needs-mapping, locator-error or unverifiable entries. The 18 compare mismatches are the six combined average/mixing cells in Table II and twelve Table VII cells; their printed values and row/column placement were resolved against the renders. The comparison output is not treated as the final transcription authority.

## Thermodynamic lineage review

The Table V reaction functions retain their printed A+BT coefficients. Their stored parents now close on the component functions the authors use, with signs and factors stated in each row's derivation note. Row 1 is Table III row 1 + Table I rows 1 and 4: `(-86000 - 61.89T) + (-810200 + 247.93T) + (-616240 + 305.27T) = -1512440 + 491.31T`. Row 2 is Table IV row 2 + Table I row 4 − row 6: `(-805316 + 311.84T) + (-616240 + 305.27T) - (-417010 + 135.86T) = -1004546 + 481.25T`. Row 3 is Table IV row 3 + 2 × (Table I row 4 − row 6): `(-1251208 + 498.76T) + 2[(-616240 + 305.27T) - (-417010 + 135.86T)] = -1649668 + 837.58T`.

Row 4 uses Table III equation (16), row 2, and Table I rows 1 and 2: `(-86002 - 61.89T) - (-119106 + 113.74T) + (-810200 + 247.93T) - (-269200 + 62.24T) = -507896 + 10.06T`. Row 5 uses twice Table IV row 2, subtracts Table III row 1 and Table I rows 1 and 6, then adds Table I row 4 − row 6: `2(-805316 + 311.84T) - [(-86002 - 61.89T) + (-810200 + 247.93T) + (-417010 + 135.86T)] + [(-616240 + 305.27T) - (-417010 + 135.86T)] = -496650 + 471.19T`.

Row 7 is Table IV row 3 − row 2 + (Table I row 4 − row 6): `(-1251208 + 498.76T) - (-805316 + 311.84T) + [(-616240 + 305.27T) - (-417010 + 135.86T)] = -645122 + 356.33T`. Row 8 is twice Table IV row 3 minus Table III row 1 and Table I rows 1 and 6, plus three times (Table I row 4 − row 6): `2(-1251208 + 498.76T) - [(-86000 - 61.89T) + (-810200 + 247.93T) + (-417010 + 135.86T)] + 3[(-616240 + 305.27T) - (-417010 + 135.86T)] = -1786896 + 1183.85T`. Row 9 is Table IV rows 3 + 2 minus Table III row 1 and Table I rows 1 and 6, plus twice (Table I row 4 − row 6): `(-1251208 + 498.76T) + (-805316 + 311.84T) - [(-86000 - 61.89T) + (-810200 + 247.93T) + (-417010 + 135.86T)] + 2[(-616240 + 305.27T) - (-417010 + 135.86T)] = -1141774 + 827.52T`.

Table VI row 10 retains its printed `log(P_O2) = -19385/T - 3.07` and phase assemblage. The source gives no reaction input for this relation, so its previously assigned Fe/FeO parent is removed, no derivation parent is invented, and it is retained as `quoted_attributed` with the missing input noted on the row.

## Conditions, phases and evidence

- Table II states 40 °C for the calorimetry. The extract uses `T_C` and located `point_conditions`; the real migrator maps this to 313.15 K. No numeric oxygen pressure, total pressure or gas ratio is printed for these runs.
- Table I prints the 600–1300 K range. The range is retained as printed; no midpoint is assigned as a point temperature.
- Table III row 2’s 1075 K melting condition is located to Eq. (30), PDF page 4. Row 3’s 904 K three-phase condition is located to Eq. (33), PDF page 5. Both temperatures reach the migrated identity. Row 1 retains the fitted EMF range 923–1023 K without selecting a single temperature.
- Table VII prints 873 K and 1173 K; each populated cell retains its own printed temperature and phase assemblage. The tabulated log pressures are outputs, not an experimental total-pressure or fO₂ condition.
- The EMF cell description states that commercial-grade air was circulated to maintain constant oxygen partial pressure. This qualitative atmosphere is retained with its locator; no numeric fO₂, gas ratio or total pressure is inferred.
- Every observation has a located phase assemblage and fO₂/pressure unknowns where the source supplies no numeric condition. The migrator leaves unprinted pressure axes absent. There are no quenched-glass measurements in this paper.
- Table II readings are `measured_direct`; the reported averages and mixing enthalpies are `measured_reduced` from the located calorimetry cells. Table III row 1 is `measured_reduced` from the located fitted EMF relation. Table III rows 2–3, Table IV rows 2–3, Table V rows 2–9, and Table VI rows 1–9 and 11 are `model_derived`. Table VI row 10 is `quoted_attributed` because its relation is reported without derivation inputs. Table VII values are `model_derived`. Table I values are `quoted_attributed` to reference [15]. Table IV row 1 and Table V row 1 retain their measured-reduction lineage.
- The Table II uncertainty widths are retained as printed; the source does not state their confidence level.

The fitted EMF relation `E (mV) = 461.98 (±0.11) + 0.1264 (±0.00011)T` and its 923–1023 K range are stored as measured-reduced context with the cell notation, atmosphere, uncertainties, and locators. Individual Figure 4 readings are a figure-read candidate and were not digitized. Phase-diagram values in Figures 1, 2, 5 and 6 are also figure-read candidates and were not extracted.

## Schema refusals

Rows use descriptive non-token labels where no exact quantity/form token exists, so `TYPE_QUANTITY` cannot infer a near match. The focused migration test asserts the exact quantity-unknown observation IDs and verifies that every phase maps.

- Table I rows with exact `delta_fG` quantity retain that quantity token; coefficient-only functions have no evaluated point value. Other reaction and transition Gibbs functions have no exact quantity token (`simulator/battery/enums.py::Quantity`).
- Table II acid-solution heat evolution and reported averages are not formation enthalpy or partial-molar enthalpy; enthalpy-of-mixing cells also have no exact match (`simulator/battery/enums.py::Quantity.DELTA_FH`, `simulator/battery/enums.py::Quantity.PARTIAL_MOLAR_ENTHALPY`).
- Table III formation Gibbs energies from component oxides and Table V reaction Gibbs functions are not element-based formation Gibbs energy; Table V is not a log equilibrium constant (`simulator/battery/enums.py::Quantity.DELTA_FG`, `simulator/battery/enums.py::Quantity.LOG10_K_REACTION`).
- Table VI combined logarithmic pressure relations are not single-species Pa values. Table VII’s values are printed as log(P), with no pressure reference stated. The closest token, `p_partial`, represents Pa, and `TYPE_QUANTITY` would infer it from `partial_pressure`; neither is an exact match for these printed log forms (`simulator/battery/enums.py::Quantity.P_PARTIAL`, `simulator/battery/migrate.py::TYPE_QUANTITY`). No exponentiation or pressure-reference assumption was added.
- EMF has no quantity token or electrochemical observation type. `MethodToken.EMF_CELL` names the apparatus, not the quantity; the fitted relation remains typed-refused context (`tools/validate_literature_extracts.py::OBSERVATION_TYPES`, `simulator/battery/enums.py::Quantity`, `simulator/battery/enums.py::MethodToken.EMF_CELL`).

## Isolated migration

The real migrator was run against this extract only: **67 observations, 0 hard issues, 67 unavailable values, 30 ordinary issues, 61 quantity-unknown observations, and 0 phase-unknown observations**. The unavailable values preserve the printed cells while the exact quantity/form gaps above remain unresolved by the current schema.

Migrated identity axes (`composition`, `total_pressure_Pa`, `fO2_Pa`, `temperature_K`) were printed for representative rows:

| Observation | Migrated axes |
| --- | --- |
| Table II FeO heat evolution | composition not mapped; total pressure not mapped; fO₂ not mapped; 313.15 K |
| Table III Na₄FeO₃ formation from oxides | composition unknown: basis and denominator are not printed; total pressure not mapped; fO₂ not mapped; 904 K |
| Table VII O₂ log pressure, phase row 1 | composition not mapped; total pressure not mapped; fO₂ not mapped; 873 K |

For those rows, the source does not print a sample mixture composition, numeric oxygen pressure, or experimental total pressure. The Table III Na₄FeO₃ calculation is an exception: page 5 prints “0.85 Na-fraction” for the melt at 904 K. The verbatim value, label, and Eq. (33) locator are retained as a method input; the migrated identity composition and `point_conditions.composition` remain unknown because the source does not state the basis or denominator. This is an open source-interpretation question for a later reader, not a schema gap. The existing composition parser accepts `AmountBasis` values and component maps (`simulator/battery/enums.py::AmountBasis`, `simulator/battery/migrate.py::_composition_from_plain`).

## Verification status

- Focused extract test: **1 passed**; the real migrator reports 67 observations, 0 hard issues, and 67 unavailable values. The test pins the Table V parent lists and printed equations, both Table II reaction bindings and unchanged enthalpy/uncertainty values, the Table IV 9920 J/mol note, and the unresolved Table VI row 10 input.
- Mutation check: changing the pinned Table II `345600` cell to `345601` made the focused test fail at the independent render pin; the source value was restored and the test passed again.
- `tests/test_yaml_flow_scalar_commas.py`: 17 passed.
- `tests/test_literature_extracts.py`: the source-specific fidelity selector passed (**1 passed, 892 deselected**). Earlier bounded local `-k` batches passed for all 637 fidelity samples and the other collected cases, except `test_repo_extracts_validate_green` and `test_pilot_extract_count_exact_and_merge_smoke`. Each of those repository-wide checks was run alone and stopped at 290 seconds under the 300-second per-call cap; both remain **UNVERIFIED-local**. The full invocation collected 893 tests before it was stopped at the same cap.
- Expected store delta is 67 unavailable observations. No generated battery store is included.

## Open composition interpretation

The paper does not state whether “0.85 Na-fraction” is a cation fraction, an atom fraction, or a mole fraction of an oxide component. The identity remains explicitly unknown with the page 5 Eq. (33) locator, while the printed value and label remain on the row as a method input. A later reader may resolve the basis from the cited phase-diagram source [7].

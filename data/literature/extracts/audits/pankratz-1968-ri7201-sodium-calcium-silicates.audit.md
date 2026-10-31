# Pankratz (1968), USBM RI 7201: extraction audit

Source: `pankratz-1968-ri7201-sodium-calcium-silicates`. The machine-readable MinerU table blocks and markdown were transcribed first, then checked against `pdftotext-layout.txt`. Renders were used where the table QA or layout called for them. The per-cell record is in `pankratz-1968-ri7201-sodium-calcium-silicates.cell-audit.csv`; the table mapping and printed locators are in the sibling `.mapping.yaml`.

## Printed table reconciliation

| Printed item | Printed body rows | Numeric cells | Audit result |
| --- | ---: | ---: | --- |
| Table 1, p. 2 / PDF p. 9 | 2 component rows | 16 | CaO and SiO2, four compounds, measured and theoretical parenthetical values. MinerU and pdftotext agree on the digits; the render confirmed row and compound-column assignment. |
| Table 2, p. 3 / PDF p. 10 | 71 measured points | 142 | Temperature and measured heat-content cells for all four compounds. The table QA agreement was 94.9%; every numeric cell and column assignment was checked against the full-page render. |
| Table 3, p. 6 / PDF p. 13 | 47 smoothed points | 141 | Temperature, smoothed heat content, and relative entropy increment. The table QA agreement was 98.6%; all cells and row/column assignments were checked against the upright render, including the extrapolated entries. |
| Six fitted equations, p. 5 / PDF p. 12 | 6 equations | 6 records | Render-checked equation text, signs, coefficients, exponents, ranges, average deviations, and transition enthalpies. |

The cell audit contains 373 records: the 299 numeric table cells above and 74 numeric equation or prose values. Table 2 has 71 rows, not a row for each column block. Table 3 has 47 rows; each row contributes exactly three numeric cells. Compound assignments were checked against the printed row headings. The three Table 2 measurements above all printed fit ranges (Na2CaSiO4 at 1309.0 K, Na2Ca2Si2O7 at 1300.3 K, and Na2Ca2Si3O9 at 1504.4 K) remain measured observations and are excluded from fit parents.

Every distinct table locator was checked against its caption on the render. Table 1 actual and theoretical values remain separate. Table 3 entropy values are `S_T-S_298.15`, never absolute entropy. The α/β marks are retained only at the 830 K and 750 K Table 3 points where the paper prints them.

## Machine compare

The committed `pankratz-1968-ri7201-sodium-calcium-silicates.mineru-compare.csv` is the output of `tools/mineru_table_draft.py compare`. It reports 2 no-match, 0 matched, mismatch, presence-only, needs-mapping, locator-error, or unverifiable rows. The two candidates are the 830 K and 750 K transition temperatures quoted in equation-page prose, not cells in the MinerU table blocks. The comparator's `tools/mineru_table_draft.py::_numeric_points` reads scalar and `values.points` values, while this extract stores table cells in `values.series`; therefore the compare output does not independently compare Tables 1–3. Those tables are covered by the full cell audit and render reconciliation above, not by a claimed compare pass.

## Evidence, conditions, and schema gaps

- Table 2 heat contents are the authors' drop-calorimetry measurements (`measured_direct`). Table 3 heat contents and relative entropy increments are calculated/smoothed from the printed fits (`model_derived`). Each fit references only its stored Table 2 input subset within the printed range. The three outside-range Table 2 endpoints are not parents.
- Table 1 measured analyses are `measured_direct`; the parenthesized theoretical values are attributed to the paper because their calculation inputs are not stored as observations. No unreported Na2O number is calculated from the printed percentages.
- The capsules were platinum-rhodium and sealed under 1 atmosphere helium, as stated for setup on PDF p. 10. This is retained in a located note, not promoted to measurement total pressure. No fO2 is printed. No pressure, fO2, or composition was added to heat-content rows. Every numeric heat-content row migrates its printed temperature and locator into `point_conditions.temperature_K`.
- `composition_series` is accepted by `tools/validate_literature_extracts.py::OBSERVATION_TYPES`, but no exact quantity in `simulator/battery/migrate.py::QUANTITY_ALIASES` describes this non-equilibrium sample assay. `TYPE_QUANTITY` inference is suppressed with explicit descriptive quantity labels and verified as unknown by the focused test. `Quantity.RESIDUE_COMPONENT_COMPOSITION` means residue composition; `Quantity.EQUILIBRIUM_PHASE_COMPOSITION` means equilibrium phase composition. Existing melt/slag composition examples such as `saini-2023-sulfur-vanadium-borosilicate-structure.yaml` and `avarmaa-2022-cr-don-smelting.yaml` do not describe these prepared crystalline samples. This is a real quantity gap, not a reason to reclassify the assays.
- `Quantity.S` is absolute entropy; there is no exact relative-entropy-increment token in `QUANTITY_ALIASES` or `TYPE_QUANTITY`. The 47 printed `S_T-S_298.15` values therefore remain typed refusals with the descriptive label `relative_entropy_increment_S_T_minus_S_298_15`.
- `Quantity.ENTHALPY_OF_FUSION` does not represent the two solid-solid transition heats. The 160 cal/mol and 1,550 cal/mol measurements are retained as typed refusals, with the measured transition temperature attached. The reported 830 K and 750 K transition temperatures themselves use the exact `transition_temperature` token.
- The quoted 733–753 K interval, the irreversible structure-change lower bound above 1309 K, and melting slightly above 1504 K are not exact scalar temperatures and remain typed refusals. The paper gives no numeric fusion enthalpy for the incongruently melting samples and says no fusion information could be determined for Na4CaSi3O9.
- Phase token `cr` is recognized by `simulator/battery/migrate.py::PHASE_MAP`; its role is supported by `simulator/battery/enums.py::PHASE_BASIS_TOKENS`. No phase-unknown observation remains. The source identifies sample patterns against ASTM or Kroger and Blomer references, with the unidentified low-intensity lines recorded in the extract notes.

The exact schema symbols reviewed were `tools/validate_literature_extracts.py::OBSERVATION_TYPES`, `simulator/battery/migrate.py::QUANTITY_ALIASES`, `simulator/battery/migrate.py::TYPE_QUANTITY`, `simulator/battery/migrate.py::PHASE_MAP`, `simulator/battery/enums.py::PHASE_BASIS_TOKENS`, and the relevant members of `simulator/battery/enums.py::Quantity`. No schema or migrator change was needed.

## Isolated migration

The focused test runs the real migrator on this extract: 186 observations, 0 hard issues, and 66 unavailable values. The 66 comprise 60 typed-refusal rows (8 sample assays, 47 relative entropy increments, 2 transition enthalpies, and 3 range/bound/qualitative transition rows) plus the 6 equation-form fit records, which carry a printed fit but no point value. There are exactly 60 quantity-unknown observations and no phase-unknown observations.

| Migrated row | Quantity | Phase | Composition | Total pressure | fO2 | Temperature |
| --- | --- | --- | --- | --- | --- | --- |
| Na2CaSiO4, Table 2, 395.1 K | H_minus_H298 | cr | not applicable | not applicable | not applicable | 395.1 K, located on PDF p. 10 / Table 2 |
| Na2CaSiO4, Table 3, 400 K | H_minus_H298 | cr | not applicable | not applicable | not applicable | 400 K, located on PDF p. 13 / Table 3 |
| Na2Ca2Si3O9, Table 3, 1500 K | H_minus_H298 | cr | not applicable | not applicable | not applicable | 1500 K, located on PDF p. 13 / Table 3 |

The focused test pins the three migrated values to the page renders and checks the reported printed row counts. Its mutation check changes the first measured heat-content value by one cal/mol and confirms the corresponding fidelity sample fails.

Verification completed locally with the repository `.venv` and the required environment. The focused extract test passed (1); `tests/test_yaml_flow_scalar_commas.py` passed (17); `tests/test_literature_extracts.py` passed in bounded `-k` groups: 617 fidelity samples, 253 remaining cases, the standalone repo-wide validator, and the pilot extract merge smoke test (872 total cases, all passed).

## Expected store delta

No battery store was regenerated or committed. The isolated extract migration yields 186 observations, including the 66 unavailable values described above. The controller should expect that observation delta when it regenerates the store.

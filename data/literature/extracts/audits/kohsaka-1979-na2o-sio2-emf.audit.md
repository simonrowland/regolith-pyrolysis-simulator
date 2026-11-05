# Kohsaka et al. (1979) extraction audit

## Coverage and source

The extract records the printed concentration-cell EMF and temperature readings from Table 1, the relative partial-molar values from Table 2, and the printed cell/reference definition. The paper's composition formula is `((1−x)Na2O + xSiO2)`, so `x` is the SiO2 mole fraction. All observation rows carry their own printed composition and temperature.

The printed counts are:

- Table 1: 15 body rows, 9 composition groups, and 65 EMF/temperature pairs (65 EMF cells and 65 temperature cells).
- Table 2: 10 composition rows and 30 partial-molar cells at 1450 K. Twenty-seven cells carry printed ± widths; the three zeroes at the x=60.0 reference melt have no width.
- Table 1 has 50 trailing blank temperature/EMF slots in continuation rows. No dashes are printed in those slots; they are recorded as typed absence in `kohsaka-1979-na2o-sio2-emf.empty-cells.csv`.

The full numeric audit has 206 cell records in `kohsaka-1979-na2o-sio2-emf.audit-cells.csv`: 130 Table 1 T/E cells, 9 Table 1 composition labels, 10 Table 2 composition cells, 30 Table 2 central values, and 27 Table 2 uncertainty widths. Every Table 1 numeric cell was independently read by MinerU and pdftotext; the row/column pairing and continued composition groups were checked against the page render. The Table 2 layout and every numeric value and uncertainty were checked against the 600 dpi render because MinerU marked the first six data rows as headers and its cell text did not parse the printed `±` forms.

## Render and table-tool reconciliation

The seat's MinerU table draft and QA were used. Table 1 had 15 body rows, 13 columns, and 145 numeric cells including continuation/group cells; digit agreement with pdftotext was 1.000 with no flagged cells. Its row/column layout was checked against the render.

Table 2 had 11 draft rows and 4 columns. QA reported 0.840 numeric agreement and three OCR disagreements. The 600 dpi render resolved the disagreements at x=55.0, ΔH=46.9 ± 5.9; x=50.0, ΔH=38.1 ± 8.8; and x=45.0, ΔS=−8.8 ± 2.9. The render also confirmed the first six data rows, which MinerU had labelled as header rows. The table mapping records render verification for all 30 value cells. The draft's comparison-only central values and row classifications were corrected from the render in the scratch table draft; the original OCR cell text remains in that draft. The committed compare report then matched all 95 observation values (65 Table 1 EMFs and 30 Table 2 central values), with no mismatches, missing candidates, or locator errors.

No OCR redo was needed: the remaining table cells were resolved from the page render.

## Conditions, cell, and standard state

Equation (1) is retained as printed: `E = −(RT/2F) ln{a(Na2O)/a°(Na2O)}`. The paper defines `a(Na2O)` and `a°(Na2O)` as the activities in the sample melt and reference melt `{0.4Na2O·0.6SiO2}`, respectively. That reference melt is the printed standard state for the reported relative quantities; the paper does not specify pure-liquid Na2O as its standard state.

The cell is retained as printed, `O2(Pt)|0.4Na2O·0.6SiO2|Na2O+SiO2|O2(Pt)`, with `O2(Pt)` at both ends. The text describes two platinum crucibles, their small side compartments and 1 mm melt passage, a semicircular alumina liquid junction, and platinum electrode wires. The temperature was held within ±2 K; equilibrium was defined by EMF constancy within ±0.5 mV for 1–2 h. Table 1 does not print per-reading uncertainties. Table 2 states that figures following ± are widths of the 95% confidence limit; those widths and `ci95` are retained.

Every observation has a row-level temperature and composition. The source gives no measurement total pressure, oxygen fugacity, oxygen partial pressure, or gas mixture. `O2(Pt)` is cell notation and is not used as a pressure or fugacity. The occasional vacuum sentence applies only to pretreatment; no pressure is assigned to the measurements. The 1500 K/24 h heat treatment is retained as preparation context, not as a measurement condition.

## Evidence, lineage, and unavailable schema forms

Table 1 EMFs are the authors' direct measurements (`measured_direct`) and are retained as typed refusals. `simulator/battery/enums.py::Quantity` has no EMF or voltage quantity, and `simulator/battery/migrate.py::QUANTITY_ALIASES` has no exact alias. The closed host-type set is `tools/validate_literature_extracts.py::OBSERVATION_TYPES`; it has no EMF-specific observation type. The rows therefore keep the descriptive non-token `electromotive_force_as_published`, which prevents `simulator/battery/migrate.py::TYPE_QUANTITY` from inferring `activity_coefficient` from the host type. The printed measurements are not converted to an activity ratio.

The relative partial-molar Gibbs energy and entropy rows are authors' reductions (`measured_reduced`) with the printed numerical values and uncertainties preserved, but typed refused because no exact quantity exists. `simulator/battery/enums.py::Quantity.DELTA_FG` is formation Gibbs energy and `simulator/battery/enums.py::Quantity.S` is standard entropy; neither names the paper's relative partial-molar quantity. Their descriptive non-token quantity strings prevent inference through `simulator/battery/migrate.py::TYPE_QUANTITY`.

The ten ΔH rows keep the exact `partial_molar_enthalpy` quantity and their printed values, units, and uncertainty widths. They are typed refused because `simulator/battery/migrate.py::QUANTITY_SOURCE_FIELDS` has no numeric field selector for `simulator/battery/enums.py::Quantity.PARTIAL_MOLAR_ENTHALPY`; no migrator change is in scope. Their same-composition Table 1 EMF rows are retained in `derived_from` and `derivation.inputs`, preserving the measured reduction without changing evidence class. The x=60.0 zeroes are reference values by definition; Table 1 has no separate x=60.0 EMF series, so no parent measurement rows are fabricated.

No numeric fitted coefficients are printed. The paper says it applied a least-mean-square linear function to each Table 1 EMF/temperature set and derived ΔG, ΔS, and ΔH from those temperature equations. The relation is recorded; no coefficients or new values are calculated. The prose statement that an 80 kJ mol⁻¹ ΔG change at 1450 K corresponds to a 10³ activity ratio is retained as context, not treated as a composition-resolved activity point. Figure 1 EMF traces and Figure 2 relative partial-molar curves are figure-read candidates and remain undigitized.

## Isolated migration

The real migrator produced 95 observations with 0 hard issues and 95 unavailable values. The 85 quantity-unknown rows are the 65 typed-refused EMFs plus 20 typed-refused relative partial-molar Gibbs/entropy rows. The remaining 10 rows retain the exact `partial_molar_enthalpy` identity quantity but their values are unavailable under the missing `QUANTITY_SOURCE_FIELDS` mapping described above. All 95 rows migrate with known liquid phase, temperature, and composition; the focused test pins the identities and conditions for both printed tables. Pressure and fO2 remain absent/unknown in the source and are not inferred into identity fields.

The focused test passed (2 tests), checking four render-pinned values across Table 1 and Table 2 and their actual migrated temperature/composition fields. Its mutation check changes a pinned Table 1 cell and confirms the fidelity check rejects the mutation. `tests/test_yaml_flow_scalar_commas.py` passed (17 tests). The complete `tests/test_literature_extracts.py` suite passed across bounded partitions (880 tests). The required j01 store census test passed (1 test).

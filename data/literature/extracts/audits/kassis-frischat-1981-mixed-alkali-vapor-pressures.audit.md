# Kassis and Frischat (1981), mixed-alkali vapor pressures

## Scope and transcription

Table 3 on PDF page 7 (printed page 95) has two data rows and four temperature
columns. The first row is the Rb partial-pressure ratio for melts with
\(\gamma_{Rb}=0.3\) and 0.5; the four printed values are `0,44`, `0,46`,
`0,48`, and `0,56` at 1150, 1200, 1250, and 1300 °C. The second row contains
Rb microprobe intensity ratios, not vapor pressures, and is outside this
extract's pressure scope. The full printed row count is four pressure-ratio
cells; all four are represented.

The ratio's numerator is melt 11 and its denominator melt 3. Their exact
printed compositions are retained per observation from Table 1 on PDF page 2
(printed page 90), rows 4 and 5. Table 1 has six composition rows overall;
only these two are used by the Table 3 ratio.

Table 3 reports ratios of the authors' calculated partial pressures. Their
individual Na and Rb partial-pressure series appear only in Figures 8 and 9.
The wave rule excludes figure-read values, so those series are recorded as
figure-read candidates and are not transcribed. Table 2 reports free-evaporation
rates and Table 4 reports thermodynamic data; neither is a partial-pressure
measurement table. No absolute Na partial-pressure point is available in a
printed table in this source.

## Cell audit and render checks

`kassis-frischat-1981-mixed-alkali-vapor-pressures.cell-audit.csv` records every
numeric source cell stored by the extract, its printed page/table/row/column,
and the confirmation path. All comma-decimal Table 1 and Table 3 cells were
checked against page renders. The Table 3 row/column assignment was checked as
a whole against PDF page 7. Table 1 row assignments for melts 11 and 3 were
checked against PDF page 2. The temperature-setting `± 5 K` and chamber
operating vacuum `6,6 · 10^-4 Pa` were also render-checked. No OCR redo was
needed. MinerU's invented `<details>` chart blocks were ignored.

The MinerU/pdftotext prescreen found full numeric agreement for Table 1, full
agreement for Table 2, 0.750 for Table 3, and 0.850 for Table 4. Table 3's four
stored comma-decimal values were individually settled from the render. Table
4's flagged thermodynamic values were rendered but are outside this pressure
extract. The compare command reported zero numeric points because its parser
does not treat the source's comma-decimal strings as numbers; its committed
CSV is header-only. This is not a cell mismatch result. The table-to-observation
crosswalk is in `kassis-frischat-1981-mixed-alkali-vapor-pressures.mapping.yaml`,
and the four pressure-ratio cells were independently checked against the page
render as recorded in the cell audit.

## Conditions and identity

The source states a chamber operating vacuum of `6,6 · 10^-4 Pa` and a
temperature setting accuracy of `± 5 K` (PDF page 2, section 1.2.2). The
numeric chamber pressure is carried in the linked experiment's pressure
environment. It is outside-cell background pressure, not the in-cell vapor
total pressure calculated by the paper. Each ratio row carries its printed
temperature. No in-cell total pressure is printed, and the ratio has no
quantity profile, so the chamber background is not copied to an identity axis.
The oxygen fugacity is unknown because no fO2 or gas mixture is reported. A
single composition cannot identify a ratio between two different melts, so the
identity composition is unknown; both complete compositions and their Table 1
locators are retained on each row. No room temperature, 1 bar, or assumed gas
condition is added.

The isolated migration reports 4 observations, 0 hard issues, and 4 unavailable
values. For the first three rows, identity axes are:

| Observation temperature | composition | total_pressure_Pa | fO2_Pa | temperature_K |
| --- | --- | --- | --- | ---: |
| 1150 °C | unknown: paired melts | not populated: ratio has no quantity profile | not populated | 1423.15 |
| 1200 °C | unknown: paired melts | not populated: ratio has no quantity profile | not populated | 1473.15 |
| 1250 °C | unknown: paired melts | not populated: ratio has no quantity profile | not populated | 1523.15 |

It preserves the printed chamber pressure on the shared Knudsen-effusion
experiment. The extract explicitly marks in-cell total_pressure_Pa and fO2_Pa
unknown; the migrator omits the unknown in-cell pressure from the point record,
and neither absent identity axis receives an invented value. Composition is
unknown because the row compares two compositions. The chamber-background exclusion follows
`simulator/battery/migrate.py::_knudsen_effusion_chamber_background_not_for_identity`;
the paper's in-cell vapor total pressure is calculated, not a stated condition.
Expected store delta: 4 unavailable Rb pressure-ratio records, no scored
numeric pressure values; the generated stores were not regenerated.

## Schema gap and evidence class

The exact printed datum is a ratio of two Rb partial pressures. There is no
exact closed token for that quantity: `simulator/battery/enums.py::Quantity.P_PARTIAL`
denotes a scalar pressure; `simulator/battery/enums.py::Quantity.DISTRIBUTION_RATIO`
is an interphase partition; `simulator/battery/enums.py::Quantity.ION_INTENSITY_RATIO`
and `simulator/battery/enums.py::Quantity.VALENCE_RATIO` describe other
quantities. `simulator/battery/migrate.py::QUANTITY_ALIASES` maps the
scalar `partial_pressure` token to `P_PARTIAL` and has no partial-pressure
ratio alias. `tools/validate_literature_extracts.py::OBSERVATION_TYPES` has
`partial_pressure`, but no partial-pressure-ratio observation type. Existing
ion-current-ratio extracts use the detector-signal quantity, not a pressure
ratio; for example, `bennour-1996-ternary-cs-na-silicate.yaml` stores
`ion_current_ratio`. No existing extract with a numeric partial-pressure-ratio
quantity was found. The rows therefore retain the approved `partial_pressure`
observation type with a descriptive non-token `values.quantity`, and are typed
refused as unavailable. The focused test asserts that these are exactly the four
quantity-unknown rows and that phase remains known as gas.

The authors reduce their measured evaporation data to the reported ratio using
the printed partial-pressure relations (Eqs. (19)–(20) and (23)–(26), PDF page 7,
printed page 95), so the rows are `measured_reduced`. The numeric partial-pressure
inputs appear only as curves in Figures 8a–b and 9a–b (PDF page 8, printed page
96) and are not stored as parent rows. Each observation records these raw inputs
as `not_printed` at that figure locator; the paper itself is not a derivation
input. The values are dimensionless as printed.
The table's Rb intensity-ratio row is not a partial pressure and is not included.

## Relation to the other Kassis and Frischat source

This extract is the Glastechnische Berichte mixed-alkali paper, repository DOI
10.34657/17394. It is distinct from
`kassis-frischat-1981-silicate-glass-vapour` (Journal of the American Ceramic
Society). Tabelle 4's end-member values at γ_Rb = 0 (ΔH_Ver 316 kJ mol⁻¹,
ΔS_Ver 102 J K⁻¹) and γ_Rb = 1 (ΔH_Ver 328 kJ mol⁻¹, ΔS_Ver 122 J K⁻¹), with
the footnote pressure `p = 1,013·10^5 Pa`, are already stored in the JACS
extract's context rows `kassis_1981_total_pressure_thermochemistry` and
`kassis_1981_total_pressure_thermochemistry_rb`. They are an end-member overlap
between the two papers; no rows from that source are duplicated here.

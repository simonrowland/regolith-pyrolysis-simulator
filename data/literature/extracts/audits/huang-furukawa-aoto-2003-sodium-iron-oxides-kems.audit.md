# Huang, Furukawa and Aoto (2003) extraction audit

## Machine reads and table census

The Table 1 transcription uses the MinerU HTML table in `mineru/p001-p006` and
`pdftotext-layout.txt`. The article is born digital. MinerU QA found one table on
absolute PDF page 5 (printed page 65), 25 body rows, five columns, 125 numeric
cells, exact numeric agreement of 1.000, no flagged cells, and four invented
`<details>` blocks that were ignored. The cell audit records both machine reads
for all 125 cells. All five columns and their row assignments were checked once
against the page 5 render; there was no cell-specific render trigger.

Table 1 contains 25 printed rows. Those rows carry 25 temperatures, 25 sodium
partial pressures, 25 reaction Gibbs changes, and 25 Kp values. Its 25 row
labels are also listed in the cell audit. The table locator is printed Table 1,
not the MinerU block index.

The table compare report matches all 75 stored numeric Table 1 value cells with
zero mismatches or locator errors. The compare helper accepts `pressure_Pa` but
not the migrator's explicit `partial_pressure_pa` source field, so the command
used a temporary Table 1-only comparison projection with that key renamed for
the comparison. Values were unchanged; the report paths point back to the
extract's actual `partial_pressure_pa` field. The extract keeps that explicit
field because `pressure_Pa` is mistaken by lab-condition discovery for an
experiment total-pressure condition.

## Printed observations and conditions

- Table 1 stores all 25 printed `PNa /Pa` values as Na partial pressure in Pa.
  The row temperatures are stored as printed in K. Each row has the printed
  sample composition source, 2:1 molar Na2O(s):FeO(s), with the source's
  Na4FeO3(s) identification. The article does not associate individual table
  rows with either specimen, so no row-to-specimen split is made.
- The printed Table 1 reaction Gibbs-change values (25) and linear Kp values
  (25) are retained verbatim with their row temperatures. They are typed
  refusals because neither printed form has an exact stored quantity token.
- The article reports a third-law Na(g) formation enthalpy of 107.6 ± 0.8
  kJ mol−1 at 298.15 K. The uncertainty width is retained as printed; its
  confidence level is not stated. The 107.3 kJ mol−1 comparison is attributed
  to NIST-JANAF [18]. The third-law source temperature-pressure points are not
  published and are not represented by the Table 1 measurements.
- The apparatus section gives an ultimate vacuum-system pressure of 10^-7 Pa
  and says experiments ran at a 10^-6 Pa vacuum-system level, including at high
  temperature. Those are chamber/system descriptions, not a printed in-cell
  total pressure. Total pressure therefore stays unknown on every Table 1 row.
- High-purity argon is stated only as a system purge gas. The measurement
  carrier gas and measurement atmosphere are not stated. The glove-box oxygen
  and water-vapor limits (<10 ppm each) apply to sample preparation, not the
  measurement; fO2 stays unknown.
- The sample was made from Na2O(s) 99.4% and FeO(s) 99.9% at a 2:1 molar ratio,
  sealed in stainless steel, and sintered at 873 K for 100 h. XRD found the
  prepared sample almost pure Na4FeO3(s). One specimen ran for 4 h over 590–717
  K and was reported after the run as Na4FeO3(s):Na3FeO3(s) = 9:1. A separate
  specimen evaporated for about 30 h and completely decomposed to Na3FeO3(s).
  No measurement rows are assigned to those specimens by the paper.
- The pressure calibration uses sodium stated as 99.7% and prints
  `P(Na) = K × {I × T}`: I is Na+ ion intensity, T is sample temperature, and
  K is calibrated against saturated sodium vapor pressure in [17]. No numeric
  K or Na+ intensity is printed, so no pressure is recalculated. The authors'
  Table 1 pressure values retain `method_class: author_derived`.
- The secondary electron multiplier factor is calibrated by the Faraday cup.
  The apparatus text also names a ThO2-Ir ionization filament and gives 9.8 eV
  electron-impact energy. Before installation, the prepared sample was kept in
  a glove box; installation used another glove box attached to the mass
  spectrometer.
- The paper prints standard symbols ΔrG°, ΔfG°, and ΔfH°, but no numeric
  reference pressure or fugacity basis. Kp has no printed unit or standard
  pressure. No 1 bar or 1 atm condition is supplied.
  The existing extract schema requires a `units` field for populated rows, so
  Kp uses `units: not_printed` to preserve that absence; it is not a physical
  unit or a dimensionless assumption.

## Species, equations and figure-only values

The source identifies Na(g) as the main vapor species over Na4FeO3(s) and says
it can be detected above about 550 K. Na2(g) is identified with ion intensity
three to four orders of magnitude below the monomer, but neither absolute
intensity nor a numeric ratio is printed. NaO(g) and Na2O(g) are not observed
at the detection limit. The small NaOH XRD peak is attributed to possible
moisture uptake during XRD analysis; it is not a reported vapor species. No K,
Fe(g), FeO(g), O2(g), activity, or numeric flux/vaporization-rate result is
reported.

Equation (3), PDF page 3, prints `lnPNa = 24.376 − 17749/T`, with PNa in Pa by
Table 1 and T in K over 590–717 K. It is retained as a model-derived fit to the
25 stored pressure rows. Equation (4), PDF page 4, prints
`ΔrG°(T) = (148522.2 ± 2753.8) − (108.29 ± 4.18) × T` over 590–717 K. Both
coefficient widths are retained and the confidence level is not stated. Its
inputs are the 25 Table 1 reaction Gibbs rows.

Equation (5), PDF page 4, relates the Na3FeO3 formation Gibbs energy to the
reaction Gibbs energy and the Na(g) and Na4FeO3 formation Gibbs energies.
Equation (6), PDF page 5, is attributed to Bhat and Borgstedt [7] and prints
`ΔfG°(Na4FeO3) = −1212202 + 351.10 × T`, with ±2100 J mol−1 and no stated
confidence level. The SGTE/Thermo-Calc [19] Na(g) function is retained as
`104949 − 95.53 × T`. Equation (7), PDF page 5, prints
`ΔfG°(Na3FeO3) = −1168629 + 338.34 × T` for 298 < T < 720 K, with an estimated
uncertainty of about ±7 kJ mol−1 from the source-data uncertainty. Equation
(7)'s stored inputs are the Table 1 reaction-Gibbs fit, the quoted SGTE Na(g)
function, and the quoted Bhat–Borgstedt Na4FeO3 function.

Figure 3 repeats the Table 1 sodium pressure series, so it adds no points.
Figure 4 compares the Na3FeO3 Gibbs function with MALT2 [20]; its plotted
values are a figure-read candidate and remain undigitised.

## Schema decisions and migration

Reaction ΔrG° is not mapped to formation ΔfG. The exact candidate check uses
`simulator/battery/enums.py::Quantity.DELTA_FG`,
`simulator/battery/migrate.py::QUANTITY_ALIASES`,
`simulator/battery/migrate.py::TYPE_QUANTITY`, and
`tools/validate_literature_extracts.py::OBSERVATION_TYPES`. Raw linear Kp is
not mapped to `log10_K_reaction`; the relevant closed token is
`simulator/battery/enums.py::Quantity.LOG10_K_REACTION`, with its aliases and
type inference in `simulator/battery/migrate.py::QUANTITY_ALIASES`,
`simulator/battery/migrate.py::TYPE_QUANTITY`, and
`tools/validate_literature_extracts.py::OBSERVATION_TYPES`. The source rows
remain typed refusals with their printed numeric cells intact.

The isolated real migration produced 77 observations: 27 point values (25
Table 1 sodium pressures and two Na enthalpy values) and 50 unavailable values
(25 reaction Gibbs cells and 25 Kp cells). It had zero hard issues. The only
quantity-unknown observations are the 50 schema-gap rows above; there are no
phase-unknown observations. All 25 pressure rows carry their own migrated
temperature, Na2O:FeO composition, gas phase, and p_partial quantity. Their
identity total pressure and fO2 remain unknown, and no total-pressure
point-condition is emitted. Rows 1, 13, and 25 were checked after migration.

The migrator reports conditional evidence/method queues for author-derived
values whose raw measurement inputs or named method are not fully printed,
alongside the expected typed-refusal queues. These are retained as source
limitations; evidence classes were not changed to suppress validity gates.
The Na(g) pressure result should be forwarded to the openimcc-species lane; no
potassium-vapor result was found.

Expected regenerated store delta: +77 observations (27 points, 50 unavailable).
No battery store was regenerated or committed.

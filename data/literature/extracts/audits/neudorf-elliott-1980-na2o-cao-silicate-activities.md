# Neudorf & Elliott (1980) extraction audit

## Acquisition and table census

Transcription used the MinerU table HTML/markdown and `pdftotext-layout.txt`.
`mineru_table_qa.py` reports digit agreement 1.000 for all four printed tables,
with no trapped-cell flags. Both machine reads agree on all 106 numeric cells;
the page 5 render was checked for row and column assignment in Tables I–III,
and the page 7 render was checked for Table IV's row/column assignment, signs,
and footnote. The cell-by-cell record is
`neudorf-elliott-1980-na2o-cao-silicate-activities.cell-audit.csv`; the table
crosswalk is in the matching `.mapping.yaml`.

| Printed table | PDF page | Body rows | Numeric cells | Render check |
|---|---:|---:|---:|---|
| I | 5 | 4 | 16 | Page 5 table layout and the thousands separators, signs, and ± cells checked. |
| II | 5 | 4 | 21 | Page 5 left/right subtable layout checked; three unprinted left cells in the last row remain absent. |
| III | 5 | 4 | 40 | Page 5 row/column layout and negative signs checked. |
| IV | 7 | 6 | 29 | Page 7 positive log values, row/column layout, and asterisk footnote checked. |

The four body-row counts match the printed tables. Table II's fourth row has no
left-hand T, C, or D cell; Table IV has an em dash in the first calculated
cell. Those four absent cells are not numeric values. The CSV records the
render check for each of the remaining 106 cells. The focused test independently
pins every cell, the row counts, five located render samples, and an interior
mutation. The five calculated-column cells are labeled `By Eq. [8]¹¹`, citing
Richardson [11]; the equation number remains [8].

## Transcription findings and admission

Table I prints 34.4 m/o Na2O in its 25 w/o row. The abstract gives the activity
at 25 wt pct, and the page 3 Results and Discussion maps 25/30/35/40 w/o to
24.4/29.4/34.3/39.3 m/o; Table III also prints 24.4 m/o. The page 5 render
confirms Table I's 34.4 digits. Both printed forms are preserved at their own
locators. The abstract activity observation is bound to the located 24.4 m/o
composition; Table I's first-row 34.4 remains unchanged in its own row.

The page 7 Table IV heading is `log a*Na2O`. The paper's asterisk footnote says
`* Relative to pure solid silica (tridymite).` This defines the reference state;
the paper contains no definition of `a*` as a reciprocal or negated activity.
The render and both machine reads show positive values 8.82–9.41 with no minus
sign. The abstract, prose, and Figures 3–7 show log activities around −8 to
−10. Under the controller's ruling, all 11 printed Table IV values are kept
verbatim as typed refusals; none is changed to a negative number. The
calculated-by-Eq.-[8] and interpolated columns are separate `author_derived`
observations. The first calculated dash is recorded as absent. Their refusal
records that the input activity readings appear only in Figures 3–7 and remain
undigitized, so no derivation parents are available. The 11 observations remain
unavailable and their migrated evidence stays unknown; migration reports zero
hard issues without inventing parent rows.

The abstract's approximately −10.2 and −8.3 log10 Na2O activities at 1050 °C
and 25/40 wt pct are recorded as printed. The migrator converts those log10
values to activity points. The prose's approximately 0.24 SiO2 activity at
1070 °C and NSiO2 = 0.65 is recorded as printed and classified
`measured_reduced`: the text identifies the Gibbs–Duhem calculation and the
Table II silica fit, so its parents are the two adjacent printed-composition
fit rows. The approximately 1.56
CaO–SiO2 binary silica activity is a quoted input attributed to Rein and
Chipman [25], extrapolated by these authors from 1550/1600 °C to 1070 °C; it is
kept as located context, not presented as a direct measurement by this study.

Tables I and II print the fit forms `log aNa2O = A/T + B`,
`log aNa2O = C × R + D`, and `log aSiO2 = A/T + B`, with their coefficients,
uncertainties, and printed conditions stored. These rows are typed refusals:
the closed quantity set has no exact scalar token for these log-activity fits.
`Quantity.ACTIVITY` is scalar, and
`Quantity.ACTIVITY_COEFFICIENT_TEMPERATURE_FIT` represents a different fitted
form (`log10 gamma = A + B/T`) in
`simulator/battery/migrate.py::_printed_scalar_selection`; the rows therefore
use explicit descriptive non-token quantity labels. No fit result is silently
converted to activity.

Table III's four complete rows are stored as quantity-unknown observations,
with every printed value and unit retained. Each row bundles partial Gibbs
energy, enthalpy, and entropy for both components with integral properties of
the melt, so no single quantity token represents that composite row. Its
`measured_reduced` lineage points to the same-composition Table I row, the
Table II left fit at 1323 K, and the corresponding Table II right row. These
are the printed fit inputs used for 1050 °C; Table I's first-row 34.4 m/o
composition conflict is retained rather than corrected. The prose explicitly
derives partial H/S from the Table I/II coefficients and uses Gibbs–Duhem to
calculate silica activity and integral properties. `Quantity.PARTIAL_MOLAR_ENTHALPY`
covers only the partial-enthalpy subset; `Quantity.DELTA_FG` and
`Quantity.DELTA_FH` are formation quantities, and `Quantity.S` is not a
partial-molar entropy token. The descriptive quantity label prevents
`gibbs_table` from inferring `delta_fG`. See
`simulator/battery/enums.py::Quantity.PARTIAL_MOLAR_ENTHALPY`,
`simulator/battery/enums.py::Quantity.DELTA_FG`,
`simulator/battery/enums.py::Quantity.DELTA_FH`,
`simulator/battery/enums.py::Quantity.S`,
`simulator/battery/migrate.py::QUANTITY_ALIASES`, and
`tools/validate_literature_extracts.py::OBSERVATION_TYPES`.

Figures 3–8 have figure-read candidates, but no plot values are stored. The
MinerU `<details>` chart blocks are synthetic digitizations and were ignored.
The paper describes measured reversible cell EMF but does not tabulate numeric
EMF readings. It reports no vapor-pressure values. Equation [4] is transcribed
in context. The reference-melt equations [5] and [6] are retained with their
printed Lin and Elliott [24] attribution and applicable conditions. Reference
[23] supports the preparation procedure; the printed GM equation conditions
are strictly `0.53 < NWO3 < 0.8` at 1200 K.

## Conditions and standard states

The paper prints `O2(g, 0.21 atm)` on both sides of the cell diagram. The
extract records this gas condition and its converted oxygen partial pressure
of 21278.25 Pa at the migrator boundary. The total experiment pressure is
unknown: the paper prints no total pressure for the cell. The `1 atm` in the
Na2O standard-state sentence is not used as an experiment pressure.

The printed Na2O standard state is pure liquid Na2O at 1 atm. The paper also
states pure solid tridymite for silica; Table II says “Tridymite is the
Standard State,” and Table IV's asterisk is relative to pure solid silica
(tridymite). These statements have their own page/table locators. Rows record
the printed temperatures and composition; no room temperature or other
unprinted condition is added.

## Isolated migration

The source migrates to 29 observations: 3 numeric point values and 26
unavailable values, with 2 contexts. The 15 quantity-unknown observation IDs
are exactly the four Table I fit rows, three left Table II fit rows, four right
Table II fit rows, and four composite Table III rows. No observation has an
unknown phase.
All three admitted point values carry temperature, composition, and the printed
oxygen partial-pressure condition into identity. Total pressure stays unknown.
Table IV keeps activity as its known quantity and liquid as its phase while
retaining unavailable values.

The 11 Table IV typed refusals use `author_derived` because they are the
authors' calculated and interpolated results. Inputs from the paper's plots are
excluded by the extraction rules, so no parents are added. Their migrated
evidence remains unknown pending page adjudication; this yields zero hard
issues while preserving all 11 unavailable values. The focused test asserts
zero hard issues.

`mineru_table_draft.py compare` can compare numeric observation scalars to
draft table cells; this extract's admitted points are prose values on PDF
pages 1 and 6, while every numeric table row is represented as a fit,
composite property row, or typed refusal. The committed compare output records
that no table-cell match is asserted for those prose values; full table
coverage is instead checked by the independent 106-cell audit and focused
test.

Expected derived-store delta, without regeneration: 29 observation records
(3 points and 26 unavailable) and 2 contexts for this new source. The store
files are not committed.

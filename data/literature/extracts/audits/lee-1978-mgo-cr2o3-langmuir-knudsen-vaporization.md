# Lee (1978) extraction audit

Source: Hong-Lim Lee, “High Temperature Vaporization of the High Melting Point
Oxides,” *Journal of the Korean Ceramic Society* 15(2), 72–78 (1978). The
source is a seven-page scan with no text layer. Machine reads: the MinerU
Markdown/table block and the independent Tesseract OCR at
`text/lee-1978-mgo-cr2o3-langmuir-knudsen-vaporization/tesseract-layout.txt`.
The MinerU table draft and emitted fragment were created with
`tools/mineru_table_draft.py`; the table QA screen found one table and 26
numeric-like cells, with scan-mode digit agreement unavailable because the
text layer is empty. All 21 numeric cells in the printed table were therefore
checked against the rendered page.

## Table 1 cell audit

Printed Table 1 is on PDF page 5 (journal page 76), captioned “The Values of
ΔS*−ΔS°, ΔH*−ΔH°, and α for High Melting Point Oxides.” It has seven oxide
rows and three numeric columns. Every value below was confirmed against the
PDF page 5 render (verification method **b**, render); row and column
assignments were checked against the rendered grid.

| Printed oxide row | ΔS*−ΔS° (column 2) | ΔH*−ΔH° (column 3) | α at Tm/T = 1.5 (column 4) |
| --- | --- | --- | --- |
| 2, ThO₂ | 2.01 (p5 r2 c2, b) | 7.01 (p5 r2 c3, b) | 6.0×10⁻¹ (p5 r2 c4, b) |
| 3, MgO | 7.02 (p5 r3 c2, b) | 21.57 (p5 r3 c3, b) | 1.7×10⁻¹ (p5 r3 c4, b) |
| 4, Y₂O₃ | 9.83 (p5 r4 c2, b) | 26.67 (p5 r4 c3, b) | 8.4×10⁻² (p5 r4 c4, b) |
| 5, Yb₂O₃ | 10.38 (p5 r5 c2, b) | 27.00 (p5 r5 c3, b) | 7.8×10⁻² (p5 r5 c4, b) |
| 6, MgCr₂O₄ | 10.63 (p5 r6 c2, b) | 29.17 (p5 r6 c3, b) | 7.5×10⁻² (p5 r6 c4, b) |
| 7, Cr₂O₃ | 17.75 (p5 r7 c2, b) | 46.82 (p5 r7 c3, b) | 9.7×10⁻³ (p5 r7 c4, b) |
| 8, Al₂O₃ | 34.23 (p5 r8 c2, b) | 79.53 (p5 r8 c3, b) | 2.0×10⁻⁴ (p5 r8 c4, b) |

Other numeric statements retained in context were checked against the page
renders as well:

| Locator | Printed numeric statement | Check |
| --- | --- | --- |
| Abstract, PDF p1 | 1300 to 1750 °C; `<10^-5 torr` | (b), p1 render; resolves Tesseract’s `<10^-8` exponent as `<10^-5` |
| Langmuir method, PDF p3 | furnace max 2500 °C; calibration error ±5 °C; pellet 12 mm × 2 mm | (b), p3 render |
| Knudsen method, PDF p3 | orifice/sample area about 1:1000; Clausing factor 0.62–0.65 | (b), p3 render |

The seven stored observations are the α column. The MinerU and Tesseract OCR
reads did not reliably preserve the decimal punctuation and powers of ten in
the scan; the rendered cells are authoritative for this table. The full
row/value comparison against the MinerU draft is in
`lee-1978-mgo-cr2o3-langmuir-knudsen-vaporization.compare.csv`.

## Other source checks and scope

- The abstract locator (PDF p1, journal p72) was checked against the render.
  It gives the authors’ MgO, Cr₂O₃, and MgCr₂O₄ vapor-pressure study range as
  1300 to 1750 °C and the vacuum bound as `<10^-5 torr`. Tesseract read the
  exponent as `-8`; the render confirms the MinerU read, `-5`. This is an
  experiment-wide bound, not a point total pressure. The three author-work
  α observations preserve its exact printed form and locator as
  `total_pressure_as_printed`; the identity’s exact numeric pressure remains
  unknown. The four recalculated literature rows do not inherit that bound.
- PDF p3 (journal p74) render checks confirmed the apparatus prose and Figure
  3/4 locators: a cylindrical tungsten-resistance furnace (maximum 2500 °C),
  W5%Re/W26%Re thermocouple, Pt/Pd/Au/Ag melting-point calibration (±5 °C),
  12 mm diameter × 2 mm sintered-pellet sample in a Pt10%Rh basket, rotary and
  diffusion pumps, and mass loss per specimen area per time for Langmuir
  measurements. The Knudsen method uses powder in Pt10%Rh or Pt30%Rh cells,
  an orifice/sample-surface area ratio of about 1:1000, and a Clausing factor
  Kc of 0.62–0.65. Cell/orifice diameters and per-run sample masses are not
  stated.
- Figure locators were checked on PDF pp3–5: Fig. 5 MgO vapor pressure (p3),
  Fig. 6 Cr₂O₃ vapor pressure (p4), Fig. 7 MgCr₂O₄ vapor pressure (p5), and
  Fig. 8 vaporization-coefficient curves (p5). Figure-read rates, partial
  pressures, and per-run temperatures are candidates for a later figure pass;
  no plotted values were digitized here. The table values are at normalized
  Tm/T = 1.5, not at a printed absolute temperature.
- MinerU `<details>` chart blocks contain generated plot tables and were
  ignored as instructed. The Table 1 fit equation is equation (15). Its
  ΔS*−ΔS° and ΔH*−ΔH° inputs are retained as seven printed rows in one
  typed-refusal context record because neither parameter has a store quantity
  token. The α values are
  `quoted_attributed`: the paper calculates them from fit parameters, but the
  input parameters cannot be stored as scored observations and therefore
  cannot be lineage parents. The four literature-derived values cite their
  original references; the three author-work values cite Lee’s plotted
  measurements, whose rate/pressure points are not tabulated.
- The temperature identity is unknown because the table prints only Tm/T;
  fO₂ is not stated; mixture composition is not stated; and the table gives no
  exact point total pressure. A strict pressure bound cannot populate the
  identity’s exact-Decimal axis, so the bound is retained as printed text and
  the exact pressure condition remains unknown. This is a schema limitation
  of the identity axis, not a point pressure. The external-source rows have no
  pressure condition stated here. The table does not print a phase tag for its
  rows, so migrated phase identity is left unknown. No ambient pressure, room
  temperature, or assumed oxygen fugacity was added.

## Migration and compare

Isolated real migration produced 7 observations, 0 hard issues, and 14 queued
metadata items (one missing phase and one unresolved composite method token
per α row). All seven α values migrate as point values. Every row carries a
located unknown absolute temperature at Table 1; the author-work rows also
carry the abstract’s pressure bound as printed text. For three representative
rows, the α identity profile requires temperature, total pressure, fO₂, and
composition; each remains unknown because the source supplies no exact
absolute temperature, point pressure, fO₂, or mixture composition. This is
also pinned in the focused migration test. The table comparator matched all
seven α values to the printed Table 1 rows and column 4, with zero mismatches,
missing matches, or mapping requests.

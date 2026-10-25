# Wahlbeck (1971) extraction audit

## Scope and table inventory

The scanned 133-page report has no text layer. The extraction pass converted all 26 MinerU table blocks to cell CSVs and ran the table QA pre-screen. The pre-screen confirms this is a scan, so its pdftotext digit agreement check is unavailable. MinerU also created 30 tables inside `<details>` chart blocks; these are invented figure readings and were excluded. Tesseract OCR (`eng`, 300 dpi, `--psm 6`) was the second machine read. The table draft lives only in dispatch scratch; the committed mapping selects its p036-t10 draft block while the extract uses the printed label Table IX.

| PDF page | Printed item | Disposition |
|---:|---|---|
| 5 | Table of contents | Excluded; navigation only. |
| 22 | Table I, Orifice Parameter Data | Excluded; apparatus data for the preceding selenide study. |
| 24 | Table II, Molecular Parameters (ScSe, YSe, LaSe) | Excluded; unrelated selenides. |
| 25 | Table III, Observed Species | Excluded; appearance potentials for selenides. |
| 27–29 | Tables IV–VI, dissociation energies of ScSe, YSe, and LaSe | Excluded; unrelated selenides. |
| 30 | Table VII, dissociation-energy comparison | Excluded; unrelated selenides. |
| 34 | Table VIII, Appearance Potentials | Excluded; not temperature, pressure, or rate data. |
| 36 | Table IX, TiO(g) pressure over Ti2O3(s) + Ti3O5(s) | Included; 11 printed runs. |
| 43 | Table X, experimental parameters for Figure 3 | Excluded; CsCl beam/orifice work. |
| 48 | Tables I–III | Excluded; boron-selenide mass-spectrometry observations. |
| 53 | Table I, Characteristics of the orifices | Excluded; CsCl velocity-distribution study. |
| 55 | Table II, CsCl near-ideal-orifice runs | Excluded; unrelated CsCl gas. |
| 55 | Table III, Velocity distribution data for three orifices | Excluded; unrelated CsCl gas. |
| 55 | Table IV, CsCl short-orifice runs | Excluded; unrelated CsCl gas. |
| 56 | Figure 6 data block | Excluded; figure-only velocity data. |
| 57 | Table V, CsCl long-orifice runs | Excluded; unrelated CsCl gas. |
| 58 | Table VI, parameters for Eq. (7) | Excluded; CsCl velocity-distribution analysis. |
| 64 | Table I, Regions of Gas Flow | Excluded; generic flow-regime theory. |
| 97 | Table I, Orifice Geometries and Characteristics | Excluded; CsCl beam/orifice work. |
| 120 | Table I, Effusion Orifice Dimensions | Excluded; apparatus for Ti(s,β) sublimation. |
| 124–125 | Table II, Vapor Pressure and Thermodynamic Data from this Study | Excluded; Ti(s,β) sublimation, not oxide or silicate vapor. |
| 127 | Table III, Ti(s,β) sublimation thermodynamics | Excluded; summary values, not target oxide/silicate observations. |

The machine-text search found no printed effusion table rows for SiO2, MgO, Al2O3, CaO, FeO, Na2O, K2O, or SiO. Table IX is an adjacent regolith-oxide-vapor dataset for TiO and is extracted in full. CsCl, boron and metal selenides, elemental Ti, generic flow equations, and their geometry/velocity tables are unrelated materials excluded above. No figure-only oxide/silicate pressure values were identified; the Figure 3 and Figure 6 blocks concern CsCl and were not digitized.

## Cell audit: printed Table IX

The caption identifies **Table IX**, printed page 31 (PDF page 36), with runs 2–12: 11 body rows. The 11 runs map to 11 observations. All row/column assignments and all 22 stored numeric cells (temperature and pressure) were checked against the 220 dpi page render (method b). The rendered layout confirms the pressure is column 3 and the true temperature is column 2. Run labels are included below to make the row alignment auditable.

The pressure cells use `mantissa x 10^-7 atm`. MinerU retained them as LaTeX strings, which the draft comparator does not parse as numeric candidates; Tesseract also mangles the superscript exponents and fuses the final run/temperature line. The render resolves every exponent and row. Comparator mismatches for these cells therefore reflect its numeric parser, not disagreement with the printed scan; each was resolved by render. The row count and species assignment were also checked against the caption and headers in the render.

| PDF page / table row | Run | Printed true T (K) | Printed P_TiO (atm) | Cell audit |
|---|---:|---:|---:|---|
| 36 / row 2 | 2 | 2010 | 3.1848 × 10⁻⁷ | T and pressure: (b), render; exponent resolved. |
| 36 / row 3 | 3 | 2022 | 3.6856 × 10⁻⁷ | T and pressure: (b), render; exponent resolved. |
| 36 / row 4 | 4 | 1995 | 2.7056 × 10⁻⁷ | T and pressure: (b), render; exponent resolved. |
| 36 / row 5 | 5 | 2032 | 4.3096 × 10⁻⁷ | T and pressure: (b), render; exponent resolved. |
| 36 / row 6 | 6 | 1977 | 1.6208 × 10⁻⁷ | T and pressure: (b), render; exponent resolved. |
| 36 / row 7 | 7 | 2043 | 5.6656 × 10⁻⁷ | T and pressure: (b), render; exponent resolved. |
| 36 / row 8 | 8 | 1968 | 1.3344 × 10⁻⁷ | T and pressure: (b), render; exponent resolved. |
| 36 / row 9 | 9 | 1957 | 1.3328 × 10⁻⁷ | T and pressure: (b), render; exponent resolved. |
| 36 / row 10 | 10 | 1947 | 0.8944 × 10⁻⁷ | T and pressure: (b), render; exponent resolved. |
| 36 / row 11 | 11 | 1985 | 2.4624 × 10⁻⁷ | T and pressure: (b), render; exponent resolved. |
| 36 / row 12 | 12 | 1936 | 0.8704 × 10⁻⁷ | T and pressure: (b), render; exponent resolved. |

The run label, temperature and pressure columns agree with the visual row grouping. Tesseract pressure exponents appear variously as `1077`, `10°”`, or `10’`; these are not accepted as values. It also merges the final run label with 1936. The render provides the values in the table above.

## Equipment, method, and conditions audit

- PDF page 33 (printed page 28), section “2. TiO(g) (Hung Yuan Wu)” (headed on PDF page 31 / printed page 26): the prose identifies a W Knudsen cell and effective orifice area `0.01445 cm2`. The rendered page confirmed the apparatus text and value. The schema stores the area in SI as `1.445e-6 m2`; the printed value and conversion are retained in its locator note.
- PDF page 35 (printed page 30), same section: rendered prose says TiO pressure was calculated from cell mass loss using the stated `P_TiO:P_TiO2 = 1.0:0.2` pressure correction. The raw mass-loss-rate inputs are not tabulated. The pressure rows are therefore `measured_reduced`; their derivation declares those inputs `not_printed` and does not invent parent observations.
- Table IX temperatures are stored on every row through `values.T_K` and `point_conditions.temperature_K` with the row’s page/table locator.
- The report calls the support a vacuum balance, but prints no numeric chamber/total pressure and no oxygen fugacity for these runs. The TiO partial pressure is a calculated output, not a total-pressure condition. No total-pressure, fO2, or composition condition is assigned to the rows. The qualitative Ti2O3/Ti3O5 phase assemblage is recorded as sample form; no bulk proportion is printed.

## Table-tool outputs

- `mineru_table_draft convert` produced drafts for all 26 MinerU tables. `mineru_table_qa` reported 26 scan tables, 9 flagged tables, and 30 invented details-block tables. It skipped exact-text-layer digit agreement. Table IX p036-t10 was flagged for 11 LaTeX and 11 multiple-number cells; its row layout and pressure cells were render-checked.
- `mineru_table_draft emit` emitted the 11 Table IX observations from columns 2 (temperature) and 3 (TiO pressure).
- `mineru_table_draft compare` reported 11 mismatches because the pressure column's `x $10^{-7}$` cell spelling is not parsed as a numeric draft candidate. The committed compare output records every affected value. All 11 were checked and resolved against the page render above; no mismatch remains unresolved.
- The focused isolated-migration test pins all 11 temperatures and pressures to these render readings, the 11-row count, the page 36 / printed Table IX locators, the converted orifice area, and the absence of unprinted pressure and fO2 conditions.
- Mutation check: changing Run 2 in the extract from `3.1848e-7` to `3.1849e-7` made the focused test fail on the migrated pressure assertion. The original extract was restored immediately afterward.

## Isolated migration evidence

Real migrator result: 11 observations, 0 hard issues, 0 unavailable values, 11 point values. For each profile-required identity axis, three rows report:

| Run | Composition | Total pressure (Pa) | fO2 (Pa) | Temperature (K) |
|---:|---|---|---|---:|
| 2 | unknown: no composition mapped from source | unknown: in-cell total pressure not derivable | unknown: no fO2 mapped from source | 2010 |
| 7 | unknown: no composition mapped from source | unknown: in-cell total pressure not derivable | unknown: no fO2 mapped from source | 2043 |
| 12 | unknown: no composition mapped from source | unknown: in-cell total pressure not derivable | unknown: no fO2 mapped from source | 1936 |

The P_PARTIAL identity profile requires composition, total pressure, fO2, and temperature. The three unknowns are consistent with the printed source: no bulk phase ratio, numeric total pressure, or fO2 is reported for the runs; temperature is mapped from each row.

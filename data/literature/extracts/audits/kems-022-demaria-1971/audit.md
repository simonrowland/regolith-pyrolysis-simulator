# DeMaria et al. 1971 extraction audit

Source: `kems-022-demaria-1971`. The supplied pdftotext layout contains only the
article identifier on each page and no table text. MinerU tables were the
machine transcription. I rendered PDF pages 4–6 and 9–11 and checked every
stored numeric table cell, including its row and column assignment, against the
render: all audited cells below are **(b)**. Table rows and species headings
were checked in the same render pass. No OCR redo was needed: MinerU read the
tables; the renders resolved the single-read case.

| Table | PDF page (printed page) | Printed body rows | Numeric pressure values stored | Audit |
|---|---:|---:|---:|---|
| 1 | 4 (1370) | 7 | 7 | T and O2 pressure cells in rows 1–7 (b) |
| 2 | 5 (1371) | 4 | 5 | T, Mn and Cr cells in rows 1–4 (b); render confirms MinerU `Cf` header is Cr |
| 3 | 6 (1372) | 8 | 8 | T and Mg cells in rows 1–8 (b); NAA/BC assignment checked |
| 4 | 9 (1375) | 15 | 20 | T, Al2O and AlO cells in rows 1–15 (b); render confirms AlO header and NAA/BC columns |
| 5 | 9 (1375) | 13 | 13 | T and atomic-O cells in rows 1–13 (b); NAA/BC assignment checked |
| 6 | 10 (1376) | 6 reactions | 10 reported heats | Reaction 1–6 and continuation cells for reactions 4 and 6 (b); ± signs and footnote a checked |
| 7 | 11 (1377) | 9 | 27 cells | Sample, T, −R ln Kp, −Δ(fef), and ΔH0 for rows 1–9 (b); footnotes a/b checked |
| 8 | 11 (1377) | 5 | 21 cells | T, Kp, −R ln Kp, −Δ(fef), and ΔH0 for rows 1–5 plus mean (b); footnotes a/b checked |

The Table 1–5 pressure total is 53 printed values: 33 KEMS-reduced values and
20 values already classified `measured_direct`. The pressure table row counts
reconcile to 7 + 4 + 8 + 15 + 13 = 47 printed rows; Table 2 has two pressure
values in its 1725 K row, giving 53 values. The 9/16 task counts were a planner
priority tally, not a filter. Figure-only F rows were not changed or promoted.

## Pressure audit cells

Each row below gives the printed temperature and every non-dash pressure cell;
all listed cells were read from the render (b). Dashes remain absence, not zero.

| PDF page / table row | Printed cells (b) |
|---|---|
| 4 / T1.1–T1.7 | 1396 K: O2 5.54×10⁻⁹ atm; 1475 K: 4.96×10⁻⁸; 1433 K: 3.78×10⁻⁹; 1482 K: 1.22×10⁻⁸; 1499 K: 2.11×10⁻⁸; 1471 K: 1.27×10⁻⁸; 1459 K: 8.78×10⁻⁹ |
| 5 / T2.1–T2.4 | 1583 K: Mn 7.66×10⁻⁹; 1609 K: Mn 1.32×10⁻⁸; 1725 K: Mn 8.47×10⁻⁸ and Cr 1.03×10⁻⁸; 1794 K: Cr 6.2×10⁻⁸ atm |
| 6 / T3.1–T3.8 | 12022: 1650 K BC 1.6×10⁻⁸, 1694 K NAA 5.7×10⁻⁸, 1815 K NAA 3.96×10⁻⁶ atm; 12065: 1747 K 1.65×10⁻⁹, 1854 K 9.4×10⁻⁹, 1776 K 2.40×10⁻⁹, 1857 K 8.85×10⁻⁸, 1927 K 3.20×10⁻⁸ atm (NAA) |
| 9 / T4.1–T4.9 | 12022 Al2O/NAA: 2060 K 5.07×10⁻⁹; 2104 K 1.13×10⁻⁸; 2125 K 1.57×10⁻⁸; 2146 K 2.24×10⁻⁸; 2180 K 3.65×10⁻⁸; 2183 K 3.82×10⁻⁸; 2194 K 4.01×10⁻⁸; 2290 K 1.55×10⁻⁷; 2270 K 1.04×10⁻⁷ atm |
| 9 / T4.10–T4.15 | 12065: 2320 K AlO/BC 1.05×10⁻⁶; 2360 K Al2O/BC 6.90×10⁻⁷ and AlO/BC 1.30×10⁻⁶; 2395 K 9.88×10⁻⁷ and 2.22×10⁻⁶; 2425 K 1.90×10⁻⁶ and 3.79×10⁻⁶; 2465 K 3.39×10⁻⁶ and 6.84×10⁻⁶; 2500 K 4.73×10⁻⁶ and 8.96×10⁻⁶ atm |
| 9 / T5.1–T5.7 | 12022 O/NAA: 2015 K 9.2×10⁻⁸; 2060 K 2.2×10⁻⁷; 2104 K 3.2×10⁻⁷; 2146 K 6.5×10⁻⁷; 2162 K 9.0×10⁻⁷; 2194 K 8.54×10⁻⁷; 2270 K 2.0×10⁻⁶ atm |
| 9 / T5.8–T5.13 | 12065 O/BC: 2320 K 1.63×10⁻⁶; 2360 K 2.91×10⁻⁶; 2395 K 3.80×10⁻⁶; 2425 K 7.40×10⁻⁶; 2465 K 1.13×10⁻⁵; 2500 K 1.42×10⁻⁵ atm |

## Thermochemistry audit cells

Every cell listed is from the page render (b). Table 6 has six reaction rows;
the two continuation values are separately stored as second-law outputs.
Tables 7 and 8 retain each printed numeric column as a separate observation.

| PDF page / table row | Printed cells (b) |
|---|---|
| 10 / T6.1 reaction 1 | BC 47 ± 2; NAA 46 ± 2 kcal/mole |
| 10 / T6.2 reaction 2 | BC 112 ± 3 |
| 10 / T6.3 reaction 3 | BC 15.6 ± 2 |
| 10 / T6.4 reaction 4 and continuation | BC 39 ± 2; NAA 38 ± 2; second law BC 38 ± 3ᵃ |
| 10 / T6.5 reaction 5 | BC 83 ± 2 |
| 10 / T6.6 reaction 6 and continuation | NAA 142 ± 3; second law NAA 143 ± 8ᵃ |
| 11 / T7.1–T7.5 (12022) | 2060 K: 4.09, 19.025, 47.6; 2080 K: 3.80, 19.02, 47.5; 2120 K: 3.07, 19.00, 46.8; 2150 K: 3.07, 18.99, 47.4; 2170 K: 2.70, 18.98, 47.0 |
| 11 / T7.6–T7.9 (12065) | 1990 K: 4.62, 19.05, 47.1; 2035 K: 4.30, 19.03, 47.5; 2060 K: 3.98, 19.02, 47.4; 1850 K: 5.995, 19.105, 46.4ᵇ; mean 47.3 ± 0.3ᵃ |
| 11 / T8.1–T8.5 | 2360 K: 3.19×10⁻⁵, 20.56, 27.26, 112.8; 2395 K: 4.02×10⁻⁵, 20.01, 27.30, 113.4; 2425 K: 8.03×10⁻⁵, 18.73, 27.32, 111.6; 2465 K: 1.33×10⁻⁴, 17.74, 27.35, 111.1; 2500 K: 1.58×10⁻⁴, 17.39, 27.38, 111.9; mean 112.2 ± 0.9ᵇ |

Table 6 reaction enthalpies, Table 7 −R ln Kp and −Δ(fef) columns, and Table 8
Kp/−R ln Kp/−Δ(fef) columns have no matching quantity token in schema v2.1.
They remain separate typed-refused observations; no schema or migrator change was
made. Table 7 and Table 8 ΔH0 are reaction enthalpies, not formation enthalpies
or vaporization enthalpies, and are likewise typed-refused. No Table 6–8 result
is labelled a direct instrument measurement: these are the authors' thermodynamic
calculations from measured gas pressures and JANAF/Hultgren functions. The raw
ion currents and all fit inputs are not stored as observation parents, so the
pressure points retain `measured_reduced` and the migrator reports missing
reduced-lineage fields rather than an invented parent.

## Conditions, migration, and changed observation IDs

The article prints T for all table pressure points, but no experiment total
pressure, absolute fO2, or run melt composition for these rows. The extract maps
the printed T into `T_K` and point conditions. It leaves total P, fO2, and
composition unknown; no 1 atm, vacuum, ambient, or room-temperature condition
was added. C2's 70 numeric partial-pressure points refused with
`in_cell_total_pressure_not_derivable` therefore remain expected pending the Q1
policy (74 observations total include four nonnumeric or summary rows).

Changed pressure series (33 measured-reduced point values):
`demaria_1971_mn_psat_table2_sample_12022`,
`demaria_1971_cr_psat_table2_sample_12022`,
`demaria_1971_mg_psat_table3_lunar_basalt`,
`demaria_1971_al2o_psat_table4`, and
`demaria_1971_alo_psat_table4`. Direct Table 1 O2 and Table 5 O rows were left
`measured_direct`.

Changed/split thermochemistry observations:
`demaria_1971_table6_feo_dissociation`,
`demaria_1971_table6_feo_dissociation_second_law`,
`demaria_1971_table6_sio2_dissociation`,
`demaria_1971_table6_tio2_half_o2`,
`demaria_1971_table6_tio2_atomic_o`,
`demaria_1971_table6_tio2_atomic_o_second_law`,
`demaria_1971_table6_al2o_o_2alo`,
`demaria_1971_table6_alo_dissociation`,
`demaria_1971_sio2_sio_o2_equilibrium_table7`,
`demaria_1971_sio2_sio_o2_table7_minus_R_ln_Kp`,
`demaria_1971_sio2_sio_o2_table7_minus_delta_fef`,
`demaria_1971_alo_al_o_kp_table8`,
`demaria_1971_alo_al_o_table8_Kp`,
`demaria_1971_alo_al_o_table8_minus_R_ln_Kp`, and
`demaria_1971_alo_al_o_table8_minus_delta_fef`.

Isolated real migrator on the edited extract: 1 work, 3 experiments, 152
observations, 75 unavailable values, 303 unavailable queue entries, and 232
validation issues. There are zero hard `conditional_field` issues: all five
`measured_reduced` pressure series declare the source's printed calibration
method with unprinted raw inputs. The source does not publish the raw
ion-current/calibration values as observation rows, so no parent observations
were invented. The 232 `identity_incomplete` issues are expected for source
rows whose scoring profile requires composition, fO2, or total pressure that the
paper does not print.
Unsupported thermochemistry quantities remain explicit typed refusals. The
figure-only F rows had their derived `p_Pa` keys removed; the printed atm values
remain, and no converted pressure twins are stored.
No battery store was regenerated. Expected store delta: +35 observation rows
(33 Table 7–8 split point quantities and two Table 6 second-law summaries), with
no new works or experiments.

Identity/condition projection from the isolated migration, using the axes
required by the pressure profiles (`composition`, `total_pressure_Pa`, `fO2_Pa`,
`temperature_K`): Table 2 Mn row 1 has T=1583 K in identity and a
`temperature_K` point condition located at Table 2, lines 157–159; Table 1 O2
row 1 has identity T=1396 K and unknown composition, total pressure
(`in_cell_total_pressure_not_derivable`), and fO2; Table 4 Al2O row 1 has
identity T=2060 K and the same three unprinted axes unknown. Point conditions
for each contain the printed temperature with a table locator. The paper prints
no total pressure, absolute fO2, or row-level composition for these runs.

The focused test asserts 53 pressure values across 47 printed table rows and
pins representative rendered values from each of Tables 1–5, plus the separate
Table 6–8 quantities. This regression pin is separate from the full manual cell
audit above.
The committed MinerU comparison output is `mineru-compare.csv`. It is
presence-only and reports 35 no-matches: the two pre-existing Fe activity
values from Figure 3, plus the 33 newly split Table 7–8 point values. It does
not assign these values to cells because the split observations do not have
per-cell page mappings; every one of those table values was instead checked
directly against the page 11 render above. The comparison is not treated as
cell-level verification.

# Johnston (1965) extraction audit

Source: *Oxidation-reduction equilibria in molten Na2O.2SiO2 glass*, published pp. 184–190. Table I is published p. 186 (PDF page 3, zero-based index 2); the apparent-enthalpy list is published p. 189 (PDF page 6, index 5).

## Counts and assignments

- Table I has 47 printed data rows. The MinerU table block has 55 body rows: 47 data rows and 8 element-column subheaders. The extract has exactly 47 Table I observations, with two printed wt% cells per row.
- The rendered row-block counts are Ti 7, Sn 6, Ce 7, Sb 5, Mn 7, Co 6, Ni 2, V4+/V5+ 4, and V3+/Total V 3. These total 47. There is no Fe data block in Table I; Fe occurs only in the apparent-enthalpy list.
- The paper prints reduced-species wt% and total-element wt%. Both are stored as printed; no ratios were calculated.
- Asterisks on Total Ti*, Total Co*, and Total Ni* are retained; the page footer says “* Nominal.” Ditto marks in the Colour column are retained in `color_as_published`, with the resolved colour held separately.
- All Table I rows represent quenched glass from the Na2O.2SiO2 melt; each has T_C and point_conditions with state and locator on the parent and both values.points entries. The source prints no row-level numeric pO2 or total pressure, so those remain explicit unknowns in point_conditions. Composition is encoded as component pairs for the migrator's point-condition reader. Alkali_shuttle is context for this alkali-rich melt; the observations are on the redox rail.
- The source says opaque samples became somewhat devitrified during quenching (p. 186), reports NaSbO3 X-ray patterns for the Sb O2 and air samples at 1085 C (p. 187), and notes possible glass separation on cooling for Ti CO-C at 1200 and 1300 C (p. 187). Those five rows are marked asserted_altered; remaining Table I rows are not_stated. No row is marked asserted_preserved.

## Full numeric-cell audit

Evidence (a): MinerU table and pdftotext read the same digits. Evidence (b): rendered published p. 186 resolves a disagreement or a cell missed by pdftotext. Temperatures and both wt% cells are listed for every Table I row.

| Row | Printed block and atmosphere | T C | Reduced-species wt% | Total-element wt% |
|---:|---|---:|---:|---:|
| 1 | Ti3+; CO/CO2 = 10 | 1085 (a) | 0.021 (a) | 1.50 (a) |
| 2 | Ti3+; CO/CO2 = 100 | 1085 (a) | 0.035 (a) | 1.50 (a) |
| 3 | Ti3+; H2-H2O (0°C) | 1085 (a) | 0.089 (a) | 1.50 (a) |
| 4 | Ti3+; CO-C | 1085 (a) | 0.083 (a) | 1.50 (a) |
| 5 | Ti3+; CO-C | 1000 (a) | 0.065 (a) | 1.50 (a) |
| 6 | Ti3+; CO-C | 1200 (a) | 0.115 (a) | 1.50 (a) |
| 7 | Ti3+; CO-C | 1300 (a) | 0.218 (b) | 1.50 (a) |
| 8 | Sn2+; CO2 | 1085 (a) | 0.011 (a) | 1.68 (a) |
| 9 | Sn2+; CO2/CO = 100 | 1085 (a) | 0.71 (a) | 1.68 (a) |
| 10 | Sn2+; CO2/CO = 10 | 1085 (a) | 1.34 (a) | 1.68 (b) |
| 11 | Sn2+; CO2/CO = 100 | 1000 (a) | 0.387 (a) | 1.68 (a) |
| 12 | Sn2+; CO2/CO = 100 | 1200 (a) | 0.955 (a) | 1.56 (a) |
| 13 | Sn2+; CO2/CO = 100 | 1300 (a) | 0.727 (a) | 1.32 (b) |
| 14 | Ce4+; O2 | 1085 (a) | 2.45 (a) | 3.31 (a) |
| 15 | Ce4+; air | 1085 (b) | 2.18 (a) | 3.20 (b) |
| 16 | Ce4+; Argon/O2 = 100 | 1085 (b) | 1.47 (a) | 3.18 (a) |
| 17 | Ce4+; CO2 | 1085 (b) | 0.685 (a) | 3.10 (a) |
| 18 | Ce4+; air | 1000 (a) | 2.49 (a) | 3.38 (a) |
| 19 | Ce4+; air | 1200 (a) | 1.84 (a) | 2.92 (a) |
| 20 | Ce4+; air | 1300 (a) | 1.53 (a) | 2.54 (a) |
| 21 | Sb3+; O2 | 1085 (a) | 0.10 (a) | 1.68 (a) |
| 22 | Sb3+; air | 1085 (a) | 0.31 (a) | 1.79 (a) |
| 23 | Sb3+; Argon/O2 = 100 | 1085 (a) | 1.00 (a) | 1.97 (a) |
| 24 | Sb3+; air | 1200 (a) | 0.82 (a) | 1.94 (a) |
| 25 | Sb3+; air | 1300 (a) | 1.01 (a) | 1.34 (a) |
| 26 | Mn3+; O2 | 1085 (a) | 0.21 (a) | 1.375 (a) |
| 27 | Mn3+; air | 1085 (a) | 0.17 (a) | 1.43 (a) |
| 28 | Mn3+; Argon/O2 = 100 | 1085 (a) | 0.076 (a) | 1.46 (a) |
| 29 | Mn3+; CO2 | 1085 (a) | 0.021 (a) | 1.388 (a) |
| 30 | Mn3+; air | 1000 (a) | 0.20 (a) | 1.43 (a) |
| 31 | Mn3+; air | 1200 (a) | 0.12 (a) | 1.43 (a) |
| 32 | Mn3+; air | 1300 (a) | 0.08 (a) | 1.43 (a) |
| 33 | Co3+; O2 | 1085 (a) | 0.13 (a) | 1.84 (a) |
| 34 | Co3+; air | 1085 (a) | 0.085 (a) | 1.84 (a) |
| 35 | Co3+; Argon/O2 = 100 | 1085 (a) | 0.042 (a) | 1.84 (a) |
| 36 | Co3+; air | 1000 (a) | 0.087 (a) | 1.84 (a) |
| 37 | Co3+; air | 1200 (a) | 0.065 (a) | 1.84 (a) |
| 38 | Co3+; air | 1300 (a) | 0.068 (b) | 1.84 (a) |
| 39 | Ni3+; O2 | 1085 (a) | 0.046 (a) | 1.96 (a) |
| 40 | Ni3+; air | 1085 (a) | 0.042 (a) | 1.96 (a) |
| 41 | V4+/V5+; CO2 | 1085 (a) | 0.033 (a) | 1.16 (a) |
| 42 | V4+/V5+; CO2/CO = 100 | 1085 (a) | 0.237 (a) | 1.01 (a) |
| 43 | V4+/V5+; CO2/CO = 10 | 1085 (a) | 0.58 (a) | 0.73 (a) |
| 44 | V4+/V5+; CO2/CO = 1 | 1085 (a) | 1.25 (a) | 0.14 (a) |
| 45 | V3+/Total V; CO/CO2 = 10 | 1085 (a) | 0.63 (a) | 1.26 (a) |
| 46 | V3+/Total V; CO/CO2 = 100 | 1085 (a) | 1.02 (a) | 1.34 (a) |
| 47 | V3+/Total V; CO-C | 1085 (a) | 1.00 (a) | 1.31 (a) |

Rendered exceptions on p. 186: row 7 Ti3+ is 0.218 (pdftotext OCR: “(I . 218”); row 10 Total Sn is 1.68 (pdftotext OCR: “1 .fix”); row 13 Total Sn is 1.32 (pdftotext OCR: “1.38”); row 15 Total Ce is 3.20 (MinerU 3.20, pdftotext OCR 8.20); row 15–17 temperatures are 1085 (MinerU reads them, pdftotext omits them); row 38 Co3+ is 0.068 (MinerU 0.068, pdftotext OCR “0 ,068”). The render also resolves the V column headers as V4+/V5+ in rows 41–44 and V3+/Total V in rows 45–47; MinerU placed V3+ in both first-block headers. The render confirms all row-to-element assignments, colour ditto marks, and nominal-total asterisks.

The committed MinerU compare output contains 101 comparisons: 101 matched, 0 unmatched, 0 mismatched. Every stored Table I cell and Table II enthalpy value was confirmed against both machine reads or the page render as marked above.

## Apparent enthalpy list

Both machine reads agree on the seven printed values on p. 189. Table I fits stored as parents for model-derived entries are the same-atmosphere temperature sequences: Ti CO-C rows 4–7; Sn CO2/CO=100 rows 9, 11–13; Sb air rows 22, 24–25; Ce air rows 15, 18–20; Mn air rows 27, 30–32; Co air rows 34, 36–38. These are the temperature-dependent series used for each listed fit. Fe has no Table I row in this extract; it is retained as quoted_attributed because its underlying inputs are not stored.

| Element | Apparent ΔH (kcal per element) | Evidence |
|---|---:|---|
| Ti | 115 | (a) |
| Sn | 170 | (a) |
| Fe | 110 | (a) |
| Sb | 105 | (a) |
| Ce | 30 | (a) |
| Mn | 55 | (a) |
| Co | 20 | (a) |

## Migrator and scope

The isolated real-migrator run emitted 101 observations: 94 children from the 47 two-cell Table I rows and 7 apparent-enthalpy values. All 101 are unavailable because neither stored quantity has a schema token; hard issues: 0; migration queue: 216. Every Table I child carries point_conditions keys T_C, temperature_K, atmosphere, fO2_Pa, total_pressure_Pa, and composition (94/94); every Table II child carries T_C, atmosphere, fO2_Pa, total_pressure_Pa, and composition (7/7). No ratio or apparent enthalpy was computed.

For Ti row 1, Sn row 13, and Ce row 15, Identity composition and temperature_K map from the extract (mole fractions Na2O 1/3, SiO2 2/3; temperatures 1358.15 K, 1573.15 K, 1358.15 K). The source prints gas mixtures as atmosphere controls but no numeric total pressure or absolute fO2; point_conditions retains the atmosphere and unknown pressure/fO2 states. The migrator therefore leaves total_pressure_Pa and fO2_Pa unset in Identity; no printed numeric value is lost, and the schema or migrator was not changed.

Figure-read candidates not extracted: log pO2 axes/curves in Figs. 2–3 and temperature-dependent equilibrium-constant curves in Fig. 5.

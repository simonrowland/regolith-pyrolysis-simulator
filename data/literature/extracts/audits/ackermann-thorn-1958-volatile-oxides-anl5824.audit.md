# Ackermann and Thorn (1958) extraction audit

## Source and method

The report is a scan without a usable text layer. Transcription used the MinerU markdown and table blocks plus the independent Tesseract OCR. The PDF page renders were used to confirm every numeric table cell and the row/column assignments. Absolute PDF page numbers are stored in locators; printed page numbers are two lower.

The table draft contains three blocks: Table I on PDF p. 10, Table II on p. 13, and Table III on p. 15. The draft misread the first body row in Tables II and III as a header. Table I has eight printed body rows and twelve separate oxide/cell/temperature measurement entries; the refusal record retains those twelve entries. Table II has eight data rows and Table III has four. The first body row of each table and each column assignment were checked against the render.

Table I's row locators use the eight printed body rows, not the twelve expanded measurement entries. Entries 1–3 are Al2O3 in the tungsten columns on source rows 1–3. Entry 4 is the single Al2O3/tantalum value in the merged tantalum cells on source row 1. Entry 5 is MgO/tungsten on row 4; entries 6–7 are UO2 tungsten/tantalum on row 5; entries 8–9 are ThO2 tungsten/tantalum on row 6; entry 10 is BeO/tungsten on row 7; and entries 11–12 are ZrO2 tungsten/tantalum on row 8. Each locator records the row oxide label, the printed tungsten or tantalum group heading, and its `T (°K)` subcolumn.

## Full-cell audit

All table entries below were render-confirmed (method b). The two OCR reads were used as the initial transcription; the text layer is empty, so it was not a usable independent cell read. The table-tool comparison matches all eight migrated Table II pressure cells after applying the printed `× 10^6` scale factor; see the committed compare output.

### Table I — oxide weight transported per cell metal, PDF p. 10, printed Table I

| Entry | Oxide / cell | Temperature (K) | Weight ratio as printed | Product interpretation as printed |
|---:|---|---:|---:|---|
| 1 | Al2O3 / tungsten | 2544 | 1.23 ± 0.01 | Al2O + Al + (O) + WOx |
| 2 | Al2O3 / tungsten | 2582 | 1.16 ± 0.01 | Al2O + Al + (O) + WOx |
| 3 | Al2O3 / tungsten | 2635 | 1.13 ± 0.01 | Al2O + Al + (O) + WOx |
| 4 | Al2O3 / tantalum | 2523 | 0.19 ± 0.01 | Al + TaO, with smaller amounts of Al2O and TaO2 |
| 5 | MgO / tungsten | 2045 | 0.66 ± 0.01 | Mg + WO3 |
| 6 | UO2 / tungsten | 1600–2500 | ≥50 | UO2 |
| 7 | UO2 / tantalum | 2100–2500 | ≥50 | UO2 |
| 8 | ThO2 / tungsten | 2765 | ≥50 | ThO2 + (ThO + O) |
| 9 | ThO2 / tantalum | 2753 | 3.06 | ThO + TaO + TaO2 |
| 10 | BeO / tungsten | 2462 | 0.92 | Be + WOx + (O); possibly some BeO |
| 11 | ZrO2 / tungsten | 2600 | ≥50 | ZrO2 + ZrO + O |
| 12 | ZrO2 / tantalum | 2560 | 0.89 ± 0.02 | ZrO + TaO + TaO2 |

The weight ratio has no quantity token, so these twelve measurements remain a typed refusal rather than being re-labelled as a pressure or rate. No Table I orifice dimension or temperature calibration statement is printed. Cell-vaporization background was measured separately before and after oxide vaporization and corrected when its rate returned to the cell-only rate. Uncertainty widths are retained as printed; the report does not state a confidence level.

### Table II — WO3 effusion, PDF p. 13, printed Table II

The column order is total cell-and-contents mass loss (mg), duration (s), temperature (K), `p(WO3) × 10^6` (atm), `−log10 p`, and `10^4/T`. All eight rows use a tungsten cell and an orifice area of 2.88 × 10^-2 cm² at the average temperature.

| Run | Mass loss (mg) | Time (s) | T (K) | p(WO3) × 10^6 (atm) | −log10 p | 10^4/T |
|---:|---:|---:|---:|---:|---:|---:|
| 1 | 10.4 | 1800 | 2005 | 8.458 | 5.0727 | 4.988 |
| 2 | 8.8 | 2100 | 1977 | 6.091 | 5.2153 | 5.058 |
| 3 | 8.6 | 4500 | 1922 | 2.739 | 5.5624 | 5.203 |
| 4 | 9.7 | 4740 | 1925 | 2.934 | 5.5325 | 5.195 |
| 5 | 6.2 | 1800 | 1956 | 4.980 | 5.3028 | 5.112 |
| 6 | 7.3 | 900 | 2019 | 11.91 | 4.9241 | 4.953 |
| 7 | 8.0 | 1841 | 1975 | 6.311 | 5.1999 | 5.063 |
| 8 | 7.8 | 1200 | 1996 | 9.494 | 5.0255 | 5.010 |

Runs 1–5 use the printed MgO charge; runs 6–8 use MgO mixed with tungsten powder, with no mixture ratio stated. The magnesium deposit reflected heat back to the cell and raised its temperature. The authors report a probable 2.5-degree error for a representative experiment; no separate calibration procedure is given. Equation (13), PDF p. 17, reduces the printed total cell-and-contents mass-loss rate to WO3 pressure under the stated Mg(g) and WO3(g) assumption. Since the schema cannot store the mass-loss and time inputs as parent quantities, the pressure rows are `quoted_attributed`; their raw inputs and equation are retained in the extract. No total pressure or oxygen fugacity is stated. The authors identify periclase in all residues, tungsten lines in runs 6–8, and an unidentified phase.

### Table III — ZrO2 total volatility, PDF p. 15, printed Table III

| Run | T (K) | Mass loss (mg) | Time (s) | Pressure as printed | Orifice area |
|---:|---:|---:|---:|---:|---:|
| 1 | 2526 | 1.7* | 3600 | 1.90 × 10^-6 atm | 2.54 × 10^-2 cm² |
| 2 | 2434 | 1.2* | 7200 | 6.56 × 10^-7 atm | 2.54 × 10^-2 cm² |
| 3 | 2745 | 13.0* | 1800 | 3.02 × 10^-5 atm | 2.54 × 10^-2 cm² |
| 4 | 2632 | 11.3** | 3600 | 9.43 × 10^-6 atm | 3.46 × 10^-2 cm² |

All runs used a tungsten cell; the zirconia contained about 2% hafnium and tungsten background loss was corrected. The table does not state a temperature calibration procedure. The `*` and `**` marks assign the two printed orifice areas; `***` on the pressure heading states that the calculation assumes the ZrO2 molecular weight. These pressures are for unresolved aggregate vapor, so the rows remain typed refusals rather than species partial pressures. Total pressure and fO2 are not printed conditions. Equation (7), PDF p. 14, is the corresponding total-volatility fit and remains typed-refused for the same reason. Figure 2 plots ZrO and ZrO2 pressure curves; no figure-only points were digitized.

## Equation and free-energy checks

The equation numbers below are the printed equation locators; each expression was checked against its page render where the OCR sign, exponent, or layout was uncertain.

| PDF page / equation | Stored content | Audit outcome |
|---|---|---|
| 18 / 16 | Al2O formation reaction (15): `4/3 Al(l) + 1/3 Al2O3(s) → Al2O(g)`; `ΔF° ≈ 86900 − 35.21T cal/mole`; heat at about 1650 K is 86.9 kcal/mole; entropy estimates 35.21 and 37.82 eu, with 35.21 selected | Typed refusal: non-formation reaction free energy has no schema token |
| 18 / 17 | Al2O(g) `ΔfF = −47200 − 9.27T cal/mole` | Stored as formation free-energy function, quoted-attributed because reference inputs are not stored |
| 18 / 21 | O2(g) → 2O(g), `ΔF°D = 121945 − 32.22T cal/mole` | Typed refusal: dissociation reaction free energy has no schema token |
| 18 / 22 | Al2O(g) → 2Al(g) + 1/2O2(g), `ΔF°D ≈ 252100 − 2.288T log T − 51.92T cal/mole` | Typed refusal: dissociation reaction free energy has no schema token |
| 19 / 23 | WO3(g) `log p(atm) = −24760/T + 7.409` | Stored as a model-derived pressure relation; parents are exactly the eight Table II pressure observations |
| 19 / 24 | Mg(g) `log p(atm) = −6969/T + 5.013` | Retained as contextual approximate relation attributed to Hartmann and Schneider; not a WO3 result |
| 20 / 31 | WO3(g) `ΔfF = −75649 − 1.650T log T + 20.20T + 38 × 10^-5 T² cal/mole` | Stored as quoted-attributed formation free-energy function; external inputs are not stored |
| 21 / 34 | ZrO(g) `ΔfF = 25320 + 7.588T log T − 45.58T cal/mole` | Stored as quoted-attributed formation free-energy function |
| 22 / 36 | TaO(g) `ΔfF = 43754 − 19.13T cal/mole`, valid 1500–3000 K; approximation error less than ±5% | Stored with the printed range and uncertainty; confidence level not stated |
| 22 / 38 | TaO2(g) `ΔfF = −6.650T + 0.505T log T + 9.773 × 10^-4 T² + 721825/T − 40845 cal/mole` | Stored as quoted-attributed formation free-energy function; MinerU, fresh Tesseract, and the PDF render agree on `−6.650T` |
| 24 / prose | ThO(g) at 2500 K is approximately −53 kcal/mole as a lower limit; TaO2 and possibly ThO2 also contribute | Typed refusal: the scalar Gibbs extraction path cannot carry a one-sided bound; retain the printed approximate bound without migrating it as an exact point |
| 24 / prose | UO(g) at 2500 K is a lower limit of −42 kcal/mole; the bound uses `P_UO ≤ 0.10 P_UO2` | Typed refusal for the same bound-shape gap; this is UO, not UO2, and the report gives no numeric UO2(g) free energy |
| 25 / 45–46 | ZrO2(s) → ZrO(g) + O(g); subtracting this volatility contribution from total volatility gives `log p_ZrO2(atm) = −3.57 × 10^4/T + 8.30` | Reaction contribution is not digitized from Figure 2; the species-specific relation is stored with author attribution |

The report supplies no numeric ThO2(g) formation free energy here; it points to earlier effusion work. Likewise, its UO2(g) numerical free-energy values are not printed here. Figures 1–2 and their plotted readings are figure-pass candidates, not extracted data. The report discusses Langmuir's indirect WO3(g) inference in equations (1)–(2), but contains no numeric Langmuir experiment table.

## Migration and condition audit

The isolated migration emits 18 observations with 0 hard issues: 9 point values and 9 unavailable values. Seven unavailable values are functions or fitted relations; the ThO and UO lower limits are two additional typed refusals, rather than exact point observations. Table II temperatures reach identity from the printed `T_K`/`point_conditions` fields. MgO reaches the identity for runs 1–5 through the migrator's `composition_mol` path. Runs 6–8 retain the named MgO/tungsten mixture, but its unspecified ratio leaves quantitative identity composition unknown. The paper prints neither experimental total pressure nor fO2; both axes remain unknown. The pressure column is a calculated WO3 partial pressure and is not promoted to total pressure.

Expected battery-store delta: 18 observations, including nine point values and nine unavailable values. No battery store was regenerated or committed.

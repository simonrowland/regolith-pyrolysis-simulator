# Chatain et al. (2021), NaCrO2 thermodynamics: extraction audit

## Source and scope

The extract uses the MinerU machine text for PDF pages 5–8 and 12–15, then `pdftotext-layout.txt` for the full paper. The PDF renders were used only where MinerU was absent, a table draft had a cell/layout issue, or a locator needed checking. No OCR redo was needed: the born-digital pdftotext layer supplied readable values for every manually checked table, and the page renders settled the row assignments and flagged cells.

The paper has four printed tables. Table 1 reports prior Gibbs-energy fits, Table 2 reports GD-MS impurity content for two samples, Table 3 compares experimental and CASTEP crystal parameters, and Table 4 lists CASTEP harmonic frequencies and degeneracies. It has no numeric Cp(T), H(T)-H(298), or S-S298 data table. The observed Cp and Gibbs-energy series appear only in figures, and the enthalpy-increment readings used by Iyer et al. are not reproduced. No numeric vapour-pressure, raw EMF, or raw KEMS readings are printed. The Table 1 Gibbs-energy fits are transcribed as attributed literature fits.

The sample synthesis and phase identity are retained: commercial Neyco NaCrO2 at 99% purity; an equimolar 0.0322 mol Na2CO3/Cr2O3 synthesis, with the printed purities, heating schedule, and slight neutral Ar flow; NaCrO2 as the hexagonal NaFeO2 prototype, space group R\bar{3}m. The reference cell parameters in Figure 3's caption are attributed to [23]. The oxygen probe was at the furnace exit, but no numeric oxygen pressure is printed; no total pressure or fO2 was invented. The 825 °C, 5 h sealed-ampoule XRD check found only NaCrO2, so the paper does not confirm decomposition at the DTA transition.

## Printed-table inventory and cell audit

Verification route **(a)** means MinerU and pdftotext agree on the stored digits, sign, exponent, and assignment. Route **(b)** means a page render resolved absent/malformed machine text, a flagged cell, or row/column placement. The page and row labels below are the printed locators, not MinerU block order.

### Table 1, PDF page 6

Printed count: 11 body rows, containing 15 equation subrows and 13 temperature spans. The MinerU draft marked all body rows as headers and concatenated some ranges. Its numeric-cell count was therefore zero; that QA result was not treated as cell verification. The page 6 render confirmed every physical row, column, equation, temperature interval, reference, and uncertainty. All Table 1 cells below use route (b).

| Printed row | Reference / subrow | Printed T range (K) | Printed Gibbs-energy equation |
|---:|---|---|---|
| 1 | [3] | 573–773 | `-1132+0.510xT` |
| 2 | [4], I | 700–1100 | `-883.368+0.2108xT` |
| 2 | [4], II | 700–1100 | `-864.707+0.2074xT` |
| 2 | [4], Average | 700–1100 | `-874+0.209xT (±8.4 kJ/mol)` |
| 3 | [5] | 800–980 | `-819.409+0.16508xT` |
| 4 | [6] | 784–1012 | `-869.98+0.18575xT (±1.86 kJ/mol)` |
| 5 | [8], EMF | 657–825 | `-800.847+0.14785xT (±1.35 kJ/mol)` |
| 6 | [7], I | 782–1014 | `-908+0.2317xT (±1.6 kJ/mol)` |
| 6 | [7], II | 809–1040 | `-953.5+0.278xT (±3.3 kJ/mol)` |
| 7 | [9] | 820–1006 | `-856.1+0.1693xT (±2.5 kJ/mol)` |
| 8 | [10] | 573–773 | `-995.79+0.269xT` |
| 9 | [8], KEMS | 825–1025 | `-870.773+0.193171xT` |
| 10 | [12], Knudsen effusion mass loss | 930–1044 | `-847.0+0.1772xT (±3.7 kJ/mol)` |
| 11 | [11], I | 510–1000 | `-(876±18)+(0.194±0.009)xT` |
| 11 | [11], II | 660–890 | `-(909.4±27)+(0.203±0.029)xT` |

The experimental method, equilibrium, and reference assignment for each row was checked against the same render. In particular, Table 1 includes both EMF and Knudsen-cell/effusion-derived fits, but not their raw pressure or voltage data. The fits are `quoted_attributed`, because Chatain et al. quote other papers' results.

### Table 2, PDF page 9; caption on PDF page 10

The two sample panels are both printed on page 9; the caption is carried to the top of page 10. MinerU is absent on pages 9–11, so pdftotext supplied the first machine read and the page 9 render checked all row/value associations. The page 10 render confirms the caption, unit, and note that elements below 0.1 µg g-1 are not listed. Printed count: 22 analytes per sample, 44 measurements total. There are 39 numeric values and five printed less-than values; no limit was replaced with a number. All cells use route (b).

| Element | Experimental NaCrO2 (µg g-1) | Commercial NaCrO2 (µg g-1) |
|---|---:|---:|
| B | 7 | 5 |
| Mg | 25 | 20 |
| Al | 50 | 130 |
| Si | 70 | 250 |
| P | 12 | 7 |
| S | 45 | 20 |
| Ca | 25 | 5 |
| Ti | 0.85 | 1.2 |
| V | <3 | 1.0 |
| Mn | <3 | <3 |
| Fe | 250 | 400 |
| Co | 0.75 | 1.0 |
| Ni | 5.5 | 3.0 |
| Cu | 25 | 10 |
| Zn | 0.8 | 0.4 |
| Sr | 10 | 0.1 |
| Zr | <5 | <5 |
| Mo | 2.1 | 1.1 |
| Sn | 0.5 | 0.1 |
| Ba | 3.1 | 2.1 |
| Ta | 2.0 | 0.2 |
| Pb | 0.2 | 1.0 |

This is direct GD-MS sample characterization, but the exact measured quantity is elemental impurity concentration. `simulator/battery/enums.py::Quantity` and `simulator/battery/migrate.py::QUANTITY_ALIASES` have no exact token for it; `concentration_series` is present in `tools/validate_literature_extracts.py::OBSERVATION_TYPES` but is not mapped by `simulator/battery/migrate.py::TYPE_QUANTITY`. An existing extract, `deguzman-2026-simulant-physicochemical.yaml`, uses `elemental_concentration`, which is not in the closed quantity enum or aliases. The measured table is therefore retained in typed-refused context instead of assigned a near-match quantity.

### Table 3, PDF page 12

Printed count: two rows and 17 numeric cells. MinerU and pdftotext agree on the digits; the page 12 render confirms the row and column assignments. Empty cells remain empty. Route (a) confirms the digits; the render checks layout.

| Printed row | Z | a (Å) | b (Å) | c (Å) | α (°) | β (°) | γ (°) | V0 (Å3) | B0 (GPa) | B'0 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Exp. [18] | 3 | 2.968 | 2.968 | 15.94 | 90 | 90 | 120 | 120.645 | — | — |
| Calc. | — | 3.028 | 3.028 | 15.984 | 90 | 90 | 120 | 127.022 | 121.14 | 3.65 |

The Calc. values are CASTEP DFT/GGA outputs. As their supporting model inputs are not all stored, the extract records them as attributed outputs in typed-refused model context. The table caption distinguishes the computed italicized parameters from experimental values [18].

### Table 4, PDF page 13

Printed count: 14 frequency/degeneracy rows, 28 numeric cells. MinerU and pdftotext agree; the page 13 render confirms all row assignments and digits. Route (a) confirms the digits, with render layout verification.

| Printed row | Frequency (cm-1) | Degeneracy |
|---:|---:|---:|
| 1 | 69.135847 | 4 |
| 2 | 126.073050 | 2 |
| 3 | 153.784178 | 4 |
| 4 | 170.803531 | 2 |
| 5 | 284.958325 | 1 |
| 6 | 305.271195 | 2 |
| 7 | 408.208746 | 4 |
| 8 | 410.547233 | 2 |
| 9 | 415.498866 | 2 |
| 10 | 419.262014 | 4 |
| 11 | 497.073448 | 1 |
| 12 | 526.283746 | 1 |
| 13 | 537.228558 | 2 |
| 14 | 580.187810 | 2 |

The caption identifies these as harmonic Γ-point CASTEP frequencies and degeneracies. `simulator/battery/enums.py::Quantity`, `simulator/battery/migrate.py::QUANTITY_ALIASES`, and `tools/validate_literature_extracts.py::OBSERVATION_TYPES` provide no exact stored frequency or mode-degeneracy quantity, so they remain typed-refused model context.

## Other stored numeric cells and printed equations

Every numeric value retained from prose or a figure caption was read from the machine text and checked against the listed page render. These are source cells, not figure-coordinate readings.

| PDF page and locator | Stored values and verification |
|---|---|
| 2, Abstract | DTA transition temperature `819±4 °C`; page 2 render. |
| 4, literature thermochemistry | Gross reaction enthalpy `-101.4±1.0 kJ mol-1`; standard formation enthalpy `-874.2±7.3 kJ mol-1`; Jain et al. GGA/GGA+U formation enthalpy `-883.1 kJ mol-1`; Van Vuuren reaction enthalpy `-142.0±3.8 kJ mol-1`, measured over `240–490 °C`; page 4 render. Uncertainty confidence is not stated. |
| 4, prior Cp fits | Sreedharan: `27.15+0.1247xT(K)`, reported for `77–327 °C`. Iyer: `-75.364+0.02512xT(K)`, reported for `50–566 °C`. Page 4 render. The citation is [17] in the text on page 4 and the reference list, while one page 14 comparison prints [16]; both source spellings are retained in the extract note. |
| 7, sample preparation | Commercial purity `99%`, chip size `1–10 mm`; precursor charges `0.0322 mol` each; Na2CO3 purity `99.9%`, Cr2O3 purity `99%`; heating rate `7.8 °C min-1`, Ar flow about `7 L h-1`, `850–900 °C`, `5 h`. Page 7 render. |
| 8, Figure 3 caption | Hexagonal R\bar{3}m, `Z=3`, reference cell parameters `a=2.9747±0.0001 Å`, `c=15.9538±0.0003 Å`; attributed to [23]. Page 8 render. The XRD method-error statement on page 7 is `0.5% in volume`; page 7 render. |
| 9, Table 2 prose | Silver-pellet purity `99.999%`; page 9 render. The table caption and its `<0.1 µg g-1` omission rule are on page 10; page 10 render. |
| 10, DSC and DTA protocols | DSC: `30 °C`, `30 min`, `20 °C` steps to about `600 °C`, `1 h` per step, heating/cooling at `10 °C min-1`. DTA: Ar purity `99.9997%`, flow `1 L h-1`, heating rates `5, 7, 10 °C min-1`, extrapolated to rate `0`. Page 10 render. No total pressure or fO2 is printed. |
| 13, theoretical calculation | Cp regression as printed: `97.42+1.46x10-6T^2+0,00792T-1532260T^-2`; decimal comma, signs, and exponents confirmed at 600 dpi. At `298.15 K`, theoretical Cp is `82.9 J K-1 mol-1` and absolute S is printed as `94.5 J K-1 mol-1`. The separate absolute entropy value `94.53 J mol-1 K-1` is printed on page 15. Theoretical entropy formula was transcribed from the page 13 render; its last logarithm is printed with subscript D and is preserved as printed. |
| 14, experimental Cp | Measured domain `25–600 °C`; polynomial `-80.575+0.04736xT(K)-2.9x10-5xT(K)^2-1641963/T(K)^2 (±1.5%)`; average point dispersion `3%` experimental powder and `2%` commercial powder. Page 14 render. No plotted Cp points were digitized. |
| 15, Gibbs calculation | S° at `298.15 K` is `94.53 J mol-1 K-1`; Gross ΔfH° at `298 K` is `-874.2±7.3 kJ mol-1`; equations (6)–(8) are preserved as thermochemical-cycle context. Fitted Gibbs energy is `-876.094+0.191xT (±2%)`; no fit range is stated. Page 15 render. |
| 16, decomposition discussion | A second report of `819±4 °C`; XRD check at `825 °C` for `5 h` in a sealed quartz ampoule; only NaCrO2 was observed. Page 16 render. |

No relative S-S298 column or table is present. The printed entropy equation on page 13 is an absolute theoretical entropy function; the absolute values at 298.15 K are stored separately from any formation-entropy relation. Equation (8) is ΔfS°(T), not a relative S-S298 series. No numerical H(T)-H(298) values or separate enthalpy-increment table is printed.

## Figure-only candidates

No plotted values were digitized. Figure 1 (PDF p. 3) plots literature Gibbs-energy points; Figure 5 (p. 13) shows DFT E(V) points; Figure 6 (p. 14) plots measured Cp; Figure 7 (p. 15) compares Cp data; Figure 8 (p. 16) compares Gibbs-energy curves and points; Figure 9 (p. 17) shows post-treatment XRD. Their numeric point/curve values are figure-read candidates only. The Figure 3 caption's crystal parameters are printed caption text, not values read from plotted coordinates.

All MinerU `<details>` blocks, including chart-derived artificial tables, were ignored. No supplement was found in the corpus or the supporting-files location.

## Schema and method decisions

- Table 1 Gibbs-energy functions use the existing `delta_fG` token, but their coefficients do not form numeric point series. The extract stores the full equations and ranges with `typed_refused_schema_gap`; `simulator/battery/migrate.py::select_declared_source` has no generic coefficient-function source form for these Cp/G polynomials. No equation was evaluated to make new points.
- Authors' Cp fit outputs, DFT outputs with incomplete model inputs, and the zero-rate transition extrapolation are `quoted_attributed` under d-110. Their input Cp points, onset temperatures, or full model inputs are not stored. Prior literature fits and values retain the reference attribution printed by Chatain et al.
- General reaction enthalpy is kept separate from formation enthalpy: `delta_fH` would change the printed quantity. The Table 2 elemental concentrations, Table 3 lattice/cell properties, and Table 4 harmonic frequencies remain typed-refused context because the closed `simulator/battery/enums.py::Quantity` set and `simulator/battery/migrate.py::QUANTITY_ALIASES` have no exact tokens. Table 2's existing-extract comparison is documented above.
- `simulator/battery/enums.py::Quantity.S` is used only for the absolute standard entropy point. No S-S298 data are relabeled as absolute S.
- The transition temperature `T_C` is the measured result, not a pressure or condition. The three DTA onset temperatures are not printed. No unprinted total pressure, numeric fO2, ambient condition, or vacuum condition was added.

## Migration and j01 census

The real migrator was run in isolation on the extract. It produced 25 observations: 5 point values and 20 unavailable typed refusals; there were 0 hard issues, 0 quantity-unknown observations, and 0 phase-unknown observations. The Table 2 sample-analysis contexts remain outside the scored observation store because their quantity has no exact token.

| Migrated row | Composition | Total pressure | fO2 | Identity temperature |
|---|---|---|---|---:|
| Gross ΔfH° at 298 K | N/A | N/A | N/A | 298 K |
| theoretical Cp at 298.15 K | N/A | N/A | N/A | 298.15 K |
| DTA transition temperature | N/A | Unknown; total pressure is not printed | N/A | N/A; result is `point_conditions.temperature_K = 1092.15 K` |

The j01 series census is zero numeric cells and zero unavailable series cells for this source. The paper prints no numeric thermo series; the scalar values and unavailable equation records do not contribute to the series census. No battery-store regeneration was committed. Expected regenerated delta: +25 observations (20 unavailable, 5 points).

## Tool comparison and focused test

`tools/mineru_table_qa.py` found 17 Table 3 numeric cells with 1.000 agreement and no flagged cells; Table 1 and Table 4 were misclassified as header rows with zero numeric candidates, so renders verified them manually. `tools/mineru_table_draft.py compare` is committed as `chatain-2021-nacro2-thermodynamics.compare.csv`. It finds no draft candidates for the two page 4 scalar formation-enthalpy points because they are prose values, and it marks the Table 2 page 9 row locators unverifiable because the Table 2 caption is on page 10. The rendered page 9 and page 10 checks establish those locators; no context-table mismatch is hidden by the compare result.

The focused test pins the Table 1 row 6 II equation, both Table 2 Fe values, Table 3 `V0=127.022` and `B0=121.14`, Table 4's final frequency, the Figure 3 caption cell parameter, and printed row counts. After isolated migration it also confirms migrated scalar values, temperatures, and absent pressure/fO2 conditions. A mutation changing Table 3 `V0` from `127.022` to `127.023` fails the independent fidelity-sample check.

Verification: the focused extraction test passed (2 tests), `tests/test_yaml_flow_scalar_commas.py` passed (17 tests), and all 872 collected cases in `tests/test_literature_extracts.py` passed in bounded `-k` slices (255 other cases and 617 per-extract fidelity samples). The full-file invocation exceeded the 300-second cap, so the suite was partitioned without omitting cases.

# Shornikov (2022) extraction audit

## Source reads and locator review

The extraction uses the born-digital `pdftotext-layout.txt` as the numeric
transcription source. MinerU supplied two content lists but no table blocks;
the seat's table conversion and QA tools reported zero tables, zero flagged
tables, and zero scan tables. The page renders therefore resolve all table
row/column assignments, the printed table labels, prose locators, equation
symbols, and figure captions. Absolute PDF pages 3–11 were rendered and checked
against the published page numbers 352–360. No plotted coordinates were
digitized.

The compare tool reports 0 matched, 2 no-match, 0 mismatch, 0 presence-only,
0 needs-mapping, 0 locator-error, and 18 unverifiable records (20 total). The
two no-match records are the prose melting points, which do not occur in a
MinerU table draft. The 18 unverifiable records are Table 1 and Table 2
locators whose printed captions are absent from MinerU because it supplied no
tables. The renders verified these locators as printed Table 1 on PDF page 4
(published page 353) and printed Table 2 on PDF page 6 (published page 355).
These compare statuses do not indicate unresolved source cells.

### Table 1 cell audit

Table 1 contains 19 printed rows. The table below lists every populated
composition and temperature cell. Each listed cell was read from the exact
born-digital text and assigned to its row and column against the render of PDF
page 4. This covers 79 populated composition cells and 19 temperature cells:
98 numeric cells containing 115 endpoints. Six composition cells contain
ranges and eleven temperature cells contain ranges. The 35 composition dashes
are absence marks, not zeros. All 19 row assignments and all reference and
apparatus columns were checked on the same render.

| Row | CaO | MgO | FeO | Al2O3 | TiO2 | SiO2 | T, K | Cell audit |
|---:|---|---|---|---|---|---|---|---|
| 1 | 31.6–49.2 | – | – | – | 50.8–68.4 | – | 2278 | (b), PDF p. 4, rendered row/column assignment |
| 2 | 50.00 | – | – | – | 50.00 | – | 1791–2398 | (b), PDF p. 4, rendered row/column assignment |
| 3 | 6.8–62.1 | – | – | – | 37.9–93.2 | – | 2241–2441 | (b), PDF p. 4, rendered row/column assignment |
| 4 | – | 22.0–79.5 | – | – | – | 21.5–78.0 | 1973 | (b), PDF p. 4, rendered row/column assignment |
| 5 | – | 66.67 | – | – | 33.33 | – | 1873–2323 | (b), PDF p. 4, rendered row/column assignment |
| 6 | 2.58 | 33.90 | 1.78 | 27.95 | – | 33.79 | 1973–2173 | (b), PDF p. 4, rendered row/column assignment |
| 7 | 19.44 | 7.26 | 18.72 | 3.98 | 0.25 | 50.35 | 1574–2502 | (b), PDF p. 4, rendered row/column assignment |
| 8 | 1.95 | 36.50 | 1.47 | 20.71 | 0.07 | 39.31 | 1438–2378 | (b), PDF p. 4, rendered row/column assignment |
| 9 | 6.51 | 47.47 | 2.35 | 5.15 | – | 38.52 | 1580–2156 | (b), PDF p. 4, rendered row/column assignment |
| 10 | 15.00 | 10.03 | 12.22 | 6.08 | – | 56.67 | 1749–2063 | (b), PDF p. 4, rendered row/column assignment |
| 11 | 2.20 | 34.82 | 1.76 | 10.31 | 0.07 | 50.83 | 1406–2147 | (b), PDF p. 4, rendered row/column assignment |
| 12 | 3.11 | 33.16 | 1.77 | 29.60 | 0.07 | 32.30 | 1500–2237 | (b), PDF p. 4, rendered row/column assignment |
| 13 | 2.08 | 35.15 | 29.18 | 1.38 | 0.08 | 32.13 | 1673–2273 | (b), PDF p. 4, rendered row/column assignment |
| 14 | 26.76 | 17.05 | – | 11.41 | – | 44.78 | 2173 | (b), PDF p. 4, rendered row/column assignment |
| 15 | 2.03 | 63.49 | – | 1.40 | – | 33.08 | 2173 | (b), PDF p. 4, rendered row/column assignment |
| 16 | 12.94 | 44.39 | – | 8.06 | – | 34.61 | 2173 | (b), PDF p. 4, rendered row/column assignment |
| 17 | 8.68 | 45.52 | – | 7.35 | – | 38.45 | 2173 | (b), PDF p. 4, rendered row/column assignment |
| 18 | 23.41 | 23.88 | – | 16.44 | – | 36.27 | 2173 | (b), PDF p. 4, rendered row/column assignment |
| 19 | 14.43 | 25.60 | – | 23.83 | – | 36.14 | 2173 | (b), PDF p. 4, rendered row/column assignment |

The approach markers `Iᵃ` and `IIᵇ` were checked at the footnote and retained
on their cells. Table 1 row 5 prints 1873–2323 K; separate prose on PDF page 3
states 2023–2323 K for the Hashimoto and Davis experiments. Both source
statements remain unchanged.

### Table 2 cell audit

Table 2 has 16 reaction rows. All 21 printed K values and all 11 dash cells
were assigned to their reaction number and temperature column against the
render of PDF page 6. The two printed temperature headers are 2173 and 2278 K.

| Reaction row | K at 2173 K | K at 2278 K | Cell audit |
|---:|---:|---:|---|
| 10 | – | 7.267 × 10⁻⁹ | (b), PDF p. 6, rendered row/column assignment |
| 11 | – | 2.372 × 10⁻⁶ | (b), PDF p. 6, rendered row/column assignment |
| 12 | – | 3.573 × 10⁻⁴ | (b), PDF p. 6, rendered row/column assignment |
| 13 | – | 1.917 × 10⁻⁷ | (b), PDF p. 6, rendered row/column assignment |
| 14 | – | 5.351 × 10⁻¹⁰ | (b), PDF p. 6, rendered row/column assignment |
| 15 | – | 3.197 × 10⁻³ | (b), PDF p. 6, rendered row/column assignment |
| 16 | – | 2.092 × 10⁻⁸ | (b), PDF p. 6, rendered row/column assignment |
| 17 | 5.102 × 10⁻⁶ | 1.872 × 10⁻⁵ | (b), PDF p. 6, rendered row/column assignment |
| 18 | 3.955 × 10⁻¹ | 4.920 × 10⁻¹ | (b), PDF p. 6, rendered row/column assignment |
| 19 | 1.848 × 10⁷ | 4.637 × 10⁶ | (b), PDF p. 6, rendered row/column assignment |
| 20 | 2.605 × 10¹³ | 1.436 × 10¹² | (b), PDF p. 6, rendered row/column assignment |
| 21 | 1.234 × 10¹⁶ | 2.895 × 10¹⁴ | (b), PDF p. 6, rendered row/column assignment |
| 22 | 1.657 × 10⁻⁴ | – | (b), PDF p. 6, rendered row/column assignment |
| 23 | 1.705 × 10⁻⁴ | – | (b), PDF p. 6, rendered row/column assignment |
| 24 | 3.038 × 10⁻¹³ | – | (b), PDF p. 6, rendered row/column assignment |
| 25 | 5.345 × 10⁻¹ | – | (b), PDF p. 6, rendered row/column assignment |

The reaction rows identify the printed gas species and condensed phases in
the extract. Since the table reports linear reaction constants and
`simulator/battery/enums.py::Quantity.LOG10_K_REACTION` stores log10 K, the
21 values are retained as typed refusal context without conversion.

### Prose numeric-cell audit

| PDF page | Printed numeric cells and statements | Audit basis |
|---:|---|---|
| 3 | Experimental vacuum pressure “no more than 10⁻⁹ atm”; this general statement is not assigned to individual experiments. | (b), rendered prose and source locator |
| 4 | Complex gas species: no more than 0.1 mol. %; at 2300 K, CaO 0.63, MgO 5.10, FeO 9.05, Al2O3 10⁻⁵, SiO2 5.93, TiO2 83.38 mol. %. | (b), rendered prose and source locator |
| 4 | Equation (5), including φ and p°; oxide evaporation coefficients close to unity. | (b), rendered equation symbols and paragraph locator |
| 5 | Equations (6)–(9); at 2300 K dominant vapor species Ca 54.81, Mg 58.73, Fe 57.41, Al 29.72, SiO 59.80, TiO2 83.38 mol. %. | (b), rendered equations, values, and locator |
| 5 | Perovskite component vapor pressures CaO 1.514 × 10⁻⁵ and TiO2 2.702 × 10⁻⁵ atm at 2278 K; neutral residual TiO2 values 41.25 and 44.83 mol. %; melting point 2241 ± 10 K. | (b), rendered paragraph and uncertainty glyph |
| 6 | Reducing perovskite residual TiO2 values 55.18 and 68.30 mol. %; residual melt weight loss exceeding 80 wt. %; [WO3] condensate 0.06–0.6 wt. %; forsterite melting point 2171 ± 11 K. | (b), rendered paragraph, species labels, and uncertainty glyph |
| 7 | Forsterite component vapor pressures MgO 5.831 × 10⁻⁵ and SiO2 2.771 × 10⁻⁴ atm at 2173 K; residual MgO values 65.56 and 67.20 mol. %. | (b), rendered paragraph and source locator |
| 8 | Forsterite initial-to-final composition difference does not exceed 0.6 mol. % at 2173 K. | (b), rendered paragraph and source locator |
| 10 | Figure 13 describes a constant O/O2 ratio through 40% residual-melt weight loss; the printed sentence says this ration is sharply increased, indicating evaporation of refractory calcium aluminates. Figure temperature is 2173 K. | (b), rendered prose, caption, and series labels |

The figure captions and numbered series for Figures 1–14 were checked against
the corresponding rendered pages. Figure-only curves for volatility,
activity, melt composition, gas composition, and oxygen-pressure ratios remain
undigitized. Figure 13's legend has nine series, including CAI FUN1 (8) and
CAI FUN2 (9). No numeric Na or K vapor value is printed.

## Extraction and schema decisions

- Table 1's 19 rows, all six oxide columns, temperatures, approach markers,
  apparatus, result labels, and citations remain in context exactly as
  printed. Dashes remain absence marks. No range is converted to a point.
- Table 2's 16 rows preserve all 21 printed linear K cells and 11 absence
  marks. The linear form has no exact observation token: the available
  `reaction_equilibrium_constant` alias resolves to
  `simulator/battery/enums.py::Quantity.LOG10_K_REACTION`. The row remains a
  typed refusal. Checked closed sets are
  `tools/validate_literature_extracts.py::OBSERVATION_TYPES`,
  `simulator/battery/migrate.py::QUANTITY_ALIASES`,
  `simulator/battery/migrate.py::TYPE_QUANTITY`,
  `simulator/battery/enums.py::PHASE_BASIS_TOKENS`, and
  `simulator/battery/enums.py::PhaseRole`.
- The four component vapor pressures retain the printed CaO, TiO2, MgO, and
  SiO2 component names, atm units, temperatures, and attributions. The source
  does not name an individual gas species or provide a full multicomponent
  melt composition for these inputs, so they remain context rather than
  vapor-species observations.
- The calculated pure-oxide gas amounts, dominant vapor-species amounts,
  model composition endpoints, and the quoted tungsten-oxide condensate range
  remain in context with their printed units, conditions, and method classes.
  No activities, partial pressures, vapor rates, or ion intensities are
  digitized from plots. The equations are stored as printed; φ and the degree
  symbols in p° and x_d° were checked against the renders.
- No carrier gas, numeric experimental fO2, or gas-mixture ratio is reported.
  Figure 13 plots a calculated pO/pO2 ratio, which remains figure-only and
  undigitized. No numeric standard pressure or gas standard-state convention
  is given. The qualitative neutral/reducing descriptions and general vacuum
  statement are not promoted to row-level conditions. The model says its
  activity of a pure component equals the mole fraction of molecules not
  included in the compound and uses standard Gibbs energies in Gibbs
  total-energy minimization.
- Reported values calculated by this paper without their numerical inputs are
  `author_derived`; values attributed to the cited prior studies are
  `quoted_attributed`. The printed melting-point widths are stored separately
  from their central values; the source gives no confidence level.

## Migration and verification

The real migrator was run on this extract in isolation. It produced 3
observations, 0 hard issues, and 0 unavailable values. All 3 migrated values
are numeric (two transition points and one mass-loss bound); no observation
has an unknown quantity or phase. The migration test also checks that the
unprinted total pressure and numeric fO2 remain absent.

| Observation | Composition identity | Total pressure identity | fO2 identity | Temperature identity | Located point condition |
|---|---|---|---|---|---|
| Perovskite melting point | not applicable | unknown | not applicable | not applicable | 2241 K, PDF p. 5 |
| Forsterite melting point | not applicable | unknown | not applicable | not applicable | 2171 K, PDF p. 6 |
| Perovskite residue mass-loss bound | unknown | unknown | unknown | 2278 K | 2278 K, PDF p. 7; composition unknown, PDF p. 6 |

The focused extract test passed and pins every migrated value and its
conditions, the printed Table 1 and Table 2 rows/cells, and the two distinct
table locators after isolated migration. Interior mutations to a Table 1
composition cell, a Table 2 K cell, the forsterite temperature condition, and
the migrated mass-loss bound all fail their assertions. It confirms 19 Table
1 rows, 16 Table 2 rows, 3 migrated numeric values, 0 migrated unavailable
values, and no quantity-unknown or phase-unknown observations.

Local verification: `tests/test_yaml_flow_scalar_commas.py` passed (17);
the j01 census selector in `tests/battery/test_migrate.py` passed (1); and
the `test_repo_extracts_validate_green` selector in
`tests/test_literature_extracts.py` passed. The Shornikov-focused selector in
that file passed (74), including the registered fidelity sample. The full
`tests/test_literature_extracts.py` run reached 159 passing tests before it
was interrupted at 255.50 seconds to stay within the per-call limit. The
separate repository count/fidelity-coverage selector also exceeded the
300-second local cap before reporting a result; those remaining tests are
UNVERIFIED-local.

No store regeneration was run. Expected store delta: 3 observations and zero
generated store files.

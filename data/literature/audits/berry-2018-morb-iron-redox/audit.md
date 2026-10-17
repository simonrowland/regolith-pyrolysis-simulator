# Berry et al. (2018) extraction audit

Source: Berry, Stewart, O'Neill, Mallmann & Mosselmans (2018), *A re-assessment of the oxidation state of iron in MORB glasses*, EPSL 483, 114–123, doi:10.1016/j.epsl.2017.11.032.

## Inputs and row reconciliation

- Primary reads: corpus `pdftotext-layout.txt` and MinerU table HTML/content-list blocks. The PDF was used only to render PDF pages 4 and 6 (printed pages 117 and 119) for the layout check and flagged cells. The pages are born-digital; pdftotext characters are exact.
- MinerU Table 1 and Table 2 layouts were each checked once against their full page render. Table 1 has 9 printed body rows; all are retained as context, not redox observations. Table 2 has 13 printed body rows; all are retained in the four separate Fe3+/SumFe series. The Table 2 row heading and caption assign every row to iron.
- Table 2 row 1 is the 4.8 log fO2 / QFM+11.2 sample prepared at 1.0 GPa in a sealed Pt capsule. Rows 2–13 are the 1-atm gas-mixing furnace samples. This pressure split is preserved per row.
- Row-level Table 2 conditions include T_C=1400, absolute log fO2, delta-QFM, total pressure, and the nominal starting glass composition. The method states that the furnace gas was CO/CO2 or O2/CO2 but does not print per-row gas ratios. Quenched glass is the measured phase. Quench status is `not_stated` for every glass row: the source says the melts were quenched in water (printed page 115), but does not state whether Fe valence was preserved or altered.
- The Table 2 mean analysed glass composition (n=50) and XANES centroid/edge energies are retained in characterization context. The mean composition is not normalized and missing oxides are not inferred. Table 1's blank grouped condition cells remain null; conditions are not copied down to the D2 or Average rows.

## Experimental prose and mean-composition audit

| PDF page / printed page | Stored numeric cells and evidence |
|---|---|
| 2 / 115, Experimental | (a) nominal oxide recipe SiO2 52.0, Al2O3 16.1, CaO 12.4, FeO 10.0, MgO 8.2, TiO2 1.3 wt.%, and equilibration at 1400 °C: MinerU prose and pdftotext agree; the render confirms column/oxide assignment. (b) temperature error ±2 °C, furnace pressure 1 atm, furnace quench after ~24 h, and the separate sample at 1.0 GPa / 1400 °C for ~4 h: the signs, units, and row context were confirmed against the rendered page. The 1 atm and 1.0 GPa conditions are stored in Pa using the existing migrator path; the printed units are retained in context. |
| 3 / 116, Results | (b) mean-glass composition n=50: SiO2 52.10(40), Al2O3 15.87(22), CaO 12.33(17), FeOTotal 9.40(22), MgO 7.77(9), TiO2 1.27(3). The rendered page confirms the digits, oxide assignment, and one-standard-deviation note; MinerU and pdftotext read the same tokens. Parenthesized uncertainties are retained at last-digit precision. |

## Full-cell audit

`(a)` means MinerU and pdftotext read the same digits/sign and the row/column assignment was confirmed by the one-time page-layout render. `(b)` means the cell was checked against the rendered page because it contains a minus sign, parenthesized uncertainty or fixed endpoint, or is part of the printed model footnote. For a parenthesized last-digit uncertainty, the displayed value and parsed uncertainty both inherit the cell's proof mark. Fixed `(1)` and `(0)` endpoints are values, not uncertainties.

### Table 1 — PDF page 4, printed page 117

| Printed body row | Numeric cells and evidence |
|---|---|
| 1, D1 | (a) log fO2 4.8; delta-QFM 11.2; Fraction 77.9%; δ 0.288; σ(δ) 0.021; Δ 1.244; σ(Δ) 0.459. (b) θ −1.5°. |
| 2, D2 | (a) Fraction 22.1%; δ 0.311; σ(δ) 0.014; Δ 1.882; σ(Δ) 0.534. |
| 3, Average | (b) δ 0.293(2); σ(δ) 0.019(5); Δ 1.39(5); σ(Δ) 0.48(3). |
| 4, Fit A D1 | (b) log fO2 −11.0; delta-QFM −4.6. (a) Fraction 89.4%; δ 1.077; σ(δ) 0.159; Δ 1.897; σ(Δ) 0.414; θ 5°. |
| 5, Fit A D2 | (a) Fraction 10.6%; δ 0.650; σ(δ) 0.031; Δ 1.445; σ(Δ) 0.354. |
| 6, Fit A Average | (b) δ 1.03(2); σ(δ) 0.15(1); Δ 1.85(4); σ(Δ) 0.41(1). |
| 7, Fit B D1 | (a) Fraction 89.5%; δ 1.047; σ(δ) 0.100; Δ 1.955; σ(Δ) 0.459; θ 5°. |
| 8, Fit B D2 | (a) Fraction 10.5%; δ 0.888; σ(δ) 0.030; Δ 0.956; σ(Δ) 0.349. |
| 9, Fit B Average | (b) δ 1.03(2); σ(δ) 0.10(1); Δ 1.85(4); σ(Δ) 0.45(1). |

### Table 2 — PDF page 6, printed page 119

| Printed body row | Numeric cells and evidence |
|---|---|
| 1 | (b) log fO2 4.8(5), delta-QFM 11.2(5), 295 K `(1)`, 77.5 K `(1)`, model 0.982(8). (a) centroid 7114.566 eV, edge 7126.529 eV, fit 0.983. |
| 2 | (b) log fO2 0.00(1), delta-QFM 6.38(1), model 0.775(10). (a) centroid 7114.414, edge 7125.336, 295 K 0.773, 77.5 K 0.769, fit 0.783. |
| 3 | (b) log fO2 −1.00(1), delta-QFM 5.40(1), model 0.662(10). (a) centroid 7114.295, edge 7124.603, 295 K 0.661, fit 0.670. 77.5 K is a printed dash. |
| 4 | (b) log fO2 −2.00(2), delta-QFM 4.40(2), model 0.524(10). (a) centroid 7114.103, edge 7123.971, 295 K 0.537, 77.5 K 0.537, fit 0.533. |
| 5 | (b) log fO2 −3.07(10), delta-QFM 3.32(10), model 0.404(20). (a) centroid 7113.862, edge 7123.347, 295 K 0.414, fit 0.382. 77.5 K is a printed dash. |
| 6 | (b) log fO2 −4.00(4), delta-QFM 2.39(4), model 0.251(7). (a) centroid 7113.509, 295 K 0.250, 77.5 K 0.241, fit 0.266. Edge is a printed dash. |
| 7 | (b) log fO2 −5.00(2), delta-QFM 1.39(2), model 0.162(6). (a) centroid 7113.319, edge 7121.950, 295 K 0.167, fit 0.169. 77.5 K is a printed dash. |
| 8 | (b) log fO2 −6.00(2), delta-QFM .39(2), model 0.098(5). (a) centroid 7113.170, edge 7121.581, 295 K 0.103, 77.5 K 0.077, fit 0.103. |
| 9 | (b) log fO2 −7.00(3), delta-QFM −.61(3), model 0.059(4). (a) centroid 7113.101, edge 7121.355, 295 K 0.039, 77.5 K 0.041, fit 0.060. |
| 10 | (b) log fO2 −8.00(2), delta-QFM −1.61(2), model 0.034(3). (a) centroid 7113.040, edge 7121.185, 295 K 0.024, fit 0.035. 77.5 K is a printed dash. |
| 11 | (b) log fO2 −9.00(2), delta-QFM −2.61(2), model 0.019(2). (a) centroid 7112.992, edge 7121.094, 295 K 0.017, fit 0.020. 77.5 K is a printed dash. |
| 12 | (b) log fO2 −10.00(3), delta-QFM −3.61(3), 295 K `(0)`, model 0.011(1). (a) centroid 7112.977, edge 7121.033, fit 0.011. 77.5 K is a printed dash. |
| 13 | (b) log fO2 −11.00(2), delta-QFM −4.61(2), 295 K `(0)`, 77.5 K `(0)`, model 0.006(1). (a) centroid 7112.958, edge 7121.036, fit 0.006. |

The caption gives ±0.01 for both direct Mössbauer columns, ±0.022 eV for centroid, and ±0.05 eV for edge. The Table 2 `model^a` footnote was also checked on the rendered page: (b) centroid formula coefficients 7112.935, 2.687, −1.000 and edge formula coefficients 7120.998, 5.613; the printed exponents and signs are retained. The paper says coefficient uncertainties are highly correlated and reports errors for the values rather than individual terms.

## MinerU compare and render resolutions

The converter comparison is committed in [`mineru-compare.csv`](mineru-compare.csv). Its full CLI output repeats every numeric candidate on the page for each point; the committed CSV keeps the converter's status and selected cell coordinates/text. The run checked 46 numeric `points[].value` cells: 42 matched, 4 mismatched, 0 no-match. The converter covers the four Table 2 valence series; Table 1 and the XANES context rows are audited cell-by-cell above.

The four mismatches are fixed endpoint cells the converter does not parse as numbers: Table 2 row 1 `(1)` at 295 K and 77.5 K, and row 13 `(0)` at 295 K and 77.5 K. The page 6 render confirms all four values and their column assignments. The converter also reports the 295 K row 12 `(0)` as matched to draft row 3, column 1 (`0.00(1)`), because its comparison is numeric-only and ignores cell coordinates. The page 6 render confirms the stored `(0)` belongs to printed Table 2 row 12, 295 K column; the returned match is spurious and is not treated as confirmation.

## Migration, valence identity, and store delta

- The real migrator on this extract emitted 52 observations, with 0 hard issues and 0 registry issues. All 52 are admitted; 46 have point values and 6 are unavailable because the 77.5 K column prints dashes at those row coordinates. The four 13-row columns remain separate: 295 K and 77.5 K measured-direct, `fit` measured-reduced, and `model^a` model-derived.
- Every emitted row has `temperature_K=1673.15`, composition in `Identity`, and located `T_C`, absolute `fO2_log`, `delta_QFM`, total pressure, and composition in `point_conditions`. The high-pressure row carries 1,000,000,000 Pa; the other rows carry 101325 Pa.
- All four series migrate as `VALENCE_RATIO` with Fe³⁺ numerator state 3, total Fe denominator, and printed form `fraction`.
- Identity-axis check from the isolated migration: Table 2 rows 1, 2, and 3 (`log fO2` 4.8 / 0.00 / -1.00; pressure 1 GPa / 1 atm / 1 atm) each have the required axes `composition`, `fO2_Pa`, `temperature_K`, `total_pressure_Pa`, and `valence_basis`. All are populated; `temperature_K=1673.15`; `fO2_Pa` is 6309573444.801932494343601366 / 100000 / 10000 Pa; total pressure is 1000000000 / 101325 / 101325 Pa. The existing migrator applies its 1 bar reference to the retained absolute log values; the extract keeps `fO2_log` as printed.
- The measurements are of quenched glass, so every migrated observation has phase `glass`. The paper says the melts were water-quenched after about 24 h (printed p. 115), but does not state that Fe valence was preserved or altered; each point therefore has typed `quench_state: not_stated` located to p. 115.
- No battery store was regenerated or committed. Expected store delta: 52 admitted observations, 46 point-valued and 6 unavailable (the printed 77.5 K dashes).

The focused regression test pins rendered Table 2 values and row counts, checks the valence basis, all identity and point-condition axes, the glass phase and located quench state, and verifies that mutating a pinned value makes the fidelity check fail. `tests/test_yaml_flow_scalar_commas.py` was run as required.

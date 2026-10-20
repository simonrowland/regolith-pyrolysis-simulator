# Shornikov (2005), K2O–GeO2: extraction audit

## Duplicate verdict

Not a duplicate. Printed title: Vaporization Behavior and Thermodynamic Properties of K2O–GeO2 Melts Studied by Mass Spectrometry, Inorganic Materials 41(12), 1345–1356 (2005). pdfinfo Title is `InorgMat12_05ShornikovLO`. No extract on this base has this title. The 2014 silicate-model extract only cites the Russian pagination of this paper; it does not contain these tables.

## Table 1 (published page 1348, PDF page 4)

The 600 dpi render matches the text layer on every digit used below, including the dashes that are absent and the footnotes * (recommended) and ** (extrapolation). No cell was hand-corrected.

Scored partial pressures are the rows that print a p(1300 K). Segments that print only a and b are context (`vapor_pressure_fit`): no midpoint pressure is invented.

K4 rows are context (`equilibrium_constant_schema_gap`). The schema has no heterogeneous Kp quantity. The printed 1300 K cell is K, not log10 K. The header unit is atm; the reaction [GeO2] = (GeO) + 1/2(O2) would make K atm^(3/2) if the standard state is 1 atm. The printed number is not converted.

Phase:

- [30] rows: the page says the Kazenas pressures are for solid GeO2. That is the condensed reservoir, kept in phase_as_printed. The scored species is the vapor, so the observation phase is g.
- [29]: the page says "corresponding to liquid GeO". That is not rewritten as liquid GeO2. The scored species is the vapor, phase g.
- [28] and this-work rows: the table does not state the condensed phase. The 1308 K and 1389 K breaks are unlabelled. The scored species is still the vapor, phase g.

This-work rows with a cell material are `derived_least_squares`. The cell "–" rows are the evaluation from [23, 24], `model_derived`. Neither publishes the parent points. No `derived_from` link is invented (b-767).

Alpha, same page: α(GeO) = (2.27±0.43)×10^−1 and α(O2) = (1.98±0.85)×10^−1, from the [28] versus [30] discrepancy at Sv/sor about 3000. `model_derived`. The arithmetic is not shown, so there is no parent link. No single temperature is printed for alpha.

## Table 2 (published page 1354, PDF page 10)

The 600 dpi render matches the text layer, including the K2O·4SiO2 dashes (four cells, not stored as zero) and the 1081* decomposition footnote.

Melt ΔfH equals solid ΔfH + ΔmH for every non-dash row (checked to the printed hundredth). Those five melt enthalpies are `model_derived` with `derived_from` the solid enthalpy and the fusion enthalpy of the same row.

Melt and solid ΔfS are context. Formation entropy has no quantity. The melt ΔfS arithmetic also reproduces (solid ΔfS + ΔmS) and that fact is recorded, but the row stays unscored.

The caption does not say formation from the elements or from the oxides. That sentence is the standard_state text. It is not an activity reference.

Published page 1353 quotes Barin et al. [50] fusion enthalpies of 50.21 kJ/mol at 1249 K (metasilicate) and 41.00 kJ/mol at 1318 K (disilicate), and says those [50] functions have low accuracy. They are context. The adopted numbers are the Table 2 calorimetry values 8.79 and 10.67. The same page's "about 35 kJ/mol" and "about 15 and 25 kJ/mol" are differences between curves, not a printed enthalpy at one composition, so they were not stored.

## Fig. 2 panel (a), series 4, this work at 1400 K

Seven open stars, one at each printed melt composition except 100 mol% GeO2 (no ink at the right-hand frame).

Tick calibration is in the locator note. Residual rms on the nine labelled y ticks is 0.0012 in log10 a. The x ticks are linear with residual about 0. The stored composition is the printed sample mol%, because the star's x-read falls within 0.3 mol% of that sample.

| mol% GeO2 | read 1 log10 a | read 2 log10 a | stored activity | uncertainty |
| --- | --- | --- | --- | --- |
| 54.80 | −9.026 | −9.007 | 9.627200e-10 | ±0.05 in log10 a |
| 59.59 | −9.518 | −9.514 | 3.047895e-10 | ±0.05 |
| 62.41 | −9.819 | −9.818 | 1.518798e-10 | ±0.05 |
| 66.40 | −10.237 | −10.228 | 5.854637e-11 | ±0.05 |
| 71.83 | −10.851 | −10.823 | 1.455459e-11 | ±0.05 |
| 82.93 | −12.341 | −12.322 | 4.661224e-13 | ±0.05 |
| 91.00 | −13.730 | −13.742 | 1.836538e-14 | ±0.05 |

A crosshair on the 600 dpi crop sits on the star, not on the square or the triangle. Series 1 triangles ([1], emf) and series 3 squares ([19], 1300 K) are replots and were not read.

## Other figures, refused

- Fig. 2 panels (b)–(e): series 4 stars are on the page. Crosshair checks put the automatic centre on the guide curve or on a neighbouring symbol. No value was stored.
- Fig. 3: Arrhenius plot of the Table 1 p(GeO) sets. The admitted numbers are the table. Markers were not read again.
- Fig. 4: the axis label prints `log p(i, 1033 K)` and the caption prints 1300 K. Both are on the 600 dpi page. The temperature is not unique, so every marker on the three series is refused. Compositions run from about 55 to 100 mol% GeO2 with filled circles (p(GeO)), squares (p(K)) and triangles (p(O2)).
- Fig. 5, published page 1352: nine panels at 1473 K for four glass-forming systems. Series 4 is this work. Markers were not separated from the curves. Not read.

## Standard state for the activities

Equations (8) and (9) on published page 1350 ratio the measured partial pressures to p^0. The paper does not say whether p^0 is the pure solid or the pure liquid. The structured standard_state has phase tag unknown, inferred false. Expected migration phase: unknown.

## Render checks

600 dpi: PDF pages 3 (Fig. 2), 4 (Table 1), 5 (Fig. 3), 6 (Fig. 4), 8 (Fig. 5), 10 (Table 2). Table digits were not changed.

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

## Fig. 2 panels (b)–(e), fix round

Legend on the 600 dpi page, checked before any glyph was refused. Panel (b) and (c): filled triangle = 1 (emf [1]), open square = 3 (KEMS 1300 K [19]), open star = 4 (this work, 1400 K). Panel (d): open triangle = 2 (calorimetry 1223 K [9]), open star = 4. Panel (e): filled triangle = 1, open star = 4.

Tick lines were measured on the tick stub, not on the numeral. Panel (a) recovers the ticks already used for the seven admitted stars (y −9 at 574.5 through −17 at 1565.5; x 50 at 930.5 through 90 at 2028.5). Panel (d) and (b) x ticks: 50 at 2972.5, 60 at 3246.5, 70 at 3521.5, 80 at 3795.5, 90 at 4070.0; fit x = 27.440·mol% + 1600.400, rms 0.200 px. Panel (d) y: 0 at 1808.5, −30 at 2005.5, −60 at 2202.5, −90 at 2399.5, −120 at 2597.5, −150 at 2794.5; y = −6.574286·ΔfH + 1808.262, rms 0.264 px. Panel (b) labelled y ticks only: 0 at 515.5, −1 at 936.5, −2 at 1358.0. The half-steps at 726.5, 1147.5 and 1568.5 are unlabelled and were not used. Least squares y = −421.25·log10(a) + 515.4167, residual rms 0.12 px. The earlier assignment of −1 to 726.5 and −2 to 936.5 is withdrawn. Panel (c) y: 0, −3, −6, −9, −12, −15 at 1808.5, 2014.5, 2220.5, 2426.5, 2632.5, 2838.5; the fit is exact. Panel (c) does not repeat the mol% numerals; its x ticks sit on the same page-x as the labelled ticks of panels (a) and (e). Panel (e) y: 10, 0, −10, −20, −30, −40 at 3151.5, 3326.5, 3502.5, 3678.0, 3853.5, 4029.0; rms 0.171 px.

A vertical window at each printed melt composition, then a 12 px disk centroid. The same window on panel (a) recovers the admitted star to within about 5–15 px, so a candidate was admitted only after the 600 dpi crosshair sat on the open star and the two centroids agreed. Stored value is the mean. Half-width covers a 6 px centre error: ±1.5 kJ/mol, ±0.15 in ΔfG/(RT), ±0.5 J/(mol K).

| panel | mol% GeO2 | decision | value | centre (read 1, page px) |
| --- | --- | --- | --- | --- |
| (d) series 4 | 54.80 | read, context | −125.59 kJ/mol | (3105.4, 2634.3) |
| (d) series 4 | 59.59 | read, context | −116.92 kJ/mol | (3236.9, 2577.3) |
| (d) series 4 | 62.41 | read, context | −110.55 kJ/mol | (3313.0, 2535.6) |
| (d) series 4 | 66.40 | read, context | −101.00 kJ/mol | (3420.9, 2472.5) |
| (d) series 4 | 71.83 | read, context | −88.43 kJ/mol | (3568.7, 2389.9) |
| (d) series 4 | 82.93 | read, context | −57.13 kJ/mol | (3874.8, 2183.7) |
| (d) series 4 | 91.00 | read, context | −33.25 kJ/mol | (4094.7, 2027.2) |
| (c) series 4 | 54.80 | read, context | −12.392 | (1063.3, 2659.9) |
| (c) series 4 | 59.59 | read, context | −11.496 | (1196.4, 2597.5) |
| (c) series 4 | 62.41 | read, context | −10.721 ±0.15 | (1265.2, 2544.7) |
| (c) series 4 | 66.40 | read, context | −9.861 ±0.15 | (1375.0, 2485.7) |
| (c) series 4 | 71.83 | read, context | −8.588 ±0.15 | (1525.4, 2398.2) |
| (c) series 4 | 82.93 | read, context | −5.683 ±0.15 | (1838.7, 2198.8) |
| (c) series 4 | 91.00 | read, context | −3.187 ±0.15 | (2064.9, 2027.4) |
| (e) series 4 | 71.83 | read, context | 9.496 J/(mol K) | (1528.9, 3160.2) |
| (e) series 4 | 82.93 | read, context | 7.810 J/(mol K) | (1834.0, 3189.4) |
| (e) series 4 | 91.00 | read, context | 3.620 J/(mol K) | (2057.0, 3263.2) |
| (e) series 4 | 54.80 | read, context, extrapolated | 12.006 ±2.02 J/(mol K) | (1046.5, 3116.1) |
| (e) series 4 | 59.59 | read, context, extrapolated | 11.476 ±1.49 J/(mol K) | (1176.6, 3125.4) |
| (e) series 4 | 62.41 | read, context, extrapolated | 11.246 ±1.26 J/(mol K) | (1252.7, 3129.5) |
| (e) series 4 | 66.40 | read, context, extrapolated | 10.747 ±0.76 J/(mol K) | (1361.6, 3138.2) |
| (b) series 4 | 54.80 | deferred | star inside the square, and below the labelled −2 tick at y 1358 | (3104, 1463–1553) |
| (b) series 4 | 59.59 | deferred | star inside the square; y 1265–1343 is inside the labelled span | (3236, 1265–1343) |
| (b) series 4 | 62.41 | deferred | star inside the square; inside the labelled span | (3313, 1162–1234) |
| (b) series 4 | 66.40 | deferred | star inside the square; inside the labelled span | (3422, 1033–1099) |
| (b) series 4 | 71.83 | deferred | star inside the square; inside the labelled span | (3571, 888–950) |
| (b) series 4 | 82.93 | deferred | star inside the square; the triangle above is series 1 | (3876, 667–727) |
| (b) series 4 | 91.00 | deferred | star inside the square, against the filled triangle | (4097, 573–637) |

Panel (d) reads are the enthalpy of formation of the melt. They are not scored as delta_fH of K2O or of GeO2. Panel (c) is ΔfG(T)/(RT), which is not delta_fG. Panel (e) is ΔfS(T), which is not entropy_of_fusion. The five new panel (c) centres are the midpoints of the two holes the guide line leaves inside the star. The four new panel (e) reads are inside the frame and above the labelled 10 tick. The half-width is that extrapolation, which is larger than a 6 px centre error. Series 1, 2 and 3 are replots ([1], [9], [19]) and are refused. Their centres were not stored.

GeO2 activities on panel (b) were not admitted. On the corrected scale, six of the seven series-4 glyphs sit inside the labelled span. Each star is drawn inside the series-3 square, so the centre is not a star centre. No GeO2 melting temperature is printed in this paper, and none is cited from a table on this base, so a GeO2 activity reference would stay phase unknown even if a star were read.

## Other figures

- Fig. 3: Arrhenius plot of the Table 1 p(GeO) sets. The admitted numbers are the table. Markers were not read again. Refused as a replot.
- Fig. 4: the axis label prints `log p(i, 1033 K)` and the caption prints 1300 K. Both are on the 600 dpi page. Every marker is refused because the temperature is not unique. The three series are filled circles (p(GeO)), squares (p(K)) and triangles (p(O2)).
- Fig. 5, published page 1352, PDF page 8, 600 dpi, 5100×6600. Caption: thermodynamic data for melts in (1) Na2O–SiO2, (2) Na2O–GeO2, (3) K2O–SiO2 and (4) K2O–GeO2 at 1473 K. Panels, confirmed by the panel letter on the render: top row (c) (f) (i), middle row (b) (e) (h), bottom row (a) (d) (g). (a, b) activities, (c) Gibbs energy of melt formation, (d, e) partial enthalpies, (f) enthalpy of melt formation, (g, h) partial entropies. The caption text for (i) says enthalpy. The panel (i) axis prints an entropy.
- Legend on panel (i), 600 dpi: open triangle = 1, open circle = 2, filled triangle = 3, open star = 4. Series 1, 2 and 3 are replots of [20], [43] and [46]. One refusal row per panel. Their centres were not stored.
- Series 4 is this work. Every open star below has its own context row. No Fig. 5 value is stored. Panel (c) is the only panel whose horizontal axis was calibrated, and its vertical axis prints no mol% numerals (the left margin and the gap to panel (f) are blank). Panel (i) prints mol% 40 to 100 on its own ticks. Those tick y values do not match panel (c) (the offset grows from 3.5 px to 21.5 px), so the labels were not copied onto any other panel. The other panels were not calibrated on labelled ticks in this pass.
- Panel (c) labelled horizontal tick lines, the tick not the glyph: 0, −2, −4, −6, −8, −10, −12 at x 550.5, 711.5, 873.5, 1034.5, 1196.5, 1357.5, 1518.5. Least squares x = −80.6964·(ΔfG/(RT)) + 550.4643, residuals 0.04, −0.36, 0.25, −0.14, 0.46, 0.07, −0.32 px, rms 0.28 px. The reading in the table is not stored. Uncertainty 0.10 for an 8 px centre error.
- Centres are template-match peaks unless the evidence column says otherwise. The template is the star at panel (c) page px (1219, 1170), with the guide line masked out of the match. A crop of each listed peak is an open star. Centre uncertainty 8 px, except the panel (b) top star.

| panel | n | page px | decision | evidence |
| --- | --- | --- | --- | --- |
| (c) series 4 | 1 | (815, 742) | deferred, no value | open star. Unstored ΔfG/(RT) = -3.278. No mol% label |
| (c) series 4 | 2 | (1001, 922) | deferred, no value | open star. Unstored ΔfG/(RT) = -5.583. No mol% label |
| (c) series 4 | 3 | (1219, 1170) | deferred, no value | open star. Unstored ΔfG/(RT) = -8.285. No mol% label |
| (c) series 4 | 4 | (1317, 1293) | deferred, no value | open star. Unstored ΔfG/(RT) = -9.499. No mol% label |
| (c) series 4 | 5 | (1385, 1382) | deferred, no value | open star. Unstored ΔfG/(RT) = -10.342. No mol% label |
| (c) series 4 | 6 | (1430, 1446) | deferred, no value | open star. Unstored ΔfG/(RT) = -10.899. No mol% label |
| (c) series 4 | 7 | (1502, 1553) | deferred, no value | open star. Unstored ΔfG/(RT) = -11.792. No mol% label |
| (f) series 4 | 1 | (1904, 731) | deferred, no value | open star on a 600 dpi crop |
| (f) series 4 | 2 | (2072, 909) | deferred, no value | open star on a 600 dpi crop |
| (f) series 4 | 3 | (2289, 1155) | deferred, no value | open star on a 600 dpi crop |
| (f) series 4 | 4 | (2377, 1276) | deferred, no value | open star on a 600 dpi crop |
| (f) series 4 | 5 | (2440, 1364) | deferred, no value | open star on a 600 dpi crop |
| (f) series 4 | 6 | (2486, 1428) | deferred, no value | open star on a 600 dpi crop |
| (f) series 4 | 7 | (2556, 1534) | deferred, no value | open star on a 600 dpi crop |
| (i) series 4 | 1 | (3199, 729) | deferred, no value | open star on a 600 dpi crop |
| (i) series 4 | 2 | (3074, 906) | deferred, no value | open star on a 600 dpi crop |
| (i) series 4 | 3 | (3010, 1151) | deferred, no value | open star on a 600 dpi crop |
| (i) series 4 | 4 | (2950, 1272) | deferred, no value | open star on a 600 dpi crop |
| (i) series 4 | 5 | (2924, 1360) | deferred, no value | open star on a 600 dpi crop |
| (i) series 4 | 6 | (2916, 1423) | deferred, no value | open star on a 600 dpi crop |
| (i) series 4 | 7 | (2891, 1530) | deferred, no value | open star on a 600 dpi crop |
| (b) series 4 | 1 | (638, 2633) | deferred, no value | open star touching the series-2 circle. Centre is the dark centroid of box x 632-644, y 2625-2642, not the template peak (627, 2639). Uncertainty 6 px |
| (b) series 4 | 2 | (687, 2823) | deferred, no value | open star on a 600 dpi crop |
| (b) series 4 | 3 | (803, 3073) | deferred, no value | open star on a 600 dpi crop |
| (b) series 4 | 4 | (884, 3194) | deferred, no value | open star on a 600 dpi crop |
| (b) series 4 | 5 | (957, 3283) | deferred, no value | open star on a 600 dpi crop |
| (b) series 4 | 6 | (1016, 3347) | deferred, no value | open star on a 600 dpi crop |
| (b) series 4 | 7 | (1129, 3454) | deferred, no value | open star on a 600 dpi crop |
| (e) series 4 | 1 | (1832, 2645) | deferred, no value | open star on a 600 dpi crop |
| (e) series 4 | 2 | (1843, 2823) | deferred, no value | open star on a 600 dpi crop |
| (e) series 4 | 3 | (1911, 3067) | deferred, no value | open star on a 600 dpi crop |
| (e) series 4 | 4 | (1931, 3187) | deferred, no value | open star on a 600 dpi crop |
| (e) series 4 | 5 | (1953, 3275) | deferred, no value | open star. Hole-pair midpoint. Template peak (1955, 3273) agrees within 3 px. An open triangle sits beside it |
| (e) series 4 | 6 | (1964, 3335) | deferred, no value | open star on a 600 dpi crop |
| (e) series 4 | 7 | (1993, 3441) | deferred, no value | open star on a 600 dpi crop |
| (h) series 4 | 1 | (3697, 2814) | deferred, no value | open star on a 600 dpi crop |
| (h) series 4 | 2 | (3615, 3061) | deferred, no value | open star on a 600 dpi crop |
| (h) series 4 | 3 | (3531, 3182) | deferred, no value | open star on a 600 dpi crop |
| (h) series 4 | 4 | (3464, 3270) | deferred, no value | open star on a 600 dpi crop |
| (h) series 4 | 5 | (3398, 3333) | deferred, no value | open star on a 600 dpi crop |
| (h) series 4 | 6 | (3280, 3439) | deferred, no value | open star on a 600 dpi crop |
| (a) series 4 | 1 | (1441, 4540) | deferred, no value | open star on a 600 dpi crop |
| (a) series 4 | 2 | (1315, 4721) | deferred, no value | open star on a 600 dpi crop |
| (a) series 4 | 3 | (1173, 4971) | deferred, no value | open star on a 600 dpi crop |
| (a) series 4 | 4 | (1115, 5093) | deferred, no value | open star on a 600 dpi crop |
| (a) series 4 | 5 | (1073, 5183) | deferred, no value | open star on a 600 dpi crop |
| (a) series 4 | 6 | (1045, 5246) | deferred, no value | open star on a 600 dpi crop |
| (a) series 4 | 7 | (1000, 5353) | deferred, no value | open star on a 600 dpi crop |
| (d) series 4 | 1 | (2630, 4547) | deferred, no value | open star on a 600 dpi crop |
| (d) series 4 | 2 | (2534, 4724) | deferred, no value | open star on a 600 dpi crop |
| (d) series 4 | 3 | (2403, 4968) | deferred, no value | open star on a 600 dpi crop |
| (d) series 4 | 4 | (2350, 5088) | deferred, no value | open star on a 600 dpi crop |
| (d) series 4 | 5 | (2315, 5175) | deferred, no value | open star on a 600 dpi crop |
| (d) series 4 | 6 | (2302, 5238) | deferred, no value | open star on a 600 dpi crop |
| (d) series 4 | 7 | (2263, 5342) | deferred, no value | open star on a 600 dpi crop |
| (g) series 4 | 1 | (3056, 4533) | deferred, no value | open star on a 600 dpi crop |
| (g) series 4 | 2 | (3182, 4713) | deferred, no value | open star on a 600 dpi crop |
| (g) series 4 | 3 | (3292, 4961) | deferred, no value | open star on a 600 dpi crop |
| (g) series 4 | 4 | (3341, 5082) | deferred, no value | open star on a 600 dpi crop |
| (g) series 4 | 5 | (3376, 5170) | deferred, no value | open star on a 600 dpi crop |
| (g) series 4 | 6 | (3415, 5233) | deferred, no value | open star on a 600 dpi crop |
| (g) series 4 | 7 | (3455, 5339) | deferred, no value | open star on a 600 dpi crop |
| (h) spacing gap | — | near (3760, 2634) | refused | filled triangle against a filled circle, beside the printed 90 mol% label. No open star. The six stars below leave this gap |

Panel (b) top star and the panel (h) gap are the two places where a seventh series-4 marker would complete the spacing shared by panels (c), (f), (i), (a), (d) and (g). On (b) the star is there and touches the circle. On (h) the glyph is series 3. No other series-4 star was left without a centre.

## Standard state for the K2O activities

Eq. (2) on published page 1346 is a(i) = p(i)/p0(i), with p0 over the corresponding oxide. Eq. (8) on published page 1350 writes that ratio for K2O. The paper does not name the pure-oxide phase. The points are at 1400 K. Shornikov (2013) Table 2, published page 110, prints Tm(K2O) = 1013±10 K (Glushko et al. 1978–1982), melting band 1003–1023 K. 1400 K is 377 K above that band, so the stable phase is liquid. Structured standard_state: raoultian K2O, phase tag value l, inferred true. A read-only migration lands identity.reference_state as raoultian pure K2O, phase l, on all seven points.

## Per-mole basis and fusion-entropy unit

Table 2 headers print kJ/mol and J/(mol K). Published page 1351 says the melt-formation comparison is per mole of the appropriate compound. The stored basis is mol_species. The five fusion entropies are stored as J/mol K, with units_as_printed J/(mol K).

## Render checks

600 dpi: PDF pages 3 (Fig. 2), 4 (Table 1), 5 (Fig. 3), 6 (Fig. 4), 8 (Fig. 5), 10 (Table 2). Table digits were not changed.

# Shornikov 2000 Al2O3–SiO2 melt thermodynamics — extraction audit

Source: Shornikov, S. I., and Archakov, I. Yu. (2000), A Mass Spectrometric Study of the Thermodynamic Properties of Al2O3–SiO2 Melts, Russian Journal of Physical Chemistry 74(5), 684–688. Translated from Zhurnal Fizicheskoi Khimii 74(5), 783–788. PDF SHA256 3f70aad89b48cb4982b53566b24788d4fba605f2748fb5a774ea64c4d232db82. Five pages. No DOI is printed. pdfinfo has no Title; the title is the page-1 heading.

## Duplicate check

Not a duplicate. The adjacent extract `shornikov-2000-aluminosilicate-vaporization` is RJPC 74(5) 677–683, “Vaporization and Phase Equilibria” (Russian pp. 775–782), cited here as [1]. This article is pp. 684–688 (Russian pp. 783–788). `shornikov-2002-alumina-silica-melt-thermodynamics` is a later enthalpies paper (RJPC 76(7) 1054–1060). No extract on this base cites pp. 684–688.

## Tables

MinerU content_list has text, equation, chart, and image blocks only. There is no HTML table and nothing for `mineru_table_draft` to emit. The chart `<details>` tilde-tables are invented OCR and were ignored.

## Figure 2 (activities)

PDF page 3, published page 686. Linear activity and linear mol% Al2O3. Both reads are on the 600 dpi page.

X: mol% = (x_px − 593.7955) / 17.0395. Tick centers used: (593.5, 0), (764.5, 10), (934.5, 20), (1105.0, 30), (1275.5, 40), (1445.5, 50), (1616.5, 60), (1786.5, 70), (1957.0, 80), (2127.5, 90), (2297.5, 100).

Panel a: a = (1771.5 − y) / 1180. Panel b: a = (3113.5 − y) / 1167.5. The panel-b labels 0.8, 0.6, 0.4, and 0.2 land on that line within about 1 px. The panel-a frame corner is a = 1.

Read 1 is the eroded-core seed (filled markers) or the open-square ring center. Read 2 is a radius-14 dark centroid (filled) or a dark-annulus centroid (open squares). Shifts are under 2 px. The nominal value is the mean. `read_uncertainty` is at least half a marker (12 px circles, 8.5 px triangles, 10 px squares) and contains both reads. It is a digitising width. `read_uncertainty_dex` is stored only where activity is at least 0.05.

Admitted, this work only, symbols 1–3:

- SiO2, 2200 K, filled circles: 10 points. The gap from 45.82 to 61.02 mol% Al2O3 is the “10” label; those circles were not separable and were not invented.
- SiO2, 2300 K, open squares: 19 points. The fix round added one square at 84.99 mol% Al2O3, activity 0.017. Its bottom edge is the axis, so the y uncertainty is 12 px.
- SiO2, 2400 K, filled triangles: 19 points. The fix round added four triangles, at 78.04, 87.54, 93.51, and 96.06 mol% Al2O3 (activities 0.060, 0.015, 0.021, 0.012). The last three meet the axis, so their y uncertainty is 12 px. The rise from 0.015 to 0.021 is inside that uncertainty.
- Al2O3, 2200 K, filled circles: 12 points. The last is 71.95 mol% Al2O3, activity 0.997, on the top frame. The printed corundum-liquidus limit from [1] is 28.56 mol% SiO2, which is 71.44 mol% Al2O3. The read was not forced onto that limit.
- Al2O3, 2300 K, open squares: 20 points, ending at 85.11 mol%, activity 0.836. The printed limit is 90.10 mol% Al2O3. Squares between there and a = 1 were not separable from the stroke.
- Al2O3, 2400 K, filled triangles: 25 points, ending at 99.98 mol%, activity 0.994. The corner core is a filled marker (about 490 dark pixels inside a 14 px ball), not the frame line. The printed limit at 2400 K is x*(SiO2) = 0.

Dropped: the panel-a legend column near x = 1901, y < 1100; one false panel-b square at (1467, 1997) under the top label; three near-origin square doubles closer than 40 px. Symbol 7 (crossed circles, literature) was not admitted. Curves 4–10 were not sampled.

## Standard state

SiO2, all three temperatures. Equation (14), published p. 685, integrates from x(Al2O3) = 0. Published p. 686 says this avoids the thermodynamic functions of pure components. The phase of that limit is not stated. Expected migration result: unknown. The 2026-10-20 pure-oxide rule was not applied. The anchor is an extrapolated Gibbs–Duhem limit, not an ion current over a pure oxide and not a tabulated standard at the experiment temperature.

Al2O3. Published p. 685 sets the lower limit of equation (1) on the corundum crystallization line at the printed x*(SiO2). The phase is not stated, including at 2400 K where x* = 0. Expected migration result: unknown. The pure-oxide rule was not applied, because the reference is the x* liquidus, not a pure oxide measured in the cell.

## Figure 1 and Figure 3

Figure 1 (published p. 685) is log I(SiO)+/I(Al)+. Context only.

Figure 3 (published p. 687) is the integral melt −ΔfG. Y ticks 0, 10, 20, 30 fit −ΔfG = (y − 596) / 39.067 kJ/mol. X is mol% = (x − 2710.9) / 17.03. The fix round read the this-work markers twice. Read 1 is the 7×7 eroded core (filled) or the bright-hole centre (open square). Read 2 is a radius-14 dark centroid or the dark-ring centroid. Shifts are under 2 px. Stored, as a typed refusal because the schema has no solution-composition carrier: 15 filled circles at 2200 K, 19 open squares at 2300 K, and 25 filled triangles at 2400 K. Four open-square centres were removed: (2794, 766), (2832, 872), (3080, 1118), and (3964, 1438). The white-hole flood leaks or the crop shows a glyph gap. (3964, 1438) is the upper counter of a printed 8. Kept squares have a closed hole about 18x18 or 19x19 px, including 9.65 mol% at (2874, 874) and 15.38 mol% at (2972, 1014). The values of the kept points were not recalibrated. The legend glyphs near x = 3427 were excluded. Half-marker uncertainty is 0.31 kJ/mol for circles, 0.26 for squares, and 0.22 for triangles.

Refused on Figure 3, each one: the four removed centres above; open-square candidates at (2960, 988), (3144, 1138), (3908, 1220), (3956, 1468), (3962, 1186), and (4062, 1120), because they sit on the circle curve or break the square series; and the clump (4176, 1022), (4208, 984), (4224, 964), (4228, 992), (4248, 964), past about 85 mol%, where curves 7-9 converge and the white gaps are not one square each. A partly hidden square near (2796, 748) was not given a centre: deferred: overlapping, arc-fit needed (t-1380). Curves 4-9 were not sampled.

Refused on the Figure 2(a) tail, each one: the dark mass near x = 1830–1872 (the stored 2300 K square, the curve, and any triangle are one mass); x ≈ 1895, curve thickness; x ≈ 1984–1990, a triangle tip merged with the stored square at (1966, 1730); x ≈ 2128, the 90% tick through the glyph; x ≈ 2275, the axis plus a bump; x ≈ 2296–2300, the right-hand frame. The dashed upper strokes are curves 4–10, not markers. Partial Gibbs energies remain RT ln a on the Figure 2 rows. The new activity points reuse the parent observation's standard state. The reference phase is not stated, and the pure-oxide rule is not applied.

## Prose kept as context

x*(SiO2) = 28.56 ± 0.15, 9.90 ± 0.15, and 0 mol% at 2200, 2300, and 2400 K, cited from [1]. Equation (16) residuals 2.2%, 1.6%, and 0.8%. The k1,T, k2,T, and k3,T coefficients of equation (15) are not printed.

## Decision tables

### 2000 Figure 1

| marker | place | evidence | decision |
|---|---|---|---|
| curve 1 | 2300 K | continuous log ion-current trace | refused: the stroke is one curve, not a set of separable legend markers. |
| curve 2 | 2400 K | continuous log ion-current trace | refused: the stroke is one curve, not a set of separable legend markers. |

### 2000 Figure 2 stored points

Values were not changed in this round. Each stored marker is a read.

| id | pixel | symbol | decision |
|---|---|---|---|
| shornikov_2000_f2_sio2_2200k-01 | (865.20, 676.20) | shornikov_2000_f2_sio2_2200k | read: a=0.928, 15.92 mol% Al2O3 |
| shornikov_2000_f2_sio2_2200k-02 | (954.20, 732.10) | shornikov_2000_f2_sio2_2200k | read: a=0.881, 21.15 mol% Al2O3 |
| shornikov_2000_f2_sio2_2200k-03 | (1037.40, 798.60) | shornikov_2000_f2_sio2_2200k | read: a=0.824, 26.02 mol% Al2O3 |
| shornikov_2000_f2_sio2_2200k-04 | (1118.60, 867.40) | shornikov_2000_f2_sio2_2200k | read: a=0.766, 30.81 mol% Al2O3 |
| shornikov_2000_f2_sio2_2200k-05 | (1191.50, 940.20) | shornikov_2000_f2_sio2_2200k | read: a=0.705, 35.09 mol% Al2O3 |
| shornikov_2000_f2_sio2_2200k-06 | (1287.30, 1048.60) | shornikov_2000_f2_sio2_2200k | read: a=0.612, 40.69 mol% Al2O3 |
| shornikov_2000_f2_sio2_2200k-07 | (1374.60, 1159.00) | shornikov_2000_f2_sio2_2200k | read: a=0.519, 45.83 mol% Al2O3 |
| shornikov_2000_f2_sio2_2200k-08 | (1633.40, 1542.30) | shornikov_2000_f2_sio2_2200k | read: a=0.194, 61.00 mol% Al2O3 |
| shornikov_2000_f2_sio2_2200k-09 | (1722.90, 1636.90) | shornikov_2000_f2_sio2_2200k | read: a=0.114, 66.27 mol% Al2O3 |
| shornikov_2000_f2_sio2_2200k-10 | (1815.30, 1705.60) | shornikov_2000_f2_sio2_2200k | read: a=0.056, 71.68 mol% Al2O3 |
| shornikov_2000_f2_sio2_2300k-01 | (677.40, 616.90) | shornikov_2000_f2_sio2_2300k | read: a=0.978, 4.86 mol% Al2O3 |
| shornikov_2000_f2_sio2_2300k-02 | (767.50, 648.50) | shornikov_2000_f2_sio2_2300k | read: a=0.952, 10.21 mol% Al2O3 |
| shornikov_2000_f2_sio2_2300k-03 | (924.50, 748.50) | shornikov_2000_f2_sio2_2300k | read: a=0.867, 19.38 mol% Al2O3 |
| shornikov_2000_f2_sio2_2300k-04 | (1000.60, 816.00) | shornikov_2000_f2_sio2_2300k | read: a=0.810, 23.89 mol% Al2O3 |
| shornikov_2000_f2_sio2_2300k-05 | (1081.00, 891.00) | shornikov_2000_f2_sio2_2300k | read: a=0.746, 28.59 mol% Al2O3 |
| shornikov_2000_f2_sio2_2300k-06 | (1151.00, 965.00) | shornikov_2000_f2_sio2_2300k | read: a=0.683, 32.70 mol% Al2O3 |
| shornikov_2000_f2_sio2_2300k-07 | (1223.00, 1039.00) | shornikov_2000_f2_sio2_2300k | read: a=0.621, 36.93 mol% Al2O3 |
| shornikov_2000_f2_sio2_2300k-08 | (1290.00, 1115.40) | shornikov_2000_f2_sio2_2300k | read: a=0.556, 40.86 mol% Al2O3 |
| shornikov_2000_f2_sio2_2300k-09 | (1353.00, 1186.00) | shornikov_2000_f2_sio2_2300k | read: a=0.496, 44.56 mol% Al2O3 |
| shornikov_2000_f2_sio2_2300k-10 | (1411.30, 1250.20) | shornikov_2000_f2_sio2_2300k | read: a=0.442, 48.00 mol% Al2O3 |
| shornikov_2000_f2_sio2_2300k-11 | (1468.60, 1312.00) | shornikov_2000_f2_sio2_2300k | read: a=0.389, 51.42 mol% Al2O3 |
| shornikov_2000_f2_sio2_2300k-12 | (1520.00, 1372.30) | shornikov_2000_f2_sio2_2300k | read: a=0.338, 54.35 mol% Al2O3 |
| shornikov_2000_f2_sio2_2300k-13 | (1573.40, 1418.00) | shornikov_2000_f2_sio2_2300k | read: a=0.300, 57.48 mol% Al2O3 |
| shornikov_2000_f2_sio2_2300k-14 | (1620.00, 1467.00) | shornikov_2000_f2_sio2_2300k | read: a=0.258, 60.23 mol% Al2O3 |
| shornikov_2000_f2_sio2_2300k-15 | (1704.80, 1543.60) | shornikov_2000_f2_sio2_2300k | read: a=0.193, 65.19 mol% Al2O3 |
| shornikov_2000_f2_sio2_2300k-16 | (1776.00, 1603.00) | shornikov_2000_f2_sio2_2300k | read: a=0.142, 69.38 mol% Al2O3 |
| shornikov_2000_f2_sio2_2300k-17 | (1869.50, 1663.50) | shornikov_2000_f2_sio2_2300k | read: a=0.091, 74.81 mol% Al2O3 |
| shornikov_2000_f2_sio2_2300k-18 | (1966.00, 1730.00) | shornikov_2000_f2_sio2_2300k | read: a=0.036, 80.53 mol% Al2O3 |
| shornikov_2000_f2_sio2_2300k-19 | Read mean (2041.9, 1751.15). The axis-excluded read sits about 3 px higher because the bottom edge is the axis | shornikov_2000_f2_sio2_2300k | read: a=0.017, 84.99 mol% Al2O3 |
| shornikov_2000_f2_sio2_2400k-01 | (770.50, 630.40) | shornikov_2000_f2_sio2_2400k | read: a=0.967, 10.36 mol% Al2O3 |
| shornikov_2000_f2_sio2_2400k-02 | (835.20, 701.80) | shornikov_2000_f2_sio2_2400k | read: a=0.907, 14.16 mol% Al2O3 |
| shornikov_2000_f2_sio2_2400k-03 | (920.60, 768.30) | shornikov_2000_f2_sio2_2400k | read: a=0.851, 19.19 mol% Al2O3 |
| shornikov_2000_f2_sio2_2400k-04 | (1003.90, 846.30) | shornikov_2000_f2_sio2_2400k | read: a=0.784, 24.06 mol% Al2O3 |
| shornikov_2000_f2_sio2_2400k-05 | (1086.20, 935.10) | shornikov_2000_f2_sio2_2400k | read: a=0.709, 28.89 mol% Al2O3 |
| shornikov_2000_f2_sio2_2400k-06 | (1167.90, 1024.60) | shornikov_2000_f2_sio2_2400k | read: a=0.633, 33.69 mol% Al2O3 |
| shornikov_2000_f2_sio2_2400k-07 | (1243.50, 1106.70) | shornikov_2000_f2_sio2_2400k | read: a=0.563, 38.14 mol% Al2O3 |
| shornikov_2000_f2_sio2_2400k-08 | (1320.70, 1194.10) | shornikov_2000_f2_sio2_2400k | read: a=0.489, 42.66 mol% Al2O3 |
| shornikov_2000_f2_sio2_2400k-09 | (1388.30, 1271.60) | shornikov_2000_f2_sio2_2400k | read: a=0.424, 46.61 mol% Al2O3 |
| shornikov_2000_f2_sio2_2400k-10 | (1453.00, 1340.30) | shornikov_2000_f2_sio2_2400k | read: a=0.366, 50.42 mol% Al2O3 |
| shornikov_2000_f2_sio2_2400k-11 | (1579.80, 1465.70) | shornikov_2000_f2_sio2_2400k | read: a=0.259, 57.85 mol% Al2O3 |
| shornikov_2000_f2_sio2_2400k-12 | (1632.10, 1509.50) | shornikov_2000_f2_sio2_2400k | read: a=0.222, 60.92 mol% Al2O3 |
| shornikov_2000_f2_sio2_2400k-13 | (1687.50, 1553.60) | shornikov_2000_f2_sio2_2400k | read: a=0.185, 64.19 mol% Al2O3 |
| shornikov_2000_f2_sio2_2400k-14 | (1734.50, 1592.90) | shornikov_2000_f2_sio2_2400k | read: a=0.152, 66.93 mol% Al2O3 |
| shornikov_2000_f2_sio2_2400k-15 | (1780.60, 1624.20) | shornikov_2000_f2_sio2_2400k | read: a=0.125, 69.66 mol% Al2O3 |
| shornikov_2000_f2_sio2_2400k-16 | Two reads, shift 0.47 px, mean (1923.51, 1700.89). audit shornikov_2000_f2_sio2_2400k-16. | shornikov_2000_f2_sio2_2400k | read: a=0.060, 78.04 mol% Al2O3 |
| shornikov_2000_f2_sio2_2400k-17 | Two reads, shift 0.16 px, mean (2085.40, 1753.60). The base meets the axis, so the y uncertainty is widened to 12 px. audit shornikov_2000_f2_sio2_2400k-17. | shornikov_2000_f2_sio2_2400k | read: a=0.015, 87.54 mol% Al2O3 |
| shornikov_2000_f2_sio2_2400k-18 | Two reads, shift 0.53 px, mean (2187.17, 1746.65). The base meets the axis, so the y uncertainty is widened to 12 px. audit shornikov_2000_f2_sio2_2400k-18. | shornikov_2000_f2_sio2_2400k | read: a=0.021, 93.51 mol% Al2O3 |
| shornikov_2000_f2_sio2_2400k-19 | Two reads, shift 2.01 px, mean (2230.54, 1757.59). The base meets the axis, so the y uncertainty is widened to 12 px. audit shornikov_2000_f2_sio2_2400k-19. | shornikov_2000_f2_sio2_2400k | read: a=0.012, 96.06 mol% Al2O3 |
| shornikov_2000_f2_al2o3_2200k-01 | (867.90, 3046.30) | shornikov_2000_f2_al2o3_2200k | read: a=0.058, 16.09 mol% Al2O3 |
| shornikov_2000_f2_al2o3_2200k-02 | (957.70, 3019.00) | shornikov_2000_f2_al2o3_2200k | read: a=0.081, 21.36 mol% Al2O3 |
| shornikov_2000_f2_al2o3_2200k-03 | (1041.80, 2997.20) | shornikov_2000_f2_al2o3_2200k | read: a=0.100, 26.30 mol% Al2O3 |
| shornikov_2000_f2_al2o3_2200k-04 | (1119.00, 2973.30) | shornikov_2000_f2_al2o3_2200k | read: a=0.120, 30.82 mol% Al2O3 |
| shornikov_2000_f2_al2o3_2200k-05 | (1189.70, 2946.30) | shornikov_2000_f2_al2o3_2200k | read: a=0.143, 34.98 mol% Al2O3 |
| shornikov_2000_f2_al2o3_2200k-06 | (1287.60, 2902.40) | shornikov_2000_f2_al2o3_2200k | read: a=0.181, 40.73 mol% Al2O3 |
| shornikov_2000_f2_al2o3_2200k-07 | (1377.00, 2850.40) | shornikov_2000_f2_al2o3_2200k | read: a=0.226, 45.96 mol% Al2O3 |
| shornikov_2000_f2_al2o3_2200k-08 | (1472.70, 2757.90) | shornikov_2000_f2_al2o3_2200k | read: a=0.305, 51.59 mol% Al2O3 |
| shornikov_2000_f2_al2o3_2200k-09 | (1525.80, 2689.30) | shornikov_2000_f2_al2o3_2200k | read: a=0.363, 54.70 mol% Al2O3 |
| shornikov_2000_f2_al2o3_2200k-10 | (1632.00, 2496.80) | shornikov_2000_f2_al2o3_2200k | read: a=0.528, 60.93 mol% Al2O3 |
| shornikov_2000_f2_al2o3_2200k-11 | (1722.70, 2273.80) | shornikov_2000_f2_al2o3_2200k | read: a=0.719, 66.26 mol% Al2O3 |
| shornikov_2000_f2_al2o3_2200k-12 | (1819.60, 1949.80) | shornikov_2000_f2_al2o3_2200k | read: a=0.997, 71.95 mol% Al2O3 |
| shornikov_2000_f2_al2o3_2300k-01 | (700.00, 3078.00) | shornikov_2000_f2_al2o3_2300k | read: a=0.030, 6.24 mol% Al2O3 |
| shornikov_2000_f2_al2o3_2300k-02 | (791.40, 3076.60) | shornikov_2000_f2_al2o3_2300k | read: a=0.032, 11.51 mol% Al2O3 |
| shornikov_2000_f2_al2o3_2300k-03 | (871.10, 3066.00) | shornikov_2000_f2_al2o3_2300k | read: a=0.041, 16.21 mol% Al2O3 |
| shornikov_2000_f2_al2o3_2300k-04 | (922.00, 3041.40) | shornikov_2000_f2_al2o3_2300k | read: a=0.062, 19.26 mol% Al2O3 |
| shornikov_2000_f2_al2o3_2300k-05 | (978.00, 3022.00) | shornikov_2000_f2_al2o3_2300k | read: a=0.079, 22.52 mol% Al2O3 |
| shornikov_2000_f2_al2o3_2300k-06 | (1078.00, 3007.00) | shornikov_2000_f2_al2o3_2300k | read: a=0.091, 28.41 mol% Al2O3 |
| shornikov_2000_f2_al2o3_2300k-07 | (1150.00, 2982.00) | shornikov_2000_f2_al2o3_2300k | read: a=0.113, 32.64 mol% Al2O3 |
| shornikov_2000_f2_al2o3_2300k-08 | (1222.00, 2955.00) | shornikov_2000_f2_al2o3_2300k | read: a=0.136, 36.87 mol% Al2O3 |
| shornikov_2000_f2_al2o3_2300k-09 | (1290.00, 2927.00) | shornikov_2000_f2_al2o3_2300k | read: a=0.160, 40.86 mol% Al2O3 |
| shornikov_2000_f2_al2o3_2300k-10 | (1353.00, 2895.00) | shornikov_2000_f2_al2o3_2300k | read: a=0.187, 44.56 mol% Al2O3 |
| shornikov_2000_f2_al2o3_2300k-11 | (1415.40, 2860.00) | shornikov_2000_f2_al2o3_2300k | read: a=0.217, 48.22 mol% Al2O3 |
| shornikov_2000_f2_al2o3_2300k-12 | (1471.00, 2827.00) | shornikov_2000_f2_al2o3_2300k | read: a=0.245, 51.48 mol% Al2O3 |
| shornikov_2000_f2_al2o3_2300k-13 | (1524.00, 2790.00) | shornikov_2000_f2_al2o3_2300k | read: a=0.277, 54.59 mol% Al2O3 |
| shornikov_2000_f2_al2o3_2300k-14 | (1574.00, 2755.00) | shornikov_2000_f2_al2o3_2300k | read: a=0.307, 57.52 mol% Al2O3 |
| shornikov_2000_f2_al2o3_2300k-15 | (1620.00, 2717.00) | shornikov_2000_f2_al2o3_2300k | read: a=0.340, 60.23 mol% Al2O3 |
| shornikov_2000_f2_al2o3_2300k-16 | (1704.00, 2641.00) | shornikov_2000_f2_al2o3_2300k | read: a=0.405, 65.15 mol% Al2O3 |
| shornikov_2000_f2_al2o3_2300k-17 | (1779.00, 2561.00) | shornikov_2000_f2_al2o3_2300k | read: a=0.473, 69.56 mol% Al2O3 |
| shornikov_2000_f2_al2o3_2300k-18 | (1866.00, 2451.00) | shornikov_2000_f2_al2o3_2300k | read: a=0.568, 74.68 mol% Al2O3 |
| shornikov_2000_f2_al2o3_2300k-19 | (1970.00, 2268.00) | shornikov_2000_f2_al2o3_2300k | read: a=0.724, 80.77 mol% Al2O3 |
| shornikov_2000_f2_al2o3_2300k-20 | (2044.00, 2138.00) | shornikov_2000_f2_al2o3_2300k | read: a=0.836, 85.11 mol% Al2O3 |
| shornikov_2000_f2_al2o3_2400k-01 | (748.20, 3092.20) | shornikov_2000_f2_al2o3_2400k | read: a=0.018, 9.05 mol% Al2O3 |
| shornikov_2000_f2_al2o3_2400k-02 | (837.20, 3086.30) | shornikov_2000_f2_al2o3_2400k | read: a=0.024, 14.29 mol% Al2O3 |
| shornikov_2000_f2_al2o3_2400k-03 | (921.40, 3072.40) | shornikov_2000_f2_al2o3_2400k | read: a=0.036, 19.23 mol% Al2O3 |
| shornikov_2000_f2_al2o3_2400k-04 | (1004.80, 3058.10) | shornikov_2000_f2_al2o3_2400k | read: a=0.048, 24.13 mol% Al2O3 |
| shornikov_2000_f2_al2o3_2400k-05 | (1087.10, 3043.50) | shornikov_2000_f2_al2o3_2400k | read: a=0.060, 28.96 mol% Al2O3 |
| shornikov_2000_f2_al2o3_2400k-06 | (1169.40, 3023.60) | shornikov_2000_f2_al2o3_2400k | read: a=0.077, 33.79 mol% Al2O3 |
| shornikov_2000_f2_al2o3_2400k-07 | (1245.70, 3000.50) | shornikov_2000_f2_al2o3_2400k | read: a=0.097, 38.28 mol% Al2O3 |
| shornikov_2000_f2_al2o3_2400k-08 | (1321.10, 2975.40) | shornikov_2000_f2_al2o3_2400k | read: a=0.119, 42.70 mol% Al2O3 |
| shornikov_2000_f2_al2o3_2400k-09 | (1388.50, 2950.20) | shornikov_2000_f2_al2o3_2400k | read: a=0.140, 46.63 mol% Al2O3 |
| shornikov_2000_f2_al2o3_2400k-10 | (1456.40, 2920.90) | shornikov_2000_f2_al2o3_2400k | read: a=0.165, 50.62 mol% Al2O3 |
| shornikov_2000_f2_al2o3_2400k-11 | (1518.80, 2890.10) | shornikov_2000_f2_al2o3_2400k | read: a=0.192, 54.30 mol% Al2O3 |
| shornikov_2000_f2_al2o3_2400k-12 | (1578.30, 2855.20) | shornikov_2000_f2_al2o3_2400k | read: a=0.221, 57.78 mol% Al2O3 |
| shornikov_2000_f2_al2o3_2400k-13 | (1635.50, 2822.20) | shornikov_2000_f2_al2o3_2400k | read: a=0.250, 61.15 mol% Al2O3 |
| shornikov_2000_f2_al2o3_2400k-14 | (1687.50, 2788.10) | shornikov_2000_f2_al2o3_2400k | read: a=0.279, 64.20 mol% Al2O3 |
| shornikov_2000_f2_al2o3_2400k-15 | (1735.40, 2756.90) | shornikov_2000_f2_al2o3_2400k | read: a=0.306, 67.00 mol% Al2O3 |
| shornikov_2000_f2_al2o3_2400k-16 | (1782.30, 2722.00) | shornikov_2000_f2_al2o3_2400k | read: a=0.336, 69.75 mol% Al2O3 |
| shornikov_2000_f2_al2o3_2400k-17 | (1825.70, 2691.00) | shornikov_2000_f2_al2o3_2400k | read: a=0.362, 72.31 mol% Al2O3 |
| shornikov_2000_f2_al2o3_2400k-18 | (1864.50, 2657.80) | shornikov_2000_f2_al2o3_2400k | read: a=0.390, 74.57 mol% Al2O3 |
| shornikov_2000_f2_al2o3_2400k-19 | (1963.50, 2565.90) | shornikov_2000_f2_al2o3_2400k | read: a=0.469, 80.39 mol% Al2O3 |
| shornikov_2000_f2_al2o3_2400k-20 | (2038.50, 2485.50) | shornikov_2000_f2_al2o3_2400k | read: a=0.538, 84.78 mol% Al2O3 |
| shornikov_2000_f2_al2o3_2400k-21 | (2126.70, 2366.90) | shornikov_2000_f2_al2o3_2400k | read: a=0.640, 89.97 mol% Al2O3 |
| shornikov_2000_f2_al2o3_2400k-22 | (2194.50, 2247.10) | shornikov_2000_f2_al2o3_2400k | read: a=0.742, 93.93 mol% Al2O3 |
| shornikov_2000_f2_al2o3_2400k-23 | (2238.20, 2136.00) | shornikov_2000_f2_al2o3_2400k | read: a=0.837, 96.50 mol% Al2O3 |
| shornikov_2000_f2_al2o3_2400k-24 | (2255.20, 2066.40) | shornikov_2000_f2_al2o3_2400k | read: a=0.897, 97.50 mol% Al2O3 |
| shornikov_2000_f2_al2o3_2400k-25 | (2297.40, 1953.00) | shornikov_2000_f2_al2o3_2400k | read: a=0.994, 99.98 mol% Al2O3 |

### 2000 Figure 2 refused

| marker | pixel | what | decision |
|---|---|---|---|
| legend column | x=1901, y<1100 | panel a | refused: legend glyphs, not data. |
| false square | (1467, 1997) | under the panel-b top label | refused: not on the square series. |
| near-origin doubles | three pairs closer than 40 px | panel b | refused: two centres inside one marker width. |
| symbol 7 | crossed circles | literature | refused: not this-work symbols 1-3. |
| curves 4-10 | dashed upper strokes | literature and equation (18) | refused: not symbols 1-3. |
| tail mass | x=1830-1872 | 2300 K square, curve, triangle | refused: one dark mass. |
| tail | x about 1895 | curve thickness | refused: not a separate marker. |
| tail | x=1984-1990 | triangle tip | refused: merged with the stored square at (1966, 1730). |
| tail | x about 2128 | 90% tick | refused: the tick runs through the glyph. |
| tail | x about 2275 | axis bump | refused: axis plus a bump, not a marker. |
| tail | x=2296-2300 | right-hand frame | refused: frame, not a marker. |

### 2000 Figure 3

Kept centres were not recalibrated. Removed centres are glyph gaps.

| id | pixel | symbol | decision |
|---|---|---|---|
| fig3-01 | (2778.5, 736.6) | filled_circle 2200 K | read: -dG=3.60 kJ/mol, 3.96 mol% |
| fig3-02 | (2834.5, 801.0) | filled_circle 2200 K | read: -dG=5.25 kJ/mol, 7.24 mol% |
| fig3-03 | (2886.4, 858.5) | filled_circle 2200 K | read: -dG=6.71 kJ/mol, 10.29 mol% |
| fig3-04 | (2982.3, 961.3) | filled_circle 2200 K | read: -dG=9.35 kJ/mol, 15.93 mol% |
| fig3-05 | (3070.7, 1041.5) | filled_circle 2200 K | read: -dG=11.41 kJ/mol, 21.14 mol% |
| fig3-06 | (3157.4, 1122.8) | filled_circle 2200 K | read: -dG=13.49 kJ/mol, 26.21 mol% |
| fig3-07 | (3235.4, 1186.4) | filled_circle 2200 K | read: -dG=15.11 kJ/mol, 30.79 mol% |
| fig3-08 | (3308.7, 1239.1) | filled_circle 2200 K | read: -dG=16.46 kJ/mol, 35.11 mol% |
| fig3-09 | (3404.0, 1296.7) | filled_circle 2200 K | read: -dG=17.94 kJ/mol, 40.70 mol% |
| fig3-10 | (3491.5, 1333.2) | filled_circle 2200 K | read: -dG=18.87 kJ/mol, 45.85 mol% |
| fig3-11 | (3588.6, 1356.2) | filled_circle 2200 K | read: -dG=19.46 kJ/mol, 51.55 mol% |
| fig3-12 | (3641.3, 1357.1) | filled_circle 2200 K | read: -dG=19.48 kJ/mol, 54.62 mol% |
| fig3-13 | (3753.1, 1330.9) | filled_circle 2200 K | read: -dG=18.81 kJ/mol, 61.19 mol% |
| fig3-14 | (3840.6, 1277.9) | filled_circle 2200 K | read: -dG=17.46 kJ/mol, 66.35 mol% |
| fig3-15 | (3935.1, 1180.4) | filled_circle 2200 K | read: -dG=14.95 kJ/mol, 71.88 mol% |
| fig3-16 | (2874.0, 874.0) | open_square 2300 K | read: -dG=7.08 kJ/mol, 9.65 mol% |
| fig3-17 | (2972.0, 1014.0) | open_square 2300 K | read: -dG=10.70 kJ/mol, 15.38 mol% |
| fig3-18 | (3040.0, 1090.0) | open_square 2300 K | read: -dG=12.65 kJ/mol, 19.33 mol% |
| fig3-19 | (3118.0, 1176.0) | open_square 2300 K | read: -dG=14.85 kJ/mol, 23.93 mol% |
| fig3-20 | (3196.0, 1252.0) | open_square 2300 K | read: -dG=16.79 kJ/mol, 28.49 mol% |
| fig3-21 | (3268.0, 1314.0) | open_square 2300 K | read: -dG=18.38 kJ/mol, 32.74 mol% |
| fig3-22 | (3336.0, 1362.0) | open_square 2300 K | read: -dG=19.60 kJ/mol, 36.71 mol% |
| fig3-23 | (3404.0, 1406.0) | open_square 2300 K | read: -dG=20.73 kJ/mol, 40.71 mol% |
| fig3-24 | (3468.0, 1436.0) | open_square 2300 K | read: -dG=21.49 kJ/mol, 44.47 mol% |
| fig3-25 | (3528.0, 1456.0) | open_square 2300 K | read: -dG=22.01 kJ/mol, 47.97 mol% |
| fig3-26 | (3584.0, 1474.0) | open_square 2300 K | read: -dG=22.47 kJ/mol, 51.27 mol% |
| fig3-27 | (3640.0, 1480.0) | open_square 2300 K | read: -dG=22.63 kJ/mol, 54.55 mol% |
| fig3-28 | (3690.0, 1484.0) | open_square 2300 K | read: -dG=22.73 kJ/mol, 57.49 mol% |
| fig3-29 | (3736.0, 1480.0) | open_square 2300 K | read: -dG=22.63 kJ/mol, 60.19 mol% |
| fig3-30 | (3822.0, 1462.0) | open_square 2300 K | read: -dG=22.16 kJ/mol, 65.23 mol% |
| fig3-31 | (3896.0, 1432.0) | open_square 2300 K | read: -dG=21.40 kJ/mol, 69.57 mol% |
| fig3-32 | (3982.0, 1372.0) | open_square 2300 K | read: -dG=19.86 kJ/mol, 74.63 mol% |
| fig3-33 | (4084.0, 1262.0) | open_square 2300 K | read: -dG=17.04 kJ/mol, 80.64 mol% |
| fig3-34 | (4158.0, 1142.0) | open_square 2300 K | read: -dG=13.98 kJ/mol, 84.98 mol% |
| fig3-35 | (2860.7, 909.9) | filled_triangle 2400 K | read: -dG=8.04 kJ/mol, 8.80 mol% |
| fig3-36 | (2950.4, 1060.8) | filled_triangle 2400 K | read: -dG=11.90 kJ/mol, 14.06 mol% |
| fig3-37 | (3036.1, 1197.3) | filled_triangle 2400 K | read: -dG=15.39 kJ/mol, 19.09 mol% |
| fig3-38 | (3121.4, 1316.1) | filled_triangle 2400 K | read: -dG=18.43 kJ/mol, 24.10 mol% |
| fig3-39 | (3205.9, 1414.2) | filled_triangle 2400 K | read: -dG=20.94 kJ/mol, 29.06 mol% |
| fig3-40 | (3285.2, 1496.5) | filled_triangle 2400 K | read: -dG=23.04 kJ/mol, 33.71 mol% |
| fig3-41 | (3362.1, 1565.4) | filled_triangle 2400 K | read: -dG=24.81 kJ/mol, 38.23 mol% |
| fig3-42 | (3436.2, 1619.9) | filled_triangle 2400 K | read: -dG=26.20 kJ/mol, 42.58 mol% |
| fig3-43 | (3503.2, 1660.3) | filled_triangle 2400 K | read: -dG=27.23 kJ/mol, 46.51 mol% |
| fig3-44 | (3571.2, 1688.4) | filled_triangle 2400 K | read: -dG=27.95 kJ/mol, 50.51 mol% |
| fig3-45 | (3633.5, 1706.7) | filled_triangle 2400 K | read: -dG=28.42 kJ/mol, 54.18 mol% |
| fig3-46 | (3694.8, 1720.6) | filled_triangle 2400 K | read: -dG=28.78 kJ/mol, 57.77 mol% |
| fig3-47 | (3752.0, 1720.7) | filled_triangle 2400 K | read: -dG=28.78 kJ/mol, 61.13 mol% |
| fig3-48 | (3803.5, 1712.4) | filled_triangle 2400 K | read: -dG=28.57 kJ/mol, 64.16 mol% |
| fig3-49 | (3853.4, 1703.6) | filled_triangle 2400 K | read: -dG=28.34 kJ/mol, 67.09 mol% |
| fig3-50 | (3901.1, 1680.7) | filled_triangle 2400 K | read: -dG=27.76 kJ/mol, 69.89 mol% |
| fig3-51 | (3943.8, 1659.5) | filled_triangle 2400 K | read: -dG=27.22 kJ/mol, 72.41 mol% |
| fig3-52 | (3979.3, 1637.1) | filled_triangle 2400 K | read: -dG=26.64 kJ/mol, 74.49 mol% |
| fig3-53 | (4079.0, 1549.2) | filled_triangle 2400 K | read: -dG=24.39 kJ/mol, 80.34 mol% |
| fig3-54 | (4154.0, 1454.0) | filled_triangle 2400 K | read: -dG=21.96 kJ/mol, 84.74 mol% |
| fig3-55 | (4242.8, 1293.7) | filled_triangle 2400 K | read: -dG=17.86 kJ/mol, 89.95 mol% |
| fig3-56 | (4309.7, 1128.9) | filled_triangle 2400 K | read: -dG=13.64 kJ/mol, 93.88 mol% |
| fig3-57 | (4353.1, 989.4) | filled_triangle 2400 K | read: -dG=10.07 kJ/mol, 96.43 mol% |
| fig3-58 | (4365.5, 938.9) | filled_triangle 2400 K | read: -dG=8.78 kJ/mol, 97.16 mol% |
| fig3-59 | (4410.3, 602.6) | filled_triangle 2400 K | read: -dG=0.18 kJ/mol, 99.79 mol% |

### 2000 Figure 3 refused or deferred

| marker | pixel | what | decision |
|---|---|---|---|
| removed | (2794, 766) | was 4.87 mol%, 4.34 kJ/mol | refused: white region leaks. Not a closed square. |
| removed | (2832, 872) | was 7.16 mol%, 7.08 kJ/mol | refused: glyph gap between diagonal strokes. |
| removed | (3080, 1118) | was 21.65 mol%, 13.37 kJ/mol | refused: ring sat on diagonal strokes, not a closed square. |
| removed | (3964, 1438) | was 73.53 mol%, 21.58 kJ/mol | refused: upper hole of the printed numeral 8. The real square beside it, 74.63 mol% at (3982, 1372), stays. |
| hidden | (2796, 748) | partly hidden square | deferred: overlapping, arc-fit needed (t-1380) |
| candidate | (2960, 988) | open-square candidate | refused: sits on the circle curve or breaks the square series. |
| candidate | (3144, 1138) | open-square candidate | refused: sits on the circle curve or breaks the square series. |
| candidate | (3908, 1220) | open-square candidate | refused: sits on the circle curve or breaks the square series. |
| candidate | (3956, 1468) | open-square candidate | refused: sits on the circle curve or breaks the square series. |
| candidate | (3962, 1186) | open-square candidate | refused: sits on the circle curve or breaks the square series. |
| candidate | (4062, 1120) | open-square candidate | refused: sits on the circle curve or breaks the square series. |
| clump | (4176, 1022) | past about 85 mol% | refused: curves 7-9 converge; the white gap is not one square. |
| clump | (4208, 984) | past about 85 mol% | refused: curves 7-9 converge; the white gap is not one square. |
| clump | (4224, 964) | past about 85 mol% | refused: curves 7-9 converge; the white gap is not one square. |
| clump | (4228, 992) | past about 85 mol% | refused: curves 7-9 converge; the white gap is not one square. |
| clump | (4248, 964) | past about 85 mol% | refused: curves 7-9 converge; the white gap is not one square. |
| legend | x=3427, y=768 and y=964 | circle and triangle glyphs | refused: legend, not data. |
| curves 4-9 | literature and equation (18) | not symbols 1-3 | refused: not this-work markers. |

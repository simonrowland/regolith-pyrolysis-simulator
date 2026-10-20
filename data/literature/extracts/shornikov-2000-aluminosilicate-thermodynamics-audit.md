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

Figure 3 (published p. 687) is the integral melt −ΔfG. Y ticks 0, 10, 20, 30 fit −ΔfG = (y − 596) / 39.067 kJ/mol. X is mol% = (x − 2710.9) / 17.03. The fix round read the this-work markers twice. Read 1 is the 7×7 eroded core (filled) or the bright-hole centre (open square). Read 2 is a radius-14 dark centroid or the dark-ring centroid. Shifts are under 2 px. Stored, as a typed refusal because the schema has no solution-composition carrier: 15 filled circles at 2200 K, 23 open squares at 2300 K, and 25 filled triangles at 2400 K. The legend glyphs near x = 3427 were excluded. Half-marker uncertainty is 0.31 kJ/mol for circles, 0.26 for squares, and 0.22 for triangles.

Refused on Figure 3, each one: open-square candidates at (2960, 988), (3144, 1138), (3908, 1220), (3956, 1468), (3962, 1186), and (4062, 1120), because they sit on the circle curve or break the square series; and the clump (4176, 1022), (4208, 984), (4224, 964), (4228, 992), (4248, 964), past about 85 mol%, where curves 7–9 converge and the white gaps are not one square each. Curves 4–9 were not sampled.

Refused on the Figure 2(a) tail, each one: the dark mass near x = 1830–1872 (the stored 2300 K square, the curve, and any triangle are one mass); x ≈ 1895, curve thickness; x ≈ 1984–1990, a triangle tip merged with the stored square at (1966, 1730); x ≈ 2128, the 90% tick through the glyph; x ≈ 2275, the axis plus a bump; x ≈ 2296–2300, the right-hand frame. The dashed upper strokes are curves 4–10, not markers. Partial Gibbs energies remain RT ln a on the Figure 2 rows. The new activity points reuse the parent observation's standard state. The reference phase is not stated, and the pure-oxide rule is not applied.

## Prose kept as context

x*(SiO2) = 28.56 ± 0.15, 9.90 ± 0.15, and 0 mol% at 2200, 2300, and 2400 K, cited from [1]. Equation (16) residuals 2.2%, 1.6%, and 0.8%. The k1,T, k2,T, and k3,T coefficients of equation (15) are not printed.

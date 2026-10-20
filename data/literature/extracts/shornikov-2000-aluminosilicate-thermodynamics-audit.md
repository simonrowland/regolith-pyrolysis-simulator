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
- SiO2, 2300 K, open squares: 18 points. Squares past about 81 mol% sit on the axis and were left unread.
- SiO2, 2400 K, filled triangles: 15 points. Triangles past about 70 mol% merge with the axis.
- Al2O3, 2200 K, filled circles: 12 points. The last is 71.95 mol% Al2O3, activity 0.997, on the top frame. The printed corundum-liquidus limit from [1] is 28.56 mol% SiO2, which is 71.44 mol% Al2O3. The read was not forced onto that limit.
- Al2O3, 2300 K, open squares: 20 points, ending at 85.11 mol%, activity 0.836. The printed limit is 90.10 mol% Al2O3. Squares between there and a = 1 were not separable from the stroke.
- Al2O3, 2400 K, filled triangles: 25 points, ending at 99.98 mol%, activity 0.994. The corner core is a filled marker (about 490 dark pixels inside a 14 px ball), not the frame line. The printed limit at 2400 K is x*(SiO2) = 0.

Dropped: the panel-a legend column near x = 1901, y < 1100; one false panel-b square at (1467, 1997) under the top label; three near-origin square doubles closer than 40 px. Symbol 7 (crossed circles, literature) was not admitted. Curves 4–10 were not sampled.

## Standard state

SiO2, all three temperatures. Equation (14), published p. 685, integrates from x(Al2O3) = 0. Published p. 686 says this avoids the thermodynamic functions of pure components. The phase of that limit is not stated. Expected migration result: unknown. The 2026-10-20 pure-oxide rule was not applied. The anchor is an extrapolated Gibbs–Duhem limit, not an ion current over a pure oxide and not a tabulated standard at the experiment temperature.

Al2O3. Published p. 685 sets the lower limit of equation (1) on the corundum crystallization line at the printed x*(SiO2). The phase is not stated, including at 2400 K where x* = 0. Expected migration result: unknown. The pure-oxide rule was not applied, because the reference is the x* liquidus, not a pure oxide measured in the cell.

## Figure 1 and Figure 3

Figure 1 (published p. 685) is log I(SiO)+/I(Al)+. Context only.

Figure 3 (published p. 687) is the integral melt −ΔfG. Y ticks 0, 10, 20, 30 fit G = −15.253 + 0.025595 y (39.07 px per kJ/mol) with residuals under 0.01 kJ/mol on those four ticks. X ticks every 10 mol% match 17.03 px per mol%. Eroded cores on the curves are 30–40 px stroke segments, not separable markers. No Gibbs numbers are stored. The quantity is also the integral of the melt, which a one-formula species key cannot carry. Partial Gibbs energies remain RT ln a on the Figure 2 rows.

## Prose kept as context

x*(SiO2) = 28.56 ± 0.15, 9.90 ± 0.15, and 0 mol% at 2200, 2300, and 2400 K, cited from [1]. Equation (16) residuals 2.2%, 1.6%, and 0.8%. The k1,T, k2,T, and k3,T coefficients of equation (15) are not printed.

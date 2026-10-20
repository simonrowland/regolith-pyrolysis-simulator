# Shornikov 2007 redox note — extraction audit

The PDF `shornikov-2007-ca-aluminosilicate-activities` is not one article. pdfinfo Title is "Acrobat Distiller, Job 2", 3 A4 pages, created 2008-05-20. It is a slice of Experiment in Geosciences 2007 Volume 14 N 1.

PDF page 1 (journal page 37) opens with the closing figure of "Thermodynamic properties of CaO-Al2O3-SiO2 melts" (CaSiO3–CaAl2Si2O8 and CaAl2Si2O8–CaAl2O4 at 2000 K). That figure is DUPLICATE OF `shornikov-2007-cao-aluminosilicate-melts` and was not extracted again.

The same PDF then prints "Shornikov S.I. Redox condition influence on CaO-MgO-FeO-Al2O3-SiO2 melt evaporation" (journal pages 37–38). This note is not a duplicate of `shornikov-2008-redox-oxide-evaporation`. It prints two numbers the 2008 paper does not:

- αv(SiO2) = 0.04 for liquid silicon dioxide. The 2008 paper prints 0.4. Both are stored as what each paper prints. Neither is corrected.
- p(SiO) differs from chemically neutral evaporation by approximately 60 times (Fig. 1a). The 2008 paper prints a factor of 40 at 2000 K. This note does not print 2000 K on that sentence.

αv(Mg2SiO4) = 0.2 is the same number the 2008 paper prints for liquid forsterite. p(Al) approximately 10 times, p(Ca) 14 times, and p(Fe) more than 100 times are the same factors the 2008 text prints. p(Mg) 5 times is printed here; the 2008 text layer does not give a clean parallel sentence. No 2008 number was copied into this extract as if it were a 2007 measurement.

Journal page 39 is Shushkanova, Litvin, Dubrovinskaia, and Dubrovinsky on a garnet–pyrrhotite–carbonate melting study. Out of scope.

## Render checks

600 dpi pages 1 and 2 (`$TMPDIR/fix-renders/2007-p-1.png`, `2007-p-2.png`, 4959×7017).

Pressure factors, PDF page 1 crop x 2500–4666, y 5583–6500. The sentence reads: p(SiO) approximately 60 times (Fig. 1a); p(Al) approximately 10 times (Fig. 1b); p(Ca) 14 times; p(Mg) 5 times; p(Fe) more than 100 times. Word boxes in points, y from the top of the page: 60 at x 419.97–430.00, y 688.57–699.66; 10 at x 382.3, y 711.6; 14 at x 454.3, y 723.1; 5 at x 515.7, y 734.6; 100 at x 321.8, y 757.6.

Evaporation coefficients, PDF page 2 crop x 3000–4666, y 4458–4833. The sentence reads αv(SiO2) = 0.04 and αv(Mg2SiO4) = 0.2. The word 0.04 is at pt x 438.63–456.17, y 549.91–561.00 (px x 3655–3801, y 4583–4675). The word 0.2. is at pt x 388.05–403.06, y 561.43–572.52 (px x 3234–3359, y 4679–4771). The glyph is 0.04, not 0.4.

Figs. 1 and 2 were not digitized. No absolute pressure is printed next to the factors.

## Standard state

These rows are evaporation coefficients and pressure ratios, not oxide activities. The note does not state an activity reference. The pure-oxide-at-T inference was not used. Phase `l` on the coefficients is the printed liquid silicon dioxide and liquid forsterite. Phase `g` on the ratios is the gas named in p(SiO), p(Al), p(Ca), p(Mg), and p(Fe).

`quantity: partial_pressure` is the nearest observation type. No partial pressure in atm is printed, so no pressure number is stored. Migration's partial-pressure field list will not find one; the admitted number is `ratio_as_printed`.

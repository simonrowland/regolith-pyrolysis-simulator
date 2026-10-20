# Shornikov 2007 redox note — extraction audit

The PDF `shornikov-2007-ca-aluminosilicate-activities` is not one article. pdfinfo Title is "Acrobat Distiller, Job 2", 3 A4 pages, created 2008-05-20. It is a slice of Experiment in Geosciences 2007 Volume 14 N 1.

PDF page 1 (journal page 37) opens with the closing figure of "Thermodynamic properties of CaO-Al2O3-SiO2 melts" (CaSiO3–CaAl2Si2O8 and CaAl2Si2O8–CaAl2O4 at 2000 K). That figure is DUPLICATE OF `shornikov-2007-cao-aluminosilicate-melts` and was not extracted again.

The same PDF then prints "Shornikov S.I. Redox condition influence on CaO-MgO-FeO-Al2O3-SiO2 melt evaporation". The note runs through journal page 39: the left column finishes the discussion and the conclusions, and the references continue until reference 23. Shushkanova, Litvin, Dubrovinskaia, and Dubrovinsky begin in the right column after that reference. This note is not a duplicate of `shornikov-2008-redox-oxide-evaporation`.

The coefficients αv(SiO2) = 0.04 and αv(Mg2SiO4) = 0.2 are inside the paragraph that describes Hashimoto's graphite-container experiment, cited as Hashimoto [14, 15]. The next paragraph says the evaporation coefficient of liquid SiO2 that this note's author determined is close to a unit and cites [19]. That sentence does not print 0.04. The two numbers are `quoted_attributed` to Hashimoto [14, 15], with the citations as printed on journal page 39. The coefficient sentence does not print a temperature. The Hashimoto kinetics range 1873–2373 K is a separate sentence.

The 2008 paper prints αv(SiO2) = 0.4 and αv(Mg2SiO4) = 0.2 in the parallel sentence inside its Hashimoto [24, 25] paragraph, for the same liquids. Neither of those sentences prints a temperature. The silica digits disagree. The forsterite digit is 0.2 in both. The 2008 extract stores 0.4 as `author_estimate`. This extract does not choose a digit and does not edit the 2008 file.

p(SiO) differs from chemically neutral evaporation by approximately 60 times (Fig. 1a). The 2008 paper prints a factor of 40 at 2000 K. This note does not print 2000 K on that sentence. p(Al) approximately 10 times, p(Ca) 14 times, and p(Fe) more than 100 times are the same factors the 2008 text prints. p(Mg) 5 times is printed here; the 2008 text layer does not give a clean parallel sentence. Those five factors are this note's own comparisons and stay `author_derived`. That class queues until a measured lineage is present. No 2008 number was copied into this extract as a 2007 measurement.

Published page 39 prints the continuation "in the ptot(SiO2) value amount to 10^4-10^5". The 4 and the 5 are superscripts (word boxes: baseline 10 at pt y 42.79–53.88, superscript 4 at pt y 41.45–48.62). The sentence begins at the bottom of page 38, "Estimated discrepancies", in the contradiction with Hashimoto [14, 15]. The 2008 paper prints the same 10^4–10^5 for the estimated difference in p°tot(SiO2). No absolute pressure is printed.

## Render checks

600 dpi pages 1 and 2 (`$TMPDIR/fix-renders/2007-p-1.png`, `2007-p-2.png`, 4959×7017).

Pressure factors, PDF page 1 crop x 2500–4666, y 5583–6500. The sentence reads: p(SiO) approximately 60 times (Fig. 1a); p(Al) approximately 10 times (Fig. 1b); p(Ca) 14 times; p(Mg) 5 times; p(Fe) more than 100 times. Word boxes in points, y from the top of the page: 60 at x 419.97–430.00, y 688.57–699.66; 10 at x 382.3, y 711.6; 14 at x 454.3, y 723.1; 5 at x 515.7, y 734.6; 100 at x 321.8, y 757.6.

Evaporation coefficients, PDF page 2 crop x 3000–4666, y 4458–4833, and the right-column paragraph crop x 2500–4959, y 3600–6400. The sentence reads αv(SiO2) = 0.04 and αv(Mg2SiO4) = 0.2 after the description of Hashimoto's samples, weight changes, and comparison with equilibrium rates. The word 0.04 is at pt x 438.63–456.17, y 549.91–561.00 (px x 3655–3801, y 4583–4675). The word 0.2. is at pt x 388.05–403.06, y 561.43–572.52 (px x 3234–3359, y 4679–4771). The glyph is 0.04, not 0.4.

The ptot discrepancy is on PDF page 3, left column, first line. Words at pt x 221.70–251.58, y 41.45–53.88 (px x 1848–2130, y 345–449). Page 3 was rendered at 600 dpi (`$TMPDIR/fix-renders/2007-p-3-3.png`).

Figs. 1 and 2 were not digitized. No absolute pressure is printed next to the factors.

## Standard state

These rows are evaporation coefficients and pressure ratios, not oxide activities. The note does not state an activity reference. The pure-oxide-at-T inference was not used. Phase `l` on the coefficients is the printed liquid silicon dioxide and liquid forsterite. Phase `g` on the ratios is the gas named in p(SiO), p(Al), p(Ca), p(Mg), and p(Fe), and the vapor in ptot(SiO2). The coefficients are quoted from Hashimoto. They are not a pure oxide measured in this note's cell.

`quantity: partial_pressure` is the nearest observation type. No partial pressure in atm is printed, so no pressure number is stored. Migration's partial-pressure field list will not find one; the admitted number is `ratio_as_printed`.

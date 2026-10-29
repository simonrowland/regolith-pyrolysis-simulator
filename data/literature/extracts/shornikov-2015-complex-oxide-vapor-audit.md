# Shornikov and Yakovlev 2015 complex gas species — extraction audit

Source: Shornikov, S. I., and Yakovlev, O. I. (2015). Study of Complex Molecular Species in the Gas Phase over the CaO–MgO–Al2O3–TiO2–SiO2 System. Geochemistry International, Vol. 53, No. 8, pp. 690–699. DOI 10.1134/S0016702915080066. Original Russian text, Geokhimiya, 2015, No. 8, pp. 695–705. Received March 20, 2014; in final form April 30, 2014. Vernadsky Institute. PDF sha256 de86b1ae3468a4145712b7affa7bcef1fa17353aa035e7d07c9fef00ea3e3e7a, 10 pages.

## Duplicate verdict

Not a duplicate. The same year already has three other Shornikov extracts, and each is a different article:

- `shornikov-2015-cai-vaporization-coefficients` is Geochemistry International 53(12), 1080–1089.
- `shornikov-2015-cai-mineral-thermodynamics` is the 46th LPSC abstract on CAI mineral thermodynamics.
- `shornikov-2015-cai-mineral-thermodynamics-metsoc` is the Meteoritical Society abstract of that LPSC paper.

This paper is Geochemistry International 53(8), 690–699.

## Page map

Running headers: PDF page 2 prints 691, PDF page 4 prints 693, PDF page 6 prints 695, PDF page 7 prints 696, PDF page 8 prints 697. Published page = 689 + PDF page.

## Table 1

Published page 691. A literature inventory. Each temperature range belongs to the reference in that row. Not scored. pdftotext and MinerU agree on the Al2O3–SiO2 compound cell: Al2SiO5, AlSi2O5, Al3Si2O13, 1700–2550 K, Shornikov et al. 1994, Shornikov et al. 2000, Shornikov and Archakov 2000. The prose elsewhere prints mullite as Al6Si2O13. The table cell Al3Si2O13 is kept as printed and is not corrected. The footnote says compounds in bold type were identified in Ca–Al inclusions. Which cells are bold was not transcribed, because the rows are not scored.

## Table 2

Published page 697. ΔHr at 2000 K. Caption: Glushko et al., 1978–1982; Shornikov et al., 1994; Shornikov, 2001, 2013. Footnote * is the Glushko uncertainty. Footnote ** is the least-squares uncertainty. pdftotext and the MinerU HTML table agree on all twenty cells. No digit was changed. The cells are one context row. `OBSERVATION_TYPES` has no reaction-enthalpy type, so they are not a `gibbs_table`. The least-squares parents are not published in this paper, and none was invented.

## Standard state

No activity row is stored, so migration does not call `_standard_state_from_extract_text`.

Equation (16), published pages 692–693, integrates ln a[SiO2] from x[Al2O3] = 0. The paragraph attributes the Al2O3–SiO2 example to Shornikov et al. 1994 and Shornikov and Archakov 2000. The paper does not state the phase of that limit. Expected phase: unknown. The sentence "a[Al2O3] can be written in an analogous manner" (published page 694) adds no second anchor and no phase.

## Figures

110 dpi renders of PDF pages 4, 6, and 7 (published 693, 695, 696). No tick calibration, because no point was read.

Fig. 1. Panels (a), (b), (d), and (e) are continuous curves. Panels (c) and (f) have open and filled circles on the activity curves. The caption says (c, f) are the experimentally determined activities and (a, d) and (b, e) are calculated from them, at 2300 K (Al2O3–SiO2; Shornikov et al. 1994; Shornikov and Archakov 2000) and 2100 K (CaO–SiO2; Stolyarova et al. 1991). Refused. The order-of-magnitude sentence (curves 6 and 9) describes those calculated panels.

Fig. 2. Six panels, continuous curves, no markers, 2000 K, calculated from Shornikov et al. 1994, Shornikov 2001, and Shornikov 2013. Refused.

Fig. 3. Filled circles lie on each labelled line. The caption says the results are the Knudsen effusion measurements of Shornikov et al. 1994, Shornikov 2001, and Shornikov 2013, from a Mo cell. Refused as a series. The six-fold and sixteen-fold sentence is explicitly "According to (Shornikov et al., 1994)" at 1933–2033 K. The factor of 10^2 to 10^3 over 200–300 degrees describes the same cited figure.

## Other prose left unscored

- Chase (1998): mullite 2180 ± 10 K, spinel 2388 ± 15 K, perovskite 2243 ± 5 K, quoted so that 2000 K lies below those melting points and the evaporation is called crystalline. Cited, not measured here.
- Kulikov (1969): MgO dissociation degree 0.9 to 0.3 at 1400–3000 K, and CaO 0.4 to 0.25 at 1800–3200 K. Cited.
- Conclusions (1)–(4) on published pages 698–699 are qualitative.

## Admitted

None. `observations` is empty on SiO2 and on Al2O3.

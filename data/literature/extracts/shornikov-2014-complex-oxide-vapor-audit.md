# Shornikov and Yakovlev 2014 LPSC 1624 — extraction audit

Source: Shornikov, S. I., and Yakovlev, O. I. (2014). A Study of Complex Gaseous Oxides over the CaO–MgO–Al2O3–TiO2–SiO2 Melts. 45th Lunar and Planetary Science Conference, abstract 1624. Two pages. PDF sha256 be60bd75c6223243d72831c61ec50decb339ebc2ea1bd7c0922ded2a18900126. pdfinfo title 1624.PDF. No DOI.

## Duplicate verdict

DUPLICATE OF `shornikov-2015-complex-oxide-vapor`.

Evidence: same two authors, Vernadsky Institute, Kosygin st. 19. The abstract detects (CaAlO), (CaSiO3), (MgAlO), (AlSiO), and (CaTiO3) by Knudsen effusion mass spectrometry at 1500–2500 K and says their quantity does not exceed 1% of the total concentration. The journal paper, Geochemistry International 53(8), 690–699, received 20 March 2014, prints the same bound ("no more than 1% of the total concentration") and the same 1500–2500 K span of the earlier measurements. References [2] and [3] of the abstract are Shornikov et al., Rapid Communications in Mass Spectrometry 8, 478–480 (1994) and Shornikov and Archakov, Russian Journal of Physical Chemistry 74, 684–688 (2000), which the journal also uses. The abstract prints pages 684–688 for that 2000 paper.

This is not `shornikov-2014-ca-al-evaporation-coefficients` (Meteoritical Society abstract 5018) and not `shornikov-2014-sodium-silicate-model`.

## What the journal text does not print

- Fig. 1 is calculated at 2200 K over Al2O3–SiO2 melts from [2, 3]. The journal Fig. 1 labels that system at 2300 K and labels CaO–SiO2 at 2100 K.
- Fig. 3 is calculated over mullite at 2200 K from [2, 3]. The journal Fig. 2 labels the mullite panel at 2000 K.
- Fig. 2 attributes the spinel temperature curves to Shornikov 2003, Herald of the Earth Sciences Department of RAS, 21, No. 1. The journal spinel temperature panel cites Shornikov 2001.

These are labels and a citation. They are not new measured points. The 1% bound and the 1500–2500 K span are already in the journal extract and are not scored again.

## Figures

Fig. 1 was re-rendered at 600 dpi. The decision-table coordinates are `pdftoppm -r 600 -f 1 -l 1 -x 2100 -y 600 -W 2100 -H 1800`. Page pixel = crop pixel + (2100, 600). Circles past crop x = 2100 were measured on `pdftoppm -r 600 -f 1 -l 1 -x 1900 -y 400 -W 2800 -H 2200` and converted by subtracting (200, 200). A circle is a local maximum of the distance transform on a pure-color mask (one channel above 200 and the other two under 40; black is all channels under 45). Maxima within 22 px are one circle. The joining stroke has radius about 6 px and is not a circle. No log p was read. The curves are calculated at 2200 K from [2, 3], so the circles stay context.

There is no square. The earlier sentence that line 1 ends in a filled square was wrong. Every core on curves 1–4 is a filled circle. Curve 5, the purple total-pressure line, has no circle core.

Stroke peaks kept out of the table, radius under the circle cut: green 11, red 13, blue 10, black 8. The black numeral 4 above the curve is at cx 1647, cy 1430, dt 6.4, and is refused as the series glyph.

| id | series | species | cx | cy | dt px | decision |
| --- | --- | --- | --- | --- | --- | --- |
| fig1-s1-01 | 1 | SiO | 971 | 751 | 21.0 | context, not read (calculated) |
| fig1-s1-02 | 1 | SiO | 1050 | 733 | 19.1 | context, not read (calculated) |
| fig1-s1-03 | 1 | SiO | 1116 | 709 | 19.3 | context, not read (calculated) |
| fig1-s1-04 | 1 | SiO | 1205 | 683 | 19.0 | context, not read (calculated) |
| fig1-s1-05 | 1 | SiO | 1282 | 664 | 15.7 | context, not read (calculated) |
| fig1-s1-06 | 1 | SiO | 1345 | 658 | 18.8 | context, not read (calculated) |
| fig1-s1-07 | 1 | SiO | 1422 | 646 | 16.1 | context, not read (calculated) |
| fig1-s1-08 | 1 | SiO | 1461 | 640 | 15.8 | context, not read (calculated) |
| fig1-s1-09 | 1 | SiO | 1504 | 642 | 20.1 | context, not read (calculated) |
| fig1-s1-10 | 1 | SiO | 1552 | 636 | 19.3 | context, not read (calculated) |
| fig1-s1-11 | 1 | SiO | 1602 | 631 | 18.2 | context, not read (calculated) |
| fig1-s1-12 | 1 | SiO | 1651 | 626 | 17.0 | context, not read (calculated) |
| fig1-s1-13 | 1 | SiO | 1707 | 622 | 16.0 | context, not read (calculated) |
| fig1-s1-14 | 1 | SiO | 1766 | 618 | 15.0 | context, not read (calculated) |
| fig1-s1-15 | 1 | SiO | 1829 | 614 | 15.0 | context, not read (calculated) |
| fig1-s1-16 | 1 | SiO | 1894 | 617 | 20.0 | context, not read (calculated) |
| fig1-s1-17 | 1 | SiO | 1961 | 614 | 19.4 | context, not read (calculated) |
| fig1-s1-18 | 1 | SiO | 2029 | 611 | 19.0 | context, not read (calculated) |
| fig1-s1-19 | 1 | SiO | 2101 | 609 | 19.1 | context, not read (calculated); past the first crop |
| fig1-s1-20 | 1 | SiO | 2171 | 607 | 19.0 | context, not read (calculated); past the first crop |
| fig1-s1-21 | 1 | SiO | 2248 | 606 | 18.2 | context, not read (calculated); past the first crop |
| fig1-s2-01 | 2 | Al | 834 | 990 | 11.0 | context, not read (calculated) |
| fig1-s2-02 | 2 | Al | 970 | 989 | 21.0 | context, not read (calculated) |
| fig1-s2-03 | 2 | Al | 1049 | 1012 | 21.2 | context, not read (calculated) |
| fig1-s2-04 | 2 | Al | 1114 | 1053 | 21.0 | context, not read (calculated) |
| fig1-s2-05 | 2 | Al | 1205 | 1077 | 20.9 | context, not read (calculated) |
| fig1-s2-06 | 2 | Al | 1282 | 1082 | 20.9 | context, not read (calculated) |
| fig1-s2-07 | 2 | Al | 1345 | 1105 | 21.0 | context, not read (calculated) |
| fig1-s2-08 | 2 | Al | 1419 | 1109 | 21.4 | context, not read (calculated) |
| fig1-s2-09 | 2 | Al | 1460 | 1111 | 21.1 | context, not read (calculated) |
| fig1-s2-10 | 2 | Al | 1504 | 1132 | 21.0 | context, not read (calculated) |
| fig1-s2-11 | 2 | Al | 1551 | 1135 | 20.9 | context, not read (calculated) |
| fig1-s2-12 | 2 | Al | 1601 | 1138 | 21.2 | context, not read (calculated) |
| fig1-s2-13 | 2 | Al | 1653 | 1142 | 21.0 | context, not read (calculated) |
| fig1-s2-14 | 2 | Al | 1710 | 1145 | 21.0 | context, not read (calculated) |
| fig1-s2-15 | 2 | Al | 1769 | 1150 | 21.0 | context, not read (calculated) |
| fig1-s2-16 | 2 | Al | 1830 | 1154 | 20.9 | context, not read (calculated) |
| fig1-s2-17 | 2 | Al | 1894 | 1178 | 20.9 | context, not read (calculated) |
| fig1-s2-18 | 2 | Al | 1961 | 1183 | 21.1 | context, not read (calculated) |
| fig1-s2-19 | 2 | Al | 2029 | 1189 | 21.0 | context, not read (calculated) |
| fig1-s2-20 | 2 | Al | 2100 | 1196 | 21.8 | context, not read (calculated); past the first crop |
| fig1-s2-21 | 2 | Al | 2172 | 1203 | 21.9 | context, not read (calculated); past the first crop |
| fig1-s2-22 | 2 | Al | 2245 | 1220 | 21.9 | context, not read (calculated); past the first crop |
| fig1-s3-00 | 3 | O2 | 834 | 814 | 11.0 | context, not read (calculated); clipped by the y-axis |
| fig1-s3-01 | 3 | O2 | 970 | 815 | 21.0 | context, not read (calculated) |
| fig1-s3-02 | 3 | O2 | 1049 | 789 | 21.0 | context, not read (calculated) |
| fig1-s3-03 | 3 | O2 | 1114 | 747 | 14.8 | context, not read (calculated) |
| fig1-s3-04 | 3 | O2 | 1202 | 720 | 15.0 | context, not read (calculated) |
| fig1-s3-05 | 3 | O2 | 1282 | 714 | 21.0 | context, not read (calculated) |
| fig1-s3-06 | 3 | O2 | 1345 | 695 | 16.0 | context, not read (calculated) |
| fig1-s3-07 | 3 | O2 | 1419 | 689 | 21.2 | context, not read (calculated) |
| fig1-s3-08 | 3 | O2 | 1460 | 689 | 21.2 | context, not read (calculated) |
| fig1-s3-09 | 3 | O2 | 1498 | 674 | 10.0 | context, not read (calculated) |
| fig1-s3-10 | 3 | O2 | 1549 | 672 | 13.0 | context, not read (calculated) |
| fig1-s3-11 | 3 | O2 | 1601 | 669 | 16.6 | context, not read (calculated) |
| fig1-s3-12 | 3 | O2 | 1653 | 666 | 19.3 | context, not read (calculated) |
| fig1-s3-13 | 3 | O2 | 1710 | 664 | 21.2 | context, not read (calculated) |
| fig1-s3-14 | 3 | O2 | 1768 | 664 | 21.2 | context, not read (calculated) |
| fig1-s3-15 | 3 | O2 | 1830 | 664 | 21.2 | context, not read (calculated) |
| fig1-s3-16 | 3 | O2 | 1897 | 650 | 10.2 | context, not read (calculated) |
| fig1-s3-17 | 3 | O2 | 1956 | 648 | 12.0 | context, not read (calculated) |
| fig1-s3-18 | 3 | O2 | 2029 | 647 | 13.6 | context, not read (calculated) |
| fig1-s3-19 | 3 | O2 | 2100 | 646 | 15.8 | context, not read (calculated); past the first crop |
| fig1-s3-20 | 3 | O2 | 2172 | 645 | 16.8 | context, not read (calculated); past the first crop |
| fig1-s3-21 | 3 | O2 | 2243 | 644 | 17.0 | context, not read (calculated); past the first crop |
| fig1-s4-01 | 4 | AlSiO | 971 | 1475 | 21.0 | context, not read (calculated) |
| fig1-s4-02 | 4 | AlSiO | 1049 | 1477 | 21.2 | context, not read (calculated) |
| fig1-s4-03 | 4 | AlSiO | 1114 | 1495 | 21.0 | context, not read (calculated) |
| fig1-s4-04 | 4 | AlSiO | 1206 | 1492 | 21.2 | context, not read (calculated) |
| fig1-s4-05 | 4 | AlSiO | 1282 | 1475 | 21.1 | context, not read (calculated) |
| fig1-s4-06 | 4 | AlSiO | 1345 | 1494 | 21.1 | context, not read (calculated) |
| fig1-s4-07 | 4 | AlSiO | 1419 | 1484 | 21.0 | context, not read (calculated) |
| fig1-s4-08 | 4 | AlSiO | 1460 | 1480 | 21.4 | context, not read (calculated) |
| fig1-s4-09 | 4 | AlSiO | 1504 | 1507 | 21.4 | context, not read (calculated) |
| fig1-s4-10 | 4 | AlSiO | 1551 | 1504 | 21.5 | context, not read (calculated) |
| fig1-s4-11 | 4 | AlSiO | 1601 | 1501 | 21.9 | context, not read (calculated) |
| fig1-s4-12 | 4 | AlSiO | 1653 | 1499 | 21.0 | context, not read (calculated) |
| fig1-s4-13 | 4 | AlSiO | 1710 | 1497 | 21.4 | context, not read (calculated) |
| fig1-s4-14 | 4 | AlSiO | 1769 | 1496 | 21.4 | context, not read (calculated) |
| fig1-s4-15 | 4 | AlSiO | 1830 | 1495 | 21.2 | context, not read (calculated) |
| fig1-s4-16 | 4 | AlSiO | 1894 | 1528 | 21.4 | context, not read (calculated) |
| fig1-s4-17 | 4 | AlSiO | 1961 | 1529 | 21.4 | context, not read (calculated) |
| fig1-s4-18 | 4 | AlSiO | 2029 | 1532 | 21.0 | context, not read (calculated) |
| fig1-s4-19 | 4 | AlSiO | 2100 | 1536 | 21.4 | context, not read (calculated); past the first crop |
| fig1-s4-20 | 4 | AlSiO | 2172 | 1541 | 21.5 | context, not read (calculated); past the first crop |
| fig1-s4-21 | 4 | AlSiO | 2245 | 1558 | 21.5 | context, not read (calculated); past the first crop |

The first red circle, fig1-s2-01, is clipped by the y-axis.
The first blue circle, fig1-s3-00, is clipped by the same y-axis.

Round 2 first scanned the crop that ends at page x = 4200. That crop holds the y-axis clip, fig1-s3-00 at (834, 814), radius 11, and it cuts off the circles that continue to the 100 mol % frame. The census that decides is the wider render, `pdftoppm -r 600 -f 1 -l 1 -x 1900 -y 400 -W 2800 -H 2200`. Coordinates below stay in the first crop's frame: wide pixel minus (200, 200). The mask is one channel above 180 and the other two under 80; black is all three under 60. A strict mask (200 / 40, black under 45) returns the same cores. A local maximum of the distance transform with radius at least 8 px is a core. Two maxima are one disk when their separation is under max(12 px, 0.55 of the smaller radius plus 0.5 of the larger); none of these circle centres merged. Counts are SiO 21, Al 22, O2 22, AlSiO 21, total-pressure 0.

| source | SiO | Al | O2 | AlSiO |
| --- | --- | --- | --- | --- |
| round-1 extract, crop ended at page x 4200 | 18 | 19 | 18 | 18 |
| sol per-colour scan, same crop | 18 | 19 | 19 | 18 |
| grok full-page scan | 21 | 22 | 22 | 21 |
| this wide scan | 21 | 22 | 22 | 21 |

Sol's extra O2 circle is fig1-s3-00, the disk the y-axis clips. The other difference is three circles on each curve at crop x about 2100, 2172, and 2245 (page x about 4200, 4272, and 4345). They sit between the first crop's right edge and the 100 mol % frame. Sol's window flagged the border core as an edge and could not see the two beyond it. Grok's page coordinates land on these cores within 4 px, and the pixel at each centre is that series' colour. The radii are 15.8 to 21.9, the same as the circles already listed. Purple has no core. The joining stroke stays under 8 px. The black numeral 4 stays the refused glyph at (1647, 1430), radius 6.4. The narrow-crop green core at (1442, 638), radius 11, is the overlap of fig1-s1-07 and fig1-s1-08, whose centres are 39 px apart; this scan does not keep it as its own disk.

### Fig. 1 wide-crop core list

Every core of radius at least 8 px inside the figure window. cx and cy are the first crop's frame.

| cx | cy | r px | colour | matched row |
| --- | --- | --- | --- | --- |
| 970 | 751 | 21.95 | green | fig1-s1-01 |
| 1049 | 733 | 19.92 | green | fig1-s1-02 |
| 1115 | 709 | 20.25 | green | fig1-s1-03 |
| 1204 | 683 | 19.65 | green | fig1-s1-04 |
| 1283 | 664 | 16.49 | green | fig1-s1-05 |
| 1345 | 658 | 19.65 | green | fig1-s1-06 |
| 1421 | 646 | 17.12 | green | fig1-s1-07 |
| 1456 | 640 | 16.0 | green | fig1-s1-08 |
| 1503 | 641 | 21.0 | green | fig1-s1-09 |
| 1551 | 636 | 20.62 | green | fig1-s1-10 |
| 1599 | 631 | 19.0 | green | fig1-s1-11 |
| 1653 | 627 | 18.0 | green | fig1-s1-12 |
| 1712 | 622 | 16.76 | green | fig1-s1-13 |
| 1767 | 618 | 16.0 | green | fig1-s1-14 |
| 1829 | 614 | 16.0 | green | fig1-s1-15 |
| 1893 | 617 | 21.02 | green | fig1-s1-16 |
| 1960 | 614 | 20.25 | green | fig1-s1-17 |
| 2029 | 611 | 19.92 | green | fig1-s1-18 |
| 2101 | 609 | 19.1 | green | fig1-s1-19 |
| 2171 | 607 | 19.0 | green | fig1-s1-20 |
| 2248 | 606 | 18.25 | green | fig1-s1-21 |
| 834 | 989 | 12.0 | red | fig1-s2-01 |
| 970 | 989 | 21.93 | red | fig1-s2-02 |
| 1048 | 1012 | 22.14 | red | fig1-s2-03 |
| 1114 | 1053 | 21.93 | red | fig1-s2-04 |
| 1205 | 1077 | 21.93 | red | fig1-s2-05 |
| 1282 | 1082 | 22.14 | red | fig1-s2-06 |
| 1345 | 1105 | 21.84 | red | fig1-s2-07 |
| 1418 | 1109 | 21.93 | red | fig1-s2-08 |
| 1459 | 1111 | 21.93 | red | fig1-s2-09 |
| 1503 | 1132 | 21.84 | red | fig1-s2-10 |
| 1551 | 1136 | 21.84 | red | fig1-s2-11 |
| 1600 | 1138 | 21.84 | red | fig1-s2-12 |
| 1653 | 1142 | 22.02 | red | fig1-s2-13 |
| 1709 | 1145 | 21.93 | red | fig1-s2-14 |
| 1768 | 1150 | 21.93 | red | fig1-s2-15 |
| 1830 | 1154 | 22.02 | red | fig1-s2-16 |
| 1894 | 1178 | 21.84 | red | fig1-s2-17 |
| 1960 | 1183 | 21.93 | red | fig1-s2-18 |
| 2029 | 1189 | 21.93 | red | fig1-s2-19 |
| 2100 | 1196 | 21.84 | red | fig1-s2-20 |
| 2172 | 1203 | 21.93 | red | fig1-s2-21 |
| 2245 | 1220 | 21.95 | red | fig1-s2-22 |
| 834 | 815 | 12.0 | blue | fig1-s3-00 |
| 970 | 814 | 21.93 | blue | fig1-s3-01 |
| 1048 | 789 | 21.84 | blue | fig1-s3-02 |
| 1112 | 747 | 15.0 | blue | fig1-s3-03 |
| 1205 | 721 | 15.81 | blue | fig1-s3-04 |
| 1282 | 714 | 21.84 | blue | fig1-s3-05 |
| 1343 | 695 | 16.0 | blue | fig1-s3-06 |
| 1418 | 689 | 21.93 | blue | fig1-s3-07 |
| 1459 | 689 | 21.93 | blue | fig1-s3-08 |
| 1501 | 675 | 11.0 | blue | fig1-s3-09 |
| 1549 | 672 | 14.0 | blue | fig1-s3-10 |
| 1599 | 669 | 17.0 | blue | fig1-s3-11 |
| 1652 | 666 | 20.0 | blue | fig1-s3-12 |
| 1709 | 664 | 21.93 | blue | fig1-s3-13 |
| 1768 | 664 | 21.93 | blue | fig1-s3-14 |
| 1830 | 664 | 21.93 | blue | fig1-s3-15 |
| 1894 | 651 | 11.05 | blue | fig1-s3-16 |
| 1960 | 649 | 13.0 | blue | fig1-s3-17 |
| 2027 | 647 | 14.0 | blue | fig1-s3-18 |
| 2100 | 646 | 15.81 | blue | fig1-s3-19 |
| 2172 | 645 | 16.76 | blue | fig1-s3-20 |
| 2243 | 644 | 17.0 | blue | fig1-s3-21 |
| 970 | 1475 | 21.59 | black | fig1-s4-01 |
| 1048 | 1477 | 21.54 | black | fig1-s4-02 |
| 1114 | 1495 | 21.26 | black | fig1-s4-03 |
| 1205 | 1493 | 21.84 | black | fig1-s4-04 |
| 1282 | 1474 | 21.84 | black | fig1-s4-05 |
| 1345 | 1494 | 21.59 | black | fig1-s4-06 |
| 1418 | 1484 | 21.21 | black | fig1-s4-07 |
| 1459 | 1480 | 21.63 | black | fig1-s4-08 |
| 1503 | 1507 | 21.93 | black | fig1-s4-09 |
| 1551 | 1504 | 21.93 | black | fig1-s4-10 |
| 1601 | 1501 | 21.93 | black | fig1-s4-11 |
| 1653 | 1499 | 21.59 | black | fig1-s4-12 |
| 1709 | 1497 | 21.93 | black | fig1-s4-13 |
| 1768 | 1496 | 21.93 | black | fig1-s4-14 |
| 1830 | 1495 | 21.54 | black | fig1-s4-15 |
| 1894 | 1528 | 21.93 | black | fig1-s4-16 |
| 1961 | 1529 | 21.59 | black | fig1-s4-17 |
| 2029 | 1532 | 21.47 | black | fig1-s4-18 |
| 2100 | 1536 | 21.38 | black | fig1-s4-19 |
| 2172 | 1541 | 21.47 | black | fig1-s4-20 |
| 2245 | 1558 | 21.54 | black | fig1-s4-21 |



Fig. 2. Four lines of filled circles versus 1/T. Refused as a series. The caption assigns them to the Knudsen effusion data of [4], Shornikov 2003. They were not given per-marker rows.


Fig. 3. Five continuous curves, no markers. Refused. Calculated at 2200 K from [2, 3].

## Standard state

Equation (8) uses a(Al2O3) and a(SiO2) and does not name a solid or liquid endmember. No activity row is stored. Expected migration phase: unknown.

## Admitted

None.

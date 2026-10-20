# Shornikov 2017 spinel thermodynamics — extraction audit

Source: Shornikov, S. I. (2017), “Thermodynamic Properties of Spinel MgAl2O4: A Mass Spectrometric Study,” *Russian Journal of Physical Chemistry A* 91(2), 287–294. DOI 10.1134/S0036024417020303. Extract: [shornikov-2017-spinel-thermodynamics.yaml](shornikov-2017-spinel-thermodynamics.yaml).

This is not the evaporation paper already extracted as `shornikov-2017-spinel-evaporation` (91(1), 10–16, DOI 10.1134/S0036024417010241). The title, volume number, and DOI differ. PDF page 7, published page 293, prints reference [1] as “S. I. Shornikov, Zh. Fiz. Khim. 90 (2016, in press).” The identity with the evaporation paper is an inference from the 1851–2089 K span, not the citation this paper prints.

## Tables

MinerU `convert` of the content-list JSON wrote `p002-t01` (PDF page 2, published 288) and `p004-t02` (PDF page 4, published 290). `emit` was given only the this-work rows: Table 1 CSV row 29 and Table 2 CSV row 14. It wrote five cells and skipped nothing. `pdftotext -layout` agrees with MinerU on every digit of those cells.

The ± cells are not emit traps. Emit stored the whole cell, for example `-12.02 ± 1.14`, and flagged `non_numeric_in_numeric_column`. After the render check the committed rows split the cell into `value` and `uncertainty`, and that flag was removed. The confidence level is not stated.

Table 1’s temperature cell is the range `1851–2298`, so it was not mapped as `T_K`. The committed rows carry `T_range_K: [1851, 2298]` and an unknown point temperature. No midpoint is stored.

Table 2’s four MgO body rows were tagged `header` because of a rowspan. In the scratch draft those cells were retyped `body` so `rows: [14]` could select the this-work row without emit forcing the MgO numbers out. The emitted column headers still contained those MgO cells. The 600 dpi render shows the real headers are `T_melt`, `ΔH_melt`, and `ΔS_melt`. The committed locators use those headers.

The Table 1 minus sign is U+2013 in pdftotext (`–12.02`). MinerU stored an ASCII hyphen. The render shows a minus. `value` is `-12.02`. `value_as_printed` keeps the en dash.

`transition_temperature` is read from `T_K`. Emit had put `2420 ± 25` on `values.value`. The committed melting-point row puts `2420` on `T_K` and `T_m_K`, with `property_kind: melting_point`, and keeps `2420 ± 25` as `value_as_printed`.

The Table 2 caption prints `J/(mol K)`. The melting-entropy row stores `J/mol K`, the same unit, because the migrator’s unit token does not accept the parentheses. `units_as_printed` keeps the caption spelling. Digits are unchanged.

## Render checks

pdftotext `-bbox` on this file uses a top-left origin. Page renders are 600 dpi (600/72 px per point). An earlier crop that flipped that origin hit the top of each page and was discarded.

| PDF page | Published page | What was read | Crop |
|---:|---:|---|---|
| 2 | 288 | Table 1 this-work row: Knudsen mass spectrometry, 1851–2298, −12.02 ± 1.14, 5.03 ± 0.56, This work | top-origin band covering that row and its neighbours |
| 4 | 290 | Table 2 MgAl2O4 block. The last row before Al2O3 is 2420 ± 25, 55.81 ± 4.62, 23.06 ± 1.73, This work | the whole Table 2 body |
| 6 | 292 | Prose: ΔH_T = 43.79 ± 4.84 kJ/mol and ΔS_T = 28.09 ± 1.64 J/(mol K), liquid state, equation (19) | the paragraph that prints those constants |
| 5 | 291 | Figure 4 series 8. Open right-pointing triangles with one left stroke. Eight centres on panel (a) and seven on panel (b). The 1861 K and 2103 K glyphs were compared at 1200 dpi and refused | the figure crop, each stored centre, and the 1200 dpi crops of 2103 K, 1861 K, 2162 K, and 2174 K |
| 3 | 289 | Figure 2 series 16. Nine open right-pointing triangles. No further series-16 centre was found in the sweep | the high-T chain, the low-T squares, the up-triangle band, the legend, and a 1200 dpi crop of the 2210 K point |

Neighbour rows on pages 2 and 4 match pdftotext, so the this-work row is not a shifted literature row.

A consistency check, not a new observation: 43.79 − (−12.02) = 55.81 and 28.09 − 5.03 = 23.06, and 55.81 / 23.06 is 2420 K to the printed kelvin. That is equations (20)–(22).

## What was admitted

Five scored rows, all `measured_reduced`, stoichiometric MgAl2O4 (MgO 0.5, Al2O3 0.5). Total pressure and fO2 are not printed.

- Table 1 ΔH_T = −12.02 ± 1.14 kJ/mol, crystalline spinel, 1851–2298 K. Quantity `delta_fH`.
- Table 2 ΔH_melt = 55.81 ± 4.62 kJ/mol at the printed melting temperature 2420 K. Quantity `heat_of_fusion`.
- Table 2 ΔS_melt = 23.06 ± 1.73 J/(mol K) at 2420 K. Quantity `entropy_of_fusion`.
- Table 2 T_melt = 2420 ± 25 K. Quantity `transition_temperature`, `property_kind: melting_point`.
- Prose liquid-state ΔH_T = 43.79 ± 4.84 kJ/mol. Quantity `delta_fH`, phase `l`. The sentence does not print a temperature, so the point temperature stays unknown.

Fifteen Figure 4 activities, `figure_only`, stoichiometric spinel. Quantity `activity` on type `activity_coefficient`. Uncertainty is the read, not a printed ±. Eight are panel (a) and seven are panel (b).

- Panel (a) a(MgO): 0.276 at 1917 K, 0.328 at 2008 K, 0.326 at 2051 K, 0.342 at 2101 K, 0.368 at 2161 K, 0.388 at 2218 K, 0.406 at 2257 K, 0.425 at 2304 K. Read uncertainty ±0.010 and ±5.4 K.
- Panel (b) a(Al2O3): 0.236 at 1917 K, 0.232 at 1954 K, 0.223 at 2009 K, 0.211 at 2162 K, 0.202 at 2220 K, 0.203 at 2259 K, 0.200 at 2306 K. Read uncertainty ±0.005 and ±5.4 K.

The 2008 K centre is crop (1360.5, 1059.0), page pixel (3920, 1494), unrounded 2008.03 K and 0.3281. A neighbour meets the left side. The centre is one white interior. The 2051 K centre is crop (1440.0, 1061.5), page pixel (4000, 1496), unrounded 2051.18 K and 0.3257. A second outline runs along the lower edge. The centre is open. The 2101 K centre is crop (1532.0, 1044.5), page pixel (4092, 1480), unrounded 2101.11 K and 0.3424. The bar that extends past a triangle belongs to the marker above. The centre is open.

The 1861 K and 2103 K glyphs were compared on a 1200 dpi render of PDF page 5 with the 2162 K series-8 triangle and the 2174 K series 5 or 6 glyph. The 2162 K triangle has one left stroke. The 2174 K bar extends past the top and bottom vertices. The 1861 K glyph, crop (1090.5, 1132.5), has two left strokes. At 1200 dpi the white gap is 20 px, which is 10 px at 600 dpi, and the strokes join for 8 rows at the top and 8 rows at the bottom. The 2103 K glyph, crop (1535.5, 2329.5), is the same drawing: gap 20 px at 1200 dpi, joined for 4 rows at the top and 4 rows at the bottom. Neither matches the single-stroke open triangle. Both stay refused. The strokes of those two glyphs stop at the corners. That corner closure is not the single-stroke pattern.

The 1954 K centre is crop (1260.5, 2297.5), page pixel (3820, 2732), unrounded 1953.76 K and 0.2325. A neighbouring outline meets the upper and lower corners across a 16 px gap. That contact is two rows, not a bar that extends past the triangle. The 2009 K centre is crop (1362.5, 2317.0), page pixel (3922, 2752), unrounded 2009.12 K and 0.2229. The doubled outline stops at the corners. The centre is open.

Nine Figure 2 Gibbs energies, `figure_only`, quantity `delta_fG`, crystalline spinel. Read uncertainty ±0.30 kJ/mol and ±10 K.

- −21.15 kJ/mol at 1863 K, −22.07 at 1950 K, −22.32 at 2004 K, −22.41 at 2050 K, −22.70 at 2102 K, −22.92 at 2160 K, −23.47 at 2210 K, −23.39 at 2252 K, −23.61 at 2302 K.

The 1863 K centre is crop (1477.4, 958.6), page pixel (2818, 1394), unrounded 1862.89 K and −21.154 kJ/mol. The fit line passes below the centre. The 1950 K centre is crop (1576.4, 1002.1), page pixel (2917, 1437), unrounded 1949.65 K and −22.071 kJ/mol. The centre is in the open interior.

The 2210 K point was the disagreement between the two confirm reviews. Sol called it readable. The other review called the cut interior a fair refusal. Both crops locate the same centre. At 600 dpi the midpoint is crop (1873.1, 1068.5), page pixel (3214, 1504), 2209.62 K and −23.469 kJ/mol. At 1200 dpi the white centroid is page pixel (6428, 3007), which falls at crop (1873.0, 1068.2) in the 600 dpi frame, 2209.49 K and −23.464 kJ/mol. The outline is a complete open right triangle. The next outline meets the tip and does not hide the centre. The point is stored.

The 2252 K point is the marker the earlier note called a gap near crop (1926, 1065). The 600 dpi midpoint is crop (1921.5, 1064.6), page pixel (3263, 1500), 2252.04 K and −23.387 kJ/mol. The 1200 dpi white centroid is page pixel (6521, 2999), crop (1919.6, 1064.5) in the 600 dpi frame. The two reads differ by about 2 px.

## Marker table

Every this-work candidate on the 600 dpi render. Series 8 is an open right-pointing triangle whose vertical stroke does not extend past the triangle. A doubled outline that only meets at the corners is series 8. Series 16 is the open right-pointing triangle on Figure 2. Outline contact is not a reason to refuse. A stored row uses the stroke-centre named in the extract. A refused row uses the crop that locates that symbol. Temperatures of refused rows are the tick calibration applied to that crop, rounded to the kelvin.

Figure 4 panel (a), a(MgO). Read uncertainty ±5.4 K and ±0.010.

| Approx T | Crop | Decision |
|---:|---|---|
| 1425 K | (286, 741) | Refused. Up-triangle, series 1–4. |
| 1522 K | (465, 692) | Refused. Up-triangle, series 1–4. |
| 1576 K | (565, 631) | Refused. Up-triangle, series 1–4. |
| 1643 K | (688, 1318) | Refused. Up-triangle, series 1–4. |
| 1672 K | (741, 751) | Refused. Up-triangle, series 1–4. |
| 1692 K | (779, 1272) | Refused. Up-triangle, series 1–4. |
| 1742 K | (870, 1304) | Refused. Up-triangle, series 1–4. |
| 1789 K | (958, 1288) | Refused. Up-triangle, series 1–4. |
| 1843 K | (1057, 1263) | Refused. Two up-triangles, series 1–4. |
| 1861 K | (1090.5, 1132.5) | Refused. Two left strokes. At 1200 dpi the gap is 20 px and the strokes join for 8 rows at the top and 8 rows at the bottom. Not the single left stroke of the 2162 K series-8 triangle. |
| 1873 K | (1111, 1250) | Refused. Up-triangles and a right-pointing outline are one mass. No open centre. |
| 1917 K | (1192.0, 1112.0) | Read a(MgO) = 0.276. Centre open. |
| 1933 K | (1222, 1098) | Refused. White gap 10–15 px wide runs into a filled triangle. Not a series-8 interior. |
| 1944 K | (1242, 1103) | Refused. Same filled triangle. The white gap is not a series-8 interior. |
| 1954 K | (1261, 1090) | Refused. Filled right triangle, series 7. |
| 1980 K | (1310, 1013) | Refused. The bar extends past the triangle, series 5 or 6. |
| 1988 K | (1323, 1082) | Refused. White region about 20 px wide sits on the filled triangle’s tip. |
| 2008 K | (1360.5, 1059.0) | Read a(MgO) = 0.328. Neighbour meets the left side. Centre open. |
| 2042 K | (1424, 880) | Refused. The bar extends past the triangle and a horizontal stroke crosses it, series 5 or 6. |
| 2051 K | (1440.0, 1061.5) | Read a(MgO) = 0.326. A second outline runs along the lower edge. Centre open. |
| 2080 K | (1460, 980) | Refused. The bar extends past the triangle, series 5 or 6. |
| 2101 K | (1532.0, 1044.5) | Read a(MgO) = 0.342. The protruding bar belongs to the marker above. Centre open. |
| 2161 K | (1641.5, 1018.5) | Read a(MgO) = 0.368. Centre open. |
| 2174 K | (1666, 944) | Refused. The bar extends past the triangle, series 5 or 6. |
| 2218 K | (1747.5, 998.5) | Read a(MgO) = 0.388. Centre open. |
| 2257 K | (1819.5, 979.5) | Read a(MgO) = 0.406. Centre open. |
| 2274 K | (1852, 906) | Refused. The bar extends past the triangle, series 5 or 6. |
| 2304 K | (1905.5, 961.0) | Read a(MgO) = 0.425. Centre open. |
| legend | (1633, 799) | Refused. Legend key, not a data marker. |

The white slot at crop (1058, 1136) is the inner edge of the refused 1861 K glyph, not a second symbol.

Figure 4 panel (b), a(Al2O3). Read uncertainty ±5.4 K and ±0.005.

| Approx T | Crop | Decision |
|---:|---|---|
| 1423 K | (283, 2527) | Refused. Up-triangle, series 1–4. |
| 1471 K | (370, 2642) | Refused. Up-triangle, series 1–4. |
| 1520 K | (462, 2669) | Refused. Up-triangle, series 1–4. |
| 1521 K | (464, 2615) | Refused. Up-triangle, series 1–4. |
| 1571 K | (555, 2658) | Refused. Up-triangle, series 1–4. |
| 1576 K | (564, 2607) | Refused. Up-triangle, series 1–4. |
| 1619 K | (643, 2642) | Refused. Up-triangle, series 1–4. |
| 1627 K | (658, 2675) | Refused. Up-triangle, series 1–4. |
| 1671 K | (740, 2587) | Refused. Up-triangle, series 1–4. |
| 1671 K | (740, 2628) | Refused. Up-triangle, series 1–4. |
| 1671 K | (740, 2669) | Refused. Up-triangle, series 1–4. |
| 1772 K | (926, 2371) | Refused. Up-triangle, upper half filled. Not series 8. |
| 1843 K | (1057, 2168) | Refused. Up-triangle, series 1–4. |
| 1850 K | (1080, 2280) | Refused. An up-triangle and a right triangle are one mass. No open centre. |
| 1917 K | (1193.5, 2290.0) | Read a(Al2O3) = 0.236. The tip meets the next outline. Centre open. |
| 1954 K | (1260.5, 2297.5) | Read a(Al2O3) = 0.232. Neighbour meets the corners. Centre open. |
| 1955 K | (1263, 2157) | Refused. Right triangle filled on its left half. Not an open series-8 interior. |
| 2009 K | (1362.5, 2317.0) | Read a(Al2O3) = 0.223. Doubled outline stops at the corners. Centre open. |
| 2053 K | (1443, 2321) | Refused. Filled right triangle, series 7. |
| 2103 K | (1535.5, 2329.5) | Refused. Same two-stroke pattern as 1861 K. At 1200 dpi the gap is 20 px and the strokes join for 4 rows at the top and 4 rows at the bottom. The 2162 K triangle on this panel has one left stroke. The 2174 K bar extends past the triangle. |
| 2162 K | (1644.5, 2341.5) | Read a(Al2O3) = 0.211. Centre open. |
| 2220 K | (1750.5, 2360.0) | Read a(Al2O3) = 0.202. Centre open. |
| 2259 K | (1823.0, 2358.0) | Read a(Al2O3) = 0.203. Centre open. |
| 2306 K | (1910.0, 2363.5) | Read a(Al2O3) = 0.200. Centre open. |
| legend | (1650, 2114) | Refused. Legend key, not a data marker. |

Three white pockets are not extra markers. Crop (1223.5, 2297.5) is 42 px wide and its box centre is 30 px from the stored 1917 K centre, inside that symbol’s width. It is the pocket where the 1917 K tip meets the 1954 K outline. Crop (1325, 2307) is the doubled left edge of the stored 2009 K marker. Crop (1501, 2327) is the inner edge of the refused 2103 K glyph.

Figure 2, series 16. Read uncertainty ±10 K and ±0.30 kJ/mol. The legend on the same figure prints series 16 as the open right-pointing triangle and series 13 as the filled right-pointing triangle.

| Approx T | Crop | Decision |
|---:|---|---|
| 1310 K | (847, 980) | Refused. Square, not series 16. |
| 1333 K | (873, 988) | Refused. Square, not series 16. |
| 1360 K | (903, 1003) | Refused. Square, not series 16. |
| 1381 K | (927, 1023) | Refused. Square, not series 16. |
| 1403 K | (952, 1044) | Refused. Square, not series 16. |
| 1425 K | (977, 974) | Refused. Square, not series 16. |
| 1439 K | (994, 1037) | Refused. Square, not series 16. |
| 1623 K | (1204, 873) | Refused. Up-triangle, not series 16. |
| 1639 K | (1222, 1001) | Refused. Up-triangle, not series 16. |
| 1667 K | (1254, 942) | Refused. Up-triangle, not series 16. |
| 1688 K | (1278, 950) | Refused. Up-triangle, not series 16. |
| 1737 K | (1334, 1061) | Refused. Up-triangle, not series 16. |
| 1767 K | (1368, 858) | Refused. Up-triangle, not series 16. |
| 1786 K | (1389, 1027) | Refused. Up-triangle, not series 16. |
| 1786 K | (1389, 1169) | Refused. Up-triangle, not series 16. |
| 1838 K | (1449, 1087) | Refused. Up-triangle, not series 16. |
| 1838 K | (1449, 1272) | Refused. Up-triangle, not series 16. |
| 1863 K | (1477.4, 958.6) | Read ΔG = −21.15 kJ/mol. Open right triangle. Centre open. The fit line passes below the centre. |
| 1950 K | (1576.4, 1002.1) | Read ΔG = −22.07 kJ/mol. Open right triangle. Centre open. |
| 2004 K | (1638.4, 1013.8) | Read ΔG = −22.32 kJ/mol. Open right triangle. Centre open. |
| 2050 K | (1690.5, 1018.2) | Read ΔG = −22.41 kJ/mol. Open right triangle. Centre open. |
| 2102 K | (1749.8, 1032.1) | Read ΔG = −22.70 kJ/mol. Open right triangle. Centre open. |
| 2160 K | (1816.3, 1042.2) | Read ΔG = −22.92 kJ/mol. Open right triangle. Centre open. |
| 2210 K | (1873.1, 1068.5) | Read ΔG = −23.47 kJ/mol. Open right triangle. Centre open. Confirmed at 1200 dpi. |
| 2252 K | (1921.5, 1064.6) | Read ΔG = −23.39 kJ/mol. Open right triangle. Centre open. |
| 2302 K | (1978.6, 1075.1) | Read ΔG = −23.61 kJ/mol. Open right triangle. Centre open. |
| legend | x about 2078 | Refused. Legend keys, including the series-16 key. Not data. |

The symbol above the stored chain, near crop (1739, 985), is a filled right triangle, series 13. White pockets between the stored series-16 triangles are the gaps where those outlines meet. They are not additional series-16 centres. Error-bar ends were not read.

## What was refused

Two formation entropies are stored and marked `typed_refusal`. The schema has no `delta_fS` token. They are not stored as absolute entropy `S`.

- Table 1 ΔS_T = 5.03 ± 0.56 J/(mol K), 1851–2298 K.
- Prose liquid-state ΔS_T = 28.09 ± 1.64 J/(mol K).

## Context only

- Table 1: 30 literature rows (calorimetry, EMF, thermochemical calculations, earlier KEMS). Not this paper’s measurements.
- Table 2: 15 literature melting rows (4 MgO, 8 other MgAl2O4, 3 Al2O3). Not this paper’s measurements.
- Figure 1: literature heat capacity. Not read.
- Figure 2: pointwise ΔG. Series 16 is the open right-pointing triangle. Nine centres were stored. The marker table names every other symbol in the data band. Error-bar ends were not read. The least-squares constants remain Table 1. MinerU’s chart table was ignored.
- Figure 3: pressures over spinel cited as reference [1], the evaporation paper already extracted. Not re-read.
- Figure 4: activities. Linear axes, calibrated from the tick marks on the 600 dpi render of PDF page 5. Series 8 is the open right-pointing triangle with one left stroke. Fifteen centres were stored, eight on panel (a) and seven on panel (b). The 1861 K and 2103 K glyphs were refused after the 1200 dpi comparison in the marker table. Read uncertainty is ±10 px (±5.4 K, and ±0.010 or ±0.005 in activity). MinerU’s scatter table was ignored.
- Figure 5: MgAl2O4–SiO2 phase diagram cited to [55]. Not this paper’s measurements. MinerU’s contour table was ignored.

## Standard state

Two activity rows were read from Figure 4, series 8. Both carry the equation (12)–(13) sentence.

Equations (12)–(13), PDF page 5, published page 291: “where p_i and p_i° are the partial pressures of the components of the gas phase over spinel and simple oxide (pure component), respectively.” The prose says the phase of that pure-component oxide is not stated, and the temperature of the reference measurement is not stated. `reference_state_from_extract` returns unknown on “not stated” before it looks for a Raoultian endmember. The prose does not say “Raoultian” and it does not name pure solid or pure liquid MgO, so `_standard_state_from_extract_text` does not replace that unknown. The points from 2161 K upward lie inside the crystal-state interval 2048–2298 K that the paper takes from reference [54] by equations (16) and (17). On panel (a), 2051 K and 2101 K also lie inside that interval, and 1917 K and 2008 K lie below it. On panel (b), 1917 K, 1954 K, and 2009 K lie below it. 2009 K is not inside 2048–2298 K. The paper does not say the pure oxide was measured in the cell at the experiment temperature. The 2026-10-20 pure-oxide-at-T ruling was not applied. No melting point was used. “Crystal state” names the spinel. `phase: cr` is that spinel phase.

“Crystalline” and “liquid” in equations (20)–(22) and in the liquid-state paragraph are the spinel phase, not the oxide reference. The formation rows say the paper does not state the oxide phase. That prose is not on an activity row, so the activity mapper does not read it.

The cell metal is not restated. The final introductory paragraph on published page 290 (PDF page 4) points to reference [52]. It is not copied from the 2016 conference abstract. Crystalline is printed in the conclusion on PDF page 7 and as crystal-line (sol) beside equations (20) and (21) on PDF page 6. The Table 1 caption does not say crystalline. Equation (19) is the fit ΔG_T = ΔH_T − TΔS_T.

## Not run here

No pytest and no `scripts/battery_migrate.py`. The studio commands are in the seat report.

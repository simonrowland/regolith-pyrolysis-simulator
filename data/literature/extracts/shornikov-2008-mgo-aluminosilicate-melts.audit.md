# Source audit: shornikov-2008-mgo-aluminosilicate-melts

## Scope and table-cell audit

The target article is Shornikov's MgO–Al2O3–SiO2 paper beginning after the references of the preceding article on PDF page 1. The following ZnO–P4O10 article begins on PDF page 3. Those adjacent-paper tables are outside this source. The target article's pdftotext-layout file contains only form feeds, so the scan has no usable character layer. MinerU's `<details>` chart table is an invented reading of a plot and is ignored.

The target paper's Gibbs-energy table is printed on PDF page 2 (published p. 148) with the unnumbered caption “Table.” The MinerU mapping retains its internal `table_index: 3`; stored locators identify the printed unnumbered caption as “Table.” The printed table has 20 body rows and 42 numeric cells; the other 18 value cells are blank or dashes. All 42 emitted numbers and their species/phase row-column assignments were checked against a 600 dpi page render. There were no hand corrections.

| Emitted observation | Printed row label | Phase and rendered cell | Printed value | Check |
|---|---|---|---:|---|
| `shornikov_2008_mgo_aluminosilicate_melts_p002_t03_r03_c02` | `$Al_2O_3$` | solid, c2 (r03/c02) | `-1017.742` | 600 dpi PDF p2 render; matches |
| `shornikov_2008_mgo_aluminosilicate_melts_p002_t03_r03_c04` | `$Al_2O_3$` | liquid, c4 (r03/c04) | `-1004.796` | 600 dpi PDF p2 render; matches |
| `shornikov_2008_mgo_aluminosilicate_melts_p002_t03_r03_c06` | `Al` | gas, c6 (r03/c06) | `79.835` | 600 dpi PDF p2 render; matches |
| `shornikov_2008_mgo_aluminosilicate_melts_p002_t03_r04_c02` | `$Al_2SiO_5$` | solid, c2 (r04/c02) | `-1572.345` | 600 dpi PDF p2 render; matches |
| `shornikov_2008_mgo_aluminosilicate_melts_p002_t03_r04_c06` | `AlO` | gas, c6 (r04/c06) | `-77.597` | 600 dpi PDF p2 render; matches |
| `shornikov_2008_mgo_aluminosilicate_melts_p002_t03_r05_c02` | `$Al_2Si_2O_7$` | solid, c2 (r05/c02) | `-2043.949` | 600 dpi PDF p2 render; matches |
| `shornikov_2008_mgo_aluminosilicate_melts_p002_t03_r05_c06` | `$AlO_2$` | gas, c6 (r05/c06) | `-98.856` | 600 dpi PDF p2 render; matches |
| `shornikov_2008_mgo_aluminosilicate_melts_p002_t03_r06_c02` | `$Al_6Si_2O_{13}$` | solid, c2 (r06/c02) | `-4189.139` | 600 dpi PDF p2 render; matches |
| `shornikov_2008_mgo_aluminosilicate_melts_p002_t03_r06_c04` | `$Al_6Si_2O_{13}$` | liquid, c4 (r06/c04) | `-4166.824` | 600 dpi PDF p2 render; matches |
| `shornikov_2008_mgo_aluminosilicate_melts_p002_t03_r06_c06` | `$Al_2$` | gas, c6 (r06/c06) | `185.080` | 600 dpi PDF p2 render; matches |
| `shornikov_2008_mgo_aluminosilicate_melts_p002_t03_r07_c02` | `$MgAl_2O_4$` | solid, c2 (r07/c02) | `-1432.815` | 600 dpi PDF p2 render; matches |
| `shornikov_2008_mgo_aluminosilicate_melts_p002_t03_r07_c04` | `$MgAl_2O_4$` | liquid, c4 (r07/c04) | `-1402.992` | 600 dpi PDF p2 render; matches |
| `shornikov_2008_mgo_aluminosilicate_melts_p002_t03_r07_c06` | `$Al_2O$` | gas, c6 (r07/c06) | `-284.309` | 600 dpi PDF p2 render; matches |
| `shornikov_2008_mgo_aluminosilicate_melts_p002_t03_r08_c02` | `$MgAl_2SiO_6$` | solid, c2 (r08/c02) | `-1951.349` | 600 dpi PDF p2 render; matches |
| `shornikov_2008_mgo_aluminosilicate_melts_p002_t03_r08_c06` | `$Al_2O_2$` | gas, c6 (r08/c06) | `-384.001` | 600 dpi PDF p2 render; matches |
| `shornikov_2008_mgo_aluminosilicate_melts_p002_t03_r09_c02` | `$Mg_2Al_4SiO_{10}$` | solid, c2 (r09/c02) | `-3397.978` | 600 dpi PDF p2 render; matches |
| `shornikov_2008_mgo_aluminosilicate_melts_p002_t03_r09_c06` | `$Al_2O_3$` | gas, c6 (r09/c06) | `-399.375` | 600 dpi PDF p2 render; matches |
| `shornikov_2008_mgo_aluminosilicate_melts_p002_t03_r10_c02` | `$Mg_2Al_4Si_5O_{18}$` | solid, c2 (r10/c02) | `-5687.842` | 600 dpi PDF p2 render; matches |
| `shornikov_2008_mgo_aluminosilicate_melts_p002_t03_r10_c04` | `$Mg_2Al_4Si_5O_{18}$` | liquid, c4 (r10/c04) | `-5714.275` | 600 dpi PDF p2 render; matches |
| `shornikov_2008_mgo_aluminosilicate_melts_p002_t03_r10_c06` | `AlSi` | gas, c6 (r10/c06) | `1151.705` | 600 dpi PDF p2 render; matches |
| `shornikov_2008_mgo_aluminosilicate_melts_p002_t03_r11_c02` | `$Mg_3Al_2Si_3O_{12}$` | solid, c2 (r11/c02) | `-3796.483` | 600 dpi PDF p2 render; matches |
| `shornikov_2008_mgo_aluminosilicate_melts_p002_t03_r11_c04` | `$Mg_3Al_2Si_3O_{12}$` | liquid, c4 (r11/c04) | `-3811.640` | 600 dpi PDF p2 render; matches |
| `shornikov_2008_mgo_aluminosilicate_melts_p002_t03_r11_c06` | `AlSiO` | gas, c6 (r11/c06) | `-217.442` | 600 dpi PDF p2 render; matches |
| `shornikov_2008_mgo_aluminosilicate_melts_p002_t03_r12_c02` | `MgO` | solid, c2 (r12/c02) | `-370.531` | 600 dpi PDF p2 render; matches |
| `shornikov_2008_mgo_aluminosilicate_melts_p002_t03_r12_c04` | `MgO` | liquid, c4 (r12/c04) | `-350.509` | 600 dpi PDF p2 render; matches |
| `shornikov_2008_mgo_aluminosilicate_melts_p002_t03_r12_c06` | `Mg` | gas, c6 (r12/c06) | `-61.659` | 600 dpi PDF p2 render; matches |
| `shornikov_2008_mgo_aluminosilicate_melts_p002_t03_r13_c02` | `$MgSiO_3$` | solid, c2 (r13/c02) | `-937.039` | 600 dpi PDF p2 render; matches |
| `shornikov_2008_mgo_aluminosilicate_melts_p002_t03_r13_c04` | `$MgSiO_3$` | liquid, c4 (r13/c04) | `-937.226` | 600 dpi PDF p2 render; matches |
| `shornikov_2008_mgo_aluminosilicate_melts_p002_t03_r13_c06` | `MgO` | gas, c6 (r13/c06) | `-112.070` | 600 dpi PDF p2 render; matches |
| `shornikov_2008_mgo_aluminosilicate_melts_p002_t03_r14_c02` | `$Mg_2SiO_4$` | solid, c2 (r14/c02) | `-1341.906` | 600 dpi PDF p2 render; matches |
| `shornikov_2008_mgo_aluminosilicate_melts_p002_t03_r14_c04` | `$Mg_2SiO_4$` | liquid, c4 (r14/c04) | `-1335.552` | 600 dpi PDF p2 render; matches |
| `shornikov_2008_mgo_aluminosilicate_melts_p002_t03_r14_c06` | `Si` | gas, c6 (r14/c06) | `161.631` | 600 dpi PDF p2 render; matches |
| `shornikov_2008_mgo_aluminosilicate_melts_p002_t03_r15_c02` | `$SiO_2$` | solid, c2 (r15/c02) | `-540.626` | 600 dpi PDF p2 render; matches |
| `shornikov_2008_mgo_aluminosilicate_melts_p002_t03_r15_c04` | `$SiO_2$` | liquid, c4 (r15/c04) | `-541.222` | 600 dpi PDF p2 render; matches |
| `shornikov_2008_mgo_aluminosilicate_melts_p002_t03_r15_c06` | `SiO` | gas, c6 (r15/c06) | `-257.183` | 600 dpi PDF p2 render; matches |
| `shornikov_2008_mgo_aluminosilicate_melts_p002_t03_r16_c06` | `$SiO_2$` | gas, c6 (r16/c06) | `-313.278` | 600 dpi PDF p2 render; matches |
| `shornikov_2008_mgo_aluminosilicate_melts_p002_t03_r17_c06` | `$Si_2$` | gas, c6 (r17/c06) | `210.531` | 600 dpi PDF p2 render; matches |
| `shornikov_2008_mgo_aluminosilicate_melts_p002_t03_r18_c06` | `$Si_2O_2$` | gas, c6 (r18/c06) | `-552.779` | 600 dpi PDF p2 render; matches |
| `shornikov_2008_mgo_aluminosilicate_melts_p002_t03_r19_c06` | `$Si_3$` | gas, c6 (r19/c06) | `261.005` | 600 dpi PDF p2 render; matches |
| `shornikov_2008_mgo_aluminosilicate_melts_p002_t03_r20_c06` | `O` | gas, c6 (r20/c06) | `118.320` | 600 dpi PDF p2 render; matches |
| `shornikov_2008_mgo_aluminosilicate_melts_p002_t03_r21_c06` | `$O_2$` | gas, c6 (r21/c06) | `0.000` | 600 dpi PDF p2 render; matches |
| `shornikov_2008_mgo_aluminosilicate_melts_p002_t03_r22_c06` | `$O_3$` | gas, c6 (r22/c06) | `276.725` | 600 dpi PDF p2 render; matches |

`mineru_table_draft.py compare` was run against the final extract and the draft index. Its output is committed in `shornikov-2008-mgo-aluminosilicate-melts.compare.txt`. It returned no comparison points (`total=0`): the current comparator enumerates `values.points`, while `emit` outputs scalar table values at `values.value`. The comparator therefore cannot compare these emitted scalar rows. The independent render audit above covers every emitted cell; no change was made to the tool.

## Figure 1: axes, calibration and marker audit

The figure is the paper's only figure; its caption is unnumbered (“Fig.”), so the extract labels it figure 1. The page was rendered from the source PDF at 600 dpi. R1 is the initial crop from that page at origin (150, 400). R2 uses 23 fresh 144 × 144 px crops, one centered on each printed marker; each crop has its own origin and local pixel estimate in the read table below.

Each axis was calibrated from the labelled ticks below. Fitted maps are pixel-to-axis linear least-squares maps. Activity panels use -log10(activity) on the ordinate. Panel d plots -ΔfG; the stored ΔfG value is the negative of that axis value.

| Axis | Tick values | Tick pixel coordinates in R1 crop | Fitted map |
|---|---|---|---|
| a, x(SiO2) mol% | 0, 20, 40, 60, 80, 100 | 493, 704, 918, 1135, 1352, 1557 | x = 0.09356472 px - 46.04418591 |
| b, x(SiO2) mol% | 0, 20, 40, 60, 80, 100 | 1557, 1770, 1981, 2193, 2405, 2616 | x = 0.0944413157 px - 147.099026 |
| c, x(SiO2) mol% | 0, 20, 40, 60, 80, 100 | 502, 714, 927, 1144, 1361, 1566 | x = 0.09360261 px - 46.94110471 |
| d, x(SiO2) mol% | 0, 20, 40, 60, 80, 100 | 1566, 1790, 1996, 2205, 2415, 2624 | x = 0.0949162607 px - 149.260870 |
| a, -log10(activity) | 0.5, 1.0, 1.5, 2.0 | 224, 526, 827, 1128 | -log10(a) = 0.00165947 py + 0.12778036 |
| b, -log10(activity) | 0.5, 1.0, 1.5, 2.0 | 218, 518, 819, 1119 | -log10(a) = 0.00166445 py + 0.1373174 |
| c, -log10(activity) | 0.2, 0.4, 0.6, 0.8 | 1363, 1568, 1774, 1979 | -log10(a) = 0.000973708911 py - 1.12706759 |
| d, -ΔfG kJ/mole | 26, 24, 22, 20, 18 | 1330, 1518, 1705, 1892, 2079 | -ΔfG = -0.0106837485 py + 40.2136544 |

Marker centers are read twice independently. R1 is the initial crop from the page image at origin (150, 400). For R2, I made a fresh 144 × 144 px crop around each marker from a separate 600 dpi render of PDF page 2, then estimated each centre from that crop. Each crop has a unique id `review-p2-600dpi-<panel><marker>`; the page-pixel origin and local centre are listed separately below. R2 coordinates are not generated by shifting R1 coordinates. Pixel-to-axis values use the fitted maps above. The stored x(SiO2) and activity are the means of the two calibrated reads rounded to two significant figures; x=0 markers clipped by the axis are snapped to the axis. Panel d stores the negative mean of the two plotted -ΔfG reads. Read uncertainty is half the absolute difference between the two ordinate reads; when both centre reads land on the same ordinate pixel, the key is omitted.

| Panel | Marker | Stored x(SiO2) mol% | R1 pixel / ordinate | R2 crop origin (page px) | R2 pixel / ordinate | Stored value | Read uncertainty |
|---|---:|---:|---|---|---|---:|---:|
| a | 1 | 0 | `[493, 205]` / 0.467972 | `[570, 535]` | `[73, 70]` / 0.467972 | `0.34` | omitted (same ordinate pixel) |
| a | 2 | 18 | `[685, 205]` / 0.467972 | `[775, 521]` | `[60, 84]` / 0.467972 | `0.34` | omitted (same ordinate pixel) |
| a | 3 | 52 | `[1053, 352]` / 0.711914 | `[1127, 685]` | `[76, 67]` / 0.711914 | `0.19` | omitted (same ordinate pixel) |
| a | 4 | 56 | `[1089, 382]` / 0.761698 | `[1176, 701]` | `[63, 81]` / 0.761698 | `0.17` | omitted (same ordinate pixel) |
| a | 5 | 65 | `[1189, 481]` / 0.925156 | `[1260, 817]` | `[80, 65]` / 0.927645 | `0.12` | 0.001244 dex |
| a | 6 | 75 | `[1296, 600]` / 1.123462 | `[1380, 922]` | `[70, 78]` / 1.123462 | `0.075` | omitted (same ordinate pixel) |
| b | 1 | 0 | `[1557, 342]` / 0.705727 | `[1641, 683]` | `[65, 62]` / 0.711553 | `0.20` | 0.002913 dex |
| b | 2 | 18 | `[1756, 341]` / 0.704063 | `[1824, 668]` | `[77, 72]` / 0.703230 | `0.20` | 0.000416 dex |
| b | 3 | 53 | `[2118, 570]` / 1.086054 | `[2199, 883]` | `[65, 87]` / 1.086054 | `0.082` | omitted (same ordinate pixel) |
| b | 4 | 56 | `[2153, 624]` / 1.175102 | `[2218, 954]` | `[80, 70]` / 1.175934 | `0.067` | 0.000416 dex |
| b | 5 | 65 | `[2252, 794]` / 1.458891 | `[2330, 1110]` | `[67, 84]` / 1.458891 | `0.035` | omitted (same ordinate pixel) |
| b | 6 | 75 | `[2357, 1015]` / 1.826734 | `[2448, 1348]` | `[54, 66]` / 1.825070 | `0.015` | 0.000832 dex |
| c | 1 | 18 | `[693, 2091]` / 0.908471 | `[784, 2412]` | `[59, 84]` / 0.913826 | `0.12` | 0.002678 dex |
| c | 2 | 52 | `[1057, 1642]` / 0.471762 | `[1132, 1980]` | `[73, 68]` / 0.477605 | `0.34` | 0.002921 dex |
| c | 3 | 55 | `[1093, 1579]` / 0.409932 | `[1181, 1903]` | `[62, 81]` / 0.415287 | `0.39` | 0.002678 dex |
| c | 4 | 65 | `[1193, 1426]` / 0.260954 | `[1265, 1767]` | `[78, 64]` / 0.266310 | `0.54` | 0.002678 dex |
| c | 5 | 75 | `[1299, 1299]` / 0.137293 | `[1384, 1626]` | `[65, 78]` / 0.142649 | `0.72` | 0.002678 dex |
| d | 1 | 0 | `[1566, 1628]` / 22.825850 | `[1635, 1960]` | `[78, 67]` / 22.831196 | `-23` | 0.002673 kJ/mol |
| d | 2 | 18 | `[1764, 1403]` / 25.229700 | `[1846, 1721]` | `[69, 83]` / 25.213672 | `-25` | 0.008014 kJ/mol |
| d | 3 | 52 | `[2119, 1277]` / 26.575850 | `[2185, 1612]` | `[84, 65]` / 26.570508 | `-27` | 0.002671 kJ/mol |
| d | 4 | 55 | `[2155, 1338]` / 25.924140 | `[2234, 1659]` | `[72, 80]` / 25.908115 | `-26` | 0.008012 kJ/mol |
| d | 5 | 65 | `[2255, 1596]` / 23.162390 | `[2347, 1934]` | `[58, 63]` / 23.151708 | `-23` | 0.005341 kJ/mol |
| d | 6 | 75 | `[2359, 2008]` / 18.760690 | `[2435, 2332]` | `[75, 76]` / 18.760687 | `-19` | 0.0000015 kJ/mol |
Panel d has visible experimental error bars, but the paper does not state their statistical meaning or scale; they are not converted to a numeric uncertainty. The recorded read uncertainty measures only digitization repeatability.

The figure caption marks curves 5–8 as present-study calculations. They have typed unavailable observations with T=2050 K, the published model domain `1800 to 2200 K`, and unknown composition because each curve spans the composition axis. No curve values were sampled. The text reports ΔfG2050 = -26.9 kJ/mole at cordierite concentration on PDF page 1 (published p. 147); it is stored as one quoted-attributed point at 2050 K with unknown numeric composition because the text gives no coordinate. The schema has a token for that energy value, so this is not a numeric schema gap.

## Isolated migration

The focused test migrates only this extract: 70 rows total (66 numeric point values, 4 unavailable smooth curves), zero registry issues and zero hard issues. All 70 rows retain 2050 K in both point conditions and identity. Activity markers carry the binary composition in both point conditions and identity. The δfG profile does not use composition as an identity axis, so the figure's composition remains preserved in its point condition. Numeric rows are admitted; the four curve rows are rejected as unavailable typed refusals. No pressure or oxygen-fugacity condition is printed.

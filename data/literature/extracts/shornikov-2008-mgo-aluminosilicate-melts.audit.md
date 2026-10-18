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

The figure is the paper's only figure; its caption is unnumbered (“Fig.”), so the extract labels it figure 1. The page was rendered from the source PDF at 600 dpi. R1 is the initial crop from that page at origin (150, 400). R2 is a separate crop of the same render for each marker; the crop origin and the local centre are listed below. The crop is not the R1 window.

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

Marker centers are read twice. R1 is the initial crop from the page image at origin (150, 400). R2 is the centre of the filled mark on the same 600 dpi page, measured again in its own crop. A 3 px opening removes curves, grid lines and error bars. Squares and triangles use the bounding-box centre of that body. Panel d uses a circle fit to the edge, except marker 3, whose cap inflates the fit, so that marker uses the deepest interior pixel. Marker a1 is a square cut by the axis, so its abscissa stays on the axis and its ordinate is the vertical middle of the square. Markers b1 and d1 are whole symbols. Both reads sit on the measured centre, and the stored x is two significant figures of that centre (1.7 and 0.71), not a mean with the axis tick. Each crop has a unique id `lmax-r2-<panel><marker>`. Pixel-to-axis values use the fitted maps above. The stored x(SiO2) and activity are the means of the two calibrated reads rounded to two significant figures. Panel d stores the negative mean of the two plotted -ΔfG reads. Read uncertainty is half the absolute difference of the two ordinate reads when those reads do not share an integer page ordinate, and the axis width of half a pixel when they do. The key is present and positive on every marker. A measured pair may land on the same pixel; the cross is not moved off the centre to open a gap.

| Panel | Marker | Stored x(SiO2) mol% | R1 pixel / ordinate | R2 crop origin (page px) | R2 pixel / ordinate | Stored value | Read uncertainty |
|---|---:|---:|---|---|---|---:|---:|
| a | 1 | 0 | `[493, 205]` / 0.467972 | `[621, 558]` | `[22, 47]` / 0.467972 | `0.34` | 0.000830 dex |
| a | 2 | 18 | `[685, 205]` / 0.467972 | `[777, 574]` | `[58, 31]` / 0.467972 | `0.34` | 0.000830 dex |
| a | 3 | 52 | `[1053, 352]` / 0.711914 | `[1166, 689]` | `[36, 64]` / 0.713573 | `0.19` | 0.000830 dex |
| a | 4 | 56 | `[1089, 382]` / 0.761698 | `[1166, 756]` | `[71, 28]` / 0.765017 | `0.17` | 0.001660 dex |
| a | 5 | 65 | `[1189, 481]` / 0.925156 | `[1295, 828]` | `[44, 53]` / 0.925985 | `0.12` | 0.000830 dex |
| a | 6 | 75 | `[1296, 600]` / 1.123462 | `[1379, 961]` | `[67, 39]` / 1.123462 | `0.075` | 0.000830 dex |
| b | 1 | 1.7 | `[1576, 344]` / 0.709888 | `[1697, 686]` | `[29, 58]` / 0.709888 | `0.20` | 0.000832 dex |
| b | 2 | 19 | `[1756, 341]` / 0.704063 | `[1859, 707]` | `[51, 34]` / 0.704895 | `0.20` | 0.000832 dex |
| b | 3 | 53 | `[2118, 570]` / 1.086054 | `[2208, 925]` | `[63, 46]` / 1.087718 | `0.082` | 0.000832 dex |
| b | 4 | 56 | `[2153, 624]` / 1.175102 | `[2269, 956]` | `[38, 69]` / 1.177599 | `0.067` | 0.001249 dex |
| b | 5 | 66 | `[2252, 794]` / 1.458891 | `[2332, 1167]` | `[74, 27]` / 1.458891 | `0.035` | 0.000832 dex |
| b | 6 | 76 | `[2357, 1015]` / 1.826734 | `[2470, 1359]` | `[41, 55]` / 1.825070 | `0.015` | 0.000832 dex |
| c | 1 | 18 | `[693, 2091]` / 0.908471 | `[810, 2427]` | `[33, 61]` / 0.906037 | `0.12` | 0.001217 dex |
| c | 2 | 52 | `[1057, 1642]` / 0.471762 | `[1151, 1997]` | `[57, 42]` / 0.468841 | `0.34` | 0.001461 dex |
| c | 3 | 55 | `[1093, 1579]` / 0.409932 | `[1217, 1928]` | `[26, 48]` / 0.407498 | `0.39` | 0.001217 dex |
| c | 4 | 65 | `[1193, 1426]` / 0.260954 | `[1275, 1787]` | `[68, 35]` / 0.257546 | `0.55` | 0.001704 dex |
| c | 5 | 75 | `[1299, 1299]` / 0.137293 | `[1399, 1623]` | `[49, 72]` / 0.133885 | `0.73` | 0.001704 dex |
| d | 1 | 0.71 | `[1580, 1626]` / 22.841879 | `[1699, 1982]` | `[31, 44]` / 22.841879 | `-23` | 0.005342 kJ/mol |
| d | 2 | 18 | `[1764, 1403]` / 25.229700 | `[1848, 1777]` | `[66, 25]` / 25.235039 | `-25` | 0.002670 kJ/mol |
| d | 3 | 52 | `[2119, 1277]` / 26.575850 | `[2227, 1617]` | `[43, 59]` / 26.581191 | `-27` | 0.002671 kJ/mol |
| d | 4 | 55 | `[2155, 1338]` / 25.924140 | `[2252, 1700]` | `[54, 37]` / 25.929483 | `-26` | 0.002672 kJ/mol |
| d | 5 | 65 | `[2255, 1596]` / 23.162390 | `[2328, 1964]` | `[77, 32]` / 23.162392 | `-23` | 0.005342 kJ/mol |
| d | 6 | 75 | `[2359, 2008]` / 18.760690 | `[2474, 2341]` | `[35, 66]` / 18.771371 | `-19` | 0.005341 kJ/mol |
Panel d has visible experimental error bars, but the paper does not state their statistical meaning or scale; they are not converted to a numeric uncertainty. The recorded read uncertainty measures only digitization repeatability.

The figure caption marks curves 5–8 as present-study calculations. They have typed unavailable observations with T=2050 K, the published model domain `1800 to 2200 K`, and unknown composition because each curve spans the composition axis. No curve values were sampled. The text reports ΔfG2050 = -26.9 kJ/mole at cordierite concentration on PDF page 1 (published p. 147); it is stored as one quoted-attributed point at 2050 K with unknown numeric composition because the text gives no coordinate. The schema has a token for that energy value, so this is not a numeric schema gap.

## Isolated migration

The focused test migrates only this extract: 70 rows total (66 numeric point values, 4 unavailable smooth curves), zero registry issues and zero hard issues. All 70 rows retain 2050 K in both point conditions and identity. Activity markers carry the binary composition in both point conditions and identity. The δfG profile does not use composition as an identity axis, so the figure's composition remains preserved in its point condition. Numeric rows are admitted; the four curve rows are rejected as unavailable typed refusals. No pressure or oxygen-fugacity condition is printed.

# Source audit: shornikov-2008-mgo-aluminosilicate-melts

## Scope and table-cell audit

The target article is Shornikov's MgO–Al2O3–SiO2 paper beginning after the references of the preceding article on PDF page 1. The following ZnO–P4O10 article begins on PDF page 3. Those adjacent-paper tables are outside this source. The target article's pdftotext-layout file contains only form feeds, so the scan has no usable character layer. MinerU's `<details>` chart table is an invented reading of a plot and is ignored.

The target paper's Gibbs-energy table is printed on PDF page 2 (published p. 148) with the unnumbered caption “Table.” The extraction locator's `table: '3'` is MinerU's internal `p002-t03` draft index, not a printed table number. The printed table has 20 body rows and 42 numeric cells; the other 18 value cells are blank or dashes. All 42 emitted numbers and their species/phase row-column assignments were checked against a 600 dpi page render. There were no hand corrections.

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

The figure is the paper's only figure; its caption is unnumbered (“Fig.”), so the extract labels it figure 1. The page was rendered from the source PDF at 600 dpi. R1 is the crop from the page image at origin (150, 400); R2 is a fresh crop from the original page at offset origin (200, 150), dimensions 3000 x 2700 px. They are different crop windows and independent center estimates. Convert R2 coordinates into the R1 frame by x1 = x2 + 50 and y1 = y2 - 250.

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

Marker centers are read twice independently from R1 and R2. Pixels below are rounded to the nearest pixel for the test and extract; the stored per-read ordinate and x calibration values preserve the unrounded fitted reads. Marker centers clipped by the x=0 axis in a1, b1 and d1 are set to x=0 at the axis intersection. The two close panel-a squares were estimated separately from local crops. Each stored marker has T=2050 K and x(SiO2) recorded in mole percent; its binary complementary composition is in `point_conditions.composition` and the corresponding `values.composition_mol` identity field. Activities are stored once in the linear `activity` field. The two log ordinates and pixel reads are retained as digitization metadata; `read_uncertainty_dex` is half the difference between calibrated log reads. All activity pairs differ by less than 0.02 dex.

| Panel | Symbol | Marker | x(SiO2) mol% (2 sig) | R1 pixel (x%, crop) / R2 pixel (x%, crop) | R1 / R2 ordinate reads | Stored value | Read uncertainty |
|---|---|---:|---:|---|---|---:|---:|
| a | 1 | 1 | 0 | `[493, 205]` (0.0) / `[443, 455]` (0.0) | 0.467972 / 0.467972 | `0.34` | 0.0 dex |
| a | 1 | 2 | 18 | `[685, 205]` (18.04765) / `[635, 455]` (18.04765) | 0.467972 / 0.467142 | `0.34` | 0.00041 dex |
| a | 1 | 3 | 52 | `[1053, 352]` (52.47946) / `[1002, 601]` (52.3859) | 0.711914 / 0.710254 | `0.19` | 0.00083 dex |
| a | 1 | 4 | 56 | `[1089, 382]` (55.84779) / `[1037, 634]` (55.66066) | 0.761698 / 0.765017 | `0.17` | 0.00166 dex |
| a | 1 | 5 | 65 | `[1189, 481]` (65.15748) / `[1139, 731]` (65.15748) | 0.925156 / 0.925156 | `0.12` | 0.0 dex |
| a | 1 | 6 | 75 | `[1296, 600]` (75.16891) / `[1246, 850]` (75.16891) | 1.123462 / 1.123462 | `0.075` | 0.0 dex |
| b | 2 | 1 | 0 | `[1557, 342]` (0.0) / `[1507, 594]` (0.0) | 0.705727 / 0.709056 | `0.20` | 0.00166 dex |
| b | 2 | 2 | 19 | `[1756, 341]` (18.6927) / `[1705, 591]` (18.59826) | 0.704063 / 0.704895 | `0.20` | 0.00042 dex |
| b | 2 | 3 | 53 | `[2118, 570]` (52.88046) / `[2066, 820]` (52.69158) | 1.086054 / 1.085222 | `0.082` | 0.00042 dex |
| b | 2 | 4 | 56 | `[2153, 624]` (56.18591) / `[2102, 873]` (56.09146) | 1.175102 / 1.17427 | `0.067` | 0.00042 dex |
| b | 2 | 5 | 66 | `[2252, 794]` (65.58282) / `[2201, 1044]` (65.44115) | 1.458891 / 1.458891 | `0.035` | 0.0 dex |
| b | 2 | 6 | 75 | `[2357, 1015]` (75.49916) / `[2306, 1265]` (75.40471) | 1.826734 / 1.825902 | `0.015` | 0.00042 dex |
| c | 3 | 1 | 18 | `[693, 2091]` (17.9255) / `[643, 2343]` (17.9255) | 0.908471 / 0.910418 | `0.12` | 0.00097 dex |
| c | 3 | 2 | 52 | `[1057, 1642]` (51.99685) / `[1007, 1894]` (51.99685) | 0.471762 / 0.47371 | `0.34` | 0.00097 dex |
| c | 3 | 3 | 55 | `[1093, 1579]` (55.31975) / `[1043, 1831]` (55.31975) | 0.409932 / 0.411879 | `0.39` | 0.00097 dex |
| c | 3 | 4 | 65 | `[1193, 1426]` (64.72681) / `[1142, 1677]` (64.63321) | 0.260954 / 0.262415 | `0.55` | 0.00073 dex |
| c | 3 | 5 | 75 | `[1299, 1299]` (74.64869) / `[1249, 1550]` (74.64869) | 0.137293 / 0.138754 | `0.73` | 0.00073 dex |
| d | 4 | 1 | 0 | `[1566, 1628]` (0.0) / `[1516, 1878]` (0.0) | 22.82585 / 22.82585 | `-23` | 0.0 kJ/mole |
| d | 4 | 2 | 18 | `[1764, 1403]` (18.12396) / `[1714, 1652]` (18.17141) | 25.2297 / 25.23504 | `-25` | 0.00267 kJ/mole |
| d | 4 | 3 | 52 | `[2119, 1277]` (51.86669) / `[2069, 1527]` (51.86669) | 26.57585 / 26.57585 | `-27` | 0.0 kJ/mole |
| d | 4 | 4 | 55 | `[2155, 1338]` (55.28367) / `[2106, 1588]` (55.33113) | 25.92414 / 25.92414 | `-26` | 0.0 kJ/mole |
| d | 4 | 5 | 65 | `[2255, 1596]` (64.72784) / `[2205, 1847]` (64.7753) | 23.16239 / 23.15171 | `-23` | 0.00534 kJ/mole |
| d | 4 | 6 | 75 | `[2359, 2008]` (74.64659) / `[2309, 2258]` (74.64659) | 18.76069 / 18.76069 | `-19` | 0.0 kJ/mole |

Panel d has visible experimental error bars, but the paper does not state their statistical meaning or scale; they are not converted to a numeric uncertainty. The recorded read uncertainty measures only digitization repeatability.

The figure caption marks curves 5–8 as present-study calculations. They have typed unavailable observations with T=2050 K, the published model domain `1800 to 2200 K`, and unknown composition because each curve spans the composition axis. No curve values were sampled. The text reports ΔfG2050 = -26.9 kJ/mole at cordierite concentration; it is stored as one quoted-attributed point at 2050 K with unknown numeric composition because the text gives no coordinate. The schema has a token for that energy value, so this is not a numeric schema gap.

## Isolated migration

The focused test migrates only this extract: 70 rows total (66 numeric point values, 4 unavailable smooth curves), zero registry issues and zero hard issues. All 70 rows retain 2050 K in both point conditions and identity. Activity markers carry the binary composition in both point conditions and identity. The δfG profile does not use composition as an identity axis, so the figure's composition remains preserved in its point condition. Numeric rows are admitted; the four curve rows are rejected as unavailable typed refusals. No pressure or oxygen-fugacity condition is printed.

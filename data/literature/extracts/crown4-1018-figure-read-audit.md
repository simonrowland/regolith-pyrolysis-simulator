# Crown4 figure-read audit (re-measured 2026-10-18)

Centres were read from the symbol the caption names. Vector markers (1997, 2007) use the path-geometry centre: bounding-box centre for filled circles, filled squares and open triangles, and the vertex centroid for filled triangles. The 2001 scan is measured in native pixels of the embedded image; 600 dpi page pixels are native times 600/72. Read B is a second ink measurement, not an offset from read A: the inner dark core of a filled mark, the bright hole of an open circle, the stroke-box midpoint of an open triangle, or (for the 2001 scan) the same triangle at a darker threshold and a heavier erosion. Where the axis covers half of a marker, read B keeps the path x on the tick and uses the ink's y. `read_uncertainty_dex` is half the centre-to-centre difference in log space. A pair wider than 0.02 dex was not stored. Where the two ink centres land on the same pixel, the stored uncertainty is half a pixel on the log axis (0.5 / px per decade), not zero.

Log direction: 1997 a = 10^(-y); 2007 and 2001 a = 10^y. Figure 3 ΔfG/RT is not an activity and is not converted: the schema has no quantity token for dimensionless ΔfG/RT.

No activity row is claimed above 2100 K. The 2100 K CT-S curves stay figure-only.

## 1997 Fig. 1(a), page pixels at 600 dpi

Y ticks, labelled −log a(CaO): 0 at py 618.22, 1 at 1067.18, 2 at 1510.23. Fit: −log = (py − 619.205) / 446.005.
X ticks, labelled mol% CaO: 30 at 1025.63, 50 at 1482.93, 70 at 1934.65, 90 at 2386.14. Fit: x = −15.324374 + 0.044118034·px.
Legend glyphs near px 2005 are not data. Series 3 filled circles, series 4 filled squares, series 5 open triangles.

## 2007 Fig. 1, page pixels at 600 dpi

Y ticks, labelled log a: 0 at py 629.0, −1 at 989.0, −2 at 1349.5, −3 at 1709.5. log10(a) = −(py − 629) / 360.1667.
X ticks, labelled mol% Al2O3: 0 at 884.5, then every 5 mol% through 50 at 3176.5. x = (px − 884.5) / 45.84.
Panel a is x ≤ 25 (join CaO = 0.5 − x, Al2O3 = x, SiO2 = 0.5). Panel b is x > 25 (CaO = x, Al2O3 = x, SiO2 = 1 − 2x). The x = 25 marker is the anorthite boundary, where both joins meet; it is stored once.
Legend glyphs at px 1981 are not data. No open circle is drawn at x = 0.

## 2001 Fig. 2, native pixels of the 1300×2000 scan

Panel a: log 0 at native y 85 (top corner), log −1 at 164, log −4 at the axis y 400. log10(a) = −(y − 85) / 78.75. X ticks 0 at 406.5, 10 at 460.5, 20 at 516, 30 at 569.5, 40 at 623.5; x = (native x − 406.5) / 5.425.
Panel b: labelled −1 at native y 693 and −2 at 820.5, which places log 0 at 565.5. log10(a) = −(y − 565.5) / 127.5. X tick 0 at 404.5, step 5.45 px per mol%. No up-triangle at x = 0 (the stack on the axis is an inverted triangle and a square).
Panel c: labelled 0 at native y 1055, −1 at 1162, −2 at 1269. log10(a) = −(y − 1055) / 107. X tick 0 at 407.5, step 5.375 px per mol%. No up-triangle at x = 0.
Fitted x is used only to match a triangle to a printed composition. The stored composition is the printed value.

## 2001 Fig. 3 curve 1

Y labels −1 at native y 130.5, −2 at 231.5, −3 at 333.5. ΔfG/RT = −1 − (y − 130.5) / 101.5. X tick 0 at 389, step 5.542 px per mol%. Five up-triangles. The 52.21 mole% position is a square.

## Reads

| audit_read_id | T_K | component | x | activity | px_A | py_A | px_B | py_B | unc_dex | note |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| shornikov_1997_cao_fig1_series3_sulfide_1773k-01 | 1773 | CaO | 58.13 | 0.2 | 1665.00 | 927.00 | 1665.00 | 927.00 | 0.001121 | inner-core |
| shornikov_1997_cao_fig1_series3_sulfide_1773k-02 | 1773 | CaO | 60.12 | 0.25 | 1710.00 | 891.00 | 1710.00 | 891.00 | 0.001121 | inner-core |
| shornikov_1997_cao_fig1_series3_sulfide_1773k-03 | 1773 | CaO | 61.97 | 0.3 | 1752.00 | 855.00 | 1752.00 | 855.00 | 0.001121 | inner-core |
| shornikov_1997_cao_fig1_series3_sulfide_1773k-04 | 1773 | CaO | 63.82 | 0.37 | 1794.00 | 810.00 | 1794.00 | 810.00 | 0.001121 | inner-core |
| shornikov_1997_cao_fig1_series3_sulfide_1773k-05 | 1773 | CaO | 65.94 | 0.49 | 1842.00 | 759.00 | 1842.00 | 759.00 | 0.001121 | inner-core |
| shornikov_1997_cao_fig1_series3_sulfide_1773k-06 | 1773 | CaO | 68.19 | 0.64 | 1893.00 | 705.00 | 1893.00 | 705.00 | 0.001121 | inner-core |
| shornikov_1997_cao_fig1_series3_sulfide_1773k-07 | 1773 | CaO | 70.97 | 0.9 | 1956.00 | 639.00 | 1956.00 | 639.00 | 0.001121 | inner-core |
| shornikov_1997_cao_fig1_series4_sulfide_1873k-01 | 1873 | CaO | 14.43 | 0.01 | 674.36 | 1508.36 | 674.37 | 1508.37 | 0.000011 | inner-core |
| shornikov_1997_cao_fig1_series4_sulfide_1873k-02 | 1873 | CaO | 33.33 | 0.025 | 1102.93 | 1331.50 | 1102.88 | 1331.50 | 0.001121 | inner-core |
| shornikov_1997_cao_fig1_series4_sulfide_1873k-03 | 1873 | CaO | 50.35 | 0.11 | 1488.65 | 1054.25 | 1488.58 | 1054.23 | 0.000022 | inner-core |
| shornikov_1997_cao_fig1_series4_sulfide_1873k-04 | 1873 | CaO | 60.12 | 0.33 | 1710.08 | 834.36 | 1710.14 | 834.39 | 0.000034 | inner-core |
| shornikov_1997_cao_fig1_series4_sulfide_1873k-05 | 1873 | CaO | 63.27 | 0.47 | 1781.50 | 767.44 | 1781.50 | 767.50 | 0.0001 | inner-core |
| shornikov_1997_cao_fig1_series4_sulfide_1873k-06 | 1873 | CaO | 75.04 | 0.91 | 2048.17 | 638.38 | 2048.20 | 638.41 | 0.000034 | inner-core |
| shornikov_1997_cao_fig1_series5_emf_1773k-01 | 1773 | CaO | 14.48 | 0.0057 | 675.55 | 1619.08 | 675.50 | 1620.00 | 0.0010 | stroke-extent |
| shornikov_1997_cao_fig1_series5_emf_1773k-02 | 1773 | CaO | 33.39 | 0.031 | 1104.13 | 1289.25 | 1104.50 | 1290.00 | 0.0008 | stroke-extent |
| shornikov_1997_cao_fig1_series5_emf_1773k-03 | 1773 | CaO | 50.30 | 0.089 | 1487.46 | 1088.48 | 1487.50 | 1089.00 | 0.0006 | stroke-extent |
| shornikov_1997_cao_fig1_series5_emf_1773k-04 | 1773 | CaO | 63.01 | 0.24 | 1775.55 | 894.89 | 1775.50 | 895.00 | 0.0001 | stroke-extent |
| shornikov_1997_cao_fig1_series5_emf_1773k-05 | 1773 | CaO | 70.78 | 0.56 | 1951.74 | 729.97 | 1952.50 | 730.50 | 0.0006 | stroke-extent |
| shornikov_2007_figure1_cao_activity_prior_markers-01 | 2033 | CaO | 0.00 | 0.022 | 884.00 | 1224.50 | 884.00 | 1224.50 | 0.001388 | a axis-y log=-1.653 |
| shornikov_2007_figure1_cao_activity_prior_markers-02 | 2033 | CaO | 6.89 | 0.021 | 1200.25 | 1236.25 | 1200.34 | 1235.97 | 0.0004 | a inner-core log=-1.686 |
| shornikov_2007_figure1_cao_activity_prior_markers-03 | 2033 | CaO | 11.68 | 0.015 | 1419.75 | 1284.00 | 1419.75 | 1284.00 | 0.001388 | a inner-core log=-1.819 |
| shornikov_2007_figure1_cao_activity_prior_markers-04 | 2033 | CaO | 13.00 | 0.013 | 1480.25 | 1311.75 | 1480.32 | 1311.68 | 0.0001 | a inner-core log=-1.896 |
| shornikov_2007_figure1_cao_activity_prior_markers-05 | 2033 | CaO | 18.99 | 0.0082 | 1755.00 | 1379.75 | 1755.00 | 1379.75 | 0.001388 | a inner-core log=-2.084 |
| shornikov_2007_figure1_cao_activity_prior_markers-06 | 2033 | CaO | 25.00 | 0.0048 | 2030.00 | 1464.75 | 2030.00 | 1464.75 | 0.001388 | a inner-core log=-2.320 |
| shornikov_2007_figure1_cao_activity_prior_markers-07 | 2033 | CaO | 29.98 | 0.0063 | 2259.00 | 1421.00 | 2259.00 | 1421.00 | 0.001388 | b inner-core log=-2.199 |
| shornikov_2007_figure1_cao_activity_prior_markers-08 | 2033 | CaO | 33.32 | 0.0081 | 2411.75 | 1382.75 | 2411.68 | 1382.68 | 0.0001 | b inner-core log=-2.093 |
| shornikov_2007_figure1_cao_activity_prior_markers-09 | 2033 | CaO | 37.48 | 0.01 | 2602.75 | 1347.75 | 2602.68 | 1347.68 | 0.0001 | b inner-core log=-1.996 |
| shornikov_2007_figure1_cao_activity_prior_markers-10 | 2033 | CaO | 44.98 | 0.038 | 2946.50 | 1141.50 | 2946.50 | 1141.50 | 0.001388 | b inner-core log=-1.423 |
| shornikov_2007_figure1_cao_activity_prior_markers-11 | 2033 | CaO | 49.98 | 0.098 | 3175.75 | 992.75 | 3175.75 | 992.12 | 0.0009 | b axis-y log=-1.010 |
| shornikov_2007_figure1_al2o3_activity_prior_markers-01 | 2033 | Al2O3 | 6.89 | 0.017 | 1200.25 | 1266.00 | 1200.22 | 1266.00 | 0.001388 | a hole log=-1.769 |
| shornikov_2007_figure1_al2o3_activity_prior_markers-02 | 2033 | Al2O3 | 11.68 | 0.06 | 1419.75 | 1068.25 | 1419.76 | 1068.24 | 0.000014 | a hole log=-1.220 |
| shornikov_2007_figure1_al2o3_activity_prior_markers-03 | 2033 | Al2O3 | 13.00 | 0.09 | 1480.25 | 1005.50 | 1480.19 | 1005.50 | 0.001388 | a hole log=-1.045 |
| shornikov_2007_figure1_al2o3_activity_prior_markers-04 | 2033 | Al2O3 | 19.01 | 0.23 | 1755.75 | 857.75 | 1755.75 | 857.76 | 0.000014 | a hole log=-0.635 |
| shornikov_2007_figure1_al2o3_activity_prior_markers-05 | 2033 | Al2O3 | 25.00 | 0.43 | 2029.75 | 760.00 | 2029.78 | 760.45 | 0.0006 | a hole log=-0.364 |
| shornikov_2007_figure1_al2o3_activity_prior_markers-06 | 2033 | Al2O3 | 29.98 | 0.33 | 2259.00 | 801.50 | 2259.50 | 802.00 | 0.0007 | b ring-extent log=-0.479 |
| shornikov_2007_figure1_al2o3_activity_prior_markers-07 | 2033 | Al2O3 | 33.32 | 0.26 | 2411.75 | 838.75 | 2411.76 | 838.76 | 0.000014 | b hole log=-0.582 |
| shornikov_2007_figure1_al2o3_activity_prior_markers-08 | 2033 | Al2O3 | 37.48 | 0.24 | 2602.75 | 851.50 | 2602.81 | 851.50 | 0.001388 | b hole log=-0.618 |
| shornikov_2007_figure1_al2o3_activity_prior_markers-09 | 2033 | Al2O3 | 44.99 | 0.2 | 2946.75 | 880.25 | 2946.76 | 880.24 | 0.000014 | b hole log=-0.698 |
| shornikov_2007_figure1_al2o3_activity_prior_markers-10 | 2033 | Al2O3 | 49.98 | 0.18 | 3175.75 | 896.00 | 3175.74 | 896.12 | 0.0002 | b hole log=-0.741 |
| shornikov_2007_figure1_sio2_activity_prior_markers-01 | 2033 | SiO2 | 0.00 | 0.18 | 884.17 | 897.33 | 884.17 | 897.36 | 0.000042 | a axis-y log=-0.745 |
| shornikov_2007_figure1_sio2_activity_prior_markers-02 | 2033 | SiO2 | 6.89 | 0.21 | 1200.33 | 872.83 | 1200.34 | 872.88 | 0.0001 | a inner-core log=-0.677 |
| shornikov_2007_figure1_sio2_activity_prior_markers-03 | 2033 | SiO2 | 11.69 | 0.27 | 1420.17 | 833.67 | 1420.24 | 833.68 | 0.000014 | a inner-core log=-0.568 |
| shornikov_2007_figure1_sio2_activity_prior_markers-04 | 2033 | SiO2 | 13.01 | 0.3 | 1480.67 | 816.33 | 1480.61 | 816.29 | 0.0001 | a inner-core log=-0.520 |
| shornikov_2007_figure1_sio2_activity_prior_markers-05 | 2033 | SiO2 | 18.99 | 0.39 | 1755.17 | 776.17 | 1755.22 | 776.17 | 0.001388 | a inner-core log=-0.409 |
| shornikov_2007_figure1_sio2_activity_prior_markers-06 | 2033 | SiO2 | 25.00 | 0.52 | 2030.17 | 730.33 | 2030.28 | 730.31 | 0.000028 | a inner-core log=-0.281 |
| shornikov_2007_figure1_sio2_activity_prior_markers-07 | 2033 | SiO2 | 29.99 | 0.3 | 2259.17 | 816.33 | 2259.28 | 816.31 | 0.000028 | b inner-core log=-0.520 |
| shornikov_2007_figure1_sio2_activity_prior_markers-08 | 2033 | SiO2 | 33.33 | 0.15 | 2412.17 | 924.83 | 2412.21 | 924.89 | 0.0001 | b inner-core log=-0.821 |
| shornikov_2007_figure1_sio2_activity_prior_markers-09 | 2033 | SiO2 | 37.49 | 0.054 | 2603.17 | 1085.33 | 2603.28 | 1085.31 | 0.000028 | b inner-core log=-1.267 |
| shornikov_2007_figure1_sio2_activity_prior_markers-10 | 2033 | SiO2 | 44.99 | 0.011 | 2946.67 | 1334.17 | 2946.62 | 1334.20 | 0.000042 | b inner-core log=-1.958 |
| shornikov_2001_f2_mgo_ma_s_2050k-01 | 2050 | MgO | 0.00 | 0.36 | 3394.00 | 998.00 | 3394.25 | 1000.80 | 0.0021 | native A=(407.28,119.76) B=(407.31,120.10) fitted x=0.14 log=-0.441 |
| shornikov_2001_f2_mgo_ma_s_2050k-02 | 2050 | MgO | 18.18 | 0.34 | 4217.00 | 1011.92 | 4217.34 | 1012.22 | 0.0002 | native A=(506.04,121.43) B=(506.08,121.47) fitted x=18.35 log=-0.463 |
| shornikov_2001_f2_mgo_ma_s_2050k-03 | 2050 | MgO | 52.21 | 0.18 | 5747.25 | 1191.50 | 5749.09 | 1192.49 | 0.0008 | native A=(689.67,142.98) B=(689.89,143.10) fitted x=52.20 log=-0.736 |
| shornikov_2001_f2_mgo_ma_s_2050k-04 | 2050 | MgO | 55.56 | 0.16 | 5897.17 | 1226.67 | 5898.29 | 1227.00 | 0.0003 | native A=(707.66,147.20) B=(707.79,147.24) fitted x=55.51 log=-0.790 |
| shornikov_2001_f2_mgo_ma_s_2050k-05 | 2050 | MgO | 64.89 | 0.11 | 6320.50 | 1334.25 | 6320.40 | 1334.89 | 0.0005 | native A=(758.46,160.11) B=(758.45,160.19) fitted x=64.88 log=-0.954 |
| shornikov_2001_f2_mgo_ma_s_2050k-06 | 2050 | MgO | 74.69 | 0.068 | 6760.67 | 1474.25 | 6759.14 | 1474.91 | 0.0005 | native A=(811.28,176.91) B=(811.10,176.99) fitted x=74.61 log=-1.167 |
| shornikov_2001_f2_al2o3_ma_s_2050k-01 | 2050 | Al2O3 | 18.18 | 0.2 | 4204.17 | 5462.92 | 4204.58 | 5464.29 | 0.0006 | native A=(504.50,655.55) B=(504.55,655.72) fitted x=18.35 log=-0.706 |
| shornikov_2001_f2_al2o3_ma_s_2050k-02 | 2050 | Al2O3 | 52.21 | 0.078 | 5731.00 | 5887.25 | 5730.86 | 5888.66 | 0.0007 | native A=(687.72,706.47) B=(687.70,706.64) fitted x=51.97 log=-1.106 |
| shornikov_2001_f2_al2o3_ma_s_2050k-03 | 2050 | Al2O3 | 55.56 | 0.064 | 5883.00 | 5980.83 | 5882.71 | 5980.60 | 0.0001 | native A=(705.96,717.70) B=(705.93,717.67) fitted x=55.31 log=-1.194 |
| shornikov_2001_f2_al2o3_ma_s_2050k-04 | 2050 | Al2O3 | 64.89 | 0.033 | 6300.17 | 6289.08 | 6298.86 | 6287.61 | 0.0007 | native A=(756.02,754.69) B=(755.86,754.51) fitted x=64.50 log=-1.484 |
| shornikov_2001_f2_al2o3_ma_s_2050k-05 | 2050 | Al2O3 | 74.69 | 0.014 | 6736.42 | 6681.83 | 6737.77 | 6683.03 | 0.0006 | native A=(808.37,801.82) B=(808.53,801.96) fitted x=74.10 log=-1.853 |
| shornikov_2001_f2_sio2_ma_s_2050k-01 | 2050 | SiO2 | 18.18 | 0.12 | 4209.92 | 9613.42 | 4208.71 | 9612.98 | 0.0002 | native A=(505.19,1153.61) B=(505.04,1153.56) fitted x=18.17 log=-0.922 |
| shornikov_2001_f2_sio2_ma_s_2050k-02 | 2050 | SiO2 | 52.21 | 0.32 | 5736.58 | 9235.00 | 5736.42 | 9236.43 | 0.0008 | native A=(688.39,1108.20) B=(688.37,1108.37) fitted x=52.26 log=-0.497 |
| shornikov_2001_f2_sio2_ma_s_2050k-03 | 2050 | SiO2 | 55.56 | 0.36 | 5885.42 | 9182.83 | 5886.57 | 9182.43 | 0.0002 | native A=(706.25,1101.94) B=(706.39,1101.89) fitted x=55.58 log=-0.439 |
| shornikov_2001_f2_sio2_ma_s_2050k-04 | 2050 | SiO2 | 64.89 | 0.51 | 6304.00 | 9054.58 | 6304.87 | 9054.98 | 0.0002 | native A=(756.48,1086.55) B=(756.58,1086.60) fitted x=64.93 log=-0.295 |
| shornikov_2001_f2_sio2_ma_s_2050k-05 | 2050 | SiO2 | 74.69 | 0.66 | 6742.50 | 8949.92 | 6743.58 | 8949.37 | 0.0003 | native A=(809.10,1073.99) B=(809.23,1073.92) fitted x=74.72 log=-0.177 |

## Figure 3 ΔfG/RT (not activities; not converted)

- x(SiO2)=0.00 ΔfG/RT raw -1.345 stored -1.3; page px (3238.08,1379.17) and (3239.29,1377.80); half-difference 0.0008
- x(SiO2)=18.18 ΔfG/RT raw -1.488 stored -1.5; page px (4077.08,1500.25) and (4076.04,1502.60); half-difference 0.0014
- x(SiO2)=55.56 ΔfG/RT raw -1.545 stored -1.5; page px (5808.58,1548.33) and (5808.52,1548.98); half-difference 0.0004
- x(SiO2)=64.89 ΔfG/RT raw -1.390 stored -1.4; page px (6240.58,1417.33) and (6240.21,1418.03); half-difference 0.0004
- x(SiO2)=74.69 ΔfG/RT raw -1.151 stored -1.2; page px (6695.83,1215.42) and (6696.54,1214.89); half-difference 0.0003

The schema has no quantity token for dimensionless ΔfG/RT.

# crown4-1018 figure-read audit

All figure images were rendered from the source PDFs with `pdftoppm -r 600`. Coordinates are `(x,y)` pixel positions in the full-page render; independent reads list separate pixel pairs. Activities are tick-calibrated and stored to two significant figures. Read uncertainty is ±0.03 dex for logarithmic activity axes.

## Axis calibrations

| Source / figure | x-axis tick labels and full-page pixel positions | y-axis tick labels and full-page pixel positions | fitted map |
|---|---|---|---|
| Shornikov 1997, Fig. 1(a), page 21 / PDF page index 2 | x(CaO) mol%: 10→577, 30→1029, 50→1481, 70→1933, 90→2385 px | −log10(a_CaO): 0→618, 1→1062, 2→1506 px | x=10+(px−577)/22.6; −log10(a)=(py−618)/444 |
| Shornikov 2007, Fig. 1(a,b), page 37 / PDF page index 2 | x(Al2O3) mol%: 0→885, 10→1343, 20→1802, 30→2260, 40→2719, 50→3177 px | log10(a(i)): 0→629, −1→988, −2→1347, −3→1707 px | x=(px−885)/45.84; log10(a)=−(py−629)/359.33 |
| Shornikov 2001, Fig. 2(a), page 327 / PDF page index 5 | x(SiO2) mol%: 0→3335, 20→4124, 40→4913, 60→5702, 80→6491, 100→7280 px | log10(a(i)): 0→872, −1→1384, −2→1896, −3→2408, −4→2920 px | x=(px−3335)/39.45; log10(a)=−(py−872)/512 |
| Shornikov 2001, Fig. 2(b), page 327 / PDF page index 5 | x(SiO2) mol%: 0→3335, 20→4124, 40→4913, 60→5702, 80→6491, 100→7280 px | log10(a(i)): 0→4645, −1→5795, −2→6945 px | x=(px−3335)/39.45; log10(a)=−(py−4645)/1150 |
| Shornikov 2001, Fig. 2(c), page 327 / PDF page index 5 | x(SiO2) mol%: 0→3335, 20→4124, 40→4913, 60→5702, 80→6491, 100→7280 px | log10(a(i)): 0→8520, −1→9352, −2→10184, −3→11016 px | x=(px−3335)/39.45; log10(a)=−(py−8520)/832 |
| Shornikov 2001, Fig. 3, page 328 / PDF page index 6 | x(SiO2) mol%: 0→3209, 20→4021, 40→4833, 60→5645, 80→6457, 100→7269 px | ΔfG/RT: −1→950, −2→1690, −3→2430 px | x=(px−3209)/40.61; ΔfG/RT=−1−(py−950)/740 |

## CaO activity readings, Shornikov 1997

Stolyarova (1995) Fig. 1 reproduces this plot. Markers are stored once in the 1997 extract; the 1995 locator cross-references that canonical record.

| read id | x(CaO), read A / B mol% | −log10(a_CaO), read A / B | page pixel read A / B | stored activity |
|---|---:|---:|---|---:|
| shornikov_1997_cao_fig1_series3_sulfide_1773k-01 | 57.9 / 58.1 | 0.71 / 0.73 | (1660,933) / (1664,942) | 0.19 |
| shornikov_1997_cao_fig1_series3_sulfide_1773k-02 | 60.4 / 60.6 | 0.61 / 0.63 | (1716,889) / (1721,898) | 0.24 |
| shornikov_1997_cao_fig1_series3_sulfide_1773k-03 | 62.4 / 62.6 | 0.51 / 0.53 | (1761,844) / (1766,853) | 0.3 |
| shornikov_1997_cao_fig1_series3_sulfide_1773k-04 | 64.4 / 64.6 | 0.41 / 0.43 | (1806,800) / (1811,809) | 0.38 |
| shornikov_1997_cao_fig1_series3_sulfide_1773k-05 | 66.4 / 66.6 | 0.31 / 0.33 | (1852,756) / (1856,765) | 0.48 |
| shornikov_1997_cao_fig1_series3_sulfide_1773k-06 | 68.4 / 68.6 | 0.21 / 0.23 | (1897,711) / (1901,720) | 0.6 |
| shornikov_1997_cao_fig1_series3_sulfide_1773k-07 | 70.9 / 71.1 | 0.04 / 0.06 | (1953,636) / (1958,645) | 0.89 |
| shornikov_1997_cao_fig1_series4_sulfide_1873k-01 | 13.9 / 14.1 | 1.99 / 2.01 | (665,1502) / (670,1510) | 0.01 |
| shornikov_1997_cao_fig1_series4_sulfide_1873k-02 | 31.9 / 32.1 | 1.59 / 1.61 | (1072,1324) / (1076,1333) | 0.025 |
| shornikov_1997_cao_fig1_series4_sulfide_1873k-03 | 49.9 / 50.1 | 0.99 / 1.01 | (1479,1058) / (1483,1066) | 0.1 |
| shornikov_1997_cao_fig1_series4_sulfide_1873k-04 | 62.9 / 63.1 | 0.44 / 0.46 | (1773,813) / (1777,822) | 0.35 |
| shornikov_1997_cao_fig1_series4_sulfide_1873k-05 | 74.9 / 75.1 | 0.04 / 0.06 | (2044,636) / (2048,645) | 0.89 |
| shornikov_1997_cao_fig1_series5_emf_1773k-01 | 13.9 / 14.1 | 2.29 / 2.31 | (665,1635) / (670,1644) | 0.005 |
| shornikov_1997_cao_fig1_series5_emf_1773k-02 | 48.9 / 49.1 | 0.71 / 0.73 | (1456,933) / (1461,942) | 0.19 |
| shornikov_1997_cao_fig1_series5_emf_1773k-03 | 56.9 / 57.1 | 0.41 / 0.43 | (1637,800) / (1641,809) | 0.38 |
| shornikov_1997_cao_fig1_series5_emf_1773k-04 | 63.9 / 64.1 | 0.27 / 0.29 | (1795,738) / (1800,747) | 0.52 |

## Ternary activity readings, Shornikov 2007

Panel (a) is the CaSiO3-CaAl2Si2O8 join; panel (b) is the CaAl2Si2O8-CaAl2O4 join. Oxide mole fractions are bound per marker to the printed join and x(Al2O3).

| read id | x(Al2O3), read A / B mol% | log10(a), read A / B | page pixel read A / B | stored activity |
|---|---:|---:|---|---:|
| shornikov_2007_figure1_cao_activity_prior_markers-01 | 0 / 0.05 | -1.61 / -1.59 | (885,1208) / (887,1200) | 0.025 |
| shornikov_2007_figure1_cao_activity_prior_markers-02 | 6.95 / 7.05 | -1.66 / -1.64 | (1204,1225) / (1208,1218) | 0.022 |
| shornikov_2007_figure1_cao_activity_prior_markers-03 | 11.95 / 12.05 | -1.81 / -1.79 | (1433,1279) / (1437,1272) | 0.016 |
| shornikov_2007_figure1_cao_activity_prior_markers-04 | 12.95 / 13.05 | -1.91 / -1.89 | (1479,1315) / (1483,1308) | 0.013 |
| shornikov_2007_figure1_cao_activity_prior_markers-05 | 18.95 / 19.05 | -2.16 / -2.14 | (1754,1405) / (1758,1398) | 0.0071 |
| shornikov_2007_figure1_cao_activity_prior_markers-06 | 24.95 / 25.05 | -2.36 / -2.34 | (2029,1477) / (2033,1470) | 0.0045 |
| shornikov_2007_figure1_cao_activity_prior_markers-07 | 29.95 / 30.05 | -2.26 / -2.24 | (2258,1441) / (2262,1434) | 0.0056 |
| shornikov_2007_figure1_cao_activity_prior_markers-08 | 32.95 / 33.05 | -2.11 / -2.09 | (2395,1387) / (2400,1380) | 0.0079 |
| shornikov_2007_figure1_cao_activity_prior_markers-09 | 37.45 / 37.55 | -2.01 / -1.99 | (2602,1351) / (2606,1344) | 0.01 |
| shornikov_2007_figure1_cao_activity_prior_markers-10 | 44.95 / 45.05 | -1.91 / -1.89 | (2946,1315) / (2950,1308) | 0.013 |
| shornikov_2007_figure1_cao_activity_prior_markers-11 | 49.95 / 50 | -1.01 / -0.99 | (3175,992) / (3177,985) | 0.1 |
| shornikov_2007_figure1_al2o3_activity_prior_markers-01 | 0 / 0.05 | -1.01 / -0.99 | (885,992) / (887,985) | 0.1 |
| shornikov_2007_figure1_al2o3_activity_prior_markers-02 | 6.95 / 7.05 | -1.76 / -1.74 | (1204,1261) / (1208,1254) | 0.018 |
| shornikov_2007_figure1_al2o3_activity_prior_markers-03 | 11.95 / 12.05 | -1.31 / -1.29 | (1433,1100) / (1437,1093) | 0.05 |
| shornikov_2007_figure1_al2o3_activity_prior_markers-04 | 12.95 / 13.05 | -1.06 / -1.04 | (1479,1010) / (1483,1003) | 0.089 |
| shornikov_2007_figure1_al2o3_activity_prior_markers-05 | 18.95 / 19.05 | -0.66 / -0.64 | (1754,866) / (1758,859) | 0.22 |
| shornikov_2007_figure1_al2o3_activity_prior_markers-06 | 24.95 / 25.05 | -0.41 / -0.39 | (2029,776) / (2033,769) | 0.4 |
| shornikov_2007_figure1_al2o3_activity_prior_markers-07 | 29.95 / 30.05 | -0.51 / -0.49 | (2258,812) / (2262,805) | 0.32 |
| shornikov_2007_figure1_al2o3_activity_prior_markers-08 | 33.45 / 33.55 | -0.59 / -0.57 | (2418,841) / (2423,834) | 0.26 |
| shornikov_2007_figure1_al2o3_activity_prior_markers-09 | 37.45 / 37.55 | -0.63 / -0.61 | (2602,855) / (2606,848) | 0.24 |
| shornikov_2007_figure1_al2o3_activity_prior_markers-10 | 44.95 / 45.05 | -0.71 / -0.69 | (2946,884) / (2950,877) | 0.2 |
| shornikov_2007_figure1_al2o3_activity_prior_markers-11 | 49.95 / 50 | -0.76 / -0.74 | (3175,902) / (3177,895) | 0.18 |
| shornikov_2007_figure1_sio2_activity_prior_markers-01 | 0 / 0.05 | -0.76 / -0.74 | (885,902) / (887,895) | 0.18 |
| shornikov_2007_figure1_sio2_activity_prior_markers-02 | 6.95 / 7.05 | -0.66 / -0.64 | (1204,866) / (1208,859) | 0.22 |
| shornikov_2007_figure1_sio2_activity_prior_markers-03 | 11.95 / 12.05 | -0.41 / -0.39 | (1433,776) / (1437,769) | 0.4 |
| shornikov_2007_figure1_sio2_activity_prior_markers-04 | 12.95 / 13.05 | -0.36 / -0.34 | (1479,758) / (1483,751) | 0.45 |
| shornikov_2007_figure1_sio2_activity_prior_markers-05 | 18.95 / 19.05 | -0.21 / -0.19 | (1754,704) / (1758,697) | 0.63 |
| shornikov_2007_figure1_sio2_activity_prior_markers-06 | 24.95 / 25.05 | -0.26 / -0.24 | (2029,722) / (2033,715) | 0.56 |
| shornikov_2007_figure1_sio2_activity_prior_markers-07 | 33.45 / 33.55 | -0.81 / -0.79 | (2418,920) / (2423,913) | 0.16 |
| shornikov_2007_figure1_sio2_activity_prior_markers-08 | 37.45 / 37.55 | -1.26 / -1.24 | (2602,1082) / (2606,1075) | 0.056 |
| shornikov_2007_figure1_sio2_activity_prior_markers-09 | 44.95 / 45.05 | -1.86 / -1.84 | (2946,1297) / (2950,1290) | 0.014 |

## MgAl2O4-SiO2 activity readings, Shornikov 2001 Figure 2

Composition coordinates are the six printed x(SiO2) values 0.00, 18.18, 52.21, 55.56, 64.89, and 74.69 mole%. Figure 2(c), SiO2, has no zero-composition marker.

| read id | printed x(SiO2) mol% | log10(a), read A / B | page pixel read A / B | stored activity |
|---|---:|---:|---|---:|
| shornikov_2001_f2_mgo_ma_s_2050k-01 | 0.00 (printed) | -0.56 / -0.54 | (3335,1159) / (3336,1148) | 0.28 |
| shornikov_2001_f2_mgo_ma_s_2050k-02 | 18.18 (printed) | -0.56 / -0.54 | (4051,1159) / (4053,1148) | 0.28 |
| shornikov_2001_f2_mgo_ma_s_2050k-03 | 52.21 (printed) | -0.67 / -0.65 | (5394,1215) / (5396,1205) | 0.22 |
| shornikov_2001_f2_mgo_ma_s_2050k-04 | 55.56 (printed) | -0.71 / -0.69 | (5526,1236) / (5528,1225) | 0.2 |
| shornikov_2001_f2_mgo_ma_s_2050k-05 | 64.89 (printed) | -0.91 / -0.89 | (5894,1338) / (5896,1328) | 0.13 |
| shornikov_2001_f2_mgo_ma_s_2050k-06 | 74.69 (printed) | -1.19 / -1.17 | (6280,1481) / (6283,1471) | 0.066 |
| shornikov_2001_f2_al2o3_ma_s_2050k-01 | 0.00 (printed) | -0.61 / -0.59 | (3335,5347) / (3336,5324) | 0.25 |
| shornikov_2001_f2_al2o3_ma_s_2050k-02 | 18.18 (printed) | -0.66 / -0.64 | (4051,5404) / (4053,5381) | 0.22 |
| shornikov_2001_f2_al2o3_ma_s_2050k-03 | 52.21 (printed) | -1.16 / -1.14 | (5394,5979) / (5396,5956) | 0.071 |
| shornikov_2001_f2_al2o3_ma_s_2050k-04 | 55.56 (printed) | -1.26 / -1.24 | (5526,6094) / (5528,6071) | 0.056 |
| shornikov_2001_f2_al2o3_ma_s_2050k-05 | 64.89 (printed) | -1.63 / -1.61 | (5894,6520) / (5896,6497) | 0.024 |
| shornikov_2001_f2_al2o3_ma_s_2050k-06 | 74.69 (printed) | -1.95 / -1.93 | (6280,6888) / (6283,6865) | 0.011 |
| shornikov_2001_f2_sio2_ma_s_2050k-01 | 18.18 (printed) | -0.91 / -0.89 | (4051,9277) / (4053,9260) | 0.13 |
| shornikov_2001_f2_sio2_ma_s_2050k-02 | 52.21 (printed) | -0.83 / -0.81 | (5394,9211) / (5396,9194) | 0.15 |
| shornikov_2001_f2_sio2_ma_s_2050k-03 | 55.56 (printed) | -0.63 / -0.61 | (5526,9044) / (5528,9028) | 0.24 |
| shornikov_2001_f2_sio2_ma_s_2050k-04 | 64.89 (printed) | -0.46 / -0.44 | (5894,8903) / (5896,8886) | 0.35 |
| shornikov_2001_f2_sio2_ma_s_2050k-05 | 74.69 (printed) | -0.23 / -0.21 | (6280,8711) / (6283,8695) | 0.6 |

## ΔfG/RT readings, Shornikov 2001 Figure 3

The plotted ordinate is dimensionless ΔfG/RT. Six readings are preserved verbatim with T=2050 K; they are not converted to ΔfG.

| read id | printed x(SiO2) mol% | ΔfG/RT, read A / B | page pixel read A / B | stored form |
|---|---:|---:|---|---|
| shornikov_2001_f3_ma_s_2050k-01 | 0.00 (printed) | -1.41 / -1.39 | (3209,1253) / (3210,1239) | retained verbatim; no conversion |
| shornikov_2001_f3_ma_s_2050k-02 | 18.18 (printed) | -1.51 / -1.49 | (3946,1327) / (3948,1313) | retained verbatim; no conversion |
| shornikov_2001_f3_ma_s_2050k-03 | 52.21 (printed) | -1.61 / -1.59 | (5328,1401) / (5330,1387) | retained verbatim; no conversion |
| shornikov_2001_f3_ma_s_2050k-04 | 55.56 (printed) | -1.51 / -1.49 | (5464,1327) / (5466,1313) | retained verbatim; no conversion |
| shornikov_2001_f3_ma_s_2050k-05 | 64.89 (printed) | -1.41 / -1.39 | (5843,1253) / (5844,1239) | retained verbatim; no conversion |
| shornikov_2001_f3_ma_s_2050k-06 | 74.69 (printed) | -1.11 / -1.09 | (6241,1031) / (6242,1017) | retained verbatim; no conversion |

**Schema gap:** the extract schema and migrator have no quantity token for dimensionless ΔfG/RT. The Shornikov 2001 Figure 3 identity remains `delta_fG` with an unavailable energy value; the axis readings and T=2050 K are preserved in the figure locator note and this report.

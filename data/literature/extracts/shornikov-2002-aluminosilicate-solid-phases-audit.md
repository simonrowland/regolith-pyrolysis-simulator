# Shornikov 2002 Al2O3–SiO2 solid phases — extraction audit

Source: Shornikov, S. I., Archakov, I. Yu., Shultz, M. M., and Borisova, N. V. (2002), Mass Spectrometric Study of Evaporation and the Thermodynamic Properties of Solid Phases in the Al2O3–SiO2 System, Doklady Chemistry 383(1–3), 82–85. Translated from Doklady Akademii Nauk 383(3), 358–361. Received 27 November 2001. PDF SHA256 eb36c88205ba7bf9138bfdbc016ee6b53ed4fb5512a60992087cb3adbb1e9286. Four pages.

## Duplicate check

Not a duplicate of an extract on this base. `shornikov-2002-mullite-thermodynamics` is Geochemistry International 40 Suppl. 1, S46–S60. `shornikov-2002-alumina-silica-melt-thermodynamics` is RJPC 76(7) 1054–1060. This Doklady article is the solid-phase campaign. The 2003 RJPC solids paper (pp. 1037–1043) is a sibling report, not this article. The later mullite compilation, `shornikov-2003-mullite-thermodynamics` (RJPC 77(7) 1044–1050), already tabulates 12.9 ± 0.8 kJ/mol and 11.6 ± 0.4 J/(mol K) as “this work” with span 1720–2180 K and a 1950 K row label. The Doklady prints 1676–2180 K and no 1950 K label. Both statements are kept. This is not recorded as DUPLICATE OF that extract.

## Admitted

ΔfH of mullite from oxides, 12.9 ± 0.8 kJ/mol, least squares of ΔfG over 1676–2180 K, equation (6), published page 84. Species Al6Si2O13, phase cr, quantity delta_fH. No single temperature is printed, so none is invented. The ±0.8 is the printed uncertainty; the confidence level is not stated. The fix round adds the printed lineage: derivation.relation is the least squares of equations (4) and (5), and raw_inputs is not_printed with a locator on equation (6). No parent observation is invented, and derived_from is absent.

Standard state: “formation of mullite from oxides.” The paper does not state solid versus liquid oxides. The prose contains “not stated” and does not name one pure endmember, so migration of an activity mapper would stay unknown. This row is a formation enthalpy, not an activity. The 2026-10-20 pure-oxide rule was not applied: equations (4) and (5) compare partial pressures between the two heterogeneous regions, not a pure oxide measured in the cell.

## Refused as scored rows, kept in context

ΔfS = 11.6 ± 0.4 J/(mol K), same sentence and temperature span. The schema has no delta_fS token. It is a context row, not a second typed_refusal observation, so it cannot add a hard issue. The mullite compilation already stores the same digits as a typed_refusal table cell.

Mullite composition 60 ± 5 mol% Al2O3, 3Al2O3·2SiO2, from equations (4) and (5).

Transition estimates: M+S to M+liquid and M+S to S+liquid do not exceed 5 kJ/mol. M+A to A+liquid is 13 ± 10 kJ/mol. These are author estimates at pressures below 10^−9 atm. They are not enthalpies of fusion of a pure endmember.

Evaporation coefficients: the paper says direct data are unavailable and assumes the silica and alumina values. Not alpha observations.

## Figures

No content_list table. Figure 1 is a phase-diagram comparison.

| marker | place | evidence | decision |
|---|---|---|---|
| phase boundaries | Figure 1 | continuous comparison curves | refused: not discrete legend symbols of Figure 2. No pressure centre. |

Figure 2 (published p. 83) was re-read at 1200 dpi (`p2002-p-2.png`, PDF page 2). Panel frames, page pixels: (a) y-axis x=5430, y=1155-3541, x-axis y=3541, x=5430-8913. T ticks used for the three diamonds: 1700 at x=5692.0 and 1800 at x=6502.5, so T = 1700 + (x-5692.0)/8.105. Later T spacing is 729.5, 653.5, and 593 px per 100 K and was not used. Log ticks used: -5 at y=2885.5 and -6 at y=3442.5, so -log = 5 + (y-2885.5)/557. The -4 to -5 gap is 608 px, not the same. Panel (b) y-axis x=5522, y=3971-6238, x-axis y=6238. Panel (c) y-axis x=5430, y=6719-9130, x-axis y=9130. The diamond beside the VIII legend entry is not a datum. Three filled diamonds on panel (a) match that legend symbol and are stored at the printed 84.14 mol% Al2O3. The caption says replica filled symbols are a changed composition, and the new mole percent is not printed, so none was invented. The point at (5892.5, 2993.5) is 0.16 dex below its neighbour and is the likely replica. Filled components and closed holes from the same page scan are one row each below. A component within 8 px of a stored centre or a dash core is not repeated.

Figure 3 (published p. 84): log p at 1850 K. The y axis is broken between -4.0 and -8.5. The x axis is S, M, A only.

| marker | place | evidence | decision |
|---|---|---|---|
| SiO, Al, O traces | S / M / A labels | broken y axis, categorical x | refused: schematic. S/M/A is not a numeric composition, and the break leaves the upper line uncalibrated. |


### 2002 Figure 2 stored

| id | pixel | symbol | decision |
|---|---|---|---|
| shornikov_2002_f2a_sio_01 | (5892.5, 2993.5) | filled diamond VIII | read: T=1724.7 K, -log 5.194, 84.14 mol% Al2O3, p=6.397348e-06 atm |
| shornikov_2002_f2a_sio_02 | (5898.5, 2903.8) | filled diamond VIII | read: T=1725.5 K, -log 5.033, 84.14 mol% Al2O3, p=9.268298e-06 atm |
| shornikov_2002_f2a_sio_03 | (6042.5, 2897.7) | filled diamond VIII | read: T=1743.2 K, -log 5.022, 84.14 mol% Al2O3, p=9.506048e-06 atm |

### 2002 Figure 2 dash cores

| marker | pixel | size | decision |
|---|---|---|---|
| dash | (5512, 3410) | 24x24 core | refused: 2 px opening leaves a dash core of the dotted fit, area about 417. Not a legend symbol. |
| dash | (5596, 3367) | 24x24 core | refused: 2 px opening leaves a dash core of the dotted fit, area about 417. Not a legend symbol. |
| dash | (5859, 3272) | 24x24 core | refused: 2 px opening leaves a dash core of the dotted fit, area about 417. Not a legend symbol. |
| dash | (5889, 3123) | 24x24 core | refused: 2 px opening leaves a dash core of the dotted fit, area about 417. Not a legend symbol. |
| dash | (6068, 3028) | 24x24 core | refused: 2 px opening leaves a dash core of the dotted fit, area about 417. Not a legend symbol. |
| dash | (6873, 2506) | 24x24 core | refused: 2 px opening leaves a dash core of the dotted fit, area about 417. Not a legend symbol. |
| dash | (6928, 2458) | 24x24 core | refused: 2 px opening leaves a dash core of the dotted fit, area about 417. Not a legend symbol. |
| dash | (7137, 2489) | 24x24 core | refused: 2 px opening leaves a dash core of the dotted fit, area about 417. Not a legend symbol. |
| dash | (7200, 2276) | 24x24 core | refused: 2 px opening leaves a dash core of the dotted fit, area about 417. Not a legend symbol. |
| dash | (7269, 2230) | 24x24 core | refused: 2 px opening leaves a dash core of the dotted fit, area about 417. Not a legend symbol. |
| dash | (7390, 2162) | 24x24 core | refused: 2 px opening leaves a dash core of the dotted fit, area about 417. Not a legend symbol. |
| dash | (7521, 2130) | 24x24 core | refused: 2 px opening leaves a dash core of the dotted fit, area about 417. Not a legend symbol. |
| dash | (8363, 1574) | 24x24 core | refused: 2 px opening leaves a dash core of the dotted fit, area about 417. Not a legend symbol. |
| dash | (8853, 1261) | 24x24 core | refused: 2 px opening leaves a dash core of the dotted fit, area about 417. Not a legend symbol. |

### 2002 panel a

| kind | pixel | size | decision |
|---|---|---|---|
| F | (8502.1, 1564.2) | 72x96 n=2448 | deferred: overlapping, arc-fit needed (t-1380) |
| F | (5580.5, 1553.5) | 36x36 n=864 | deferred: overlapping, arc-fit needed (t-1380) |
| F | (5883.4, 1579.8) | 84x72 n=3600 | refused: closed interior only (84x72 px, n=3600). Symbol class was not confirmed as one legend symbol clear of other ink, so no value was stored. |
| F | (8363.0, 1574.0) | 48x48 n=1152 | refused: 2 px opening leaves a 24x24 dash core of the dotted fit (area about 417), not a legend symbol. |
| F | (8051.0, 1667.0) | 36x72 n=1152 | deferred: overlapping, arc-fit needed (t-1380) |
| F | (8109.5, 1676.0) | 60x48 n=1152 | deferred: overlapping, arc-fit needed (t-1380) |
| F | (8180.9, 1679.9) | 72x48 n=1440 | refused: closed interior only (72x48 px, n=1440). Symbol class was not confirmed as one legend symbol clear of other ink, so no value was stored. |
| F | (8643.8, 1668.2) | 60x36 n=1584 | refused: closed interior only (60x36 px, n=1584). Symbol class was not confirmed as one legend symbol clear of other ink, so no value was stored. |
| F | (8124.5, 1724.6) | 48x36 n=1008 | deferred: overlapping, arc-fit needed (t-1380) |
| F | (5883.0, 1741.4) | 84x72 n=4176 | refused: closed interior only (84x72 px, n=4176). Symbol class was not confirmed as one legend symbol clear of other ink, so no value was stored. |
| F | (8558.2, 1746.1) | 48x60 n=2016 | refused: closed interior only (48x60 px, n=2016). Symbol class was not confirmed as one legend symbol clear of other ink, so no value was stored. |
| F | (8478.3, 1761.3) | 53x60 n=1389 | refused: closed interior only (53x60 px, n=1389). Symbol class was not confirmed as one legend symbol clear of other ink, so no value was stored. |
| F | (8426.3, 1813.2) | 60x59 n=2688 | refused: closed interior only (60x59 px, n=2688). Symbol class was not confirmed as one legend symbol clear of other ink, so no value was stored. |
| F | (8338.9, 1837.2) | 60x40 n=953 | refused: closed interior only (60x40 px, n=953). Symbol class was not confirmed as one legend symbol clear of other ink, so no value was stored. |
| F | (5920.1, 1903.4) | 72x96 n=3888 | refused: closed interior only (72x96 px, n=3888). Symbol class was not confirmed as one legend symbol clear of other ink, so no value was stored. |
| F | (8028.5, 1892.5) | 60x72 n=2592 | refused: closed interior only (60x72 px, n=2592). Symbol class was not confirmed as one legend symbol clear of other ink, so no value was stored. |
| F | (8207.0, 1907.0) | 48x45 n=965 | refused: closed interior only (48x45 px, n=965). Symbol class was not confirmed as one legend symbol clear of other ink, so no value was stored. |
| F | (7861.6, 2004.6) | 63x60 n=1926 | refused: closed interior only (63x60 px, n=1926). Symbol class was not confirmed as one legend symbol clear of other ink, so no value was stored. |
| F | (7665.1, 2009.9) | 48x36 n=1008 | deferred: overlapping, arc-fit needed (t-1380) |
| F | (7802.4, 2028.6) | 60x60 n=2736 | refused: closed interior only (60x60 px, n=2736). Symbol class was not confirmed as one legend symbol clear of other ink, so no value was stored. |
| F | (7561.5, 2041.5) | 60x60 n=1728 | deferred: overlapping, arc-fit needed (t-1380) |
| F | (5916.5, 2066.1) | 84x84 n=4320 | refused: closed interior only (84x84 px, n=4320). Symbol class was not confirmed as one legend symbol clear of other ink, so no value was stored. |
| F | (7542.5, 2080.0) | 48x60 n=1152 | deferred: overlapping, arc-fit needed (t-1380) |
| F | (7726.1, 2097.2) | 84x72 n=3600 | refused: closed interior only (84x72 px, n=3600). Symbol class was not confirmed as one legend symbol clear of other ink, so no value was stored. |
| F | (7776.5, 2132.5) | 60x72 n=2592 | refused: closed interior only (60x72 px, n=2592). Symbol class was not confirmed as one legend symbol clear of other ink, so no value was stored. |
| F | (7435.2, 2135.8) | 48x60 n=1296 | refused: closed interior only (48x60 px, n=1296). Symbol class was not confirmed as one legend symbol clear of other ink, so no value was stored. |
| F | (5922.0, 2223.1) | 72x84 n=3168 | refused: closed interior only (72x84 px, n=3168). Symbol class was not confirmed as one legend symbol clear of other ink, so no value was stored. |
| F | (7416.5, 2297.5) | 60x60 n=1728 | refused: closed interior only (60x60 px, n=1728). Symbol class was not confirmed as one legend symbol clear of other ink, so no value was stored. |
| F | (5916.5, 2384.9) | 84x84 n=4464 | refused: closed interior only (84x84 px, n=4464). Symbol class was not confirmed as one legend symbol clear of other ink, so no value was stored. |
| F | (7160.1, 2374.3) | 96x60 n=2736 | refused: closed interior only (96x60 px, n=2736). Symbol class was not confirmed as one legend symbol clear of other ink, so no value was stored. |
| F | (7236.5, 2385.5) | 60x48 n=1728 | refused: closed interior only (60x48 px, n=1728). Symbol class was not confirmed as one legend symbol clear of other ink, so no value was stored. |
| F | (6666.6, 2620.1) | 72x60 n=2882 | refused: closed interior only (72x60 px, n=2882). Symbol class was not confirmed as one legend symbol clear of other ink, so no value was stored. |
| F | (6614.2, 2673.6) | 72x96 n=3024 | refused: closed interior only (72x96 px, n=3024). Symbol class was not confirmed as one legend symbol clear of other ink, so no value was stored. |
| F | (6503.0, 2741.0) | 60x48 n=1152 | refused: closed interior only (60x48 px, n=1152). Symbol class was not confirmed as one legend symbol clear of other ink, so no value was stored. |
| F | (6442.8, 2787.9) | 72x60 n=3024 | refused: closed interior only (72x60 px, n=3024). Symbol class was not confirmed as one legend symbol clear of other ink, so no value was stored. |
| F | (6307.4, 2807.2) | 72x60 n=2736 | refused: closed interior only (72x60 px, n=2736). Symbol class was not confirmed as one legend symbol clear of other ink, so no value was stored. |
| F | (6118.3, 2971.1) | 72x36 n=1584 | deferred: overlapping, arc-fit needed (t-1380) |
| F | (6098.0, 3001.0) | 60x36 n=1152 | deferred: overlapping, arc-fit needed (t-1380) |
| F | (5639.9, 3126.9) | 61x96 n=3867 | refused: closed interior only (61x96 px, n=3867). Symbol class was not confirmed as one legend symbol clear of other ink, so no value was stored. |
| H | (8530.8, 1510.9) | 37x56 n=1285 | refused: closed interior only (37x56 px, n=1285). Symbol class was not confirmed as one legend symbol clear of other ink, so no value was stored. |
| H | (8496.4, 1554.5) | 36x59 n=983 | deferred: overlapping, arc-fit needed (t-1380) |
| H | (5580.5, 1578.9) | 36x36 n=1008 | deferred: overlapping, arc-fit needed (t-1380) |
| H | (8141.9, 1634.5) | 70x64 n=1894 | refused: closed interior only (70x64 px, n=1894). Symbol class was not confirmed as one legend symbol clear of other ink, so no value was stored. |
| H | (8129.9, 1689.0) | 54x48 n=1295 | deferred: overlapping, arc-fit needed (t-1380) |
| H | (8023.7, 1689.4) | 24x28 n=324 | deferred: overlapping, arc-fit needed (t-1380) |
| H | (6155.9, 1716.4) | 57x78 n=2033 | refused: closed interior only (57x78 px, n=2033). Symbol class was not confirmed as one legend symbol clear of other ink, so no value was stored. |
| H | (5579.4, 1742.4) | 60x48 n=1584 | refused: closed interior only (60x48 px, n=1584). Symbol class was not confirmed as one legend symbol clear of other ink, so no value was stored. |
| H | (6092.1, 1890.3) | 40x66 n=790 | refused: closed interior only (40x66 px, n=790). Symbol class was not confirmed as one legend symbol clear of other ink, so no value was stored. |
| H | (7656.3, 1980.6) | 56x46 n=1204 | deferred: overlapping, arc-fit needed (t-1380) |
| H | (6095.1, 2036.9) | 60x79 n=1855 | refused: closed interior only (60x79 px, n=1855). Symbol class was not confirmed as one legend symbol clear of other ink, so no value was stored. |
| H | (7552.5, 2058.5) | 24x24 n=432 | deferred: overlapping, arc-fit needed (t-1380) |
| H | (7511.1, 2085.3) | 36x24 n=551 | deferred: overlapping, arc-fit needed (t-1380) |
| H | (7520.0, 2164.6) | 72x82 n=1146 | refused: closed interior only (72x82 px, n=1146). Symbol class was not confirmed as one legend symbol clear of other ink, so no value was stored. |
| H | (6094.2, 2203.9) | 47x68 n=1312 | refused: closed interior only (47x68 px, n=1312). Symbol class was not confirmed as one legend symbol clear of other ink, so no value was stored. |
| H | (7114.6, 2322.5) | 28x30 n=368 | refused: closed interior only (28x30 px, n=368). Symbol class was not confirmed as one legend symbol clear of other ink, so no value was stored. |
| H | (7075.9, 2357.1) | 29x35 n=348 | refused: closed interior only (29x35 px, n=348). Symbol class was not confirmed as one legend symbol clear of other ink, so no value was stored. |
| H | (7041.1, 2412.5) | 34x22 n=397 | refused: closed interior only (34x22 px, n=397). Symbol class was not confirmed as one legend symbol clear of other ink, so no value was stored. |
| H | (6944.9, 2441.0) | 36x32 n=385 | refused: closed interior only (36x32 px, n=385). Symbol class was not confirmed as one legend symbol clear of other ink, so no value was stored. |
| H | (6720.9, 2572.4) | 26x31 n=375 | refused: closed interior only (26x31 px, n=375). Symbol class was not confirmed as one legend symbol clear of other ink, so no value was stored. |
| H | (6108.5, 2981.5) | 36x24 n=576 | deferred: overlapping, arc-fit needed (t-1380) |

### 2002 panel b

| kind | pixel | size | decision |
|---|---|---|---|
| F | (8473.2, 4188.0) | 96x83 n=4057 | refused: closed interior only (96x83 px, n=4057). Symbol class was not confirmed as one legend symbol clear of other ink, so no value was stored. |
| F | (8549.8, 4187.5) | 40x38 n=812 | refused: closed interior only (40x38 px, n=812). Symbol class was not confirmed as one legend symbol clear of other ink, so no value was stored. |
| F | (8247.5, 4254.7) | 48x60 n=1872 | refused: closed interior only (48x60 px, n=1872). Symbol class was not confirmed as one legend symbol clear of other ink, so no value was stored. |
| F | (8423.0, 4259.9) | 73x48 n=1700 | refused: closed interior only (73x48 px, n=1700). Symbol class was not confirmed as one legend symbol clear of other ink, so no value was stored. |
| F | (8290.9, 4287.0) | 60x48 n=1872 | refused: closed interior only (60x48 px, n=1872). Symbol class was not confirmed as one legend symbol clear of other ink, so no value was stored. |
| F | (8194.3, 4330.1) | 60x84 n=3312 | refused: closed interior only (60x84 px, n=3312). Symbol class was not confirmed as one legend symbol clear of other ink, so no value was stored. |
| F | (8379.6, 4310.2) | 72x49 n=2298 | refused: closed interior only (72x49 px, n=2298). Symbol class was not confirmed as one legend symbol clear of other ink, so no value was stored. |
| F | (8051.0, 4322.5) | 60x48 n=2304 | refused: closed interior only (60x48 px, n=2304). Symbol class was not confirmed as one legend symbol clear of other ink, so no value was stored. |
| F | (7963.5, 4374.1) | 60x60 n=2160 | refused: closed interior only (60x60 px, n=2160). Symbol class was not confirmed as one legend symbol clear of other ink, so no value was stored. |
| F | (8149.7, 4380.2) | 48x60 n=1872 | refused: closed interior only (48x60 px, n=1872). Symbol class was not confirmed as one legend symbol clear of other ink, so no value was stored. |
| F | (7885.7, 4523.1) | 60x72 n=2880 | refused: closed interior only (60x72 px, n=2880). Symbol class was not confirmed as one legend symbol clear of other ink, so no value was stored. |
| F | (7794.1, 4550.4) | 60x48 n=2016 | refused: closed interior only (60x48 px, n=2016). Symbol class was not confirmed as one legend symbol clear of other ink, so no value was stored. |
| F | (8482.4, 4573.5) | 48x48 n=2016 | refused: closed interior only (48x48 px, n=2016). Symbol class was not confirmed as one legend symbol clear of other ink, so no value was stored. |
| F | (7244.5, 4615.5) | 24x36 n=864 | refused: closed interior only (24x36 px, n=864). Symbol class was not confirmed as one legend symbol clear of other ink, so no value was stored. |
| F | (7600.3, 4656.6) | 84x72 n=3744 | deferred: overlapping, arc-fit needed (t-1380) |
| F | (7479.5, 4707.5) | 48x59 n=1848 | deferred: overlapping, arc-fit needed (t-1380) |
| F | (8172.4, 4729.0) | 48x47 n=1692 | refused: closed interior only (48x47 px, n=1692). Symbol class was not confirmed as one legend symbol clear of other ink, so no value was stored. |
| F | (8649.5, 4735.0) | 72x59 n=4248 | refused: closed interior only (72x59 px, n=4248). Symbol class was not confirmed as one legend symbol clear of other ink, so no value was stored. |
| F | (7275.5, 4743.1) | 48x59 n=1848 | refused: closed interior only (48x59 px, n=1848). Symbol class was not confirmed as one legend symbol clear of other ink, so no value was stored. |
| F | (6938.5, 4754.1) | 48x48 n=1584 | refused: closed interior only (48x48 px, n=1584). Symbol class was not confirmed as one legend symbol clear of other ink, so no value was stored. |
| F | (7574.5, 4762.7) | 60x60 n=2448 | refused: closed interior only (60x60 px, n=2448). Symbol class was not confirmed as one legend symbol clear of other ink, so no value was stored. |
| F | (7761.0, 4774.3) | 73x60 n=1464 | refused: closed interior only (73x60 px, n=1464). Symbol class was not confirmed as one legend symbol clear of other ink, so no value was stored. |
| F | (7275.6, 4816.3) | 60x48 n=1584 | deferred: overlapping, arc-fit needed (t-1380) |
| F | (7342.9, 4848.0) | 60x48 n=1354 | refused: closed interior only (60x48 px, n=1354). Symbol class was not confirmed as one legend symbol clear of other ink, so no value was stored. |
| F | (7688.0, 4868.7) | 84x84 n=3168 | deferred: overlapping, arc-fit needed (t-1380) |
| F | (8649.5, 4890.5) | 72x60 n=3744 | refused: closed interior only (72x60 px, n=3744). Symbol class was not confirmed as one legend symbol clear of other ink, so no value was stored. |
| F | (6614.0, 4930.9) | 49x48 n=1632 | refused: closed interior only (49x48 px, n=1632). Symbol class was not confirmed as one legend symbol clear of other ink, so no value was stored. |
| F | (7093.8, 4964.0) | 60x41 n=829 | refused: closed interior only (60x41 px, n=829). Symbol class was not confirmed as one legend symbol clear of other ink, so no value was stored. |
| F | (6880.6, 4992.3) | 72x95 n=3828 | refused: closed interior only (72x95 px, n=3828). Symbol class was not confirmed as one legend symbol clear of other ink, so no value was stored. |
| F | (7469.6, 4962.0) | 72x35 n=1260 | refused: closed interior only (72x35 px, n=1260). Symbol class was not confirmed as one legend symbol clear of other ink, so no value was stored. |
| F | (8649.5, 5047.5) | 72x72 n=3456 | refused: closed interior only (72x72 px, n=3456). Symbol class was not confirmed as one legend symbol clear of other ink, so no value was stored. |
| F | (8358.2, 5064.2) | 72x36 n=1296 | deferred: overlapping, arc-fit needed (t-1380) |
| F | (6707.3, 5109.2) | 96x84 n=3744 | refused: closed interior only (96x84 px, n=3744). Symbol class was not confirmed as one legend symbol clear of other ink, so no value was stored. |
| F | (7284.1, 5099.5) | 36x48 n=1440 | refused: closed interior only (36x48 px, n=1440). Symbol class was not confirmed as one legend symbol clear of other ink, so no value was stored. |
| F | (6597.9, 5187.6) | 85x72 n=3792 | deferred: overlapping, arc-fit needed (t-1380) |
| F | (8649.5, 5199.2) | 72x71 n=3432 | refused: closed interior only (72x71 px, n=3432). Symbol class was not confirmed as one legend symbol clear of other ink, so no value was stored. |
| F | (6723.2, 5216.2) | 60x59 n=2280 | refused: closed interior only (60x59 px, n=2280). Symbol class was not confirmed as one legend symbol clear of other ink, so no value was stored. |
| F | (6397.5, 5233.0) | 60x47 n=1692 | refused: closed interior only (60x47 px, n=1692). Symbol class was not confirmed as one legend symbol clear of other ink, so no value was stored. |
| F | (7022.5, 5270.5) | 36x36 n=864 | deferred: overlapping, arc-fit needed (t-1380) |
| F | (6325.5, 5313.1) | 60x48 n=1872 | refused: closed interior only (60x48 px, n=1872). Symbol class was not confirmed as one legend symbol clear of other ink, so no value was stored. |
| F | (5917.5, 5320.5) | 36x36 n=1296 | refused: closed interior only (36x36 px, n=1296). Symbol class was not confirmed as one legend symbol clear of other ink, so no value was stored. |
| F | (8681.5, 5350.5) | 72x72 n=3456 | refused: closed interior only (72x72 px, n=3456). Symbol class was not confirmed as one legend symbol clear of other ink, so no value was stored. |
| F | (6822.5, 5376.5) | 36x48 n=864 | refused: closed interior only (36x48 px, n=864). Symbol class was not confirmed as one legend symbol clear of other ink, so no value was stored. |
| F | (6707.7, 5493.6) | 72x71 n=1848 | refused: closed interior only (72x71 px, n=1848). Symbol class was not confirmed as one legend symbol clear of other ink, so no value was stored. |
| F | (8686.3, 5501.6) | 72x83 n=3096 | refused: closed interior only (72x83 px, n=3096). Symbol class was not confirmed as one legend symbol clear of other ink, so no value was stored. |
| F | (8685.5, 5653.7) | 72x72 n=2880 | refused: closed interior only (72x72 px, n=2880). Symbol class was not confirmed as one legend symbol clear of other ink, so no value was stored. |
| F | (6387.9, 5651.9) | 72x60 n=1440 | deferred: overlapping, arc-fit needed (t-1380) |
| F | (6301.5, 5755.6) | 36x71 n=1116 | refused: closed interior only (36x71 px, n=1116). Symbol class was not confirmed as one legend symbol clear of other ink, so no value was stored. |
| F | (8682.1, 5807.9) | 84x84 n=4032 | refused: closed interior only (84x84 px, n=4032). Symbol class was not confirmed as one legend symbol clear of other ink, so no value was stored. |
| F | (8681.8, 5953.4) | 60x60 n=3024 | refused: closed interior only (60x60 px, n=3024). Symbol class was not confirmed as one legend symbol clear of other ink, so no value was stored. |
| F | (5905.5, 5968.5) | 36x48 n=864 | refused: closed interior only (36x48 px, n=864). Symbol class was not confirmed as one legend symbol clear of other ink, so no value was stored. |
| F | (8676.6, 6110.1) | 60x60 n=3024 | refused: closed interior only (60x60 px, n=3024). Symbol class was not confirmed as one legend symbol clear of other ink, so no value was stored. |
| H | (8493.2, 4143.6) | 49x60 n=1560 | refused: closed interior only (49x60 px, n=1560). Symbol class was not confirmed as one legend symbol clear of other ink, so no value was stored. |
| H | (7971.5, 4419.7) | 38x36 n=421 | refused: closed interior only (38x36 px, n=421). Symbol class was not confirmed as one legend symbol clear of other ink, so no value was stored. |
| H | (7673.9, 4580.2) | 35x44 n=771 | refused: closed interior only (35x44 px, n=771). Symbol class was not confirmed as one legend symbol clear of other ink, so no value was stored. |
| H | (7610.7, 4621.9) | 41x28 n=561 | deferred: overlapping, arc-fit needed (t-1380) |
| H | (7908.9, 4670.0) | 72x72 n=2106 | refused: closed interior only (72x72 px, n=2106). Symbol class was not confirmed as one legend symbol clear of other ink, so no value was stored. |
| H | (7491.1, 4681.4) | 39x29 n=584 | deferred: overlapping, arc-fit needed (t-1380) |
| H | (7841.4, 4711.8) | 31x21 n=309 | refused: closed interior only (31x21 px, n=309). Symbol class was not confirmed as one legend symbol clear of other ink, so no value was stored. |
| H | (7290.6, 4794.4) | 24x17 n=252 | deferred: overlapping, arc-fit needed (t-1380) |
| H | (7674.5, 4838.5) | 24x24 n=432 | deferred: overlapping, arc-fit needed (t-1380) |
| H | (8359.5, 5048.2) | 48x36 n=1296 | deferred: overlapping, arc-fit needed (t-1380) |
| H | (6599.8, 5149.7) | 52x45 n=1110 | deferred: overlapping, arc-fit needed (t-1380) |
| H | (8859.8, 5174.7) | 51x79 n=1690 | refused: closed interior only (51x79 px, n=1690). Symbol class was not confirmed as one legend symbol clear of other ink, so no value was stored. |
| H | (8361.5, 5198.5) | 48x36 n=1152 | refused: closed interior only (48x36 px, n=1152). Symbol class was not confirmed as one legend symbol clear of other ink, so no value was stored. |
| H | (7065.8, 5213.8) | 24x15 n=258 | refused: closed interior only (24x15 px, n=258). Symbol class was not confirmed as one legend symbol clear of other ink, so no value was stored. |
| H | (6996.5, 5256.5) | 36x36 n=864 | deferred: overlapping, arc-fit needed (t-1380) |
| H | (6908.1, 5298.5) | 26x36 n=475 | refused: closed interior only (26x36 px, n=475). Symbol class was not confirmed as one legend symbol clear of other ink, so no value was stored. |
| H | (8815.5, 5498.1) | 33x55 n=589 | refused: closed interior only (33x55 px, n=589). Symbol class was not confirmed as one legend symbol clear of other ink, so no value was stored. |
| H | (8806.1, 5632.4) | 56x83 n=1955 | refused: closed interior only (56x83 px, n=1955). Symbol class was not confirmed as one legend symbol clear of other ink, so no value was stored. |
| H | (6345.9, 5665.6) | 36x40 n=713 | deferred: overlapping, arc-fit needed (t-1380) |
| H | (8804.2, 5795.6) | 45x74 n=1049 | refused: closed interior only (45x74 px, n=1049). Symbol class was not confirmed as one legend symbol clear of other ink, so no value was stored. |
| H | (8780.5, 5954.9) | 51x55 n=1037 | refused: closed interior only (51x55 px, n=1037). Symbol class was not confirmed as one legend symbol clear of other ink, so no value was stored. |

### 2002 panel c

| kind | pixel | size | decision |
|---|---|---|---|
| F | (8825.4, 6980.8) | 74x96 n=3704 | deferred: overlapping, arc-fit needed (t-1380) |
| F | (5566.0, 6971.0) | 48x60 n=1152 | deferred: overlapping, arc-fit needed (t-1380) |
| F | (5891.5, 6984.5) | 84x72 n=4320 | refused: closed interior only (84x72 px, n=4320). Symbol class was not confirmed as one legend symbol clear of other ink, so no value was stored. |
| F | (8643.7, 6972.1) | 60x48 n=2448 | deferred: overlapping, arc-fit needed (t-1380) |
| F | (5599.0, 7004.0) | 60x48 n=1152 | deferred: overlapping, arc-fit needed (t-1380) |
| F | (8737.4, 7044.4) | 60x64 n=1210 | refused: closed interior only (60x64 px, n=1210). Symbol class was not confirmed as one legend symbol clear of other ink, so no value was stored. |
| F | (8650.8, 7122.1) | 48x48 n=1537 | refused: closed interior only (48x48 px, n=1537). Symbol class was not confirmed as one legend symbol clear of other ink, so no value was stored. |
| F | (5891.5, 7150.7) | 84x72 n=3744 | refused: closed interior only (84x72 px, n=3744). Symbol class was not confirmed as one legend symbol clear of other ink, so no value was stored. |
| F | (8355.7, 7273.1) | 60x47 n=2400 | refused: closed interior only (60x47 px, n=2400). Symbol class was not confirmed as one legend symbol clear of other ink, so no value was stored. |
| F | (8669.5, 7272.0) | 24x47 n=1128 | refused: closed interior only (24x47 px, n=1128). Symbol class was not confirmed as one legend symbol clear of other ink, so no value was stored. |
| F | (8215.5, 7303.9) | 60x60 n=2160 | deferred: overlapping, arc-fit needed (t-1380) |
| F | (5891.5, 7310.5) | 84x72 n=4032 | refused: closed interior only (84x72 px, n=4032). Symbol class was not confirmed as one legend symbol clear of other ink, so no value was stored. |
| F | (8307.7, 7338.1) | 60x60 n=2880 | refused: closed interior only (60x60 px, n=2880). Symbol class was not confirmed as one legend symbol clear of other ink, so no value was stored. |
| F | (8216.3, 7389.5) | 72x60 n=2160 | refused: closed interior only (72x60 px, n=2160). Symbol class was not confirmed as one legend symbol clear of other ink, so no value was stored. |
| F | (8837.5, 7391.5) | 24x48 n=1152 | refused: closed interior only (24x48 px, n=1152). Symbol class was not confirmed as one legend symbol clear of other ink, so no value was stored. |
| F | (8760.7, 7439.5) | 60x72 n=2880 | refused: closed interior only (60x72 px, n=2880). Symbol class was not confirmed as one legend symbol clear of other ink, so no value was stored. |
| F | (5927.5, 7472.1) | 84x84 n=4032 | deferred: overlapping, arc-fit needed (t-1380) |
| F | (8059.5, 7471.9) | 60x60 n=2160 | refused: closed interior only (60x60 px, n=2160). Symbol class was not confirmed as one legend symbol clear of other ink, so no value was stored. |
| F | (8285.5, 7481.5) | 36x48 n=864 | refused: closed interior only (36x48 px, n=864). Symbol class was not confirmed as one legend symbol clear of other ink, so no value was stored. |
| F | (8591.5, 7547.5) | 36x48 n=1728 | refused: closed interior only (36x48 px, n=1728). Symbol class was not confirmed as one legend symbol clear of other ink, so no value was stored. |
| F | (7904.0, 7606.8) | 96x84 n=4896 | deferred: overlapping, arc-fit needed (t-1380) |
| F | (8015.5, 7609.5) | 60x48 n=1728 | refused: closed interior only (60x48 px, n=1728). Symbol class was not confirmed as one legend symbol clear of other ink, so no value was stored. |
| F | (5927.1, 7636.3) | 84x84 n=4176 | refused: closed interior only (84x84 px, n=4176). Symbol class was not confirmed as one legend symbol clear of other ink, so no value was stored. |
| F | (8375.5, 7679.5) | 36x48 n=1728 | refused: closed interior only (36x48 px, n=1728). Symbol class was not confirmed as one legend symbol clear of other ink, so no value was stored. |
| F | (8002.4, 7705.1) | 36x48 n=1584 | refused: closed interior only (36x48 px, n=1584). Symbol class was not confirmed as one legend symbol clear of other ink, so no value was stored. |
| F | (7917.2, 7734.1) | 60x60 n=3024 | refused: closed interior only (60x60 px, n=3024). Symbol class was not confirmed as one legend symbol clear of other ink, so no value was stored. |
| F | (5925.8, 7799.8) | 72x84 n=3024 | refused: closed interior only (72x84 px, n=3024). Symbol class was not confirmed as one legend symbol clear of other ink, so no value was stored. |
| F | (7670.8, 7822.6) | 64x48 n=1968 | deferred: overlapping, arc-fit needed (t-1380) |
| F | (7632.0, 7898.5) | 48x98 n=2365 | refused: closed interior only (48x98 px, n=2365). Symbol class was not confirmed as one legend symbol clear of other ink, so no value was stored. |
| F | (7466.9, 7894.8) | 84x83 n=4116 | refused: closed interior only (84x83 px, n=4116). Symbol class was not confirmed as one legend symbol clear of other ink, so no value was stored. |
| F | (7690.0, 7889.0) | 72x59 n=3396 | refused: closed interior only (72x59 px, n=3396). Symbol class was not confirmed as one legend symbol clear of other ink, so no value was stored. |
| F | (7561.0, 7898.7) | 45x47 n=1412 | refused: closed interior only (45x47 px, n=1412). Symbol class was not confirmed as one legend symbol clear of other ink, so no value was stored. |
| F | (7376.2, 7949.2) | 84x60 n=2592 | deferred: overlapping, arc-fit needed (t-1380) |
| F | (7317.5, 7966.5) | 48x48 n=1728 | refused: closed interior only (48x48 px, n=1728). Symbol class was not confirmed as one legend symbol clear of other ink, so no value was stored. |
| F | (7596.6, 8000.9) | 36x48 n=1584 | refused: closed interior only (36x48 px, n=1584). Symbol class was not confirmed as one legend symbol clear of other ink, so no value was stored. |
| F | (5934.0, 8122.5) | 60x72 n=3168 | refused: closed interior only (60x72 px, n=3168). Symbol class was not confirmed as one legend symbol clear of other ink, so no value was stored. |
| F | (6971.1, 8211.6) | 72x72 n=3098 | refused: closed interior only (72x72 px, n=3098). Symbol class was not confirmed as one legend symbol clear of other ink, so no value was stored. |
| F | (7337.5, 8194.5) | 24x48 n=1152 | refused: closed interior only (24x48 px, n=1152). Symbol class was not confirmed as one legend symbol clear of other ink, so no value was stored. |
| F | (5925.9, 8284.5) | 60x60 n=3168 | refused: closed interior only (60x60 px, n=3168). Symbol class was not confirmed as one legend symbol clear of other ink, so no value was stored. |
| F | (7061.5, 8350.5) | 24x48 n=1152 | refused: closed interior only (24x48 px, n=1152). Symbol class was not confirmed as one legend symbol clear of other ink, so no value was stored. |
| F | (6491.5, 8488.5) | 60x36 n=1008 | deferred: overlapping, arc-fit needed (t-1380) |
| F | (6119.5, 8703.5) | 36x36 n=1008 | deferred: overlapping, arc-fit needed (t-1380) |
| F | (6085.5, 8727.5) | 36x36 n=864 | deferred: overlapping, arc-fit needed (t-1380) |
| F | (6467.5, 8733.5) | 36x48 n=1728 | refused: closed interior only (36x48 px, n=1728). Symbol class was not confirmed as one legend symbol clear of other ink, so no value was stored. |
| F | (5963.5, 8798.0) | 36x60 n=1152 | refused: closed interior only (36x60 px, n=1152). Symbol class was not confirmed as one legend symbol clear of other ink, so no value was stored. |
| H | (8836.7, 6944.1) | 30x39 n=478 | deferred: overlapping, arc-fit needed (t-1380) |
| H | (5562.4, 6944.4) | 32x29 n=373 | deferred: overlapping, arc-fit needed (t-1380) |
| H | (5584.1, 6985.9) | 48x48 n=1872 | deferred: overlapping, arc-fit needed (t-1380) |
| H | (8668.0, 7006.8) | 60x66 n=1728 | deferred: overlapping, arc-fit needed (t-1380) |
| H | (8595.1, 7049.1) | 48x36 n=791 | refused: closed interior only (48x36 px, n=791). Symbol class was not confirmed as one legend symbol clear of other ink, so no value was stored. |
| H | (5583.5, 7149.2) | 48x36 n=1296 | refused: closed interior only (48x36 px, n=1296). Symbol class was not confirmed as one legend symbol clear of other ink, so no value was stored. |
| H | (8092.9, 7277.9) | 32x40 n=351 | refused: closed interior only (32x40 px, n=351). Symbol class was not confirmed as one legend symbol clear of other ink, so no value was stored. |
| H | (8211.0, 7280.8) | 37x29 n=351 | deferred: overlapping, arc-fit needed (t-1380) |
| H | (5583.5, 7310.8) | 48x36 n=1296 | refused: closed interior only (48x36 px, n=1296). Symbol class was not confirmed as one legend symbol clear of other ink, so no value was stored. |
| H | (5917.6, 7439.5) | 36x28 n=418 | deferred: overlapping, arc-fit needed (t-1380) |
| H | (6079.4, 7468.0) | 31x47 n=448 | refused: closed interior only (31x47 px, n=448). Symbol class was not confirmed as one legend symbol clear of other ink, so no value was stored. |
| H | (7896.3, 7564.0) | 67x60 n=1243 | deferred: overlapping, arc-fit needed (t-1380) |
| H | (6075.4, 7609.5) | 58x75 n=1872 | refused: closed interior only (58x75 px, n=1872). Symbol class was not confirmed as one legend symbol clear of other ink, so no value was stored. |
| H | (7671.5, 7729.5) | 24x24 n=432 | refused: closed interior only (24x24 px, n=432). Symbol class was not confirmed as one legend symbol clear of other ink, so no value was stored. |
| H | (7639.5, 7849.5) | 36x36 n=864 | deferred: overlapping, arc-fit needed (t-1380) |
| H | (7400.1, 7947.5) | 34x22 n=397 | deferred: overlapping, arc-fit needed (t-1380) |
| H | (7118.2, 8027.8) | 47x48 n=978 | deferred: overlapping, arc-fit needed (t-1380) |
| H | (7421.8, 8027.5) | 28x27 n=253 | refused: closed interior only (28x27 px, n=253). Symbol class was not confirmed as one legend symbol clear of other ink, so no value was stored. |
| H | (7139.5, 8059.5) | 36x24 n=576 | deferred: overlapping, arc-fit needed (t-1380) |
| H | (6045.5, 8112.9) | 51x55 n=1037 | refused: closed interior only (51x55 px, n=1037). Symbol class was not confirmed as one legend symbol clear of other ink, so no value was stored. |
| H | (6742.9, 8326.2) | 65x46 n=811 | refused: closed interior only (65x46 px, n=811). Symbol class was not confirmed as one legend symbol clear of other ink, so no value was stored. |
| H | (6475.0, 8483.5) | 53x45 n=678 | deferred: overlapping, arc-fit needed (t-1380) |

Checked against the 600 dpi renders of PDF pages 2 and 3, and the 1200 dpi render of PDF page 2. No hand-corrected table cell, because there is no table.

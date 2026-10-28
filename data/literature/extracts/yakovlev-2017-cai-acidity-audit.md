# Yakovlev 2017 CAI acidity — figure digitisation audit (t1374)

Source `yakovlev-2017-cai-acidity`. Worker t1374-fig-t1. Render 600 dpi. The figures are embedded images, not vectors. Filled-marker centres are centroids of ink at least 8 px from the background, inside the peak's Voronoi cell. Open-marker centres are hole centroids. Hand-measured centres are named in the decision row where a neighbour, the frame, or a crossing line moved the raw peak.

Half-ticks and unlabelled frame corners were not fitted. Log axes are fitted in the printed log value.

## Tick calibration

| panel | axis | labelled ticks (px = value) | max |residual| |
| --- | --- | --- | --- |
| fig1 | x | 1065.5=1000, 1525.5=1500, 1984.5=2000 | 0.36271 |
| fig1 | y | 519=-3, 740=-4, 960=-5, 1180=-6, 1402=-7, 1622=-8 | 0.00363 |
| fig2 | x | 748=1400, 1198=1600, 1650=1800, 2100=2000 | 0.26611 |
| fig2 | y | 2598=-4, 2872=-5, 3146=-6, 3420=-7, 3695=-8 | 0.00146 |
| fig3 | x | 3125.5=20, 3455.5=40, 3786.5=60, 4117.5=80, 4446.5=100 | 0.04840 |
| fig3 | y | 580=80, 850=60, 1119=40, 1388=20 | 0.02971 |
| fig4 | x | 2912=1600, 3288=1800, 3664=2000, 4040=2200, 4416=2400 | 0.00000 |
| fig4 | y | 2606=-5, 2976=-6, 3344=-7, 3714=-8 | 0.00163 |
| fig5 | x | 709.5=0, 976.5=5, 1245.5=10, 1513.5=15, 1781.5=20, 2049.5=25 | 0.01314 |
| fig5 | y | 756=0.8, 992=0.6, 1230=0.4, 1468=0.2 | 0.00067 |
| fig6 | x | 739.5=0, 1050=5, 1360=10, 1668=15, 1978=20, 2286=25 | 0.01702 |
| fig6 | y | 3104=0.8, 3332=0.6, 3560=0.4, 3788=0.2 | 0.00000 |
| fig7 | x | 706.5=15, 977.5=20, 1248.5=25, 1519.5=30, 1791.5=35, 2062.5=40, 2333.5=45 | 0.00790 |
| fig7 | y | 515=1, 838=0.8, 1163=0.6, 1488=0.4, 1812=0.2, 2135=0 | 0.00053 |

Fig. 3 x = 0 is not in the fit. The printed 0 has no tick of the same length separate from the y-axis spine. Fig. 5 x = 30 is not in the fit. The stroke at that station is a filled marker, not a tick line. Fig. 5 and Fig. 6 y = 1.0 are not in the fit. Those ticks are covered by markers. Fig. 5 and Fig. 6 y = 0 are not labelled ticks. Values past the fitted ticks are the extrapolation and are not clamped.

## Census

| panel | read | deferred | refused |
| --- | --- | --- | --- |
| fig1 | 0 | 8 | 0 |
| fig2 | 27 | 0 | 0 |
| fig3 | 34 | 5 | 0 |
| fig4 | 58 | 0 | 0 |
| fig5 | 26 | 0 | 0 |
| fig6 | 21 | 0 | 1 |
| fig7 | 39 | 0 | 0 |


Curve-numeral check, fix round 1. On Fig. 4 the printed numeral 1 stands on the open-circle curve and the printed numeral 3 stands on the filled-triangle curve. The caption says (1) Ca and (3) SiO2, so those two series are stored that way. Numerals 2, 4, and 5 stay on the filled circles (Mg), diamonds (TiO2), and open squares (Al). A separate render of the same figure shows the same placement: label 1 beside the open circles (first stored point 1725.16 °C, log10 p = -7.770), label 2 beside the filled circles (first stored point 1578.44 °C, log10 p = -6.861), label 3 beside the filled triangles (first stored point 1577.49 °C, log10 p = -6.488), label 4 beside the diamonds, and label 5 beside the open squares. The stored counts are 14, 7, 16, 10, and 11. SiO2 partial pressures are the 8 Fig. 2 open circles plus these 16 Fig. 4 triangles. Fig. 2 matches its caption: (1) open circle SiO2, (2) filled circle Mg, (3) open square Ca, (4) filled square Al. Fig. 3 matches its caption: (1) filled circle MgO, (2) open circle SiO2, (3) downward triangle CaO, (4) open square Al2O3. Fig. 5 and Fig. 6 match their captions: (1) open circle SiO2, (2) filled circle SiO2, (3) open square MgO, (4) filled square MgO. Fig. 1 has no discrete markers. Fig. 7 is one filled-circle series. The Fig. 5 filled circle at 20 mol% CaO and the Fig. 6 open square under the filled square near 10 mol% Al2O3 were measured on the 1200 dpi render. The overlapped Fig. 5 MgO square was re-read from its own flat top and vertical sides on that render; the circle below it was excluded, and the square centre is above the circle.
Fig. 1 is eight continuous total-vapor-pressure curves (Kazenas and Tsvetkov, 1997). No discrete marker was found inside the frame, and the curves were not sampled.

Fig. 7 reads are not scored observations. The ordinate is an MgO/SiO2 ratio, and there is no quantity token for that ratio. The correlation r = 0.8 stays the author_estimate already on the context record.

Fig. 3 stores one residue-composition observation per oxide marker. The y axis, material loss in wt%, is the condition `mass_loss_wt_pct` on that point. The same ink is not also emitted as `mass_loss_fraction`.

## Standard state

Activities in Fig. 5 and Fig. 6, published page 254, equation (4): Equation (4), published page 254: p_i = p_i° a_i = p_i° x_i γ_i, where p_i is the partial pressure of component i in melt, p_i° is the partial pressure of pure component i, a_i is the activity of component i in the melt, x_i is the molar fraction of component i in the melt, and γ_i is the activity coefficient of component i in the melt. The physical phase of the pure component is not stated.

The pure-component phase is not stated, and this paper does not tabulate a pure-oxide melting temperature, so the phase is not inferred. Partial-pressure rows in Fig. 2 and Fig. 4 are the plotted pressures, not activities, and do not carry this sentence.

Fig. 5 composition closure is inferred from the caption. The axis is CaO mol%, the system is CaO–MgO–SiO2, and MgO/SiO2 = 2:3, so SiO2 = (1 − x_CaO) × 3/5 and MgO = (1 − x_CaO) × 2/5. Fig. 6 is not closed. The caption names CaO–MgO–SiO2 while the axis is Al2O3, and the discussion names MgO–Al2O3–SiO2. Only the printed Al2O3 axis is stored.

## SiO2 partial pressures

Fig. 2 series 1 and Fig. 4 series 3 are SiO2 partial pressures. The vapour rail sends SiO and SiO2 formulas to SiO_evolution. The species is the caption's SiO2 and was not renamed.

## Decision table

| series | species | x | y | value | decision |
| --- | --- | --- | --- | --- | --- |
| fig1 line | — | — | — | K2O total vapor-pressure curve has no discrete marker; the line was not sampled | deferred |
| fig1 line | — | — | — | Na2O total vapor-pressure curve has no discrete marker; the line was not sampled | deferred |
| fig1 line | — | — | — | FeO total vapor-pressure curve has no discrete marker; the line was not sampled | deferred |
| fig1 line | — | — | — | SiO2 total vapor-pressure curve has no discrete marker; the line was not sampled | deferred |
| fig1 line | — | — | — | MgO total vapor-pressure curve has no discrete marker; the line was not sampled | deferred |
| fig1 line | — | — | — | CaO total vapor-pressure curve has no discrete marker; the line was not sampled | deferred |
| fig1 line | — | — | — | TiO2 total vapor-pressure curve has no discrete marker; the line was not sampled | deferred |
| fig1 line | — | — | — | Al2O3 total vapor-pressure curve has no discrete marker; the line was not sampled | deferred |
| fig2/1 open circle | SiO2 | 1626.1 | 2550.2 | T=1789.66 ± 0.52 °C; log10_p=-3.826 ± 0.004 | read |
| fig2/1 open circle | SiO2 | 1440.8 | 2596.9 | T=1707.45 ± 0.52 °C; log10_p=-3.997 ± 0.004 | read |
| fig2/1 open circle | SiO2 | 1256.5 | 2712.7 | T=1625.69 ± 0.52 °C; log10_p=-4.419 ± 0.004 | read |
| fig2/1 open circle | SiO2 | 1101.1 | 2770.5 | T=1556.74 ± 0.52 °C; log10_p=-4.630 ± 0.004 | read |
| fig2/1 open circle | SiO2 | 1808.5 | 2801.0 | T=1870.59 ± 0.52 °C; log10_p=-4.741 ± 0.004 | read |
| fig2/1 open circle | SiO2 | 906.8 | 2967.8 | T=1470.54 ± 0.52 °C; log10_p=-5.349 ± 0.004 | read |
| fig2/3 open square | Ca | 1963.5 | 3029.5 | T=1939.35 ± 0.52 °C; log10_p=-5.574 ± 0.004 | read |
| fig2/3 open square | Ca | 2322.1 | 3060.1 | T=2098.45 ± 0.52 °C; log10_p=-5.686 ± 0.004 | read |
| fig2/1 open circle | SiO2 | 742.5 | 3199.7 | T=1397.65 ± 0.52 °C; log10_p=-6.195 ± 0.004 | read |
| fig2/3 open square | Ca | 1828.5 | 3241.5 | T=1879.46 ± 0.52 °C; log10_p=-6.348 ± 0.004 | read |
| fig2/1 open circle | SiO2 | 1964.0 | 3381.1 | T=1939.57 ± 0.52 °C; log10_p=-6.857 ± 0.004 | read |
| fig2/3 open square | Ca | 1644.1 | 3400.5 | T=1797.65 ± 0.52 °C; log10_p=-6.927 ± 0.004 | read |
| fig3/4 open square | Al2O3 | 4410.5 | 511.5 | Al2O3=97.78 ± 0.08 wt%; mass loss=85.11 ± 0.08 wt% | read |
| fig3/4 open square | Al2O3 | 4394.0 | 681.0 | Al2O3=96.79 ± 0.08 wt%; mass loss=72.52 ± 0.08 wt% | read |
| fig3/4 open square | Al2O3 | 4377.9 | 875.5 | Al2O3=95.81 ± 0.08 wt%; mass loss=58.08 ± 0.08 wt% | read |
| fig3/2 open circle | SiO2 | 3151.0 | 1048.0 | SiO2=21.54 ± 0.08 wt%; mass loss=45.27 ± 0.08 wt% | read |
| fig3/2 open circle | SiO2 | 3100.6 | 1052.4 | SiO2=18.49 ± 0.08 wt%; mass loss=44.94 ± 0.08 wt% | read |
| fig3/2 open circle | SiO2 | 2984.8 | 1060.3 | SiO2=11.48 ± 0.08 wt%; mass loss=44.35 ± 0.08 wt% | read |
| fig3/2 open circle | SiO2 | 2851.7 | 1084.7 | SiO2=3.43 ± 0.08 wt%; mass loss=42.54 ± 0.08 wt% | read |
| fig3/2 open circle | SiO2 | 3416.1 | 1084.7 | SiO2=37.59 ± 0.08 wt%; mass loss=42.54 ± 0.08 wt% | read |
| fig3/2 open circle | SiO2 | 2808.8 | 1096.6 | SiO2=0.83 ± 0.08 wt%; mass loss=41.66 ± 0.08 wt% | read |
| fig3/2 open circle | SiO2 | 3813.7 | 1244.4 | SiO2=61.66 ± 0.08 wt%; mass loss=30.68 ± 0.08 wt% | read |
| fig3/4 open square | Al2O3 | 4194.6 | 1420.0 | Al2O3=84.72 ± 0.08 wt%; mass loss=17.64 ± 0.08 wt% | read |
| fig3/2 open circle | SiO2 | 4195.5 | 1546.1 | SiO2=84.77 ± 0.08 wt%; mass loss=8.27 ± 0.08 wt% | read |
| fig3/4 open square | Al2O3 | 3814.0 | 1567.5 | Al2O3=61.68 ± 0.08 wt%; mass loss=6.68 ± 0.08 wt% | read |
| fig3/4 open square | Al2O3 | 3416.0 | 1604.0 | Al2O3=37.58 ± 0.08 wt%; mass loss=3.97 ± 0.08 wt% | read |
| fig3/4 open square | Al2O3 | 3150.9 | 1616.0 | Al2O3=21.54 ± 0.08 wt%; mass loss=3.08 ± 0.08 wt% | read |
| fig3/4 open square | Al2O3 | 3101.0 | 1617.9 | Al2O3=18.52 ± 0.08 wt%; mass loss=2.94 ± 0.08 wt% | read |
| fig3/4 open square | Al2O3 | 2985.5 | 1622.0 | Al2O3=11.53 ± 0.08 wt%; mass loss=2.64 ± 0.08 wt% | read |
| fig3/4 open square | Al2O3 | 2852.0 | 1624.1 | Al2O3=3.44 ± 0.08 wt%; mass loss=2.48 ± 0.08 wt% | read |
| fig3/4 open square | Al2O3 | 2808.0 | 1625.5 | Al2O3=0.78 ± 0.08 wt%; mass loss=2.38 ± 0.08 wt% | read |
| fig4/5 open square | Al | 4321.0 | 2568.0 | T=2349.47 ± 0.53 °C; log10_p=-4.897 ± 0.003 | read |
| fig4/5 open square | Al | 4174.0 | 2690.0 | T=2271.28 ± 0.53 °C; log10_p=-5.227 ± 0.003 | read |
| fig4/1 open circle | Ca | 3805.8 | 2695.5 | T=2075.43 ± 0.53 °C; log10_p=-5.242 ± 0.003 | read |
| fig4/1 open circle | Ca | 3877.6 | 2695.5 | T=2113.62 ± 0.53 °C; log10_p=-5.242 ± 0.003 | read |
| fig4/1 open circle | Ca | 3690.6 | 2735.7 | T=2014.15 ± 0.53 °C; log10_p=-5.351 ± 0.003 | read |
| fig4/1 open circle | Ca | 4009.3 | 2761.6 | T=2183.67 ± 0.53 °C; log10_p=-5.421 ± 0.003 | read |
| fig4/1 open circle | Ca | 3947.1 | 2767.9 | T=2150.59 ± 0.53 °C; log10_p=-5.438 ± 0.003 | read |
| fig4/1 open circle | Ca | 4054.8 | 2809.5 | T=2207.87 ± 0.53 °C; log10_p=-5.551 ± 0.003 | read |
| fig4/1 open circle | Ca | 3618.6 | 2822.8 | T=1975.85 ± 0.53 °C; log10_p=-5.587 ± 0.003 | read |
| fig4/1 open circle | Ca | 4168.2 | 2828.2 | T=2268.19 ± 0.53 °C; log10_p=-5.601 ± 0.003 | read |
| fig4/5 open square | Al | 4057.0 | 2848.0 | T=2209.04 ± 0.53 °C; log10_p=-5.655 ± 0.003 | read |
| fig4/5 open square | Al | 3970.0 | 2862.5 | T=2162.77 ± 0.53 °C; log10_p=-5.694 ± 0.003 | read |
| fig4/1 open circle | Ca | 4317.6 | 2877.9 | T=2347.66 ± 0.53 °C; log10_p=-5.736 ± 0.003 | read |
| fig4/5 open square | Al | 4030.5 | 2891.5 | T=2194.95 ± 0.53 °C; log10_p=-5.773 ± 0.003 | read |
| fig4/5 open square | Al | 3911.0 | 2955.5 | T=2131.38 ± 0.53 °C; log10_p=-5.946 ± 0.003 | read |
| fig4/1 open circle | Ca | 3505.6 | 2992.8 | T=1915.74 ± 0.53 °C; log10_p=-6.047 ± 0.003 | read |
| fig4/1 open circle | Ca | 3424.2 | 3099.7 | T=1872.45 ± 0.53 °C; log10_p=-6.337 ± 0.003 | read |
| fig4/5 open square | Al | 3880.6 | 3108.1 | T=2115.21 ± 0.53 °C; log10_p=-6.359 ± 0.003 | read |
| fig4/1 open circle | Ca | 3332.1 | 3244.4 | T=1823.46 ± 0.53 °C; log10_p=-6.729 ± 0.003 | read |
| fig4/1 open circle | Ca | 3241.5 | 3357.5 | T=1775.27 ± 0.53 °C; log10_p=-7.035 ± 0.003 | read |
| fig4/5 open square | Al | 3798.0 | 3400.5 | T=2071.28 ± 0.53 °C; log10_p=-7.151 ± 0.003 | read |
| fig4/5 open square | Al | 3685.0 | 3539.5 | T=2011.17 ± 0.53 °C; log10_p=-7.528 ± 0.003 | read |
| fig4/5 open square | Al | 3619.0 | 3553.5 | T=1976.06 ± 0.53 °C; log10_p=-7.566 ± 0.003 | read |
| fig4/1 open circle | Ca | 3147.3 | 3628.7 | T=1725.16 ± 0.53 °C; log10_p=-7.770 ± 0.003 | read |
| fig4/5 open square | Al | 3505.5 | 3625.5 | T=1915.69 ± 0.53 °C; log10_p=-7.761 ± 0.003 | read |
| fig5/1 open circle | SiO2 | 874.7 | 518.2 | a=0.9998 ± 0.0011; CaO=3.09 ± 0.02 mol% | read |
| fig5/1 open circle | SiO2 | 1134.8 | 637.6 | a=0.8992 ± 0.0011; CaO=7.94 ± 0.02 mol% | read |
| fig5/1 open circle | SiO2 | 1327.5 | 756.1 | a=0.7994 ± 0.0011; CaO=11.53 ± 0.02 mol% | read |
| fig5/1 open circle | SiO2 | 1485.0 | 874.2 | a=0.6999 ± 0.0011; CaO=14.47 ± 0.02 mol% | read |
| fig5/1 open circle | SiO2 | 1639.6 | 993.6 | a=0.5993 ± 0.0011; CaO=17.35 ± 0.02 mol% | read |
| fig5/1 open circle | SiO2 | 1770.5 | 1112.6 | a=0.4991 ± 0.0011; CaO=19.80 ± 0.02 mol% | read |
| fig5/1 open circle | SiO2 | 1894.3 | 1231.3 | a=0.3991 ± 0.0011; CaO=22.10 ± 0.02 mol% | read |
| fig5/1 open circle | SiO2 | 2036.3 | 1350.4 | a=0.2987 ± 0.0011; CaO=24.75 ± 0.02 mol% | read |
| fig5/1 open circle | SiO2 | 2127.6 | 1409.2 | a=0.2492 ± 0.0011; CaO=26.46 ± 0.02 mol% | read |
| fig5/1 open circle | SiO2 | 2234.4 | 1468.9 | a=0.1989 ± 0.0011; CaO=28.45 ± 0.02 mol% | read |
| fig5/3 open square | MgO | 1686.0 | 1587.0 | a=0.0994 ± 0.0011; CaO=18.22 ± 0.02 mol% | read |
| fig6/1 open circle | SiO2 | 827.6 | 2872.3 | a=1.0032 ± 0.0009; Al2O3=1.41 ± 0.02 mol% | read |
| fig6/1 open circle | SiO2 | 946.0 | 2986.8 | a=0.9028 ± 0.0009; Al2O3=3.32 ± 0.02 mol% | read |
| fig6/1 open circle | SiO2 | 1061.2 | 3100.6 | a=0.8030 ± 0.0009; Al2O3=5.19 ± 0.02 mol% | read |
| fig6/1 open circle | SiO2 | 1245.4 | 3214.4 | a=0.7032 ± 0.0009; Al2O3=8.16 ± 0.02 mol% | read |
| fig6/1 open circle | SiO2 | 1834.0 | 3443.1 | a=0.5025 ± 0.0009; Al2O3=17.68 ± 0.02 mol% | read |
| fig6/1 open circle | SiO2 | 2223.5 | 3557.2 | a=0.4025 ± 0.0009; Al2O3=23.98 ± 0.02 mol% | read |
| fig6 bridge | — | 2282.0 | 3768.0 | Distance peak between the filled circle at y=3743 and the filled square at y=3790. It is the contact of those two glyphs, not a third marker. | refused |
| fig2/3 open square | Ca | 2199.6 | 2986.8 | T=2044.10 ± 0.93 °C; log10_p=-5.419 ± 0.007 | read |
| fig5/3 open square | MgO | 2217.5 | 1468.5 | a=0.1992 ± 0.0018; CaO=28.13 ± 0.04 mol% | read |
| fig6/1 open circle | SiO2 | 1492.9 | 3327.3 | a=0.6041 ± 0.0013; Al2O3=12.17 ± 0.03 mol% | read |
| fig6/3 open square | MgO | 2298.5 | 3831.0 | a=0.1623 ± 0.0013; Al2O3=25.19 ± 0.03 mol% | read |
| fig6/2 filled circle | SiO2 | 2289.0 | 3743.0 | a=0.2395 ± 0.0018; Al2O3=25.04 ± 0.04 mol% | read |
| fig6/4 filled square | MgO | 2289.0 | 3790.0 | a=0.1982 ± 0.0026; Al2O3=25.04 ± 0.05 mol% | read |
| fig3/2 open circle | SiO2 | 4370.2 | 1642.1 | SiO2=95.34 ± 0.49 wt%; mass loss=1.14 ± 0.59 wt% | read |
| fig3/3 filled downward triangle | CaO | 4377.8 | 1178.1 | CaO=95.80 ± 0.13 wt%; mass loss=35.60 ± 0.15 wt% | read |
| fig3/3 filled downward triangle | CaO | 4394.4 | 1313.9 | CaO=96.81 ± 0.13 wt%; mass loss=25.52 ± 0.15 wt% | read |
| fig3/3 filled downward triangle | CaO | 4411.3 | 1463.0 | CaO=97.83 ± 0.10 wt%; mass loss=14.45 ± 0.12 wt% | read |
| fig3/3 filled downward triangle | CaO | 4195.5 | 1490.3 | CaO=84.77 ± 0.25 wt%; mass loss=12.42 ± 0.30 wt% | read |
| fig3/3 filled downward triangle | CaO | 3814.1 | 1602.5 | CaO=61.68 ± 0.10 wt%; mass loss=4.08 ± 0.12 wt% | read |
| fig3/3 filled downward triangle | CaO | 3416.1 | 1633.2 | CaO=37.59 ± 0.13 wt%; mass loss=1.81 ± 0.15 wt% | read |
| fig3 filled_down_triangle | — | 2803.6 | 1650.8 | Downward triangle between an open square and the x-axis. The apex enters the axis stroke and the base is not separable from the square, so the mass-loss coordinate cannot be read. | deferred |
| fig3 filled_down_triangle | — | 2852.0 | 1649.8 | Downward triangle between an open square and the x-axis. The apex enters the axis stroke and the base is not separable from the square, so the mass-loss coordinate cannot be read. | deferred |
| fig3 filled_down_triangle | — | 2985.1 | 1648.1 | Downward triangle between an open square and the x-axis. The apex enters the axis stroke and the base is not separable from the square, so the mass-loss coordinate cannot be read. | deferred |
| fig3 filled_down_triangle | — | 3100.8 | 1644.2 | Downward triangle between an open square and the x-axis. The apex enters the axis stroke and the base is not separable from the square, so the mass-loss coordinate cannot be read. | deferred |
| fig3 filled_down_triangle | — | 3151.1 | 1642.6 | Downward triangle between an open square and the x-axis. The apex enters the axis stroke and the base is not separable from the square, so the mass-loss coordinate cannot be read. | deferred |
| fig2/2 filled circle | Mg | 1808.7 | 2557.9 | T=1870.65 ± 0.52 °C; log10_p=-3.854 ± 0.004 | read |
| fig2/2 filled circle | Mg | 1635.1 | 2669.9 | T=1793.63 ± 0.52 °C; log10_p=-4.263 ± 0.004 | read |
| fig2/2 filled circle | Mg | 1440.7 | 2852.2 | T=1707.42 ± 0.52 °C; log10_p=-4.928 ± 0.004 | read |
| fig2/2 filled circle | Mg | 927.5 | 3288.2 | T=1479.74 ± 0.52 °C; log10_p=-6.518 ± 0.004 | read |
| fig2/2 filled circle | Mg | 1112.6 | 3025.9 | T=1561.86 ± 0.52 °C; log10_p=-5.561 ± 0.004 | read |
| fig2/2 filled circle | Mg | 1973.1 | 3145.5 | T=1943.61 ± 0.52 °C; log10_p=-5.997 ± 0.004 | read |
| fig2/2 filled circle | Mg | 1265.6 | 2994.6 | T=1629.73 ± 0.52 °C; log10_p=-5.447 ± 0.004 | read |
| fig2/2 filled circle | Mg | 781.3 | 3690.6 | T=1414.86 ± 0.52 °C; log10_p=-7.985 ± 0.004 | read |
| fig2/4 filled square | Al | 2305.0 | 2821.0 | T=2090.84 ± 0.52 °C; log10_p=-4.814 ± 0.004 | read |
| fig2/4 filled square | Al | 2177.5 | 2959.6 | T=2034.31 ± 0.52 °C; log10_p=-5.319 ± 0.004 | read |
| fig2/4 filled square | Al | 2341.3 | 2835.8 | T=2106.96 ± 0.52 °C; log10_p=-4.868 ± 0.004 | read |
| fig2/4 filled square | Al | 1664.0 | 3469.5 | T=1806.49 ± 0.52 °C; log10_p=-7.179 ± 0.004 | read |
| fig2/4 filled square | Al | 1963.5 | 3068.0 | T=1939.33 ± 0.52 °C; log10_p=-5.715 ± 0.004 | read |
| fig2/4 filled square | Al | 1829.0 | 3377.5 | T=1879.67 ± 0.52 °C; log10_p=-6.844 ± 0.004 | read |
| fig3/1 filled circle | MgO | 2851.0 | 1300.0 | MgO=3.38 ± 0.08 wt%; mass loss=26.55 ± 0.08 wt% | read |
| fig3/1 filled circle | MgO | 3416.2 | 1130.9 | MgO=37.60 ± 0.08 wt%; mass loss=39.11 ± 0.08 wt% | read |
| fig3/1 filled circle | MgO | 3150.9 | 1226.5 | MgO=21.54 ± 0.08 wt%; mass loss=32.01 ± 0.08 wt% | read |
| fig3/1 filled circle | MgO | 2984.9 | 1270.3 | MgO=11.49 ± 0.08 wt%; mass loss=28.76 ± 0.08 wt% | read |
| fig3/1 filled circle | MgO | 3100.7 | 1238.0 | MgO=18.50 ± 0.08 wt%; mass loss=31.16 ± 0.08 wt% | read |
| fig3/1 filled circle | MgO | 3813.7 | 957.6 | MgO=61.66 ± 0.08 wt%; mass loss=51.98 ± 0.08 wt% | read |
| fig3/1 filled circle | MgO | 4195.5 | 897.8 | MgO=84.77 ± 0.08 wt%; mass loss=56.42 ± 0.08 wt% | read |
| fig3/1 filled circle | MgO | 2810.8 | 1309.7 | MgO=0.95 ± 0.08 wt%; mass loss=25.83 ± 0.08 wt% | read |
| fig4/2 filled circle | Mg | 3235.8 | 3120.5 | T=1772.21 ± 0.53 °C; log10_p=-6.393 ± 0.003 | read |
| fig4/2 filled circle | Mg | 2977.4 | 3198.3 | T=1634.79 ± 0.53 °C; log10_p=-6.604 ± 0.003 | read |
| fig4/2 filled circle | Mg | 3142.9 | 3120.5 | T=1722.80 ± 0.53 °C; log10_p=-6.393 ± 0.003 | read |
| fig4/2 filled circle | Mg | 3327.8 | 3010.5 | T=1821.19 ± 0.53 °C; log10_p=-6.095 ± 0.003 | read |
| fig4/2 filled circle | Mg | 2871.5 | 3293.2 | T=1578.44 ± 0.53 °C; log10_p=-6.861 ± 0.003 | read |
| fig4/2 filled circle | Mg | 3048.9 | 3172.4 | T=1672.82 ± 0.53 °C; log10_p=-6.534 ± 0.003 | read |
| fig4/4 filled diamond | TiO2 | 3806.2 | 3571.8 | T=2075.65 ± 0.53 °C; log10_p=-7.615 ± 0.003 | read |
| fig4/4 filled diamond | TiO2 | 3619.3 | 3511.7 | T=1976.23 ± 0.53 °C; log10_p=-7.453 ± 0.003 | read |
| fig4/4 filled diamond | TiO2 | 3415.6 | 3478.9 | T=1867.87 ± 0.53 °C; log10_p=-7.364 ± 0.003 | read |
| fig4/4 filled diamond | TiO2 | 3878.1 | 3369.6 | T=2113.86 ± 0.53 °C; log10_p=-7.068 ± 0.003 | read |
| fig4/4 filled diamond | TiO2 | 4004.2 | 3270.8 | T=2180.95 ± 0.53 °C; log10_p=-6.800 ± 0.003 | read |
| fig4/4 filled diamond | TiO2 | 4323.8 | 3663.3 | T=2350.94 ± 0.53 °C; log10_p=-7.863 ± 0.003 | read |
| fig4/4 filled diamond | TiO2 | 3690.9 | 3578.1 | T=2014.28 ± 0.53 °C; log10_p=-7.633 ± 0.003 | read |
| fig4/4 filled diamond | TiO2 | 3507.6 | 3441.5 | T=1916.79 ± 0.53 °C; log10_p=-7.263 ± 0.003 | read |
| fig4/4 filled diamond | TiO2 | 4049.4 | 3299.7 | T=2204.98 ± 0.53 °C; log10_p=-6.878 ± 0.003 | read |
| fig4/4 filled diamond | TiO2 | 4183.6 | 3560.3 | T=2276.37 ± 0.53 °C; log10_p=-7.584 ± 0.003 | read |
| fig4/2 filled circle | Mg | 3417.8 | 3135.8 | T=1869.05 ± 1.18 °C; log10_p=-6.434 ± 0.006 | read |
| fig4/3 filled upward triangle | SiO2 | 3805.8 | 2901.8 | T=2075.44 ± 0.53 °C; log10_p=-5.801 ± 0.003 | read |
| fig4/3 filled upward triangle | SiO2 | 2881.2 | 3065.9 | T=1583.59 ± 0.53 °C; log10_p=-6.245 ± 0.003 | read |
| fig4/3 filled upward triangle | SiO2 | 4057.1 | 2950.9 | T=2209.09 ± 0.53 °C; log10_p=-5.934 ± 0.003 | read |
| fig4/3 filled upward triangle | SiO2 | 2983.4 | 2996.9 | T=1637.96 ± 0.53 °C; log10_p=-6.058 ± 0.003 | read |
| fig4/3 filled upward triangle | SiO2 | 3328.2 | 2698.9 | T=1821.39 ± 0.53 °C; log10_p=-5.251 ± 0.003 | read |
| fig4/3 filled upward triangle | SiO2 | 3049.3 | 2950.7 | T=1673.01 ± 0.53 °C; log10_p=-5.933 ± 0.003 | read |
| fig4/3 filled upward triangle | SiO2 | 3507.3 | 2757.6 | T=1916.67 ± 0.53 °C; log10_p=-5.410 ± 0.003 | read |
| fig4/3 filled upward triangle | SiO2 | 4162.5 | 2990.8 | T=2265.17 ± 0.53 °C; log10_p=-6.042 ± 0.003 | read |
| fig4/3 filled upward triangle | SiO2 | 3143.3 | 2872.0 | T=1723.04 ± 0.53 °C; log10_p=-5.720 ± 0.003 | read |
| fig4/3 filled upward triangle | SiO2 | 3687.2 | 2909.9 | T=2012.32 ± 0.53 °C; log10_p=-5.823 ± 0.003 | read |
| fig4/3 filled upward triangle | SiO2 | 3613.2 | 2889.9 | T=1972.98 ± 0.53 °C; log10_p=-5.768 ± 0.003 | read |
| fig4/3 filled upward triangle | SiO2 | 4004.1 | 2927.0 | T=2180.92 ± 0.53 °C; log10_p=-5.869 ± 0.003 | read |
| fig4/3 filled upward triangle | SiO2 | 3885.4 | 2907.7 | T=2117.77 ± 0.53 °C; log10_p=-5.817 ± 0.003 | read |
| fig4/3 filled upward triangle | SiO2 | 2869.7 | 3155.7 | T=1577.49 ± 0.53 °C; log10_p=-6.488 ± 0.003 | read |
| fig4/3 filled upward triangle | SiO2 | 3234.5 | 2816.9 | T=1771.54 ± 0.53 °C; log10_p=-5.571 ± 0.003 | read |
| fig4/3 filled upward triangle | SiO2 | 3415.4 | 2641.1 | T=1867.75 ± 0.53 °C; log10_p=-5.095 ± 0.003 | read |
| fig5/2 filled circle | SiO2 | 1781.75 | 1445.50 | a=0.2186 ± 0.0051; CaO=20.01 ± 0.113 mol%; 1200 dpi re-read, halved, allowance 6 px, overlaps the series-4 square | read |
| fig6/3 open square | MgO | 1352.25 | 3852.75 | a=0.1432 ± 0.0035; Al2O3=9.89 ± 0.067 mol%; 1200 dpi re-read, halved, allowance 4 px, open square under the filled square | read |
| fig5/4 filled square | MgO | 1781.75 | 1437.25 | a=0.2256 ± 0.0018; CaO=20.01 ± 0.040 mol%; 1200 dpi flat top plus half the side width, halved, allowance 2 px, square above the series-2 circle | read |
| fig5/2 filled circle | SiO2 | 1512.2 | 1095.5 | a=0.5135 ± 0.0011; CaO=14.98 ± 0.02 mol% | read |
| fig5/2 filled circle | SiO2 | 1242.8 | 831.6 | a=0.7358 ± 0.0011; CaO=9.95 ± 0.02 mol% | read |
| fig5/2 filled circle | SiO2 | 973.5 | 640.3 | a=0.8970 ± 0.0011; CaO=4.93 ± 0.02 mol% | read |
| fig5/2 filled circle | SiO2 | 2050.5 | 1640.0 | a=0.0547 ± 0.0011; CaO=25.02 ± 0.02 mol% | read |
| fig5/4 filled square | MgO | 973.5 | 1546.5 | a=0.1335 ± 0.0011; CaO=4.93 ± 0.02 mol% | read |
| fig5/4 filled square | MgO | 1243.0 | 1536.0 | a=0.1424 ± 0.0011; CaO=9.96 ± 0.02 mol% | read |
| fig5/4 filled square | MgO | 1512.5 | 1513.0 | a=0.1618 ± 0.0011; CaO=14.98 ± 0.02 mol% | read |
| fig5/4 filled square | MgO | 2050.5 | 1207.0 | a=0.4195 ± 0.0011; CaO=25.02 ± 0.02 mol% | read |
| fig5/2 filled circle | SiO2 | 2319.3 | 1676.4 | a=0.0241 ± 0.0068; CaO=30.03 ± 0.15 mol% | read |
| fig5/4 filled square | MgO | 2319.4 | 1002.6 | a=0.5917 ± 0.0068; CaO=30.03 ± 0.15 mol% | read |
| fig5/2 filled circle | SiO2 | 704.5 | 518.0 | a=1.0000 ± 0.0068; CaO=-0.09 ± 0.15 mol% | read |
| fig5/4 filled square | MgO | 705.0 | 1547.4 | a=0.1327 ± 0.0068; CaO=-0.08 ± 0.15 mol% | read |
| fig6/2 filled circle | SiO2 | 1668.0 | 3567.1 | a=0.3938 ± 0.0009; Al2O3=15.00 ± 0.02 mol% | read |
| fig6/2 filled circle | SiO2 | 1357.7 | 3178.1 | a=0.7350 ± 0.0009; Al2O3=9.98 ± 0.02 mol% | read |
| fig6/2 filled circle | SiO2 | 1978.6 | 3755.9 | a=0.2281 ± 0.0009; Al2O3=20.02 ± 0.02 mol% | read |
| fig6/2 filled circle | SiO2 | 1047.0 | 2971.5 | a=0.9162 ± 0.0009; Al2O3=4.96 ± 0.02 mol% | read |
| fig6/4 filled square | MgO | 1668.0 | 3751.5 | a=0.2320 ± 0.0009; Al2O3=15.00 ± 0.02 mol% | read |
| fig6/4 filled square | MgO | 1978.9 | 3711.3 | a=0.2672 ± 0.0050; Al2O3=20.02 ± 0.09 mol% | read |
| fig6/4 filled square | MgO | 1356.0 | 3828.9 | a=0.1641 ± 0.0009; Al2O3=9.95 ± 0.02 mol% | read |
| fig6/4 filled square | MgO | 1046.5 | 3854.0 | a=0.1421 ± 0.0009; Al2O3=4.95 ± 0.02 mol% | read |
| fig6/2 filled circle | SiO2 | 736.0 | 2872.3 | a=1.0032 ± 0.0070; Al2O3=-0.07 ± 0.13 mol% | read |
| fig6/4 filled square | MgO | 736.5 | 3860.0 | a=0.1368 ± 0.0070; Al2O3=-0.06 ± 0.13 mol% | read |
| fig7/1 filled circle | bulk | 1983.3 | 1865.7 | CaO=38.54 ± 0.11 mol%; MgO/SiO2=0.167 ± 0.004; audit only, no quantity token | read |
| fig7/1 filled circle | bulk | 1697.1 | 1653.8 | CaO=33.27 ± 0.05 mol%; MgO/SiO2=0.297 ± 0.002; audit only, no quantity token | read |
| fig7/1 filled circle | bulk | 2007.8 | 1922.9 | CaO=38.99 ± 0.05 mol%; MgO/SiO2=0.131 ± 0.002; audit only, no quantity token | read |
| fig7/1 filled circle | bulk | 2056.9 | 1965.8 | CaO=39.90 ± 0.02 mol%; MgO/SiO2=0.105 ± 0.001; audit only, no quantity token | read |
| fig7/1 filled circle | bulk | 1822.4 | 1698.7 | CaO=35.58 ± 0.09 mol%; MgO/SiO2=0.270 ± 0.003; audit only, no quantity token | read |
| fig7/1 filled circle | bulk | 1837.3 | 1807.6 | CaO=35.85 ± 0.03 mol%; MgO/SiO2=0.202 ± 0.001; audit only, no quantity token | read |
| fig7/1 filled circle | bulk | 2058.0 | 1806.0 | CaO=39.92 ± 0.02 mol%; MgO/SiO2=0.203 ± 0.001; audit only, no quantity token | read |
| fig7/1 filled circle | bulk | 1498.9 | 1387.0 | CaO=29.61 ± 0.02 mol%; MgO/SiO2=0.462 ± 0.001; audit only, no quantity token | read |
| fig7/1 filled circle | bulk | 1614.1 | 1463.8 | CaO=31.74 ± 0.02 mol%; MgO/SiO2=0.414 ± 0.001; audit only, no quantity token | read |
| fig7/1 filled circle | bulk | 2074.8 | 1674.7 | CaO=40.23 ± 0.02 mol%; MgO/SiO2=0.284 ± 0.001; audit only, no quantity token | read |
| fig7/1 filled circle | bulk | 1620.7 | 1505.0 | CaO=31.86 ± 0.02 mol%; MgO/SiO2=0.389 ± 0.001; audit only, no quantity token | read |
| fig7/1 filled circle | bulk | 1866.6 | 1816.1 | CaO=36.39 ± 0.03 mol%; MgO/SiO2=0.197 ± 0.001; audit only, no quantity token | read |
| fig7/1 filled circle | bulk | 1796.1 | 1531.0 | CaO=35.09 ± 0.02 mol%; MgO/SiO2=0.373 ± 0.001; audit only, no quantity token | read |
| fig7/1 filled circle | bulk | 1281.4 | 1556.2 | CaO=25.60 ± 0.02 mol%; MgO/SiO2=0.358 ± 0.001; audit only, no quantity token | read |
| fig7/1 filled circle | bulk | 1262.6 | 1583.8 | CaO=25.26 ± 0.02 mol%; MgO/SiO2=0.340 ± 0.001; audit only, no quantity token | read |
| fig7/1 filled circle | bulk | 1096.2 | 1442.6 | CaO=22.19 ± 0.02 mol%; MgO/SiO2=0.428 ± 0.001; audit only, no quantity token | read |
| fig7/1 filled circle | bulk | 1557.0 | 1770.7 | CaO=30.68 ± 0.02 mol%; MgO/SiO2=0.225 ± 0.001; audit only, no quantity token | read |
| fig7/1 filled circle | bulk | 1399.7 | 1600.3 | CaO=27.78 ± 0.05 mol%; MgO/SiO2=0.330 ± 0.002; audit only, no quantity token | read |
| fig7/1 filled circle | bulk | 1725.0 | 1633.6 | CaO=33.78 ± 0.05 mol%; MgO/SiO2=0.310 ± 0.002; audit only, no quantity token | read |
| fig7/1 filled circle | bulk | 1772.0 | 1618.0 | CaO=34.65 ± 0.02 mol%; MgO/SiO2=0.319 ± 0.001; audit only, no quantity token | read |
| fig7/1 filled circle | bulk | 1711.0 | 1502.5 | CaO=33.52 ± 0.02 mol%; MgO/SiO2=0.391 ± 0.001; audit only, no quantity token | read |
| fig7/1 filled circle | bulk | 1536.9 | 1566.3 | CaO=30.31 ± 0.02 mol%; MgO/SiO2=0.351 ± 0.001; audit only, no quantity token | read |
| fig7/1 filled circle | bulk | 1372.6 | 1610.3 | CaO=27.28 ± 0.05 mol%; MgO/SiO2=0.324 ± 0.002; audit only, no quantity token | read |
| fig7/1 filled circle | bulk | 1443.6 | 1133.1 | CaO=28.59 ± 0.02 mol%; MgO/SiO2=0.618 ± 0.001; audit only, no quantity token | read |
| fig7/1 filled circle | bulk | 1003.4 | 1163.8 | CaO=20.48 ± 0.02 mol%; MgO/SiO2=0.600 ± 0.001; audit only, no quantity token | read |
| fig7/1 filled circle | bulk | 1966.0 | 1829.3 | CaO=38.22 ± 0.02 mol%; MgO/SiO2=0.189 ± 0.001; audit only, no quantity token | read |
| fig7/1 filled circle | bulk | 2153.9 | 1810.4 | CaO=41.69 ± 0.02 mol%; MgO/SiO2=0.201 ± 0.001; audit only, no quantity token | read |
| fig7/1 filled circle | bulk | 1955.3 | 1798.0 | CaO=38.03 ± 0.02 mol%; MgO/SiO2=0.208 ± 0.001; audit only, no quantity token | read |
| fig7/1 filled circle | bulk | 1907.8 | 1719.3 | CaO=37.15 ± 0.02 mol%; MgO/SiO2=0.257 ± 0.001; audit only, no quantity token | read |
| fig7/1 filled circle | bulk | 1338.5 | 1526.8 | CaO=26.66 ± 0.02 mol%; MgO/SiO2=0.376 ± 0.001; audit only, no quantity token | read |
| fig7/1 filled circle | bulk | 1747.2 | 1496.3 | CaO=34.19 ± 0.02 mol%; MgO/SiO2=0.394 ± 0.001; audit only, no quantity token | read |
| fig7/1 filled circle | bulk | 1549.4 | 1679.0 | CaO=30.54 ± 0.02 mol%; MgO/SiO2=0.282 ± 0.001; audit only, no quantity token | read |
| fig7/1 filled circle | bulk | 1693.8 | 1702.9 | CaO=33.20 ± 0.02 mol%; MgO/SiO2=0.267 ± 0.001; audit only, no quantity token | read |
| fig7/1 filled circle | bulk | 1176.4 | 759.2 | CaO=23.67 ± 0.02 mol%; MgO/SiO2=0.849 ± 0.001; audit only, no quantity token | read |
| fig7/1 filled circle | bulk | 947.5 | 1316.4 | CaO=19.45 ± 0.02 mol%; MgO/SiO2=0.505 ± 0.001; audit only, no quantity token | read |
| fig7/1 filled circle | bulk | 1188.3 | 1281.5 | CaO=23.89 ± 0.02 mol%; MgO/SiO2=0.527 ± 0.001; audit only, no quantity token | read |
| fig7/1 filled circle | bulk | 1152.5 | 1221.8 | CaO=23.23 ± 0.02 mol%; MgO/SiO2=0.564 ± 0.001; audit only, no quantity token | read |
| fig7/1 filled circle | bulk | 1293.5 | 1202.4 | CaO=25.82 ± 0.02 mol%; MgO/SiO2=0.576 ± 0.001; audit only, no quantity token | read |
| fig7/1 filled circle | bulk | 1492.5 | 1622.3 | CaO=29.49 ± 0.02 mol%; MgO/SiO2=0.317 ± 0.001; audit only, no quantity token | read |

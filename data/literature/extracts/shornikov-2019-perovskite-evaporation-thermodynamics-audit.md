# Audit — shornikov-2019-perovskite-evaporation-thermodynamics

Fix round 3 on t1372-w4-fix1-t5, stacked on the round-2 recalibration. PDF page 2 render `eg-p2-2.png`, 4961×7016 px at 600 dpi.

## Duplicate verdict

Not the TMS chapter `shornikov-2019-perovskite-evaporation`, not the RJPC evaporation paper, and not RJPC 93(8). The recalibrated Fig. 2a Ca pressures match the TMS table's first point (1791 K, 2.08×10^−9 atm; this read is 1790.3 K, 2.084×10^−9 atm). The points stay in this extract. The venue is Experiment in Geosciences.

## Equations (8)–(9)

Printed on published page 39, read on the 600 dpi crop:

Values of oxide activities (a_i) in perovskite and in the CaO–TiO2 melts were calculated on the Lewis ratio:

(8) a_CaO = p_CaO / p°_CaO

(9) a_TiO2 = p_TiO2. / p°_TiO2

The degree sign is on each denominator only. Equation (9) has a full stop after the numerator. The paper does not print "pure simple oxide".

## Fig. 2 log-pressure calibration

Labelled ticks only. Half-decade ticks between the labels were not given integer values.

Fig. 2a labelled ticks: −6 at y=2380.0, −7 at y=2555.0, −8 at y=2729.0, −9 at y=2902.5, −10 at y=3076.5, −11 at y=3251.5. Least squares: lg p = 7.667841 − 0.00574193·y, 174.16 px per decade. Residuals on the six labels are +0.0021, −0.0028, −0.0019, +0.0019, +0.0028, −0.0021.

Fig. 2b labelled ticks: −5 at y=2385.0, −6 at y=2559.5, −7 at y=2733.0, −8 at y=2907.0, −9 at y=3082.0, −10 at y=3256.0. lg p = 8.691991 − 0.00574099·y, 174.19 px per decade. Residuals at most 0.0029.

Example. The first Fig. 2a Ca marker is at y=2847.3. lg p = 7.667841 − 0.00574193·2847.3 = −8.681. pressure_atm = 2.084×10^−9. The old fit, which treated the half-decade ticks as whole decades, stored −11.353.

Re-read against the labelled ticks, stored value minus the formula, all within 0.0005:

- Fig. 2a y=2387.6 (label −6 at 2380), stored −6.042
- Fig. 2a y=2551.5 (label −7 at 2555), stored −6.983
- Fig. 2a y=2734.3 (label −8 at 2729), stored −8.032
- Fig. 2a y=2894.2 (label −9 at 2902.5), stored −8.950
- Fig. 2a y=3077.3 (label −10 at 3076.5), stored −10.002
- Fig. 2a y=3253.5 (label −11 at 3251.5), stored −11.014
- Fig. 2b y=2401.0 (label −5 at 2385), stored −5.092
- Fig. 2b y=2569.0 (label −6 at 2559.5), stored −6.057
- Fig. 2b y=2728.0 (label −7 at 2733), stored −6.969
- Fig. 2b y=2902.0 (label −8 at 2907), stored −7.968
- Fig. 2b y=3096.0 (label −9 at 3082), stored −9.082. The former point at y=3085.6 was the series-3 label, not a triangle, and is not stored.
- Fig. 2b y=3242.0 (label −10 at 3256), stored −9.920

T and mol% calibrations are unchanged. Fig. 2a T is piecewise between 1800 K at x=944, 1900 at 1317, 2000 at 1653.5, 2100 at 1958, 2200 at 2235. Fig. 2b mol% = −60.4091 + 0.038890·x. Fig. 3a and Fig. 3b ordinates are unchanged.

## Standard state

The Lewis-ratio sentence does not say that p° was measured, or tabulated, at the temperature of the point. Equations (8) and (9) name CaO and TiO2 only. Every Fig. 3a activity row ends "The phase is not stated." Expected migration phase: unknown for CaO, TiO2, and CaTiO3.

## Counts

Fig. 2 partial pressures in the table below include the round-2 markers (one Fig. 2a TiO2 disk, twelve Fig. 2b TiO2 triangles, two Ti triangles, and two O2 triangles) and the round-3 reads: one Fig. 2a Ti disk, the Fig. 2a O2 centre moved in y only, one Fig. 2b CaO arc, one Fig. 2b O2 tip, the Ti triangle moved from (4050.0, 3085.6) to (4024.0, 3096.0), and seven series-6 O triangles. Round 4 adds the right-spine Ti triangle at (4124.5, 3094.0) and the right-spine O2 triangle at (4124.0, 2657.0). Fig. 3a gains one series-1 CaO disk at (1119.48, 4619.05). The other Fig. 3a activities are unchanged. Fig. 3b mixing energies stay context.

## Refusals, one marker at a time

A marker is a centre, or an assignable exposed arc or tip, inside a stated uncertainty. The uncertainty is wider when only an arc or a tip is visible. A 6–8 px connecting stroke is not a centre. Column thickness is not the test. The decision table below has one row for every this-work marker, read or refused.

- Fig. 2a red at x=1387 and x=1739: the red ink is the series line, not a closed disk. The first closed red disk is the stored marker at (1459, 3278). Refused.
- Fig. 2a black at x=1958 and x=2186: no separate black disk beside the stored series. Refused. Sol upholds both.
- Fig. 2a Ti at x=2186: stored at (2187.3, 2875.0), the bright-blue disk. The navy disk below it is series 7, now stored at (2186.5, 2900.0). The old O2 centre (2186.5, 2886.6) was the overlap.
- Fig. 2b red at x=3475: stored at (3474.5, 3105.0) from the exposed lower arc. The clean circle at (3556, 3121) fixes the 9 px offset from the arc bottom.
- Fig. 2b navy at x=3386: stored at (3386.0, 2686.0) from the tip at (3386, 2702). The clean triangle at (3324, 2691) fixes the 16 px offset.
- Fig. 2b Ti at x=4023: stored at (4024.0, 3096.0). The apex is y=3080-3084. Sibling triangles place the centre 16 px below the first apex row. The purple diamond crosses the body. The blue glyph near (4070, 3056) is the series label 3. The old centre (4050.0, 3085.6) is blank page.
- Fig. 2b series 6. The clean triangle at (2646, 2475) has its one-pixel tip 8 px below the stored centre, so a new centre is tip y minus 8. Stored tips: (3266, 2521), (3323, 2519), (3386, 2516), (3474, 2512), (3557, 2510), (3640, 2507), (4125, 2502). x=2709 stays refused: two offset clumps, and the pixel at (2707, 2490) is not on the body axis. x=2780 is unresolved below, and x=2839 and x=3174 are refused below: the purple at those stations is not an independent tip. Series 4 is not used as the comparison.
- Fig. 2b Ti at x=4124.5: stored at (4124.5, 3094.0). Saturated-blue apex y=3078-3079, x midpoint 4124.5. Centre y = 3078 + 16. The bar at y=3090-3096 crosses the body. lg p = -9.071. The glyph near (4070, 3056) remains the series label.
- Fig. 2b O2 at x=4124: stored at (4124.0, 2657.0). One-pixel navy tip at (4124, 2673). Centre y = 2673 - 16. lg p = -6.562. The diagonal trail at x=4062-4092 is not the tip.
- Fig. 2a magenta TiO near x=907, crop x=860-1000, y=3000-3140. Read. The diagonal stroke was fitted on the magenta pixels with page x>940 and removed. The crescent below that line is one component of 138 px. Its boundary fits a circle at (907.60, 3062.61), radius 18.77 px, rms 1.64 px, max residual 2.71 px. lg p = -9.917 ± 0.010. T = 1790.2 ± 1.0 K, extrapolated 36.4 px left of the labelled 1800 stub on the 3.73 px/K segment. The stored series-6 disk at (903.9, 3053.5) stays. The old leftmost-magenta fit, radius 122 px, rms 2.05, was the stroke and is not the stored centre.
- Fig. 2b TiO near x=4125. Read. Strict magenta rows at the station: y=2461 is 1 px, and the cap widens to 14 px by y=2472, then the horizontal bar begins. The vertical run in the station column is y=2461-2477. Its midpoint is (4124.5, 2469.0). The same midpoint on the neighbour at x=3937 reproduces the stored centre (3937.0, 2471.0). lg p = -5.483 ± 0.007. x is the labelled 100 tick. There is no lower magenta tip; the purple point below the white slot is series 6, already stored. The legend numeral 4 is the green fragment up and to the left, not this apex.
- Fig. 2b series 6 at x=2780, x=2839 and x=3174. The clean tip at x=3266 has purple groups y=2497-2499 and y=2517-2523. At x=2780 the same purple mask, column x±8, is only y=2512-2515 (4 px). That speck has no base and no second vertex, so no tip centre is fixed. The value is withheld. The marker is not declared absent. At x=2839 the purple is y=2498-2499 (2 px). Refused: no tip. At x=3174 the purple is y=2501-2503 (3 px) and y=2526-2527 (2 px). The magenta run y=2503-2516 is the stored series-4 diamond at (3174.0, 2509.0). Refused: no independent O tip.
- Fig. 3a near x=1119, y=4620. Read. This is the Fig. 3a panel (activity ticks 1.0 at y=4119 through 0 at y=5419), not a Fig. 2a marker. The ink is black, the same channel values as the stored series-1 CaO disks. The blue disk at (1119.5, 4597.9) is the stored series-4 CaTiO3 and stays. The exposed rim, with rays into the blue disk excluded and crossings more than 1.5 px from the median radius excluded as the descending stroke, fits a circle at (1119.48, 4619.05), radius 19.63 px, rms 0.39 px, max residual 1.28 px, 142 samples at threshold 110. Thresholds 50, 80, 110, 140, and 170 give rms 0.38 to 0.43 px and centres within 0.12 px. Activity = 0.6155 ± 0.0005. TiO2 = 34.21 mol% ± 0.08. The earlier cup fit, radius 18.24 px, rms 2.66 px, included the stroke shoulder and is not the stored centre.
- Fig. 3a series 3 and 5 are Banon et al. 1981. Fig. 3b series 2–4 are other papers. Fig. 1 is Gong et al. 2018.

## This-work markers

| series | species | x | y | value | decision |
| --- | --- | --- | --- | --- | --- |
| a/1 filled circle | Ca | 907.7 | 2847.3 | log10_p=-8.681 | read |
| a/1 filled circle | Ca | 1116.7 | 2767.6 | log10_p=-8.224 | read |
| a/1 filled circle | Ca | 1227.4 | 2723.6 | log10_p=-7.971 | read |
| a/1 filled circle | Ca | 1387.4 | 2663.4 | log10_p=-7.625 | read |
| a/1 filled circle | Ca | 1459.3 | 2636.4 | log10_p=-7.470 | read |
| a/1 filled circle | Ca | 1509.7 | 2614.1 | log10_p=-7.342 | read |
| a/1 filled circle | Ca | 1576.2 | 2591.5 | log10_p=-7.212 | read |
| a/1 filled circle | Ca | 1627.7 | 2563.8 | log10_p=-7.053 | read |
| a/1 filled circle | Ca | 1736.0 | 2523.5 | log10_p=-6.822 | read |
| a/1 filled circle | Ca | 1765.1 | 2515.3 | log10_p=-6.775 | read |
| a/1 filled circle | Ca | 1811.0 | 2495.4 | log10_p=-6.661 | read |
| b/1 filled circle | Ca | 2646.0 | 2452.0 | log10_p=-5.385 | read |
| b/1 filled circle | Ca | 2712.0 | 2473.0 | log10_p=-5.505 | read |
| b/1 filled circle | Ca | 2780.0 | 2499.0 | log10_p=-5.655 | read |
| b/1 filled circle | Ca | 2839.0 | 2520.0 | log10_p=-5.775 | read |
| b/1 filled circle | Ca | 2970.0 | 2569.0 | log10_p=-6.057 | read |
| b/1 filled circle | Ca | 3174.0 | 2632.0 | log10_p=-6.418 | read |
| b/1 filled circle | Ca | 3266.0 | 2656.0 | log10_p=-6.556 | read |
| b/1 filled circle | Ca | 3324.0 | 2668.0 | log10_p=-6.625 | read |
| b/1 filled circle | Ca | 3386.0 | 2684.0 | log10_p=-6.717 | read |
| b/1 filled circle | Ca | 3475.0 | 2702.0 | log10_p=-6.820 | read |
| b/1 filled circle | Ca | 3556.0 | 2724.0 | log10_p=-6.946 | read |
| b/1 filled circle | Ca | 3640.0 | 2746.0 | log10_p=-7.073 | read |
| b/1 filled circle | Ca | 3745.0 | 2786.0 | log10_p=-7.302 | read |
| b/1 filled circle | Ca | 3843.0 | 2824.0 | log10_p=-7.521 | read |
| b/1 filled circle | Ca | 3937.0 | 2852.0 | log10_p=-7.681 | read |
| b/1 filled circle | Ca | 4023.0 | 2896.0 | log10_p=-7.934 | read |
| a/2 filled circle | CaO | 1459.1 | 3278.0 | log10_p=-11.154 | read |
| a/2 filled circle | CaO | 1509.5 | 3253.8 | log10_p=-11.015 | read |
| a/2 filled circle | CaO | 1575.9 | 3225.2 | log10_p=-10.851 | read |
| a/2 filled circle | CaO | 1628.2 | 3201.6 | log10_p=-10.716 | read |
| a/2 filled circle | CaO | 1771.0 | 3122.1 | log10_p=-10.259 | read |
| a/2 filled circle | CaO | 1958.6 | 3048.6 | log10_p=-9.837 | read |
| a/2 filled circle | CaO | 2184.7 | 2941.3 | log10_p=-9.221 | read |
| b/2 open circle | CaO | 2646.0 | 2824.0 | log10_p=-7.521 | read |
| b/2 open circle | CaO | 2712.0 | 2859.0 | log10_p=-7.721 | read |
| b/2 open circle | CaO | 2780.0 | 2898.0 | log10_p=-7.945 | read |
| b/2 open circle | CaO | 2839.0 | 2928.0 | log10_p=-8.118 | read |
| b/2 open circle | CaO | 2970.0 | 2980.0 | log10_p=-8.416 | read |
| b/2 open circle | CaO | 3174.0 | 3042.0 | log10_p=-8.772 | read |
| b/2 open circle | CaO | 3266.0 | 3062.0 | log10_p=-8.887 | read |
| b/2 open circle | CaO | 3324.0 | 3074.0 | log10_p=-8.956 | read |
| b/2 open circle | CaO | 3386.0 | 3086.0 | log10_p=-9.025 | read |
| b/2 open circle | CaO | 3556.0 | 3121.0 | log10_p=-9.226 | read |
| b/2 open circle | CaO | 3640.0 | 3141.0 | log10_p=-9.340 | read |
| b/2 open circle | CaO | 3745.0 | 3176.0 | log10_p=-9.541 | read |
| b/2 open circle | CaO | 3843.0 | 3213.0 | log10_p=-9.754 | read |
| b/2 open circle | CaO | 3937.0 | 3242.0 | log10_p=-9.920 | read |
| b/2 open circle | CaO | 4023.0 | 3282.0 | log10_p=-10.150 | read |
| a/1 filled circle | CaO | 1202.5 | 4954.8 | activity=0.3571 | read |
| a/1 filled circle | CaO | 1256.4 | 5116.4 | activity=0.2327 | read |
| a/1 filled circle | CaO | 1288.4 | 5195.1 | activity=0.1722 | read |
| a/1 filled circle | CaO | 1338.7 | 5271.0 | activity=0.1138 | read |
| a/1 filled circle | CaO | 1394.0 | 5332.9 | activity=0.0661 | read |
| a/1 filled circle | CaO | 1441.2 | 5360.4 | activity=0.0450 | read |
| a/1 filled circle | CaO | 1543.7 | 5389.5 | activity=0.0227 | read |
| a/1 filled circle | CaO | 1706.2 | 5405.8 | activity=0.0101 | read |
| a/1 filled circle | CaO | 1779.3 | 5408.8 | activity=0.0078 | read |
| a/1 filled circle | CaO | 1825.6 | 5410.4 | activity=0.0066 | read |
| a/1 filled circle | CaO | 1873.4 | 5411.9 | activity=0.0054 | read |
| a/1 filled circle | CaO | 1943.8 | 5413.2 | activity=0.0044 | read |
| a/1 filled circle | CaO | 2008.8 | 5414.8 | activity=0.0032 | read |
| a/1 filled circle | CaO | 2074.3 | 5416.1 | activity=0.0022 | read |
| a/1 filled circle | CaO | 2158.4 | 5417.3 | activity=0.0013 | read |
| a/1 filled circle | CaO | 2236.3 | 5417.7 | activity=0.0009 | read |
| a/1 filled circle | CaO | 2309.4 | 5418.3 | activity=0.0005 | read |
| a/3 filled circle | Ti | 1576.0 | 3268.3 | log10_p=-11.099 | read |
| a/3 filled circle | Ti | 1627.7 | 3231.9 | log10_p=-10.890 | read |
| a/3 filled circle | Ti | 1740.8 | 3165.6 | log10_p=-10.509 | read |
| a/3 filled circle | Ti | 1770.9 | 3150.9 | log10_p=-10.424 | read |
| a/3 filled circle | Ti | 1815.7 | 3122.9 | log10_p=-10.264 | read |
| a/3 filled circle | Ti | 1957.3 | 3021.4 | log10_p=-9.681 | read |
| b/3 open triangle | Ti | 2712.0 | 3298.0 | log10_p=-10.242 | read |
| b/3 open triangle | Ti | 2780.0 | 3237.0 | log10_p=-9.892 | read |
| b/3 open triangle | Ti | 2839.0 | 3195.0 | log10_p=-9.650 | read |
| b/3 open triangle | Ti | 2970.0 | 3144.0 | log10_p=-9.358 | read |
| b/3 open triangle | Ti | 3174.0 | 3112.0 | log10_p=-9.174 | read |
| b/3 open triangle | Ti | 3266.0 | 3107.0 | log10_p=-9.145 | read |
| b/3 open triangle | Ti | 3324.0 | 3104.0 | log10_p=-9.128 | read |
| b/3 open triangle | Ti | 3386.0 | 3104.0 | log10_p=-9.128 | read |
| b/3 open triangle | Ti | 3475.0 | 3101.0 | log10_p=-9.111 | read |
| b/3 open triangle | Ti | 3556.0 | 3100.0 | log10_p=-9.105 | read |
| b/3 open triangle | Ti | 3640.0 | 3098.0 | log10_p=-9.094 | read |
| b/3 open triangle | Ti | 3745.0 | 3097.0 | log10_p=-9.088 | read |
| b/3 open triangle | Ti | 3843.0 | 3097.0 | log10_p=-9.088 | read |
| b/3 open triangle | Ti | 3937.0 | 3096.0 | log10_p=-9.082 | read |
| b/3 open triangle | Ti | 2655.0 | 3363.2 | log10_p=-10.616 | read |
| b/3 open triangle | Ti | 4024.0 | 3096.0 | log10_p=-9.082 | read |
| a/4 filled circle | TiO | 1116.5 | 2951.5 | log10_p=-9.279 | read |
| a/4 filled circle | TiO | 1227.2 | 2894.2 | log10_p=-8.950 | read |
| a/4 filled circle | TiO | 1387.2 | 2809.5 | log10_p=-8.464 | read |
| a/4 filled circle | TiO | 1459.4 | 2771.7 | log10_p=-8.247 | read |
| a/4 filled circle | TiO | 1509.6 | 2744.7 | log10_p=-8.092 | read |
| a/4 filled circle | TiO | 1576.0 | 2709.6 | log10_p=-7.890 | read |
| a/4 filled circle | TiO | 1628.0 | 2681.9 | log10_p=-7.731 | read |
| a/4 filled circle | TiO | 1739.1 | 2621.0 | log10_p=-7.382 | read |
| a/4 filled circle | TiO | 1769.1 | 2606.8 | log10_p=-7.300 | read |
| a/4 filled circle | TiO | 1812.2 | 2582.7 | log10_p=-7.162 | read |
| a/4 filled circle | TiO | 1958.0 | 2506.4 | log10_p=-6.724 | read |
| a/4 filled circle | TiO | 2186.7 | 2387.6 | log10_p=-6.042 | read |
| b/4 filled diamond | TiO | 2646.0 | 2728.0 | log10_p=-6.969 | read |
| b/4 filled diamond | TiO | 2712.0 | 2674.0 | log10_p=-6.659 | read |
| b/4 filled diamond | TiO | 2780.0 | 2619.0 | log10_p=-6.344 | read |
| b/4 filled diamond | TiO | 2839.0 | 2587.0 | log10_p=-6.160 | read |
| b/4 filled diamond | TiO | 2970.0 | 2542.0 | log10_p=-5.902 | read |
| b/4 filled diamond | TiO | 3174.0 | 2509.0 | log10_p=-5.712 | read |
| b/4 filled diamond | TiO | 3266.0 | 2501.0 | log10_p=-5.666 | read |
| b/4 filled diamond | TiO | 3324.0 | 2497.0 | log10_p=-5.643 | read |
| b/4 filled diamond | TiO | 3386.0 | 2493.0 | log10_p=-5.620 | read |
| b/4 filled diamond | TiO | 3475.0 | 2488.0 | log10_p=-5.592 | read |
| b/4 filled diamond | TiO | 3556.0 | 2484.0 | log10_p=-5.569 | read |
| b/4 filled diamond | TiO | 3640.0 | 2490.0 | log10_p=-5.603 | read |
| b/4 filled diamond | TiO | 3745.0 | 2486.0 | log10_p=-5.580 | read |
| b/4 filled diamond | TiO | 3843.0 | 2472.0 | log10_p=-5.500 | read |
| b/4 filled diamond | TiO | 3937.0 | 2471.0 | log10_p=-5.494 | read |
| b/4 filled diamond | TiO | 4023.0 | 2470.0 | log10_p=-5.488 | read |
| a/5 filled circle | TiO2 | 907.7 | 2938.8 | log10_p=-9.207 | read |
| a/5 filled circle | TiO2 | 1116.6 | 2840.5 | log10_p=-8.642 | read |
| a/5 filled circle | TiO2 | 1227.6 | 2792.3 | log10_p=-8.365 | read |
| a/5 filled circle | TiO2 | 1387.4 | 2716.3 | log10_p=-7.929 | read |
| a/5 filled circle | TiO2 | 1459.0 | 2680.4 | log10_p=-7.723 | read |
| a/5 filled circle | TiO2 | 1509.8 | 2662.2 | log10_p=-7.618 | read |
| a/5 filled circle | TiO2 | 1576.1 | 2628.7 | log10_p=-7.426 | read |
| a/5 filled circle | TiO2 | 1628.3 | 2606.6 | log10_p=-7.299 | read |
| a/5 filled circle | TiO2 | 1738.9 | 2551.5 | log10_p=-6.983 | read |
| a/5 filled circle | TiO2 | 1768.8 | 2539.9 | log10_p=-6.916 | read |
| a/5 filled circle | TiO2 | 1812.8 | 2520.6 | log10_p=-6.805 | read |
| a/5 filled circle | TiO2 | 1961.4 | 2455.9 | log10_p=-6.434 | read |
| b/5 filled triangle | TiO2 | 2646.0 | 2540.0 | log10_p=-5.890 | read |
| b/5 filled triangle | TiO2 | 2712.0 | 2502.0 | log10_p=-5.672 | read |
| b/5 filled triangle | TiO2 | 2780.0 | 2462.0 | log10_p=-5.442 | read |
| b/5 filled triangle | TiO2 | 2839.0 | 2438.0 | log10_p=-5.305 | read |
| b/5 filled triangle | TiO2 | 2970.0 | 2401.0 | log10_p=-5.092 | read |
| a/2 filled circle | TiO2 | 1119.6 | 5408.1 | activity=0.0083 | read |
| a/2 filled circle | TiO2 | 1202.0 | 5394.6 | activity=0.0187 | read |
| a/2 filled circle | TiO2 | 1272.0 | 5369.3 | activity=0.0382 | read |
| a/2 filled circle | TiO2 | 1338.6 | 5327.0 | activity=0.0708 | read |
| a/2 filled circle | TiO2 | 1393.7 | 5264.1 | activity=0.1191 | read |
| a/2 filled circle | TiO2 | 1440.8 | 5207.0 | activity=0.1631 | read |
| a/2 filled circle | TiO2 | 1543.6 | 5072.6 | activity=0.2665 | read |
| a/2 filled circle | TiO2 | 1706.1 | 4858.0 | activity=0.4316 | read |
| a/2 filled circle | TiO2 | 1780.0 | 4756.3 | activity=0.5099 | read |
| a/2 filled circle | TiO2 | 1825.0 | 4705.7 | activity=0.5488 | read |
| a/2 filled circle | TiO2 | 1873.6 | 4638.0 | activity=0.6009 | read |
| a/2 filled circle | TiO2 | 1944.0 | 4547.9 | activity=0.6702 | read |
| a/2 filled circle | TiO2 | 2008.9 | 4461.6 | activity=0.7366 | read |
| a/2 filled circle | TiO2 | 2074.4 | 4378.2 | activity=0.8007 | read |
| a/2 filled circle | TiO2 | 2158.7 | 4265.9 | activity=0.8872 | read |
| a/2 filled circle | TiO2 | 2236.3 | 4193.2 | activity=0.9431 | read |
| a/2 filled circle | TiO2 | 2309.9 | 4155.2 | activity=0.9723 | read |
| a/2 filled circle | TiO2 | 2379.4 | 4130.9 | activity=0.9911 | read |
| a/5 filled circle | TiO2 | 2183.9 | 2349.0 | log10_p=-5.820 | read |
| b/5 filled triangle | TiO2 | 3174.0 | 2366.2 | log10_p=-4.892 | read |
| b/5 filled triangle | TiO2 | 3266.0 | 2354.3 | log10_p=-4.824 | read |
| b/5 filled triangle | TiO2 | 3323.0 | 2348.2 | log10_p=-4.789 | read |
| b/5 filled triangle | TiO2 | 3386.0 | 2341.0 | log10_p=-4.748 | read |
| b/5 filled triangle | TiO2 | 3475.0 | 2333.1 | log10_p=-4.702 | read |
| b/5 filled triangle | TiO2 | 3557.0 | 2325.5 | log10_p=-4.659 | read |
| b/5 filled triangle | TiO2 | 3639.0 | 2319.4 | log10_p=-4.624 | read |
| b/5 filled triangle | TiO2 | 3745.0 | 2310.7 | log10_p=-4.574 | read |
| b/5 filled triangle | TiO2 | 3842.0 | 2306.0 | log10_p=-4.547 | read |
| b/5 filled triangle | TiO2 | 3936.0 | 2304.0 | log10_p=-4.535 | read |
| b/5 filled triangle | TiO2 | 4023.0 | 2303.2 | log10_p=-4.531 | read |
| b/5 filled triangle | TiO2 | 4125.0 | 2302.5 | log10_p=-4.527 | read |
| a/6 filled circle | O | 903.9 | 3053.5 | log10_p=-9.865 | read |
| a/6 filled circle | O | 1116.5 | 2980.7 | log10_p=-9.447 | read |
| a/6 filled circle | O | 1227.3 | 2940.1 | log10_p=-9.214 | read |
| a/6 filled circle | O | 1387.3 | 2879.3 | log10_p=-8.865 | read |
| a/6 filled circle | O | 1459.1 | 2851.8 | log10_p=-8.707 | read |
| a/6 filled circle | O | 1509.8 | 2832.8 | log10_p=-8.598 | read |
| a/6 filled circle | O | 1576.0 | 2807.6 | log10_p=-8.453 | read |
| a/6 filled circle | O | 1627.9 | 2787.8 | log10_p=-8.340 | read |
| a/6 filled circle | O | 1739.2 | 2747.3 | log10_p=-8.107 | read |
| a/6 filled circle | O | 1768.8 | 2734.3 | log10_p=-8.032 | read |
| a/6 filled circle | O | 1812.6 | 2718.9 | log10_p=-7.944 | read |
| a/6 filled circle | O | 1957.9 | 2662.4 | log10_p=-7.619 | read |
| a/6 filled circle | O | 2186.6 | 2574.1 | log10_p=-7.112 | read |
| b/6 filled triangle | O | 2646.0 | 2475.0 | log10_p=-5.517 | read |
| b/6 filled triangle | O | 2970.0 | 2518.0 | log10_p=-5.764 | read |
| b/6 filled triangle | O | 3745.0 | 2498.0 | log10_p=-5.649 | read |
| b/6 filled triangle | O | 3843.0 | 2496.0 | log10_p=-5.638 | read |
| b/6 filled triangle | O | 3937.0 | 2494.0 | log10_p=-5.626 | read |
| b/6 filled triangle | O | 4023.0 | 2494.0 | log10_p=-5.626 | read |
| a/7 filled circle | O2 | 1227.2 | 3286.9 | log10_p=-11.205 | read |
| a/7 filled circle | O2 | 1387.5 | 3235.1 | log10_p=-10.908 | read |
| a/7 filled circle | O2 | 1459.6 | 3204.5 | log10_p=-10.732 | read |
| a/7 filled circle | O2 | 1509.8 | 3182.9 | log10_p=-10.608 | read |
| a/7 filled circle | O2 | 1576.0 | 3155.0 | log10_p=-10.448 | read |
| a/7 filled circle | O2 | 1628.1 | 3132.5 | log10_p=-10.319 | read |
| a/7 filled circle | O2 | 1739.2 | 3085.1 | log10_p=-10.047 | read |
| a/7 filled circle | O2 | 1769.1 | 3072.7 | log10_p=-9.975 | read |
| a/7 filled circle | O2 | 1812.2 | 3054.2 | log10_p=-9.869 | read |
| a/7 filled circle | O2 | 1958.9 | 2991.9 | log10_p=-9.511 | read |
| a/7 filled circle | O2 | 2186.5 | 2900.0 | log10_p=-8.984 | read |
| b/7 open triangle | O2 | 2646.0 | 2620.0 | log10_p=-6.349 | read |
| b/7 open triangle | O2 | 2712.0 | 2650.0 | log10_p=-6.522 | read |
| b/7 open triangle | O2 | 2780.0 | 2679.0 | log10_p=-6.688 | read |
| b/7 open triangle | O2 | 2839.0 | 2694.0 | log10_p=-6.774 | read |
| b/7 open triangle | O2 | 3266.0 | 2694.0 | log10_p=-6.774 | read |
| b/7 open triangle | O2 | 3324.0 | 2691.0 | log10_p=-6.757 | read |
| b/7 open triangle | O2 | 3475.0 | 2679.0 | log10_p=-6.688 | read |
| b/7 open triangle | O2 | 3556.0 | 2674.0 | log10_p=-6.659 | read |
| b/7 open triangle | O2 | 3640.0 | 2669.0 | log10_p=-6.631 | read |
| b/7 open triangle | O2 | 3745.0 | 2662.0 | log10_p=-6.591 | read |
| b/7 open triangle | O2 | 3843.0 | 2658.0 | log10_p=-6.568 | read |
| b/7 open triangle | O2 | 3937.0 | 2655.0 | log10_p=-6.550 | read |
| b/7 open triangle | O2 | 4023.0 | 2654.0 | log10_p=-6.545 | read |
| b/7 open triangle | O2 | 2977.0 | 2709.9 | log10_p=-6.866 | read |
| b/7 open triangle | O2 | 3172.0 | 2702.0 | log10_p=-6.820 | read |
| a/8 filled circle | CaTiO3 | 1576.0 | 3363.5 | log10_p=-11.645 | read |
| a/8 filled circle | CaTiO3 | 1628.0 | 3325.2 | log10_p=-11.425 | read |
| a/8 filled circle | CaTiO3 | 1738.8 | 3253.5 | log10_p=-11.014 | read |
| a/8 filled circle | CaTiO3 | 1769.8 | 3227.9 | log10_p=-10.867 | read |
| a/8 filled circle | CaTiO3 | 1812.3 | 3188.5 | log10_p=-10.640 | read |
| a/8 filled circle | CaTiO3 | 1958.0 | 3077.3 | log10_p=-10.002 | read |
| a/8 filled circle | CaTiO3 | 2184.9 | 2919.1 | log10_p=-9.093 | read |
| b/8 filled diamond | CaTiO3 | 2646.0 | 2883.0 | log10_p=-7.859 | read |
| b/8 filled diamond | CaTiO3 | 2712.0 | 2880.0 | log10_p=-7.842 | read |
| b/8 filled diamond | CaTiO3 | 2780.0 | 2882.0 | log10_p=-7.854 | read |
| b/8 filled diamond | CaTiO3 | 2839.0 | 2886.0 | log10_p=-7.877 | read |
| b/8 filled diamond | CaTiO3 | 2970.0 | 2902.0 | log10_p=-7.968 | read |
| b/8 filled diamond | CaTiO3 | 3174.0 | 2927.0 | log10_p=-8.112 | read |
| b/8 filled diamond | CaTiO3 | 3266.0 | 2936.0 | log10_p=-8.164 | read |
| b/8 filled diamond | CaTiO3 | 3324.0 | 2940.0 | log10_p=-8.187 | read |
| b/8 filled diamond | CaTiO3 | 3386.0 | 2945.0 | log10_p=-8.215 | read |
| b/8 filled diamond | CaTiO3 | 3475.0 | 2954.0 | log10_p=-8.267 | read |
| b/8 filled diamond | CaTiO3 | 3556.0 | 2964.0 | log10_p=-8.324 | read |
| b/8 filled diamond | CaTiO3 | 3640.0 | 2978.0 | log10_p=-8.405 | read |
| b/8 filled diamond | CaTiO3 | 3745.0 | 3007.0 | log10_p=-8.571 | read |
| b/8 filled diamond | CaTiO3 | 3843.0 | 3038.0 | log10_p=-8.749 | read |
| b/8 filled diamond | CaTiO3 | 3937.0 | 3064.0 | log10_p=-8.898 | read |
| b/8 filled diamond | CaTiO3 | 4023.0 | 3104.0 | log10_p=-9.128 | read |
| a/4 filled circle | CaTiO3 | 1119.5 | 4597.9 | activity=0.6317 | read |
| a/4 filled circle | CaTiO3 | 1202.1 | 4332.5 | activity=0.8359 | read |
| a/4 filled circle | CaTiO3 | 1256.0 | 4204.2 | activity=0.9347 | read |
| a/4 filled circle | CaTiO3 | 1288.2 | 4150.9 | activity=0.9757 | read |
| a/4 filled circle | CaTiO3 | 1338.8 | 4119.7 | activity=0.9997 | read |
| a/4 filled circle | CaTiO3 | 1393.2 | 4142.5 | activity=0.9821 | read |
| a/4 filled circle | CaTiO3 | 1440.6 | 4224.7 | activity=0.9188 | read |
| a/4 filled circle | CaTiO3 | 1543.8 | 4425.1 | activity=0.7647 | read |
| a/4 filled circle | CaTiO3 | 1706.0 | 4675.9 | activity=0.5717 | read |
| a/4 filled circle | CaTiO3 | 1780.4 | 4794.5 | activity=0.4804 | read |
| a/4 filled circle | CaTiO3 | 1824.9 | 4830.9 | activity=0.4524 | read |
| a/4 filled circle | CaTiO3 | 1873.6 | 4903.2 | activity=0.3968 | read |
| a/4 filled circle | CaTiO3 | 1943.8 | 4980.0 | activity=0.3377 | read |
| a/4 filled circle | CaTiO3 | 2008.9 | 5074.4 | activity=0.2651 | read |
| a/4 filled circle | CaTiO3 | 2075.1 | 5172.9 | activity=0.1893 | read |
| a/4 filled circle | CaTiO3 | 2158.9 | 5260.5 | activity=0.1219 | read |
| a/4 filled circle | CaTiO3 | 2236.2 | 5320.5 | activity=0.0757 | read |
| a/4 filled circle | CaTiO3 | 2309.8 | 5367.3 | activity=0.0397 | read |
| a/4 filled circle | CaTiO3 | 2379.3 | 5391.0 | activity=0.0215 | read |
| b/1 filled circle | CaO-TiO2 | 3365.1 | 5142.9 | axis_delta_G_m_kJ_per_mol=-39.59 | read |
| b/1 filled circle | CaO-TiO2 | 3484.0 | 5270.0 | axis_delta_G_m_kJ_per_mol=-44.49 | read |
| b/1 filled circle | CaO-TiO2 | 3570.6 | 5348.1 | axis_delta_G_m_kJ_per_mol=-47.49 | read |
| b/1 filled circle | CaO-TiO2 | 3604.2 | 5354.9 | axis_delta_G_m_kJ_per_mol=-47.75 | read |
| b/1 filled circle | CaO-TiO2 | 3659.0 | 5348.5 | axis_delta_G_m_kJ_per_mol=-47.51 | read |
| b/1 filled circle | CaO-TiO2 | 3692.7 | 5337.9 | axis_delta_G_m_kJ_per_mol=-47.10 | read |
| b/1 filled circle | CaO-TiO2 | 3751.7 | 5288.7 | axis_delta_G_m_kJ_per_mol=-45.21 | read |
| b/1 filled circle | CaO-TiO2 | 3785.1 | 5265.8 | axis_delta_G_m_kJ_per_mol=-44.32 | read |
| b/1 filled circle | CaO-TiO2 | 3869.8 | 5166.6 | axis_delta_G_m_kJ_per_mol=-40.51 | read |
| b/1 filled circle | CaO-TiO2 | 3919.7 | 5084.7 | axis_delta_G_m_kJ_per_mol=-37.35 | read |
| b/1 filled circle | CaO-TiO2 | 3979.7 | 5018.7 | axis_delta_G_m_kJ_per_mol=-34.81 | read |
| b/1 filled circle | CaO-TiO2 | 4096.6 | 4819.6 | axis_delta_G_m_kJ_per_mol=-27.15 | read |
| b/1 filled circle | CaO-TiO2 | 4197.7 | 4634.7 | axis_delta_G_m_kJ_per_mol=-20.03 | read |
| b/1 filled circle | CaO-TiO2 | 4287.8 | 4440.9 | axis_delta_G_m_kJ_per_mol=-12.57 | read |
| b/1 filled circle | CaO-TiO2 | 4323.0 | 4363.0 | axis_delta_G_m_kJ_per_mol=-9.57 | read |
| a/3 filled circle | Ti | 2187.3 | 2875.0 | log10_p=-8.840 | read |
| b/2 open circle | CaO | 3474.5 | 3105.0 | log10_p=-9.134 | read |
| b/7 open triangle | O2 | 3386.0 | 2686.0 | log10_p=-6.728 | read |
| b/6 filled triangle | O | 3266.0 | 2513.0 | log10_p=-5.735 | read |
| b/6 filled triangle | O | 3323.0 | 2511.0 | log10_p=-5.724 | read |
| b/6 filled triangle | O | 3386.0 | 2508.0 | log10_p=-5.706 | read |
| b/6 filled triangle | O | 3474.5 | 2504.0 | log10_p=-5.683 | read |
| b/6 filled triangle | O | 3557.0 | 2502.0 | log10_p=-5.672 | read |
| b/6 filled triangle | O | 3640.0 | 2499.0 | log10_p=-5.655 | read |
| b/6 filled triangle | O | 4125.0 | 2494.0 | log10_p=-5.626 | read |
| a/2 red stroke | CaO | 1387 | — | — | refused: series line, not a closed disk |
| a/2 red stroke | CaO | 1739 | — | — | refused: series line, not a closed disk |
| a/1 black stroke | Ca | 1958 | — | — | refused: no separate black disk |
| a/1 black stroke | Ca | 2186 | — | — | refused: no separate black disk |
| b/3 label | Ti | 4070 | 3056 | — | refused: series label 3, not a triangle |
| b/6 purple ink | O | 2709 | 2490 | — | refused: two offset clumps, no one tip on an axis |
| b/3 open triangle | Ti | 4124.5 | 3094.0 | log10_p=-9.071 | read |
| b/7 open triangle | O2 | 4124.0 | 2657.0 | log10_p=-6.562 | read |
| a/4 filled circle | TiO | 907.60 | 3062.61 | log10_p=-9.917 ± 0.010; T=1790.2 ± 1.0 K | read: crescent circle, r=18.77, rms 1.64 px. T extrapolated 36.4 px left of labelled 1800. Series-6 disk at (903.9, 3053.5) stays. Sol about 1790 ± 2 K, log p -9.93 ± 0.04. The 0.012 dex gap is the 1.9 px centre difference. |
| b/4 filled diamond | TiO | 4124.5 | 2469.0 | log10_p=-5.483 ± 0.007; TiO2=100 mol% | read: midpoint of magenta run y=2461-2477. Same chord reproduces the stored neighbour (3937.0, 2471.0). On the labelled 100 tick. Sol about -5.49 ± 0.04. |
| b/6 purple ink | O | 2780 | 2512-2515 | — | deferred: unresolved geometry. Purple in x±8 is 4 px. No base and no second vertex, so no tip centre. The value is withheld. The marker is not declared absent. |
| b/6 purple ink | O | 2839 | 2498-2499 | — | refused: purple in x±8 is 2 px. No tip below the stroke. A triangle template has no vertices. |
| b/6 purple ink | O | 3174 | 2501-2503 | — | refused: purple specks of 3 px and 2 px (y=2526-2527). The magenta run y=2503-2516 is the stored series-4 diamond. No independent O tip. |
| a/1 filled circle | CaO | 1119.48 | 4619.05 | activity=0.6155 ± 0.0005; TiO2=34.21 mol% | read: exposed rim, r=19.63, rms 0.39 px. Blue occlusion and the descending stroke excluded. Thresholds 50 to 170 stay under 0.43 px. Sol about 0.616 ± 0.010 at 34.2 mol%, centre (1119.4, 4618.6). The 0.0005 gap is inside that band. |

# Shornikov 2000 titania melts — figure digitisation audit (t1374)

Source `shornikov-2000-titania-melts`. Worker t1374-fig-t1. Render 600 dpi. Marker centres are the PDF filled-path centroids (form matrix times the page `cm`), checked against the 600 dpi render: every stored centre is inside filled ink.

Same-kind paths are one glyph when the centre distance is at most 0.28 of the smaller path, or when the copy is vertical (x within 2 px) and the distance is at most 0.50 of a triangle or 0.62 of any other shape. Bounding-box overlap is not used: neighbour boxes touch, and that rule merged distinct markers. Pairs that remain closer than 14 px were cropped; they are separate markers.

Half-ticks are unlabelled and were not used in any fit. Log axes are fitted in the printed log value.

## Tick calibration

| panel | axis | labelled ticks (px = value) | max |residual| |
| --- | --- | --- | --- |
| fig1 | x | 1564.82=0, 2018.07=10, 2471.32=20, 2924.48=30, 3377.56=40, 3830.38=50 | 0.00459 |
| fig1 | y | 3576.50=-3, 3881.46=-4, 4186.51=-5, 4491.56=-6, 4796.52=-7, 5101.56=-8 | 0.00012 |
| fig2c | x | 1029.15=0, 1314.13=10, 1599.08=20, 1883.78=30, 2168.73=40, 2453.67=50 | 0.00354 |
| fig2c | y | 2261.85=0, 2507.52=-1, 2752.94=-2, 2998.61=-3, 3244.00=-4 | 0.00035 |
| fig2d | x | 2887.33=0, 3172.53=10, 3457.75=20, 3742.62=30, 4027.84=40, 4313.06=50 | 0.00463 |
| fig2d | y | 2259.15=0, 2505.02=-1, 2750.64=-2, 2996.46=-3, 3241.96=-4 | 0.00044 |
| fig2e | x | 1077.49=0, 1362.38=10, 1647.27=20, 1931.80=30, 2216.69=40, 2501.58=50 | 0.00470 |
| fig2e | y | 3821.91=0, 3996.25=-0.5, 4170.60=-1, 4344.89=-1.5, 4518.83=-2, 4693.15=-2.5 | 0.00048 |
| fig2f | x | 2862.48=0, 3147.45=10, 3432.17=20, 3717.13=30, 4001.84=40, 4286.81=50 | 0.00291 |
| fig2f | y | 3816.75=0, 3991.78=-0.5, 4166.97=-1, 4342.05=-1.5, 4517.09=-2, 4692.28=-2.5 | 0.00015 |
| fig3 | x | 1892.22=0, 2201.42=10, 2510.49=20, 2819.45=30, 3128.52=40, 3437.70=50 | 0.00218 |
| fig3 | y | 795.65=-2, 1152.60=-3, 1509.40=-4 | 0.00014 |

## Census

| panel | read | deferred | refused |
| --- | --- | --- | --- |
| fig1 | 130 | 0 | 0 |
| fig2a | 0 | 1 | 0 |
| fig2b | 0 | 1 | 0 |
| fig2c | 92 | 0 | 0 |
| fig2d | 23 | 0 | 0 |
| fig2e | 44 | 0 | 0 |
| fig2f | 14 | 0 | 0 |
| fig3 | 30 | 0 | 2 |

Fig. 2a and Fig. 2b are continuous phase boundaries (vector forms contain tick strokes and text, and no filled marker paths). No point was sampled from those curves.

## Standard state

Oxide activities, this work, published page 1470: The values of the CaO, TiO2 and SiO2 activities (a(i)) for the concentration section CaSiO3-CaTiO3 were calculated using the corresponding vapor species partial pressures with the partial vapor pressures of the same vapor species over individual oxides as standards (Fig.2). The physical phase of the pure-oxide reference is not stated.

Oxide activities, previous data [31], panels (d): Fig. 2 caption, published page 1471: panels (d, f) are previous data [31] at 1933 K. This article does not restate a pure-oxide reference for the [31] series. The physical phase of the pure-oxide reference is not stated.

Compound activities, panel (e): Published page 1472: in the case of the choice of the calcium methasilicate and calcium methatitanate as a standard for the CaSiO3-CaTiO3 section in the CaO-TiO2-SiO2 system. The physical phase of the pure-compound reference is not stated.

Compound activities, panel (f): Published page 1472: calcium methasilicate and calcium methaaluminate were chosen as standards for the CaSiO3-CaAl2O4 section in the CaO-Al2O3-SiO2 system. The physical phase of the pure-compound reference is not stated. The Ca2Al2SiO8 activity standard is not stated in this article.

The pure-oxide and pure-compound phase is not stated, and this paper does not tabulate a pure-oxide melting temperature, so the phase is not inferred. `reference_state_from_extract` returns unknown because the prose says the phase is not stated.

Fig. 1 partial pressures are the plotted pressures, not activities. Series 5, atomic oxygen, is calculated from equilibria (1)-(4) and is still `method_class: figure_only`.

## Fig. 3 schema gap

The ordinate is ΔfG/RT, not ΔfG. There is no quantity token for ΔfG/RT. The reads below are not stored as scored observations. Series 1 filled circles are CaSiO3–CaAl2O4 at 1933 K from [32]. Series 2 filled squares are CaSiO3–CaTiO3 at 2100 K, this study. The legend circle and square beside numerals 1 and 2 are refused.

## Decision table

| series | species | x | y | value | decision |
| --- | --- | --- | --- | --- | --- |
| fig2a phase boundaries | — | — | — | no discrete markers | deferred — continuous liquidus and solidus, sampling the curve would invent points |
| fig2b phase boundaries | — | — | — | no discrete markers | deferred — continuous liquidus and solidus, sampling the curve would invent points |
| fig1/1 filled circle | SiO | 2062.9 | 3698.5 | log10_p=-3.400 ± 0.0033 | read |
| fig1/2 filled square | Ca | 2066.1 | 4467.3 | log10_p=-5.920 ± 0.0033 | read |
| fig1/3 filled up-triangle | TiO | 2067.5 | 4856.5 | log10_p=-7.197 ± 0.0033 | read |
| fig1/4 filled down-triangle | TiO2 | 2067.5 | 4913.8 | log10_p=-7.384 ± 0.0033 | read |
| fig1/5 filled diamond | O | 2067.5 | 5135.1 | log10_p=-8.110 ± 0.0033 | read |
| fig1/2 filled square | Ca | 2197.1 | 4464.1 | log10_p=-5.910 ± 0.0033 | read |
| fig1/1 filled circle | SiO | 2200.4 | 3798.5 | log10_p=-3.728 ± 0.0033 | read |
| fig1/3 filled up-triangle | TiO | 2201.2 | 4844.0 | log10_p=-7.155 ± 0.0033 | read |
| fig1/4 filled down-triangle | TiO2 | 2201.2 | 4904.3 | log10_p=-7.353 ± 0.0033 | read |
| fig1/1 filled circle | SiO | 2225.3 | 3804.8 | log10_p=-3.749 ± 0.0033 | read |
| fig1/2 filled square | Ca | 2228.7 | 4436.1 | log10_p=-5.818 ± 0.0033 | read |
| fig1/5 filled diamond | O | 2230.6 | 5139.6 | log10_p=-8.125 ± 0.0033 | read |
| fig1/3 filled up-triangle | TiO | 2230.7 | 4824.8 | log10_p=-7.093 ± 0.0033 | read |
| fig1/4 filled down-triangle | TiO2 | 2230.7 | 4893.7 | log10_p=-7.319 ± 0.0033 | read |
| fig1/1 filled circle | SiO | 2318.9 | 3854.8 | log10_p=-3.912 ± 0.0033 | read |
| fig1/2 filled square | Ca | 2322.2 | 4486.0 | log10_p=-5.982 ± 0.0033 | read |
| fig1/3 filled up-triangle | TiO | 2323.7 | 4813.8 | log10_p=-7.057 ± 0.0033 | read |
| fig1/4 filled down-triangle | TiO2 | 2323.7 | 4865.8 | log10_p=-7.227 ± 0.0033 | read |
| fig1/5 filled diamond | O | 2323.7 | 5145.1 | log10_p=-8.143 ± 0.0033 | read |
| fig1/2 filled square | Ca | 2412.7 | 4467.3 | log10_p=-5.920 ± 0.0033 | read |
| fig1/1 filled circle | SiO | 2412.8 | 3904.8 | log10_p=-4.076 ± 0.0033 | read |
| fig1/5 filled diamond | O | 2414.7 | 5146.0 | log10_p=-8.146 ± 0.0033 | read |
| fig1/3 filled up-triangle | TiO | 2414.7 | 4801.9 | log10_p=-7.017 ± 0.0033 | read |
| fig1/4 filled down-triangle | TiO2 | 2414.7 | 4853.8 | log10_p=-7.188 ± 0.0033 | read |
| fig1/1 filled circle | SiO | 2540.7 | 3995.5 | log10_p=-4.374 ± 0.0033 | read |
| fig1/4 filled down-triangle | TiO2 | 2544.3 | 4824.2 | log10_p=-7.091 ± 0.0033 | read |
| fig1/2 filled square | Ca | 2578.2 | 4417.3 | log10_p=-5.757 ± 0.0033 | read |
| fig1/1 filled circle | SiO | 2581.5 | 4011.0 | log10_p=-4.425 ± 0.0033 | read |
| fig1/4 filled down-triangle | TiO2 | 2581.7 | 4838.2 | log10_p=-7.137 ± 0.0033 | read |
| fig1/1 filled circle | SiO | 2631.6 | 4023.5 | log10_p=-4.465 ± 0.0033 | read |
| fig1/2 filled square | Ca | 2631.6 | 4417.3 | log10_p=-5.757 ± 0.0033 | read |
| fig1/3 filled up-triangle | TiO | 2634.8 | 4727.9 | log10_p=-6.775 ± 0.0033 | read |
| fig1/4 filled down-triangle | TiO2 | 2634.8 | 4792.8 | log10_p=-6.988 ± 0.0033 | read |
| fig1/5 filled diamond | O | 2634.8 | 5144.6 | log10_p=-8.141 ± 0.0033 | read |
| fig1/2 filled square | Ca | 2650.3 | 4417.3 | log10_p=-5.757 ± 0.0033 | read |
| fig1/1 filled circle | SiO | 2656.5 | 4036.0 | log10_p=-4.507 ± 0.0033 | read |
| fig1/5 filled diamond | O | 2658.1 | 5141.6 | log10_p=-8.131 ± 0.0144 | read |
| fig1/2 filled square | Ca | 2659.8 | 4420.5 | log10_p=-5.767 ± 0.0033 | read |
| fig1/4 filled down-triangle | TiO2 | 2662.4 | 4781.7 | log10_p=-6.951 ± 0.0033 | read |
| fig1/2 filled square | Ca | 2672.3 | 4433.1 | log10_p=-5.808 ± 0.0033 | read |
| fig1/1 filled circle | SiO | 2675.3 | 4042.3 | log10_p=-4.527 ± 0.0033 | read |
| fig1/4 filled down-triangle | TiO2 | 2676.9 | 4775.7 | log10_p=-6.932 ± 0.0033 | read |
| fig1/2 filled square | Ca | 2697.3 | 4454.7 | log10_p=-5.879 ± 0.0033 | read |
| fig1/1 filled circle | SiO | 2719.1 | 4036.0 | log10_p=-4.507 ± 0.0033 | read |
| fig1/2 filled square | Ca | 2722.3 | 4423.5 | log10_p=-5.777 ± 0.0033 | read |
| fig1/4 filled down-triangle | TiO2 | 2724.9 | 4737.2 | log10_p=-6.806 ± 0.0033 | read |
| fig1/1 filled circle | SiO | 2744.1 | 4086.0 | log10_p=-4.671 ± 0.0033 | read |
| fig1/2 filled square | Ca | 2747.4 | 4423.5 | log10_p=-5.777 ± 0.0033 | read |
| fig1/4 filled down-triangle | TiO2 | 2749.3 | 4743.2 | log10_p=-6.825 ± 0.0033 | read |
| fig1/1 filled circle | SiO | 2772.4 | 4098.5 | log10_p=-4.711 ± 0.0033 | read |
| fig1/2 filled square | Ca | 2772.4 | 4420.5 | log10_p=-5.767 ± 0.0033 | read |
| fig1/5 filled diamond | O | 2775.3 | 5138.1 | log10_p=-8.120 ± 0.0033 | read |
| fig1/3 filled up-triangle | TiO | 2775.4 | 4694.9 | log10_p=-6.667 ± 0.0033 | read |
| fig1/4 filled down-triangle | TiO2 | 2775.4 | 4730.2 | log10_p=-6.783 ± 0.0033 | read |
| fig1/2 filled square | Ca | 2787.8 | 4433.1 | log10_p=-5.808 ± 0.0033 | read |
| fig1/1 filled circle | SiO | 2787.8 | 4098.5 | log10_p=-4.711 ± 0.0033 | read |
| fig1/4 filled down-triangle | TiO2 | 2791.9 | 4721.7 | log10_p=-6.755 ± 0.0033 | read |
| fig1/1 filled circle | SiO | 2800.3 | 4098.5 | log10_p=-4.711 ± 0.0033 | read |
| fig1/2 filled square | Ca | 2800.3 | 4436.1 | log10_p=-5.818 ± 0.0033 | read |
| fig1/4 filled down-triangle | TiO2 | 2802.9 | 4715.2 | log10_p=-6.733 ± 0.0033 | read |
| fig1/2 filled square | Ca | 2922.2 | 4458.1 | log10_p=-5.890 ± 0.0033 | read |
| fig1/1 filled circle | SiO | 2925.4 | 4179.7 | log10_p=-4.978 ± 0.0033 | read |
| fig1/4 filled down-triangle | TiO2 | 2926.9 | 4639.2 | log10_p=-6.484 ± 0.0033 | read |
| fig1/1 filled circle | SiO | 3012.7 | 4233.0 | log10_p=-5.152 ± 0.0033 | read |
| fig1/2 filled square | Ca | 3012.7 | 4448.6 | log10_p=-5.859 ± 0.0106 | read |
| fig1/3 filled up-triangle | TiO | 3014.5 | 4522.4 | log10_p=-6.101 ± 0.0033 | read |
| fig1/4 filled down-triangle | TiO2 | 3014.5 | 4562.0 | log10_p=-6.231 ± 0.0206 | read |
| fig1/3 filled up-triangle | TiO | 3014.5 | 4568.5 | log10_p=-6.252 ± 0.0033 | read |
| fig1/4 filled down-triangle | TiO2 | 3014.5 | 4595.8 | log10_p=-6.342 ± 0.0033 | read |
| fig1/5 filled diamond | O | 3015.8 | 5143.8 | log10_p=-8.138 ± 0.0270 | read |
| fig1/2 filled square | Ca | 3062.7 | 4442.3 | log10_p=-5.839 ± 0.0033 | read |
| fig1/2 filled square | Ca | 3087.7 | 4429.8 | log10_p=-5.798 ± 0.0033 | read |
| fig1/1 filled circle | SiO | 3087.7 | 4229.8 | log10_p=-5.142 ± 0.0033 | read |
| fig1/3 filled up-triangle | TiO | 3092.0 | 4521.4 | log10_p=-6.098 ± 0.0033 | read |
| fig1/4 filled down-triangle | TiO2 | 3092.0 | 4567.7 | log10_p=-6.250 ± 0.0033 | read |
| fig1/5 filled diamond | O | 3092.0 | 5132.1 | log10_p=-8.100 ± 0.0033 | read |
| fig1/2 filled square | Ca | 3116.0 | 4426.5 | log10_p=-5.787 ± 0.0033 | read |
| fig1/3 filled up-triangle | TiO | 3117.4 | 4518.4 | log10_p=-6.088 ± 0.0033 | read |
| fig1/5 filled diamond | O | 3117.4 | 5132.1 | log10_p=-8.100 ± 0.0033 | read |
| fig1/1 filled circle | SiO | 3169.0 | 4223.6 | log10_p=-5.122 ± 0.0033 | read |
| fig1/5 filled diamond | O | 3172.5 | 5145.1 | log10_p=-8.143 ± 0.0033 | read |
| fig1/4 filled down-triangle | TiO2 | 3172.6 | 4569.8 | log10_p=-6.257 ± 0.0033 | read |
| fig1/2 filled square | Ca | 3187.7 | 4433.1 | log10_p=-5.808 ± 0.0033 | read |
| fig1/1 filled circle | SiO | 3187.7 | 4211.0 | log10_p=-5.080 ± 0.0033 | read |
| fig1/4 filled down-triangle | TiO2 | 3192.1 | 4556.7 | log10_p=-6.214 ± 0.0033 | read |
| fig1/5 filled diamond | O | 3192.1 | 5142.1 | log10_p=-8.133 ± 0.0033 | read |
| fig1/3 filled up-triangle | TiO | 3197.6 | 4523.9 | log10_p=-6.106 ± 0.0181 | read |
| fig1/1 filled circle | SiO | 3200.3 | 4223.6 | log10_p=-5.122 ± 0.0033 | read |
| fig1/2 filled square | Ca | 3200.3 | 4445.6 | log10_p=-5.849 ± 0.0033 | read |
| fig1/4 filled down-triangle | TiO2 | 3203.1 | 4569.1 | log10_p=-6.254 ± 0.0033 | read |
| fig1/5 filled diamond | O | 3203.1 | 5156.1 | log10_p=-8.179 ± 0.0033 | read |
| fig1/1 filled circle | SiO | 3244.0 | 4257.9 | log10_p=-5.234 ± 0.0309 | read |
| fig1/2 filled square | Ca | 3245.5 | 4443.9 | log10_p=-5.844 ± 0.0073 | read |
| fig1/3 filled up-triangle | TiO | 3247.6 | 4497.4 | log10_p=-6.019 ± 0.0033 | read |
| fig1/5 filled diamond | O | 3247.6 | 5139.6 | log10_p=-8.125 ± 0.0033 | read |
| fig1/4 filled down-triangle | TiO2 | 3248.1 | 4563.5 | log10_p=-6.236 ± 0.0033 | read |
| fig1/3 filled up-triangle | TiO | 3248.6 | 4520.9 | log10_p=-6.096 ± 0.0033 | read |
| fig1/2 filled square | Ca | 3311.1 | 4442.3 | log10_p=-5.839 ± 0.0053 | read |
| fig1/1 filled circle | SiO | 3312.8 | 4223.6 | log10_p=-5.122 ± 0.0033 | read |
| fig1/3 filled up-triangle | TiO | 3314.7 | 4528.4 | log10_p=-6.121 ± 0.0064 | read |
| fig1/4 filled down-triangle | TiO2 | 3314.7 | 4576.0 | log10_p=-6.277 ± 0.0049 | read |
| fig1/5 filled diamond | O | 3314.7 | 5141.6 | log10_p=-8.131 ± 0.0107 | read |
| fig1/2 filled square | Ca | 3369.1 | 4445.6 | log10_p=-5.849 ± 0.0033 | read |
| fig1/2 filled square | Ca | 3372.1 | 4426.5 | log10_p=-5.787 ± 0.0033 | read |
| fig1/5 filled diamond | O | 3372.1 | 5144.6 | log10_p=-8.141 ± 0.0033 | read |
| fig1/3 filled up-triangle | TiO | 3374.1 | 4528.6 | log10_p=-6.122 ± 0.0077 | read |
| fig1/4 filled down-triangle | TiO2 | 3374.1 | 4572.2 | log10_p=-6.265 ± 0.0146 | read |
| fig1/5 filled diamond | O | 3376.1 | 5161.2 | log10_p=-8.196 ± 0.0033 | read |
| fig1/1 filled circle | SiO | 3412.9 | 4229.8 | log10_p=-5.142 ± 0.0033 | read |
| fig1/2 filled square | Ca | 3416.1 | 4414.0 | log10_p=-5.746 ± 0.0033 | read |
| fig1/5 filled diamond | O | 3417.6 | 5150.2 | log10_p=-8.159 ± 0.0033 | read |
| fig1/3 filled up-triangle | TiO | 3417.7 | 4526.9 | log10_p=-6.116 ± 0.0033 | read |
| fig1/4 filled down-triangle | TiO2 | 3417.7 | 4568.2 | log10_p=-6.252 ± 0.0033 | read |
| fig1/2 filled square | Ca | 3441.2 | 4445.6 | log10_p=-5.849 ± 0.0033 | read |
| fig1/5 filled diamond | O | 3445.1 | 5144.6 | log10_p=-8.141 ± 0.0033 | read |
| fig1/3 filled up-triangle | TiO | 3445.1 | 4529.9 | log10_p=-6.126 ± 0.0033 | read |
| fig1/4 filled down-triangle | TiO2 | 3445.1 | 4568.2 | log10_p=-6.252 ± 0.0033 | read |
| fig1/2 filled square | Ca | 3516.2 | 4445.6 | log10_p=-5.849 ± 0.0033 | read |
| fig1/3 filled up-triangle | TiO | 3520.2 | 4544.3 | log10_p=-6.173 ± 0.0033 | read |
| fig1/4 filled down-triangle | TiO2 | 3520.2 | 4567.2 | log10_p=-6.248 ± 0.0033 | read |
| fig1/5 filled diamond | O | 3520.2 | 5161.2 | log10_p=-8.196 ± 0.0033 | read |
| fig1/1 filled circle | SiO | 3662.7 | 4261.0 | log10_p=-5.244 ± 0.0033 | read |
| fig1/2 filled square | Ca | 3665.7 | 4445.6 | log10_p=-5.849 ± 0.0033 | read |
| fig1/5 filled diamond | O | 3668.7 | 5150.2 | log10_p=-8.159 ± 0.0033 | read |
| fig1/3 filled up-triangle | TiO | 3668.7 | 4515.4 | log10_p=-6.078 ± 0.0033 | read |
| fig1/4 filled down-triangle | TiO2 | 3668.7 | 4575.7 | log10_p=-6.276 ± 0.0033 | read |
| fig1/2 filled square | Ca | 3828.3 | 4439.1 | log10_p=-5.828 ± 0.0033 | read |
| fig1/5 filled diamond | O | 3830.3 | 5144.2 | log10_p=-8.140 ± 0.0033 | read |
| fig1/3 filled up-triangle | TiO | 3830.4 | 4524.8 | log10_p=-6.109 ± 0.0033 | read |
| fig1/4 filled down-triangle | TiO2 | 3830.4 | 4568.2 | log10_p=-6.252 ± 0.0033 | read |
| fig2c/2 filled square | CaO | 1343.0 | 2663.2 | log10_a=-1.634 a=0.0232 ± 0.0041 | read |
| fig2c/1 filled circle | SiO2 | 1344.9 | 2725.0 | log10_a=-1.886 a=0.0130 ± 0.0041 | read |
| fig2c/3 filled up-triangle | TiO2 | 1345.4 | 2675.5 | log10_a=-1.684 a=0.0207 ± 0.0079 | read |
| fig2c/2 filled square | CaO | 1427.5 | 2667.0 | log10_a=-1.650 a=0.0224 ± 0.0041 | read |
| fig2c/1 filled circle | SiO2 | 1427.5 | 2811.4 | log10_a=-2.238 a=0.0058 ± 0.0041 | read |
| fig2c/3 filled up-triangle | TiO2 | 1429.1 | 2669.2 | log10_a=-1.659 a=0.0219 ± 0.0041 | read |
| fig2c/2 filled square | CaO | 1446.3 | 2642.6 | log10_a=-1.550 a=0.0282 ± 0.0041 | read |
| fig2c/1 filled circle | SiO2 | 1446.3 | 2811.4 | log10_a=-2.238 a=0.0058 ± 0.0041 | read |
| fig2c/3 filled up-triangle | TiO2 | 1447.7 | 2661.2 | log10_a=-1.626 a=0.0236 ± 0.0041 | read |
| fig2c/1 filled circle | SiO2 | 1504.2 | 2856.3 | log10_a=-2.421 a=0.0038 ± 0.0041 | read |
| fig2c/2 filled square | CaO | 1504.2 | 2685.7 | log10_a=-1.726 a=0.0188 ± 0.0041 | read |
| fig2c/3 filled up-triangle | TiO2 | 1506.4 | 2643.1 | log10_a=-1.552 a=0.0280 ± 0.0164 | read |
| fig2c/2 filled square | CaO | 1562.4 | 2672.6 | log10_a=-1.673 a=0.0212 ± 0.0041 | read |
| fig2c/1 filled circle | SiO2 | 1562.4 | 2897.6 | log10_a=-2.589 a=0.0026 ± 0.0041 | read |
| fig2c/3 filled up-triangle | TiO2 | 1563.7 | 2629.1 | log10_a=-1.495 a=0.0320 ± 0.0041 | read |
| fig2c/1 filled circle | SiO2 | 1643.2 | 2968.9 | log10_a=-2.879 a=0.0013 ± 0.0041 | read |
| fig2c/3 filled up-triangle | TiO2 | 1645.1 | 2605.6 | log10_a=-1.400 a=0.0398 ± 0.0041 | read |
| fig2c/2 filled square | CaO | 1665.7 | 2625.6 | log10_a=-1.481 a=0.0330 ± 0.0041 | read |
| fig2c/1 filled circle | SiO2 | 1667.6 | 2980.0 | log10_a=-2.925 a=0.0012 ± 0.0041 | read |
| fig2c/3 filled up-triangle | TiO2 | 1668.4 | 2616.8 | log10_a=-1.445 a=0.0359 ± 0.0041 | read |
| fig2c/2 filled square | CaO | 1699.4 | 2629.5 | log10_a=-1.497 a=0.0318 ± 0.0041 | read |
| fig2c/1 filled circle | SiO2 | 1701.2 | 2995.0 | log10_a=-2.986 a=0.0010 ± 0.0041 | read |
| fig2c/3 filled up-triangle | TiO2 | 1701.9 | 2578.8 | log10_a=-1.290 a=0.0512 ± 0.0056 | read |
| fig2c/2 filled square | CaO | 1712.4 | 2629.5 | log10_a=-1.497 a=0.0318 ± 0.0041 | read |
| fig2c/1 filled circle | SiO2 | 1716.3 | 3000.7 | log10_a=-3.009 a=0.0010 ± 0.0075 | read |
| fig2c/3 filled up-triangle | TiO2 | 1719.2 | 2571.4 | log10_a=-1.261 a=0.0549 ± 0.0041 | read |
| fig2c/2 filled square | CaO | 1725.6 | 2637.0 | log10_a=-1.527 a=0.0297 ± 0.0041 | read |
| fig2c/1 filled circle | SiO2 | 1727.5 | 3004.3 | log10_a=-3.024 a=0.0009 ± 0.0041 | read |
| fig2c/3 filled up-triangle | TiO2 | 1728.6 | 2566.6 | log10_a=-1.241 a=0.0574 ± 0.0041 | read |
| fig2c/2 filled square | CaO | 1740.5 | 2655.6 | log10_a=-1.603 a=0.0249 ± 0.0041 | read |
| fig2c/2 filled square | CaO | 1755.6 | 2629.5 | log10_a=-1.497 a=0.0318 ± 0.0041 | read |
| fig2c/1 filled circle | SiO2 | 1757.5 | 2998.9 | log10_a=-3.001 a=0.0010 ± 0.0041 | read |
| fig2c/3 filled up-triangle | TiO2 | 1758.3 | 2535.4 | log10_a=-1.114 a=0.0769 ± 0.0041 | read |
| fig2c/1 filled circle | SiO2 | 1772.5 | 3036.4 | log10_a=-3.154 a=0.0007 ± 0.0041 | read |
| fig2c/2 filled square | CaO | 1772.5 | 2629.5 | log10_a=-1.497 a=0.0318 ± 0.0041 | read |
| fig2c/3 filled up-triangle | TiO2 | 1773.8 | 2539.9 | log10_a=-1.132 a=0.0738 ± 0.0041 | read |
| fig2c/1 filled circle | SiO2 | 1787.6 | 3051.3 | log10_a=-3.215 a=0.0006 ± 0.0041 | read |
| fig2c/2 filled square | CaO | 1789.5 | 2629.5 | log10_a=-1.497 a=0.0318 ± 0.0041 | read |
| fig2c/3 filled up-triangle | TiO2 | 1790.4 | 2529.6 | log10_a=-1.090 a=0.0812 ± 0.0041 | read |
| fig2c/3 filled up-triangle | TiO2 | 1790.4 | 2546.2 | log10_a=-1.158 a=0.0695 ± 0.0041 | read |
| fig2c/1 filled circle | SiO2 | 1798.8 | 3047.6 | log10_a=-3.200 a=0.0006 ± 0.0041 | read |
| fig2c/3 filled up-triangle | TiO2 | 1800.6 | 2522.8 | log10_a=-1.063 a=0.0866 ± 0.0041 | read |
| fig2c/1 filled circle | SiO2 | 1806.4 | 3045.8 | log10_a=-3.193 a=0.0006 ± 0.0041 | read |
| fig2c/3 filled up-triangle | TiO2 | 1807.8 | 2517.9 | log10_a=-1.043 a=0.0906 ± 0.0041 | read |
| fig2c/2 filled square | CaO | 1883.2 | 2648.1 | log10_a=-1.573 a=0.0267 ± 0.0041 | read |
| fig2c/1 filled circle | SiO2 | 1885.0 | 3104.0 | log10_a=-3.429 a=0.0004 ± 0.0041 | read |
| fig2c/3 filled up-triangle | TiO2 | 1885.6 | 2456.7 | log10_a=-0.793 a=0.1609 ± 0.0041 | read |
| fig2c/1 filled circle | SiO2 | 1937.5 | 3160.1 | log10_a=-3.658 a=0.0002 ± 0.0154 | read |
| fig2c/2 filled square | CaO | 1940.0 | 2655.7 | log10_a=-1.604 a=0.0249 ± 0.0233 | read |
| fig2c/3 filled up-triangle | TiO2 | 1940.5 | 2389.1 | log10_a=-0.518 a=0.3034 ± 0.0041 | read |
| fig2c/3 filled up-triangle | TiO2 | 1940.5 | 2418.1 | log10_a=-0.636 a=0.2312 ± 0.0141 | read |
| fig2c/3 filled up-triangle | TiO2 | 1940.5 | 2442.2 | log10_a=-0.734 a=0.1843 ± 0.0041 | read |
| fig2c/1 filled circle | SiO2 | 1986.3 | 3152.6 | log10_a=-3.627 a=0.0002 ± 0.0041 | read |
| fig2c/2 filled square | CaO | 1988.3 | 2631.3 | log10_a=-1.504 a=0.0313 ± 0.0041 | read |
| fig2c/3 filled up-triangle | TiO2 | 1989.4 | 2400.2 | log10_a=-0.563 a=0.2735 ± 0.0049 | read |
| fig2c/2 filled square | CaO | 2003.3 | 2629.5 | log10_a=-1.497 a=0.0318 ± 0.0041 | read |
| fig2c/3 filled up-triangle | TiO2 | 2005.4 | 2399.3 | log10_a=-0.560 a=0.2756 ± 0.0041 | read |
| fig2c/1 filled circle | SiO2 | 2038.7 | 3158.1 | log10_a=-3.650 a=0.0002 ± 0.0041 | read |
| fig2c/3 filled up-triangle | TiO2 | 2040.0 | 2400.5 | log10_a=-0.564 a=0.2727 ± 0.0041 | read |
| fig2c/1 filled circle | SiO2 | 2050.1 | 3145.1 | log10_a=-3.597 a=0.0003 ± 0.0041 | read |
| fig2c/2 filled square | CaO | 2050.1 | 2640.6 | log10_a=-1.542 a=0.0287 ± 0.0041 | read |
| fig2c/3 filled up-triangle | TiO2 | 2052.5 | 2390.0 | log10_a=-0.522 a=0.3008 ± 0.0041 | read |
| fig2c/3 filled up-triangle | TiO2 | 2052.5 | 2411.6 | log10_a=-0.610 a=0.2457 ± 0.0041 | read |
| fig2c/1 filled circle | SiO2 | 2057.5 | 3163.9 | log10_a=-3.673 a=0.0002 ± 0.0041 | read |
| fig2c/2 filled square | CaO | 2057.5 | 2663.2 | log10_a=-1.634 a=0.0232 ± 0.0041 | read |
| fig2c/3 filled up-triangle | TiO2 | 2059.2 | 2399.9 | log10_a=-0.562 a=0.2742 ± 0.0041 | read |
| fig2c/3 filled up-triangle | TiO2 | 2059.2 | 2423.0 | log10_a=-0.656 a=0.2207 ± 0.0041 | read |
| fig2c/1 filled circle | SiO2 | 2083.8 | 3186.4 | log10_a=-3.765 a=0.0002 ± 0.0041 | read |
| fig2c/2 filled square | CaO | 2085.6 | 2650.0 | log10_a=-1.581 a=0.0263 ± 0.0041 | read |
| fig2c/3 filled up-triangle | TiO2 | 2087.4 | 2393.1 | log10_a=-0.534 a=0.2923 ± 0.0201 | read |
| fig2c/1 filled circle | SiO2 | 2127.0 | 3148.9 | log10_a=-3.612 a=0.0002 ± 0.0041 | read |
| fig2c/2 filled square | CaO | 2128.0 | 2649.1 | log10_a=-1.577 a=0.0265 ± 0.0121 | read |
| fig2c/3 filled up-triangle | TiO2 | 2129.5 | 2410.3 | log10_a=-0.604 a=0.2486 ± 0.0336 | read |
| fig2c/2 filled square | CaO | 2165.4 | 2651.0 | log10_a=-1.585 a=0.0260 ± 0.0054 | read |
| fig2c/3 filled up-triangle | TiO2 | 2165.5 | 2416.4 | log10_a=-0.629 a=0.2348 ± 0.0041 | read |
| fig2c/3 filled up-triangle | TiO2 | 2166.8 | 2402.5 | log10_a=-0.572 a=0.2677 ± 0.0138 | read |
| fig2c/3 filled up-triangle | TiO2 | 2168.1 | 2431.7 | log10_a=-0.692 a=0.2034 ± 0.0041 | read |
| fig2c/1 filled circle | SiO2 | 2192.6 | 3167.6 | log10_a=-3.689 a=0.0002 ± 0.0041 | read |
| fig2c/2 filled square | CaO | 2192.6 | 2633.1 | log10_a=-1.512 a=0.0308 ± 0.0041 | read |
| fig2c/3 filled up-triangle | TiO2 | 2194.3 | 2399.3 | log10_a=-0.560 a=0.2756 ± 0.0041 | read |
| fig2c/3 filled up-triangle | TiO2 | 2194.3 | 2420.7 | log10_a=-0.647 a=0.2256 ± 0.0041 | read |
| fig2c/2 filled square | CaO | 2209.6 | 2653.8 | log10_a=-1.596 a=0.0254 ± 0.0041 | read |
| fig2c/3 filled up-triangle | TiO2 | 2211.4 | 2399.3 | log10_a=-0.560 a=0.2756 ± 0.0041 | read |
| fig2c/3 filled up-triangle | TiO2 | 2211.4 | 2418.5 | log10_a=-0.638 a=0.2302 ± 0.0041 | read |
| fig2c/2 filled square | CaO | 2256.3 | 2667.0 | log10_a=-1.650 a=0.0224 ± 0.0041 | read |
| fig2c/3 filled up-triangle | TiO2 | 2258.8 | 2398.4 | log10_a=-0.556 a=0.2781 ± 0.0041 | read |
| fig2c/3 filled up-triangle | TiO2 | 2258.8 | 2443.1 | log10_a=-0.738 a=0.1828 ± 0.0041 | read |
| fig2c/2 filled square | CaO | 2350.1 | 2657.6 | log10_a=-1.611 a=0.0245 ± 0.0041 | read |
| fig2c/1 filled circle | SiO2 | 2350.1 | 3190.1 | log10_a=-3.780 a=0.0002 ± 0.0041 | read |
| fig2c/3 filled up-triangle | TiO2 | 2351.9 | 2408.2 | log10_a=-0.596 a=0.2536 ± 0.0116 | read |
| fig2c/2 filled square | CaO | 2451.3 | 2648.1 | log10_a=-1.573 a=0.0267 ± 0.0041 | read |
| fig2c/3 filled up-triangle | TiO2 | 2453.7 | 2414.3 | log10_a=-0.621 a=0.2395 ± 0.0041 | read |
| fig2d/1 filled circle | SiO2 | 2884.6 | 2427.2 | log10_a=-0.684 a=0.2072 ± 0.0041 | read |
| fig2d/2 filled square | CaO | 2886.4 | 2541.6 | log10_a=-1.149 a=0.0709 ± 0.0041 | read |
| fig2d/1 filled circle | SiO2 | 3042.3 | 2475.9 | log10_a=-0.882 a=0.1313 ± 0.0041 | read |
| fig2d/2 filled square | CaO | 3042.3 | 2504.1 | log10_a=-0.997 a=0.1008 ± 0.0041 | read |
| fig2d/3 filled up-triangle | Al2O3 | 3044.2 | 2761.6 | log10_a=-2.044 a=0.0090 ± 0.0041 | read |
| fig2d/2 filled square | CaO | 3115.5 | 2496.7 | log10_a=-0.966 a=0.1081 ± 0.0041 | read |
| fig2d/1 filled circle | SiO2 | 3117.2 | 2487.4 | log10_a=-0.928 a=0.1179 ± 0.0041 | read |
| fig2d/3 filled up-triangle | Al2O3 | 3118.5 | 2740.2 | log10_a=-1.958 a=0.0110 ± 0.0041 | read |
| fig2d/2 filled square | CaO | 3239.4 | 2429.0 | log10_a=-0.691 a=0.2038 ± 0.0041 | read |
| fig2d/1 filled circle | SiO2 | 3320.1 | 2577.2 | log10_a=-1.294 a=0.0508 ± 0.0041 | read |
| fig2d/2 filled square | CaO | 3333.2 | 2385.8 | log10_a=-0.515 a=0.3055 ± 0.0041 | read |
| fig2d/1 filled circle | SiO2 | 3368.8 | 2674.9 | log10_a=-1.692 a=0.0203 ± 0.0041 | read |
| fig2d/2 filled square | CaO | 3370.7 | 2372.7 | log10_a=-0.462 a=0.3455 ± 0.0041 | read |
| fig2d/3 filled up-triangle | Al2O3 | 3372.1 | 2722.5 | log10_a=-1.885 a=0.0130 ± 0.0041 | read |
| fig2d/1 filled circle | SiO2 | 3597.8 | 2761.2 | log10_a=-2.043 a=0.0091 ± 0.0041 | read |
| fig2d/2 filled square | CaO | 3599.7 | 2376.5 | log10_a=-0.477 a=0.3333 ± 0.0041 | read |
| fig2d/3 filled up-triangle | Al2O3 | 3600.1 | 2652.7 | log10_a=-1.602 a=0.0250 ± 0.0041 | read |
| fig2d/2 filled square | CaO | 3798.7 | 2385.8 | log10_a=-0.515 a=0.3055 ± 0.0041 | read |
| fig2d/1 filled circle | SiO2 | 3826.8 | 2821.2 | log10_a=-2.287 a=0.0052 ± 0.0041 | read |
| fig2d/2 filled square | CaO | 3952.6 | 2429.0 | log10_a=-0.691 a=0.2038 ± 0.0041 | read |
| fig2d/3 filled up-triangle | Al2O3 | 4006.7 | 2504.8 | log10_a=-1.000 a=0.1001 ± 0.0041 | read |
| fig2d/2 filled square | CaO | 4311.2 | 2505.9 | log10_a=-1.004 a=0.0991 ± 0.0041 | read |
| fig2d/3 filled up-triangle | Al2O3 | 4313.1 | 2442.1 | log10_a=-0.744 a=0.1802 ± 0.0041 | read |
| fig2e/1 filled circle | CaSiO3 | 1391.0 | 4053.0 | log10_a=-0.663 a=0.2173 ± 0.0029 | read |
| fig2e/2 filled up-triangle | CaTiO3 | 1393.5 | 4260.4 | log10_a=-1.258 a=0.0552 ± 0.0082 | read |
| fig2e/1 filled circle | CaSiO3 | 1475.5 | 4177.0 | log10_a=-1.019 a=0.0958 ± 0.0029 | read |
| fig2e/2 filled up-triangle | CaTiO3 | 1477.3 | 4221.8 | log10_a=-1.147 a=0.0712 ± 0.0029 | read |
| fig2e/1 filled circle | CaSiO3 | 1492.3 | 4145.0 | log10_a=-0.927 a=0.1184 ± 0.0029 | read |
| fig2e/2 filled up-triangle | CaTiO3 | 1495.9 | 4175.0 | log10_a=-1.013 a=0.0971 ± 0.0029 | read |
| fig2e/1 filled circle | CaSiO3 | 1552.5 | 4270.9 | log10_a=-1.288 a=0.0515 ± 0.0029 | read |
| fig2e/2 filled up-triangle | CaTiO3 | 1554.4 | 4210.8 | log10_a=-1.116 a=0.0766 ± 0.0168 | read |
| fig2e/1 filled circle | CaSiO3 | 1608.8 | 4308.5 | log10_a=-1.396 a=0.0402 ± 0.0029 | read |
| fig2e/1 filled circle | CaSiO3 | 1714.0 | 4357.3 | log10_a=-1.536 a=0.0291 ± 0.0029 | read |
| fig2e/2 filled up-triangle | CaTiO3 | 1716.5 | 4088.9 | log10_a=-0.766 a=0.1714 ± 0.0029 | read |
| fig2e/1 filled circle | CaSiO3 | 1747.8 | 4387.2 | log10_a=-1.622 a=0.0239 ± 0.0029 | read |
| fig2e/2 filled up-triangle | CaTiO3 | 1749.8 | 4040.3 | log10_a=-0.626 a=0.2363 ± 0.0052 | read |
| fig2e/1 filled circle | CaSiO3 | 1774.0 | 4406.0 | log10_a=-1.676 a=0.0211 ± 0.0029 | read |
| fig2e/2 filled up-triangle | CaTiO3 | 1776.6 | 4031.0 | log10_a=-0.600 a=0.2513 ± 0.0029 | read |
| fig2e/1 filled circle | CaSiO3 | 1804.1 | 4387.2 | log10_a=-1.622 a=0.0239 ± 0.0029 | read |
| fig2e/2 filled up-triangle | CaTiO3 | 1806.3 | 3977.4 | log10_a=-0.446 a=0.3581 ± 0.0029 | read |
| fig2e/1 filled circle | CaSiO3 | 1819.1 | 4439.9 | log10_a=-1.773 a=0.0169 ± 0.0029 | read |
| fig2e/2 filled up-triangle | CaTiO3 | 1821.9 | 3982.9 | log10_a=-0.462 a=0.3455 ± 0.0029 | read |
| fig2e/1 filled circle | CaSiO3 | 1837.9 | 4462.3 | log10_a=-1.838 a=0.0145 ± 0.0029 | read |
| fig2e/2 filled up-triangle | CaTiO3 | 1838.4 | 3969.7 | log10_a=-0.424 a=0.3769 ± 0.0029 | read |
| fig2e/2 filled up-triangle | CaTiO3 | 1838.4 | 3993.1 | log10_a=-0.491 a=0.3229 ± 0.0029 | read |
| fig2e/1 filled circle | CaSiO3 | 1931.9 | 4567.6 | log10_a=-2.140 a=0.0073 ± 0.0029 | read |
| fig2e/2 filled up-triangle | CaTiO3 | 1933.6 | 3891.2 | log10_a=-0.199 a=0.6331 ± 0.0029 | read |
| fig2e/1 filled circle | CaSiO3 | 1988.1 | 4645.2 | log10_a=-2.362 a=0.0043 ± 0.0145 | read |
| fig2e/1 filled circle | CaSiO3 | 1988.1 | 4669.0 | log10_a=-2.431 a=0.0037 ± 0.0029 | read |
| fig2e/2 filled up-triangle | CaTiO3 | 1988.5 | 3820.2 | log10_a=0.005 a=1.0123 ± 0.0238 | read |
| fig2e/2 filled up-triangle | CaTiO3 | 1988.5 | 3846.4 | log10_a=-0.070 a=0.8511 ± 0.0052 | read |
| fig2e/1 filled circle | CaSiO3 | 2037.0 | 4608.9 | log10_a=-2.258 a=0.0055 ± 0.0029 | read |
| fig2e/1 filled circle | CaSiO3 | 2097.0 | 4616.4 | log10_a=-2.280 a=0.0053 ± 0.0029 | read |
| fig2e/2 filled up-triangle | CaTiO3 | 2100.5 | 3818.8 | log10_a=0.009 a=1.0216 ± 0.0029 | read |
| fig2e/1 filled circle | CaSiO3 | 2104.5 | 4672.7 | log10_a=-2.441 a=0.0036 ± 0.0029 | read |
| fig2e/2 filled up-triangle | CaTiO3 | 2107.1 | 3833.9 | log10_a=-0.034 a=0.9248 ± 0.0029 | read |
| fig2e/1 filled circle | CaSiO3 | 2134.6 | 4684.0 | log10_a=-2.474 a=0.0034 ± 0.0029 | read |
| fig2e/2 filled up-triangle | CaTiO3 | 2135.3 | 3807.6 | log10_a=0.041 a=1.0999 ± 0.0029 | read |
| fig2e/1 filled circle | CaSiO3 | 2176.0 | 4629.5 | log10_a=-2.317 a=0.0048 ± 0.0029 | read |
| fig2e/2 filled up-triangle | CaTiO3 | 2176.8 | 3821.9 | log10_a=0.000 a=1.0006 ± 0.0124 | read |
| fig2e/1 filled circle | CaSiO3 | 2239.9 | 4635.1 | log10_a=-2.333 a=0.0046 ± 0.0029 | read |
| fig2e/2 filled up-triangle | CaTiO3 | 2242.2 | 3819.4 | log10_a=0.008 a=1.0178 ± 0.0029 | read |
| fig2e/1 filled circle | CaSiO3 | 2371.3 | 4177.0 | log10_a=-1.019 a=0.0958 ± 0.0029 | read |
| fig2e/2 filled up-triangle | CaTiO3 | 2371.9 | 4273.5 | log10_a=-1.296 a=0.0506 ± 0.0029 | read |
| fig2e/1 filled circle | CaSiO3 | 2397.5 | 4702.8 | log10_a=-2.528 a=0.0030 ± 0.0029 | read |
| fig2e/2 filled up-triangle | CaTiO3 | 2399.8 | 3826.3 | log10_a=-0.012 a=0.9724 ± 0.0029 | read |
| fig2e/2 filled up-triangle | CaTiO3 | 2501.6 | 3824.0 | log10_a=-0.006 a=0.9873 ± 0.0132 | read |
| fig2f/1 filled circle | CaSiO3 | 2861.9 | 3814.3 | log10_a=0.007 a=1.0160 ± 0.0029 | read |
| fig2f/1 filled circle | CaSiO3 | 3017.4 | 3825.4 | log10_a=-0.025 a=0.9445 ± 0.0029 | read |
| fig2f/2 filled square | CaAl2O4 | 3017.4 | 4270.1 | log10_a=-1.295 a=0.0507 ± 0.0029 | read |
| fig2f/3 filled up-triangle | Ca2Al2SiO8 | 3019.3 | 3928.7 | log10_a=-0.320 a=0.4790 ± 0.0029 | read |
| fig2f/1 filled circle | CaSiO3 | 3090.6 | 3829.4 | log10_a=-0.036 a=0.9203 ± 0.0029 | read |
| fig2f/2 filled square | CaAl2O4 | 3092.4 | 4224.9 | log10_a=-1.166 a=0.0683 ± 0.0029 | read |
| fig2f/3 filled up-triangle | Ca2Al2SiO8 | 3093.2 | 3888.8 | log10_a=-0.206 a=0.6227 ± 0.0029 | read |
| fig2f/2 filled square | CaAl2O4 | 3343.7 | 4024.2 | log10_a=-0.593 a=0.2556 ± 0.0029 | read |
| fig2f/1 filled circle | CaSiO3 | 3345.6 | 3923.2 | log10_a=-0.304 a=0.4967 ± 0.0029 | read |
| fig2f/3 filled up-triangle | Ca2Al2SiO8 | 3346.8 | 3820.4 | log10_a=-0.011 a=0.9760 ± 0.0029 | read |
| fig2f/2 filled square | CaAl2O4 | 3572.5 | 3932.5 | log10_a=-0.330 a=0.4672 ± 0.0029 | read |
| fig2f/1 filled circle | CaSiO3 | 3574.5 | 4050.6 | log10_a=-0.668 a=0.2149 ± 0.0029 | read |
| fig2f/3 filled up-triangle | Ca2Al2SiO8 | 3574.8 | 3816.5 | log10_a=0.001 a=1.0015 ± 0.0029 | read |
| fig2f/2 filled square | CaAl2O4 | 4285.1 | 3816.1 | log10_a=0.002 a=1.0041 ± 0.0029 | read |
| fig3/1 filled circle | melt | 1889.2 | 842.8 | delta_fG/RT=-2.132 at x=-0.10 mol% ± 0.0028 | read |
| fig3/1 filled circle | melt | 2060.2 | 912.3 | delta_fG/RT=-2.327 at x=5.43 mol% ± 0.0028 | read |
| fig3/1 filled circle | melt | 2141.7 | 932.8 | delta_fG/RT=-2.384 at x=8.07 mol% ± 0.0028 | read |
| fig3/2 filled square | melt | 2233.3 | 1515.6 | delta_fG/RT=-4.017 at x=11.03 mol% ± 0.0028 | read |
| fig3/2 filled square | melt | 2322.9 | 1618.2 | delta_fG/RT=-4.305 at x=13.93 mol% ± 0.0028 | read |
| fig3/2 filled square | melt | 2343.4 | 1569.0 | delta_fG/RT=-4.167 at x=14.60 mol% ± 0.0028 | read |
| fig3/2 filled square | melt | 2408.6 | 1671.1 | delta_fG/RT=-4.453 at x=16.70 mol% ± 0.0058 | read |
| fig3/1 filled circle | melt | 2414.7 | 998.3 | delta_fG/RT=-2.568 at x=16.90 mol% ± 0.0028 | read |
| fig3/2 filled square | melt | 2469.8 | 1666.9 | delta_fG/RT=-4.441 at x=18.69 mol% ± 0.0028 | read |
| fig3/2 filled square | melt | 2583.8 | 1624.1 | delta_fG/RT=-4.321 at x=22.38 mol% ± 0.0028 | read |
| fig3/2 filled square | melt | 2620.7 | 1599.6 | delta_fG/RT=-4.253 at x=23.57 mol% ± 0.0060 | read |
| fig3/2 filled square | melt | 2649.1 | 1593.5 | delta_fG/RT=-4.236 at x=24.49 mol% ± 0.0028 | read |
| fig3/1 filled circle | melt | 2663.3 | 1031.0 | delta_fG/RT=-2.660 at x=24.95 mol% ± 0.0028 | read |
| fig3/2 filled square | melt | 2681.6 | 1534.1 | delta_fG/RT=-4.069 at x=25.54 mol% ± 0.0028 | read |
| fig3/2 filled square | melt | 2697.9 | 1558.7 | delta_fG/RT=-4.138 at x=26.06 mol% ± 0.0028 | read |
| fig3/2 filled square | melt | 2716.3 | 1552.6 | delta_fG/RT=-4.121 at x=26.66 mol% ± 0.0028 | read |
| fig3/2 filled square | melt | 2716.3 | 1569.0 | delta_fG/RT=-4.167 at x=26.66 mol% ± 0.0028 | read |
| fig3/2 filled square | melt | 2820.0 | 1487.1 | delta_fG/RT=-3.938 at x=30.01 mol% ± 0.0028 | read |
| fig3/2 filled square | melt | 2879.3 | 1435.9 | delta_fG/RT=-3.794 at x=31.93 mol% ± 0.0170 | read |
| fig3/2 filled square | melt | 2932.3 | 1343.0 | delta_fG/RT=-3.534 at x=33.65 mol% ± 0.0030 | read |
| fig3/2 filled square | melt | 2999.5 | 1290.7 | delta_fG/RT=-3.387 at x=35.82 mol% ± 0.0028 | read |
| fig3/2 filled square | melt | 2999.5 | 1315.3 | delta_fG/RT=-3.456 at x=35.82 mol% ± 0.0028 | read |
| fig3/2 filled square | melt | 3007.7 | 1341.9 | delta_fG/RT=-3.531 at x=36.09 mol% ± 0.0028 | read |
| fig3/2 filled square | melt | 3038.2 | 1291.9 | delta_fG/RT=-3.390 at x=37.08 mol% ± 0.0142 | read |
| fig3/2 filled square | melt | 3082.9 | 1256.0 | delta_fG/RT=-3.290 at x=38.52 mol% ± 0.0115 | read |
| fig3/2 filled square | melt | 3154.3 | 1196.7 | delta_fG/RT=-3.124 at x=40.83 mol% ± 0.0028 | read |
| fig3/1 filled circle | melt | 3307.1 | 1534.1 | log10=-4.069 ± 0.0028 | refused — legend glyph beside the printed numerals 1 and 2 inside the right of the frame; not on either data curve |
| fig3/2 filled square | melt | 3307.1 | 1636.4 | log10=-4.356 ± 0.0028 | refused — legend glyph beside the printed numerals 1 and 2 inside the right of the frame; not on either data curve |
| fig3/2 filled square | melt | 3325.3 | 1084.2 | delta_fG/RT=-2.809 at x=46.37 mol% ± 0.0114 | read |
| fig3/2 filled square | melt | 3435.4 | 959.4 | delta_fG/RT=-2.459 at x=49.93 mol% ± 0.0028 | read |
| fig3/2 filled square | melt | 3435.4 | 985.0 | delta_fG/RT=-2.530 at x=49.93 mol% ± 0.0029 | read |
| fig3/1 filled circle | melt | 3437.5 | 802.0 | delta_fG/RT=-2.018 at x=50.00 mol% ± 0.0028 | read |


# Shornikov 1999 silica partial vaporization coefficients — extraction audit

Source: Shornikov, S. I., Archakov, I. Yu., and Shul'ts, M. M. (1999). Mass Spectrometric Study of Vaporization and Thermodynamic Properties of Silicon Dioxide. II. Determination of Partial Vaporization Coefficients of Silicon Dioxide. Russian Journal of General Chemistry, vol. 69, no. 2, pp. 187–196. Translated from Zhurnal Obshchei Khimii, vol. 69, no. 2, 1999, pp. 197–206. Received 6 April 1998. No DOI is printed. corpus sha256 3c520be818701c126c4df21c0f5685f1e6c3109fd8e06991378398b85f02a362.

Part I is shornikov-1998-silica-vaporization (RJGC 68(8), 1171–1177). Part III is shornikov-2000-silica-gas-equilibria (RJGC 70(3), 360–370). This paper is neither of those.

Scored observations: 15. Context rows: 35.

## Duplicate verdict

Not a duplicate. The title on published p. 187 is part II, determination of partial vaporization coefficients. No extract on this base stores these Table 4 coefficients.

## Standard state

These rows are evaporation coefficients and pressure-fit coefficients, not activities. Migration reads standard_state prose only for activity and activity_coefficient. No standard_state field is written.

Published p. 187: “the condensed and gas phases are denoted by brackets and parentheses, respectively.” The sample is pure [SiO2]. The paper does not say solid, liquid, or a polymorph. Part I prints the melting point of [SiO2] as 1996±5 K. Every this-work temperature in Tables 1–4 ends at or below 1983 K, which is outside the melting band 1991–2001 K and within 100 K of 1996 K. The SiO Table 4 interval 1700–1950 K also spans the temperature where tridymite and cristobalite change stability. This paper does not discuss that change, so no polymorph is stored. Expected migration phase: unknown, because the phase key is omitted.

## Table 4 — admitted this-work cells

Header α_i × 10^2. Stored alpha = printed/100. Fifteen cells: three vapor species and five columns. Columns (10), (15), (17), and (19) are the printed results of those equations. The recommended column is not their arithmetic mean, so it has no derived_from link.

| species | T, K | column | printed ×10^2 | alpha |
| --- | --- | --- | --- | --- |
| SiO | 1700–1950 | (10) | 2.01±0.45 | 0.0201 |
| SiO | 1700–1950 | (15) | 2.39±0.35 | 0.0239 |
| SiO | 1700–1950 | (17) | 2.25±0.25 | 0.0225 |
| SiO | 1700–1950 | (19) | 2.20±0.24 | 0.0220 |
| SiO | 1700–1950 | recommended value | 2.23±0.30 | 0.0223 |
| SiO2 | 1850–1950 | (10) | 1.56±0.52 | 0.0156 |
| SiO2 | 1850–1950 | (15) | 1.90±0.43 | 0.0190 |
| SiO2 | 1850–1950 | (17) | 1.64±0.35 | 0.0164 |
| SiO2 | 1850–1950 | (19) | 2.02±0.38 | 0.0202 |
| SiO2 | 1850–1950 | recommended value | 1.80±0.41 | 0.0180 |
| Si2O2 | 1850–1950 | (10) | 1.31±0.56 | 0.0131 |
| Si2O2 | 1850–1950 | (15) | 1.60±0.49 | 0.0160 |
| Si2O2 | 1850–1950 | (17) | 1.53±0.41 | 0.0153 |
| Si2O2 | 1850–1950 | (19) | 1.88±0.45 | 0.0188 |
| Si2O2 | 1850–1950 | recommended value | 1.62±0.47 | 0.0162 |

Refused Table 4 rows, literature, not measured in this paper:

- (SiO) 1692–1739 K, reference [15]: (10) 5.0±0.9, (15) 3.2±0.9, recommended 3.2±0.9. Columns (17) and (19) are empty.
- (SiO) 1800–1950 K, reference [16]: (10) 5.6±4.9, (15) 5.6±4.2, recommended 2.2±0.8. Columns (17) and (19) are empty.
- (SiO) 1800–1950 K, reference [16]: (15) 2.1±1.6, recommended 2.2±0.8. The other equation columns are empty.

The [16] recommended cell 2.2±0.8 × 10^{-2} is the comparison quoted on published p. 189, (2.2±0.8)×10^{-2} at 1820–1960 K. It is not re-stored.

## Tables 1–3 — context only

This-work rows are context of type experiment_series. The quantity token partial_pressure names the relation. The stored numbers are A, B or the printed −a(i), −b(i), not a pressure. Literature rows of Tables 1 and 3 are refused and are not stored.

Table 1 this-work (SiO), Mo, published p. 189: 1623–1930 K, n=31, f=1.12×10^{-3}, A=27880±612, B=10.84±0.15; 1616–1947 K, n=24, f=2.39×10^{-3}, A=27240±420, B=10.45±0.24; 1650–1970 K, n=31, f=3.35×10^{-3}, A=26932±820, B=10.27±0.27; 1690–1947 K, n=32, f=3.75×10^{-1}, A=26452±860, B=8.00±0.48.

Table 2, all eight rows, this work, Mo, published p. 190. (SiO2) then (Si2O2), four f values each. The open-cell f is 3.75×10^{-1}, equal to the printed cell Clausing coefficient 0.375 when s_or = S_c.

Table 3 this-work Mo rows, published p. 193. Printed columns are −a(i) and −b(i). SiO at 1700, 1750, 1800, 1850, 1900, and 1950 K; (SiO2) and (Si2O2) at 1850, 1900, and 1950 K. Ditto marks continue This work.

## Figures

MinerU chart tables were not used. Point values below were read from 1200 dpi renders. PDF page 5 (published p. 191), crop `pdftoppm -r 1200 -f 5 -l 5 -x 100 -y 700 -W 5200 -H 9800`: crop pixel + (100, 700) is the page pixel. PDF page 6 (published p. 192), crop `pdftoppm -r 1200 -f 6 -l 6 -x 100 -y 200 -W 9800 -H 9000`: crop pixel + (100, 200) is the page pixel. Every centre below is a crop pixel.

Calibration uses the tick stroke, not the glyph. A filled marker in Fig. 2 is a distance-transform radius of at least 10 px at 1200 dpi. The fit stroke itself is about 12–14 px wide perpendicular to the line and has a distance-transform radius under 10 px. Peaks under that cut that lie on a Table line are the stroke, not markers. A whole series is not deferred.

Fig. 2 reads are context on the existing experiment_series rows, method_class figure_only. They are not scored partial_pressure observations. The paper prints the condensed phase as [SiO2] and does not name solid, liquid, or a polymorph, so a scored pressure would carry an unknown phase. The read uncertainty is the panel value below (about 20 px at the marker centre, divided by the pixels per dex), not the tick-fit residual. The line often bisects the marker, so the centre is not claimed more tightly than that.

### Fig. 2 calibration

Glyphs read on these crops: panel 2a, y labels −3, −4, −5, −6, −7, −8 and x labels 0.52, 0.56, 0.60, 0.64 (0.50 is not a labelled tick). Panel 2b, y labels −6, −7, −8 and x labels 0.50, 0.52, 0.54, 0.56, 0.58, 0.60. Panel 2c, y labels −6, −7, −8 and x labels 0.52, 0.54, 0.56, 0.58, 0.60, 0.62. log10 p is fit in log space. 1000/T is fit in linear space. The page is rotated by about 0.6°, and the reading removes that tilt before applying the y fit.

Panel 2a, SiO(g). Y fit log10 p = -0.228969 + (-2.766679e-03) y_corrected, rms 0.00325 dex, max residual 0.00580 dex, residuals [-0.001585, -0.001431, 0.000107, 0.005795, 0.001799, -0.004686]. Tick strokes (px, log10 p): [(1001.0, -3), (1362.5, -4), (1724.5, -5), (2088.0, -6), (2448.0, -7), (2807.1, -8)]. X fit 1000/T = 0.396295 + (6.501402e-05) x, rms 0.000079, max 0.000130, residuals [-8.1e-05, 0.00013, -1.6e-05, -3.2e-05]. Tick strokes (px, 1000/T): [(1904.0, 0.52), (2516.0, 0.56), (3133.5, 0.6), (3749.0, 0.64)]. vertical axis x = 1567.1 + 0.01092 y, rms 4.91 px; horizontal axis y = 2824.5 − 0.01088 x, rms 4.44 px; corner (1597.7, 2807.1). Read uncertainty ±0.06 dex and ±0.002 in 1000/T.

Panel 2b, SiO2(g). Y fit log10 p = -0.073775 + (-1.651526e-03) y_corrected, rms 0.00078 dex, max residual 0.00110 dex, residuals [-0.00055, 0.001101, -0.000551]. Tick strokes (px, log10 p): [(3588.0, -6), (4194.5, -7), (4799.0, -8)]. X fit 1000/T = 0.437526 + (3.852175e-05) x, rms 0.000057, max 0.000095, residuals [3e-05, -1e-06, -5.2e-05, 5.2e-05, -9.5e-05, 6.6e-05]. Tick strokes (px, 1000/T): [(1621.0, 0.5), (2141.0, 0.52), (2661.5, 0.54), (3178.0, 0.56), (3701.0, 0.58), (4216.0, 0.6)]. vertical axis x = 1562.1 + 0.01148 y, rms 2.77 px; horizontal axis y = 5242.4 − 0.01040 x, rms 4.06 px; corner (1622.0, 5225.5). The −8 tick is not the x-axis; the axis is at y ≈ 5226, so log10 p < −8 is in-frame extrapolation. Read uncertainty ±0.04 dex and ±0.002 in 1000/T.

Panel 2c, Si2O2(g). Y fit log10 p = 5.824708 + (-1.890249e-03) y_corrected, rms 0.00624 dex, max residual 0.00882 dex, residuals [0.004469, -0.008821, 0.004352]. Tick strokes (px, log10 p): [(6258.0, -6), (6780.0, -7), (7316.0, -8)]. X fit 1000/T = 0.419617 + (4.823581e-05) x, rms 0.000057, max 0.000085, residuals [4e-06, -6.2e-05, 1.7e-05, 4.7e-05, 7.8e-05, -8.5e-05]. Tick strokes (px, 1000/T): [(2081.0, 0.52), (2497.0, 0.54), (2910.0, 0.56), (3324.0, 0.58), (3738.0, 0.6), (4156.0, 0.62)]. vertical axis x = 1711.4 + 0.00807 y, rms 7.04 px; horizontal axis y = 7780.9 − 0.00852 x, rms 6.38 px; corner (1774.0, 7765.8). The x-axis is below the −8 tick, so log10 p < −8 is in-frame extrapolation. Read uncertainty ±0.04 dex and ±0.002 in 1000/T.

### Fig. 2 markers

Table n on each line is the least-squares sample size in Tables 1 and 2. It is not a marker count. A bare stretch is a run of the stroke whose smoothed width stays at the stroke width. No marker was invented to fill Table n.

#### Panel 2a

series 1 (f = 1.12e-3, Table n = 31, T 1623–1930 K): 0 read; series 2 (f = 2.39e-3, Table n = 24, T 1616–1947 K): 0 read; series 3 (f = 3.35e-3, Table n = 31, T 1650–1970 K): 0 read; series 4 (f = 3.75e-1, Table n = 32, T 1690–1947 K): 7 read.

Series 1 and 2 differ by −640/T + 0.39 dex, so they cross near 1641 K and stay within a few hundredths of a dex over most of the frame. Series 3 lies in that same bundle. A centre within 0.10 dex of two lines, and within 0.03 dex of being equidistant, is deferred and not assigned. The measured log10 p stays on the row. Series 4, the open cell, is about 2 dex from the other three, and those centres are the reads.

| id | decision | reason | series | log10 p_ef | ± dex | 1000/T | T/K | cx | cy | dt | offset dex | second series | second abs dex | flags |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2a-00 | defer | 0.10 to 0.18 dex off the nearer Table line | unassigned | -3.480 | 0.06 | 0.5176 | 1931.9 | 1866.1 | 1172.2 | 20.0 | 0.111 | 2 | 0.170 | T outside the nearer line's printed interval |
| 2a-02 | defer | isolated triangle; not assigned to a Table line | unassigned | -3.568 | 0.06 | 0.5275 | 1895.6 | 2018.6 | 1202.1 | 17.0 | 0.300 | 2 | 0.352 | none |
| 2a-03 | defer | series 1 and 2 both within 0.10 dex and not separable | 1 or 2 | -4.471 | 0.06 | 0.5488 | 1822.2 | 2345.6 | 1525.0 | 29.1 | -0.010 | 2 | 0.028 | none |
| 2a-04 | defer | series 1 and 2 both within 0.10 dex and not separable | 1 or 2 | -4.778 | 0.06 | 0.5597 | 1786.7 | 2513.1 | 1634.1 | 31.1 | -0.014 | 2 | 0.018 | none |
| 2a-05 | defer | series 1 and 2 both within 0.10 dex and not separable | 1 or 2 | -5.143 | 0.06 | 0.5731 | 1745.0 | 2719.0 | 1763.9 | 30.0 | -0.006 | 2 | 0.018 | none |
| 2a-06 | defer | series 1 and 2 both within 0.10 dex and not separable | 1 or 2 | -5.228 | 0.06 | 0.5770 | 1733.0 | 2779.9 | 1794.1 | 17.0 | 0.019 | 2 | 0.040 | none |
| 2a-08 | defer | series 1 and 2 both within 0.10 dex and not separable | 1 or 2 | -5.265 | 0.06 | 0.5781 | 1729.9 | 2796.0 | 1807.4 | 15.5 | 0.011 | 2 | 0.031 | none |
| 2a-09 | defer | series 1 and 2 both within 0.10 dex and not separable | 1 or 2 | -5.378 | 0.06 | 0.5820 | 1718.4 | 2855.6 | 1847.5 | 27.7 | 0.007 | 2 | 0.024 | none |
| 2a-10 | defer | series 1 and 2 both within 0.10 dex and not separable | 1 or 2 | -5.421 | 0.06 | 0.5830 | 1715.3 | 2871.7 | 1862.9 | 26.1 | -0.007 | 2 | 0.010 | none |
| 2a-11 | defer | series 1 and 3 both within 0.10 dex and not separable | 1 or 3 | -5.886 | 0.06 | 0.6000 | 1666.6 | 3133.9 | 2028.0 | 25.5 | 0.003 | 3 | 0.004 | none |
| 2a-12 | defer | series 1 and 2 both within 0.10 dex and not separable | 1 or 2 | -6.282 | 0.06 | 0.6108 | 1637.1 | 3300.1 | 2169.2 | 24.0 | -0.091 | 2 | 0.092 | none |
| 2a-14 | defer | series 2 and 3 both within 0.10 dex and not separable | 2 or 3 | -4.464 | 0.06 | 0.5477 | 1825.7 | 2329.4 | 1522.9 | 29.1 | 0.006 | 3 | 0.017 | none |
| 2a-15 | defer | series 2 and 3 both within 0.10 dex and not separable | 2 or 3 | -4.762 | 0.06 | 0.5586 | 1790.1 | 2496.7 | 1628.6 | 30.6 | 0.005 | 3 | 0.013 | none |
| 2a-16 | defer | 0.10 to 0.18 dex off the nearer Table line | unassigned | -5.847 | 0.06 | 0.5935 | 1685.0 | 3033.1 | 2015.2 | 22.0 | -0.131 | 3 | 0.134 | none |
| 2a-17 | defer | 0.10 to 0.18 dex off the nearer Table line | unassigned | -6.130 | 0.06 | 0.6046 | 1653.9 | 3204.7 | 2115.4 | 23.0 | -0.110 | 1 | 0.113 | none |
| 2a-18 | defer | series 3 and 2 both within 0.10 dex and not separable | 3 or 2 | -3.689 | 0.06 | 0.5162 | 1937.2 | 1844.3 | 1248.0 | 22.0 | -0.057 | 2 | 0.078 | none |
| 2a-19 | defer | 0.10 to 0.18 dex off the nearer Table line | unassigned | -4.113 | 0.06 | 0.5300 | 1886.8 | 2056.4 | 1398.9 | 27.0 | -0.109 | 2 | 0.126 | none |
| 2a-20 | defer | series 3 and 2 both within 0.10 dex and not separable | 3 or 2 | -4.342 | 0.06 | 0.5399 | 1852.1 | 2209.1 | 1480.0 | 33.2 | -0.071 | 2 | 0.085 | none |
| 2a-21 | defer | series 3 and 2 both within 0.10 dex and not separable | 3 or 2 | -4.379 | 0.06 | 0.5412 | 1847.7 | 2229.1 | 1493.2 | 26.4 | -0.073 | 2 | 0.087 | none |
| 2a-23 | defer | series 3 and 2 both within 0.10 dex and not separable | 3 or 2 | -4.935 | 0.06 | 0.5644 | 1771.8 | 2585.4 | 1690.3 | 25.0 | -0.005 | 2 | 0.012 | none |
| 2a-24 | defer | series 3 and 2 both within 0.10 dex and not separable | 3 or 2 | -5.118 | 0.06 | 0.5688 | 1758.0 | 2653.8 | 1755.7 | 23.0 | -0.068 | 2 | 0.073 | none |
| 2a-25 | defer | series 3 and 2 both within 0.10 dex and not separable | 3 or 2 | -5.132 | 0.06 | 0.5711 | 1751.0 | 2688.8 | 1760.2 | 28.3 | -0.021 | 2 | 0.025 | none |
| 2a-26 | defer | series 3 and 2 both within 0.10 dex and not separable | 3 or 2 | -5.512 | 0.06 | 0.5860 | 1706.3 | 2918.6 | 1895.1 | 27.0 | 0.002 | 2 | 0.002 | none |
| 2a-27 | refuse | stroke artifact; distance transform at this pixel is 8.0 px, not the recorded 14.3. The centre is the fitted line, 50 px below the triangle | unassigned | -6.186 | 0.06 | 0.6156 | 1624.3 | 3373.7 | 2133.7 | 8.0 | 0.125 | 2 | 0.134 | T outside the nearer line's printed interval |
| 2a-31 | read | within 0.10 dex of one Table line | 4 | -5.642 | 0.06 | 0.5132 | 1948.7 | 1797.6 | 1954.5 | 15.5 | -0.068 | 3 | 2.092 | outside labelled ticks; T outside the nearer line's printed interval |
| 2a-32 | read | within 0.10 dex of one Table line | 4 | -5.964 | 0.06 | 0.5291 | 1890.1 | 2042.4 | 2067.9 | 20.6 | 0.032 | 3 | 1.984 | none |
| 2a-33 | read | within 0.10 dex of one Table line | 4 | -5.955 | 0.06 | 0.5307 | 1884.4 | 2067.0 | 2064.4 | 21.2 | 0.083 | 3 | 1.932 | none |
| 2a-34 | read | within 0.10 dex of one Table line | 4 | -6.421 | 0.06 | 0.5415 | 1846.7 | 2233.6 | 2231.1 | 17.0 | -0.097 | 3 | 2.107 | none |
| 2a-35 | read | within 0.10 dex of one Table line | 4 | -6.595 | 0.06 | 0.5483 | 1824.0 | 2337.3 | 2292.8 | 17.7 | -0.092 | 3 | 2.099 | none |
| 2a-37 | defer | 0.10 to 0.18 dex off the nearer Table line | unassigned | -7.053 | 0.06 | 0.5652 | 1769.2 | 2598.2 | 2455.5 | 14.0 | -0.102 | 3 | 2.100 | none |
| 2a-38 | read | within 0.10 dex of one Table line | 4 | -7.328 | 0.06 | 0.5768 | 1733.8 | 2775.8 | 2553.2 | 19.0 | -0.072 | 3 | 2.065 | none |
| 2a-40 | read | within 0.10 dex of one Table line | 4 | -7.615 | 0.06 | 0.5910 | 1692.1 | 2994.4 | 2654.3 | 28.0 | 0.018 | 2 | 1.967 | none |
| 2a-41 | defer | 0.10 to 0.18 dex off the nearer Table line | unassigned | -7.826 | 0.06 | 0.5918 | 1689.7 | 3007.3 | 2730.6 | 14.0 | -0.172 | 2 | 2.155 | T outside the nearer line's printed interval |
| 2a-07 | refuse | dt below 10 px and off every Table line; not a filled marker | unassigned | -3.221 | 0.06 | 0.5774 | 1732.0 | 2784.9 | 1068.5 | 8.0 | 2.036 | 2 | 2.056 | none |
| 2a-13 | refuse | dt below 10 px and off every Table line; not a filled marker | unassigned | -7.718 | 0.06 | 0.6329 | 1580.0 | 3639.2 | 2684.8 | 5.0 | -0.913 | 2 | 0.928 | T outside the nearer line's printed interval |
| 2a-29 | refuse | dt below 10 px and off every Table line; not a filled marker | unassigned | -6.111 | 0.06 | 0.6288 | 1590.4 | 3576.1 | 2104.6 | 2.2 | 0.553 | 2 | 0.567 | T outside the nearer line's printed interval |
| 2a-30 | refuse | dt below 10 px and off every Table line; not a filled marker | unassigned | -6.335 | 0.06 | 0.6297 | 1588.2 | 3589.4 | 2185.3 | 8.0 | 0.353 | 2 | 0.367 | T outside the nearer line's printed interval |

| 2a-80 | refuse | more than 0.18 dex from every Table line | unassigned | -4.102 | 0.06 | 0.5226 | 1913.7 | 1942.0 | 1396.0 | 31.8 | -0.298 | 2 | 0.317 | none |
| 2a-81 | refuse | more than 0.18 dex from every Table line | unassigned | -4.419 | 0.06 | 0.5321 | 1879.3 | 2089.0 | 1509.0 | 29.0 | -0.358 | 2 | 0.374 | none |
| 2a-82 | refuse | more than 0.18 dex from every Table line | unassigned | -4.507 | 0.06 | 0.5378 | 1859.5 | 2176.0 | 1540.0 | 29.1 | -0.294 | 2 | 0.308 | none |
| 2a-83 | refuse | more than 0.18 dex from every Table line | unassigned | -4.804 | 0.06 | 0.5465 | 1829.7 | 2311.0 | 1646.0 | 25.0 | -0.355 | 2 | 0.367 | none |
| 2a-84 | refuse | more than 0.18 dex from every Table line | unassigned | -4.839 | 0.06 | 0.5495 | 1819.9 | 2356.0 | 1658.0 | 25.3 | -0.311 | 2 | 0.321 | none |
| 2a-85 | defer | series 1 and 2 both within 0.10 dex and not separable | 1 or 2 | -4.846 | 0.06 | 0.5644 | 1771.9 | 2585.0 | 1658.0 | 25.0 | 0.048 | 2 | 0.077 | none |
| 2a-86 | defer | 0.10 to 0.18 dex off the nearer Table line | unassigned | -4.935 | 0.06 | 0.5600 | 1785.7 | 2518.0 | 1691.0 | 25.0 | -0.123 | 2 | 0.131 | none |
| 2a-87 | defer | 0.10 to 0.18 dex off the nearer Table line | unassigned | -4.949 | 0.06 | 0.5721 | 1748.0 | 2704.0 | 1694.0 | 28.0 | 0.161 | 2 | 0.185 | none |
| 2a-88 | defer | 0.10 to 0.18 dex off the nearer Table line | unassigned | -5.140 | 0.06 | 0.5670 | 1763.6 | 2626.0 | 1764.0 | 16.1 | -0.139 | 2 | 0.145 | none |
| 2a-89 | refuse | more than 0.18 dex from every Table line | unassigned | -5.183 | 0.06 | 0.5583 | 1791.1 | 2492.0 | 1781.0 | 25.2 | -0.417 | 2 | 0.425 | none |
| 2a-90 | refuse | more than 0.18 dex from every Table line | unassigned | -5.212 | 0.06 | 0.5615 | 1781.0 | 2541.0 | 1791.0 | 24.0 | -0.360 | 2 | 0.367 | none |
| 2a-91 | refuse | more than 0.18 dex from every Table line | unassigned | -5.291 | 0.06 | 0.5697 | 1755.3 | 2667.0 | 1818.0 | 25.0 | -0.218 | 2 | 0.223 | none |
| 2a-92 | refuse | more than 0.18 dex from every Table line | unassigned | -5.610 | 0.06 | 0.5773 | 1732.2 | 2784.0 | 1932.0 | 24.0 | -0.332 | 2 | 0.334 | none |
| 2a-93 | refuse | more than 0.18 dex from every Table line | unassigned | -5.646 | 0.06 | 0.5835 | 1713.9 | 2879.0 | 1944.0 | 23.1 | -0.202 | 2 | 0.202 | none |
| 2a-94 | refuse | more than 0.18 dex from every Table line | unassigned | -5.741 | 0.06 | 0.5788 | 1727.7 | 2807.0 | 1979.0 | 35.7 | -0.423 | 2 | 0.424 | none |
| 2a-95 | defer | 0.10 to 0.18 dex off the nearer Table line | unassigned | -5.767 | 0.06 | 0.5998 | 1667.3 | 3130.0 | 1985.0 | 17.0 | 0.115 | 3 | 0.117 | none |
| 2a-96 | refuse | more than 0.18 dex from every Table line | unassigned | -5.771 | 0.06 | 0.5846 | 1710.6 | 2896.0 | 1989.0 | 28.8 | -0.297 | 3 | 0.297 | none |
| 2a-97 | refuse | more than 0.18 dex from every Table line | unassigned | -5.787 | 0.06 | 0.5764 | 1734.8 | 2771.0 | 1996.0 | 23.9 | -0.532 | 2 | 0.534 | none |
| 2a-98 | refuse | more than 0.18 dex from every Table line | unassigned | -6.020 | 0.06 | 0.5961 | 1677.6 | 3073.0 | 2077.0 | 23.0 | -0.232 | 3 | 0.236 | none |
| 2a-99 | refuse | more than 0.18 dex from every Table line | unassigned | -6.550 | 0.06 | 0.6065 | 1648.8 | 3233.0 | 2267.0 | 23.8 | -0.480 | 1 | 0.481 | none |
| 2a-100 | refuse | more than 0.18 dex from every Table line | unassigned | -6.686 | 0.06 | 0.6188 | 1616.1 | 3422.0 | 2314.0 | 23.4 | -0.275 | 2 | 0.281 | T outside the nearer line's printed interval |
| 2a-101 | defer | isolated triangle; not assigned to a Table line | unassigned | -6.723 | 0.06 | 0.5494 | 1820.2 | 2355.0 | 2339.0 | 14.2 | -0.190 | 3 | 2.197 | none |
| 2a-102 | defer | 0.10 to 0.18 dex off the nearer Table line | unassigned | -7.264 | 0.06 | 0.6549 | 1526.9 | 3978.0 | 2517.0 | 14.4 | 0.104 | 2 | 0.126 | outside labelled ticks; T outside the nearer line's printed interval |
| 2a-103 | refuse | more than 0.18 dex from every Table line; filled triangle | unassigned | -6.048 | 0.06 | 0.6157 | 1624.1 | 3375.0 | 2084.0 | 15.0 | 0.264 | 2 | 0.274 | T outside the nearer line's printed interval |
| 2a-104 | defer | 0.10 to 0.18 dex off the nearer Table line; filled triangle | unassigned | -6.137 | 0.06 | 0.6159 | 1623.6 | 3378.0 | 2116.0 | 15.3 | 0.181 | 2 | 0.191 | T outside the nearer line's printed interval |

Fit-stroke peaks on panel 2a: 5 peaks with dt < 10 px and |log10 p − nearer Table line| ≤ 0.10 dex. They are the line, not markers, and they are not given values.

Refused as glyphs, x-axis numerals near the 0.60 and 0.64 labels: 2a-42 at (3169.7, 2893.3) dt 4.0, 2a-43 at (3657.5, 2892.3) dt 5.0, 2a-44 at (3687.4, 2893.7) dt 4.0, 2a-45 at (3793.7, 2891.1) dt 4.2, 2a-46 at (3878.1, 2893.9) dt 7.2.

Bare stretches of at least 100 px, with no separable mark:

| series | x0 | x1 | length px |
| --- | --- | --- | --- |
| 1 | 1875 | 2209 | 334 |
| 2 | 1872 | 2049 | 177 |
| 3 | 1682 | 1823 | 141 |
| 3 | 1872 | 2048 | 176 |
| 4 | 1817 | 2011 | 194 |
| 4 | 2082 | 2228 | 146 |
| 4 | 2375 | 2593 | 218 |
| 4 | 2616 | 2758 | 142 |
| 4 | 2813 | 2956 | 143 |

#### Panel 2b

series 1 (f = 1.12e-3, Table n = 28, T 1704–1907 K): 13 read; series 2 (f = 2.39e-3, Table n = 7, T 1764–1983 K): 1 read; series 3 (f = 3.35e-3, Table n = 8, T 1795–1970 K): 6 read; series 4 (f = 3.75e-1, Table n = 9, T 1847–1947 K): 7 read.

Series 2 has Table n = 7 and σ(B) = ±0.38. One separable circle sits on that line. The other six are not separable from the stroke, and they are not invented. A walk on the central equation through a ±28 px band found a ridge distance-transform radius of 0.62 px, which is white space, not a row of marks.

| id | decision | reason | series | log10 p_ef | ± dex | 1000/T | T/K | cx | cy | dt | offset dex | second series | second abs dex | flags |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2b-00 | read | within 0.10 dex of one Table line | 1 | -6.460 | 0.04 | 0.5240 | 1908.3 | 2245.2 | 3860.1 | 19.0 | 0.057 | 2 | 0.414 | T outside the nearer line's printed interval |
| 2b-01 | read | within 0.10 dex of one Table line | 1 | -6.518 | 0.04 | 0.5261 | 1900.9 | 2298.1 | 3894.7 | 14.6 | 0.058 | 2 | 0.417 | none |
| 2b-02 | read | within 0.10 dex of one Table line | 1 | -6.824 | 0.04 | 0.5344 | 1871.2 | 2514.9 | 4078.2 | 26.7 | -0.005 | 2 | 0.357 | none |
| 2b-03 | read | within 0.10 dex of one Table line | 1 | -7.006 | 0.04 | 0.5389 | 1855.6 | 2632.1 | 4186.8 | 15.3 | -0.055 | 2 | 0.309 | none |
| 2b-04 | read | within 0.10 dex of one Table line | 1 | -7.022 | 0.04 | 0.5405 | 1850.0 | 2674.4 | 4196.0 | 19.0 | -0.023 | 2 | 0.342 | none |
| 2b-09 | read | within 0.10 dex of one Table line | 1 | -7.341 | 0.04 | 0.5491 | 1821.2 | 2896.0 | 4387.3 | 15.0 | -0.094 | 2 | 0.274 | none |
| 2b-11 | read | within 0.10 dex of one Table line | 1 | -7.365 | 0.04 | 0.5520 | 1811.5 | 2972.8 | 4401.0 | 15.7 | -0.032 | 2 | 0.338 | none |
| 2b-14 | defer | 0.10 to 0.18 dex off the nearer Table line | unassigned | -7.698 | 0.04 | 0.5607 | 1783.5 | 3197.3 | 4599.9 | 15.0 | -0.112 | 2 | 0.261 | none |
| 2b-16 | read | within 0.10 dex of one Table line | 1 | -7.714 | 0.04 | 0.5640 | 1773.0 | 3283.3 | 4609.1 | 15.6 | -0.032 | 2 | 0.343 | none |
| 2b-19 | read | within 0.10 dex of one Table line | 1 | -8.095 | 0.04 | 0.5785 | 1728.7 | 3658.9 | 4835.5 | 15.5 | 0.009 | 2 | 0.391 | outside labelled ticks |
| 2b-20 | read | within 0.10 dex of one Table line | 1 | -8.225 | 0.04 | 0.5805 | 1722.6 | 3711.7 | 4913.7 | 18.4 | -0.061 | 2 | 0.321 | outside labelled ticks |
| 2b-21 | read | within 0.10 dex of one Table line | 1 | -8.112 | 0.04 | 0.5812 | 1720.7 | 3728.7 | 4845.0 | 14.2 | 0.071 | 2 | 0.453 | outside labelled ticks |
| 2b-22 | read | within 0.10 dex of one Table line | 1 | -8.233 | 0.04 | 0.5822 | 1717.6 | 3756.0 | 4918.5 | 19.0 | -0.020 | 2 | 0.362 | outside labelled ticks |
| 2b-23 | read | within 0.10 dex of one Table line | 1 | -8.352 | 0.04 | 0.5873 | 1702.6 | 3889.0 | 4989.1 | 26.6 | 0.010 | 2 | 0.395 | outside labelled ticks; T outside the nearer line's printed interval |
| 2b-24 | read | within 0.10 dex of one Table line | 2 | -7.989 | 0.04 | 0.5603 | 1784.7 | 3187.9 | 4776.2 | 20.8 | -0.040 | 3 | 0.075 | none |
| 2b-28 | read | within 0.10 dex of one Table line | 3 | -6.904 | 0.04 | 0.5187 | 1927.9 | 2107.0 | 4130.5 | 24.2 | -0.056 | 2 | 0.187 | none |
| 2b-31 | read | within 0.10 dex of one Table line | 3 | -7.379 | 0.04 | 0.5384 | 1857.4 | 2618.0 | 4413.1 | 24.8 | 0.043 | 2 | 0.080 | none |
| 2b-38 | read | within 0.10 dex of one Table line | 3 | -7.935 | 0.04 | 0.5542 | 1804.5 | 3028.4 | 4745.3 | 24.7 | -0.051 | 2 | 0.168 | none |
| 2b-40 | refuse | numeral glyph on the plot, dt below the filled-marker cut | unassigned | -7.382 | 0.04 | 0.5103 | 1959.7 | 1889.0 | 4422.1 | 2.8 | 0.317 | 3 | 0.780 | T outside the nearer line's printed interval |
| 2b-41 | read | within 0.10 dex of one Table line | 4 | -7.776 | 0.04 | 0.5133 | 1948.1 | 1967.7 | 4659.9 | 15.3 | 0.011 | 3 | 1.085 | T outside the nearer line's printed interval |
| 2b-43 | read | within 0.10 dex of one Table line | 4 | -8.033 | 0.04 | 0.5213 | 1918.3 | 2174.6 | 4813.7 | 15.6 | -0.017 | 3 | 1.110 | outside labelled ticks |
| 2b-47 | read | within 0.10 dex of one Table line | 4 | -8.308 | 0.04 | 0.5309 | 1883.6 | 2424.0 | 4977.4 | 16.0 | -0.014 | 3 | 1.104 | outside labelled ticks |
| 2b-48 | read | within 0.10 dex of one Table line | 4 | -8.402 | 0.04 | 0.5341 | 1872.3 | 2507.0 | 5033.7 | 14.4 | -0.016 | 3 | 1.105 | outside labelled ticks |
| 2b-49 | read | within 0.10 dex of one Table line | 4 | -8.445 | 0.04 | 0.5355 | 1867.3 | 2544.3 | 5059.0 | 15.0 | -0.017 | 3 | 1.105 | outside labelled ticks |
| 2b-50 | read | within 0.10 dex of one Table line | 4 | -8.599 | 0.04 | 0.5412 | 1847.6 | 2692.6 | 5150.7 | 14.4 | -0.006 | 3 | 1.093 | outside labelled ticks |
| 2b-18 | refuse | dt below 10 px and off every Table line; not a filled marker | unassigned | -6.814 | 0.04 | 0.5784 | 1729.0 | 3656.0 | 4060.0 | 6.0 | 1.287 | 2 | 1.668 | none |
| 2b-39 | refuse | dt below 10 px and off every Table line; not a filled marker | unassigned | -8.407 | 0.04 | 0.5601 | 1785.5 | 3181.2 | 5029.8 | 6.4 | -0.352 | 2 | 0.467 | outside labelled ticks; T outside the nearer line's printed interval |

| 2b-80 | read | within 0.10 dex of one Table line | 3 | -6.434 | 0.04 | 0.5037 | 1985.4 | 1717.0 | 3850.0 | 23.4 | -0.025 | 2 | 0.162 | T outside the nearer line's printed interval |
| 2b-81 | read | within 0.10 dex of one Table line | 3 | -6.435 | 0.04 | 0.5054 | 1978.8 | 1761.0 | 3850.0 | 24.0 | 0.024 | 2 | 0.112 | T outside the nearer line's printed interval |
| 2b-82 | defer | 0.10 to 0.18 dex off the nearer Table line | unassigned | -7.759 | 0.04 | 0.5463 | 1830.5 | 2824.0 | 4641.0 | 23.8 | -0.105 | 2 | 0.225 | none |
| 2b-83 | read | within 0.10 dex of one Table line; filled triangle | 4 | -8.251 | 0.04 | 0.5318 | 1880.4 | 2447.0 | 4943.0 | 14.9 | 0.068 | 3 | 1.022 | outside labelled ticks |
| 2b-84 | read | within 0.10 dex of one Table line | 3 | -8.348 | 0.04 | 0.5670 | 1763.6 | 3362.0 | 4992.0 | 24.0 | -0.089 | 2 | 0.201 | outside labelled ticks; T outside the nearer line's printed interval |

Fit-stroke peaks on panel 2b: 20 peaks with dt < 10 px and |log10 p − nearer Table line| ≤ 0.10 dex. They are the line, not markers, and they are not given values.

Refused as glyphs, ink above the top labelled tick y = 3588: 2b-07 at (2874.8, 3520.3) dt 6.0, 2b-08 at (2895.8, 3555.2) dt 2.8, 2b-10 at (2942.4, 3515.2) dt 6.1, 2b-12 at (3018.9, 3517.7) dt 8.0.

Bare stretches of at least 100 px, with no separable mark:

| series | x0 | x1 | length px |
| --- | --- | --- | --- |
| 1 | 2306 | 2475 | 169 |
| 1 | 3087 | 3206 | 119 |
| 1 | 3401 | 3636 | 235 |
| 2 | 1703 | 2622 | 919 |
| 2 | 2639 | 3177 | 538 |
| 2 | 3207 | 3387 | 180 |
| 3 | 1789 | 2083 | 294 |
| 3 | 2178 | 2393 | 215 |
| 4 | 1990 | 2162 | 172 |

#### Panel 2c

series 1 (f = 1.12e-3, Table n = 21, T 1610–1907 K): 13 read; series 2 (f = 2.39e-3, Table n = 14, T 1749–1871 K): 2 read; series 3 (f = 3.35e-3, Table n = 6, T 1795–1970 K): 7 read; series 4 (f = 3.75e-1, Table n = 7, T 1847–1947 K): 6 read.

| id | decision | reason | series | log10 p_ef | ± dex | 1000/T | T/K | cx | cy | dt | offset dex | second series | second abs dex | flags |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2c-00 | read | within 0.10 dex of one Table line | 1 | -5.742 | 0.04 | 0.5241 | 1908.2 | 2165.3 | 6115.6 | 22.0 | -0.039 | 2 | 0.448 | outside labelled ticks; T outside the nearer line's printed interval |
| 2c-01 | read | within 0.10 dex of one Table line | 1 | -5.730 | 0.04 | 0.5256 | 1902.8 | 2196.2 | 6109.1 | 15.0 | 0.020 | 2 | 0.508 | outside labelled ticks |
| 2c-03 | read | within 0.10 dex of one Table line | 1 | -6.083 | 0.04 | 0.5351 | 1868.9 | 2393.8 | 6294.2 | 17.0 | -0.031 | 2 | 0.461 | none |
| 2c-04 | read | within 0.10 dex of one Table line | 1 | -6.078 | 0.04 | 0.5384 | 1857.5 | 2461.8 | 6291.2 | 15.3 | 0.077 | 2 | 0.570 | none |
| 2c-05 | defer | isolated triangle; not assigned to a Table line | unassigned | -6.262 | 0.04 | 0.5485 | 1823.1 | 2672.2 | 6386.7 | 15.5 | 0.215 | 2 | 0.712 | none |
| 2c-06 | read | within 0.10 dex of one Table line | 1 | -6.554 | 0.04 | 0.5512 | 1814.2 | 2728.1 | 6540.5 | 18.0 | 0.009 | 2 | 0.507 | none |
| 2c-07 | defer | 0.10 to 0.18 dex off the nearer Table line | unassigned | -6.733 | 0.04 | 0.5521 | 1811.3 | 2746.5 | 6635.3 | 14.0 | -0.142 | 2 | 0.356 | none |
| 2c-08 | read | within 0.10 dex of one Table line | 1 | -6.870 | 0.04 | 0.5604 | 1784.6 | 2917.8 | 6706.0 | 15.0 | -0.017 | 2 | 0.485 | none |
| 2c-11 | read | within 0.10 dex of one Table line | 1 | -6.866 | 0.04 | 0.5637 | 1774.1 | 2986.5 | 6703.7 | 15.0 | 0.091 | 2 | 0.595 | none |
| 2c-17 | read | within 0.10 dex of one Table line | 1 | -7.545 | 0.04 | 0.5810 | 1721.3 | 3345.0 | 7059.6 | 15.8 | -0.039 | 2 | 0.471 | none |
| 2c-18 | read | within 0.10 dex of one Table line | 1 | -7.576 | 0.04 | 0.5822 | 1717.8 | 3369.6 | 7075.9 | 16.0 | -0.033 | 2 | 0.478 | none |
| 2c-20 | read | within 0.10 dex of one Table line | 1 | -7.758 | 0.04 | 0.5875 | 1702.1 | 3481.0 | 7171.0 | 17.9 | -0.044 | 2 | 0.469 | none |
| 2c-28 | read | within 0.10 dex of one Table line | 1 | -8.392 | 0.04 | 0.6109 | 1636.8 | 3966.3 | 7502.2 | 14.9 | 0.065 | 2 | 0.586 | outside labelled ticks |
| 2c-32 | refuse | junction artifact; pixel is white, distance transform 0 px, not the recorded 12 px | unassigned | -8.820 | 0.04 | 0.6203 | 1612.0 | 4161.5 | 7727.2 | 0.0 | -0.066 | 2 | 0.460 | outside labelled ticks |
| 2c-33 | refuse | numeral glyph (6 or 4) on the x-axis label row | unassigned | -9.017 | 0.04 | 0.6204 | 1611.8 | 4162.8 | 7831.3 | 10.0 | -0.260 | 2 | 0.265 | outside labelled ticks |
| 2c-34 | read | within 0.10 dex of one Table line; re-read filled triangle above the junction, dt peak (4177, 7651) radius 15 | 1 | -8.679 | 0.04 | 0.6211 | 1610.0 | 4177.7 | 7652.3 | 15.0 | 0.100 | 2 | 0.626 | outside labelled ticks; T outside the nearer line's printed interval |
| 2c-35 | refuse | junction artifact; distance transform 4.1 px at this pixel, not the recorded 11 px | unassigned | -8.841 | 0.04 | 0.6212 | 1609.7 | 4179.6 | 7737.9 | 4.1 | -0.058 | 2 | 0.468 | outside labelled ticks; T outside the nearer line's printed interval |
| 2c-37 | read | within 0.10 dex of one Table line | 2 | -7.252 | 0.04 | 0.5570 | 1795.3 | 2848.4 | 6909.1 | 24.2 | -0.005 | 3 | 0.140 | none |
| 2c-38 | read | within 0.10 dex of one Table line | 2 | -7.432 | 0.04 | 0.5611 | 1782.2 | 2933.5 | 7003.2 | 22.2 | -0.053 | 3 | 0.093 | none |
| 2c-44 | read | within 0.10 dex of one Table line | 3 | -6.628 | 0.04 | 0.5349 | 1869.5 | 2390.2 | 6582.8 | 30.1 | 0.051 | 2 | 0.090 | none |
| 2c-45 | read | within 0.10 dex of one Table line | 3 | -6.899 | 0.04 | 0.5420 | 1845.1 | 2536.8 | 6724.8 | 22.8 | 0.008 | 2 | 0.134 | none |
| 2c-49 | read | within 0.10 dex of one Table line | 3 | -7.279 | 0.04 | 0.5526 | 1809.7 | 2756.7 | 6923.7 | 14.4 | -0.029 | 2 | 0.173 | none |
| 2c-50 | read | within 0.10 dex of one Table line | 3 | -7.323 | 0.04 | 0.5531 | 1807.8 | 2768.3 | 6946.9 | 24.7 | -0.055 | 2 | 0.199 | none |
| 2c-55 | read | within 0.10 dex of one Table line | 4 | -7.622 | 0.04 | 0.5138 | 1946.3 | 1952.4 | 7112.3 | 18.4 | -0.034 | 3 | 1.624 | outside labelled ticks |
| 2c-57 | read | within 0.10 dex of one Table line | 4 | -7.658 | 0.04 | 0.5172 | 1933.4 | 2023.5 | 7130.4 | 14.3 | 0.042 | 3 | 1.549 | none |
| 2c-59 | read | within 0.10 dex of one Table line | 4 | -7.911 | 0.04 | 0.5226 | 1913.5 | 2135.0 | 7263.7 | 19.7 | -0.037 | 3 | 1.629 | none |
| 2c-61 | read | within 0.10 dex of one Table line | 4 | -8.105 | 0.04 | 0.5312 | 1882.5 | 2313.7 | 7364.7 | 16.2 | 0.049 | 3 | 1.545 | outside labelled ticks |
| 2c-63 | read | within 0.10 dex of one Table line | 4 | -8.381 | 0.04 | 0.5383 | 1857.6 | 2461.3 | 7509.6 | 15.3 | 0.004 | 3 | 1.592 | outside labelled ticks |
| 2c-65 | read | within 0.10 dex of one Table line | 4 | -8.556 | 0.04 | 0.5415 | 1846.7 | 2527.2 | 7601.6 | 16.0 | -0.067 | 3 | 1.664 | outside labelled ticks; T outside the nearer line's printed interval |
| 2c-66 | refuse | numeral glyph (6 or 4) on the x-axis label row | unassigned | -9.005 | 0.04 | 0.5450 | 1834.8 | 2599.6 | 7838.1 | 10.0 | -0.402 | 3 | 2.000 | outside labelled ticks; T outside the nearer line's printed interval |
| 2c-12 | refuse | dt below 10 px and off every Table line; not a filled marker | unassigned | -5.816 | 0.04 | 0.5649 | 1770.3 | 3011.3 | 6147.5 | 2.2 | 1.180 | 2 | 1.684 | outside labelled ticks |
| 2c-25 | refuse | dt below 10 px and off every Table line; not a filled marker | unassigned | -8.229 | 0.04 | 0.5970 | 1675.2 | 3676.5 | 7418.6 | 5.0 | -0.216 | 2 | 0.300 | outside labelled ticks |

| 2c-80 | read | within 0.10 dex of one Table line | 3 | -6.960 | 0.04 | 0.5421 | 1844.6 | 2540.0 | 6757.0 | 24.7 | -0.048 | 2 | 0.190 | none |
| 2c-81 | defer | 0.10 to 0.18 dex off the nearer Table line | unassigned | -7.091 | 0.04 | 0.5441 | 1838.0 | 2580.0 | 6826.0 | 24.4 | -0.117 | 2 | 0.259 | none |
| 2c-82 | read | within 0.10 dex of one Table line | 1 | -7.489 | 0.04 | 0.5806 | 1722.3 | 3338.0 | 7030.0 | 16.4 | 0.006 | 2 | 0.516 | none |
| 2c-83 | defer | isolated triangle; not assigned to a Table line | unassigned | -7.668 | 0.04 | 0.5798 | 1724.9 | 3320.0 | 7125.0 | 15.0 | -0.200 | 2 | 0.309 | none |
| 2c-84 | defer | 0.10 to 0.18 dex off the nearer Table line; filled triangle | unassigned | -7.684 | 0.04 | 0.5815 | 1719.6 | 3357.0 | 7133.0 | 15.0 | -0.160 | 2 | 0.351 | none |
| 2c-85 | read | within 0.10 dex of one Table line | 3 | -7.754 | 0.04 | 0.5662 | 1766.3 | 3038.0 | 7173.0 | 28.0 | -0.068 | 2 | 0.214 | T outside the nearer line's printed interval |
| 2c-86 | read | within 0.10 dex of one Table line | 3 | -7.854 | 0.04 | 0.5714 | 1750.0 | 3147.0 | 7225.0 | 24.2 | 0.002 | 2 | 0.145 | T outside the nearer line's printed interval |

Fit-stroke peaks on panel 2c: 24 peaks with dt < 10 px and |log10 p − nearer Table line| ≤ 0.10 dex. They are the line, not markers, and they are not given values.

Refused as glyphs, x-axis label ink under the axis (cy > 7800): 2c-36 at (4255.7, 7823.3) dt 8.0, 2c-39 at (4054.0, 7831.8) dt 7.2, 2c-51 at (3440.6, 7831.5) dt 6.0, 2c-52 at (3628.9, 7833.0) dt 7.2, 2c-53 at (3739.1, 7831.4) dt 6.4, 2c-54 at (3824.4, 7831.6) dt 8.0, 2c-56 at (1991.2, 7839.1) dt 6.0, 2c-58 at (2111.9, 7835.0) dt 8.0, 2c-60 at (2187.1, 7838.1) dt 8.0, 2c-62 at (2394.4, 7838.0) dt 6.4, 2c-64 at (2515.6, 7832.1) dt 8.0, 2c-67 at (2798.4, 7836.5) dt 7.2, 2c-68 at (2917.5, 7829.2) dt 8.0, 2c-69 at (2989.6, 7834.4) dt 6.4, 2c-70 at (3245.9, 7834.0) dt 8.0, 2c-71 at (3362.2, 7829.3) dt 8.1.

Bare stretches of at least 100 px, with no separable mark:

| series | x0 | x1 | length px |
| --- | --- | --- | --- |
| 1 | 2218 | 2375 | 157 |
| 1 | 2468 | 2698 | 230 |
| 1 | 2748 | 2898 | 150 |
| 1 | 2936 | 3313 | 377 |
| 1 | 3506 | 3949 | 443 |
| 1 | 3973 | 4141 | 168 |
| 2 | 2415 | 2835 | 420 |
| 2 | 2957 | 3183 | 226 |
| 3 | 1794 | 2336 | 542 |
| 3 | 2569 | 2752 | 183 |
| 4 | 2184 | 2286 | 102 |
| 4 | 2322 | 2444 | 122 |



The core at (3375, 2084), radius 15, is a filled downward triangle and is not 2a-27. 2a-27's stored centre (3373.7, 2133.7) is 50 px lower. That pixel is black, and its distance transform is 8.0 px, the fitted-line width, not the recorded 14.3. The judged crop is the 1200 dpi page `pdftoppm -r 1200 -f 5 -l 5 -x 100 -y 700 -W 5200 -H 9800` at x 3348–3410, y 2060–2160, file `/tmp/goal-flight-501/dispatch/t1372-w2-w9-t1.scratch/renders/2a27-zoom-1200.png`. The upper body's corners are (3352, 2070), (3400, 2073), and (3367, 2103). That triangle's incenter is (3371.1, 2083.2), 4 px from the distance-transform peak, so 2a-103 stores the peak. Table 1 prints `log p_ef(SiO) = -A/T + B` and, for this-work molybdenum, T 1623–1930 K, A = 27880±612, B = 10.84±0.15. It prints no point at 1624 K. 2a-103 is an independent figure point, 0.264 dex from series 3, 0.274 from series 2, and 0.278 from series 1, and is refused. The silhouette widens again below a 19 px waist: a second filled body, peak (3378, 2116), radius 15.3, 32 px from the first. The inscribed circles do not overlap, so it is 2a-104, deferred at 0.181 dex from series 3. The core at (3372, 2148), radius 10, remains the stroke and stays on the refused 2a-27.

### Fig. 2 round-2 core list

Dark pixels are gray below 110 on the 1200 dpi crop. A core is a local maximum of the distance transform with radius at least 8 px. Maxima within 10 px, or within 0.42 of the smaller radius, are one core. A filled marker on this render has radius at least 14 px and a compact thick core. A core under that radius is the fit stroke (about 8 px), the axis bar, a tick, or a numeral. The two junction rows 2c-32 and 2c-35 are not cores: the pixels measure radius 0 and 4.1. The triangle above them is 2c-34.

Panel 2a: 149 cores. 37 match an existing row, 25 are new decision rows, 87 are refused.

| cx | cy | r px | colour | matched row |
| --- | --- | --- | --- | --- |
| 1612 | 999 | 8.0 | black | refused: y-axis stroke or tick; radius is not a filled marker |
| 1580 | 1003 | 11.7 | black | refused: y-axis stroke or tick; radius is not a filled marker |
| 2781 | 1065 | 8.0 | black | 2a-07 |
| 1580 | 1081 | 8.5 | black | refused: y-axis stroke or tick; radius is not a filled marker |
| 1864 | 1169 | 20.0 | black | 2a-00 |
| 1581 | 1184 | 9.2 | black | refused: y-axis stroke or tick; radius is not a filled marker |
| 2018 | 1202 | 17.0 | black | 2a-02 |
| 1844 | 1245 | 23.0 | black | 2a-18 |
| 1516 | 1315 | 8.0 | black | refused: y-axis stroke or tick; radius is not a filled marker |
| 1584 | 1361 | 8.5 | black | refused: y-axis stroke or tick; radius is not a filled marker |
| 1519 | 1376 | 8.6 | black | refused: y-axis stroke or tick; radius is not a filled marker |
| 1942 | 1396 | 31.8 | black | 2a-80 |
| 2057 | 1408 | 27.0 | black | 2a-19 |
| 2199 | 1489 | 33.2 | black | 2a-20 |
| 2089 | 1509 | 29.0 | black | 2a-81 |
| 2335 | 1524 | 29.1 | black | 2a-14 |
| 2176 | 1540 | 29.1 | black | 2a-82 |
| 1583 | 1580 | 8.0 | black | refused: y-axis stroke or tick; radius is not a filled marker |
| 2499 | 1629 | 31.1 | black | 2a-15 |
| 2311 | 1646 | 25.0 | black | 2a-83 |
| 2333 | 1652 | 19.0 | black | refused: overlap peak between two circles; not a separate marker |
| 2356 | 1658 | 25.3 | black | 2a-84 |
| 2585 | 1658 | 25.0 | black | 2a-85 |
| 1507 | 1670 | 8.0 | black | refused: y-axis stroke or tick; radius is not a filled marker |
| 2588 | 1672 | 24.2 | black | 2a-85 |
| 2518 | 1691 | 25.0 | black | 2a-86 |
| 2584 | 1693 | 25.3 | black | 2a-23 |
| 2704 | 1694 | 28.0 | black | 2a-87 |
| 1507 | 1700 | 8.0 | black | refused: y-axis stroke or tick; radius is not a filled marker |
| 1588 | 1726 | 8.5 | black | refused: y-axis stroke or tick; radius is not a filled marker |
| 1608 | 1726 | 8.0 | black | refused: y-axis stroke or tick; radius is not a filled marker |
| 2647 | 1752 | 23.0 | black | 2a-24 |
| 2680 | 1762 | 25.0 | black | 2a-25 |
| 2716 | 1763 | 30.0 | black | 2a-05 |
| 2626 | 1764 | 16.1 | black | 2a-88 |
| 2753 | 1767 | 10.0 | black | refused: radius is the fit stroke or a numeral, not a filled marker |
| 2492 | 1781 | 25.2 | black | 2a-89 |
| 2775 | 1785 | 17.7 | black | 2a-06 |
| 2541 | 1791 | 24.0 | black | 2a-90 |
| 1589 | 1806 | 10.0 | black | refused: y-axis stroke or tick; radius is not a filled marker |
| 2800 | 1806 | 17.0 | black | 2a-08 |
| 2762 | 1813 | 8.1 | black | refused: radius is the fit stroke or a numeral, not a filled marker |
| 1587 | 1818 | 8.0 | black | refused: y-axis stroke or tick; radius is not a filled marker |
| 2667 | 1818 | 25.0 | black | 2a-91 |
| 2796 | 1831 | 11.3 | black | refused: radius is the fit stroke or a numeral, not a filled marker |
| 1587 | 1845 | 8.0 | black | refused: stroke or axis ridge, span 73 px; radius is not a filled marker |
| 2645 | 1847 | 9.2 | black | refused: radius is the fit stroke or a numeral, not a filled marker |
| 2856 | 1848 | 26.1 | black | 2a-09 |
| 2875 | 1866 | 27.7 | black | 2a-10 |
| 2896 | 1879 | 27.2 | black | refused: same marker as 2a-26 |
| 2914 | 1892 | 28.6 | black | 2a-26 |
| 2926 | 1901 | 30.5 | black | 2a-26 |
| 2784 | 1932 | 24.0 | black | 2a-92 |
| 1801 | 1943 | 12.8 | black | 2a-31 |
| 2879 | 1944 | 23.1 | black | 2a-93 |
| 1795 | 1961 | 15.5 | black | 2a-31 |
| 2807 | 1979 | 35.7 | black | 2a-94 |
| 3130 | 1985 | 17.0 | black | 2a-95 |
| 2896 | 1989 | 28.8 | black | 2a-96 |
| 2771 | 1996 | 23.9 | black | 2a-97 |
| 3048 | 1998 | 9.2 | black | refused: radius is the fit stroke or a numeral, not a filled marker |
| 3023 | 2024 | 22.0 | black | 2a-16 |
| 3136 | 2029 | 25.5 | black | 2a-11 |
| 2079 | 2062 | 19.0 | black | 2a-33 |
| 2048 | 2068 | 21.2 | black | 2a-32 |
| 3073 | 2077 | 23.0 | black | 2a-98 |
| 3375 | 2084 | 15.0 | black | 2a-103 |
| 1592 | 2089 | 9.2 | black | refused: y-axis stroke or tick; radius is not a filled marker |
| 1494 | 2092 | 8.1 | black | refused: y-axis stroke or tick; radius is not a filled marker |
| 3053 | 2102 | 8.0 | black | refused: radius is the fit stroke or a numeral, not a filled marker |
| 1538 | 2106 | 8.0 | black | refused: y-axis stroke or tick; radius is not a filled marker |
| 3203 | 2113 | 23.0 | black | 2a-17 |
| 3378 | 2116 | 15.3 | black | 2a-104 |
| 3372 | 2148 | 10.0 | black | 2a-27 |
| 3301 | 2168 | 24.0 | black | 2a-12 |
| 3579 | 2185 | 8.0 | black | 2a-30 |
| 1590 | 2192 | 8.0 | black | refused: y-axis stroke or tick; radius is not a filled marker |
| 2578 | 2200 | 8.1 | black | refused: radius is the fit stroke or a numeral, not a filled marker |
| 1590 | 2222 | 8.0 | black | refused: y-axis stroke or tick; radius is not a filled marker |
| 2238 | 2232 | 17.0 | black | 2a-34 |
| 3522 | 2246 | 9.0 | black | refused: radius is the fit stroke or a numeral, not a filled marker |
| 2565 | 2255 | 8.6 | black | refused: radius is the fit stroke or a numeral, not a filled marker |
| 2222 | 2258 | 18.9 | black | refused: same marker as 2a-34 |
| 2227 | 2267 | 18.7 | black | refused: same marker as 2a-34 |
| 3233 | 2267 | 23.8 | black | 2a-99 |
| 2344 | 2288 | 18.0 | black | 2a-35 |
| 2335 | 2303 | 17.0 | black | 2a-35 |
| 3422 | 2314 | 23.4 | black | 2a-100 |
| 2355 | 2339 | 14.2 | black | 2a-101 |
| 1596 | 2450 | 10.0 | black | refused: y-axis stroke or tick; radius is not a filled marker |
| 2598 | 2451 | 12.0 | black | 2a-37 |
| 1594 | 2460 | 8.0 | black | refused: y-axis stroke or tick; radius is not a filled marker |
| 2596 | 2463 | 14.0 | black | 2a-37 |
| 3978 | 2517 | 14.4 | black | 2a-102 |
| 2772 | 2555 | 19.0 | black | 2a-38 |
| 2997 | 2654 | 28.3 | black | 2a-40 |
| 3007 | 2732 | 14.0 | black | 2a-41 |
| 3132 | 2744 | 8.0 | black | refused: radius is the fit stroke or a numeral, not a filled marker |
| 2516 | 2748 | 8.0 | black | refused: stroke or axis ridge, span 36 px; radius is not a filled marker |
| 1904 | 2755 | 8.0 | black | refused: stroke or axis ridge, span 37 px; radius is not a filled marker |
| 1598 | 2759 | 8.0 | black | refused: stroke or axis ridge, span 43 px; radius is not a filled marker |
| 3130 | 2760 | 10.0 | black | refused: radius is the fit stroke or a numeral, not a filled marker |
| 4109 | 2776 | 10.3 | black | refused: radius is the fit stroke or a numeral, not a filled marker |
| 3747 | 2781 | 8.1 | black | refused: radius is the fit stroke or a numeral, not a filled marker |
| 4067 | 2782 | 8.0 | black | refused: stroke or axis ridge, span 32 px; radius is not a filled marker |
| 4124 | 2782 | 8.0 | black | refused: radius is the fit stroke or a numeral, not a filled marker |
| 3140 | 2784 | 14.0 | black | refused: x-axis junction, thick core 27 by 12 px; not a marker |
| 3571 | 2786 | 8.0 | black | refused: axis bar or tick; radius is not a filled marker |
| 3168 | 2790 | 8.0 | black | refused: axis bar or tick; radius is not a filled marker |
| 3190 | 2790 | 8.0 | black | refused: stroke or axis ridge, span 107 px; radius is not a filled marker |
| 3314 | 2790 | 8.0 | black | refused: axis bar or tick; radius is not a filled marker |
| 2516 | 2795 | 10.0 | black | refused: axis bar or tick; radius is not a filled marker |
| 2425 | 2797 | 8.0 | black | refused: stroke or axis ridge, span 81 px; radius is not a filled marker |
| 2526 | 2797 | 8.0 | black | refused: stroke or axis ridge, span 84 px; radius is not a filled marker |
| 2627 | 2797 | 8.0 | black | refused: axis bar or tick; radius is not a filled marker |
| 2149 | 2801 | 8.0 | black | refused: axis bar or tick; radius is not a filled marker |
| 2183 | 2801 | 8.0 | black | refused: stroke or axis ridge, span 76 px; radius is not a filled marker |
| 1904 | 2803 | 10.0 | black | refused: axis bar or tick; radius is not a filled marker |
| 1877 | 2805 | 8.0 | black | refused: axis bar or tick; radius is not a filled marker |
| 1914 | 2805 | 8.0 | black | refused: axis bar or tick; radius is not a filled marker |
| 1955 | 2805 | 8.0 | black | refused: stroke or axis ridge, span 39 px; radius is not a filled marker |
| 2015 | 2805 | 8.0 | black | refused: axis bar or tick; radius is not a filled marker |
| 2048 | 2805 | 8.0 | black | refused: axis bar or tick; radius is not a filled marker |
| 1616 | 2808 | 8.0 | black | refused: axis bar or tick; radius is not a filled marker |
| 1657 | 2808 | 8.0 | black | refused: axis bar or tick; radius is not a filled marker |
| 1522 | 2814 | 10.0 | black | refused: axis bar or tick; radius is not a filled marker |
| 1501 | 2849 | 8.0 | black | refused: y-axis stroke or tick; radius is not a filled marker |
| 2568 | 2902 | 8.0 | black | refused: x-axis numeral; radius is not a filled marker |
| 3262 | 2923 | 8.0 | black | refused: x-axis numeral; radius is not a filled marker |
| 3770 | 2925 | 8.1 | black | refused: x-axis numeral; radius is not a filled marker |
| 2038 | 2927 | 8.0 | black | refused: x-axis numeral; radius is not a filled marker |
| 3210 | 2927 | 8.0 | black | refused: x-axis numeral; radius is not a filled marker |
| 2426 | 2931 | 8.0 | black | refused: x-axis numeral; radius is not a filled marker |
| 3699 | 2931 | 8.0 | black | refused: x-axis numeral; radius is not a filled marker |
| 2556 | 2932 | 8.0 | black | refused: x-axis numeral; radius is not a filled marker |
| 3138 | 2932 | 8.6 | black | refused: x-axis numeral; radius is not a filled marker |
| 2479 | 2934 | 8.0 | black | refused: stroke or axis ridge, span 36 px; radius is not a filled marker |
| 3012 | 2934 | 8.0 | black | refused: x-axis numeral; radius is not a filled marker |
| 1925 | 2939 | 8.0 | black | refused: x-axis numeral; radius is not a filled marker |
| 2629 | 2939 | 8.1 | black | refused: x-axis numeral; radius is not a filled marker |
| 3068 | 2942 | 8.0 | black | refused: x-axis numeral; radius is not a filled marker |
| 1796 | 2946 | 8.0 | black | refused: x-axis numeral; radius is not a filled marker |
| 3132 | 2946 | 8.0 | black | refused: x-axis numeral; radius is not a filled marker |
| 1852 | 2949 | 8.0 | black | refused: x-axis numeral; radius is not a filled marker |
| 2423 | 2949 | 8.0 | black | refused: x-axis numeral; radius is not a filled marker |
| 3882 | 2956 | 10.0 | black | refused: x-axis numeral; radius is not a filled marker |
| 3766 | 2960 | 8.0 | black | refused: x-axis numeral; radius is not a filled marker |
| 2426 | 2964 | 8.0 | black | refused: x-axis numeral; radius is not a filled marker |
| 2676 | 2964 | 8.0 | black | refused: x-axis numeral; radius is not a filled marker |

Panel 2b: 129 cores. 26 match an existing row, 5 are new decision rows, 98 are refused.

| cx | cy | r px | colour | matched row |
| --- | --- | --- | --- | --- |
| 1605 | 3483 | 10.3 | black | refused: ink above the top labelled tick; radius is not a filled marker |
| 3023 | 3490 | 8.0 | black | refused: ink above the top labelled tick; radius is not a filled marker |
| 1602 | 3494 | 8.0 | black | refused: ink above the top labelled tick; radius is not a filled marker |
| 1602 | 3531 | 8.0 | black | refused: ink above the top labelled tick; radius is not a filled marker |
| 1606 | 3588 | 8.1 | black | refused: y-axis stroke or tick; radius is not a filled marker |
| 1627 | 3588 | 8.0 | black | refused: y-axis stroke or tick; radius is not a filled marker |
| 1608 | 3773 | 10.8 | black | refused: y-axis stroke or tick; radius is not a filled marker |
| 1605 | 3796 | 8.0 | black | refused: y-axis stroke or tick; radius is not a filled marker |
| 1605 | 3834 | 8.0 | black | refused: y-axis stroke or tick; radius is not a filled marker |
| 1717 | 3850 | 23.4 | black | 2b-80 |
| 1761 | 3850 | 24.0 | black | 2b-81 |
| 1605 | 3856 | 8.0 | black | refused: y-axis stroke or tick; radius is not a filled marker |
| 2241 | 3860 | 19.0 | black | 2b-00 |
| 2298 | 3894 | 14.6 | black | 2b-01 |
| 2513 | 4074 | 26.8 | black | 2b-02 |
| 2104 | 4130 | 24.2 | black | 2b-28 |
| 1609 | 4162 | 8.0 | black | refused: y-axis stroke or tick; radius is not a filled marker |
| 2633 | 4179 | 13.9 | black | 2b-03 |
| 1609 | 4184 | 8.0 | black | refused: y-axis stroke or tick; radius is not a filled marker |
| 2625 | 4189 | 15.3 | black | 2b-03 |
| 2673 | 4193 | 19.0 | black | 2b-04 |
| 1614 | 4196 | 8.5 | black | refused: y-axis stroke or tick; radius is not a filled marker |
| 2896 | 4386 | 15.0 | black | 2b-09 |
| 2961 | 4399 | 15.7 | black | 2b-11 |
| 2985 | 4402 | 15.6 | black | 2b-11 |
| 2616 | 4417 | 24.8 | black | 2b-31 |
| 1616 | 4444 | 10.3 | black | refused: y-axis stroke or tick; radius is not a filled marker |
| 1911 | 4449 | 8.5 | black | refused: radius is the fit stroke or a numeral, not a filled marker |
| 1613 | 4456 | 8.0 | black | refused: y-axis stroke or tick; radius is not a filled marker |
| 1613 | 4501 | 8.0 | black | refused: y-axis stroke or tick; radius is not a filled marker |
| 3542 | 4547 | 8.0 | black | refused: radius is the fit stroke or a numeral, not a filled marker |
| 3198 | 4599 | 15.0 | black | 2b-14 |
| 3283 | 4607 | 15.6 | black | 2b-16 |
| 2824 | 4641 | 23.8 | black | 2b-82 |
| 1970 | 4663 | 15.3 | black | 2b-41 |
| 1617 | 4744 | 8.0 | black | refused: y-axis stroke or tick; radius is not a filled marker |
| 3027 | 4746 | 24.7 | black | 2b-38 |
| 3188 | 4776 | 23.9 | black | 2b-24 |
| 1617 | 4785 | 8.0 | black | refused: y-axis stroke or tick; radius is not a filled marker |
| 1500 | 4787 | 10.3 | black | refused: y-axis stroke or tick; radius is not a filled marker |
| 1512 | 4791 | 8.1 | black | refused: y-axis stroke or tick; radius is not a filled marker |
| 1619 | 4797 | 9.8 | black | refused: y-axis stroke or tick; radius is not a filled marker |
| 1638 | 4797 | 8.0 | black | refused: y-axis stroke or tick; radius is not a filled marker |
| 1617 | 4807 | 8.0 | black | refused: stroke or axis ridge, span 40 px; radius is not a filled marker |
| 2177 | 4819 | 15.6 | black | 2b-43 |
| 3658 | 4831 | 15.5 | black | 2b-19 |
| 3728 | 4845 | 14.2 | black | 2b-21 |
| 3753 | 4916 | 19.0 | black | 2b-22 |
| 3705 | 4920 | 18.4 | black | 2b-20 |
| 2447 | 4943 | 14.9 | black | 2b-83 |
| 3229 | 4945 | 8.0 | black | refused: radius is the fit stroke or a numeral, not a filled marker |
| 3382 | 4961 | 8.0 | black | refused: radius is the fit stroke or a numeral, not a filled marker |
| 2426 | 4983 | 16.0 | black | 2b-47 |
| 3885 | 4988 | 26.6 | black | 2b-23 |
| 3362 | 4992 | 24.0 | black | 2b-84 |
| 3221 | 4998 | 8.0 | black | refused: radius is the fit stroke or a numeral, not a filled marker |
| 1620 | 5013 | 8.0 | black | refused: y-axis stroke or tick; radius is not a filled marker |
| 2508 | 5040 | 14.4 | black | 2b-48 |
| 2547 | 5060 | 17.0 | black | 2b-49 |
| 1620 | 5072 | 8.0 | black | refused: stroke or axis ridge, span 84 px; radius is not a filled marker |
| 2571 | 5073 | 8.0 | black | refused: radius is the fit stroke or a numeral, not a filled marker |
| 2693 | 5151 | 16.0 | black | 2b-50 |
| 1620 | 5173 | 8.0 | black | refused: y-axis stroke or tick; radius is not a filled marker |
| 2143 | 5181 | 8.0 | black | refused: radius is the fit stroke or a numeral, not a filled marker |
| 1620 | 5195 | 8.0 | black | refused: y-axis stroke or tick; radius is not a filled marker |
| 4150 | 5196 | 11.7 | black | refused: radius is the fit stroke or a numeral, not a filled marker |
| 4026 | 5200 | 8.0 | black | refused: radius is the fit stroke or a numeral, not a filled marker |
| 4079 | 5200 | 8.0 | black | refused: stroke or axis ridge, span 58 px; radius is not a filled marker |
| 4168 | 5200 | 8.0 | black | refused: stroke or axis ridge, span 43 px; radius is not a filled marker |
| 3699 | 5202 | 10.0 | black | refused: radius is the fit stroke or a numeral, not a filled marker |
| 3724 | 5204 | 8.0 | black | refused: stroke or axis ridge, span 151 px; radius is not a filled marker |
| 3911 | 5204 | 8.0 | black | refused: axis bar or tick; radius is not a filled marker |
| 3178 | 5207 | 9.0 | black | refused: axis bar or tick; radius is not a filled marker |
| 3284 | 5208 | 8.0 | black | refused: stroke or axis ridge, span 158 px; radius is not a filled marker |
| 3459 | 5208 | 8.0 | black | refused: axis bar or tick; radius is not a filled marker |
| 2763 | 5210 | 12.2 | black | refused: axis bar or tick; radius is not a filled marker |
| 2884 | 5211 | 8.0 | black | refused: stroke or axis ridge, span 226 px; radius is not a filled marker |
| 2659 | 5212 | 10.3 | black | refused: axis bar or tick; radius is not a filled marker |
| 2556 | 5215 | 8.0 | black | refused: stroke or axis ridge, span 92 px; radius is not a filled marker |
| 2671 | 5215 | 8.0 | black | refused: stroke or axis ridge, span 78 px; radius is not a filled marker |
| 2783 | 5215 | 8.0 | black | refused: axis bar or tick; radius is not a filled marker |
| 2847 | 5215 | 8.0 | black | refused: axis bar or tick; radius is not a filled marker |
| 2143 | 5217 | 10.0 | black | refused: axis bar or tick; radius is not a filled marker |
| 2190 | 5219 | 8.0 | black | refused: axis bar or tick; radius is not a filled marker |
| 2216 | 5219 | 8.0 | black | refused: axis bar or tick; radius is not a filled marker |
| 2239 | 5219 | 8.0 | black | refused: stroke or axis ridge, span 278 px; radius is not a filled marker |
| 1862 | 5223 | 8.0 | black | refused: axis bar or tick; radius is not a filled marker |
| 1903 | 5223 | 8.0 | black | refused: stroke or axis ridge, span 118 px; radius is not a filled marker |
| 2037 | 5223 | 8.0 | black | refused: stroke or axis ridge, span 77 px; radius is not a filled marker |
| 1624 | 5225 | 8.0 | black | refused: stroke or axis ridge, span 158 px; radius is not a filled marker |
| 1802 | 5226 | 8.0 | black | refused: axis bar or tick; radius is not a filled marker |
| 3705 | 5297 | 8.0 | black | refused: radius is the fit stroke or a numeral, not a filled marker |
| 3209 | 5301 | 8.0 | black | refused: radius is the fit stroke or a numeral, not a filled marker |
| 2683 | 5305 | 8.0 | black | refused: radius is the fit stroke or a numeral, not a filled marker |
| 2672 | 5306 | 9.0 | black | refused: radius is the fit stroke or a numeral, not a filled marker |
| 2175 | 5312 | 8.0 | black | refused: radius is the fit stroke or a numeral, not a filled marker |
| 2761 | 5316 | 8.1 | black | refused: radius is the fit stroke or a numeral, not a filled marker |
| 4255 | 5319 | 8.0 | black | refused: stroke or axis ridge, span 39 px; radius is not a filled marker |
| 1639 | 5320 | 8.0 | black | refused: y-axis stroke or tick; radius is not a filled marker |
| 2273 | 5322 | 8.0 | black | refused: radius is the fit stroke or a numeral, not a filled marker |
| 4307 | 5322 | 8.0 | black | refused: stroke or axis ridge, span 36 px; radius is not a filled marker |
| 2579 | 5326 | 8.0 | black | refused: radius is the fit stroke or a numeral, not a filled marker |
| 4183 | 5328 | 8.6 | black | refused: radius is the fit stroke or a numeral, not a filled marker |
| 2531 | 5330 | 8.0 | black | refused: radius is the fit stroke or a numeral, not a filled marker |
| 3198 | 5330 | 8.6 | black | refused: radius is the fit stroke or a numeral, not a filled marker |
| 3617 | 5330 | 8.0 | black | refused: radius is the fit stroke or a numeral, not a filled marker |
| 4113 | 5330 | 8.0 | black | refused: radius is the fit stroke or a numeral, not a filled marker |
| 2657 | 5334 | 8.0 | black | refused: radius is the fit stroke or a numeral, not a filled marker |
| 3068 | 5334 | 8.0 | black | refused: stroke or axis ridge, span 31 px; radius is not a filled marker |
| 3270 | 5334 | 8.0 | black | refused: radius is the fit stroke or a numeral, not a filled marker |
| 4057 | 5337 | 8.0 | black | refused: radius is the fit stroke or a numeral, not a filled marker |
| 3783 | 5340 | 10.0 | black | refused: radius is the fit stroke or a numeral, not a filled marker |
| 4177 | 5341 | 8.0 | black | refused: radius is the fit stroke or a numeral, not a filled marker |
| 2164 | 5342 | 8.0 | black | refused: radius is the fit stroke or a numeral, not a filled marker |
| 1500 | 5343 | 9.0 | black | refused: stroke or axis ridge, span 42 px; radius is not a filled marker |
| 2583 | 5345 | 8.0 | black | refused: radius is the fit stroke or a numeral, not a filled marker |
| 3266 | 5345 | 8.0 | black | refused: radius is the fit stroke or a numeral, not a filled marker |
| 1628 | 5346 | 8.0 | black | refused: y-axis stroke or tick; radius is not a filled marker |
| 2179 | 5346 | 8.0 | black | refused: radius is the fit stroke or a numeral, not a filled marker |
| 2683 | 5346 | 8.0 | black | refused: radius is the fit stroke or a numeral, not a filled marker |
| 1695 | 5348 | 8.0 | black | refused: stroke or axis ridge, span 32 px; radius is not a filled marker |
| 2527 | 5348 | 8.0 | black | refused: radius is the fit stroke or a numeral, not a filled marker |
| 3124 | 5352 | 8.0 | black | refused: radius is the fit stroke or a numeral, not a filled marker |
| 3318 | 5360 | 8.0 | black | refused: radius is the fit stroke or a numeral, not a filled marker |
| 3565 | 5360 | 8.0 | black | refused: radius is the fit stroke or a numeral, not a filled marker |
| 3617 | 5360 | 8.0 | black | refused: radius is the fit stroke or a numeral, not a filled marker |
| 2764 | 5375 | 9.2 | black | refused: radius is the fit stroke or a numeral, not a filled marker |
| 1500 | 5392 | 9.2 | black | refused: y-axis stroke or tick; radius is not a filled marker |
| 2242 | 5404 | 9.0 | black | refused: radius is the fit stroke or a numeral, not a filled marker |

Panel 2c: 162 cores. 31 match an existing row, 7 are new decision rows, 124 are refused.

| cx | cy | r px | colour | matched row |
| --- | --- | --- | --- | --- |
| 2893 | 6080 | 8.0 | black | refused: radius is the fit stroke or a numeral, not a filled marker |
| 3038 | 6087 | 8.0 | black | refused: radius is the fit stroke or a numeral, not a filled marker |
| 2196 | 6103 | 15.0 | black | 2c-01 |
| 2158 | 6110 | 22.0 | black | 2c-00 |
| 1761 | 6116 | 10.8 | black | refused: radius is the fit stroke or a numeral, not a filled marker |
| 1758 | 6128 | 8.0 | black | refused: stroke or axis ridge, span 118 px; radius is not a filled marker |
| 2168 | 6133 | 21.6 | black | 2c-00 |
| 1791 | 6256 | 8.0 | black | refused: radius is the fit stroke or a numeral, not a filled marker |
| 1764 | 6257 | 9.2 | black | refused: radius is the fit stroke or a numeral, not a filled marker |
| 1676 | 6278 | 8.0 | black | refused: y-axis stroke or tick; radius is not a filled marker |
| 2461 | 6290 | 15.3 | black | 2c-04 |
| 2391 | 6291 | 17.0 | black | 2c-03 |
| 2672 | 6387 | 15.5 | black | 2c-05 |
| 1762 | 6472 | 8.0 | black | refused: radius is the fit stroke or a numeral, not a filled marker |
| 1762 | 6516 | 8.0 | black | refused: radius is the fit stroke or a numeral, not a filled marker |
| 1762 | 6535 | 8.0 | black | refused: stroke or axis ridge, span 147 px; radius is not a filled marker |
| 2723 | 6539 | 18.0 | black | 2c-06 |
| 2383 | 6584 | 30.1 | black | 2c-44 |
| 2746 | 6635 | 14.0 | black | 2c-07 |
| 1650 | 6696 | 8.0 | black | refused: y-axis stroke or tick; radius is not a filled marker |
| 1682 | 6697 | 9.0 | black | refused: radius is the fit stroke or a numeral, not a filled marker |
| 2916 | 6703 | 15.0 | black | 2c-08 |
| 2986 | 6703 | 15.0 | black | 2c-11 |
| 2537 | 6718 | 24.5 | black | 2c-45 |
| 2540 | 6757 | 24.7 | black | 2c-80 |
| 1767 | 6780 | 9.0 | black | refused: radius is the fit stroke or a numeral, not a filled marker |
| 2580 | 6826 | 24.4 | black | 2c-81 |
| 2848 | 6909 | 24.2 | black | 2c-37 |
| 1766 | 6912 | 8.0 | black | refused: radius is the fit stroke or a numeral, not a filled marker |
| 2768 | 6948 | 24.7 | black | 2c-50 |
| 1771 | 6949 | 9.5 | black | refused: radius is the fit stroke or a numeral, not a filled marker |
| 1766 | 6960 | 8.0 | black | refused: radius is the fit stroke or a numeral, not a filled marker |
| 1766 | 6983 | 8.0 | black | refused: stroke or axis ridge, span 43 px; radius is not a filled marker |
| 2934 | 7002 | 24.2 | black | 2c-38 |
| 3338 | 7030 | 16.4 | black | 2c-82 |
| 3340 | 7059 | 15.8 | black | 2c-17 |
| 3371 | 7074 | 16.0 | black | 2c-18 |
| 1954 | 7118 | 18.4 | black | 2c-55 |
| 3320 | 7125 | 15.0 | black | 2c-83 |
| 3654 | 7125 | 8.0 | black | refused: radius is the fit stroke or a numeral, not a filled marker |
| 2028 | 7131 | 14.3 | black | 2c-57 |
| 3357 | 7133 | 15.0 | black | 2c-84 |
| 3475 | 7171 | 17.9 | black | 2c-20 |
| 3038 | 7173 | 28.0 | black | 2c-85 |
| 3147 | 7225 | 24.2 | black | 2c-86 |
| 2163 | 7262 | 17.0 | black | refused: same marker as 2c-59 |
| 2139 | 7266 | 19.7 | black | 2c-59 |
| 1770 | 7274 | 8.0 | black | refused: radius is the fit stroke or a numeral, not a filled marker |
| 1656 | 7309 | 9.0 | black | refused: y-axis stroke or tick; radius is not a filled marker |
| 1773 | 7315 | 10.3 | black | refused: radius is the fit stroke or a numeral, not a filled marker |
| 1783 | 7316 | 8.0 | black | refused: stroke or axis ridge, span 32 px; radius is not a filled marker |
| 1770 | 7326 | 8.0 | black | refused: radius is the fit stroke or a numeral, not a filled marker |
| 2321 | 7359 | 16.2 | black | 2c-61 |
| 2297 | 7374 | 10.0 | black | refused: radius is the fit stroke or a numeral, not a filled marker |
| 1770 | 7375 | 8.0 | black | refused: radius is the fit stroke or a numeral, not a filled marker |
| 3453 | 7375 | 8.0 | black | refused: radius is the fit stroke or a numeral, not a filled marker |
| 1770 | 7397 | 8.0 | black | refused: radius is the fit stroke or a numeral, not a filled marker |
| 3437 | 7408 | 8.1 | black | refused: radius is the fit stroke or a numeral, not a filled marker |
| 3445 | 7427 | 8.0 | black | refused: radius is the fit stroke or a numeral, not a filled marker |
| 3966 | 7502 | 14.9 | black | 2c-28 |
| 2459 | 7511 | 18.0 | black | 2c-63 |
| 2772 | 7540 | 8.1 | black | refused: radius is the fit stroke or a numeral, not a filled marker |
| 2766 | 7580 | 8.0 | black | refused: radius is the fit stroke or a numeral, not a filled marker |
| 2533 | 7582 | 9.2 | black | refused: radius is the fit stroke or a numeral, not a filled marker |
| 2725 | 7591 | 8.0 | black | refused: radius is the fit stroke or a numeral, not a filled marker |
| 2762 | 7592 | 8.9 | black | refused: radius is the fit stroke or a numeral, not a filled marker |
| 2524 | 7614 | 16.0 | black | 2c-65 |
| 1773 | 7639 | 8.0 | black | refused: stroke or axis ridge, span 62 px; radius is not a filled marker |
| 4177 | 7651 | 15.0 | black | 2c-34 |
| 4152 | 7696 | 10.0 | black | refused: radius is the fit stroke or a numeral, not a filled marker |
| 4159 | 7713 | 9.2 | black | 2c-32 |
| 2497 | 7714 | 8.0 | black | refused: stroke or axis ridge, span 33 px; radius is not a filled marker |
| 4154 | 7729 | 8.0 | black | 2c-32 |
| 2083 | 7740 | 8.0 | black | refused: radius is the fit stroke or a numeral, not a filled marker |
| 4188 | 7741 | 12.0 | black | 2c-35 |
| 4154 | 7743 | 10.0 | black | refused: radius is the fit stroke or a numeral, not a filled marker |
| 1773 | 7744 | 8.0 | black | refused: axis bar or tick; radius is not a filled marker |
| 4075 | 7745 | 8.0 | black | refused: stroke or axis ridge, span 69 px; radius is not a filled marker |
| 4164 | 7745 | 8.0 | black | refused: axis bar or tick; radius is not a filled marker |
| 4205 | 7745 | 8.0 | black | refused: stroke or axis ridge, span 126 px; radius is not a filled marker |
| 3736 | 7747 | 10.0 | black | refused: axis bar or tick; radius is not a filled marker |
| 3324 | 7749 | 10.0 | black | refused: axis bar or tick; radius is not a filled marker |
| 3601 | 7749 | 8.0 | black | refused: axis bar or tick; radius is not a filled marker |
| 3620 | 7749 | 8.0 | black | refused: axis bar or tick; radius is not a filled marker |
| 3664 | 7749 | 8.0 | black | refused: stroke or axis ridge, span 62 px; radius is not a filled marker |
| 3746 | 7749 | 8.0 | black | refused: stroke or axis ridge, span 73 px; radius is not a filled marker |
| 3836 | 7749 | 8.0 | black | refused: axis bar or tick; radius is not a filled marker |
| 3228 | 7752 | 8.0 | black | refused: stroke or axis ridge, span 84 px; radius is not a filled marker |
| 3336 | 7752 | 8.0 | black | refused: stroke or axis ridge, span 147 px; radius is not a filled marker |
| 2911 | 7754 | 9.2 | black | refused: axis bar or tick; radius is not a filled marker |
| 2735 | 7755 | 13.0 | black | refused: axis bar or tick; radius is not a filled marker |
| 2921 | 7756 | 8.0 | black | refused: stroke or axis ridge, span 215 px; radius is not a filled marker |
| 2497 | 7758 | 10.0 | black | refused: axis bar or tick; radius is not a filled marker |
| 2466 | 7760 | 8.0 | black | refused: axis bar or tick; radius is not a filled marker |
| 2507 | 7760 | 8.0 | black | refused: stroke or axis ridge, span 212 px; radius is not a filled marker |
| 2758 | 7760 | 8.0 | black | refused: axis bar or tick; radius is not a filled marker |
| 2814 | 7760 | 8.0 | black | refused: axis bar or tick; radius is not a filled marker |
| 2423 | 7761 | 9.0 | black | refused: stroke or axis ridge, span 36 px; radius is not a filled marker |
| 2083 | 7762 | 9.8 | black | refused: axis bar or tick; radius is not a filled marker |
| 1774 | 7764 | 8.6 | black | refused: axis bar or tick; radius is not a filled marker |
| 1843 | 7767 | 8.0 | black | refused: stroke or axis ridge, span 166 px; radius is not a filled marker |
| 2030 | 7767 | 8.0 | black | refused: axis bar or tick; radius is not a filled marker |
| 4247 | 7812 | 8.0 | black | refused: x-axis numeral; radius is not a filled marker |
| 4266 | 7822 | 8.0 | black | refused: x-axis numeral; radius is not a filled marker |
| 3358 | 7823 | 8.0 | black | refused: x-axis numeral; radius is not a filled marker |
| 2912 | 7827 | 8.0 | black | refused: x-axis numeral; radius is not a filled marker |
| 2507 | 7831 | 8.0 | black | refused: x-axis numeral; radius is not a filled marker |
| 4270 | 7833 | 8.0 | black | refused: x-axis numeral; radius is not a filled marker |
| 2104 | 7834 | 8.0 | black | refused: x-axis numeral; radius is not a filled marker |
| 2187 | 7834 | 8.0 | black | refused: x-axis numeral; radius is not a filled marker |
| 3800 | 7837 | 8.0 | black | refused: x-axis numeral; radius is not a filled marker |
| 3848 | 7837 | 8.0 | black | refused: x-axis numeral; radius is not a filled marker |
| 2600 | 7838 | 10.0 | black | 2c-66 |
| 2202 | 7845 | 8.0 | black | refused: x-axis numeral; radius is not a filled marker |
| 2602 | 7848 | 8.0 | black | 2c-66 |
| 3221 | 7848 | 8.0 | black | refused: x-axis numeral; radius is not a filled marker |
| 3348 | 7848 | 8.1 | black | refused: x-axis numeral; radius is not a filled marker |
| 4152 | 7849 | 10.0 | black | refused: x-axis numeral; radius is not a filled marker |
| 2773 | 7852 | 8.0 | black | refused: x-axis numeral; radius is not a filled marker |
| 4027 | 7852 | 8.0 | black | refused: x-axis numeral; radius is not a filled marker |
| 3358 | 7853 | 8.0 | black | refused: x-axis numeral; radius is not a filled marker |
| 2419 | 7856 | 8.0 | black | refused: x-axis numeral; radius is not a filled marker |
| 3602 | 7856 | 8.0 | black | refused: x-axis numeral; radius is not a filled marker |
| 3852 | 7856 | 8.0 | black | refused: x-axis numeral; radius is not a filled marker |
| 2204 | 7857 | 10.0 | black | refused: x-axis numeral; radius is not a filled marker |
| 2494 | 7857 | 8.0 | black | refused: x-axis numeral; radius is not a filled marker |
| 2907 | 7857 | 8.0 | black | refused: x-axis numeral; radius is not a filled marker |
| 3726 | 7857 | 8.1 | black | refused: x-axis numeral; radius is not a filled marker |
| 1967 | 7860 | 8.0 | black | refused: x-axis numeral; radius is not a filled marker |
| 2826 | 7860 | 8.0 | black | refused: stroke or axis ridge, span 31 px; radius is not a filled marker |
| 4083 | 7860 | 8.0 | black | refused: x-axis numeral; radius is not a filled marker |
| 4147 | 7860 | 8.0 | black | refused: x-axis numeral; radius is not a filled marker |
| 2507 | 7862 | 9.0 | black | refused: x-axis numeral; radius is not a filled marker |
| 2091 | 7863 | 8.1 | black | refused: x-axis numeral; radius is not a filled marker |
| 2367 | 7863 | 8.0 | black | refused: x-axis numeral; radius is not a filled marker |
| 3796 | 7863 | 8.0 | black | refused: x-axis numeral; radius is not a filled marker |
| 2972 | 7864 | 8.1 | black | refused: x-axis numeral; radius is not a filled marker |
| 3443 | 7865 | 9.0 | black | refused: x-axis numeral; radius is not a filled marker |
| 2770 | 7867 | 8.0 | black | refused: x-axis numeral; radius is not a filled marker |
| 3721 | 7867 | 8.0 | black | refused: x-axis numeral; radius is not a filled marker |
| 2104 | 7868 | 8.0 | black | refused: x-axis numeral; radius is not a filled marker |
| 2519 | 7868 | 8.0 | black | refused: x-axis numeral; radius is not a filled marker |
| 3220 | 7870 | 9.0 | black | refused: x-axis numeral; radius is not a filled marker |
| 2423 | 7871 | 8.0 | black | refused: x-axis numeral; radius is not a filled marker |
| 4199 | 7871 | 8.0 | black | refused: x-axis numeral; radius is not a filled marker |
| 2117 | 7873 | 8.2 | black | refused: x-axis numeral; radius is not a filled marker |
| 1964 | 7874 | 8.0 | black | refused: x-axis numeral; radius is not a filled marker |
| 2020 | 7874 | 8.0 | black | refused: x-axis numeral; radius is not a filled marker |
| 2531 | 7882 | 8.0 | black | refused: x-axis numeral; radius is not a filled marker |
| 3020 | 7882 | 8.0 | black | refused: x-axis numeral; radius is not a filled marker |
| 3774 | 7882 | 8.0 | black | refused: x-axis numeral; radius is not a filled marker |
| 2938 | 7889 | 8.0 | black | refused: x-axis numeral; radius is not a filled marker |
| 2561 | 7897 | 8.1 | black | refused: x-axis numeral; radius is not a filled marker |
| 2773 | 7897 | 8.0 | black | refused: x-axis numeral; radius is not a filled marker |
| 3419 | 7897 | 8.0 | black | refused: x-axis numeral; radius is not a filled marker |
| 2602 | 7898 | 11.3 | black | refused: x-axis numeral; radius is not a filled marker |
| 2419 | 7901 | 8.0 | black | refused: x-axis numeral; radius is not a filled marker |
| 2822 | 7901 | 8.0 | black | refused: x-axis numeral; radius is not a filled marker |
| 1967 | 7904 | 8.0 | black | refused: x-axis numeral; radius is not a filled marker |
| 2016 | 7908 | 8.0 | black | refused: x-axis numeral; radius is not a filled marker |
| 2602 | 7911 | 8.0 | black | refused: x-axis numeral; radius is not a filled marker |
| 2169 | 7927 | 8.1 | black | refused: x-axis numeral; radius is not a filled marker |

### Fig. 4a, (SiO), p_ef/p_eq versus 1000 f

Y calibration, labelled ticks only: glyph 1.0 at the tick stroke y = 2095.0, glyph 0.6 at y = 3156.5, glyph 0 at y = 4757.0. ratio = 1.786382 + (-3.755954e-04) y, residuals [0.00049, -0.000815, 0.000325], rms 0.00058. Equal-space strokes checked and not used as calibration points: [(2624.0, 0.8, 0.8008), (3693.5, 0.4, 0.3991), (4231.0, 0.2, 0.1972)]. X calibration, labelled ticks only: glyph 1 at tick stroke x = 3347, glyph 10 at tick stroke x = 5208. log10(1000 f) = (x − 3347) / 1864. The two-point residual is 0 by construction. Unlabelled minor ticks then fall on the log decades with median absolute residual 1.9 px and max 4.3 px. Those minors are not calibration points. About 2667 px per unit of the ratio. Read uncertainty for an isolated symbol is ±0.02 in the ratio (half-height about 25 px plus the 0.002 tick residual). A taller open diamond is ±0.03. 1000 f of a column is the labelled-tick prediction; each component centre is within 8 px of that prediction, which is 0.004 in log10(1000 f).

Legend, top to bottom, this work: 11 filled diamond at 1700 K, 12 open diamond at 1750 K, 13 diamond with a plus at 1800 K, 14 asterisk at 1850 K, 15 open diamond at 1900 K, 16 diamond with a vertical bar at 1950 K. Centres are connected-component centroids of pixels darker than 100, in a ±42 px window on the predicted column, not a vertical-projection peak (those peaks hit diamond edges). The open cell, 1000 f = 375, would sit at x ≈ 8145, past the last tick near x = 6985. No open-cell marker is on this panel.

| 4a-f1.12-s11-s16 | defer | series 11, 12, 13, 14, 15, 16 | shared window y 2109-2435 | ratio ends 0.994 to 0.872 | cx 3431.5 | 1000f measured 1.110 (nominal 1.12) | 1200 dpi |

| 4a-f2.39-s11 | read | series 11 | 0.956 ± 0.02 | 1000f measured 2.396 (nominal 2.39) | cx 4053.2 cy 2210.4 | y 2168-2252 | 1200 dpi |

| 4a-f2.39-s12 | read | series 12 | 0.919 ± 0.03 | 1000f measured 2.394 (nominal 2.39) | cx 4052.6 cy 2310.2 | y 2254-2376 | 1200 dpi |

| 4a-f2.39-s13-s16 | defer | series 13, 14, 15, 16 | shared window y 2389-2760 | ratio ends 0.889 to 0.750 | cx 4053.4 | 1000f measured 2.397 (nominal 2.39) | 1200 dpi |

| 4a-f3.35-s11 | read | series 11 | 0.944 ± 0.02 | 1000f measured 3.340 (nominal 3.35) | cx 4321.7 cy 2243.7 | y 2198-2290 | 1200 dpi |

| 4a-f3.35-s12 | read | series 12 | 0.895 ± 0.02 | 1000f measured 3.339 (nominal 3.35) | cx 4321.4 cy 2372.9 | y 2329-2420 | 1200 dpi |

| 4a-f3.35-s13 | read | series 13 | 0.847 ± 0.02 | 1000f measured 3.338 (nominal 3.35) | cx 4321.2 cy 2500.7 | y 2456-2547 | 1200 dpi |

| 4a-f3.35-s14 | read | series 14 | 0.805 ± 0.02 | 1000f measured 3.345 (nominal 3.35) | cx 4322.8 cy 2614.2 | y 2568-2659 | 1200 dpi |

| 4a-f3.35-s15-s16 | defer | series 15, 16 | shared window y 2672-2857 | ratio ends 0.783 to 0.713 | cx 4321.4 | 1000f measured 3.339 (nominal 3.35) | 1200 dpi |

### Fig. 4b and Fig. 4c

Panel 4b, (SiO2). Y calibration, labelled ticks only: 0.6 at y = 5708.5, 0.2 at y = 6417.5, 0 at y = 6770.0. ratio = 3.825947 + (-5.650847e-04) y, residuals [-0.000161, 0.000484, -0.000323], rms 0.00035. The 0.4 stroke at y = 6059.5 predicts 0.4018 and was not a calibration point. X calibration, labelled ticks 1, 2, 3 at x = 2785.5, 3506.0, 4228.0. 1000 f = -2.861697 + (1.386481e-03) x, residuals [-0.000347, 0.000693, -0.000346], rms 0.000490. The y-axis is not a labelled x tick.

Panel 4c, (Si2O2). Y calibration, labelled ticks only: 0.6 at y = 5805.5 and 0 at y = 6755.0. ratio = 4.268562 + (-6.319115e-04) y, residuals [-0.0, -0.0]. Check strokes, not calibration points: [(6124.5, 0.4, 0.3984), (6443.5, 0.2, 0.1968)]. X calibration, labelled ticks 1, 2, 3 at x = 5745.0, 6391.5, 7038.0. 1000 f = -7.886311 + (1.546790e-03) x, residuals [-0.0, -0.0, -0.0].

Each pile is one ink cluster: an asterisk, an open diamond, and a barred diamond drawn on top of each other (series 14, 15, 16). The three centres are not resolved. Each series is deferred to that shared window. The pile-centre ratio is the locator of the shared ink, ±0.03, and it is not a read of one series. Open-cell f = 0.375 is off both x axes.

Panel 4c has no separable mark at the nominal 1000 f = 1.12 column (x = 5823, max distance-transform radius 6.3 px, which is the trend line). The only left pile is the one below, and its measured 1000 f does not land on 1.12.

| id | decision | series | T caption | pile centre ratio ±0.03 | measured 1000f | nominal 1000f | cx | cy | dt | thick window | dpi |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 4b-left-s14 | defer | 14 | 1850 | 0.617 | 1.112 | 1.12 | 2866 | 5679 | 19.1 | x 2822-2896, y 5625-5691 | 1200 |
| 4b-left-s15 | defer | 15 | 1900 | 0.617 | 1.112 | 1.12 | 2866 | 5679 | 19.1 | x 2822-2896, y 5625-5691 | 1200 |
| 4b-left-s16 | defer | 16 | 1950 | 0.617 | 1.112 | 1.12 | 2866 | 5679 | 19.1 | x 2822-2896, y 5625-5691 | 1200 |
| 4b-mid-s14 | defer | 14 | 1850 | 0.245 | 2.385 | 2.39 | 3784 | 6337 | 17.7 | x 3755-3818, y 6298-6351 | 1200 |
| 4b-mid-s15 | defer | 15 | 1900 | 0.245 | 2.385 | 2.39 | 3784 | 6337 | 17.7 | x 3755-3818, y 6298-6351 | 1200 |
| 4b-mid-s16 | defer | 16 | 1950 | 0.245 | 2.385 | 2.39 | 3784 | 6337 | 17.7 | x 3755-3818, y 6298-6351 | 1200 |
| 4b-right-s14 | defer | 14 | 1850 | 0.214 | 3.311 | 3.35 | 4452 | 6391 | 15.2 | x 4419-4477, y 6377-6418 | 1200 |
| 4b-right-s15 | defer | 15 | 1900 | 0.214 | 3.311 | 3.35 | 4452 | 6391 | 15.2 | x 4419-4477, y 6377-6418 | 1200 |
| 4b-right-s16 | defer | 16 | 1950 | 0.214 | 3.311 | 3.35 | 4452 | 6391 | 15.2 | x 4419-4477, y 6377-6418 | 1200 |

| id | decision | series | T caption | pile centre ratio ±0.03 | measured 1000f | nominal 1000f | cx | cy | dt | thick window | dpi |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 4c-left-s14 | defer | 14 | 1850 | 0.470 | 1.306 | 1.12 | 5943 | 6011 | 17.7 | x 5915-5980, y 5971-6023 | 1200 |
| 4c-left-s15 | defer | 15 | 1900 | 0.470 | 1.306 | 1.12 | 5943 | 6011 | 17.7 | x 5915-5980, y 5971-6023 | 1200 |
| 4c-left-s16 | defer | 16 | 1950 | 0.470 | 1.306 | 1.12 | 5943 | 6011 | 17.7 | x 5915-5980, y 5971-6023 | 1200 |
| 4c-mid-s14 | defer | 14 | 1850 | 0.130 | 2.397 | 2.39 | 6648 | 6550 | 19.1 | x 6612-6684, y 6502-6562 | 1200 |
| 4c-mid-s15 | defer | 15 | 1900 | 0.130 | 2.397 | 2.39 | 6648 | 6550 | 19.1 | x 6612-6684, y 6502-6562 | 1200 |
| 4c-mid-s16 | defer | 16 | 1950 | 0.130 | 2.397 | 2.39 | 6648 | 6550 | 19.1 | x 6612-6684, y 6502-6562 | 1200 |
| 4c-right-s14 | defer | 14 | 1850 | 0.119 | 3.359 | 3.35 | 7270 | 6567 | 19.2 | x 7235-7308, y 6500-6578 | 1200 |
| 4c-right-s15 | defer | 15 | 1900 | 0.119 | 3.359 | 3.35 | 7270 | 6567 | 19.2 | x 7235-7308, y 6500-6578 | 1200 |
| 4c-right-s16 | defer | 16 | 1950 | 0.119 | 3.359 | 3.35 | 7270 | 6567 | 19.2 | x 7235-7308, y 6500-6578 | 1200 |

### Series left as one refusal

| figure | decision | evidence |
| --- | --- | --- |
| 1, series 1–6 | refused | Straight lines, no markers. Series 6 is the Table 1 this-work fit. Series 1–5 cite [29], [15], [16], and [6]. |
| 3 | refused | Silver calibration against Paule and Mandel. Not an oxide measurement. |
| 4a series 1–10 | refused | Effusion results of [15] and [16], as the caption states. Not reopened. Asterisks past the labelled 10 are in this refusal; they are not the this-work columns. |
| 5, series 1–5 | refused | Equation (8) applied to Nagai et al. [16]. Not this work. |

Fig. 2's MinerU caption prints series 4 as f = 3.75×10^{-3}. The table cell and equation (11), with the printed open-cell Clausing coefficient 0.375, give 3.75×10^{-1}. The caption exponent was not stored.

## Render checks

pdftoppm -r 600 of PDF pages 3, 4, 7, and 8. Running headers read 189, 190, 193, and 194, so published page = 186 + PDF page. The journal line on PDF page 1 reads pp. 187–196.

pdftoppm -r 1200 of PDF pages 5 and 6, the Fig. 2 and Fig. 4 crops named above. Tick glyphs listed in the calibration section were read from those crops. Marker centres were re-measured on the same renders: Fig. 2 by distance-transform radius, Fig. 4a by connected-component centroid, Fig. 4b and Fig. 4c by the distance-transform core of each pile.

Tesseract of the 600 dpi table bands agrees with MinerU on every this-work digit that tesseract did not fuse. Fused cells, kept as MinerU and not edited: Table 3 SiO 1800 K, OCR “2243” and “4,626”, MinerU 22±3 and 4.626±0.008; Table 3 SiO 1850 K, OCR “28244”, MinerU 28±4. Table 3 SiO 1700 K, OCR “§+3”, MinerU 8±3. Tesseract drops the minus in 10^{-n} and garbles species glyphs. Species labels and exponents are the MinerU text.

## What was not stored

Literature rows of Tables 1, 3, and 4. Empty equation cells. Scored point pressures. Fig. 2 and Fig. 4 values above are context, not partial_pressure or alpha observations. Equilibrium pressures computed from 10^{b(i)}. A parent link from a recommended alpha to the equation columns or to Table 3. Fig. 1, Fig. 3, and Fig. 5 coordinates. Fig. 4a series 1–10. Any marker on the open-cell position of Fig. 4, which is off the frame.

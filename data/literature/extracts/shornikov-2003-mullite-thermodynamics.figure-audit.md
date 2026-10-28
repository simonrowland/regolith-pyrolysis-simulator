# Figure 1 digitization audit — shornikov-2003-mullite-thermodynamics

PDF page 3 (published p. 1046), rendered at 600 dpi. The figure crop is 2400 × 1900 pixels with its top-left at page pixel (250, 250). Coordinates below include crop-local and full-page positions.

Both axes are linear. Each marker was read twice from fresh crops: a 120 × 120 source-pixel crop enlarged 5×, then an independent 200 × 200 crop enlarged 3×. The symbol center was estimated separately in each crop. The stored `delta_fG` is the negative of the plotted `−ΔfG_T(M)` ordinate. Values are rounded to two significant figures; the conservative read uncertainty is ±0.05 kJ/mol, based on the marker-center width.

## Calibration

| Axis | Label | Crop pixel | Full-page pixel |
|---|---:|---:|---:|
| T (K) | 1200 | 602 | 852 |
| T (K) | 1400 | 895 | 1145 |
| T (K) | 1600 | 1190 | 1440 |
| T (K) | 1800 | 1483 | 1733 |
| T (K) | 2000 | 1777 | 2027 |
| T (K) | 2200 | 2070 | 2320 |
| −ΔfG_T(M) (kJ/mol) | 2 | 268 | 518 |
| −ΔfG_T(M) (kJ/mol) | 4 | 487 | 737 |
| −ΔfG_T(M) (kJ/mol) | 6 | 701 | 951 |
| −ΔfG_T(M) (kJ/mol) | 8 | 915 | 1165 |
| −ΔfG_T(M) (kJ/mol) | 10 | 1129 | 1379 |
| −ΔfG_T(M) (kJ/mol) | 12 | 1345 | 1595 |

Least-squares linear maps from the six labelled ticks on each axis:

- T_K = 790.0710 + 0.681000 × x_crop_px
- −ΔfG_T(M) = −0.511535 + 0.00930221 × y_crop_px

## Marker reads

| Series | Symbol | Read | Source crop | Crop pixel (x, y) | Page pixel (x, y) | T read (K) | −ΔfG read (kJ/mol) | Stored ΔfG (kJ/mol) | Attribution |
|---:|---|---:|---:|---:|---:|---:|---:|---:|---|
| 1 | square | 1 | 120 × 120 | (412, 253) | (662, 503) | 1070.64 | 1.842 | -1.8 | High-temperature calorimetry [69]. |
| 1 | square | 2 | 200 × 200 | (411, 254) | (661, 504) | 1069.96 | 1.851 | -1.8 | High-temperature calorimetry [69]. |
| 4 | filled circle | 1 | 120 × 120 | (1520, 590) | (1770, 840) | 1825.19 | 4.977 | -5.0 | Heterogeneous equilibria in slags [7, 10, 44], series 2–4. |
| 4 | filled circle | 2 | 200 × 200 | (1520, 593) | (1770, 843) | 1825.19 | 5.005 | -5.0 | Heterogeneous equilibria in slags [7, 10, 44], series 2–4. |
| 6 | filled triangle | 1 | 120 × 120 | (1590, 1126) | (1840, 1376) | 1872.86 | 9.963 | -1.0e+01 | Knudsen mass spectrometry in [29, 32, 59, 60] and this work, series 5–9. |
| 6 | filled triangle | 2 | 200 × 200 | (1590, 1125) | (1840, 1375) | 1872.86 | 9.953 | -1.0e+01 | Knudsen mass spectrometry in [29, 32, 59, 60] and this work, series 5–9. |
| 8 | filled inverted triangle | 1 | 120 × 120 | (2001, 589) | (2251, 839) | 2152.75 | 4.967 | -5.0 | Knudsen mass spectrometry in [29, 32, 59, 60] and this work, series 5–9. |
| 8 | filled inverted triangle | 2 | 200 × 200 | (2001, 590) | (2251, 840) | 2152.75 | 4.977 | -5.0 | Knudsen mass spectrometry in [29, 32, 59, 60] and this work, series 5–9. |
| 17 | filled diamond | 1 | 120 × 120 | (1960, 708) | (2210, 958) | 2124.83 | 6.074 | -6.1 | Redlich–Kister equation [49]. |
| 17 | filled diamond | 2 | 200 × 200 | (1960, 708) | (2210, 958) | 2124.83 | 6.074 | -6.1 | Redlich–Kister equation [49]. |
| 18 | square with cross | 1 | 120 × 120 | (1591, 633) | (1841, 883) | 1873.54 | 5.377 | -5.4 | Monte Carlo method [68]. |
| 18 | square with cross | 2 | 200 × 200 | (1591, 633) | (1841, 883) | 1873.54 | 5.377 | -5.4 | Monte Carlo method [68]. |
| 19 | circle with cross | 1 | 120 × 120 | (1592, 789) | (1842, 1039) | 1874.22 | 6.828 | -6.8 | Kapur–Froberg equation [24]. |
| 19 | circle with cross | 2 | 200 × 200 | (1592, 787) | (1842, 1037) | 1874.22 | 6.809 | -6.8 | Kapur–Froberg equation [24]. |
| 20 | open triangle | 1 | 120 × 120 | (1517, 567) | (1767, 817) | 1823.15 | 4.763 | -4.8 | Lattice model [26]. |
| 20 | open triangle | 2 | 200 × 200 | (1518, 570) | (1768, 820) | 1823.83 | 4.791 | -4.8 | Lattice model [26]. |
| 21 | crossed open triangle | 1 | 120 × 120 | (1329, 586) | (1579, 836) | 1695.12 | 4.940 | -4.9 | Data on the phase diagram [63]. |
| 21 | crossed open triangle | 2 | 200 × 200 | (1330, 586) | (1580, 836) | 1695.80 | 4.940 | -4.9 | Data on the phase diagram [63]. |
| 23 | crossed diamond | 1 | 120 × 120 | (1446, 604) | (1696, 854) | 1774.80 | 5.107 | -5.1 | Estimated values [67]. |
| 23 | crossed diamond | 2 | 200 × 200 | (1446, 601) | (1696, 851) | 1774.80 | 5.079 | -5.1 | Estimated values [67]. |

The reads agree within 0.04 kJ/mol on the plotted ordinate. Figure 1 has no logarithmic axis.

## Unmarked curves

Series 2–3, 5, 7, 9–16, and 22 are smooth unmarked lines. They are recorded as unavailable Figure 1 observations with quantity `delta_fG` and the panel temperature span 1000–2200 K. No curve points were sampled.

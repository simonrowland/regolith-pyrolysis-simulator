# Shornikov 2003 SiO2–CaTiO3 melts — extraction audit

S. Shornikov and I. Archakov. The PDF title is “MASS SPECTROMETRIC STUDY OF THERMODYNAMIC PROPERTIES OF THE SiO2-CaTiO3 MELTS”. Printed pages 112–116. pdfinfo Title is “Microsoft Word - 01 CTSe.doc”. The PDF does not name the proceedings. Shornikova 2019, reference [47], identifies the proceedings (ISUCST, Ivanovo, 2003, p. 112).

## Duplicate verdict

Not a duplicate. No extract on this base cites this title or this symposium. It is not the 2003 mullite paper (`shornikov-2003-mullite-thermodynamics`).

## What was admitted

36 Fig. 1 activities at 2100 K, `method_class: figure_only`. CaO 8 filled circles, TiO2 7 up-triangles, SiO2 6 down-triangles, CaTiO3 8 open squares, CaTiSiO5 7 open circles. The stored number is log10 a from the tick calibration. `activity` is 10 to that power, six decimal places. Composition is the digitised mol% SiO2 with the balance CaTiO3.

8 Fig. 2 series-3 readings of Δf G_T/RT at 2100 K. These stay in context. The axis is dimensionless, the caption does not name the formation reaction, and Quantity has no Δf G/RT token. They are not stored as delta_fG.

Refused, one marker at a time:

- TiO2 and SiO2 at about 40 mol% SiO2. Sol upholds the refusal. The two markers occupy one filled core at page pixel (2628.7, 1054.7). Neither centre can be assigned, so each species is its own refused row.
- CaTiSiO5 at about 20 mol% SiO2 is stored. The visible arc is 261 degrees on the series-5 inner radius of 21 px, the radius of the clean circle at (2905, 956). The centre is (2084.0, 937.0), log10 a = −0.215, 20.37 mol% SiO2. The series-4 square at (2074.0, 980.6) hides the lower arc, so the uncertainty stays ±0.03 and ±0.4 mol%.
- Fig. 2 series 1 (filled circles, SiO2–MgAl2O4, 2050 K, reference [13]) and series 2 (up-triangles, SiO2–CaAl2O4, 1933 K, reference [14]). Other papers.
- The three symbols stacked near 71 mol% SiO2 are the Fig. 2 legend, not data.

x*(SiO2) = 10.94±0.18 mol% at 2100 K stays a composition anchor, not an activity. Partial pressures are not tabulated.

## Standard state

SiO2, equation (5): the integral starts at x(SiO2)=1. The cell samples stop at 60 mol% SiO2. The paper does not name solid or liquid. The prose ends “The phase is not stated.” Expected migration phase: unknown.

CaO, equation (6), and TiO2, equation (7): the quote is “a*(i) is the activity of the i component of the CaTiO3 compound crystallized from the SiO2-CaTiO3 melt at the concentration equal to x*(i).” That is a compound at x*. The prose ends “The phase is not stated.” Expected migration phase: unknown. The pure-oxide-at-T ruling does not apply.

CaTiO3 and CaTiSiO5: the paper does not give a reference-state sentence. The phase is not stated. Unknown.

Fig. 2 caption does not state the formation reaction. The phase is not stated. Unknown.

## Figure 1

600 dpi PDF page 4 (published p. 115), 4959×7017. Tick centres:

- log10 a(i) = 0 at y = 852.0
- −1 at y = 1246.5
- −2 at y = 1641.0
- −3 at y = 2035.5
- −4 at y = 2430.0

The four intervals are 394.5 px. Residuals on those five ticks are 0.

x ticks: 0 mol% at x = 1519.5, 80 mol% at x = 3737.0. Scale (3737.0 − 1519.5) / 80 = 27.71875 px per mol% = 277.1875 px per 10 mol%. Minor-tick residuals are at most 0.6 px.

Filled-marker centres are the connected-component cores after an r=8 dark-neighborhood test. Open-symbol centres are the white holes; the stroke peaks near 23 px. Every stored centre was drawn back onto the page. Isolated markers are ±0.02 in log10 a and ±0.4 mol%. The y-axis crosses the CaO and TiO2 markers at x≈0, so those x uncertainties are ±0.6 mol% and the TiO2 log uncertainty is ±0.03. The corner CaTiO3 square is bisected by both axes: the stored composition is x=0, the pixel centre is −0.13 mol%, and the uncertainty is ±0.06 in log10 a and ±0.8 mol%. The x=10 CaTiO3 square is bisected by the log10 a = 0 frame (±0.06). The SiO2 / CaTiSiO5 pair at 50 mol% touch (±0.05). The TiO2 triangle and CaTiO3 square at 20 mol% are about 43 px apart and were both read (±0.03 for the triangle).

## Figure 2

Same page. y ticks: −1 at y=3673.0, −1.5 at y=3992.0, −2.5 at y=4630.5, −3 at y=4950.5, and the x-axis at y=5269.5 is −3.5. Scale 639 px per unit. Residuals at most 1 px. The −2 tick is under the series 2 marker and was not used to fit the scale. x: 0 mol% at x=1574.5, 80 mol% at x=3792.5, 27.725 px per mol%.

Series 3 down-triangle cores, Δf G_T/RT ±0.02 (±0.03 at x=0, where the axis crosses the marker):

| x(SiO2) mol% | Δf G_T/RT |
| --- | --- |
| 0.36 | −2.543 |
| 10.00 | −2.901 |
| 20.06 | −3.130 |
| 29.97 | −3.133 |
| 33.33 | −3.085 |
| 40.00 | −2.932 |
| 50.00 | −2.580 |
| 60.02 | −2.105 |

## This-work markers

A marker is a centre, or an assignable exposed arc or tip, inside a stated uncertainty. One row per this-work marker, including the refused pair.

| series | species | x | y | value | decision |
| --- | --- | --- | --- | --- | --- |
| /4 open square | CaTiO3 | 1516.0 | 852.0 | activity=1.000000 | read |
| /4 open square | CaTiO3 | 1796.0 | 850.0 | activity=1.011579 | read |
| /4 open square | CaTiO3 | 2074.0 | 980.6 | activity=0.472063 | read |
| /4 open square | CaTiO3 | 2350.0 | 1163.5 | activity=0.162181 | read |
| /4 open square | CaTiO3 | 2444.0 | 1231.9 | activity=0.108893 | read |
| /4 open square | CaTiO3 | 2632.0 | 1368.0 | activity=0.049204 | read |
| /4 open square | CaTiO3 | 2905.0 | 1590.5 | activity=0.013428 | read |
| /4 open square | CaTiO3 | 3183.0 | 1782.6 | activity=0.004375 | read |
| /3 filled down-triangle | CaTiO3 | 1584.4 | 4658.7 | quantity_as_printed=Delta_f G_T / RT; axis_delta_fG_over_RT=-2.543; x_SiO2_mol_pct=0.36 | read |
| /3 filled down-triangle | CaTiO3 | 1851.8 | 4887.5 | quantity_as_printed=Delta_f G_T / RT; axis_delta_fG_over_RT=-2.901; x_SiO2_mol_pct=10.00 | read |
| /3 filled down-triangle | CaTiO3 | 2130.8 | 5034.3 | quantity_as_printed=Delta_f G_T / RT; axis_delta_fG_over_RT=-3.130; x_SiO2_mol_pct=20.06 | read |
| /3 filled down-triangle | CaTiO3 | 2405.3 | 5035.9 | quantity_as_printed=Delta_f G_T / RT; axis_delta_fG_over_RT=-3.133; x_SiO2_mol_pct=29.97 | read |
| /3 filled down-triangle | CaTiO3 | 2498.5 | 5005.2 | quantity_as_printed=Delta_f G_T / RT; axis_delta_fG_over_RT=-3.085; x_SiO2_mol_pct=33.33 | read |
| /3 filled down-triangle | CaTiO3 | 2683.6 | 4907.5 | quantity_as_printed=Delta_f G_T / RT; axis_delta_fG_over_RT=-2.932; x_SiO2_mol_pct=40.00 | read |
| /3 filled down-triangle | CaTiO3 | 2960.8 | 4682.4 | quantity_as_printed=Delta_f G_T / RT; axis_delta_fG_over_RT=-2.580; x_SiO2_mol_pct=50.00 | read |
| /3 filled down-triangle | CaTiO3 | 3238.6 | 4378.9 | quantity_as_printed=Delta_f G_T / RT; axis_delta_fG_over_RT=-2.105; x_SiO2_mol_pct=60.02 | read |
| /3 filled down-triangle | SiO2 | 1796.7 | 1901.0 | activity=0.002193 | read |
| /3 filled down-triangle | SiO2 | 2074.1 | 1535.5 | activity=0.018493 | read |
| /3 filled down-triangle | SiO2 | 2350.4 | 1259.4 | activity=0.092683 | read |
| /3 filled down-triangle | SiO2 | 2443.4 | 1186.4 | activity=0.141906 | read |
| /3 filled down-triangle | SiO2 | 2903.3 | 924.2 | activity=0.656145 | read |
| /3 filled down-triangle | SiO2 | 3182.3 | 871.8 | activity=0.891251 | read |
| /1 filled circle | CaO | 1530.2 | 1480.4 | activity=0.025527 | read |
| /1 filled circle | CaO | 1797.0 | 1480.6 | activity=0.025527 | read |
| /1 filled circle | CaO | 2074.2 | 1679.9 | activity=0.007962 | read |
| /1 filled circle | CaO | 2350.7 | 1877.7 | activity=0.002512 | read |
| /1 filled circle | CaO | 2443.7 | 1940.2 | activity=0.001746 | read |
| /1 filled circle | CaO | 2628.2 | 2052.3 | activity=0.000906 | read |
| /1 filled circle | CaO | 2905.3 | 2184.7 | activity=0.000419 | read |
| /1 filled circle | CaO | 3183.0 | 2252.2 | activity=0.000282 | read |
| /2 filled up-triangle | TiO2 | 1529.5 | 1096.6 | activity=0.239883 | read |
| /2 filled up-triangle | TiO2 | 1796.6 | 1093.8 | activity=0.243781 | read |
| /2 filled up-triangle | TiO2 | 2074.7 | 1024.3 | activity=0.365595 | read |
| /2 filled up-triangle | TiO2 | 2350.5 | 1009.5 | activity=0.399025 | read |
| /2 filled up-triangle | TiO2 | 2443.6 | 1016.3 | activity=0.383707 | read |
| /2 filled up-triangle | TiO2 | 2905.0 | 1129.1 | activity=0.198609 | read |
| /2 filled up-triangle | TiO2 | 3182.5 | 1253.9 | activity=0.095719 | read |
| /5 open circle | CaTiSiO5 | 1794.0 | 1188.0 | activity=0.140605 | read |
| /5 open circle | CaTiSiO5 | 2084.0 | 937.0 | activity=0.609537 | read |
| /5 open circle | CaTiSiO5 | 2349.0 | 861.0 | activity=0.948418 | read |
| /5 open circle | CaTiSiO5 | 2447.0 | 850.0 | activity=1.011579 | read |
| /5 open circle | CaTiSiO5 | 2626.0 | 868.0 | activity=0.909913 | read |
| /5 open circle | CaTiSiO5 | 2905.0 | 950.0 | activity=0.564937 | read |
| /5 open circle | CaTiSiO5 | 3185.0 | 1071.0 | activity=0.278612 | read |
| /2 filled up-triangle | TiO2 | 2628.7 | 1054.7 | — | refused: one core with SiO2, centre not assignable |
| /3 filled down-triangle | SiO2 | 2628.7 | 1054.7 | — | refused: one core with TiO2, centre not assignable |

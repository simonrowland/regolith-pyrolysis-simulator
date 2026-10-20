# Shornikov 2019 perovskite thermodynamics — extraction audit

Source id `shornikova-2019-perovskite-thermodynamics` (unchanged). The 600 dpi render of PDF page 1 shows the English byline S. I. Shornikov with a superscript a and an asterisk. pdftotext glues that superscript into Shornikova. The correspondence address is sergey.shornikov@gmail.com. Russian Journal of Physical Chemistry A, 2019, Vol. 93, No. 8, pp. 1428–1434. DOI 10.1134/S0036024419080284. Russian text: Zhurnal Fizicheskoi Khimii, 93(8), 1130–1137. The running header on published page 1432 is SHORNIKOV.

This is not the RJPC evaporation paper (DOI 10.1134/S0036024419060293, Vol. 93, pp. 1024–1031), the TMS chapter, or the Experiment in Geosciences note. Reference [47] of this paper is the 2003 symposium paper. Reference [2] is the evaporation study. Reference [48] is the equipment paper and does not restate a cell material here.

## Duplicate verdict

Not a duplicate. Title, venue, DOI, and page range differ from every Shornikov or Yakovlev extract on this base.

## Tables

`mineru_table_draft.py emit` wrote 78 observations from `p002-t01` column 3 and from `p004-t02` columns 2–4, split by compound because the compound cell is a rowspan. Empty ΔH and ΔS cells were skipped (10). They are not stored as zero.

MinerU and `pdftotext-layout.txt` agree on every numeric token. pdftotext prints the minus sign as U+2013. MinerU and the stored cells use ASCII hyphen-minus. A 600 dpi render of PDF page 2 checked the head of Table 1, including −40.48 ± 0.42, and both this-work rows: 1791–2241, −39.98 ± 0.54, 3.15 ± 0.28, and 2241–2398, 7.73 ± 1.76, 24.39 ± 0.76. A 600 dpi render of PDF page 4 checked the Table 2 this-work row: 2241 ± 10, 47.61 ± 1.84, 21.24 ± 0.81, and the neighbouring CaTiO3 rows through the first TiO2 row. The ± split uses those renders plus the agreement of the two text layers. No digit was changed.

Table 1 temperature cells are intervals (1791–2241), points (298), or a point with a printed ± (1068 ± 2, 1740 ± 20). The interval is `T_range_K` and `point_conditions.temperature_K` is unknown. No midpoint is invented. A ± temperature stays a point at the printed centre, with `T_as_printed` holding the full cell.

This-work rows (footnote *) are `derived_least_squares`. The text says the enthalpies come from equation (12), ΔG_T = ΔH_T − TΔS_T, and the melting row from equations (13)–(15). Other rows are `quoted_attributed` with the Ref. cell. The cited papers are not in this extract.

The 1791–2241 K this-work enthalpy is phase `cr` because equations (13)–(15) call that residual the crystalline (sol) value. The 2241–2398 K row is phase `l` because the text assigns 7.73 ± 1.76 kJ/mol and 24.39 ± 0.76 J/(mol K) to the liquid state. Literature rows have no phase. Melting rows are phase `cr`, the solid that melts, matching the 2020 melting rows. Table 2 does not print a total pressure.

## Formation entropy

Table 1 ΔS_T has no quantity token. `entropy_of_fusion` is the melting entropy. `S` is absolute entropy. The 14 printed ΔS_T cells, including this-work 3.15 ± 0.28 and 24.39 ± 0.76, are context rows of type `formation_entropy_schema_gap`. They are not scored.

## Abstract versus Table 1

The abstract and the conclusions print −39.88 ± 0.54 kJ/mol. Table 1 prints −39.98 ± 0.54 for the 1791–2241 K this-work row. Both are kept. The table cell is the scored observation. The abstract number is context, not a second measurement. The melting figures 47.61 ± 1.84 kJ/mol and 2241 ± 10 K agree between the abstract and Table 2.

## Standard state

Table 1 caption and equation (2): formation from simple oxides, [CaO] + [TiO2] = [CaTiO3], per 1 mol. The paper does not state whether each simple oxide is the pure solid or the pure liquid. `reference_state_from_extract` on that prose returns unknown (it does not name one reference endmember). These rows are `delta_fH`, so migration does not lift an activity reference from them.

Fig. 3 series 3 is stored. The reference sentence, published page 1431, under equations (5) and (6), is quoted as printed: pi and pi° are the partial pressures of gas phase components over perovskite and a simple oxide (a pure component). Equation (5) is printed `a_CaO = p_CaO / p°_CaO = p_Ca p_O / (p°_Ca p°_O)`. Equation (6) is printed `a_TiO2 = p_TiO2 / p°_TiO2 = (p_TiO / p°_TiO)(p_O / p°_O)`, with no superscript 2 on the parenthetical factors; a 200 dpi render scaled six times is the check, and pdftotext had inserted carets the page does not show. The paper does not print the phrase pure simple oxide.

The pure-oxide phase is the stable phase at the read temperature, inferred. CaO uses Table 2's lowest melting temperature, 2843 K: every Fig. 3a point is crystalline lime, and none is within 100 K of 2843 K. TiO2 uses Table 2's highest melting temperature, 2185 ± 10 K, ref. [41]. Points below that band are crystalline; points above it are liquid. 2050.6 K is 47 K from the lower listed melting temperature 2098 K, and the inference says so. 2197.7 K is 13 K above 2185 K, above the ±10 K band, so the reference is liquid while the perovskite sample is still crystalline (43 K below the this-work melting temperature 2241 ± 10 K). `reference_state_from_extract` resolves CaO to crystalline lime and TiO2 to cr or l as that rule says, with reference pressure left unset.

## Figures left as context

Fig. 1 is literature heat capacity. Fig. 2 series 9 is this work; the admitted result is the Table 1 least-squares row, so the curve was not digitized. Fig. 4 is reference [2]. Fig. 5 is the De Vries diagram, reference [39].

## Fig. 3 series 3

Published page 1432 is PDF page 5 (pdf_page_index 4), not page 4. The caption assigns series 3, the open circles, to this work. Series 1 and 2 are the filled and half-filled circles of [46] and [47], and the error bars belong to those literature symbols. Series 4 and 5 are the triangles of [44] and [45].

Both panels were calibrated from the inward tick stubs on the 600 dpi render. Fig. 3a: 473 px per 200 K, residual 0 on 1800, 2000, 2200, 2400; 464 px per 0.01 in activity, residual 0 on 0.03, 0.02, 0.01. The unlabelled frame is at a = 0.00503, not at zero. Fig. 3b: least-squares temperature residuals at most 1.24 K; activity residuals at most 0.000034. The unlabelled frame is at a = 0.0498.

A marker is a centre, or an assignable exposed arc, inside a stated uncertainty. The uncertainty is wider when only an arc is visible. An open-circle centre is a white pixel whose rays meet the stroke near the series radius, 37 px on panel a and 32 px on panel b. Twenty-one Fig. 3a circles and fifteen Fig. 3b circles are stored. Isolated circles (ray std under 2 px) are stored at ±0.0002 and ±2 K on panel a, and at ±0.001 and ±3 K on panel b. Circles whose neighbour cuts the ring are stored wider: ±0.0005 and ±4 K, or ±0.002 and ±5 K. Each centre was checked against the 600 dpi render.

Each overlapping pair is stored as two rows:

- (1485.00, 1001.00), CaO, 2033.2 K, activity 0.0236, is the right-hand circle. The left circle is (1452.00, 976.00), 2019.2 K, activity 0.0241, 59 of 72 rays at radius 35 px. Uncertainty ±0.0005 and ±4 K. The sample is crystalline perovskite, 222 K below 2241 K.
- (2001.00, 840.00), CaO, 2251.4 K, activity 0.0270, is the right-hand ring. The sample is liquid perovskite, 10 K above 2241 K. The left ring is (1967.00, 846.00), 2237.0 K, activity 0.0269, 62 of 72 rays at radius 40 px. Uncertainty ±0.0005 and ±4 K. The sample is crystalline perovskite, 4 K below 2241 K, within 100 K of that melting temperature. CaO is crystalline lime at both temperatures.
- (1534.00, 2812.00), TiO2, 2100.1 K, activity 0.1997. 250 degrees at radius 31 px. The literature error bar takes the missing sector. The pure oxide is crystalline, 85 K below 2185 K and 2 K from the lower listed melting temperature 2098 K. Uncertainty ±0.001 and ±4 K.
- (1198.00, 3048.00), TiO2, 1940.2 K, activity 0.1518. 240 degrees at radius 31.5 px, 48 px from the stored circle at (1231.00, 3083.00). Uncertainty ±0.001 and ±4 K.
- (1887.00, 2604.00), TiO2, 2268.1 K, activity 0.2419. 230 degrees at radius 31 px. The pure oxide is liquid, 83 K above 2185 K. The sample is liquid perovskite, 27 K above 2241 K. Uncertainty ±0.001 and ±4 K.

Still refused, one by one:

- The legend open circle at (2213, 3127) is a complete 32 px ring and it is the series-3 key.
- (1640, 911) is the literature half-filled symbol. A 160 degree ring is visible at (1644, 913); the symbol is not a series-3 open circle.
- (1637.00, 972.00) is stored. It is the open circle below the half-filled symbol: 2097.5 K, activity 0.0242, 65 of 72 rays at radius 39 px. Uncertainty ±0.0005 and ±4 K. CaO is crystalline lime. The sample is crystalline perovskite, 144 K below 2241 K.
- (1065, 3176) has a 170 degree sector at radius 25 to 28 px, short of the series radius 32 px, and the centre moves 14 px between the two best fits.

The same page says the crystalline-state activities at 2182–2241 K were determined using the data in [47]. The caption still assigns the open circles to this work, so those points are stored, with that sentence left in the audit rather than used to drop them.

## Tool friction

Emit cannot put `transition_temperature` on `T_K`, and one mapping cannot carry three quantities. Melting temperatures were moved onto `T_K` and `T_m_K` after emit. The compound rowspan was three fixed-species mappings. The tool was not changed.

## This-work Fig. 3 markers

| series | species | x | y | value | decision |
| --- | --- | --- | --- | --- | --- |
| a/3 open circle | CaO | 2001.00 | 840.00 | activity=0.0270 | read |
| a/3 open circle | CaO | 1967.00 | 846.00 | activity=0.0269 | read |
| a/3 open circle | CaO | 1485.00 | 1001.00 | activity=0.0236 | read |
| a/3 open circle | CaO | 1452.00 | 976.00 | activity=0.0241 | read |
| a/3 open circle | CaO | 1637.00 | 972.00 | activity=0.0242 | read |
| a/3 open circle | CaO | 904.00 | 1132.00 | activity=0.0207 | read |
| a/3 open circle | CaO | 1031.50 | 1112.50 | activity=0.0212 | read |
| a/3 open circle | CaO | 1103.00 | 1075.50 | activity=0.0220 | read |
| a/3 open circle | CaO | 1206.50 | 1048.00 | activity=0.0226 | read |
| a/3 open circle | CaO | 1259.00 | 1035.00 | activity=0.0228 | read |
| a/3 open circle | CaO | 1291.00 | 984.00 | activity=0.0239 | read |
| a/3 open circle | CaO | 1343.00 | 1010.50 | activity=0.0234 | read |
| a/3 open circle | CaO | 1381.00 | 870.00 | activity=0.0264 | read |
| a/3 open circle | CaO | 1521.50 | 967.00 | activity=0.0243 | read |
| a/3 open circle | CaO | 1870.50 | 887.50 | activity=0.0260 | read |
| a/3 open circle | CaO | 2034.50 | 868.50 | activity=0.0264 | read |
| a/3 open circle | CaO | 2086.50 | 879.00 | activity=0.0262 | read |
| a/3 open circle | CaO | 2147.50 | 873.50 | activity=0.0263 | read |
| a/3 open circle | CaO | 2220.50 | 916.00 | activity=0.0254 | read |
| a/3 open circle | CaO | 2282.50 | 924.00 | activity=0.0252 | read |
| a/3 open circle | CaO | 2345.00 | 925.00 | activity=0.0252 | read |
| b/3 open circle | TiO2 | 1887.00 | 2604.00 | activity=0.2419 | read |
| b/3 open circle | TiO2 | 1198.00 | 3048.00 | activity=0.1518 | read |
| b/3 open circle | TiO2 | 1534.00 | 2812.00 | activity=0.1997 | read |
| b/3 open circle | TiO2 | 885.00 | 3261.00 | activity=0.1085 | read |
| b/3 open circle | TiO2 | 995.50 | 3183.50 | activity=0.1243 | read |
| b/3 open circle | TiO2 | 1155.00 | 3102.00 | activity=0.1408 | read |
| b/3 open circle | TiO2 | 1231.00 | 3083.00 | activity=0.1447 | read |
| b/3 open circle | TiO2 | 1307.00 | 3025.00 | activity=0.1564 | read |
| b/3 open circle | TiO2 | 1430.00 | 2923.00 | activity=0.1771 | read |
| b/3 open circle | TiO2 | 1739.00 | 2663.00 | activity=0.2299 | read |
| b/3 open circle | TiO2 | 1931.50 | 2598.50 | activity=0.2430 | read |
| b/3 open circle | TiO2 | 1982.50 | 2593.50 | activity=0.2440 | read |
| b/3 open circle | TiO2 | 2048.50 | 2589.00 | activity=0.2449 | read |
| b/3 open circle | TiO2 | 2105.00 | 2585.00 | activity=0.2457 | read |
| b/3 open circle | TiO2 | 2159.00 | 2579.00 | activity=0.2469 | read |
| legend open circle | series 3 | 2213 | 3127 | — | refused: series key, not a datum |
| half-filled circle | literature | 1640 | 911 | — | refused: series 2 of [46] or [47], not a series-3 open circle |
| b/3 open arc | TiO2 | 1065 | 3176 | — | refused: sector radius 25-28 px, short of the series radius 32 px, and the centre moves 14 px |

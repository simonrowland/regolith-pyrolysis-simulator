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

An open-circle centre is a white pixel whose rays to the inner stroke have median 37 px (panel a) or 32 px (panel b). Sixteen Fig. 3a circles and twelve Fig. 3b circles meet that test. Isolated circles (ray std under 2 px) are stored at ±0.0002 and ±2 K on panel a, and at ±0.001 and ±3 K on panel b. Circles whose neighbour cuts the ring are stored wider: ±0.0005 and ±4 K, or ±0.002 and ±5 K. Each centre was checked by drawing it back on the render.

Not stored, one by one: the legend open circle at (2213, 3127); the open circle at (1534, 2812) because the literature error bar runs through it; the white lens at (1195, 3044) between two stored circles; the open arc just left of (1521.50, 967.00), whose best ray median is 31.5 px against 37 px; the leftmost circle of the Fig. 3b high-temperature cluster, whose best ray median is 27 px against 32 px.

The same page says the crystalline-state activities at 2182–2241 K were determined using the data in [47]. The caption still assigns the open circles to this work, so those points are stored, with that sentence left in the audit rather than used to drop them.

## Tool friction

Emit cannot put `transition_temperature` on `T_K`, and one mapping cannot carry three quantities. Melting temperatures were moved onto `T_K` and `T_m_K` after emit. The compound rowspan was three fixed-species mappings. The tool was not changed.

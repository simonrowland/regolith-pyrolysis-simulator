# Shornikova 2019 perovskite thermodynamics — extraction audit

Source id `shornikova-2019-perovskite-thermodynamics`. The English byline prints S. I. Shornikova. The correspondence address is sergey.shornikov@gmail.com. Russian Journal of Physical Chemistry A, 2019, Vol. 93, No. 8, pp. 1428–1434. DOI 10.1134/S0036024419080284. Russian text: Zhurnal Fizicheskoi Khimii, 93(8), 1130–1137.

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

Fig. 3 activities were not digitized. The reference sentence is on the context row: pi and pi° are partial pressures over perovskite and a simple oxide (a pure component). The paper does not state solid or liquid. The physics ruling on a pure oxide at the experiment temperature was not applied, because no activity point was stored and the figure does not state a single reference temperature.

## Figures left as context

Fig. 1 is literature heat capacity. Fig. 2 series 9 is this work; the admitted result is the Table 1 least-squares row, so the curve was not digitized. Fig. 3 open circles (this work) overlap series 1 and 2 and carry error bars; a centre could not be read within a stated uncertainty. Fig. 4 is reference [2]. Fig. 5 is the De Vries diagram, reference [39].

## Tool friction

Emit cannot put `transition_temperature` on `T_K`, and one mapping cannot carry three quantities. Melting temperatures were moved onto `T_K` and `T_m_K` after emit. The compound rowspan was three fixed-species mappings. The tool was not changed.

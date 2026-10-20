# Shornikov 2026 wollastonite KEMS — extraction audit

Source id `shornikov-2026-wollastonite-kems`. Title: Mass-Spectrometric Investigation of Evaporation Processes and Thermodynamic Properties of Wollastonite. S. I. Shornikov. Geochemistry International 64(8), 608–632. Received 3 April 2026, revised 5 May 2026, accepted 10 May 2026. DOI printed on the title page: 10.1134/S0016702926700035. Worker t1372-w2-w7-t1. Date 2026-10-20. review_status draft.

PDF page N is published page 607+N. PDF page 1 is published page 608. `pdf_page_index` is N−1.

## Duplicate verdict

Not a full duplicate, and not a skip. This is the full Geochemistry International paper of the same evaporation campaign as `shornikov-2024-wollastonite-evaporation` (TMS 2024 chapter).

Evidence, compared on the printed numbers rather than the source id:

- Temperature range 1751–2087 K is the same.
- Table 1 this-study rows match the 2024 extract exactly: crystalline 1751–1818 K, −38.92±0.52 kJ/mol and 2.33±0.29 J/(mol K); liquid 1818–2087 K, −11.29±0.24 kJ/mol and 17.53±0.12 J/(mol K).
- Table 7 this-study row matches: Knudsen effusion mass spectrometry, 1818±7 K, 27.64±0.57 kJ/mol.
- The body says the activities are approximately 0.015 (CaO) and 0.20 (SiO2). Those prose values are already `shornikov_2024_cao_activity_prose` and `shornikov_2024_sio2_activity_prose`.

Those summary constants are not re-admitted. The 2024 extract refused the pressure curves because the chapter did not print a table. This paper prints Tables 3–6 and the rankinite estimate. Those are the rows admitted here.

No other extract on this base has this title, this DOI, or these vapor-pressure tables.

## Tables

The corpus text directory contains `pdftotext-layout.txt` only. There is no MinerU content list, so `mineru_table_draft.py` emit was not run. Born-digital tables were transcribed and checked against 600 dpi renders. Dashes are absent and are omitted, never stored as zero.

| table | published page | what was admitted |
| --- | --- | --- |
| 1 | 611–612 | nothing. Literature survey plus two this-study rows that duplicate the 2024 extract |
| 2 | 614 | nothing. Other experiments only. No this-study row |
| 3 | 618 | 146 partial pressures. CaO has 7 values (1910–2087 K). Mo has 13 values (the 1751 K cell is a dash). Nine other species have 14 values |
| 4 | 619 | 14 Hertz–Knudsen p(O) values, column (11)–(13), plus 76 equilibrium-constant cells in columns (14)–(19). Eight printed dashes are omitted |
| 5 | 620 | 15 coefficient rows. Each printed a_i and b_i falls inside its printed ± when refit to the parent pressures |
| 6 | 621 | two this-study reactions only (enthalpy and entropy each). BaSiO3 and CaTiO3 rows are other work |
| 7 | 623 | nothing. The this-study melting row duplicates the 2024 extract. The other rows are literature |

Table 3 and Table 4 are rotated on the portrait page. pdftotext reading order matched a rotated 600 dpi render, including every mantissa, exponent, and dash that was checked.

Eq. (20) and the Table 5 header both print log p_i = a_i/T + b_i. pdftotext dropped the slash. The slash is on the 600 dpi crop. The fit stored here is log10(p) = a_i/T + b_i.

Eq. (25) on the 600 dpi crop has no square on p_SiO. The pdftotext "p 2" is the subscript of SiO2. Activities are not re-admitted, so the equation is quoted only as the reference-state sentence.

## Counts

39 observations. 236 pressure points: 146 from Table 3, 14 from Table 4 column (11)–(13), 76 from Table 4 columns (14)–(19). 15 Table 5 coefficient rows, linked. 4 Table 6 reaction rows, unlinked. 2 rankinite rows, unlinked.

Zero figure points.

## Standard state

No activity row is admitted, so migration has no activity dataset from this file.

The paper's sentence, published page 622 (PDF page 15), after Eqs. (24) and (25): p_i and p_i° are the partial pressures of the component of the gas phase over wollastonite and simple oxide (pure component), respectively.

The paper does not state whether that pure component is crystalline or liquid. It does not state that p° was measured in this cell at the experiment temperature. Fig. 6 draws the pure-oxide comparison from other papers (Shornikov 2025 for CaO, Shornikov et al. 1998 for SiO2). The reference stays unknown. It is not inferred. The prose does not say Raoultian, pure liquid, or pure solid.

The same sentence is stored on the overlap context row so the quote is in the extract. It is not copied onto the pressure rows. Pressure rows are gas partial pressures, not oxide activities.

1815 K lies inside the printed melting point 1818±7 K. That band would defer an activity whose reference was the pure oxide at the run temperature. These gas-pressure rows are not that reference, so they are not deferred.

## Derivations

Table 5. Unweighted least squares of log10(p) = a/T + b against the parent pressures. Low side uses 1751, 1783, 1800, 1815 K. High side uses 1833–2087 K. 1817 K is the printed split and is not a pressure row. CaO uses only 1910–2087 K. Every fitted coefficient falls inside the printed ±. The printed coefficients are stored, not the refit. The recovered standard errors are close to the printed widths and are not identical, so they are not stored as the printed uncertainty. Oxygen's parent is Table 4 column (11)–(13). Every other species' parent is its Table 3 series.

Table 4 columns (14)–(19). Eqs. (14)–(19) as read on the 600 dpi page:

- (14) p_O = K_3(T) p_CaO / p_Ca
- (15) p_O = K_4(T) p_SiO2 / p_SiO
- (16) p_O = p_O2^(1/2) / K_5(T)^(1/2)
- (17) p_O = K_8(T) p_MoO / p_Mo
- (18) p_O = K_9(T) p_MoO2 / p_MoO
- (19) p_O = K_10(T) p_MoO3 / p_MoO2

K_r(T) is cited to Glushko et al. (1978–1982) and is not printed. The rows are model_derived with no resolvable parent. `raw_inputs: not_printed` is not used, because that declaration is invalid for model_derived evidence.

Table 6 this-study rows, render-checked: [CaSiO3] = (CaSiO3) at 1812–2187 K, 771±13 kJ/mol and 242±5 J/(mol K); (CaSiO3) = (CaO) + (SiO2) at 1910–2187 K, 479±12 kJ/mol and 47±6 J/(mol K). The intervals are not the Table 3 interval. Stored as delta_rH and delta_rS, not as delta_fH, because 771 kJ/mol is a reaction enthalpy. Parents are not linked.

Rankinite, 600 dpi crop of published page 623: −42.63±0.63 kJ/mol and 2.39±0.35 J/(mol K) at 1600–1737 K, from Eqs. (31) and (32). Eq. (32) uses orthosilicate activities from Shornikov 2026a, which are not printed here. The wollastonite activities "increase slightly" with temperature and have no printed grid. No parent is linked. The enthalpy is stored as delta_fH because that is the field the 2024 extract uses for the mixing enthalpy of the same derivation family. The paper calls it an enthalpy of mixing. The phase (crystal or liquid) is not stated.

## Render checks

600 dpi: PDF pages 10–16 (published 617–623). Table 3 rotated preview. Table 4 rotated page, including Eqs. (14)–(20). Table 5 header and every coefficient row. Eqs. (24) and (25), full resolution, no square on p_SiO. Eqs. (31)–(34). Table 6 this-study numbers. Rankinite sentence.

120 dpi glances, no marker centres read: PDF page 6 (Figs. 3 and 4, published 613), PDF pages 17–19 (Figs. 8–12, published 624–626).

Not rendered: published page 616 (PDF page 9), the experimental paragraph. The residual-gas exponent is an en dash in pdftotext, and the pyrometer phrase is "better than 10" with no unit in the text extract. Neither number is stored. Ion-current ratios and appearance energies on that page are not stored as numbers.

## Figure decisions

No labelled-tick calibration was done, because no marker centre was read. A series that is already a table is refused as a figure read so the pressure is not admitted twice.

| figure | series | what | decision |
| --- | --- | --- | --- |
| 3 | 1–22 | EMF, slag, and earlier KEMS activities named in the caption | refused: other work |
| 3 | 23 | this study, red circles near 0.015 and 0.20 | refused: 120 dpi glance, no tick calibration. The prose values are already in the 2024 extract |
| 3 | 24 | vertical dotted melting line | refused: not a data series |
| 3 | legend | keys inside the frame | refused: legend glyphs |
| 4 | 1–12, 14, 15 | other mixing energies and calculations | refused: other work |
| 4 | 13 | this study mixing-energy circles | refused: 120 dpi glance, no tick calibration. The enthalpy and entropy are the Table 1 duplicates |
| 4 | 16 | vertical dotted melting line | refused: not a data series |
| 6 | 1 | CaO, Shornikov 2025 | refused: other paper. Also the p° comparison, not a reference phase stated for this cell |
| 6 | 2 | tricalcium silicate, Shornikov 2026 | refused: a different citation from "(4) this study" |
| 6 | 3 | orthosilicate, Shornikov 2026a | refused: other paper |
| 6 | 4 | this study, Ca, SiO, O, CaSiO3 | refused as a figure read: the points are Tables 3 and 4 |
| 6 | 5 | SiO2, Shornikov et al. 1998 | refused: other paper |
| 7 | 1, 2, 4–9 | CaSiO3, CaTiO3, and BaSiO3 from the named papers | refused: other work |
| 7 | 3 | this study | refused as a figure read: Table 3 and Table 6 hold the numbers |
| 8 | 1–8, 11–13 | EMF, slag, KEMS, and thermochemical mixing energies | refused: other work |
| 8 | 9, 10 | this study, Eqs. (31) and (32) | refused as a figure read: the printed result is −42.63±0.63 kJ/mol and 2.39±0.35 J/(mol K) |
| 8 | 14 | vertical dotted decomposition line | refused: not a data series |
| 9a | 1–8 | O, O2, Ca, CaO, SiO, SiO2, Si2O2, CaSiO3, KEMS | refused as a figure read: Tables 3 and 4 |
| 9b | 1–8 | same species, calculated for neutral redox | refused: calculated strokes, no tick calibration, no table |
| 10 | 1 | open circles, reducing-cell oxygen content and O2/O | refused: 120 dpi glance, no labelled-tick calibration. The prose bounds 0.010–0.016 mol % are context |
| 10 | 2 | calculated neutral redox, about 36 mol % oxygen | refused: calculated stroke. The prose bound is context |
| 11 | 1–5 | total-pressure deviation for CaO, Ca3SiO5, Ca2SiO4, CaSiO3, SiO2 | refused: no table of the plotted deviation, no tick calibration. Series 4 is this study's wollastonite and is still a curve |
| 12 | 1–4 | CAI residual-melt composition | refused: another calculation, not this cell's pressure table |
| 12 | 5–7 | gehlenite, anorthite, and wollastonite activities in a CAI melt | refused: calculated curves, no tick calibration |

## Not stored

Evaporation coefficients are adopted from Shornikov 2015 and are not printed. Orifice area, Clausing factors, and ionization cross sections are named in Eqs. (11)–(13) and are not given numerical values. Neutral-redox pressures in Figs. 9b and 10–11 have no table. The residual-gas bound and the pyrometer unit were not render-checked.

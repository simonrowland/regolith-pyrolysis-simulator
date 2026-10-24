# Shornikov & Golyapa 2023 MgO–P4O10 melts — extraction audit

Source: Shornikov, S. I., and Golyapa, E. S. (2023), Thermodynamic properties of the MgO–P4O10 melts, Experiment in Geosciences, vol. 29, no. 1, pp. 60–62. The PDF is a six-page journal excerpt (`pdfinfo` title `titul2c_no_kaz2023.cdr`, 6 pages, A4). This extract is only the MgO article.

Page map, read from the 600 dpi renders of PDF pages 4 and 5 and from the 120 dpi preview of PDF page 3:

| PDF page | Published page | Contents |
| --- | --- | --- |
| 3 | 60 | End of the SiO2–P4O10 article, including its Fig. 5, then the MgO title, abstract and opening. Excluded from this extract except the MgO opening. |
| 4 | 61 | MgO body, Fig. 1, the −175 kJ/mol sentence. Footer: Experiment in Geosciences 2023 Volume 29 N 1, page 61. |
| 5 | 62 | Figs. 2 and 3, then the references. Footer page 62. |
| 6 | 63 | Slobodov et al., a different article. Excluded. Its equation (2) is not an MgO standard state. |

Duplicate check: not a duplicate. `shornikov-2023-calcium-phosphate` is Shornikov and Demidova, CaO–P4O10, published pages 55–57, and that extract already excludes the SiO2 article that begins on page 57. `shornikov-2023-cai-melt-evaporation` and `shornikov-2023-natural-cai-evaporation` are CAI evaporation papers. No extract on this base cites Golyapa or pages 60–62 of this volume.

No numeric table is printed. MinerU was not emitted. `pdftotext-layout.txt` has the prose and the figure captions. The −175 sentence matches the 600 dpi page.

## Standard state

Activity rows quote published page 61: the initial data are the Gibbs energies of formation of MgO and P4O10 in the crystalline and liquid phases. The article never names one activity reference phase. Each activity `standard_state` ends with "The activity reference phase is not stated."

`reference_state_from_extract` returns unknown for every activity string, reason `source reference state not printed`. The word "crystalline" is not treated as a solid endmember. The phase is not inferred. Both crystalline and liquid ΔG° are model inputs, so a single Raoultian endmember would be the wrong reading. Melting-band deferral does not apply: there is no pure-oxide reference at the experiment T.

The mixing-energy minimum is a melt mixing energy. Its reference phase is not stated.

## Prose kept as context

- Model domain 600–1800 K. Semi-empirical model of Shornikov (2019). Published page 60.
- Minimum mixing energy at 1673 K, composition region close to MgP2O6, equal to −175 kJ/mol. Published page 61, quoted. Not read off Fig. 2B.
- Vapor set used in the equilibrium calculation, with no printed partial pressure: Mg, Mg2, MgO, P, P2, P3, P4, PO, PO2, P2O3, P2O4, P2O5, P3O6, P4O6, P4O7, P4O8, P4O9, P4O10, MgPO2, MgPO3, O, O2, O3, O4.
- Parents (Glushko et al. 1978–1982 for the oxides; Jung et al. 2013 and Ding et al. 2015 for the phosphates) are not printed per point. No parent was invented.

Cited melting points 1630, 1658, 1438 and 1183 K, and the 1328 K Mg3P2O8 transition, are Berak (1958). Not stored.

The body says Oetting and McDonald (1963) measured heat capacities and enthalpies of melting. The Fig. 2 caption nevertheless assigns symbol 5 to that paper. The symbol stays refused as not this article. Iwase et al. (1987) is an EMF activity at 1673 K over 8–14 mol% P4O10 and is not a symbol on the figure.

The caption and the body say Ding et al. (2015). The reference list prints Ding et al. (2016), Acta Phys.-Chim. Sinica. The extract follows the caption year.

## Figure decisions

600 dpi, PDF page 5, published page 62. Axes are labelled. No tick calibration was performed because no coordinate was stored. Calculated series are continuous curves. Numerals on those curves are labels, not markers. Panel letters A and B are not markers.

Fig. 2 caption: activities of MgO (1, 2) and P4O10 (3–8), mixing energy (9–11), at 1400 (6), 1600 (7), 1673 (1–5, 9, 10) and 1800 (8, 11) K. Calculated in the present study (1, 3, 9). Ding et al. (2, 4, 10). Experimental: Oetting and McDonald 1963 (5), Lopatin et al. 1986 (6, 7), Lopatin and Semenov 1989 (8, 11). The caption prints "dedermined".

Legend inside Fig. 2A, upper right, off the curves: open circle 5, filled diamond 6, open diamond 7, half-filled diamond 8.

| Marker | Decision | Evidence |
| --- | --- | --- |
| Fig. 2A curve 1 | refused | Continuous calculated lg a(MgO) at 1673 K. Numeral 1 is a label on the line. No discrete calculated marker. |
| Fig. 2A curve 2 | refused | Continuous line. Caption assigns it to Ding et al. (2015). |
| Fig. 2A curve 3 | refused | Continuous calculated lg a(P4O10) at 1673 K. Numeral 3 is a label. |
| Fig. 2A curve 4 | refused | Continuous line. Caption assigns it to Ding et al. (2015). |
| Fig. 2A half-filled diamond near the crossing, centre about (1085, 843) px | refused | Style of legend symbol 8. Caption assigns 8 to Lopatin and Semenov (1989) at 1800 K. Not read. |
| Fig. 2A open diamond, centre about (886, 1215) px | refused | Style of legend symbol 7. Caption assigns 7 to Lopatin et al. (1986) at 1600 K. Not read. |
| Fig. 2A two open circles on the rising red line, centres about (797, 1247) and (786, 1312) px | refused | Style of legend symbol 5. Caption assigns 5 to Oetting and McDonald (1963). Not read. |
| Fig. 2A half-filled diamond at the lower corner, overlapping the open-circle pile | refused | Symbol-8 style, on the cited experimental series. Not read. |
| Fig. 2A open-circle pile along the lower plateau | refused | Same symbol 5. The circles overlap one another and the corner diamond. A separated count is not stable, so no centre was stored. They are not markers of this calculation. |
| Fig. 2A legend glyphs 5, 6, 7, 8 | refused | Inside the frame, upper right, with their numerals. Not on the curves. No separated solid-diamond data marker (legend style 6) was found in the plot interior. |
| Fig. 2B curve 9 | refused | Continuous calculated mixing energy. The printed minimum −175 kJ/mol is the stored prose value. The curve was not digitized. |
| Fig. 2B curve 10 | refused | Continuous line. Caption assigns it to Ding et al. (2015). |
| Fig. 2B half-filled diamond on the curves, centre about (2633, 1402) px | refused | Caption symbol 11, Lopatin and Semenov (1989) at 1800 K. Not read. |
| Fig. 2B diamond and numeral 11 at the lower left, off the curves | refused | Legend for symbol 11, placed inside the frame. Not a second data point. |

Fig. 3 caption: partial pressures of (O2) (1), (Mg) (2, 5), (PO) (3, 6) and (PO2) (4, 7) over Mg3P2O8 (A) and Mg2P2O7 (B). Calculated in the present study (1–4). Knudsen measurements: Lopatin and Semenov 1989 (5, 6), Lopatin et al. 1986 (7).

| Marker | Decision | Evidence |
| --- | --- | --- |
| Fig. 3A lines 1, 2, 3 | refused | Continuous calculated lines (O2, Mg, PO). Numerals are labels. No discrete calculated marker. |
| Fig. 3A line 5 with one error bar | refused | Experimental (Mg), Lopatin and Semenov (1989). The error bar is the cited series, not a point of this article. Not read. |
| Fig. 3A line 6 with one error bar | refused | Experimental (PO), same citation. Not read. |
| Fig. 3B lines 1 and 3 | refused | Continuous calculated O2 and PO. |
| Fig. 3B line 4 | refused | Continuous calculated PO2. Numeral 4 is a label. |
| Fig. 3B line 7 with one error bar | refused | Experimental PO2, Lopatin et al. (1986). Not read. |
| Fig. 1 | refused | Phase diagram according to Ding et al. (2015). No activity or pressure of this article. |

No point was given a value. Read uncertainty is not stated because nothing was read.

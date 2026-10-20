# Shornikov & Slobodov 2023 SiO2–P4O10 melts — extraction audit

Source: Shornikov, S. I., and Slobodov, A. A. (2023), Thermodynamic properties of the SiO2–P4O10 melts, Experiment in Geosciences, vol. 29, no. 1, pp. 57–60. The PDF is a six-page journal excerpt (`pdfinfo` title `titul2c_no_kaz2023.cdr`, 6 pages, A4). This extract is only the SiO2 article.

Page map from the text layer and the page images:

| PDF page | Published page | Contents |
| --- | --- | --- |
| 1–2 | cover | Journal masthead. No article. |
| 3 | 57 | End of the CaO–P4O10 article, including its Fig. 4 and the −210 kJ/mol sentence, then the SiO2 title. The CaO ending is `shornikov-2023-calcium-phosphate`. It is not extracted again. |
| 4 | 58 | SiO2 abstract, body, Fig. 1. Footer: Institute of Experimental Mineralogy, page 58. |
| 5 | 59 | Figs. 2, 3 and 4, and the −35 and −33 kJ/mol sentences. Footer: Experiment in Geosciences 2023 Volume 29 N 1, page 59. |
| 6 | 60 | End of the SiO2 discussion, Fig. 5, references, then the MgO–P4O10 title. The MgO opening is `shornikov-2023-mgo-phosphate-melts`. Fig. 5 belongs to the SiO2 article and is extracted here only. |

Duplicate check: not a duplicate of an already extracted paper. The calcium-phosphate extract is Shornikov and Demidova, pages 55–57, and it already excludes this article. The MgO extract starts at the Golyapa title on page 60. No extract cites Slobodov with this title.

No numeric table is printed. MinerU was not emitted. The −35 and −33 sentences match `pdftotext-layout.txt` and the page-59 image.

## Standard state

Activity rows quote published page 58: the initial values of the Gibbs energies of SiO2 and P4O10 formation are given in the crystalline and liquid state. The article never names one activity reference phase. Each activity `standard_state` ends with "The activity reference phase is not stated."

`reference_state_from_extract` returns unknown because that sentence contains "not stated". The phase is not inferred. Fig. 1 names quartz, tridymite and cristobalite as fields of the Rahman and Jantzen diagram. Those names are not the activity standard, and they are kept out of `standard_state` so a polymorph token cannot be read as one. 1100 K is near the quartz–tridymite transition, but the reference temperature is not stated as the pure oxide in a cell, so the melting-band rule is not applied and the phase stays unknown.

The mixing-energy minima are melt mixing energies. Their reference phase is not stated.

## Prose kept as context

- Model domain 700–2000 K. Semi-empirical model of Shornikov (2019). Published page 57 abstract, repeated on page 58.
- Minimum mixing energy for the SiP2O7 composition, −35 kJ/mol, in the 1100 K discussion. Published page 59. The paper prints SiP2O7 as 2SiO2 · P4O10. Not read off a figure.
- The position of that minimum is maintained, and ΔGm rises to −33 kJ/mol at 1873 K (Fig. 3). Published page 59. Not read off Fig. 3B.
- Vapor set, with no printed partial pressure: Si, Si2, Si3, SiO, SiO2, Si2O2, P, P2, P3, P4, PO, PO2, P2O3, P2O4, P2O5, P3O6, P4O6, P4O7, P4O8, P4O9, P4O10, O, O2, O3, O4.
- Parents (Glushko et al. 1978–1982 for the oxides; the phosphate ΔG° are "calculated from experimental and theoretical data" and are not printed per point). No parent was invented.

Cited transitions, not stored: M-SiP2O7 1380 K, T-SiP2O7 1480 K, C-SiP2O7 melts at 1561 K (Baret et al. 1991). Trisilicon phosphate dissociates at 1392 K (Jantzen et al. 2019). Flemish and Tressler (1991) and Lopatin and Semenov (1995) are cited experiments.

## Figure decisions

600 dpi renders of PDF pages 5 and 6. Axes are labelled. No tick calibration was performed because no coordinate was stored. Numerals on curves are labels. Panel letters are not markers.

Fig. 2 caption: SiO2 (1) and P4O10 (2) activities and mixing energies (3, 4) at 1100 (1–3) and 1873 (4) K. Present study (1–3). Rahman et al. 2013 (4). The vertical axis prints both lg a and a ΔG/RT quantity.

| Marker | Decision | Evidence |
| --- | --- | --- |
| Fig. 2 curves 1, 2, 3 | refused | Continuous calculated lines at 1100 K. The 600 dpi frame contains the curve numerals and no data symbol. |
| Fig. 2 curve 4 | refused | Continuous line. Caption assigns it to Rahman et al. (2013) at 1873 K. It is a mixing energy on the shared axis. |
| Fig. 3A curves 1 and 3 | refused | Continuous calculated SiO2 and P4O10 activities at 1873 K. |
| Fig. 3A curves 2 and 4 | refused | Continuous lines. Caption assigns them to Rahman et al. (2013). |
| Fig. 3B curve 5 | refused | Continuous calculated mixing energy. The printed −33 kJ/mol is the stored prose value. The curve was not digitized. |
| Fig. 3B curve 6 | refused | Continuous line. Caption assigns it to Rahman et al. (2013). |
| Fig. 4 lines 1, 2, 4, 5, 6, 7, 8 | refused | Continuous calculated partial pressures over SiP2O7. Numerals are labels. |
| Fig. 4 symbol 3, open circle | refused | (2004.8, 4601.0), 600 dpi, PDF page 5. Hole centroid. This circle overlaps the next one, so the hole is smaller (area 370 px versus about 760). Centre uncertainty about ±8 px. Caption assigns symbol 3 to Lopatin and Semenov (1995). No pressure stored. |
| Fig. 4 symbol 3, open circle | refused | (2019.0, 4629.1), 600 dpi. Hole centroid, 24 of 24 annulus samples dark. Uncertainty about ±3 px. Cited series. No pressure stored. |
| Fig. 4 symbol 3, open circle | refused | (2059.4, 4619.9), 600 dpi. Same hole test. Cited series. No pressure stored. |
| Fig. 4 symbol 3, open circle | refused | (2121.8, 4617.8), 600 dpi. Same hole test. Cited series. No pressure stored. |
| Fig. 4 symbol 3, open circle | refused | (2189.4, 4574.1), 600 dpi. Rightmost circle, clear of the cluster. Cited series. No pressure stored. |
| Fig. 5A blue curve 3 | refused | Calculated SiO2 activity at 2000 K. No blue marker thicker than the line. |
| Fig. 5A blue curve 6 | refused | Calculated P4O10 activity at 2000 K. Same color check. The open circles nearby are red and black, not this line. |
| Fig. 5A red curves 1 and 4 and their circles | refused | ZnO–P4O10 at 1300 K. Caption assigns them to Shornikov et al. (1996). Not read. |
| Fig. 5A black curves 2 and 5 and their circles | refused | GeO2–P4O10 at 1423 K. Caption assigns them to Stolyarova et al. (1990). Not read. |
| Fig. 5B blue curve 9 | refused | Calculated SiO2–P4O10 mixing energy at 2000 K. Continuous. No markers on the blue line. |
| Fig. 5B red curve 7 and its circles | refused | Shornikov et al. (1996) mixing energy at 1300 K. Not read. |
| Fig. 5B black curve 8 and its circles | refused | Stolyarova et al. (1990) mixing energy at 1423 K. Not read. |
| Fig. 1 | refused | Phase diagram of Rahman et al. (2013) and Jantzen et al. (2019). |

No point was given a value. Read uncertainty is not stated because nothing was read.

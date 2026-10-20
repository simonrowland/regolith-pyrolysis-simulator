# Shornikov 2025 Li2O–SiO2 melts — extraction audit

Source id `shornikov-2025-lithium-silicate-melts`. Title on published page 58 (PDF page 2): Thermodynamic properties of the Li2O–SiO2 melts. Experiment in Geosciences 2025, volume 31, number 1, pages 58–61. Author S. I. Shornikov, sergey.shornikov@gmail.com. PDF page 1 is the issue cover. No DOI is printed. PDF page 2 opens with the end of an anorthosite-fluid paper (its Fig. 2 and conclusion). That paper is not extracted. PDF page 5 carries this article's Fig. 4 and then the start of the Li2O–P4O10 article, which is extracted separately.

## Duplicate verdict

Not a duplicate. The title, venue, year, and pages were compared with every extract that cites Shornikov or Yakovlev. Neighbours that are different papers: `shornikov-2014-sodium-silicate-model` (Na2O–SiO2, Experiment in Geosciences 2014), `shornikov-2013` is cited here but is the K2O–SiO2 note, and `baborak-2023-alkali-silicate-levitation-evaporation` measures Li2O–SiO2 glass beads by levitation, not this model note. No extract has this title or these pages.

## Tables

MinerU content list types are text, image, chart, list, header, footer, page number. There is no table block. `mineru_table_draft.py emit` was not run. pdftotext-layout agrees: no numeric data table.

## Standard state

Activity rows quote published page 59: Gibbs energies of the simple oxides are taken in the crystalline and liquid states. The paper does not say which phase is the activity reference of its own curves. The prose was not written as "pure liquid SiO2" or "pure solid SiO2", because the paper does not choose one phase. Migration should leave the reference unknown. It is not inferred. The phase-diagram caption names β-quartz, tridymite and cristobalite as fields of Fig. 1. Those names are not the activity reference.

The same page says Schultz et al. (1986) normalized their EMF data to Li2SiO3 lithium metasilicate. That is the cited experiment's reference, a compound, not this model's stated standard. Schultz's points are not re-extracted. A compound reference is outside the pure-oxide mapper. See the report amendment. No row in this extract stores a number against it.

## Caption temperature conflict

Fig. 3's caption, published page 60, says two incompatible things. The opening clause puts series 1 at 1473 K (Schultz) and series 2 at 1600 K (Ikeda). The closing clause says "at 1600 (1) and 1673 (2–8) K" and never mentions series 9. The body says this study's Li2O activities (Fig. 3A) are at 1673 K, and that they are compared with experiments at 1473 and 1600 K. No point was digitized, so the conflict did not have to be resolved into a value. Series 9 has no T_range_K because the caption does not assign it one.

## Other prose

Published page 60 says the SiO2 activity has not been experimentally determined. The mixing sentence on that page prints "Li2O–SiO3". The title, abstract and Fig. 4 caption print Li2O–SiO2. The typo was not silently corrected.

## Render checks

600 dpi PNG renders of PDF pages 2–5. Figure decisions use pages 3, 4 and 5 (published 59, 60 and 61).

## Figure decisions

This-study series are continuous strokes. No labelled-tick calibration was applied, because no marker centre was read. Literature symbols were checked against the caption before refusal. Fig. 3A's filled and open circles are series 1 and 2 in the plot, not a detached legend.

| figure | series | what | decision |
| --- | --- | --- | --- |
| 1 | regions 1–19 | phase relations after Claus et al. (1996) and Lee et al. (2025) | refused: region diagram, not a point series |
| 2A | 1, 2, 3 | Nakagawa et al. (1981) and Asano and Nakagawa (1989); Ikeda et al. (1982); Penzhorn et al. (1988). Points, some joined by lines, error bars | refused: cited measurement |
| 2A | 5 stroke | this study, p(Li) over Li2SiO3 | refused: continuous calculated stroke, no marker centre |
| 2B | 4, 6, 7 | the same cited mass-spectrometric papers over Li4SiO4, plus Ihle et al. (1989) on series 7 | refused: cited measurement |
| 2B | 8 stroke | this study, p(Li) over Li4SiO4 | refused: continuous calculated stroke, no marker centre |
| 3A | 1 filled circles | Schultz et al. (1986) EMF | refused: cited measurement. Caption temperature 1473 K in the opening clause, 1600 K in the closing clause |
| 3A | 2 open circles | Ikeda et al. (1982) | refused: cited measurement at 1600 K in the opening clause |
| 3A | 3, 4, 5 strokes | Charles (1967), Konar et al. (2017), De Abreu and Fabrichnaya (2024) | refused: other calculations. Series 5 lies below the others, as the body says |
| 3A | 6 stroke | this study, Li2O activity | refused: continuous calculated stroke. Body temperature 1673 K. Not digitized |
| 3B | 7, 8 strokes | Callow (1950) and Charles (1967) | refused: other calculations. Panel B has no symbols |
| 3B | 9 stroke | this study, SiO2 activity | refused: continuous calculated stroke. Caption assigns no temperature to series 9 |
| 4 | 1, 2, 3 strokes | Shornikov (2013), Shornikov (2014), Charles (1967) at 1673 K | refused: other calculations |
| 4 | 4 stroke | this study, mixing energy at 1673 K | refused: continuous calculated stroke; not an activity point |

No point was admitted. Zero numeric activity or pressure observations.

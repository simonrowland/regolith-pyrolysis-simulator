# Shornikov 2025 Li2O–P4O10 melts — extraction audit

Source id `shornikov-2025-lithium-phosphate-melts`. Title on published page 61 (PDF page 2): Thermodynamic properties of the Li2O–P4O10 melts. Experiment in Geosciences 2025, volume 31, number 1, pages 61–64. Author S. I. Shornikov, sergey.shornikov@gmail.com. PDF page 1 is the issue cover. No DOI is printed. PDF page 2 also carries the last page of the Li2O–SiO2 article (its Fig. 4 and references). That article is not extracted here.

## Duplicate verdict

Not a duplicate. The title, venue, year, and pages were compared with every extract that cites Shornikov or Yakovlev. No extract cites Li2O–P4O10. The sodium-phosphate note in the same journal (Shornikov, 2024a, pages 67–70) is a different system and is extracted separately. The abstract says the Li2O–P4O10 calculations are performed for the first time.

## Tables

MinerU content list types are text, image, chart, equation, list, header, footer, page number, ref text. There is no table block. `mineru_table_draft.py emit` was not run. pdftotext-layout agrees: no numeric data table. Reaction (1), [LiPO3] = (LiPO3), is a scheme, not a table of pressures.

## Standard state

Activity rows quote published page 62: Gibbs energies of the simple oxides are taken in the crystalline and liquid states. The paper does not say which phase is the activity reference. Migration should leave the reference unknown. It is not inferred.

## Cited numbers that are not this study

Wagman et al. (1981) mixing enthalpies at 298 K, −272 to −255 kJ/mol, are cited estimates. Congruent melting temperatures 938, 1158 and 1498 K, and the ≤400 K dissociation of Li5P3O10 and Li6P4O13, are cited. Li6P4O13 is stored as printed (3Li2O · P4O10) and was not stoichiometrically corrected. The 22–27 mol % P4O10 sentence is a composition range for the mixing-energy minima of the alkali comparison, not a Gibbs-energy point.

## Render checks

600 dpi PNG renders of PDF pages 2–5. Figure decisions use pages 3 and 4 (published 62 and 63).

## Figure decisions

The body says "Fig. 1" for the LiPO3 pressure comparison. The caption of that plot is Fig. 2, and Fig. 1 is the phase diagram. Decisions follow the captions.

| figure | series | what | decision |
| --- | --- | --- | --- |
| 1 | regions 1–15 | phase diagram; body cites Xie et al. (2020) | refused: region diagram, not a point series |
| 2 | 1, 2 | Ratkovsky et al. (1974, 1976), points with an error bar on series 1 | refused: cited measurement |
| 2 | 3, 4 | Alikhanyan et al. (1975) and Steblevsky et al. (1977), points and a guide line, error bar on series 3 | refused: cited measurement; the line joins cited points and is not a this-study series |
| 2 | this study | none in the caption | refused: no this-study series is drawn |
| 3A | 1, 4 | Shornikov (2024), K2O–P4O10 | refused: the other EG note, not this paper |
| 3A | 2, 5 | Shornikov (2024a), Na2O–P4O10 | refused: the sodium-phosphate note, not this paper |
| 3A | 3 stroke | this study, Li2O activity at 1673 K | refused: continuous calculated stroke, no marker centre |
| 3A | 6 stroke | this study, P4O10 activity at 1673 K | refused: continuous calculated stroke, no marker centre |
| 3B | 7, 8 | Shornikov (2024) and Shornikov (2024a) mixing energies | refused: other calculations |
| 3B | 9 stroke | this study, mixing energy at 1673 K | refused: continuous calculated stroke; not an activity point |

No point was admitted. Zero numeric activity or pressure observations. Figure 2's literature markers were not given centres.

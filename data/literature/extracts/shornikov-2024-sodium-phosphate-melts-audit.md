# Shornikov 2024 Na2O–P4O10 melts — extraction audit

Source id `shornikov-2024-sodium-phosphate-melts`. Title on published page 67 (PDF page 2): Thermodynamic properties of the Na2O–P4O10 melts. Experiment in Geosciences 2024, volume 30, number 1, pages 67–70. Author S. I. Shornikov, sergey.shornikov@gmail.com. PDF page 1 is the issue cover. No DOI is printed. Published page 70 continues this article and then starts the K2O–P4O10 note. Only the Na2O–P4O10 article is extracted here.

## Duplicate verdict

Not a duplicate. The title, venue, year, and pages were compared with every extract that cites Shornikov or Yakovlev. The sodium neighbour on this base is `shornikov-2014-sodium-silicate-model` (Na2O–SiO2). The phosphate neighbour is `shornikov-2023-calcium-phosphate` (CaO–P4O10). No extract cites Na2O–P4O10.

## Tables

MinerU content list types are text, image, chart, list, header, footer, page number, ref text. There is no table block. `mineru_table_draft.py emit` was not run. pdftotext-layout agrees: no numeric data table.

## Standard state

Activity rows quote published page 69: Gibbs energies of formation of Na2O and P4O10 are taken in the crystalline and liquid state. The paper does not say which phase is the activity reference. The prose names both phases and does not say "pure liquid" or "pure solid" of one oxide, so migration should leave the reference unknown. It is not inferred.

The NaPO3 activity is an activity of the compound printed as 2Na2O · P4O10. The paper does not state a reference phase for it. No numeric value is stored. A compound reference is outside the pure-oxide Raoultian mapper. See the report amendment.

## Prose bound

Published pages 69–70: PO and PO2 over NaPO3 at 1100–1400 K do not exceed 10^-8 atm. The 600 dpi render of PDF page 4 shows a superscript minus; "atm." is the first word of published page 70 (PDF page 5). pdftotext prints the minus as an en dash (`10–8`). Stored as a bound on the model-scope context row, not as a pressure point.

The mixing-energy minimum is located near the NaPO3–Na4P2O7 eutectic. No kilojoule value is printed. The ">10 orders" and ">130 kJ/mol" sentences compare Malheiros et al. (1993) with Yamaguchi and Goto (1984). They are not this study's results. The body once prints "Huang & Ling, 1989"; the caption and the reference list print Huang and Lin (1989). The cited name was not corrected.

The 45–70 eV electron energy belongs to the cited mass-spectrometric studies.

## Render checks

600 dpi PNG renders of PDF pages 2–5. Figure decisions use pages 3 and 4 (published 68 and 69). The bound uses pages 4 and 5.

## Figure decisions

This-study series are continuous strokes. No labelled-tick calibration was applied, because no marker centre was read. Literature symbols are refused as other papers' measurements. Legend keys inside the Fig. 3 frames were checked before that refusal: they repeat the caption's symbol list and are not extra points.

| figure | series | what | decision |
| --- | --- | --- | --- |
| 1 | regions 1–20 | phase diagram after Xie et al. (2020) | refused: region diagram, not a point series |
| 2A | 1 filled triangles | Kozhina and Shultz (1997), 1073 K | refused: cited measurement |
| 2A | 2 open squares | Huang and Lin (1989), 1173 K | refused: cited measurement |
| 2A | 3 filled circles | Yamaguchi and Goto (1984), 1573 K | refused: cited measurement |
| 2A | 4 open triangles | Steblevsky et al. (1978), 1580 K | refused: cited measurement |
| 2A | 5 open circles | Malheiros et al. (1993), 1673 K | refused: cited measurement |
| 2A | 6, 7 strokes | Xie et al. (2020) at 1073 and 1673 K | refused: another calculation, not this paper |
| 2A | 8, 9, 10, 11 strokes | this study, Na2O activity, 1073, 1173, 1673, 1873 K | refused: continuous calculated strokes, no marker centre |
| 2B | 1, 3, 5 symbols | cited Na2O-panel authors on the P4O10 panel | refused: cited measurement |
| 2B | 2 stroke | Huang and Lin (1989) | refused: cited work drawn as a stroke, no separable centres |
| 2B | 8, 10, 11 strokes | this study, P4O10 activity | refused: continuous calculated strokes |
| 2B | 9 | this-study temperature 1173 K | refused: not labelled on panel B; nothing to read |
| 2C | 2, 3, 4, 5 symbols | cited NaPO3 activities | refused: cited measurement |
| 2C | 10 stroke | this study, NaPO3 activity at 1673 K | refused: continuous calculated stroke |
| 2C | 8, 9, 11 | this-study temperatures | refused: not labelled on panel C; nothing to read |
| 2D | 1, 2, 3, 5 symbols | cited mixing energies | refused: cited measurement |
| 2D | 8, 9, 10, 11 strokes | this study, mixing energy | refused: continuous calculated strokes; not an activity point |
| 3A | 1–5, 6, 7, 9 symbols | Steblevsky, Alikhanyan, Ratkovsky, Malheiros, Rudny | refused: cited measurement |
| 3A | 12–16 strokes | this study: NaPO3, Na, O2, PO, PO2 | refused: continuous calculated strokes |
| 3A | legend glyphs | frame key for series 3, 5, 7, 9 | refused: legend glyphs, not data |
| 3B | 3, 5, 7, 8, 10, 11 symbols | cited pressures over Na3PO4 | refused: cited measurement |
| 3B | 12, 13, 14 strokes | this study NaPO3, Na, O2 | refused: continuous calculated strokes |
| 3B | 15, 16 | PO and PO2 | refused: not labelled on panel B; the body places them over NaPO3 |
| 3B | legend glyphs | frame key | refused: legend glyphs, not data |

No point was admitted. Zero numeric activity or pressure observations.

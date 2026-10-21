# Shornikov 2024 FeO–P4O10 melts — extraction audit

Source id `shornikov-2024-iron-phosphate-melts`. Title on published page 73 (PDF page 2): Thermodynamic properties of the FeO–P4O10 melts. Experiment in Geosciences 2024, volume 30, number 1, pages 73–76. Author S. I. Shornikov, sergey.shornikov@gmail.com. PDF page 1 is the issue cover. No DOI is printed.

## Duplicate verdict

Not a duplicate. The title, venue, year, and pages were compared with every extract that cites Shornikov or Yakovlev. The only phosphate extract on this base is `shornikov-2023-calcium-phosphate` (CaO–P4O10, Experiment in Geosciences 29(1), 55–57). No extract cites FeO–P4O10.

## Tables

MinerU content list types are text, image, chart, equation, list, header, footer, page number. There is no table block. `mineru_table_draft.py emit` was not run. pdftotext-layout agrees: no numeric data table.

## Standard state

Activity rows quote published page 74: the model adopts Gibbs energies of FeO and P4O10 in the crystalline and liquid state. The paper does not say which phase is the activity reference. The prose does not name one pure liquid or pure solid endmember, so migration should leave the reference unknown. It is not inferred. Both phases are named, and the plotted temperature is not stated as a pure-oxide measurement in the cell.

## Prose bound

Published page 75: the Fe partial pressure, and FePO2 and FePO3, does not exceed 10^-12 atm at 1275–1525 K. The 600 dpi render of PDF page 4 shows a superscript minus. pdftotext prints the minus as an en dash (`10–12`). Stored as a bound on the model-scope context row, not as a pressure point. The mixing-energy minimum is located near the Fe2P2O7–FeP2O6 eutectic. No kilojoule value is printed.

## Render checks

600 dpi PNG renders of PDF pages 2–5. Figure decisions use pages 3 and 4 (published 74 and 75).

## Figure decisions

This-study series are continuous strokes. No labelled-tick calibration was applied, because no marker centre was read. Literature symbols are refused as other papers' measurements. Legend keys inside the frames were checked before that refusal.

| figure | series | what | decision |
| --- | --- | --- | --- |
| 1 | regions 1–17 | phase diagram compiled from cited melting points | refused: region diagram, not a point series |
| 2A | 1 filled circles | Ban-ya and Watanabe (1977) FeO activity | refused: cited measurement, not this paper |
| 2A | 2 red stroke | this study, FeO activity | refused: continuous calculated stroke, no marker centre |
| 2B | 3 open circles | Kambayashi et al. (1985) and Ohara et al. (1987) | refused: cited measurement |
| 2B | 4 filled circles | same mass-spectrometric pair, second series | refused: cited measurement |
| 2B | 5 red stroke | this study, P4O10 activity | refused: continuous calculated stroke, no marker centre |
| 2B | legend dots | frame key for series 1 and 3 | refused: legend glyphs, not data |
| 3A | 1, 2 | Lopatin (1995) PO and PO2, with one error bar on series 2 | refused: cited measurement; strokes, no separable centres |
| 3A | 3, 4, 5, 6, 7, 9 | this study PO, PO2, P2O3, P2O4, P4O9, O2 | refused: continuous calculated strokes |
| 3B | 1, 2 | Lopatin (1995), error bar on series 1 | refused: cited measurement |
| 3B | 3, 4, 5, 6, 8, 9 | this study | refused: continuous calculated strokes |
| 4A | 1–6 | this study p(Fe), p(PO), p(PO2), p(P4O10), p(O), p(O2) at 1673 K | refused: continuous calculated strokes |
| 4B | 7, 8, 9 | this study a(FeO), a(P4O10), mixing energy at 1673 K | refused: continuous calculated strokes |

No point was admitted. Zero numeric activity or pressure observations.

# Shornikov and Yakovlev (2023): extraction audit

Source: *Experimental study of evaporation of the Ca–Al–inclusions of chondrite melts*, *Experiment in Geosciences* 29(1), 8–10 (2023). The source PDF SHA-256 is `72aed8e0b15a010ea65269927fc99d061c14444d2412cf8d4be60f1f00a40a75`. No DOI is printed.

## Duplicate check

The corpus also contains `shornikov-2023-natural-cai-evaporation`, the authors' 2023 LPSC abstract *Mass Spectrometric Study of Evaporation of Natural CAIs Melts (Types A and B) in Efremovka Chondrite*, 54th Lunar and Planetary Science Conference, abstract 1983. It is a separate publication: its title and venue differ, and its Table 1 contains only A/B compositions. This journal article adds the Murchison column and cites the earlier comparison data. The two source records were retained separately.

## Table 1

Table 1 has four oxide rows and three sample columns (12 numeric cells). MinerU and `pdftotext-layout.txt` agree on the digits and row/column assignments. The born-digital page was rendered at 600 dpi to verify the table structure and assignments. No value needed hand correction. The emitted cells were regrouped into one unscored initial-composition context record because the prose attributes the typical A/B compositions to Grossman et al. (2000) and Murchison to Diakonova et al. (1979); they are not new measurements of the experimental samples.

| PDF page / row | Oxide | A (wt. %) | B (wt. %) | M (wt. %) | Attribution | Evidence |
| --- | --- | ---: | ---: | ---: | --- | --- |
| 4 / 1 | CaO | 36.2 | 27.6 | 4.4 | A/B: Grossman et al. (2000); M: Diakonova et al. (1979) | MinerU + pdftotext; row/column assignment checked on 600 dpi render |
| 4 / 2 | MgO | 5.0 | 11.4 | 47.2 | A/B: Grossman et al. (2000); M: Diakonova et al. (1979) | MinerU + pdftotext; row/column assignment checked on 600 dpi render |
| 4 / 3 | Al2O3 | 38.3 | 32.3 | 2.5 | A/B: Grossman et al. (2000); M: Diakonova et al. (1979) | MinerU + pdftotext; row/column assignment checked on 600 dpi render |
| 4 / 4 | SiO2 | 20.5 | 28.7 | 45.9 | A/B: Grossman et al. (2000); M: Diakonova et al. (1979) | MinerU + pdftotext; row/column assignment checked on 600 dpi render |

The `mineru_table_draft` mapping emits all 12 cells. Its `compare` command reports zero candidates because the extract intentionally keeps the table in `context[]`; the header-only output is committed. The source test pins four printed values and the row and cell counts.

## Text and figure context

The extract records the printed CAI age, modal mineral proportions, Efremovka CV3 sample identity, Murchison CM2 comparison, KEMS method and detected ions, and the article's qualitative evaporation results. The Murchison volatility ranges are this article's own reading of its CAI and Murchison curves on PDF page 5 / published page 10; they remain separate in `shornikov_2023_murchison_comparison_context` with `method_class: author_estimate`. Its locator records that Yakovlev et al. (1987) supplied the Murchison data (PDF page 4); the figure series carry that source attribution. The extract does not reconstruct a complete melt composition: amounts for the listed additional oxides are typed unknown because the article does not publish them.

Figures 1–3 are recorded only under the relevant species sibling `context[]` containers. Their typed quantity and identity, phase, temperature, printed axes, captions, panels, series, marker counts, locators, and attributions are retained. Marker counts were tallied from the 600 dpi page renders; no coordinates or figure values were read. Every figure record says `digitisation: deferred to a figure pass` and contains no numeric points.

| Figure series | Marker counts |
| --- | --- |
| 1: CAI A / CAI B / Murchison | 31 / 26 / 27 |
| 2, Ca(g), panels A / B / C | 14 / 12 / 9 |
| 2, Mg(g), panels A / B / C | 12 / 11 / 13 |
| 2, Al(g), panels A / B / C | 8 / 11 / 9 |
| 2, SiO(g), panels A / B / C | 16 / 20 / 10 |
| 3: CAI A / CAI B / Murchison | 19 / 24 / 17 |

## Migration and expected store delta

The isolated migration reports 0 observations, 12 contexts, 0 hard issues, and 0 unavailable observations. Expected battery-store delta: 0 scored observations; the twelve unscored context records remain available as source context. No battery store regeneration is committed.

# Schneider et al. (1980), NBSIR 80-2059: Figure 2 audit

## Scope and source reads

This repair adds only measured Figure 2 marker centers in the requested 2116–2160 K window. The existing Figure 2 record remains `figure_only`; Figures 1 and 3 and the remainder of Figure 2 remain undigitized. PDF page 16 is published page 12.

The source extract already records the prior full-page review: all 66 pages were rendered at 220 dpi, all four table CSVs and carried printed numbers/signs/exponents/units/locators were rechecked against page images, and missing prose facts were retained in context. That work was retained unchanged. The pdftotext-layout read confirms the Figure 2 caption and the prose describing its source and computed bulk compositions; it does not provide plotted marker coordinates. MinerU material available for this source covers PDF pages 2–4, 18–19, and 66 and contains no tables. `mineru_table_draft.py convert` found 0 tables; the committed presence-only comparison is therefore empty and makes no claims about these figure cells.

## Figure 2 cells checked against the render (method b)

The legend and plotted markers were read from the PDF p. 16 render at 250 dpi. The seven composition ranges below match the printed curve legend, in order A–G. Marker centers were digitized from the plotted symbols; horizontal and logarithmic vertical axes were calibrated from the printed ticks. Temperatures and pressures are approximate graphical readings, not coordinates inferred from prose.

| Cell | PDF page / curve row | Printed or digitized value | Check |
|---|---|---|---|
| Legend composition, curve A | 16 / legend A | 28.9–28.7 wt% K2O | (b) render |
| Legend composition, curve B | 16 / legend B | 26.1–25.2 wt% K2O | (b) render |
| Legend composition, curve C | 16 / legend C | 20.6–20.1 wt% K2O | (b) render |
| Legend composition, curve D | 16 / legend D | 14.9–14.5 wt% K2O | (b) render |
| Legend composition, curve E | 16 / legend E | 12.5–12.2 wt% K2O | (b) render |
| Legend composition, curve F | 16 / legend F | 11.6–11.4 wt% K2O | (b) render |
| Legend composition, curve G | 16 / legend G | 10.8–10.7 wt% K2O | (b) render |
| Marker D-1, center (215, 777) | 16 / curve D | ~2157 K; ~3.0×10⁻⁵ atm | (b) render |
| Marker D-2, center (273, 883) | 16 / curve D | ~2116 K; ~1.9×10⁻⁵ atm | (b) render |
| Marker G-1, center (260, 1190) | 16 / curve G | ~2129 K; ~1.0×10⁻⁵ atm | (b) render |

Count: 3 measured markers in the 2116–2160 K interval. No other measured marker center in Figure 2 falls within that interval. The composition values are series bounds printed in the legend, not pointwise compositions.

The seven ranges and three marker rows are also pinned by `tests/test_schneider_1980_figure2_digitization.py`. The unreported total chamber/background pressure remains `unknown/not_published`; the figure's sample vapor pressure is not an apparatus pressure. The measured points are stored as `figure_only` evidence but typed-refused for scoring: the source gives curve-level K2O ranges, not pointwise full melt compositions, and the current schema has no identity form for that range. The printed ranges remain attached to their curve points.

## Isolated migration

The real battery migrator was run on an isolated tree containing this extract only: 3 observations, 0 hard issues, 0 unavailable values, and 3 typed refusals. All three observations carry their digitized `temperature_K` in identity and `point_conditions`; each keeps an explicit unknown pointwise composition condition with the Figure 2 locator. Identity composition remains unknown because the paper reports only a curve-level K2O range, and total pressure and fO2 remain unknown because neither is printed for the experiment. The measured vapor pressures were converted from the printed atm values to Pa by the migrator.

Mutation check: changing the first pinned pressure from `3.0e-5` to `3.1e-5` made the Figure 2 regression test fail on that point; the extract was restored afterward.

Expected regenerated store delta: one work, two experiments from the retained source extract, and three typed-refused figure-only observations. No battery-store regeneration was committed.

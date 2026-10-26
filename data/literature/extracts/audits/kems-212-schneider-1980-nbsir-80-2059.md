# Schneider et al. (1980), NBSIR 80-2059: Figure 2 audit

## Scope and source reads

This repair reads the three high-temperature Figure 2 marker centres on PDF page 16. The existing Figure 2 record remains `figure_only`; Figures 1 and 3 and the remainder of Figure 2 remain undigitized. PDF page 16 is published page 12. The 2116–2160 K window is applied after the read. It does not move a centre.

The source extract already records the prior full-page review: all 66 pages were rendered at 220 dpi, all four table CSVs and carried printed numbers/signs/exponents/units/locators were rechecked against page images, and missing prose facts were retained in context. That work was retained unchanged. The pdftotext-layout read confirms the Figure 2 caption and the prose describing its source and computed bulk compositions; it does not provide plotted marker coordinates. MinerU material available for this source covers PDF pages 2–4, 18–19, and 66 and contains no tables. `mineru_table_draft.py convert` found 0 tables; the committed presence-only comparison is therefore empty and makes no claims about these figure cells.

## Figure 2 cells checked against the render (method b)

The legend and plotted markers were read from the PDF p. 16 render at 500 dpi. The seven composition ranges below match the printed curve legend, in order A–G. Marker centres are ink centroids. The hotter triangle excludes the pressure-axis stroke it touches. The 10^4/T axis is the line through the labeled 5.0 tick at x=891.64 and the 7.0 tick at x=3449.84 (the 6.0 tick at x=2169.14 lies 1.6 px off that line). Pressure is log-interpolated between the printed ticks on either side of each marker. Temperatures and pressures are those centre readings, not coordinates inferred from prose and not values snapped to a gridline.

| Cell | PDF page / curve row | Printed or digitized value | Check |
|---|---|---|---|
| Legend composition, curve A | 16 / legend A | 28.9–28.7 wt% K2O | (b) render |
| Legend composition, curve B | 16 / legend B | 26.1–25.2 wt% K2O | (b) render |
| Legend composition, curve C | 16 / legend C | 20.6–20.1 wt% K2O | (b) render |
| Legend composition, curve D | 16 / legend D | 14.9–14.5 wt% K2O | (b) render |
| Legend composition, curve E | 16 / legend E | 12.5–12.2 wt% K2O | (b) render |
| Legend composition, curve F | 16 / legend F | 11.6–11.4 wt% K2O | (b) render |
| Legend composition, curve G | 16 / legend G | 10.8–10.7 wt% K2O | (b) render |
| Marker F hot, centre (444.27, 1547.10) at 500 dpi | 16 / curve F | 2150.4 K; 3.08×10⁻⁵ atm | (b) render |
| Marker F cool, centre (550.65, 1773.22) at 500 dpi | 16 / curve F | 2112.6 K; 2.04×10⁻⁵ atm | (b) render |
| Marker G, centre (543.46, 2385.50) at 500 dpi | 16 / curve G | 2115.2 K; 6.68×10⁻⁶ atm | (b) render |

The triangle line ends on the printed letter F. The filled-circle line ends on G. Curve F's legend is 11.6–11.4 wt% K2O. Curve G's legend is 10.8–10.7 wt% K2O. The G centre is 222 px below the 10⁻⁵ tick, at 6.68×10⁻⁶ atm, not on that gridline.

Count of centres inside 2116–2160 K: 1 (the hotter F triangle, 2150.4 K). The cooler F centre is 2112.6 K and the G centre is 2115.2 K. Reading uncertainty is about 1 K, and about 1.5 K for the hotter triangle because it touches the axis, and about 1 percent in pressure. Those uncertainties are not used to move a centre into the window. The composition values are series bounds printed in the legend, not pointwise compositions.

The seven ranges and the three marker rows are pinned by `tests/test_schneider_1980_figure2_digitization.py`, which recomputes temperature and pressure from the tick and marker pixels above. A series-G pressure of 1.0×10⁻⁵ atm fails that calibration. The unreported total chamber/background pressure remains `unknown/not_published`; the figure's sample vapor pressure is not an apparatus pressure. The measured points are stored as `figure_only` evidence but typed-refused for scoring: the source gives curve-level K2O ranges, not pointwise full melt compositions, and the current schema has no identity form for that range. The printed ranges remain attached to their curve points.

## Isolated migration

The real battery migrator was run `write=False` on an isolated tree containing this extract only (`Migrator(root, index={}, aliases={})`): 1 work, 2 experiments, 3 observations, 1 queue row, 0 hard issues. Each observation carries its digitized temperature in identity and in `point_conditions.temperature_K` with the Figure 2 locator: 2150.4 K, 2112.6 K, and 2115.2 K. Each also keeps an explicit unknown pointwise composition condition with that same figure locator. Identity composition, fO2, and total pressure remain unknown. Vapor pressures convert from atm by 101325 Pa/atm to 3.12081 Pa, 2.06703 Pa, and 0.676851 Pa. All three admissions are rejected: no pointwise full melt composition is printed. The one queue row is "source does not state method". No temperature-range midpoint was queued, because `T_range_K` is the single in-window centre.

Mutation check: setting the series-G observation pressure to `1.0e-5` atm failed the calibration assertion against the marker-centre pressure 6.67885e-6 atm. Rounding the cooler F `T_K` from 2112.6 to 2116 failed the centre assertion by 3.36 K. The extract was restored after each mutation.

Expected regenerated store delta: one work, two experiments, three typed-refused figure-only observations, and one method queue row. No battery-store regeneration was committed.

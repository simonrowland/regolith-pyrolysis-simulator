# Omori & Sanbongi (1961) figure calibration audit

Source PDF pages 2–3 were rendered at 350 dpi. Fig. 3 coordinates below use the reread crop. Fig. 5 and Fig. 6 retain their original calibration and pixel convention. Axis coordinates use linear interpolation between printed ticks. Logarithmic activity ordinates are retained as printed and migrated through `printed_form: log10`.

| Figure | physical PDF page | published page | x ticks (crop px → printed value) | y ticks (crop px → printed value) | crop | overlay |
|---|---:|---:|---|---|---|---|
| 3 | 2 | 137 | 154.5→0.35, 280.5→0.40, 404.5→0.45, 524.5→0.50, 647.5→0.55, 772→0.60 | 50.5→0, 142.5→-0.2, 240→-0.4, 322.5→-0.6, 418→-0.8, 511.5→-1.0, 604.5→-1.2, 699.5→-1.4 | 850×785 at page offset (350, 2300) | marker centers in the cell audit |
| 5 | 3 | 138 | 252→0, 421→0.05, 592→0.1, 768→0.15, 944→0.2 | 159→-1, 328→-2, 496→-3, 665→-4 | `omori-1961-fig5-crop.png` | `omori-1961-fig5-overlay.png` |
| 6 | 3 | 138 | 265→2, 331→1.8, 396→1.6, 461→1.4, 526→1.2, 593→1, 658→0.8, 724→0.6, 779→0.4 | 177→-1, 341→-2, 504→-3, 667→-4 | `omori-1961-fig6-crop.png` | `omori-1961-fig6-overlay.png` |

The figure composition convention is taken from Fig. 2, whose axes are marked w%. Fig. 3 plots N_SiO2 with fixed Al2O3/MgO mass-percent series; the four-oxide mass composition was closed by difference for CaO. Fig. 5 plots N_Al2O3 and prints adjacent N_SiO2 percent labels; the plotted marker centers, rather than those text labels, set the N_Al2O3 and log-activity reads. MgO is its symbol-coded mass-percent series. Fig. 6 is the same 18 Al2O3 samples re-plotted against the stated mole ratio `(N_CaO+N_MgO)/N_SiO2`; its ratio was recalculated from each Fig. 5 mass-percent composition using oxide formula weights, and all 18 markers align on the calibrated Fig. 6 axes. It is one observation per Fig. 5 sample, with Fig. 6 as the secondary locator.

Per-point values include crop-pixel coordinates and conservative axis-read uncertainty in the extract. Axial labels and all contour/series numerals were inspected on the crops. Fig. 5–6 third-party comparison curves are not observations.

## Fig. 3 reread

Crop coordinates start at the top left with x increasing right and y increasing down. We used piecewise linear interpolation between successive ticks because their spacing varies in the scan. The interpolation has zero residual at the tick centers by construction. The independent diagnostic fits of a single straight line give these signed residuals (fit minus printed value, in tick order):

- x slope 0.000406157 per pixel, intercept 0.286577024. Residuals −0.000672, +0.000504, +0.000868, −0.000394, −0.000436, +0.000130.
- y slope −0.002163755 per pixel, intercept 0.108433120. Residuals −0.000837, +0.000098, −0.010868, +0.010622, +0.003983, +0.001672, +0.000443, −0.005114.

The fit residuals and widths of resolved markers fit inside the retained read uncertainties of 0.006 mole fraction and 0.06 log10 activity. Coordinates carry four places after the decimal point so the stored reads preserve the tick interpolation.

| Al₂O₃ mass % | MgO mass % | Trace | Marker | Prior rows | Distinct markers |
|---:|---:|---|---|---:|---:|
| 20 | 30 | dashed | filled square | 3 | 3 |
| 20 | 20 | dashed | filled downward triangle | 4 | 3 |
| 20 | 10 | dashed | filled upright triangle | 4 | 5 |
| 20 | 0 | dashed | filled circle | 5 | 4 |
| 10 | 30 | solid | open square | 4 | 3 |
| 10 | 20 | solid | open downward triangle | 4 | 4 |
| 10 | 10 | solid | open upright triangle | 4 | 3 |
| 10 | 0 | solid | open circle | 5 | 4 |

The legend keys trace style and labels the MgO branches with leaders. Marker shapes come from the glyphs along those labeled branches. Row identifiers preserve established sample identity even when their suffix differs from left-to-right rank.

- `previously_omitted_marker` added `al20_mg10_05`, the leftmost filled upright triangle at crop pixel (295.5, 470.5).
- `no_distinct_marker` retired `al20_mg20_03` and `al20_mg00_03`. The candidate sites show crossing strokes or another series' symbol. The surviving right-side markers are `al20_mg20_04` and `al20_mg00_04`.
- `no_distinct_marker` retired `al10_mg30_04`, `al10_mg10_02`, and `al10_mg00_04`. The square curve has three open squares. The apparent mark just above the leftmost filled upright triangle is an overlapping stroke and has no open glyph. The third open circle is the ring at (548.0, 254.5), and the next open circle is the endpoint at (678.5, 155.0).

For `al20_mg30_03`, the measured coordinate 0.4712 and the nominal 20% Al₂O₃ and 30% MgO series would close at −0.18 wt% CaO. The feasible boundary at zero CaO lies within the x read uncertainty. The extract retains the plotted coordinate and nominal series labels, with the full composition marked unknown at the Fig. 3 locator.

## Fig. 4 retirement

PDF page 2, printed page 137. The 350 dpi crop at page offset (1320, 1720), size 840×715, shows eight smooth curves and their leaders without discrete data markers. The former 33 coefficient rows were curve reads at the former Fig. 3 compositions. On the same activity reference, gamma = a/N. Fig. 4 remains source context. All 33 observations and their fidelity samples were retired.

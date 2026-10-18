# shornikov-2016-cao-mgo-silicate audit

Born-digital Word 2010 PDF, 3 pages. This extract is only Shornikov, S. I., Thermodynamic properties of the CaO–MgO–SiO2 melts, Experiment in Geosciences, 2016, vol. 22, no. 1, pp. 40–42. PDF page 1 is published page 40 and is the end of the preceding Sn abstract. PDF page 3 also starts the following MgAl2O4 spinel abstract. Neither of those abstracts is stored. No DOI is printed.

## Table 1 (PDF page 2, published page 41)

Printed body: 13 solid, 8 liquid, 17 gas. Thirteen empty condensed-phase cells are blank and were skipped. Gas O2 `0.000` is a printed zero and is stored. Gas Ca2 `77.075` is positive and is stored without a minus. The column header is ΔfG°2000, kJ/mole. It is not a powers-of-ten scale factor, and the first body row (CaO) is present in the emit.

MinerU `p002-t02` and pdftotext agree on every stored digit and sign. MinerU writes an ASCII hyphen (U+002D). The page glyph is an en-dash (U+2013). No `needs_render_check` flag. One layout check of the page-2 render against the CSV: row and column assignment matches the printed solid / liquid / gas columns. The observation locator `table: "2"` is the MinerU table index on that PDF (the Sn table is index 1 and belongs to the other abstract). The printed caption is Table 1, which is the temperature locator and the fidelity samples.

`mineru_table_draft.py compare` reported `matched=0 no-match=0 mismatch=0 presence-only=0 total=0`. Emit stores the Gibbs numbers on `values.value`. The comparator reads `values.points[].value` or `pressure_atm`, so it saw no table points. The digit agreement above is the cell audit. Tool defect, not fixed on this branch.

Every liquid row stores `column_header` "Gas phase  $\Delta_f G^{\circ}_{2000},$  kJ/mole" while `phase` is `l`. The value and the phase are the liquid column. Emit copies that header from the shared MinerU header mapping, and re-emission reproduces it. t-1320 fixes the emitter. These eight cells were not hand-edited and the table was not re-emitted.

The caption says calculated in the present study, formation from elements at 2000 K. The text cites Glushko et al. 1978–1982, Bale et al. 2009, and Shornikov and Yakovlev 2015. Those parents are not stored, so the rows are quoted_attributed. Standard pressure, the reaction, and formation_elements are not printed and are not inferred. The prose formula "diopside CaMgSiO4" is stored as printed. CaMgSi2O6 is a separate printed row.

## Figure 1 calibration

Embedded JPEG on PDF page 2, native 1353×540. Placement `cm 487.35 0 0 194.4 70.9 97.42` on a 595.32×841.92 pt page. Page pixels are that matrix times 600/72, with y measured down from the top of the page. Read 1 is on the native JPEG. Read 2 is on a fresh 600 dpi render of the page. The pinned page coordinates are that conversion of the stored embedded pixels.

The ordinate is lg a_i, which is log10(activity). Fitted maps, value = slope·pixel + intercept:

| axis | ticks (pixel = label) | slope | intercept | max residual |
| --- | --- | --- | --- | --- |
| y, log10(activity) | 14 = 0, 132 = −1, 250 = −2, 369 = −3 | −0.008453049136 | 0.116645647180 | 0.0034 dex |
| panel a x, mole % CaO | 91 = 0, 203 = 10, 315 = 20, 427 = 30, 539 = 40, 651 = 50 | 0.08928571 | −8.125000 | 0.00 mole % |
| panel b x, mole % SiO2 | 768 = 30, 848 = 35, 928 = 40, 1008 = 45, 1088 = 50, 1168 = 55, 1248 = 60, 1329 = 65 | 0.06243483 | −17.939504 | 0.036 mole % |

The x-axis is below the −3 tick and is not that tick. Panel a is the 2MgO·3SiO2–CaO join. Panel b is the CaO·MgO–SiO2 join. A half pixel on the activity axis is 0.0042 dex. No stored uncertainty uses that width: the stored half-difference is the unrounded ordinate gap, and the key is omitted when that half formats as 0.000.

## Figure 1 marker reads

Symbols 1–3 are open squares of the present study. The caption and the abstract identify them as the calculated activities. Those six series stay as unavailable activity records at 1873 K, phase liquid, with the quantity, species, caption and locator, and with no numeric points. Symbols 4–6 are filled circles from Rein and Chipman, 1965. Only those are stored as numbers.

Read 1 of a retained filled circle is the centre already stored. Read 2 is a visual centre of that filled disk on a fresh 600 dpi page crop. The crop is 72 page pixels, the stored page coordinate sits at offset (24, 46) rather than at the crop centre, and the view is 3× nearest-neighbor with a 10 px grid and no cross. The centre is the centre of the round disk, not the stroke that leaves it, and not a centroid of a mask. Panel b MgO at activity 0.5 is page (3247.5, 4730.0), the midpoint of the red disk; the stroke that enters from the upper left is not that centre. `page_600dpi_px_read_2` is that visual centre. `embedded_px_read_2` is the inverse of the same page placement, so the ordinate stays on the embedded-pixel axis fit. The five markers added in review keep their embedded distance-transform peak as read 1; their read 2 is the same visual page centre. The largest activity gap is 0.0117 dex. No pair exceeds 0.02 dex. No read uncertainty is 0. A half-difference that formats as 0.000 is omitted.

Stored once as linear activity at 1873 K, phase liquid. 41 experimental points. Evidence class of the stored points is figure_only. The model records are typed refusals, `unavailable_reason: model_curve_not_sampled`.

| symbol | marker | who | panel a | panel b | stored |
| --- | --- | --- | --- | --- | --- |
| 1 | black open square | present study, model | visible, not counted | visible, not counted | unavailable, no points |
| 2 | red open square | present study, model | visible, not counted | visible, not counted | unavailable, no points |
| 3 | blue open square | present study, model | visible, not counted | visible, not counted | unavailable, no points |
| 4 | black filled circle | Rein and Chipman, 1965 | 5 | 5 | numeric |
| 5 | red filled circle | Rein and Chipman, 1965 | 3 | 3 | numeric |
| 6 | blue filled circle | Rein and Chipman, 1965 | 13 | 12 | numeric |

Added experimental centres, embedded pixels: panel a SiO2 (126.0, 14.0) activity 1; panel b SiO2 (1288.0, 13.5) activity 1; panel a CaO (518.0, 176.5) activity 0.042; panel b CaO (928.0, 213.0) activity 0.021. The two silica markers sit on the lg a = 0 tick, so the stored activity is 1.

There is no closed red square at panel a, 20 mole % CaO. That point is not invented. The connecting lines are not sampled. Raw embedded and page pixels are the `embedded_px_read_*` and `page_600dpi_px_read_*` fields on each point.

## Figure 2 calibration

Embedded JPEG on PDF page 3, native 1401×546. Placement `cm 504.7 0 0 196.8 36.85 589.07` on the same page size. Page pixels are that matrix times 600/72, y down from the top of the page.

The ordinate is linear ΔG^m, kJ/mole. The two panels do not share a scale. Fitted maps, value = slope·pixel + intercept:

| axis | ticks (pixel = label) | slope | intercept | max residual |
| --- | --- | --- | --- | --- |
| panel a x, mole % CaO | 107 = 0, 221 = 10, 332 = 20, 444 = 30, 558 = 40, 673 = 50 | 0.08853746 | −9.455829 | 0.15 mole % |
| panel b x, mole % SiO2 | 799 = 30, 881 = 35, 958 = 40, 1043 = 45, 1117 = 50, 1201 = 55, 1284 = 60, 1369 = 65 | 0.06167918 | −19.206035 | 0.31 mole % |
| panel a y, kJ/mole | 28 = −10, 138 = −20, 249 = −30, 360 = −40 | −0.09033379 | −7.497827 | 0.036 kJ/mole |
| panel b y, kJ/mole | 27 = −30, 145 = −35, 264 = −40, 381 = −45 | −0.04233658 | −28.852754 | 0.030 kJ/mole |

## Figure 2 marker reads

Symbols 1–3 are the present-study model at 1873, 1973 and 2073 K. Those six context rows keep the mixing-energy schema gap and have no numeric points. Symbol 4, the black filled circle at 1873 K, is the Rein and Chipman comparison and is stored: 9 points on panel a and 8 on panel b.

Read 1 of a retained filled circle stays the stored centre. Read 2 is the same visual page-crop centre used for Figure 1. The added panel a marker is embedded (502.0, 358.0), between the circles at x = 468 and x = 536, and its two-significant-figure energy is −40 kJ/mole. Stored energies use two significant figures, so a read of −31.35 is stored as −31. The uncertainty is half the unrounded kJ difference, not half the rounded values. The largest gap is 0.108 kJ/mole. Where that half formats as 0.000 the key is omitted. No read uncertainty is 0.

Two enclosed black holes are the counters of the printed digit 4, at about (352, 276) and (1124, 118). They are not markers. Panel b has no black filled circle at 65 mole %.

One further black open square of the 1873 K model series lies past the 65 mole % tick at x = 1369. Its ink spans embedded x 1373–1385 and y 5–17, centroid (1379.3, 10.7), about 13 by 12 px, and a stroke leaves the lower left toward the previous model point. It is not sampled.

17 experimental points, context only, type `figure_mixing_energy`, under species `CaO-MgO-SiO2` because the quantity is the melt on the join and not one oxide. Not stored as delta_fG.

The sentence "by on 4 kJ/mole approximately" (comparison with Shornikov et al., 1997) is one context row, value as printed. It is not a second delta_fG.

## Schema gaps

- No quantity token for the mixing energy ΔG^m. The experimental reads stay context. The model curves stay on those context rows with no numeric points.
- The 4 kJ/mole sentence has the same gap.
- Mole percent has no AmountBasis token. Activity and mixing-energy composition identity stays unknown. The point condition records the reason. Printed mole percents stay on the points.
- Figure 1 does not print the activity standard state, fO2, or total pressure.
- Gibbs formation reaction, formation elements, and standard pressure are not printed. Not inferred.
- Symbols 1–3 are model curves. Activity has a token, and the six series are unavailable records rather than numeric samples. Mixing energy has no token, so the same refusal is an empty point list on the context row.

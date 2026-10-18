# shornikov-2016-cao-mgo-silicate audit

Born-digital Word 2010 PDF, 3 pages. This extract is only Shornikov, S. I., Thermodynamic properties of the CaO–MgO–SiO2 melts, Experiment in Geosciences, 2016, vol. 22, no. 1, pp. 40–42. PDF page 1 is published page 40 and is the end of the preceding Sn abstract. PDF page 3 also starts the following MgAl2O4 spinel abstract. Neither of those abstracts is stored. No DOI is printed.

## Table 1 (PDF page 2, published page 41)

Printed body: 13 solid, 8 liquid, 17 gas. Thirteen empty condensed-phase cells are blank and were skipped. Gas O2 `0.000` is a printed zero and is stored. Gas Ca2 `77.075` is positive and is stored without a minus. The column header is ΔfG°2000, kJ/mole. It is not a powers-of-ten scale factor, and the first body row (CaO) is present in the emit.

MinerU `p002-t02` and pdftotext agree on every stored digit and sign. MinerU writes an ASCII hyphen (U+002D). The page glyph is an en-dash (U+2013). No `needs_render_check` flag. One layout check of the page-2 render against the CSV: row and column assignment matches the printed solid / liquid / gas columns. The observation locator `table: "2"` is the MinerU table index on that PDF (the Sn table is index 1 and belongs to the other abstract). The printed caption is Table 1, which is the temperature locator and the fidelity samples.

`mineru_table_draft.py compare` reported `matched=0 no-match=0 mismatch=0 presence-only=0 total=0`. Emit stores the Gibbs numbers on `values.value`. The comparator reads `values.points[].value` or `pressure_atm`, so it saw no table points. The digit agreement above is the cell audit. Tool defect, not fixed on this branch.

The caption says calculated in the present study, formation from elements at 2000 K. The text cites Glushko et al. 1978–1982, Bale et al. 2009, and Shornikov and Yakovlev 2015. Those parents are not stored, so the rows are quoted_attributed. Standard pressure, the reaction, and formation_elements are not printed and are not inferred. The prose formula "diopside CaMgSiO4" is stored as printed. CaMgSi2O6 is a separate printed row.

## Figure 1 calibration

Embedded JPEG on PDF page 2, native 1353×540. Placement `cm 487.35 0 0 194.4 70.9 97.42` on a 595.32×841.92 pt page. Page pixels are that matrix times 600/72, with y measured down from the top of the page. Reads are on the native JPEG. The pinned page coordinates in the test are that conversion.

The ordinate is lg a_i, which is log10(activity). Major y ticks sit at embedded y 14, 132, 250, 369 for lg a = 0, −1, −2, −3. The fit residual is at most 0.0034 dex. The x-axis is below the −3 tick and is not that tick. Panel a x ticks at 91, 203, 315, 427, 539, 651 are 0, 10, 20, 30, 40, 50 mole % CaO on the 2MgO·3SiO2–CaO join. Panel b x ticks at 768, 848, 928, 1008, 1088, 1168, 1248, 1329 are 30, 35, 40, 45, 50, 55, 60, 65 mole % SiO2 on the CaO·MgO–SiO2 join. X-fit residuals are under 0.04 mole %.

## Figure 1 marker reads

Open squares: read 1 is the centroid of the enclosed hole. Read 2 is the centroid of that square's stroke inside the hole box expanded by 3 px. Filled circles: read 1 is the peak of a radius-4 disk fill. Read 2 is the centroid of the same-colour pixels in a ±7 px window. Neither second read is a fixed offset of the first. `read_uncertainty_dex` is half the unrounded ordinate difference. Where the two reads coincide, the key is omitted rather than stored as zero. No pair exceeds 0.02 dex.

Stored once as linear activity at 1873 K, phase liquid, evidence figure_only. 102 points.

| symbol | marker | who | panel a | panel b |
| --- | --- | --- | --- | --- |
| 1 | black open square | present study | 8 | 12 |
| 2 | red open square | present study | 10 | 12 |
| 3 | blue open square | present study | 11 | 12 |
| 4 | black filled circle | Rein and Chipman, 1965 | 4 | 4 |
| 5 | red filled circle | Rein and Chipman, 1965 | 3 | 3 |
| 6 | blue filled circle | Rein and Chipman, 1965 | 12 | 11 |

There is no closed red square at panel a, 20 mole % CaO. That point is not invented. The connecting lines are not sampled. Raw embedded and page pixels are the `embedded_px_read_*` and `page_600dpi_px_read_*` fields on each point.

## Figure 2 calibration

Embedded JPEG on PDF page 3, native 1401×546. Placement `cm 504.7 0 0 196.8 36.85 589.07` on the same page size. Page pixels are that matrix times 600/72, y down from the top of the page.

The ordinate is linear ΔG^m, kJ/mole. The two panels do not share a scale.

| axis | labels | pixels | max residual |
| --- | --- | --- | --- |
| panel a x, mole % CaO | 0, 10, 20, 30, 40, 50 | 107, 221, 332, 444, 558, 673 | 0.15 mole % |
| panel b x, mole % SiO2 | 30, 35, 40, 45, 50, 55, 60, 65 | 799, 881, 958, 1043, 1117, 1201, 1284, 1369 | 0.31 mole % |
| panel a y, kJ/mole | −10, −20, −30, −40 | 28, 138, 249, 360 | 0.04 kJ/mole |
| panel b y, kJ/mole | −30, −35, −40, −45 | 27, 145, 264, 381 | 0.03 kJ/mole |

## Figure 2 marker reads

Squares and triangles: read 1 is the hole centroid, without morphological closing. Read 2 is the stroke centroid (squares) or the stroke-annulus centroid (triangles). Open circles: read 1 is the peak of a ring template, because several rings are opened by the joining line. Read 2 is the centroid of the red pixels in that ring. Filled circles: disk peak, and the ink centroid in a ±6 px window. Two enclosed black holes are the counters of the printed digit 4, at about (352, 276) and (1124, 118). They are not markers. Panel b has no black square at 65 mole %. Every stored pair differs by less than 0.4 kJ/mole. The uncertainty is half that difference.

87 points, context only, type `figure_mixing_energy`, under species `CaO-MgO-SiO2` because the quantity is the melt on the join and not one oxide. Not stored as delta_fG.

| symbol | marker | T | panel a | panel b |
| --- | --- | --- | --- | --- |
| 1 | black open square | 1873 K | 11 | 12 |
| 2 | red open circle | 1973 K | 11 | 13 |
| 3 | blue open triangle | 2073 K | 11 | 13 |
| 4 | black filled circle | 1873 K | 8 | 8 |

The sentence "by on 4 kJ/mole approximately" (comparison with Shornikov et al., 1997) is one context row, value as printed. It is not a second delta_fG.

## Schema gaps

- No quantity token for the mixing energy ΔG^m. The reads stay context.
- The 4 kJ/mole sentence has the same gap.
- Mole percent has no AmountBasis token. Activity and mixing-energy composition identity stays unknown. The point condition records the reason. Printed mole percents stay on the points.
- Figure 1 does not print the activity standard state, fO2, or total pressure.
- Gibbs formation reaction, formation elements, and standard pressure are not printed. Not inferred.

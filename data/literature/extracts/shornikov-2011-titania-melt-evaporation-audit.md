# shornikov-2011-titania-melt-evaporation audit

Born-digital, 4 PDF pages. No published page number (article NZ6101). Locators use the PDF page.

## Table (one Gibbs-energy table, PDF page 1)

Printed body: 11 solid, 7 liquid, 14 gas. Ten dashed liquid or solid cells are empty and were skipped. O2 solid-reference `0.000` is a printed zero and is stored.

MinerU `p001-t01` and pdftotext agree on every stored digit, sign and exponent. MinerU writes an ASCII hyphen (U+002D). The page glyph is an en-dash (U+2013). No `needs_render_check` flag. One layout check of the 600 dpi page-1 render against the CSV: row and column assignment matches the printed solid / liquid / gas columns.

`mineru_table_draft.py compare` reported `matched=0 no-match=0 mismatch=0 presence-only=0 total=0`. The comparator only reads `values.points[].value` (or `pressure_atm`). Emit stored the Gibbs numbers on `values.value`, so the comparator saw no table points. The digit agreement above is the cell audit. Tool defect, not fixed on this branch.

## Figure calibration

Page pixels are the PDF image matrix times 600/72. Fig. 1 image: width 360.898 pt, height 373.26 pt, lower-left (117.66, 298.22) pt. Fig. 2 image: width 352.713 pt, height 345.48 pt, lower-left (121.44, 376.58) pt. Embedded-JPEG pixels are the read frame. Page x = x0 + embedded_x * sx, page y from the top = y0 + embedded_y * sy.

Tick pixels are the outward tick strokes on the embedded JPEG. Linear fit residual is the largest miss of a labelled tick.

| axis | labels | pixels | max residual px |
| --- | --- | --- | --- |
| Fig. 1a x, mole % TiO2 | 10, 20, 30, 40, 50 | 101, 170, 238, 307, 376 | 0.40 |
| Fig. 1b x, mole % TiO2 | 50, 40, 30, 20 | 376, 467, 558, 650 | 0.40 |
| Fig. 1c x | 10, 20, 30, 40, 50 | 101, 170, 240, 305, 374 | 2.00 |
| Fig. 1d x | 50, 40, 30, 20 | 374, 465, 558, 650 | 0.70 |
| Fig. 1a y, −log a | 4, 3, 2, 1, 0 | 109, 175, 241, 307, 372 | 0.40 |
| Fig. 1b y, −log a | 4, 3, 2, 1, 0 | 111, 173, 238, 305, 376 | 3.00 |
| Fig. 1c y, −ΔG/RT | 5, 4, 3, 2, 1, 0 | 401, 453, 504, 557, 608, 657 | 1.43 |
| Fig. 1d y, −ΔG/RT | 5, 4, 3, 2, 1, 0 | 401.5, 453, 504, 556, 608, 658 | 0.80 |
| Fig. 2c/d x, q % | 100, 80, 60, 40, 20 | panel c 67, 134, 200, 267, 334; panel d 367, 434, 500, 567, 634 | 0.40 |
| Fig. 2c y, mole % | 60, 40, 20, 0 | 397, 479, 559, 640 | 0.70 |
| Fig. 2d y, mole % | 60, 40, 20, 0 | 401, 482, 561, 641 | 0.70 |
| Fig. 2a/b y, printed ticks | 0, −1, −2, −3 | left 76, 152, 226, 301; right 78, 152, 226, 300 | 0.70 / 0.00 |

Fig. 2 activity panels have no x tick at 0. The corner 0 is the activity ordinate. q is stored as the printed axis number.

## Marker reads

Read 1 is the distance-transform peak of the colour mask, checked on a 25 px window and on the red-column run through each circle. Read 2 is a separate centre: the centroid of that symbol's pixels inside one marker radius, not a fixed offset of read 1. Where the radius also covered the next circle (Fig. 1a TiO2 index 15), read 2 is the midpoint of this circle's own contiguous run. That re-read lands on the same pixel as the peak, (307, 322). `read_uncertainty_dex` is half the ordinate difference. No activity pair exceeds 0.02 dex.

Fig. 1 symbols 1–3 (KEMS markers, Shornikov et al. 2000 and Shornikov and Archakov 2003): black square CaO, red circle TiO2, blue triangle SiO2. Stored once as linear activity. Panel a 12 + 19 + 15 points. Panel b 7 + 7 + 7 points. Legend keys at the centre divider were not stored.

Fig. 1 symbol 4, magenta open squares, ordinate −ΔG_T/RT: 10 points in panel c and 6 in panel d, context only.

Fig. 2 panels c and d symbols 1–3: CaO 10 and 11, TiO2 11 and 11, SiO2 8 and 8, context only. One blue detection on the panel-d curve and one unpaired black pixel were dropped. Panels a and b have calculated lines only.

Calculated lines are figure records with quantity activity where a token exists, value unavailable, no sampled points. Twelve such records.

## Text held out of the Gibbs rows

- p(O2) = `5.0 . 10–5` atm (Cyrillic O, spaced period, en-dash). Reducing boundary at the Fig. 2 temperature 2300 K. `pressure_atm` `5.0e-5` is that printed number; the store converts atm to pascals. Not copied onto the Gibbs rows.
- Starting melt quoted from Archakov et al. 2000: 31.4 mole % CaO, 16 mole % TiO2, 52.6 mole % SiO2. Context. Not divided by 100.
- Minimum of melt formation from oxides, −53.4 kJ/mole at 2050 K, at the titanite concentration. Not the table's from-elements CaTiSiO5 value.
- At weight reduction to 50%, SiO2 from 52 to 15 mole % as printed in that sentence.

## Schema gaps

- No quantity token for −ΔG_T/RT. Not converted through RT and not stored as delta_fG.
- Mole percent has no AmountBasis token and no residue source field. Activity composition identity stays unknown. The point condition records the reason. Printed mole percents stay on the points.
- Fig. 1 activity reference state, fO2 and total pressure are not printed.
- Gibbs formation reaction, formation elements and standard pressure are not printed. Not inferred.

## DeСapitani

The text layer spells DeСapitani with Cyrillic С (U+0421). The attribution keeps that character.

# Kröger and Kreitlow (1956) extraction audit

## Scope and interpretation

The article prints solution heats, devitrification heats, formation heats, and
batch-to-glass reaction heats. `Lösungswärme` = heat of solution;
`Entglasungswärme` = devitrification heat (glass-to-crystal enthalpy);
`Bildungswärme` = formation heat; `Wärmetönung` = reaction heat;
`Einwaage` = sample/charge mass; `Intervalle` = 30-second measurement
intervals; `Δt` = galvanometer scale divisions (or °C in Table 8); `Ww` =
calorimeter water value; `Umsatz` = conversion; `Gemengesatz` = batch
composition; `Glaszusammensetzung` = glass composition.

Printed Tables 1–4 contain 3, 3, 4, and 5 trial rows (15 total). Table 5 has
6 composition/form rows, each solution result averaged from five
determinations. Table 6 has 3 glass rows. Table 7 has 3 batch compositions
with 2 solution-derived results, 1 earlier calculation, and 2 direct
calorimetry results per row. Table 8 has 6 experimental rows. Row headings and
column assignment were checked against renders of PDF pages 3, 5, 6, and 7.

## Cell audit

| Printed table / PDF page | Stored cells and rows | Machine-read agreement | Render confirmation |
|---|---|---|---|
| 1 / 3 | 3 trials; mass, intervals, Δt, Ww, cal/g, kcal/mol; mean and corrected soda heat | MinerU table block and Tesseract layout agree on row values | Page 3; rows and six columns checked |
| 2 / 3 | 3 trials; all six columns and mean | MinerU and Tesseract agree | Page 3; row/column assignment and em-dash minus signs checked |
| 3 / 3 | 4 crystalline Na2Si2O5 trials; all six columns and mean | MinerU and Tesseract agree | Page 3; row/column assignment and minus signs checked |
| 4 / 3 | 5 glassy Na2Si2O5 trials; all six columns and mean | MinerU and Tesseract agree | Page 3; row/column assignment and minus signs checked |
| 5 / 5 | 3 crystalline and 3 glass rows; solution and devitrification heats | MinerU HTML and Tesseract layout agree on digits | Page 5; six rows, phase labels, ± columns, and units checked |
| 6 / 6 | 3 glass compositions; molar ratios, wt% composition, measured cal/g and molar heat | MinerU HTML and Tesseract layout agree on values | Page 6; all rows and eight columns checked |
| 7 / 7 | 3 compositions; row 2 solution-derived, row 3 prior calculation, row 4 direct calorimetry | First-pass MinerU and Tesseract merge columns; hybrid-high and pipeline-ocr agree on all cells | Page 7 resolves boundaries and uncertainties. In particular direct row 4 is 55.5 / 11780 ± 250; 54.04 ± 0.1 / 13300 ± 250; 48.2 / 12260 ± 190. |
| 8 / 7 | 6 direct calorimetry trials, all nine numeric columns and the mean | MinerU HTML and Tesseract layout agree on the numeric rows | Page 7; all six rows, plus/minus signs, and final run values checked |

Equation cells transcribed from PDF page 5 (printed page 397) were checked
against both the MinerU prose and Tesseract body read, then render-checked:
equations (1)–(3) give −33.85, +19.36, −60.49 kcal/mol; oxide formation
equations (4)–(6) give −110.75, −134.44, −137.39 kcal/mol; binary-silicate
formation equations (7)–(9) give −12.3, −3.2, and −13.3 (or −11.6) kcal/mol;
element formation values are −1141.95, −1116.84, −1945.19 kcal/mol. The cited
input cells −34.11 and −76.9 kcal/mol are transcribed as attributed values.

Table 7 cell source ledger (agreement among redo variants; all checked against
the PDF render):

| Cell group | hybrid-high | pipeline-ocr | settled value |
|---|---|---|---|
| 1:1:6 composition and glass wt% | 1:1:6; 12.97:11.73:75.30 | same | same |
| 1:1:4.8 composition and glass wt% | 1:1:4.8; 15.26:13.79:70.95 | same | same |
| 1:0.71:4.85 composition and glass wt% | 1:0.71:4.85; 15.77:10.12:74.11 | same | same |
| Row 2, three solution-derived kcal/mol glass | 55.7; 55.43; 48.26 | same | same |
| Row 2, three kcal/100 kg glass | 11640; 13150; 12280 | same | same |
| Row 3, prior kcal/100 kg glass | 14859; 16502; 14841 | same | same |
| Row 4, direct kcal/mol glass | 55.5; 54.04 ± 0.1; 48.2 | same | same |
| Row 4, direct kcal/100 kg glass | 11780 ± 250; 13300 ± 250; 12260 ± 190 | same | same |

No values were read from figures. Table 7's row 3 is an earlier calculation
cited to [23], not an observation; it is retained as printed comparison
context in the typed refusal. The solution-heat and batch reaction-heat
quantities have no exact token in `Quantity` (`simulator/battery/enums.py:62`)
or `QUANTITY_ALIASES` / `TYPE_QUANTITY` (`simulator/battery/migrate.py:435,
453`); `heat_of_fusion` is not a heat of solution and is not reused. The
closed observation set is `OBSERVATION_TYPES` (`tools/validate_literature_extracts.py:79`);
the closest existing container is `gibbs_table`, which does not itself add a
quantity alias (`simulator/battery/migrate.py:435`). Hence those rows are
typed refusals with `quantity_unknown`, while the printed formation enthalpies
use exact `delta_fH`. Crystal and glass are mapped phases (`PHASE_MAP`,
`simulator/battery/migrate.py:380`); `PhaseRole.GLASS` and `PhaseRole.MELT` are
existing roles (`simulator/battery/enums.py:301`), and the phase basis tokens
include `wt_percent` and `mole_fraction` (`simulator/battery/enums.py:663`).
The isolated migration yields 23 observations, 0 hard issues, and 17 unavailable
values, exactly the observations with the expected quantity-unknown schema gap.

The quench statements are recorded for glass measurements. The article does
not report pressure. Solution calorimetry was performed at the printed 22°C;
Table 8 reports the batch heating interval as about 1300–1350°C for six
minutes. Its printed conversion percentages retain the numeric percent and the
canonical `%` unit. No room-temperature or pressure assumption is added.

`mineru-compare.csv` is the required draft comparison run. The tool found no
machine-comparable extracted points (`total=0`): the tables are preserved in
typed-refusal records and authored row grids rather than migrated point-series
fields. Cell fidelity is therefore demonstrated by the cell audit above and
the focused render pins, not by a false zero-mismatch interpretation.

Figure 5 (PDF page 4) plots relative heat contents of crystal, undercooled
melt, and glass as a function of temperature. It is a figure-read candidate;
no numerical values were digitized.

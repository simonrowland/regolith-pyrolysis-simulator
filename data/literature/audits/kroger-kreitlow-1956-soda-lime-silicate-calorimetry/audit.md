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
determinations. Table 6 has 3 technical-glass rows of molar composition, glass
component weight percentages, calculated solution heats, and differences; the
grid is checked but not extracted. Section 5 also reports three measured glass
solution heats in an unnumbered result list. Table 7 has 3 batch compositions,
each with a solution-derived result, a calculation attributed to [23], and a
direct calorimetry result. Table 8 has 6 experimental rows. Row headings and
column assignment were checked against renders of PDF pages 3, 5, 6, and 7.

## Cell audit

| Printed table / PDF page | Stored cells and rows | Machine-read agreement | Render confirmation |
|---|---|---|---|
| 1 / 3 | 3 trials; mass, intervals, Δt, Ww, cal/g, kcal/mol; mean and corrected soda heat | MinerU table block and Tesseract layout agree on row values | Page 3; rows and six columns checked |
| 2 / 3 | 3 trials; all six columns and mean | MinerU and Tesseract agree | Page 3; row/column assignment and em-dash minus signs checked |
| 3 / 3 | 4 crystalline Na2Si2O5 trials; all six columns and mean | MinerU and Tesseract agree | Page 3; row/column assignment and minus signs checked |
| 4 / 3 | 5 glassy Na2Si2O5 trials; all six columns and mean | MinerU and Tesseract agree | Page 3; row/column assignment and minus signs checked |
| 5 / 5 | 3 crystalline and 3 glass rows; solution and devitrification heats | MinerU HTML and Tesseract layout agree on digits | Page 5; six rows, phase labels, ± columns, and units checked |
| 6 / 6 | 3 composition rows; molar ratios, glass component wt%, calculated solution heats and differences | MinerU HTML and Tesseract layout agree on the grid | Page 6; all rows and columns checked. Table 6 is not extracted; its calculated heat cells are distinct from the measured section 5 solution heats and Table 7 compositions. |
| Unnumbered section 5 / 6 | 3 technical-glass solution heats, with cal/g means, printed cal/g uncertainties, and kcal/mol values | MinerU prose and Tesseract layout agree on all values | Page 6; the section 5 result list has no printed table number. Composition weights used for identity are separately located to Table 7 and are not attributed to Table 6 or the unnumbered list. |
| 7 / 7 | 3 compositions; solution-derived row, [23] calculation, direct calorimetry row | First-pass MinerU and Tesseract merge columns; hybrid-high and pipeline-ocr agree on all cells | Page 7 resolves boundaries and uncertainties. The solution-derived row is measured_reduced, the [23] calculation quoted_attributed, and the direct row measured_direct. Direct values are 55.5 / 11780 ± 250; 54.04 ± 0.1 / 13300 ± 250; 48.2 / 12260 ± 190. |
| 8 / 7 | 6 direct calorimetry trials, all nine numeric columns and the mean | MinerU HTML and Tesseract layout agree on the numeric rows | Page 7; all six rows, plus/minus signs, and final run values checked |

Equation cells transcribed from PDF page 5 (printed page 397) were checked
against both the MinerU prose and Tesseract body read, then render-checked:
equations (1)–(3) give −33.85, +19.36, −60.49 kcal/mol; oxide formation
equations (4)–(6) give −110.75, −134.44, −137.39 kcal/mol; binary-silicate
formation equations (7)–(9) give −12.3, −3.2, and −13.3 (or −11.6) kcal/mol;
element formation values are −1141.95, −1116.84, −1945.19 kcal/mol. The cited
input cells −34.11 and −76.9 kcal/mol are transcribed as attributed values.

The prose result cells were checked against the MinerU and Tesseract reads:
the disilicate devitrification heat is −8.79 kcal/mol from the difference of
the stored glass and crystal solution heats, and the cited direct value is
8.81 kcal/mol [2] (PDF page 4). The soda-quartz reaction enthalpy is
+15.69 kcal/mol from the stored soda, quartz, and crystalline disilicate
solution heats; the cited direct result is 16.5 kcal/mol [2] (PDF page 4).
The three technical-glass formation heats from crystalline oxides are
−63.77, −66.03, and −58.86 kcal/mol for molar ratios 1:1:6, 1:1:4.8, and
1:0.71:4.85 (PDF page 6). These are not element-basis delta_fH values.

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

The Table 7 row classes are stored separately: solution-derived results are
`measured_reduced`, the result cited to [23] is `quoted_attributed`, and direct
calorimetry is `measured_direct`.

No values were read from figures. Table 7's row cited to [23] is stored as a
separate quoted_attributed typed refusal; its solution-derived and direct rows
are separate measured_reduced and measured_direct refusals. The solution,
devitrification, and batch reaction quantities have no exact token in
`Quantity` (`simulator/battery/enums.py::Quantity`), `TYPE_QUANTITY`
(`simulator/battery/migrate.py::TYPE_QUANTITY`), or `QUANTITY_ALIASES`
(`simulator/battery/migrate.py::QUANTITY_ALIASES`). `heat_of_fusion` is not a heat of
solution or devitrification and is not reused. `OBSERVATION_TYPES` is a closed
set (`tools/validate_literature_extracts.py::OBSERVATION_TYPES`); `gibbs_table` remains the
container for these typed refusals (`simulator/battery/migrate.py::TYPE_QUANTITY`).
For oxide-basis heats, `Quantity.DELTA_FH` is the exact `delta_fH` token
(`simulator/battery/enums.py::Quantity.DELTA_FH`), and the formation identity requires
`reaction` plus `formation_elements` (`simulator/battery/identity.py::profile_for`,
where `formation_elements` are elements with elemental reference phases);
there is no exact token for formation from oxides. The three element-
basis values retain `delta_fH`. Crystal and glass are mapped phases
(`PHASE_MAP`, `simulator/battery/migrate.py::PHASE_MAP`); `PhaseRole.GLASS` and
`PhaseRole.MELT` are existing roles (`simulator/battery/enums.py::PhaseRole`), and
the phase basis tokens include `wt_percent` and `mole_fraction`
(`simulator/battery/enums.py::PHASE_BASIS_TOKENS`). The isolated migration yields 36
observations, 0 hard issues, and 33 unavailable values, exactly the rows in the
quantity-unknown set.

The element-basis −1945.19 kcal/mol value carries no uncertainty in the source;
the previous unprinted 0.1 kcal/mol field was removed. Table 4's glass
disilicate mean retains its printed ±0.1 kcal/mol uncertainty.

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

# Shornikov 2026 wollastonite KEMS — extraction audit

Source id `shornikov-2026-wollastonite-kems`. Title: Mass-Spectrometric Investigation of Evaporation Processes and Thermodynamic Properties of Wollastonite. S. I. Shornikov. Geochemistry International 64(8), 608–632. Received 3 April 2026, revised 5 May 2026, accepted 10 May 2026. DOI printed on the title page: 10.1134/S0016702926700035. Transcription worker t1372-w2-w7-t1, 2026-10-20. Reconciled 2026-10-21: Tables 1, 2, 3 and 7 keep the cell coverage; Tables 4, 5 and 6 keep the observation-level typing; figure reads stay context. review_status draft.

PDF page N is published page 607+N. PDF page 1 is published page 608. `pdf_page_index` is N−1.

## Duplicate verdict

Not a full duplicate, and not a skip. This is the full Geochemistry International paper of the same evaporation campaign as `shornikov-2024-wollastonite-evaporation` (TMS 2024 chapter).

Evidence, compared on the printed numbers rather than the source id:

- Temperature range 1751–2087 K is the same.
- Table 1 this-study rows match the 2024 extract exactly: crystalline 1751–1818 K, −38.92±0.52 kJ/mol and 2.33±0.29 J/(mol K); liquid 1818–2087 K, −11.29±0.24 kJ/mol and 17.53±0.12 J/(mol K).
- Table 7 this-study row matches: Knudsen effusion mass spectrometry, 1818±7 K, 27.64±0.57 kJ/mol.
- The body says the activities are approximately 0.015 (CaO) and 0.20 (SiO2). Those prose values are already `shornikov_2024_cao_activity_prose` and `shornikov_2024_sio2_activity_prose`.

Those summary constants are not re-admitted. The 2024 extract refused the pressure curves because the chapter did not print a table. This paper prints Tables 3–6 and the rankinite estimate. Those are the rows admitted here.

No other extract on this base has this title, this DOI, or these vapor-pressure tables.

## Tables

The corpus text directory contains `pdftotext-layout.txt` only. There is no MinerU markdown or content-list JSON under either MinerU location. The seat table draft converter reported no content-list JSON and the QA pre-screen found no table blocks, so there was no independent MinerU comparison. Contrary to the task's expected limitation, the born-digital pdftotext layer includes Tables 3–7, including every Table 5 row. It is the primary text read; renders were used for the printed table layouts and Table 5's slash, intervals, signs, and coefficients. Dashes are absent and are omitted, never stored as zero.

| table | published page | what was admitted |
| --- | --- | --- |
| 1 | 611–612 | nothing. Literature survey plus two this-study rows that duplicate the 2024 extract |
| 2 | 614 | nothing. Other experiments only. No this-study row |
| 3 | 618 | 146 partial pressures. CaO has 7 values (1910–2087 K). Mo has 13 values (the 1751 K cell is a dash). Nine other species have 14 values |
| 4 | 619 | 14 Hertz–Knudsen p(O) values, column (11)–(13), plus 76 equilibrium-constant cells in columns (14)–(19). Eight printed dashes are omitted. The 7% error bound applies only to (11)–(13) |
| 5 | 620 | 15 fitted coefficient rows. Each row's printed T interval is stored as an interval condition; no midpoint is assigned |
| 6 | 621 | four this-study records: sublimation enthalpy, sublimation entropy, dissociation enthalpy, dissociation entropy. Each migrates UNAVAILABLE. BaSiO3 and CaTiO3 rows are other work and are not stored |
| 7 | 623 | 22 literature melting-temperature and heat-of-fusion rows. The this-study melting calculation stays context; it duplicates the 2024 extract |

Table 3 and Table 4 are rotated on the portrait page. pdftotext reading order matched a rotated 600 dpi render, including every mantissa, exponent, and dash that was checked.

Eq. (20) and the Table 5 header both print log p_i = a_i/T + b_i. pdftotext dropped the slash. The slash is on the 600 dpi crop. The fit stored here is log10(p) = a_i/T + b_i.

### Table 5 full-cell audit

All Table 5 numeric cells below are from physical PDF page 13, printed Table 5, and were checked against the 600 dpi render (verification method (b)). The row number maps each species, temperature interval, a_i and b_i cell to the source. Their ± widths are retained separately for the two fitted parameters; the paper does not identify a confidence level.

| row | species | T interval, K | a_i, K | b_i, dimensionless | verification |
| --- | --- | --- | --- | --- | --- |
| 1 | O | 1751–1817 | −27820 ± 392 | 6.164 ± 0.219 | p13/table5/row1 (b) |
| 2 | O | 1817–2087 | −28311 ± 146 | 6.444 ± 0.075 | p13/table5/row2 (b) |
| 3 | O2 | 1751–1817 | −29787 ± 440 | 5.792 ± 0.246 | p13/table5/row3 (b) |
| 4 | O2 | 1817–2087 | −30049 ± 278 | 5.929 ± 0.143 | p13/table5/row4 (b) |
| 5 | Ca | 1751–1817 | −29978 ± 426 | 7.397 ± 0.238 | p13/table5/row5 (b) |
| 6 | Ca | 1817–2087 | −27468 ± 126 | 6.007 ± 0.065 | p13/table5/row6 (b) |
| 7 | CaO | 1910–2087 | −35633 ± 92 | 7.061 ± 0.046 | p13/table5/row7 (b) |
| 8 | SiO | 1751–1817 | −27767 ± 347 | 10.104 ± 0.194 | p13/table5/row8 (b) |
| 9 | SiO | 1817–2087 | −26284 ± 161 | 9.280 ± 0.082 | p13/table5/row9 (b) |
| 10 | SiO2 | 1751–1817 | −30893 ± 209 | 8.664 ± 0.117 | p13/table5/row10 (b) |
| 11 | SiO2 | 1817–2087 | −29601 ± 95 | 7.964 ± 0.049 | p13/table5/row11 (b) |
| 12 | Si2O2 | 1751–1817 | −30142 ± 323 | 8.781 ± 0.181 | p13/table5/row12 (b) |
| 13 | Si2O2 | 1817–2087 | −27068 ± 270 | 7.084 ± 0.139 | p13/table5/row13 (b) |
| 14 | CaSiO3 | 1751–1817 | −44065 ± 573 | 14.687 ± 0.321 | p13/table5/row14 (b) |
| 15 | CaSiO3 | 1817–2087 | −40293 ± 215 | 12.617 ± 0.110 | p13/table5/row15 (b) |

Table 3 has 14 temperature rows and 146 nonblank pressure cells across its 11 species columns. Its entire independent grid is pinned by the focused test and the row/column assignment was checked against the rotated 600 dpi render of physical page 11 (method (b)). Table 4 has 14 Hertz–Knudsen values and 76 calculated values across the 14 printed temperatures; all 90 cells and the rotated row/column layout were checked against the 600 dpi render of physical page 12 (method (b)). Table 7's 11 literature rows contain 22 stored values and 22 printed widths; its Table 7 values and uncertainty forms are independently pinned by the focused test against the 600 dpi render of physical page 16 (method (b)). Its twelfth row is this study's 1818 ± 7 K and 27.64 ± 0.57 kJ/mol result, retained as context because it duplicates the 2024 extract.

Eq. (25) on the 600 dpi crop has no square on p_SiO. The pdftotext "p 2" is the subscript of SiO2. Activities are not re-admitted, so the equation is quoted only as the reference-state sentence.

## Counts

Isolated migration emits 277 observations.

146 Table 3 cells + 90 Table 4 points + 15 Table 5 fits + 4 Table 6 refusals + 22 Table 7 literature rows = 277.

The 90 Table 4 points are 14 Hertz–Knudsen values, method_class measured_tabulated, and 76 equilibrium-constant cells, method_class model_derived. The 15 Table 5 fits keep their parent links. Oxygen's parent is the Table 4 Hertz–Knudsen series. Every other fit's parents are the Table 3 cells on that side of the printed 1817 K split. The 4 Table 6 records are explicit observations, not one collapsed context record.

Compared with green, the candidate also emits 277 observations and all 277 observation IDs are identical, so the expected observation delta is zero. It retains 19 unavailable values, matching green.

The prose activities are not scored. `shornikov_2024_cao_activity_prose` and `shornikov_2024_sio2_activity_prose` already hold them. The overlap record names those two ids and does not repeat the numbers. Rankinite stays the one context record. Tables 1 and 2 stay context. The Table 7 this-study melting calculation stays context.

Fig. 3 series-23 marker reads, including the two markers inside 1818±7 K, stay context. Fig. 10 series 1 stays context. The printed Fig. 10 axes are `O + O2, mol %` on panel a and `O2 + O, mol %` on panel b. The caption still calls panel b the O2/O ratio.

The isolated migration emitted 277 observations, zero hard issues, and 19 unavailable values. The 15 Table 5 rows are fit coefficients rather than scalar partial pressures; the migrator therefore retains their printed intervals and coefficients but emits no numeric p_partial. The four Table 6 reaction-enthalpy and reaction-entropy values are typed refusals. There are no registry issues or quantity-unknown rows outside those four declared schema gaps, and no phase-unknown rows.

## Uncertainty envelope

The migrated count is 201 observations with uncertainty kind `printed` and confidence `not_stated`, plus 76 Table 4 equilibrium-constant rows with no printed uncertainty. Table 3's 146 pressure values and Table 4's 14 column (11)–(13) values carry the printed “errors are no higher than 7%” relative bound; this is not a point-specific symmetric sigma. The confidence level is not stated. Table 4 columns (14)–(19) have no printed uncertainty; each note says the 7% bound does not apply. Table 5 retains separate printed half-widths for a_i and b_i. Table 6 and Table 7 store each printed half-width with its units and exact `value_as_published` form. None of these sources identifies a confidence level, so `not_stated` is retained rather than treating the widths as one sigma.

## Experiment linkage and engine readiness

The 146 Table 3 rows, 90 Table 4 points, and 15 Table 5 fits link to experiment `shornikov-2026-wollastonite-kems-runset`. The experiment's initial charge is one printed formula unit of CaSiO3 on an oxide basis of CaO 1 and SiO2 1; the printed oxide-component sum is 2. This charge is linked to Tables 3–5 through the experiment FK and is marked `initial_charge_only`, not as a measurement-state composition. The scorer emits its own `composition_from_initial_charge` flag from that existing proxy. The source does not report chronological run order, a final mass, or a conversion/mass-loss value. The temperature ordering in the tables is not treated as chronological order, and the pressures are not used to back-solve depletion. The context records depletion as unreported and the final mass and run order as located `not_published` absences.

The Experimental section describes a molybdenum Knudsen effusion cell and an MI-1201 mass spectrometer. Its temperature sentence is “accuracy better than 10 at 2400 K”; the unit for 10 is missing in the source text, so no K-valued temperature uncertainty is inferred. The Mo cell material is recorded; orifice dimensions and the Clausing factor are not printed, and no external pressure-calibration standard is reported. The measured residual-gas bound is ≤10⁻¹⁰ atm, but that is not the total pressure of the vapor mixture. No carrier gas or gas flow is reported. Table 3 prints pO2, which migrates to fO2_Pa; Table 4 and Table 5 do not print a point-specific oxygen condition. Table 5 supplies only row-specific T intervals, not scalar temperatures.

The target observable is `p_partial` for gas species. Per `simulator/battery/score.py::rail_for_quantity`, the 118 Table 3, 90 Table 4, and 11 non-silicon Table 5 rows target the `vapour` rail (219 rows total); the 28 Table 3 and four Table 5 SiO/SiO2 rows target `SiO_evolution` (32 total). `simulator/battery/waypoints.py::consumer_readiness` reports zero MAGEMin engine-point-ready rows for all 251 observations: Table 3, 0/146 ready, lacks `experiment.pressure_environment.total_pressure_Pa`, `bench.other_facts.gas_load_Pa_m3_s`, and `bench.pumping_speed_m3_s`; Table 4, 0/90 ready, lacks those pressure-boundary inputs and `fO2_log`, `experiment.fO2_control`, and `total_pressure_Pa`; Table 5, 0/15 ready, lacks those oxygen and pressure inputs and has interval rather than point `temperature_K`. The KEMS input contract in `simulator/battery/consumer_inputs.py::REQUIREMENTS` is also incomplete: no sample mass, post-mass, purity, orifice dimensions/area, Clausing factor, effective escape area, temperature program, numeric temperature uncertainty, repeat count, hold duration, total experiment pressure, calibration, or measurement selectors are printed. The molybdenum cell material is printed; its liner is not stated. Table 3 supplies pO2 for its rows; Table 4 and Table 5 have no point-specific oxygen condition. The residual-gas upper bound is not used as the total pressure of the vapor mixture. No score gate is bypassed or made ready by relabeling the source's measured results.

The 11 Table 5 rows named in the repair request (Ca, CaO, CaSiO3, O, O2, Si2O2) all now carry their row-specific printed intervals with table 5/page 13/row locators. Those rows and the four other Table 5 rows contain fitted coefficients for a pressure-temperature relation, not scalar pressure points; the migrator emits them as unavailable `p_partial` values. Adding the printed intervals therefore makes zero rows engine-ready. Existing IDs are unchanged.

## Table 6 schema gap

The four Table 6 rows print reaction enthalpy ΔrH and reaction entropy ΔrS for stated reactions, not formation enthalpy or absolute entropy. They remain descriptive quantity-unknown rows and migrate unavailable. The closed quantity set has formation enthalpy `simulator/battery/enums.py::Quantity.DELTA_FH`, heat of fusion `simulator/battery/enums.py::Quantity.ENTHALPY_OF_FUSION`, and entropy `simulator/battery/enums.py::Quantity.S`; none means the printed reaction quantity exactly. The aliases are closed in `simulator/battery/migrate.py::QUANTITY_ALIASES`; `gibbs_table` infers only `simulator/battery/enums.py::Quantity.DELTA_FG` through `simulator/battery/migrate.py::TYPE_QUANTITY`. The observation-type set is closed in `tools/validate_literature_extracts.py::OBSERVATION_TYPES`. The extract uses explicit descriptive labels so no type fallback turns these into ΔfG. The four Table 6 IDs are the entire migrated quantity-unknown set; all phases remain known. No exact reaction-enthalpy or reaction-entropy quantity is present in the existing extract set.

## Standard state

Fig. 3 series 23 stays context. The prose activities are not scored in this extract. The standard-state sentence does not contain "Raoultian" and does not name one liquid or solid endmember.

The paper's sentence, published page 622 (PDF page 15), after Eqs. (24) and (25): p_i and p_i° are the partial pressures of the component of the gas phase over wollastonite and simple oxide (pure component), respectively.

Page 622 names a pure-component reference and does not state its temperature. The stable-phase-at-T rule leaves the CaO phase unknown. It is not crystalline, and the Fig. 3 CaO figure record stores inferred false. The paper does not state that p° was measured in this cell at the experiment temperature. Fig. 6 draws the pure-oxide comparison from other papers (Shornikov 2025 for CaO, Shornikov et al. 1998 for SiO2). The SiO2 figure reference stays the cristobalite-crossing unknown. The prose does not say Raoultian, pure liquid, or pure solid.

The same sentence is stored on the overlap context row so the quote is in the extract. It is not copied onto the pressure rows. Pressure rows are gas partial pressures, not oxide activities.

1815 K lies inside the printed melting point 1818±7 K. That band would defer an activity whose reference was the pure oxide at the run temperature. These gas-pressure rows are not that reference, so they are not deferred.

## Derivations

Table 5. Unweighted least squares of log10(p) = a/T + b against the parent pressures. Low side uses 1751, 1783, 1800, 1815 K. High side uses 1833–2087 K. 1817 K is the printed split and is not a pressure row. CaO uses only 1910–2087 K. Every fitted coefficient falls inside the printed ±. The printed coefficients are stored, not the refit. The recovered standard errors are close to the printed widths and are not identical, so they are not stored as the printed uncertainty. Oxygen's parent is Table 4 column (11)–(13). Every other species' parents are its Table 3 cells on that side of the split. The page-resolved high-side B coefficients are SiO `9.280 ± 0.082` and CaSiO3 `12.617 ± 0.110`.

Table 4 columns (14)–(19). Eqs. (14)–(19) as read on the 600 dpi page:

- (14) p_O = K_3(T) p_CaO / p_Ca
- (15) p_O = K_4(T) p_SiO2 / p_SiO
- (16) p_O = p_O2^(1/2) / K_5(T)^(1/2)
- (17) p_O = K_8(T) p_MoO / p_Mo
- (18) p_O = K_9(T) p_MoO2 / p_MoO
- (19) p_O = K_10(T) p_MoO3 / p_MoO2

K_r(T) is cited to Glushko et al. (1978–1982) and is not printed. The rows are model_derived with no resolvable parent. `raw_inputs: not_printed` is not used, because that declaration is invalid for model_derived evidence.

Table 6 this-study rows, render-checked: [CaSiO3] = (CaSiO3) at 1812–2187 K, 771±13 kJ/mol and 242±5 J/(mol K); (CaSiO3) = (CaO) + (SiO2) at 1910–2187 K, 479±12 kJ/mol and 47±6 J/(mol K). The intervals are not the Table 3 interval. Stored as four observations, quantity delta_rH or delta_rS, not as delta_fH, because 771 kJ/mol is a reaction enthalpy. Migration refuses both quantities, so the four values are UNAVAILABLE and the printed numbers are not a second scored copy. Parents are not linked. The Glushko constants are not printed.

Rankinite, 600 dpi crop of published page 623: −42.63±0.63 kJ/mol and 2.39±0.35 J/(mol K) at 1600–1737 K, from Eqs. (31) and (32). The paper calls these the enthalpy and entropy of mixing. Eq. (32) uses orthosilicate activities from Shornikov 2026a, which are not printed here. No parent is linked. The schema has no mixing-enthalpy or mixing-entropy quantity, so both numbers are context, not delta_fH and not S. The printed entropy unit J/(mol K) is the unit in the context row. A read-only call of the unit owner maps J/(mol K) to no amount and maps J/mol/K to 2.39. That spelling is recorded on the context row and is not used to score an entropy. The phase (crystal or liquid) is not stated.

## Render checks

600 dpi: PDF pages 10–16 (published 617–623). Table 3 rotated preview. Table 4 rotated page, including Eqs. (14)–(20). Table 5 header and every coefficient row. Eqs. (24) and (25), full resolution, no square on p_SiO. Eqs. (31)–(34). Table 6 this-study numbers. Rankinite sentence. PDF page 6 (Figs. 3 and 4, published 613) and PDF page 18 (Fig. 10, published 625), with tick-line centres and marker centres measured on those renders.

120 dpi glances only, no marker centres: PDF pages 17 and 19 (Figs. 8, 9, 11 and 12).

Not rendered: published page 616 (PDF page 9), the experimental paragraph. Its pyrometer accuracy has an unspecified unit for 10 and is retained as a located typed absence; the residual-gas upper bound is stored separately from total vapor pressure. Ion-current ratios and appearance energies on that page are not stored as numbers.

## Figure decisions

Tick lines were measured on the 600 dpi renders. A labelled tick is the stroke centre, not the glyph. The in-plot series numbers were checked against the legend and were not taken as markers.

Fig. 3 panel (a), series 23, 14 filled red circles. Y is logarithmic. Labelled ticks: 0.025 at y=561.5, 0.020 at y=710.5, 0.015 at y=901.5, 0.010 at y=1173.5, 0.005 at y=1633.5. log10(a) = −1.23590839 − 0.00065183·y, 1534.15 px per decade. Residuals in log10: −0.00015, +0.00006, −0.00038, +0.00083, −0.00036. X ticks: 1800 at x=1059.5, 1900 at x=1483.5, 2000 at x=1909.5, 2100 at x=2335.5. T = 1551.081864 + 0.23507256·x. Residuals at most 0.19 K. Read uncertainty ±0.00015 in activity and ±1.0 K, ±1.5 K left of the 1800 tick.

Fig. 3 panel (b), series 23, 14 filled red circles. Y is linear. Labelled ticks: 0.4 at y=612.5, 0.3 at y=913.5, 0.2 at y=1213.5, 0.1 at y=1514.5. a = 0.60379226 − 0.00033267·y, 300.60 px per 0.1. Residuals at most 0.00010. X ticks: 1800 at x=2840.5, 1900 at x=3173.0, 2000 at x=3505.5, 2100 at x=3838.5, 2200 at x=4171.5. T = 946.416395 + 0.30052587·x. Residuals at most 0.09 K. Read uncertainty ±0.0010 and ±1.0 K, ±1.5 K left of the 1800 tick. Marker 3 sits 1.8 px left of the 1800 stroke centre, inside the stroke, and is not flagged as an extrapolation.

Fig. 10 panel (a), series 1, 14 open circles in the lower band. Labelled ticks: 0.016 at y=1321.5, 0.014 at y=1471.5, 0.012 at y=1622.5, 0.010 at y=1773.0. mol % = 0.03355294 − 1.32846025×10⁻⁵·y. Residuals at most 0.000005. X ticks: 5.6 at x=1067.5, 5.4 at x=1389.5, 5.2 at x=1712.5, 5.0 at x=2037.0, 4.8 at x=2361.0. Read uncertainty ±0.00010 mol % and ±2 K, ±3 K left of the 5.6 tick. The printed top-axis temperature ticks disagree with T = 10000/(10⁴/T): 1800 reads 1792 K, 1900 reads 1884 K, 2000 reads 1986 K. The stored temperature is the bottom-axis conversion.

Fig. 10 panel (b), series 1, 14 open circles. Labelled ticks: 0.045 at y=1300.5, 0.040 at y=1458.5, 0.035 at y=1616.5, 0.030 at y=1775.0. Residuals at most 0.000007. X ticks: 5.6 at x=3048.0, 5.4 at x=3370.5, 5.2 at x=3693.0, 5.0 at x=4017.5, 4.8 at x=4339.5. Read uncertainty ±0.00015 and ±2 K, ±3 K left of the 5.6 tick. The printed axis label is `O2 + O, mol %`. Panel (a) prints `O + O2, mol %`. The caption still says O2/O ratio. There is no scored quantity for that axis, so the reads stay in context.

Melting band is the printed Tm 1818±7 K, so 1811–1825 K. A marker in that band is deferred and the reference phase stays unknown. In the marker table, figure_only names the read class. Those reads are context records, not scored observations.

| figure | series | marker | centre px | read | decision |
| --- | --- | --- | --- | --- | --- |
| 3a | 23 | 1 | 852.71, 941.42 | a(CaO) 0.01414 ± 0.00015 at 1751.53 ± 1.5 K | read, figure_only, extrapolated left of 1800 |
| 3a | 23 | 2 | 987.47, 896.09 | 0.01514 ± 0.00015 at 1783.21 ± 1.5 K | read, figure_only, extrapolated left of 1800 |
| 3a | 23 | 3 | 1059.65, 870.10 | 0.01574 ± 0.00015 at 1800.18 ± 1.0 K | read, figure_only |
| 3a | 23 | 4 | 1123.64, 849.54 | 0.01623 ± 0.00015 at 1815.22 ± 1.0 K | deferred: inside 1818±7 K, reference phase unknown |
| 3a | 23 | 5 | 1199.77, 837.39 | 0.01653 ± 0.00015 at 1833.11 ± 1.0 K | read, figure_only |
| 3a | 23 | 6 | 1310.29, 828.92 | 0.01674 ± 0.00015 at 1859.10 ± 1.0 K | read, figure_only |
| 3a | 23 | 7 | 1420.98, 813.98 | 0.01712 ± 0.00015 at 1885.12 ± 1.0 K | read, figure_only |
| 3a | 23 | 8 | 1527.26, 809.20 | 0.01724 ± 0.00015 at 1910.10 ± 1.0 K | read, figure_only |
| 3a | 23 | 9 | 1624.79, 797.68 | 0.01754 ± 0.00015 at 1933.03 ± 1.0 K | read, figure_only |
| 3a | 23 | 10 | 1782.31, 776.41 | 0.01811 ± 0.00015 at 1970.05 ± 1.0 K | read, figure_only |
| 3a | 23 | 11 | 1859.06, 768.81 | 0.01832 ± 0.00015 at 1988.10 ± 1.0 K | read, figure_only |
| 3a | 23 | 12 | 2016.02, 757.48 | 0.01864 ± 0.00015 at 2024.99 ± 1.0 K | read, figure_only |
| 3a | 23 | 13 | 2190.50, 742.91 | 0.01905 ± 0.00015 at 2066.01 ± 1.0 K | read, figure_only |
| 3a | 23 | 14 | 2278.07, 735.90 | 0.01925 ± 0.00015 at 2086.59 ± 1.0 K | read, figure_only |
| 3b | 23 | 1 | 2675.67, 1233.01 | a(SiO2) 0.19361 ± 0.0010 at 1750.52 ± 1.5 K | read, figure_only, extrapolated left of 1800 |
| 3b | 23 | 2 | 2782.14, 1217.64 | 0.19872 ± 0.0010 at 1782.52 ± 1.5 K | read, figure_only, extrapolated left of 1800 |
| 3b | 23 | 3 | 2838.75, 1211.96 | 0.20061 ± 0.0010 at 1799.53 ± 1.0 K | read, figure_only, on the 1800 tick stroke |
| 3b | 23 | 4 | 2888.84, 1202.81 | 0.20366 ± 0.0010 at 1814.59 ± 1.0 K | deferred: inside 1818±7 K, reference phase unknown |
| 3b | 23 | 5 | 2948.79, 1199.82 | 0.20465 ± 0.0010 at 1832.60 ± 1.0 K | read, figure_only |
| 3b | 23 | 6 | 3035.69, 1196.77 | 0.20567 ± 0.0010 at 1858.72 ± 1.0 K | read, figure_only |
| 3b | 23 | 7 | 3122.46, 1196.70 | 0.20569 ± 0.0010 at 1884.80 ± 1.0 K | read, figure_only |
| 3b | 23 | 8 | 3206.27, 1194.23 | 0.20651 ± 0.0010 at 1909.98 ± 1.0 K | read, figure_only |
| 3b | 23 | 9 | 3282.82, 1194.10 | 0.20655 ± 0.0010 at 1932.99 ± 1.0 K | read, figure_only |
| 3b | 23 | 10 | 3406.92, 1194.05 | 0.20657 ± 0.0010 at 1970.28 ± 1.0 K | read, figure_only |
| 3b | 23 | 11 | 3467.30, 1196.38 | 0.20580 ± 0.0010 at 1988.43 ± 1.0 K | read, figure_only |
| 3b | 23 | 12 | 3591.12, 1187.95 | 0.20860 ± 0.0010 at 2025.64 ± 1.0 K | read, figure_only |
| 3b | 23 | 13 | 3727.63, 1187.84 | 0.20864 ± 0.0010 at 2066.67 ± 1.0 K | read, figure_only |
| 3b | 23 | 14 | 3796.72, 1181.89 | 0.21062 ± 0.0010 at 2087.43 ± 1.0 K | read, figure_only |
| 10a | 1 | 1 | 876.98, 1703.72 | 0.01092 ± 0.00010 mol % at 1749.09 ± 3 K | read, unscored, extrapolated left of 5.6 |
| 10a | 1 | 2 | 1032.06, 1673.72 | 0.01132 ± 0.00010 at 1778.93 ± 3 K | read, unscored, extrapolated left of 5.6 |
| 10a | 1 | 3 | 1089.55, 1710.93 | 0.01082 ± 0.00010 at 1790.25 ± 2 K | read, unscored |
| 10a | 1 | 4 | 1160.70, 1688.25 | 0.01113 ± 0.00010 at 1804.46 ± 2 K | read, unscored |
| 10a | 1 | 5 | 1240.19, 1598.38 | 0.01232 ± 0.00010 at 1820.61 ± 2 K | deferred: inside 1818±7 K |
| 10a | 1 | 6 | 1320.17, 1628.12 | 0.01192 ± 0.00010 at 1837.15 ± 2 K | read, unscored |
| 10a | 1 | 7 | 1461.49, 1590.72 | 0.01242 ± 0.00010 at 1867.13 ± 2 K | read, unscored |
| 10a | 1 | 8 | 1558.55, 1583.00 | 0.01252 ± 0.00010 at 1888.29 ± 2 K | read, unscored |
| 10a | 1 | 9 | 1682.99, 1552.98 | 0.01292 ± 0.00010 at 1916.13 ± 2 K | read, unscored |
| 10a | 1 | 10 | 1868.77, 1387.96 | 0.01511 ± 0.00010 at 1959.25 ± 2 K | read, unscored |
| 10a | 1 | 11 | 1943.89, 1380.24 | 0.01522 ± 0.00010 at 1977.24 ± 2 K | read, unscored |
| 10a | 1 | 12 | 2125.53, 1342.61 | 0.01572 ± 0.00010 at 2022.15 ± 2 K | read, unscored |
| 10a | 1 | 13 | 2253.94, 1395.61 | 0.01501 ± 0.00010 at 2055.15 ± 2 K | read, unscored |
| 10a | 1 | 14 | 2364.48, 1304.72 | 0.01622 ± 0.00010 at 2084.43 ± 2 K | read, unscored, 3.5 px past the 4.8 tick |
| 10b | 1 | 1 | 2856.26, 1715.38 | O2 + O, mol % 0.03188 ± 0.00015 at 1748.70 ± 3 K | read, unscored, extrapolated left of 5.6 |
| 10b | 1 | 2 | 3010.96, 1663.25 | 0.03353 ± 0.00015 at 1778.49 ± 3 K | read, unscored, extrapolated left of 5.6 |
| 10b | 1 | 3 | 3069.16, 1642.58 | 0.03418 ± 0.00015 at 1789.96 ± 2 K | read, unscored |
| 10b | 1 | 4 | 3139.47, 1616.63 | 0.03500 ± 0.00015 at 1804.02 ± 2 K | read, unscored |
| 10b | 1 | 5 | 3219.43, 1622.14 | 0.03483 ± 0.00015 at 1820.28 ± 2 K | deferred: inside 1818±7 K |
| 10b | 1 | 6 | 3299.07, 1621.02 | 0.03486 ± 0.00015 at 1836.77 ± 2 K | read, unscored |
| 10b | 1 | 7 | 3440.53, 1587.96 | 0.03591 ± 0.00015 at 1866.80 ± 2 K | read, unscored |
| 10b | 1 | 8 | 3537.55, 1549.98 | 0.03711 ± 0.00015 at 1887.97 ± 2 K | read, unscored |
| 10b | 1 | 9 | 3661.58, 1504.03 | 0.03856 ± 0.00015 at 1915.75 ± 2 K | read, unscored |
| 10b | 1 | 10 | 3847.44, 1443.83 | 0.04047 ± 0.00015 at 1958.94 ± 2 K | read, unscored |
| 10b | 1 | 11 | 3922.62, 1391.59 | 0.04212 ± 0.00015 at 1976.97 ± 2 K | read, unscored |
| 10b | 1 | 12 | 4104.01, 1345.94 | 0.04356 ± 0.00015 at 2021.86 ± 2 K | read, unscored |
| 10b | 1 | 13 | 4232.47, 1378.78 | 0.04252 ± 0.00015 at 2054.91 ± 2 K | read, unscored |
| 10b | 1 | 14 | 4342.62, 1320.00 | 0.04438 ± 0.00015 at 2084.12 ± 2 K | read, unscored, 3.1 px past the 4.8 tick |

Other series on the same pages, not part of the two series the review named, stay refused:

| figure | series | what | decision |
| --- | --- | --- | --- |
| 3 | 1–22 | EMF, slag, and earlier KEMS activities named in the caption | refused: other work |
| 3 | 24 | vertical dotted melting line | refused: not a data series |
| 3 | legend and in-plot 23 | keys and the series numeral | refused: glyphs, checked before the marker list was closed |
| 4 | 1–12, 14, 15 | other mixing energies and calculations | refused: other work |
| 4 | 13 | this study mixing-energy circles | refused: the enthalpy and entropy are the Table 1 duplicates of the 2024 extract. Not re-read in this round |
| 4 | 16 | vertical dotted melting line | refused: not a data series |
| 6 | 1 | CaO, Shornikov 2025 | refused: other paper |
| 6 | 2 | tricalcium silicate, Shornikov 2026 | refused: a different citation |
| 6 | 3 | orthosilicate, Shornikov 2026a | refused: other paper |
| 6 | 4 | this study, Ca, SiO, O, CaSiO3 | refused as a figure read: the points are Tables 3 and 4 |
| 6 | 5 | SiO2, Shornikov et al. 1998 | refused: other paper |
| 7 | 1, 2, 4–9 | CaSiO3, CaTiO3, and BaSiO3 from the named papers | refused: other work |
| 7 | 3 | this study | refused as a figure read: Table 3 and Table 6 hold the numbers |
| 8 | 1–8, 11–13 | EMF, slag, KEMS, and thermochemical mixing energies | refused: other work |
| 8 | 9, 10 | this study, Eqs. (31) and (32) | refused as a figure read: the printed result is the rankinite context row |
| 8 | 14 | vertical dotted decomposition line | refused: not a data series |
| 9a | 1–8 | O, O2, Ca, CaO, SiO, SiO2, Si2O2, CaSiO3, KEMS | refused as a figure read: Tables 3 and 4 |
| 9b | 1–8 | same species, calculated for neutral redox | refused: calculated strokes |
| 10 | 2 | calculated neutral redox, upper band, about 36 mol % oxygen | refused: calculated stroke. The prose bound stays context |
| 10 | in-plot 1 | series numeral | refused: glyph, checked against the open circles |
| 11 | 1–5 | total-pressure deviation | refused: no table, no tick calibration |
| 12 | 1–7 | CAI residual-melt composition and activities | refused: calculated curves |

## Not stored

Evaporation coefficients are adopted from Shornikov 2015 and are not printed. Orifice area, Clausing factors, and ionization cross sections are named in Eqs. (11)–(13) and are not given numerical values. Neutral-redox pressures in Figs. 9b and 10–11 have no table. The study does not explicitly classify its vaporization as congruent or incongruent; the located Table 6 context records the molecular vaporization and gas-phase dissociation statements without assigning that classification.

The binding d-355 ruling is implemented with the existing `initial_charge_only` extraction proxy. The `composition_from_initial_charge` flag is scorer output from regolith-engine t-1696, not an extract token. The experiment context retains the initial charge and row linkage, unreported run sequence and depletion evidence, final-mass absence, and located vaporization statements. `charge_sequence`, `final_mass_kg`, and `vaporization_statement` are context fields and are not currently migrated into store fields.

# Carrillo-Sánchez et al. (2020), phosphorus ablation extraction audit

## Scope and classification

The article reports measured MASI ablation profiles in figures, but does not tabulate their numerical curves, mass-loss series, or post-ablation residue P content. The only numeric measured PO-release result stated in the text is an Allende-particle PO LIF peak between 2100 and 2200 K for particle radii greater than 19 μm (PDF p. 9, §3.2.1). It is stored as a measured-direct typed refusal: the reported result is a signal-peak interval, not a thermodynamic transition, and there is no exact battery quantity for that observable.

The measured peak row retains the printed conditions: about 15 Pa in N2 (PDF p. 3, §2.1), Allende meteorite particles with radius >19 μm, and the linear ramp form `T = c1 + c2 t` (PDF p. 7, Fig. 3 caption). No numeric ramp slope, fO2, or sample composition is printed. The approximately 15 Pa pressure is marked approximate. No room temperature, ambient condition, or pressure is inferred.

Table 3 reports CABMOD and ZoDy integrated planetary outputs. Since the model inputs are not stored in this extraction, the model outputs and ablation percentages are `quoted_attributed` under d-110, not author measurements. The percentages printed in parentheses are stored separately as yield fractions with the canonical `%` unit; their migrated values are fractions. The printed source-specific denominator wording is retained in `yield_basis_as_printed`; it is descriptive text rather than a `PerBasis` token. The table has 15 printed body rows: 12 labeled P/PO/PO2/Total rows and three continuation rows carrying source percentages. All 54 printed mass-flux cells and all 12 percentages are transcribed. The three Total influx uncertainty widths are retained as printed; the source does not state a confidence level.

## Cell audit

Table 3 is on absolute PDF p. 13 and is printed Table 3. MinerU table block p013-t02 and the born-digital pdftotext layer agree on every mass-flux cell's digits and exponent (table QA normalized digit agreement 1.000). The p. 13 render was checked once for the complete row/column layout, row labels, species assignment, units, percentage placement, and uncertainty placement. The draft comparator reported 8 matched mass-flux cells and 46 mismatches. I resolved all 46 against the p. 13 render: they are cells with inline LaTeX powers of ten or multiple printed values in one cell, which the comparator's scalar parser cannot normalize. `A` means the exact digits/sign/exponent agreed between MinerU and pdftotext; `B` means the printed cell was resolved from the render; `A+B` records both independent machine-read agreement and render resolution after comparator mismatch or a flagged value. Every locator below is page 13, Table 3; row/column coordinates are the table body's numbered rows and printed columns. The committed compare CSV preserves its raw statuses.

| Body row | Printed label | Stored numeric cells and audit route |
|---:|---|---|
| 2 | Earth P | c3 `2.8 × 10^-5` A+B; c4 `1.6 × 10^-7` A+B; c5 `3.2 × 10^-5` A+B; c6 `6.1 × 10^-5` A+B; c7 `8.2` A; c8 `28.0 ± 8.1` A+B |
| 3 | Earth PO | c3 `1.1 × 10^-3` A+B; c4 `2.7 × 10^-4` A+B; c5 `1.2 × 10^-3` A+B; c6 `2.5 × 10^-3` A+B |
| 4 | Earth PO2 | c3 `6.0 × 10^-3` A+B; c4 `1.9 × 10^-3` A+B; c5 `6.8 × 10^-3` A+B; c6 `0.015` A |
| 5 | Earth Total | c3 `7.1 × 10^-3` A+B; c4 `2.2 × 10^-3` A+B; c5 `8.0 × 10^-3` A+B; c6 `0.017` A+B and `(41%)` B |
| 6 | Earth continuation | c3 `(25%)` B; c4 `(57%)` B; c5 `(96%)` B |
| 7 | Mars P | c3 `1.1 × 10^-6` A+B; c4 `6.0 × 10^-10` A+B; c5 `6.7 × 10^-6` A+B; c6 `7.8 × 10^-6` A+B; c7 `0.65` A; c8 `2.1 ± 1.2` A+B |
| 8 | Mars PO | c3 `1.8 × 10^-5` A+B; c4 `1.3 × 10^-6` A+B; c5 `1.3 × 10^-4` A+B; c6 `1.5 × 10^-4` A+B |
| 9 | Mars PO2 | c3 `1.5 × 10^-4` A+B; c4 `1.2 × 10^-5` A+B; c5 `8.2 × 10^-4` A+B; c6 `9.8 × 10^-4` A+B |
| 10 | Mars Total | c3 `1.7 × 10^-4` A+B; c4 `1.3 × 10^-5` A+B; c5 `9.6 × 10^-4` A+B; c6 `1.2 × 10^-3` A+B |
| 11 | Mars continuation | c3 `(11%)` B; c4 `(3%)` B; c5 `(93%)` B; c6 `(38%)` B |
| 12 | Venus P | c3 `3.4 × 10^-5` A+B; c4 `2.0 × 10^-7` A+B; c5 `5.4 × 10^-5` A+B; c6 `8.9 × 10^-5` A+B; c7 `12.7` A; c8 `31.0 ± 17.7` A+B |
| 13 | Venus PO | c3 `1.3 × 10^-3` A+B; c4 `2.5 × 10^-4` A+B; c5 `1.8 × 10^-3` A+B; c6 `3.3 × 10^-3` A+B |
| 14 | Venus PO2 | c3 `9.7 × 10^-3` A+B; c4 `1.6 × 10^-3` A+B; c5 `9.1 × 10^-3` A+B; c6 `0.020` A |
| 15 | Venus Total | c3 `0.011` A+B and `(36%)` B; c4 `1.9 × 10^-3` A+B; c5 `0.011` A+B and `(98%)` B; c6 `0.024` A+B and `(53%)` B |
| 16 | Venus continuation | c4 `(67%)` B |

The row labels were checked against the p. 13 render: rows 2–5, 7–10, and 12–15 are Earth/Mars/Venus P, PO, PO2, and Total in that order; rows 6, 11, and 16 continue the parenthetical percentages. The three printed ± cells are marked B because uncertainty glyphs can be lost or misread by text extraction. The parenthetical cells, including mixed cells such as `0.017 (41%)`, are marked B because the flux and percentage are separately stored values.

Other numeric stored cells are the measured PO peak bounds 2100 and 2200 K and the radius threshold >19 μm (PDF p. 9, §3.2.1), plus the approximate pressure 15 Pa (PDF p. 3, §2.1) and wavelength 246.27 nm (PDF p. 3, §2.1). These were checked against the corresponding page renders. The ramp caption on p. 7 gives only `T = c1 + c2 t`; no numeric heating rate is stored.

## Schema and exclusions

The printed global mass-influx quantity has no exact token. `tools/validate_literature_extracts.py::OBSERVATION_TYPES` allows `gas_speciation` as its carrier, but `simulator/battery/migrate.py::QUANTITY_ALIASES` and `simulator/battery/enums.py::Quantity` contain no planet-wide phosphorus mass-influx-rate quantity. `Quantity.MASS_LOSS_RATE` and `Quantity.EVAPORATION_RATE` describe different processes, so the 12 Table 3 flux observations retain descriptive non-token quantity `planetary_phosphorus_mass_influx_rate` and migrate quantity-unknown. The explicit label also prevents `simulator/battery/migrate.py::TYPE_QUANTITY` from assigning its `rate_series` fallback; no rate-series token is used here.

The measured PO peak is also quantity-unknown by design. `Quantity.TRANSITION_TEMPERATURE` is a thermodynamic transition, not an ablation-signal maximum. Its descriptive non-token label is `PO_ablation_peak_temperature`; `transition_point` is only the allowed typed-refusal carrier. The `gas` phase is an exact `simulator/battery/migrate.py::PHASE_MAP` token, so no phase gap is present. Table 3 ablation percentages use exact `Quantity.YIELD_FRACTION` and the printed `%` unit.

Table 1 is XANES speciation, not measured release; Table 2 contains thermodynamic/model inputs. Figures 3 and 6 show normalized PO/Ca LIF curves, and Figures 4, 5, 9–11 show MASI/model profiles; their plotted values were not digitized. They remain figure-read candidates. The supporting-information profiles were not present in the available corpus/supporting-source tree.

## Isolated migration

The real migrator emits 67 observations: 54 typed-unavailable Table 3 flux cells, 12 quoted-attributed yield fractions, and one measured-direct typed-unavailable PO peak interval. Migration has 0 hard issues and 55 unavailable values. All 67 species phases map to `gas`; the only quantity-unknown IDs are the 54 exact-quantity gaps and the one ablation-signal peak gap. All twelve printed `%` values migrate to their fraction values (for example 25% to 0.25 and 98% to 0.98).

Table 3 points retain unknown temperature, fO2, and composition conditions at their page 13/Table 3 locators; total pressure is absent from migrated conditions and identity because Table 3 prints none. The measured PO peak migrates its 2100–2200 K interval, approximate 15 Pa pressure, N2 atmosphere, unknown fO2, and unknown numeric heating rate with their page locators. The focused test checks the identity axes for Earth P, Mars PO2, and Venus Total and rejects any extra unknown quantity or phase.

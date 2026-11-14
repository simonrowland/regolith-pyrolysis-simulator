# Wetzel 2012 SiO vapor-pressure extraction audit

## Coverage and source order

The extraction covers the thesis SiO vapor-pressure measurements in Section 2.3.2, the apparatus and calibration described in Sections 2.2.1–2.2.5, Equations (2.25)–(2.27), and every occupied cell of Table 2.2. The IR spectroscopy chapters are outside scope. The available MinerU content lists contain no tables from this chapter; the table draft and QA both report zero tables. The born-digital `pdftotext-layout.txt` is the transcription source. The PDF render is used only for layout and cell confirmation.

Table 2.2 has exactly **2 printed rows**. The 1400 K row contains only the current-work value; the 298 K row contains the current-work value and four cited comparisons. All five reference comparison cells are retained in the 298 K row's `comparisons` list. Equation (2.27) repeats the 432 ± 4 value already present in Table 2.2, so it is recorded as a repeat and not as a third observation.

## Numeric-cell audit

`(a)` means the born-digital text layer supplied the digits and signs and the render confirmed row/column assignment. ` (b)` means the cell was confirmed directly against the rendered page because the chapter was absent from MinerU or the printed form required visual confirmation. All stored numeric cells below were checked against the cited render.

| Stored cell | Absolute PDF page and printed locator | Read / render confirmation |
|---|---|---|
| Crucible-to-QCM distance 9.7 cm | p. 21, Section 2.2.1 | (b), render confirms apparatus spacing |
| Post-bakeout chamber base pressure 1 × 10⁻¹⁰ mbar | p. 21, Section 2.2.1 | (b), render confirms exponent; context only, not run total pressure |
| QCM frequency 6 MHz; 1 Hz to 12.3 × 10⁻⁹ g/cm² | p. 21, Section 2.2.1 | (b), render confirms both calibration values |
| Four filaments; approximately 17 mm long | p. 23, Section 2.2.1 | (b), render confirms count and approximate length |
| Thermocouple accuracy 1%; calibration error 1.4%; ice-water reference 0 °C | p. 27, Section 2.2.3 | (b), render confirms values and reference |
| Adopted temperature error 2%; aperture and sample-area errors 10%; Clausing-factor error 4%; QCM-rate error 10%; alpha deviation 0.02 | p. 30, Section 2.2.5 | (b), render confirms each value; no extra 10% value is assigned to L/r beyond the text's “same order” description |
| Aperture diameters 0.79 ± 0.06 mm and 0.5 ± 0.04 mm; 325 mesh; 99.99% mass purity | p. 34, Section 2.3.2, Figure 2.11 caption | (b), render confirms decimal commas, uncertainty widths, sample and aperture assignments |
| Alpha quadratic coefficients 0.20821, −3.12269 × 10⁻⁴, 1.35599 × 10⁻⁷ | p. 34, Equation 2.25 | (b), render confirms signs, exponents and decimal commas |
| Table 2.2 row 1400 K: 419 ± 4 | p. 36, Table 2.2, this work | (b), render confirms row and column |
| Table 2.2 row 298 K: 432 ± 4; [58] 321; [60] 410; [61] 360 ± 4; [62] 351 ± 11 | p. 36, Table 2.2 | (b), render confirms all columns and uncertainty marks |
| Pressure-fit coefficients −50.4 ± 0.5 × 10³ and 35.5 ± 0.4 | p. 36, Equation 2.26 | (b), render confirms sign, exponent and decimal commas |
| Repeated ΔH at 298 K: 432 ± 4 | p. 36, Equation 2.27 | (b), render confirms repeat; not duplicated in observations |

The limited MinerU chunks cover PDF pages 2–10, 165–166, and 176–178, so they do not provide a second machine read for this chapter. `tools/mineru_table_draft.py compare` consequently has no MinerU table cells to compare. The accompanying mapping file records the printed table rows, equation mapping, render pages, and figure-only candidates.

## Fits, conditions, and evidence

Equation (2.25) is the authors' quadratic fit to the two measured aperture series. Equation (2.26) is their equilibrium vapor-pressure fit, with pressure obtained from QCM mass rate by Equation (2.20). Both observations remain `measured_reduced` so the measured KEMS evidence cannot bypass apparatus or calibration checks. The point series underlying both fits is shown only in Figure 2.11; no plotted points are digitized or listed as stored parents. Because no numeric point or validity interval is printed in the chapter text, the fit observations carry a located unknown temperature state. The fitted equilibrium pressure is an output, not an experiment's total-pressure condition; total pressure and fO2 remain explicitly unknown.

The source reports ΔH_sub at 1400 K as 419 ± 4 kJ/mol from Equation (2.26) via Equation (2.4); this row links to the stored pressure-fit observation. The 298 K value 432 ± 4 kJ/mol uses heat-capacity and integral inputs cited to [48] and [38], which are not numeric inputs in this chapter extraction, so its evidence is `quoted_attributed`. Printed uncertainty widths have no stated confidence level. The four comparison cells in Table 2.2 are attributed to references [58], [60], [61], and [62].

The paper uses amorphous SiO powder (325 mesh; 99.99% mass purity) and two orifice diameters, 0.79 ± 0.06 mm and 0.5 ± 0.04 mm. It does not print an in-run total-pressure or fO2 condition. The post-bakeout chamber base pressure is retained as an apparatus fact only and is never assigned to either fit's conditions or identity.

Figure 2.11 is not digitised: the numeric pressure series is in JCED Table 2, so the thesis plot is not a substitute for that table. Figure 2.8's copper-melting calibration point is also a figure-read candidate and is not transcribed. The prose calibration check is retained at PDF p. 27 without a graph-derived numeric point; Figure 2.8 is on p. 28.

## Schema gap

The enthalpy quantity is intentionally not mapped to a near-miss token. `simulator/battery/enums.py::Quantity` contains the second-law and third-law enthalpy-of-vaporization members, but no sublimation-enthalpy member. `simulator/battery/migrate.py::QUANTITY_ALIASES` has no exact sublimation-enthalpy alias. Since `gibbs_table` is a valid observation type under `tools/validate_literature_extracts.py::OBSERVATION_TYPES`, omitting the explicit descriptive label would allow `simulator/battery/migrate.py::TYPE_QUANTITY` to infer `delta_fG`. The two Table 2.2 rows therefore retain `values.quantity: sublimation_enthalpy` as a non-token label and migrate as typed refusals. The same label appears in the existing Kambayashi 1985 extract.

## Overlap

The JCED reprint (`wetzel-pucci-gail-2012-sio-evaporation-coefficient`) and thesis cover the same experiment. Thesis Equation 2.25 is JCED Equation 15. Thesis Equation 2.26 is the ln form of JCED Equation 16 at the central value; the thesis coefficient widths are ±500 K and ±0.4, compared with the journal's log10 widths ±1000 and ±2. Table 2.2 reports ±4, compared with JCED Equation 21's ±20. The thesis prints purity `99,99%`; JCED prints `99.9%`. JCED Table 2 holds the numeric series; do not digitise it from thesis Figure 2.11. Keep the thesis digits and uncertainties as printed. The existing `lead-25-wetzel-2012a` extract is a different Applied Spectroscopy infrared paper, not the JCED reprint. Gail et al. 2013 (`gail-2013-sio-nucleation-silicate-dust`) reports the same experiment with a different published fit (Arrhenius alpha; Tv = 49520 ± 1400 K, Sv = 32.52 ± 0.97, dyn cm^-2), not a reprint of these coefficients. Do not count the shared experiment or its graph series twice.

## Migration record

The isolated real-migrator run produces **4 observations, 0 hard issues, and 4 unavailable values**. The focused test pins the four rows, condition state/locator records, the exact two-row table count, known phase tokens, and exactly the two expected quantity-unknown schema-gap ids. The fit equations remain unavailable numeric series in the current store representation; their printed coefficients are retained in the extract.

The identity-axis snapshot from three rows is:

| Observation | Composition | Total pressure | fO2 | Temperature | Reservoir |
|---|---|---|---|---|---|
| Equation 2.25 alpha fit | SiO = 1.0 | unknown | unknown | unknown | unknown |
| Equation 2.26 pressure fit | not applicable | not applicable | not applicable | unknown | unknown (`no reservoir mapped from source`) |
| Table 2.2, 1400 K enthalpy | no identity field | no identity field | no identity field | 1400 K | no identity field |

**Phase correction:** the source describes amorphous SiO powder, not crystal or glass. The sample retains its material string, nominal composition bookkeeping, and printed purity, but has no sample-form token; the pressure observation omits `condensed_form`. No available form token maps this source statement to a reservoir without asserting crystal or glass, so both alpha and P_SAT reservoirs remain typed unknown (`no reservoir mapped from source`). The earlier controller ruling that allowed `condensed_solid` and produced a `Phase.CR` reservoir is superseded. No migrator edit was made.

The review of the earlier migration reported **9 non-hard `IDENTITY_INCOMPLETE` issues**: eight for the alpha fit (subtype, fO2, per, total pressure, reservoir, exposure, temperature, and sweep gas) and one for the pressure fit's temperature. After removing the crystal-phase inference, the real migration reports **10**: those same nine plus the now-correctly-unknown P_SAT reservoir. The unprinted axes remain unknown. The amorphous sample has no mapped reservoir pending the separate migrator work; the test pins both reservoirs as unknown and rules out `Phase.CR`.

The extract has `review_status: draft`. The real-migrator test pins 4 observations, 0 hard issues, 4 unavailable values, the review's 9 previously reported identity incompleteness issues (10 after the phase correction), and both reservoir unknowns. The YAML comma-scalar guard passed in the extraction run (**17 passed**). MinerU table QA found **0 tables across 6 content lists** for this chapter. The table comparison reports **10 unverifiable cells/locators** and no mismatches because the chapter tables were not present in the MinerU chunks and the compare tool cannot verify printed caption evidence for Table 2.2. The focused test pins the sample purity string, nominal composition, render values, and row count. `tests/test_literature_extracts.py` remains **UNVERIFIED-local**: bounded earlier runs were interrupted at 203.46 and 296.41 seconds after 128 passes each.

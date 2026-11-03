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

Figure 2.11 is a figure-read candidate for α(T) and equilibrium pressure points; values are excluded because they appear only in plots. Figure 2.8's copper-melting calibration point is also a figure-read candidate and is not transcribed. The prose calibration check is retained without a graph-derived numeric point.

## Schema gap

The enthalpy quantity is intentionally not mapped to a near-miss token. `simulator/battery/enums.py::Quantity` contains the second-law and third-law enthalpy-of-vaporization members, but no sublimation-enthalpy member. `simulator/battery/migrate.py::QUANTITY_ALIASES` has no exact sublimation-enthalpy alias. Since `gibbs_table` is a valid observation type under `tools/validate_literature_extracts.py::OBSERVATION_TYPES`, omitting the explicit descriptive label would allow `simulator/battery/migrate.py::TYPE_QUANTITY` to infer `delta_fG`. The two Table 2.2 rows therefore retain `values.quantity: sublimation_enthalpy` as a non-token label and migrate as typed refusals. The same label appears in the existing Kambayashi 1985 extract.

## Overlap

Gail et al. 2013 (`gail-2013-sio-nucleation-silicate-dust`) republishes the same Wetzel 2012 Heidelberg SiO Knudsen-cell/QCM experiment and some of the same SiO values. This extract includes the thesis's fit equations and enthalpy comparison table only. Do not count the shared experiment or its graph series twice.

## Migration record

The isolated real-migrator run produces **4 observations, 0 hard issues, and 4 unavailable values**. The focused test pins the four rows, condition state/locator records, the exact two-row table count, known phase tokens, and exactly the two expected quantity-unknown schema-gap ids. The fit equations remain unavailable numeric series in the current store representation; their printed coefficients are retained in the extract.

The identity-axis snapshot from three rows is:

| Observation | Composition | Total pressure | fO2 | Temperature | Reservoir |
|---|---|---|---|---|---|
| Equation 2.25 alpha fit | SiO = 1.0 | unknown | unknown | unknown | unknown |
| Equation 2.26 pressure fit | not applicable | not applicable | not applicable | unknown | SiO, condensed solid |
| Table 2.2, 1400 K enthalpy | no identity field | no identity field | no identity field | 1400 K | no identity field |

**Controller ruling applied:** nominal SiO is represented as a single-component composition of 1.0 for the pure-substance P_SAT identity. The printed 99.99% mass-purity form is retained, and the unspecified remaining 0.01% is noted as not modelled. The sample form is the generic `condensed_solid` token; its note preserves the source's amorphous SiO description without assigning a crystalline polymorph. A series-level experiment record links both fit observations to the two explicit orifice runs, avoiding attribution of either combined fit to only one aperture. The P_SAT identity now migrates its reservoir as SiO in the condensed-solid phase.

**Blocking migration gap:** the alpha fit's `EVAPORATION_COEFFICIENT_ALPHA` identity profile also requires a reservoir (`simulator/battery/identity.py::profile_for`). Its sample composition reaches identity through the explicit point condition, but the real migration leaves `identity.reservoir` unknown. The existing `simulator/battery/migrate.py::_experiment_identity_fields` reservoir fill is scoped to P_SAT and has no extraction-side path for alpha. The source identifies the amorphous SiO solid, so leaving this axis unknown is not source-faithful; mapping it requires a migrator change, which this extraction brief forbids. **Stop status: `BLOCKED: migrator-change-needed`.** No simulator or tool code was edited.

The YAML comma-scalar guard passed after the final metadata update (**17 passed**). MinerU table QA found **0 tables across 6 content lists** for this chapter. The final table comparison reports **10 unverifiable cells/locators** and no mismatches; the chapter tables were not present in the MinerU chunks, and the compare tool cannot verify printed caption evidence for Table 2.2. The focused test passes (**1 passed**) and pins the sample purity string, nominal composition, pressure-fit reservoir, render values, and row count; it does not resolve the alpha reservoir gap. `tests/test_literature_extracts.py` collects 893 cases. A local `-k 'not fidelity_sample_matches_extract'` run was interrupted at 203.46 seconds after 128 passes with 637 deselected; a smaller `-k` partition retry was interrupted at 296.41 seconds after 128 passes. The full suite remains **UNVERIFIED-local**. No mutation proof was run because the alpha identity remains incomplete.

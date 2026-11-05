# Zaitsev et al. (2006) extraction audit

## Source and scope

Transcription used the born-digital MinerU table blocks/markdown and pdftotext layout. The source table drafts are audited by `zaitsev-2006-magnesium-silicate-thermodynamics.cells.csv`; the committed mapping gives the printed table number and selected columns. The comparison output records every stored numeric point that could be matched to a draft cell.

The paper reports KEMS ion-current ratios, not a numeric absolute partial-pressure series. Equations 4 and 5 reduce the ion-current ratio to partial Gibbs energies. The fits and component activities are stored as measured-reduced data with their KEMS lineage. Figure 2 and Figure 3 show KEMS ratio series without numeric tables; their values are figure-read candidates and were not digitized. The 10⁻⁴ Pa statement is sample-preparation vacuum, not the total pressure during the KEMS measurements.

## Row counts and layout

- Table 1 has 3 printed phase-field rows; all three are included.
- Table 2 has 6 printed compositions arranged as two side-by-side triples across 3 physical rows; all 6 compositions and their 12 activities are included.
- Table 4 has 53 property rows. Its two ΔfG(298 K) property rows, for enstatite and protoenstatite, contribute 4 values. The experimental column is quoted from [31]; the calculated column is the authors' model output. Other Table 4 properties are outside this Gibbs-energy-of-formation extraction.

The row/column assignments were checked against the page renders for printed Tables 1, 2, and 4. Table 2's first composition carries the printed footnote marker. Table 4's phase labels are merged across properties; the ΔfG rows are printed table rows 13 and 53 in the MinerU cell draft, not printed row labels. The complete rendered target cells on PDF page 7 were read directly because table-level MinerU/pdftotext digit agreement was only 0.856. Printed table locators remain 1, 2, and 4; the MinerU block indices are used only in the audit mapping.

## Numeric-cell confirmation

Every included table cell and equation cell appears in the cell audit. MinerU and pdftotext agree on the transcribed digits for the born-digital cells. Render confirmation (method b) resolves the flagged footnote marker and checks table row/column assignment; the equations were also checked on their printed pages for signs, uncertainties, and exponents. Table 4's four ΔfG values were render-checked individually. Absolute PDF pages are used in every locator.

## Evidence and schema decisions

- Table 1 coefficients are `measured_reduced`: the authors fit their KEMS-measured ion-current ratio.
- Equations 6 and 7 are `measured_reduced`: the printed relations reduce the measured ratio fit to partial Gibbs energies.
- Table 2 activities and Equations 8 and 9 are `measured_reduced`: the authors calculate activities and formation Gibbs energies from KEMS ratios and the printed Gibbs–Duhem/thermodynamic relations.
- Table 4 experimental ΔfG values are `quoted_attributed` to [31]. Its calculated values and Equations 16 and 17 are `model_derived` outputs of the authors' thermodynamic optimization.
- The paper describes a global optimization of the MgO–SiO2 liquid and crystalline thermodynamic functions using experimental component activities and the system's thermochemical and phase-equilibrium data. The model-function lineage therefore names every such input row retained here: the KEMS-derived Equations 8 and 9, all 12 Table 2 activities, and the two Table 4 experimental ΔfG values. The article also used activity and phase-equilibrium data from other cited sources; those external rows are not duplicated in this extract. This parent assignment follows the paper's stated global-fit inputs.
- The exact fitted-ratio, partial-molar Gibbs, and ΔfG coefficient-function forms have no matching schema token. `Quantity.ION_INTENSITY_RATIO` names a ratio value, and `Quantity.ACTIVITY_COEFFICIENT_TEMPERATURE_FIT` names an activity-coefficient fit; neither is the printed ln-ion-ratio fit. `Quantity.PARTIAL_MOLAR_ENTHALPY` has no corresponding partial-molar Gibbs token, while `Quantity.DELTA_FG` is point-valued rather than a coefficient function. `QUANTITY_ALIASES` and `TYPE_QUANTITY` offer no exact alternative, and `OBSERVATION_TYPES` contains no function-coefficient observation form. The extract uses descriptive non-token quantity labels and `typed_unavailable`; migration leaves exactly these nine quantities unknown instead of inferring `delta_fG` from `gibbs_table`. Similar partial-molar Gibbs labels appear in `kems-111-ichise-1982` and `sugawara-2013-soda-lime-melt-na2o-activity`, and ratio-fit coefficients appear as described records in `bennour-1996-ternary-cs-na-silicate`; none supplies an exact token for these printed forms. Printed crystalline and liquid states use `Phase.CR` and `Phase.L`; the liquid activity compositions use the existing `mole_fraction` basis in `PHASE_BASIS_TOKENS`, with no phase or composition schema gap. Code checks: `simulator/battery/enums.py::Quantity`, `simulator/battery/enums.py::Phase`, `simulator/battery/enums.py::PhaseRole`, `simulator/battery/enums.py::PHASE_BASIS_TOKENS`, `simulator/battery/migrate.py::QUANTITY_ALIASES`, `simulator/battery/migrate.py::TYPE_QUANTITY`, and `tools/validate_literature_extracts.py::OBSERVATION_TYPES`.
- No total pressure or oxygen fugacity is stated for these rows. Table 1's printed temperature intervals and temperature-dependent equations are not single-point temperature conditions; their ranges remain in the source values. No ambient, room-temperature, vacuum, or 1-atm condition was added.

## Isolated migration

The extract was migrated with the repository `Migrator` in isolation. Result: 25 observations, 0 hard issues, 9 typed-unavailable values, 0 quantity-unknown observations beyond the nine explicitly schema-gapped fit/function rows, and 0 phase-unknown observations. The 12 Table 2 activities migrated with phase `l`, temperature 1873 K, and their row compositions; pressure and fO2 remain unknown. Table 4's values migrated from J/mol to the canonical kJ/mol representation. No battery-store regeneration was committed.

# Powell (1971) vapour and thermochemistry extraction audit

## Source and extraction

Powell, D. G. (1971), *A thermodynamic study of the sulphates and related compounds of alkali and alkaline earth metals at high temperatures*, PhD thesis, University of St Andrews. Source ID: `powell-1971-alkali-sulfate-metasilicate-vapour`.

The extraction contains 290 observations: all printed rows in Tables 1–13 (the thesis's own Knudsen, torsion, transpiration, vapour composition and fit results), quoted comparison data on pages 45–47, and the printed equilibrium/dissociation values on pages 100–101. The 13 principal tables contain respectively 13, 18, 12, 14, 13, 7, 3, 4, 17, 8, 5, 13, and 9 body rows (136 rows total). Table 6's seven rows are the printed second/third-law fit results; their high/low segments do not have numeric temperature bounds. Pressure, raw rate, and fit rows retain separate observation IDs and explicit `derived_from` links. Data are stored in printed values and units. No condition is supplied when the source does not print it.

## Complete MinerU numeric/table-block inventory

The table draft indexes 39 blocks. Printed captions/row counts below use the thesis pagination (PDF page); blank caption means the MinerU block is an uncaptained layout, not a printed table number.

| PDF page / block | Printed caption and body rows | Disposition |
|---|---|---|
| 20 / p020-t01 | Knudsen Clausing-factor calculation, 4 | Apparatus calculation, not observed vapour or thermochemical data. |
| 28 / p028-t02 | Literature evaporation coefficients, 7 listed values | Background survey, not Powell measurements; not a requested pressure/thermochemistry rail. |
| 29 / p029-t03 | Evaporation-coefficient correction factors, 4 | Method correction factors, not experiment results. |
| 45 / p045-t04 | Eyber et al. Na2SO4 vapour pressure, 5 | Extracted as attributed comparison values. |
| 45 / p045-t05 | Eyber et al. log Kp/atm, 6 | Extracted as attributed printed log values. |
| 46 / p046-t06 | Kroger & Stratmann Na2SO4 vapour pressure, 8 | Extracted as attributed comparison values. |
| 47 / p047-t07 | Nickel/platinum K2SO4 vapour pressure, 8 | Extracted as attributed comparison values. |
| 54 / p054-t08 | Instrument calibration readings, 8 | Calibration data, not vapour/thermochemistry results. |
| 58 / p058-t09 | Transpiration apparatus flow settings, 3 | Method settings; a gas flow is not pressure. |
| 62 / p062-t10 | Molecular-flow conductance values, 3 | Apparatus calculation. |
| 62 / p062-t11 | Knudsen cell/orifice dimensions, 3 | Apparatus geometry, captured in the experiment row metadata where relevant. |
| 64 / p064-t12 | Thermocouple calibration values, 4 | Instrument calibration, not sample temperature observations. |
| 71 / p071-t13 | Na2SO4 vapour-species stoichiometry, 4 | Equation/layout transcription, not a numeric measurement table; species interpretation retained in source discussion, not inferred into observations. |
| 79 / p079-t14 | Table 1, 13 | Extracted. |
| 81 / p081-t15 | Table 2, 18 | Extracted. |
| 83 / p083-t16 | Table 3, 12 | Extracted. |
| 85 / p085-t17 | Table 4, 14 | Extracted. |
| 87 / p087-t18 | Table 5, 13 | Extracted. |
| 92 / p092-t19 | Table 6, 7 | Extracted. |
| 100 / p100-t20 | Na2SO4 equilibrium/dissociation values, 5 | Extracted as printed; schema gap for combined positive K and dissociation degree. |
| 101 / p101-t21 | Li2SO4 equilibrium/dissociation values, 5 | Extracted as printed; schema gap for combined positive K and dissociation degree. |
| 104 / p104-t22 | Table 7, 3 | Extracted. |
| 106 / p106-t23 | Table 8, 4 | Extracted. |
| 108 / p108-t24 | Molecular-weight calculation, 3 | Derived explanatory calculation; no measured gas-species pressures. |
| 108 / p108-t25 | Molecular-weight calculation, 4 | Derived explanatory calculation; no measured gas-species pressures. |
| 111 / p111-t26 | Transpiration thermodynamic calculation, 3 | Calculation discussion; the measured reported rows are included in Table 9/10; no additional independent printed observation. |
| 116 / p116-t27 | Table 9, 17 | Extracted. |
| 119 / p119-t28 | Table 10, 8 | Extracted. |
| 130 / p130-t29 | Equation fragments, no body rows | Not a numeric table. |
| 130 / p130-t30 | Effusion torsion constants, 5 | Apparatus calibration constants, not sample measurements. |
| 131 / p131-t31 | Table 11, 5 | Extracted. |
| 136 / p136-t32 | Table 12, 13 | Extracted. |
| 142 / p142-t33 | Table 13, 9 | Extracted. |
| 155 / p155-t34 | Equation fragments, no body rows | Not a numeric table. |
| 162 / p162-t35 | Appendix II relevant thermodynamic data, 27 | Reference compilation inputs, not Powell measurements or measured vapour/thermochemistry rows. |
| 163 / p163-t36 | JANAF thermochemical data header, no body rows | Header fragment for p163-t37; reference compilation, out of measurement rail. |
| 163 / p163-t37 | JANAF thermochemical data, 21 | Reference compilation inputs, not Powell measurements or measured vapour/thermochemistry rows. |
| 163 / p163-t38 | Kelley thermochemical data header, 2 | Reference compilation header/values, not Powell measurements. |
| 163 / p163-t39 | Kelley thermochemical data, 2 | Reference compilation inputs, not Powell measurements or measured vapour/thermochemistry rows. |

Figure-only values are not transcribed. Figures 14–18 are candidates for future figure-read work, including the plotted pressure-fit lines and the diagrammed species relations.

## Quantity and phase schema gaps

The unresolved pressure observations use descriptive non-token labels `apparent_knudsen_pressure_assumed_mw`, `torsion_effusion_total_pressure`, and `quoted_salt_vapour_pressure_unresolved_gas_species`. Controller ruling seq. 2 (2026-10-28) says not to bind these to `p_sat`: Knudsen P′ depends on the assumed vapour molecular weight and torsion P is over an unresolved mixture. The closed `Quantity` enum defines `Quantity.P_SAT` and `Quantity.P_PARTIAL` as its pressure tokens (`simulator/battery/enums.py:65-66`); the p_sat identity path derives gas phase from a named gas species (`simulator/battery/migrate.py:5379-5387`). No total mixed-vapour-pressure token exists. The raw mass-loss observations use exact `rate_series` / `mass_loss_rate` tokens (the same unit `mg min-1` is retained because current unit conversion does not accept it).

Second/third-law fit outputs are recorded as measured-reduced rows derived from their stored pressure rows. Their descriptive label `vaporisation_enthalpy_per_mole_gas_unresolved_reaction` is quantity-unknown because the source does not state one named reaction for the fit; the exact `Quantity.ENTHALPY_OF_VAPORIZATION_2ND_LAW` token at `simulator/battery/enums.py:90` is reserved for a named reaction under the controller ruling. The torsion tables directly report the pressure result and give no intermediate angular reading, so those reported results are `measured_direct`; no unavailable intermediate is invented as a parent. The p100–101 combined positive equilibrium constant and dissociation-degree entries use `positive_equilibrium_constant_and_dissociation_degree`: `Quantity.LOG10_K_REACTION` is the logarithmic token (`simulator/battery/enums.py:70`), with no exact positive-K-plus-dissociation-degree quantity. `gibbs_table` is an allowed observation type (`OBSERVATION_TYPES`, `tools/validate_literature_extracts.py:79-100`), and `TYPE_QUANTITY` maps an absent `gibbs_table` quantity to `Quantity.DELTA_FG` (`simulator/battery/migrate.py:435-444`); therefore the extract records an explicit descriptive quantity label and verifies it remains unknown. Pages 100–101 are Powell's own JANAF equilibrium calculations, recorded as `model_derived` with the JANAF calculation named as the model. The test asserts the exact migrated quantity-unknown set and confirms all present phases resolve.

## Cell audit and render checks

Machine input order was MinerU tables/content lists, then `pdftotext-layout.txt`; the PDF was used only for render confirmation. The 13 printed principal-table row layouts were checked against page renders. All MinerU/pdftotext disagreement/flag cells were settled against renders, including comma-decimal readings, exponent/sign OCR, and the Table 13 first-series temperature (1241 K, not OCR 1841 K). On PDF page 142, the render also confirms the printed molecular weight is 122.1 (the pdftotext layer reads 132.1); the source caption is retained as printed and that value supplies the assumed-molecular-weight field. The page-116 Table 9 render reads 8.49 × 10⁻³ torr at 1036 K; the extract and focused test now preserve `0.00849` and `8.49 x 10-3` at row 5. The 45–47 comparison tables were also render-checked. Their body counts are 5 Eyber pressures, 6 Eyber log-K values, 8 Kroger–Stratmann pressures, and 8 nickel/platinum K2SO4 pressures. Renders were used for pages 45–47, 79, 81, 83, 85, 87, 92, 104, 106, 116, 119, 131, 136, and 142; every stored number is either corroborated by both machine reads with row/column placement checked, or was taken from the listed page render. Render pins cover pages 45, 79, 81, and 116.

The thesis states on page 46 that sodium sulphate melts at 884°C, and its Tables 1–2 label the corresponding high-temperature salt as liquid. The Eyber Na2SO4 rows at 1000–1400°C, the page-100 Na2SO4 rows at 1200 K and 1164 K, the Table 6 high-temperature Li2SO4 and Na2SO4 fits, and the page-101 Li2SO4 rows at 1130–1214 K therefore use phase `l`; the page-101 1127 K row remains `cr`. The six Eyber log-K rows omit a reaction because page 45 prints the pressure ratio `pNa2O · pSO3 / pNa2SO4`, not the previous solid-salt reaction string. Pages 100–101 are Powell's own calculations from JANAF data and are classified `measured_reduced`, with a derivation note and located declaration that the cited JANAF free-energy inputs are not tabulated with these rows.

`tools/mineru_table_draft.py compare` was run against the generated index. It reports `matched=0 no-match=0 mismatch=0 presence-only=0 locator-error=0 unverifiable=0 total=0`: this comparer only consumes `values.points[].value`, whereas this extract's established scalar row shape stores each table cell in named fields. It therefore contributes no cell-level comparison for this schema shape; the per-cell audit and independent renders above are the actual verification. The command output and mapping are committed alongside this audit.

## Migration and verification

Isolated migration: 290 observations; 0 conditional-field hard issues; 647 queued/unavailable migration values (including ten derivation records with unresolved JANAF inputs, the 167 schema-gap observations, and unmapped printed units/fields); 167 quantity-unknown, exactly the expected descriptive labels; 0 phase-unknown. There are 948 `identity_incomplete` validation notices because the required profile axes include conditions the source does not print; those axes remain unknown as required by the no-invented-condition rule. Three migrated identity samples: `powell_1971_t01_r02_mass_loss_rate` has composition/P/fO2 unknown and T=1034 K; `powell_1971_t01_r02_apparent_knudsen_pressure` carries T=1034 K but has no quantity profile axes because the ruled quantity remains unknown; `powell_1971_eyber_na2so4_r01_log_kp` has composition/P/fO2 not-applicable and T=1273.15 K (1000 °C printed). Six reaction-log observations have no species identity because their reaction-constant profile has no species axis. Seven Table 6 fit rows retain missing temperature because the table gives high/low line segments without numeric bounds. No observation has a total-pressure value in its identity: the source reports calculated/effusion vapour pressure as an output and does not print experimental total pressure. The focused test asserts row count, exact quantity-unknown identities, corrected liquid/crystalline phases, Powell's reduced-evidence derivations, the page-116 rendered value, expected species/temperature gaps, no total-pressure identity value, and point-condition locators.

Expected generated battery-store change on regeneration: +290 Powell observations (including 167 intentionally quantity-unknown observations); no generated store files are included in this change.

# Ni et al. (2021), Table 1 transcription audit

Source machine text: `pdftotext-layout.txt`, PDF p. 41; born-digital text layer. Table row and column positions and all cells below were checked against the full-page 160 dpi render (`pdftoppm`, PDF p. 41). The render check covered every listed data cell. The MinerU directory/content-list JSON is absent from both configured corpus locations; `tools/mineru_table_draft.py` (offer-21 line; run standalone) was invoked, but reported no content-list JSON. No MinerU compare output exists for this source.

Printed count: 18 data rows (Et1Cu starting powder, Cu 1.15 fusion control, and 16 levitation-heated samples). The paper calls the latter 17 experiments inclusive of the control. Header plus footnote are not counted as data rows.

## Cell transcription and render checks

Each row lists every Table 1 data cell in source column order: sample, d0 (mm), d (mm), M0 (mg), M (mg), ΔM (%), tr (s), tc (s), T (°C), CCu (ppm), δ65Cu (‰), 2 s.e., n. For each row, the p. 41 render check covered all listed cells and confirmed row/column assignment; the pdftotext character stream supplies the transcription.

| Sample | d0 | d | M0 | M | ΔM | tr | tc | T | CCu | δ65Cu | 2 s.e. | n | Render check |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| Et1Cu | - | - | - | - | - | - | - | - | 1185 | 3.46 | 0.02 | 12 | p. 41: all data cells checked against render |
| Cu 1.15 | 2.3 | 2.42 | 19.664 | - | - | - | - | - | 1129 | 3.71 | 0.03 | 4 | p. 41: all data cells checked against render |
| Cu 1.12 | 2.42 | 2.36 | 20.044 | 19.822 | 1.11 | 8 | 17.9 | 1750 | 517 | 7.10 | 0.04 | 8 | p. 41: all data cells checked against render |
| Cu 1.14 | 2.38 | 2.32 | 19.072 | 18.486 | 3.07 | 4 | 6.7 | 2150 | 50 | 16.14 | 0.06 | 8 | p. 41: all data cells checked against render |
| Cu 1.17 | 2.34 | 2.31 | 20.008 | 19.270 | 3.69 | 8 | 10.0 | 2150 | 34 | n.d. | - | - | p. 41: all data cells checked against render |
| Cu 1.18 | 2.31 | 2.39 | 19.958 | 18.632 | 6.64 | 16 | 18.6 | 2150 | 35 | n.d. | - | - | p. 41: all data cells checked against render |
| Cu 1.2 | 2.43 | 2.36 | 20.040 | 19.180 | 4.29 | 30 | 33.8 | 2150 | 2.7 | 21.57 | 0.19 | 6 | p. 41: all data cells checked against render |
| Cu 1.3 | 2.43 | 2.19 | 20.196 | 18.632 | 7.74 | 60 | 59.1 | 2150 | 0.028 | b.d. | - | - | p. 41: all data cells checked against render |
| Cu 1.4 | 2.44 | 2.33 | 20.160 | 17.724 | 12.08 | 90 | 75.4 | 2150 | 0.028 | b.d. | - | - | p. 41: all data cells checked against render |
| Cu 1.5 | 2.44 | 2.33 | 20.154 | 17.986 | 10.76 | 120 | 84.5 | 2150 | 0.31 | b.d. | - | - | p. 41: all data cells checked against render |
| Cu 1.13 | 2.41 | 2.41 | 19.558 | 19.306 | 1.29 | 4 | 7.9 | 2000 | 323 | 9.59 | 0.11 | 8 | p. 41: all data cells checked against render |
| Cu 1.11 | 2.43 | 2.41 | 20.008 | 19.590 | 2.09 | 8 | 12.3 | 2000 | 126 | 13.66 | 0.06 | 8 | p. 41: all data cells checked against render |
| Cu 1.9 | 2.44 | 2.30 | 20.220 | 19.720 | 2.47 | 16 | 16.7 | 2000 | 73 | 13.91 | 0.05 | 8 | p. 41: all data cells checked against render |
| Cu 1.19 | 2.31 | 2.39 | 19.964 | 19.296 | 3.35 | 16 | 17.5 | 2000 | 42 | n.d. | - | - | p. 41: all data cells checked against render |
| Cu 1.10 | 2.42 | 2.42 | 20.140 | 19.388 | 3.73 | 16 | 25.5 | 2000 | 18 | 21.07 | 0.03 | 6 | p. 41: all data cells checked against render |
| Cu 1.7 | 2.41 | 2.30 | 20.082 | 19.572 | 2.54 | 30 | 34.4 | 2000 | 12 | 20.25 | 0.06 | 6 | p. 41: all data cells checked against render |
| Cu 1.8 | 2.42 | 2.33 | 20.046 | 18.822 | 6.11 | 60 | 62.9 | 2000 | 0.056 | b.d. | - | - | p. 41: all data cells checked against render |
| Cu 1.1 | 2.41 | 2.42 | 19.902 | 17.900 | 10.06 | 120 | 108.6 | 2000 | 0.035 | b.d. | - | - | p. 41: all data cells checked against render |

## Disagreements, omissions, and schema gaps

- Table 1 Cu 1.15 δ65Cu is 3.71‰ in pdftotext and the PDF render. Prose on PDF p. 14 states 3.17‰; it is a cross-section discrepancy, not a Table 1 cell ambiguity. Table 1 is the requested data source, so 3.71‰ is stored.
- `n.d.` (Cu 1.17, 1.18, 1.19) and `b.d.` (Cu 1.3, 1.4, 1.5, 1.8, 1.1) in δ65Cu are preserved as typed refusals. Dashes remain in the context transcription. All Cu ppm values are numeric, including italic near-detection-limit values 0.028, 0.028, 0.056, and 0.035 ppm.
- Diameter d0/d and post-run mass M are preserved verbatim in each context row and marked typed-refused schema gaps; `tr` remains in the raw table row because there is no existing field for recorded peak-temperature duration, while `tc` is used as corrected duration. The source discusses Na2O, K2O, and FeO evaporation as likely contributors, but ΔM remains total mass loss and is not assigned to a species.
- The initial glass prose prints 1174 ± 121 ppm Cu attributed to Ni and Zhang (2016). It is stored as a separate `quoted_attributed` observation, distinct from Table 1 Et1Cu at 1185 ppm. Figures 3, 5, and 6 contain plotted trends and comparison values; they are figure-read candidates and are not extracted in this pass.
- The starting composition source text spells the component `FeOt` (10.67 wt%) and is preserved verbatim in a p. 8 context row. Per the controller ruling, the normalized composition uses the existing `FeOT` token, which the migrator converts to FeO with its total-iron-reported-as-FeO derivation note and notice. No separate FeO or Fe2O3 component is included.
- The extract retains experiment method text `levitation`, but no closed observation `MethodToken` represents aerodynamic levitation; no incorrect vacuum/Langmuir method was substituted. The control remains identified as a fusion control, and the initial assay is not assigned an experimental method.
- Quenched heated samples are glass measured after cutting laser power; the source statement and 99.999% Ar condition are on PDF p. 10. Starting powder/control rows have no row-specific quench statement and are marked `not_stated`.
- Apparatus name, laser power, and pyrometer are located on PDF p. 9. The one-atmosphere levitation statement is on PDF p. 25; the total-pressure note states `1 atm = 101325 Pa`. The PDF p. 22 citation was removed.
- Table 1 Et1Cu and Cu 1.15 each have Cu concentration and δ65Cu observations with unknown temperature. The source prints no temperature for these rows. Cu 1.15 has M0 19.664 mg (stored as 0.000019664 kg); Et1Cu M0 is a dash and no mass is invented.
- All Table 1 δ65Cu values are reported relative to NIST SRM 976. ERM-AE633 is retained as the bracketing standard, with the printed +0.01‰ correction recorded; table values are unchanged.

## Migration and condition audit

The isolated migration yields 53 observations (16 mass-loss + 16 heated Cu-residue + 18 isotope rows + 2 unheated Table 1 Cu concentrations + 1 separately attributed prior Cu assay), 0 hard issues, and 8 unavailable isotope values corresponding exactly to printed `n.d.`/`b.d.`. There are 61 migration queue entries, including unavailable method tokens and the 8 typed isotope refusals. T_C reaches identity as 2023.15 K / 2423.15 K / 2273.15 K for heated rows; starting and control observations retain unknown temperature. Heated mass-loss identities carry total pressure and mole-fraction composition. Heated residue identities now carry subtype `element_ppm_by_mass`, their printed M0 sample mass, row temperature, and the FeOT total-iron notice. fO2 remains unknown because the source does not print a run fO2.

| Migrated row | Quantity profile | T (K) | Total pressure (Pa) | Composition | fO2 (Pa) | Sample mass (kg) |
|---|---|---:|---:|---|---|---:|
| `ni_2021_cu_1_12_bulk_mass_loss` | mass_loss_fraction | 2023.15 | 101325 | mole fraction; FeO component from FeOT | unknown | unknown |
| `ni_2021_cu_1_12_cu_residue` | residue_component_composition | 2023.15 | 101325 | printed wt%; FeOT | unknown | 0.000020044 |
| `ni_2021_cu_1_14_bulk_mass_loss` | mass_loss_fraction | 2423.15 | 101325 | mole fraction; FeO component from FeOT | unknown | unknown |

## Focused value pins

`tests/test_literature_extracts.py::test_ni_2021_table1_render_pins_and_row_count` pins ΔM=1.11%, CCu=517 ppm, δ65Cu=7.10‰ for Cu 1.12 and Table 1 control δ65Cu=3.71‰, with the 18-row count, all checked against the PDF p. 41 render. `test_ni_2021_review_findings_are_pinned` pins isotope standard/correction, locators, the two starting/control measurements, the quoted 1174 ± 121 ppm assay, and the absence of an unprinted oxide total. `test_ni_2021_residue_and_unheated_measurements_migrate` proves residue subtype, sample mass, value and temperature, plus unheated values with unknown temperatures. Mutating the reporting standard from NIST SRM 976 to ERM-AE633 made the review pin fail at the standard assertion; the source was restored.

`tests/test_yaml_flow_scalar_commas.py`: 17 passed. Full `tests/test_literature_extracts.py`: 557 passed in 254.18s. Focused Ni regressions: 4 passed. Extract fidelity validator: OK. Isolated migration: 53 observations, 0 hard issues, 8 unavailable, queue 61.

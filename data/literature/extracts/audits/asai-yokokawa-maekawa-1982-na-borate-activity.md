# Asai, Yokokawa & Maekawa (1982) activity audit

## Extraction and evidence decision

Table 2 prints 32 rows of relative partial molar quantities at 1173 K. Its ΔG(Na2O) column is in kJ·mol−1 and refers to the Na2O·2B2O3 reference melt. The table does not print absolute a(Na2O) for that reference melt, so no absolute activity can be recovered. Each row preserves the printed relative ΔG and uncertainty and is typed-refused for absolute a(Na2O)/a(NaO0.5), with reason `reference-melt absolute activity not printed`. No `exp(ΔG/RT)` values or Na2O-to-NaO0.5 conversion are stored.

The published method is the emf of a concentration cell with Na2O·2B2O3 as the reference melt (PDF page 2 / printed page 1006, Experimental section). The abstract states measurements near 1150 K; Table 2 is explicitly evaluated at 1173 K. The table reports liquid-melt quantities. Sample preparation includes melting near 1300 K, glass formation and quenching before measurement; no quenched-glass activity is measured. The source does not state total pressure or numeric oxygen fugacity, so both remain unknown. The source gives the Table 2 standard/reference melt verbatim in each row.

The paper calculates relative ΔG from its measured emf temperature series (Table 1). Those Table 1 readings are not stored as activity inputs; no derived absolute activity row is admitted. The requested activity rail therefore remains unavailable. The relative ΔG quantity itself has no dedicated quantity token in the extract schema and is retained within typed-refusal evidence. This is the schema gap; no schema or migrator change was made.

## Full-cell audit

Printed Table 2 is on PDF page 4 / printed page 1008. MinerU's table block is `p004-t03`; its adjacent `p004-t04` block contains the confidence-limit footnote, not data rows. The row/column layout and all 32 data rows were checked against the page render. The pdftotext layer does not contain a readable Table 2, so each stored ΔG and uncertainty cell was confirmed by render (audit path b); the x column was also render-checked for composition assignment. The table draft QA reported low numeric agreement (0.317) and 128 numeric mismatches, so no unrendered draft cell was treated as confirmed. The full mismatch list is not a transcription source; Table 2 cells below are from MinerU and render.

| Table row | Published formula amounts | x (Al2O3) | ΔG(Na2O), kJ·mol−1 | width after ±, kJ·mol−1 | Cell evidence |
|---:|---|---:|---:|---:|---|
| 1 | Na2O + 2 B2O3 + x Al2O3 | 0.4 | -1.1 | 0.2 | render, p. 1008 |
| 2 | Na2O + 2 B2O3 + x Al2O3 | 0.6 | -10.9 | 0.9 | render, p. 1008 |
| 3 | Na2O + 2 B2O3 + x Al2O3 | 0.8 | -21.1 | 0.3 | render, p. 1008 |
| 4 | Na2O + 2 B2O3 + x Al2O3 | 1.0 | -25.1 | 0.5 | render, p. 1008 |
| 5 | Na2O + 3 B2O3 + x Al2O3 | 0 | -24.9 | 0.7 | render, p. 1008 |
| 6 | Na2O + 3 B2O3 + x Al2O3 | 0.2 | -31.5 | 0.6 | render, p. 1008 |
| 7 | Na2O + 3 B2O3 + x Al2O3 | 0.4 | -36.1 | 0.3 | render, p. 1008 |
| 8 | Na2O + 3 B2O3 + x Al2O3 | 0.5 | -41.9 | 1.5 | render, p. 1008 |
| 9 | Na2O + 3 B2O3 + x Al2O3 | 0.6 | -43.6 | 1.2 | render, p. 1008 |
| 10 | Na2O + 3 B2O3 + x Al2O3 | 0.8 | -54.4 | 0.5 | render, p. 1008 |
| 11 | Na2O + 3 B2O3 + x Al2O3 | 0.9 | -56.3 | 0.6 | render, p. 1008 |
| 12 | Na2O + 3 B2O3 + x Al2O3 | 1.0 | -59.6 | 1.1 | render, p. 1008 |
| 13 | Na2O + 4 B2O3 + x Al2O3 | 0 | -59.6 | 0.4 | render, p. 1008 |
| 14 | Na2O + 4 B2O3 + x Al2O3 | 0.25 | -60.8 | 0.5 | render, p. 1008 |
| 15 | Na2O + 4 B2O3 + x Al2O3 | 0.375 | -67.8 | 0.4 | render, p. 1008 |
| 16 | Na2O + 4 B2O3 + x Al2O3 | 0.5 | -67.8 | 0.4 | render, p. 1008 |
| 17 | Na2O + 4 B2O3 + x Al2O3 | 0.7 | -71.6 | 1.7 | render, p. 1008 |
| 18 | Na2O + 4 B2O3 + x Al2O3 | 0.9 | -79.9 | 0.8 | render, p. 1008 |
| 19 | Na2O + 4 B2O3 + x Al2O3 | 1.0 | -78.9 | 0.2 | render, p. 1008 |
| 20 | Na2O + 9 B2O3 + x Al2O3 | 0 | -99.2 | 1.5 | render, p. 1008 |
| 21 | Na2O + 9 B2O3 + x Al2O3 | 0.2 | -96.7 | 1.0 | render, p. 1008 |
| 22 | Na2O + 9 B2O3 + x Al2O3 | 0.4 | -106.6 | 1.4 | render, p. 1008 |
| 23 | Na2O + 9 B2O3 + x Al2O3 | 0.6 | -110.0 | 0.6 | render, p. 1008 |
| 24 | Na2O + 9 B2O3 + x Al2O3 | 0.8 | -119.0 | 0.7 | render, p. 1008 |
| 25 | Na2O + 9 B2O3 + x Al2O3 | 1.0 | -128.0 | 1.2 | render, p. 1008 |
| 26 | Na2O + 15 B2O3 + x Al2O3 | 0 | -117.3 | 0.2 | render, p. 1008 |
| 27 | Na2O + 15 B2O3 + x Al2O3 | 0.2 | -118.4 | 0.8 | render, p. 1008 |
| 28 | Na2O + 15 B2O3 + x Al2O3 | 0.3 | -122.4 | 1.1 | render, p. 1008 |
| 29 | Na2O + 15 B2O3 + x Al2O3 | 0.4 | -127.4 | 0.8 | render, p. 1008 |
| 30 | Na2O + 15 B2O3 + x Al2O3 | 0.6 | -126.4 | 0.7 | render, p. 1008 |
| 31 | Na2O + 15 B2O3 + x Al2O3 | 0.8 | -134.5 | 1.1 | render, p. 1008 |
| 32 | Na2O + 15 B2O3 + x Al2O3 | 1.0 | -144.6 | 0.4 | render, p. 1008 |

The Table 2 footnote says widths following ± denote 95 per cent confidence limits. Table 1 supplies the concentration-cell emf measurements at temperatures around 1150 K; Table 2 is the 1173 K relative ΔG summary. No numeric values were read from Figures 2–5; they are figure-read candidates only and excluded.

## Table tooling and migration

The MinerU draft QA reported low agreement against pdftotext; because the table is not present in the text layer, all stored numeric cells were resolved by render. `mineru_table_draft.py compare` reports no comparable numeric points: this extract preserves ΔG under a typed-refusal payload field rather than a point value for the `activity` quantity, avoiding a false interpretation as an activity. The focused test pins five rendered ΔG/uncertainty pairs and the printed row count; a mutation of a pinned ΔG from -1.1 to -1.2 is rejected.

Isolated migration emitted 32 unavailable activity observations, 0 hard issues, and 128 queue entries (identity-axis unknowns and the typed refusal). Composition reaches each identity from the printed formula amounts normalized by the existing `composition_mol` path; temperature reaches every identity as 1173 K. Total pressure and fO2 remain unknown because they are not printed. Expected store delta without regeneration: +32 observations, 0 admitted activity values.

The standalone literature validator accepts this extract. The focused regression and YAML comma/key guard cases pass (19 tests). The full `tests/test_yaml_flow_scalar_commas.py` whole-store scan exceeded 300 seconds. `tests/test_literature_extracts.py` reported 150 passing tests; its whole-store `test_repo_extracts_validate_green` exceeded its 300-second watchdog, and the remaining run was interrupted.

# Neuman et al. K–Cu extraction audit

**Source:** Neuman et al. (2022; online 2021), DOI 10.1016/j.gca.2021.09.035. Source ID `neuman-2021-k-cu-evaporation-isotope-fractionation`.

## Input and page audit

| Table | PDF page | Machine text | Render check | Result |
|---|---:|---|---|---|
| 1, starting basalt | 11 | `text/neuman-2021-k-cu-evaporation-isotope-fractionation/pdftotext-layout.txt` | Rendered PDF p. 11 | 11 oxide rows, all wt% and 1σ values, SUM 98.97, and Fe reported as Fe2O3 confirmed. |
| 2, runs | 51 | Same pdftotext file | Rendered PDF p. 51 | 20 rows confirmed. Names and column assignments checked; all numeric cells, signs, decimals, uncertainty cells, and blank cells agree. |
| 3, vapor-saturation/model results | 30 | Same pdftotext file | Rendered PDF p. 30 | The study and attributed literature rows were checked. The study model outputs are retained as context because their input observations are not stored here. |

The text corpus has no MinerU markdown or content-list JSON at either expected location (`text/<sid>/mineru/` or `raw/<sid>/ocr-mineru/`); the older review corpus contains neither. The controller-staged standalone converter was tried at `tools/mineru_table_draft.py (offer-21 line; run standalone)`; it reported `no MinerU content-list JSON found`. Therefore no MinerU draft or compare output exists. This is not a disagreement: pdftotext is the exact born-digital text layer, and the page renders supplied the independent cell and row/column check for Tables 1–3.

`neuman-2021-k-cu-evaporation-isotope-fractionation-audit.csv` lists the audited Table 2 cells by run and column (including blank isotope cells), all numeric Table 1 cells and adjacent printed trace assays, the apparatus flow statement, and Table 3 numeric and range cells. All 400 data rows (340 Table 2 cells, 26 Table 1 cells including adjacent assays, 33 Table 3 data cells, and 1 apparatus-flow cell) record the pdftotext read, render read, PDF page, row, and field. Page 9 was also rendered for the printed 1-atm gas-mixing apparatus and approximate 200 cm3/min flow. The table pages showed no trapped comma decimals, fused references, lost signs, or dropped uncertainty glyphs.

## Interpretation and scope

- Table 2 contains **20 runs**. Its printed temperatures are retained per row, including 1301–1401°C values as printed; no rounding to the prose's nominal setpoints.
- K, Cu, and Zn deltas are measured on quenched glass. The Methods describe rapid withdrawal to the subsolidus furnace zone (PDF p. 11) and state that chemical diffusion during the runs and quenching is not a concern (PDF p. 18). The extract records `asserted_preserved` and cites each statement on its printed page.
- Conditions include row temperature, absolute `log fO2`, 1 atm total pressure, the Table 1 starting basalt composition, and the row's dry-air or CO–CO2 gas control. This is a 1-atm gas-mixing furnace experiment, not vacuum.
- The three `(element/Ca)/(element/Ca)0` columns and their RSD values are preserved as printed and marked `typed_refusal`. They are Ca-normalized remaining ratios (some exceed 1), not mass-loss fractions. The current schema has `mass_loss_fraction` but no fraction-remaining-ratio quantity; no schema or migrator change was made.
- Table 3 alpha and saturation values are retained under `extraction.table_3_model_derived_context`, not promoted to observations: the model inputs/parents are not stored as observations in this extract. Attributed comparison values retain their printed attributions.
- Table 2's printed 2 SE values are stored in each series item's `sigma`; the isotope observations use `per_mil`, and `confidence_level: not_stated` is retained. The migrated uncertainty band uses the printed width as reported.
- Table 3 preserves Ni et al. (2021)'s printed Cu `alpha_evp` as `0.9960`.
- The starting basalt is attached once to the experiment sample and is not an independent observation. No `neuman-2026-levitation-k-isotopes` extract is present in this seat. No residue major-oxide composition is printed. Figure-only candidate values in Figures 3–5 were not extracted.

## Isolated migration

The real battery migrator emitted 60 observations (20 per element), with 0 hard issues and 10 unavailable isotope values corresponding to the blank cells in Table 2 (4 Cu and 6 Zn). The empty cells remain unavailable, not zero. Thirteen queue entries consist of those ten unavailable cells and three method-token refusals for the printed 1-atm gas-mixing furnace regime; the current closed method list has no matching token. This is recorded as a method schema gap; the extract does not substitute a vacuum, Langmuir, or KEMS method. All three quantities map per-row temperature to identity; the isotope-delta profile does not use identity pressure, fO2, or composition, so those printed axes remain located in each point's `point_conditions`.

Expected store delta if regenerated: **+60 isotope-delta observation rows**, including 10 typed-unavailable blank cells; the ratio and RSD data are retained but do not acquire a false mass-loss quantity. No battery store regeneration was committed.

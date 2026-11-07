# Su & Zhang 2023 supplementary extraction audit

Source scope: supplementary XLSX and DOCX only; the article PDF is absent and was not used. The workbook is the primary numeric source. DOCX paragraph locators are OOXML paragraph numbers.

## Printed row reconciliation

- Table S2: 10 bead groups, 263 EMPA positions; blank Cr2O3 and MnO cells in two bead groups remain null (never zero-filled). `Total` is retained as 263 typed-refused rows because it is an analytical sum, not an oxide component.
- Table S3: 61 SIMS Na2O positions across four beads.
- Table S4: 268 LA-ICP-MS concentration positions across eight beads, each with Na2O, K2O, Cu and reported standard deviations.
- Table S5: 32 raw 2-D isotope maps, 63,996 numeric cells. Matrices are retained verbatim as context; no map-to-profile computation was performed.
- Table S1: 10 EMPA standard records.

## Cell audit

All 71,773 numeric cells in Tables S1–S5 are cross-read between openpyxl and the XLSX OOXML numeric `<v>` values, and every cell agrees. Each cell's extract target path was also resolved and matched against the displayed value. `cells.csv` records exact source sheet/cell, displayed cell value, number format, OOXML stored value, target extract path and both agreement checks. `compare.csv` contains the same source-to-extract comparison in review form. Table row/column assignments are checked against workbook labels and groups; no PDF page rendering applies to this XLSX source. Values are stored in the extract using the displayed precision and retain the original stored numeric value in the cell audit when it differs.

| Table | Numeric cells | Data rows / maps |
|---|---:|---:|
| S1 | 30 | 10 standard records |
| S2 | 5,676 | 263 EMPA rows |
| S3 | 187 | 61 SIMS rows |
| S4 | 1,884 | 268 LA-ICP-MS rows |
| S5 | 63,996 | 32 raw maps |

## Method and interpretation

- EMPA is stated in Table S2; its standards and conditions are in Table S1. SIMS is stated in Table S3. LA-ICP-MS and its Na/K/Cu calibrations are stated in Table S4 and DOCX S3 paragraphs 69–88. Cu's external reference is Ni et al. (2019) as printed in DOCX S3 paragraph 76.
- DOCX S2 paragraph 38 says melt droplets cooled to form glass beads. Preservation or alteration of volatile concentrations during quench is not stated, so profile phase is `glass` and quench status is `not_stated`.
- No profile row prints analysis temperature, fO2, total pressure, or a starting bulk composition for the sampled beads. These identity axes remain unknown. Table S2's oxide values are the measured radial-profile outputs, not initial-composition conditions. The 1640 K in DOCX S2 paragraph 51 is the fitted model's initial temperature, not a measured condition. Ambient fNa/fO2 are explicitly unknown a priori in DOCX S1 paragraph 32.
- S2 major oxide values, S3 SIMS Na2O, and S4 Na2O/K2O/Cu values are preserved with printed 1-sigma uncertainties. Table S2 and S3 report standard deviations; DOCX S3 paragraph 84 states Cu uncertainties are standard errors. S4 is `measured_reduced` because the authors convert/calibrate their own S5 signal maps; each S4 observation names its bead's analyte map and Fe boundary map as parents.
- The workbook's `data for Fig2`, `data for Fig4`, `data for Fig5`, `data for Fig7`, `data for Fig8`, and `data for Fig9` sheets are figure-support candidates, not analytical tables; their plotting data and fitted curves/parameters are not extracted in this pass. No diffusivity, cooling fit, flux, or profile fit was calculated.

## Schema gaps

- Table S2 `Total` values are analytical sums, not individual components. No exact measurement token exists in `tools/validate_literature_extracts.py::OBSERVATION_TYPES`, `simulator/battery/migrate.py::TYPE_QUANTITY` / `QUANTITY_ALIASES`, or `simulator/battery/enums.py::Quantity`; they are explicit quantity-unknown observations with the descriptive label `analytical_total_oxide_wt_percent_as_published`.
- Table S5 values are 2-D intensity maps. `tools/validate_literature_extracts.py::OBSERVATION_TYPES` has no spatial-map type; `simulator/battery/enums.py::Quantity.ION_INTENSITY` is a scalar-intensity quantity and cannot represent a matrix. Maps remain typed-refused context with no near-miss token. No schema or migrator code was changed.

## Isolated migration

The real migrator produced 3,698 observations: 0 hard issues, 0 registry issues, and 263 unavailable values (the typed-refused Table S2 analytical totals). There are 17,175 non-hard identity-incomplete findings: the five source-absent identity axes (composition, fO2, total pressure, sample mass, temperature) on each of 3,435 profile observations. Quantity-unknown is exactly the 263 analytical-total rows; phase-unknown is 0. Profile temperature, fO2, pressure, and starting composition remain unknown; the migration does not invent them. No generated battery store was committed. Expected store delta: one source with 3,698 observations, including 263 unavailable typed refusals.

DOCX machine text was produced with `textutil` and paragraph numbering was verified against OOXML; the Python `python-docx` package was unavailable in the prescribed environment. No source binary, generated observations/store, or absolute local path is included in the commit.

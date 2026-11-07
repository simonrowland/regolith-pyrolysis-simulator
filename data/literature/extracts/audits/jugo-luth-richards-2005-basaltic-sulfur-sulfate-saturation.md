# Jugo, Luth & Richards (2005) extraction audit

Source `jugo-luth-richards-2005-basaltic-sulfur-sulfate-saturation`. The source has eight printed tables, all machine-drafted from MinerU and cross-read against the born-digital `pdftotext-layout.txt`. PDF page renders were checked for every table’s row and column assignments and to resolve the numeric cells. The cell-level ledger lists 1,475 numeric-looking cells, including numeric run identifiers, and assigns `(b)` to each cell verified against its rendered page. The companion MinerU compare CSV retains the tool output for all 198 scored Table 4 values. The QA pre-screen denominators differ from the cell ledger where MinerU did not mark table columns as numeric or where the ledger includes run identifiers.

## Table reconciliation

| Printed table | PDF / printed page | Printed rows or runs | Stored coverage | Cell audit |
|---|---:|---:|---|---|
| 1 | 3 / 785 | 11 named rows across 9 starting-material columns | Oxide wt%, totals, Mg-no., analysis counts and parenthetical widths in located starting-material context | (b), page 3; MinerU classified the oxide body as header rows, corrected against render |
| 2 | 4 / 786 | 14 runs; capsule row, 8 phase-addition rows and 9 normalized oxide rows | Capsule and ingredient masses in run-indexed context; normalized oxide wt% including SO3 in the matching run composition condition | (b), page 4; C–Pt abbreviation and all run/column bindings checked |
| 3 | 7 / 789 | 22 runs | Run ids, products, T, P and duration; every value bound to its own run | (b), page 7; table row/column layout checked |
| 4 | 8 / 790 | 22 runs | 9 measured glass components per run (198 observations); calculated totals kept as 22 context values | (b), page 8; all 220 numeric cells checked |
| 5 | 10 / 792 | 13 runs | Olivine oxide analyses, formula-unit rows, totals, Mg-no. and DFMQ; DFMQ also appears in each printed run’s fO2_control condition | (b), page 10 |
| 6 | 10 / 792 | 13 runs | Spinel oxide analyses, formula-unit rows, Fe3+/ΣFe and totals retained by run | (b), page 10 |
| 7 | 11 / 793 | 9 runs | Clinopyroxene oxide analyses, formula-unit rows and both totals retained by run | (b), page 11; first body row is SiO2, not a header |
| 8 | 11 / 793 | 14 runs | Orthopyroxene oxide analyses, formula-unit rows and both totals retained by run | (b), page 11 |

All table locators use the printed table number and absolute PDF page. Table 4’s printed row count is 22; 22 × 9 measured components reconciles to 198 observations. Its `Total*` cells are calculated with S as SO3 and remain separately identified as context. No starting composition is relabeled as a quenched glass composition.

## Cell-read reconciliation

The MinerU QA pre-screen reported the following digit agreement; low agreement triggered full rendered-page review rather than accepting draft values without checking:

| Table | Compared cells | Agreement | Reported mismatches | Resolution |
|---|---:|---:|---:|---|
| 1 | 14 | 0.762 | 4 | (b), page 3; rendered decimal marks, uncertainty suffixes, and row/column placement |
| 2 | 192 | 0.435 | 155 | (b), page 4; rendered full phase-mass and normalized-composition table; C–Pt read from render and footnote |
| 3 | 88 | 1.000 | 0 | (b), page 7; full layout checked; no digit mismatch |
| 4 | 242 | 0.823 | 117 | (b), page 8; full table checked, including totals and parenthetical widths |
| 5 | 211 | 0.697 | 117 | (b), page 10; full table checked, including negative DFMQ entries |
| 6 | 210 | 0.991 | 2 | (b), page 10; full table checked |
| 7 | 173 | 0.620 | 128 | (b), page 11; full table checked and row 2 bound to SiO2 |
| 8 | 268 | 0.619 | 199 | (b), page 11; full table checked |

`mineru_table_draft.py compare` reports 198 mismatches for Table 4, with no locator errors or unmatched rows. These are parser mismatches: the MinerU draft represents the printed decimal mark as U+00B7 (for example `47·4 (2)`), which the compare parser does not recognize as a decimal separator. The exact text layer and the rendered page read `47.4 (2)` as the numerical value. The complete page-8 render resolves the row/column binding and every value; no mismatch was silently accepted. The committed compare CSV retains the row, species, value, and path for all 198.

Table 1’s MinerU block incorrectly marked the oxide body rows as header rows; transcription follows the displayed column heads and the rendered page. The Table 7 block begins with run identifiers on its first row and has no header rows; the render confirms Table 7 row 2 is SiO2. Table 2 has merged upper-header groups and noisy middle-dot decimal marks; every row/column assignment was checked from page 4. Table 5 DFMQ values were checked for sign against page 10, including the negative reduced-run values. No source cell required an OCR redo.

## Extraction and classification decisions

- Table 1 holds starting-material analyses, distinct from Table 4’s 22 quenched-glass results. The B302 composition is used as precursor composition for the first eight runs, with the separately printed `B302 glass:FeS` 5:1 by-weight recipe. The other 14 runs use their own Table 2 normalized bulk oxide rows, including SO3.
- Table 3 prints run temperature, pressure, duration and product assemblage. Each glass row has T, pressure, fO2 control and composition under `point_conditions`, each with a locator. Table 3 pressure remains in GPa in the source fields; its identity-axis value is the migrator’s Pa projection. Buffer-relative fO2 never becomes an absolute fO2 value.
- Table 5 DFMQ is the authors’ reduction of their olivine/spinel analyses using Ballhaus et al. (1990, 1991), not a model fit or someone else’s quoted result. The values are retained as printed in the run conditions and Table 5 context. Table 6’s printed Fe3+/ΣFe is retained in context with its stoichiometric-estimation footnote; its inputs cannot be emitted as crystal observations because the schema has no crystal phase role.
- Table 4 reports total S by EMPA; no S6+/ΣS or other sulfur-speciation ratio is printed. Parenthetical uncertainty widths are stored as printed with the footnote’s 1 sigma meaning.
- Tables 5–8 print olivine, spinel and pyroxene compositions. Their oxide and formula-unit values are retained per run in context, not mislabeled as glass/melt observations. The shared method statement says elemental concentrations in all phases were determined by EMPA with WDS; that method and its PDF page 6 locator are attached to each table context record. The exact schema boundary is `simulator/battery/enums.py::PhaseRole`, `simulator/battery/migrate.py::_phase_row_rejection`, `simulator/battery/enums.py::PHASE_BASIS_TOKENS`, `simulator/battery/enums.py::Quantity`, `simulator/battery/migrate.py::QUANTITY_ALIASES`, and `tools/validate_literature_extracts.py::OBSERVATION_TYPES`.
- Pooled pyrrhotitic sulfide composition in the prose is 36.9 ± 0.9 wt% S and 60.2 ± 1.2 wt% Fe, n=12; no run is assigned to the pooled analyses. The sulfate WDS analysis was not possible; qualitative EDS reports Ca- and S-rich material with small Na and Mg, and Figure 1 says the blebs presumably crystallized as anhydrite on quenching. No numeric anhydrite composition is printed.
- Figure 2 (run-product S against DFMQ) and Figure 3 (comparison with Carroll & Rutherford) remain figure-read candidates. The paper prints no SCSS regression equation or fitted parameter; no figure values are digitized.

## Isolated migration

The regenerated extract migrates to 198 observations, 0 hard issues, 0 unavailable values and 0 quantity-unknown or phase-unknown observations. Every observation has identity temperature, total pressure and composition. Identity fO2 remains unknown because the paper prints buffer-relative controls or no run-specific fO2, not absolute fO2.

| Migrated row | Quantity value / phase | Identity axes after migration |
|---|---|---|
| run 2 SiO2, Table 4 page 8 | 47.4 wt%; glass | T = 1628.15 K; P = 1.6 × 10^9 Pa; composition = B302 precursor; absolute fO2 unknown (C–CO-buffer-relative) |
| run 2 S, Table 4 page 8 | 0.14 wt%; glass | T = 1628.15 K; P = 1.6 × 10^9 Pa; composition = B302 precursor; absolute fO2 unknown (C–CO-buffer-relative) |
| run 30A S, Table 4 page 8 | 1.34 wt%; glass | T = 1588.15 K; P = 1.2 × 10^9 Pa; composition = Table 2 run 30A bulk; absolute fO2 unknown (not printed for 30A) |

The glass quench statement is recorded as `not_stated` for preservation: the paper identifies quenched glass but does not assert that high-temperature sulfur speciation is preserved. Battery-store delta expected on regeneration: one new work, 22 experiments, 10 context records and 198 glass-composition observations; no regen files are committed.

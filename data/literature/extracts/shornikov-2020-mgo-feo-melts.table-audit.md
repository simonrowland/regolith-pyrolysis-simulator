# Shornikov et al. (2020) table audit

The target article occupies printed pages 74–77. This audit covers Tables 1 and 2; the article after the references on page 77 is outside this extract.

## Table 1: prior-study conditions

The printed count is 14 rows: four on PDF page 1 / printed page 74 and ten on PDF page 2 / printed page 75. The continued table has no new title row. All 42 numeric cells (temperature, FeO composition range, sample count) agree between the MinerU table cells and `pdftotext-layout.txt`; 600 dpi renders of pages 1 and 2 confirmed row and column assignment.

| Printed row | Page | Method | T, K | FeO, mol % | Samples | Reference | Audit |
|---:|---:|---|---|---|---:|---|---|
| 1 | 74 | H2O / H2 equilibrium | 1133–1333 | 0–100 | 18 | Shashkina & Gerasimov, 1953 | (a) |
| 2 | 74 | CO / CO2 equilibrium | 1073–1273 | 10–95 | 9 | Schmahl et al., 1961 | (a) |
| 3 | 74 | E. m. f. | 1073–1273 | 10–90 | 6 | Engell, 1962 | (a) |
| 4 | 74 | CO2 / H2 equilibrium | 1373–1573 | 5–95 | 8 | Hahn & Muan, 1962 | (a) |
| 5 | 75 | E. m. f. | 1173–1473 | 30–89 | 7 | Gordeev et al., 1965 | (b), added by hand |
| 6 | 75 | H2O / H2 equilibrium | 1123 | 4–73 | 6 | Aubry et al., 1965 | (a) |
| 7 | 75 | CO2 / H2 equilibrium | 1433 | 20–80 | 5 | Katsura & Kimura, 1965 | (a) |
| 8 | 75 | CO2 / H2 equilibrium | 1373–1573 | 30–80 | 5 | Nafziger & Muan, 1967 | (a) |
| 9 | 75 | H2O / H2 equilibrium | 1123 | 10–90 | 9 | Berthet & Perrot, 1970 | (a) |
| 10 | 75 | E. m. f. | 1073–1373 | 30–90 | 5 | Abbattista et al., 1973 | (a) |
| 11 | 75 | CO2 / H2 equilibrium | 1433 | 15–65 | 5 | Saha & Biggar, 1974 | (a) |
| 12 | 75 | E. m. f. | 1050–1400 | 15–85 | 7 | Srecec et al., 1987 | (a) |
| 13 | 75 | CO2 / H2 equilibrium | 1573 | 10–85 | 8 | Srecec et al., 1987 | (a) |
| 14 | 75 | E. m. f. | 1373–1573 | 12–90 | 9 | Hasegawa et al., 2006 | (a) |

MinerU classified row 5, `E. m. f. / 1173–1473 / 30–89 / 7 / Gordeev et al., 1965`, as a header on the continued table. It is preserved above and in `FeO.context`. The emitter has no context row shape for the printed ranges, so Table 1 is not emitted as point observations; its numeric cells are retained verbatim as context.

## Table 2: standard Gibbs energies

The printed count is 17 numeric cells. The MinerU block is keyed to PDF page 2 and draft table index 3, but its values after the first row are printed on PDF page 3. The locators in the extract use the rendered page and printed Table 2 row number. The page 2 and page 3 600 dpi renders confirm cell assignment. MinerU and `pdftotext-layout.txt` agree on all digits and signs; MinerU spells the nine negative signs with ASCII hyphen while the printed text layer uses a longer minus glyph. Each of those nine cells was checked on the render.

| Page | Printed row | Column | Species | Printed value | Audit |
|---:|---:|---:|---|---:|---|
| 75 | 1 | 2 | MgO solid | -355.557 | (b), render sign check |
| 75 | 1 | 4 | MgO liquid | -340.913 | (b), render sign check |
| 75 | 1 | 6 | Mg gas | -100.474 | (b), render sign check |
| 76 | 2 | 2 | MgFeO2 solid | -481.919 | (b), render sign check |
| 76 | 2 | 6 | Mg2 gas | -4.268 | (b), render sign check |
| 76 | 3 | 2 | MgFe2O3 solid | -602.032 | (b), render sign check |
| 76 | 3 | 6 | MgO gas | -141.776 | (b), render sign check |
| 76 | 4 | 2 | FeO solid | -118.362 | (b), render sign check |
| 76 | 4 | 4 | FeO liquid | -134.332 | (b), render sign check |
| 76 | 4 | 6 | Fe gas | 70.719 | (a) |
| 76 | 5 | 6 | Fe2 gas | 322.148 | (a) |
| 76 | 6 | 6 | FeO gas | 31.496 | (a) |
| 76 | 7 | 6 | FeO2 gas | 40.263 | (a) |
| 76 | 8 | 6 | O gas | 88.160 | (a) |
| 76 | 9 | 6 | O2 gas | 0.000 | (a) |
| 76 | 10 | 6 | O3 gas | 300.418 | (a) |
| 76 | 11 | 6 | O4 gas | 240.548 | (a) |

## Emit output

`emit` produced 17 observations and skipped 16 empty cells. No emitted numeric value was edited. Table 1's 42 numeric context cells were transcribed from its table draft and checked against the machine text; the misclassified first continuation row was restored by hand. This is the table-context gap for the trial report.

The `compare` command returned only its CSV header and reported zero rows. Its implementation reads numeric observations from `values.points[]`, while `emit` produced scalar Table 2 rows in `values.value`. The result is no automated compare coverage for this extract; the 17 cells were reviewed against the page renders and `pdftotext-layout.txt` above.

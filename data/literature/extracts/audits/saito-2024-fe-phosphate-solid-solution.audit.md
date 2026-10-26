# Saito et al. (2024) extraction audit

Source: Saito K, Makino Y, Hasegawa M (2024), ISIJ International 64(6), 935–943, DOI `10.2355/isijinternational.ISIJINT-2023-466`.

## Duplicate check and inputs

The DOI, first author/year, title words, and source ID were checked against extracts at the seat base and across refs. No matching extract or earlier source-ID commit was found.

Transcription order was MinerU content-list tables and markdown, then `pdftotext-layout.txt`. The paper is born digital. The MinerU table draft and QA tool read four tables from the `p003-p008` chunk; QA reports 1.000 numeric agreement for every table: physical page 4 Table 1 (0 numeric cells), page 4 Table 2 (44), page 5 Table 3 (168), and page 7 Table 4 (43). Table 1 contains starting-material preparation text, not activity or equilibrium-composition data, so it is not extracted. Invented values in MinerU `<details>` blocks were ignored.

Page renders were used to verify the row and column layouts for Tables 1–4, each distinct source locator, the standard-state prose on page 3, and equations 30–33 on page 6. The absolute PDF pages are 3–7; the printed journal pages are 937–941. No figure values were digitized.

## Printed row counts and cell audit

Method **(a)** means the same digits and sign appear in MinerU and `pdftotext-layout.txt`; the table layout was checked against the render. Method **(b)** means the cell was split or resolved directly against the page render. Draft CSV row/column coordinates below are 1-based audit coordinates; all extract locators use the printed table number.

| Printed table and cells | Stored use | Audit |
|---|---|---|
| Page 4, Table 2, rows A–D, columns 6–9: A `(0.647, 0, 0.235, 0.117)`; B `(0.624, 0.016, 0.238, 0.122)`; C `(0.616, 0.028, 0.235, 0.121)`; D `(0.603, 0.033, 0.235, 0.129)` | Four complete solid-solution mole-fraction compositions, repeated as each row’s condition and identity composition | **(a)** all 16 cells; p004 render confirms columns and sample assignment |
| Page 4, Table 2, draft rows 3–6, column 12: A `0`; B `0.058 ± 0.006`; C `0.117 ± 0.005`; D `0.154 ± 0.013` | Four quoted FeO activities; B–D uncertainties stored separately as printed | A **(a)**; B–D **(b)**, rendered p004 to separate activity and uncertainty |
| Page 5, Table 3, draft rows 3–26, column 5: A/13.52 `(0, 0, 0, 0)`; A/25.51 `(0, 0.34)`; B/9.57 `(0, 0.066, 0, 0.066)`; B/13.52 `(0, 0.11, 0, 0.11)`; B/15.71 `(0.50, 0.50, 0, 0.50)`; C/7.83 `(0.052, 0, 0.052)`; D/6.43 `(0.11, 0, 0.11)` | Printed initial phosphorus composition for the 24 individual runs | **(a)** all 24 cells; p005 render confirms grouping and row assignment |
| Page 5, Table 3, draft rows 3–26, column 6: A/13.52 `(0.0166, 0.0203, 0.0132, 0.0137)`; A/25.51 `(0.0688, 0.0842)`; B/9.57 `(0.00450, 0.00321, 0.00432, 0.00480)`; B/13.52 `(0.0344, 0.0350, 0.0471, 0.0368)`; B/15.71 `(0.122, 0.113, 0.127, 0.110)`; C/7.83 `(0.0183, 0.0145, 0.0209)`; D/6.43 `(0.0167, 0.0167, 0.0188)` | 24 measured liquid-Cu equilibrium P mass-percent values | **(a)** all 24 cells; p005 render confirms row/column assignment |
| Page 5, Table 3, draft rows `(3, 7, 9, 13, 17, 21, 24)`, column 4: `(0, 0, 3.12, 5.18, 7.71, 6.61, 7.87)` | Seven author-calculated liquid-Cu Fe mass-percent values | **(a)** all seven cells; p005 render confirms group alignment |
| Page 5, Table 3, same seven group rows, column 7: `0.0159 ± 0.0028`, `0.0765 ± 0.0077`, `0.00421 ± 0.00060`, `0.0383 ± 0.0051`, `0.118 ± 0.007`, `0.0179 ± 0.0026`, `0.0174 ± 0.0010` | Seven printed average P mass-percent values and separate uncertainties | **(b)** all seven joined mean/uncertainty cells, split from p005 render |
| Page 5, Table 3, same seven group rows, column 8: `−1.80 ± 0.08`, `−1.12 ± 0.04`, `−2.99 ± 0.06`, `−2.43 ± 0.06`, `−2.44 ± 0.03`, `−3.04 ± 0.06`, `−3.30 ± 0.03` | Seven author-calculated `log hP` values and separate uncertainties | **(b)** all seven joined cells, split from p005 render; signs checked |
| Page 7, Table 4, draft rows 2–5, column 3: `−19.60, −21.09, −20.89, −20.97`; column 7: `0.559, 0.559, 0.621, 0.669`; column 8: `−2.33, −2.12, −2.17, −2.17`; column 9: `−0.59, −0.80, −0.75, −0.75`; column 10: `−0.47, −0.26, −0.31, −0.30`; column 11: `−1.78, −2.65, −2.60, −2.67`; column 12: `(dash, −2.75, −2.10, −1.86)` | 27 activity or activity-coefficient results. The A/column-12 dash is omitted, never converted to zero. The four column-10 values are retained as typed refusals because the printed α′-C2S reference state has no schema token. | Numeric cells **(a)** except the footnoted A/column-7 and A/column-8 cells, checked by **(b)** against p007. The page render confirms A/column 8 is `−2.33`; MinerU fused its footnote marker into `−2.331…`. |
| Page 6, equations 30–33: Sample A `0.13`, B `0.20`, C `0.13`, D `0.05` | Four printed standard deviations for log aP2O5 | **(b)** all four uncertainty cells checked against p006; confidence level not stated |

The printed activity counts reconcile to four Table 2 rows, 24 Table 3 runs plus seven grouped rows, and four Table 4 sample rows. The extract has 76 observations: 4 Table 2 activities; 24 Table 3 P runs, 7 P averages, 7 Fe values, and 7 log hP values; 27 Table 4 activities/activity coefficients. Table 2 columns for bulk composition and the two derived mole ratios are not copied; the full printed solid-solution composition vector is retained. Table 3 values in `%P` and `%Fe` are stored on the `wt_percent` scale with canonical unit `%`; no percent-to-fraction conversion is applied to a mass-percent composition value.

`mineru_table_draft compare` reports 58 matched, 18 mismatched, and zero no-match/presence-only/needs-mapping cells (76 total). All 18 mismatches were resolved against page renders: Table 2 B–D activity/uncertainty pairs (3); Table 3 seven average P/uncertainty pairs and seven log hP/uncertainty pairs (14); Table 4 A/column 8 `−2.33` with its footnote marker (1). Table 4 A/column 7 `0.559*` was also render-checked; its numeric value matched. The emitter cannot split joined ± cells and does not recognize the fused Table 4 footnote as a trap, so those values were hand-split after render review. Its `render_verified` flag is only accepted for cells the emitter itself flags; the render decisions for its unflagged cells are recorded here.

## Conditions, phases, and lineage

- Every migrated row carries 1573 K with its Table 2, 3, or 4 locator. The caption states this temperature for each table.
- Table 3 prints the `Ar + H2 + H2O` atmosphere and per-group `pH2/pH2O` ratios `(13.52, 25.51, 9.57, 13.52, 15.71, 7.83, 6.43)`. Those gas-mix controls reach every Table 3 row. Table 4 activity rows carry the gas-ratio series used to calculate each sample’s activities. Table 2 quoted FeO activities have no equilibration gas or fO2 printed in this paper.
- No total pressure or absolute fO2 is reported. The migration leaves those identity axes unknown; it does not infer pressure or convert a buffer/gas ratio to absolute fO2. Table 3’s partial Fe/P alloy analysis is preserved as `printed_composition`; the complete Cu-alloy composition remains unknown because the copper remainder is not printed.
- Table 2 and Table 4 activities are typed as crystalline solid-solution measurements (`cr`). Table 3 P and Fe values and log hP refer to liquid Cu (`l`, melt role).
- Page 3 defines the FeO standard state as pure nonstoichiometric solid FeO in equilibrium with metallic iron, P2O5 as pure hypothetical liquid P2O5, and hP as solute P in liquid Cu at 1 mass%. Page 7 defines CaO/SiO2 as pure solids, C2S as pure solid α′-C2S, C3P as pure solid α-C3P, and W2S as fayalite with orthorhombic structure. The α′-C2S token is absent from schema; all four C2S values stay typed-refused without a schema or migrator change.
- Table 2 FeO activities are `quoted_attributed` to reference 35. Table 3 run P values are `measured_direct`, and the seven P averages are `measured_reduced` from those measured runs. The seven Table 3 equilibrium Fe concentrations are calculated by Eqs. (4), (17), and (19) from the Table 2 FeO activities attributed to reference 35, so they are `quoted_attributed`, not this paper's measured Fe. The seven log hP values use those Fe concentrations and the measured P values through Eq. (21), with the printed ePFe parameter (−0.195 at 1573 K, page 6); they inherit the quoted input and are `quoted_attributed`.
- Table 4 activities and coefficients derived from Table 3 and Table 2 inherit quoted inputs and are `quoted_attributed`. This includes the Table 4 P2O5 activities, whose log hP inputs depend on reference 35; the FeO coefficients B–D, calculated from the Table 2 FeO activities; and the CaO, SiO2, C2S, C3P, and W2S results, whose equation lineages inherit those quoted values. Sample A CaO remains `quoted_attributed` to reference 11. Sample A log γFeO remains `author_estimate`, as the Table 4 footnote explicitly assumes it equals Sample B. No activity is `measured_reduced` when any stored ancestor is quoted or estimated; no row is `model_derived`.

Evidence-class counts before and after the lineage correction:

| Evidence class | Before | After |
|---|---:|---:|
| `measured_direct` | 24 | 24 |
| `measured_reduced` | 46 | 7 |
| `quoted_attributed` | 5 | 44 |
| `author_estimate` | 1 | 1 |
| `model_derived` | 0 | 0 |
- Printed uncertainties have no stated confidence level. The values remain as printed.

Useful figure-read candidates, deferred because figure digitization is out of scope: Figures 2, 3, 5, 6, 7, 8, and 9. Figure 6 supports the printed ePFe parameter; no plotted coordinates were extracted.

## Verification and expected store delta

The focused extract test pins four rendered cells across Tables 2–4, their physical pages and printed table locators, and row counts of 4/45/27. Its mutation check changes the pinned Table 2 activity from `0.058` to `0.059` and fails as expected. Isolated migration reports 76 observations, 0 hard issues, 0 unavailable values, and 0 registry issues: 72 admitted and four rejected C2S rows. Table 2, Table 3, and Table 4 rows all receive their printed 1573 K and source locator. The three identity axes were checked: the printed sample composition reaches identity for the solid rows, while total pressure and absolute fO2 remain unknown because the paper does not print them. The Table 3 gas ratios remain in `fO2_control`.

Verification results:

- `tests/test_yaml_flow_scalar_commas.py`: 17 passed.
- `tests/battery/test_saito_2024_fe_phosphate_solid_solution_extract.py`: 4 passed; tests cover render pins and row counts, mutation failure, isolated migration, evidence-gate eligibility, and measured-only ancestry.
- `tests/test_literature_extracts.py -k 'fidelity_sample_matches_extract and saito-2024-fe-phosphate-solid-solution'`: 1 passed.
- The controller's completed full run of `tests/test_literature_extracts.py` before the lineage correction reported 817 passed and four failures in 923.76 seconds. The failures were the `docs-private` provenance-path fixture and three repository validation/coverage checks caused by existing `docs-private` source paths in `kems-012-sossi-2019.yaml` and `kems-020-hastie-1981-nbsir.yaml`; review confirmed those inputs are byte-identical to green. After the correction, `test_repo_extracts_validate_green` was rerun and reports only those same three Kems source-path errors, with no Saito extract error.

Expected battery-store delta is 76 source observations: 72 admitted and four rejected typed-refusal α′-C2S rows. No battery-store regeneration is committed.

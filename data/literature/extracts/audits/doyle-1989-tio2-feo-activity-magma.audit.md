# Doyle (1989) extraction audit

Status: Extraction and focused acceptance checks complete. The controller ruled the Table 1A/Table 1B exact-key join and approved the existing Doyle 1988 lineage pattern. The full literature test file exceeded its 300-second limit; its source-inclusive repository validator passed separately.

## Source and scope

Christopher D. Doyle, “Effect of substitution of TiO2 for SiO2 on f(FeO) in magma,” *Geochimica et Cosmochimica Acta* 53 (1989), 2631–2638. The paper reports FeO activities for 167 run-product glass compositions at 1600±2 K, five Table 1B gas/fO2 conditions, natural-magma analyses recalculated to a normalized Matrix pseudocomponent in Table 2, and activity contours in Figures 2–5. The Table 2 rows cite Nockolds (1954), Arndt (1977), and Longhi et al. (1978). No supporting directory was present for this source.

The supplement scan under `_supporting/doyle-1989-tio2-feo-activity-magma/` found no directory. The paper prints no EMF measurements or FeO activity-coefficient values. Its `w` values are Si–Ti interaction parameters, not FeO activity coefficients.

## Cell audit

All numeric Table 1A, 1B, and 2 cells were transcribed from the OCR-redo VLM table blocks and checked against the 600 dpi page render. The OCR-redo pdftotext cut and original layout text disagreed materially on table digits/layout; the VLM cell was retained only when the render confirmed it. The page render was also used to verify table captions, column alignment, row counts, footnote markers, and the printed table labels. Every Table 1A activity cell is plain numeric text with no suffix; the Hi/Lo starting-material mark is in the separate SM column. This is recorded on every Table 1A observation and pinned in the focused test. Figure 4's filled/open symbols refer to initially FeO-richer/poorer materials as its caption states.

| Printed cells | PDF page / printed page | Printed row count | Audit method and result |
|---|---:|---:|---|
| Table 1A, Fig. 4A panel, a_FeO through K2O | 4 / 2634 | 58 | OCR-redo VLM table 1; columns 1–7 for printed rows 1–58, every numeric cell checked against the render (b); SM column separately checked as Hi/Lo. |
| Table 1A, Fig. 4B panel, a_FeO through K2O | 4 / 2634 | 50 | OCR-redo VLM table 2; columns 1–7 for printed rows 1–50, every numeric cell checked against the render (b); SM column separately checked as Hi/Lo. |
| Table 1A, Fig. 4C panel, a_FeO through K2O | 4 / 2634 | 59 | OCR-redo VLM table 3; columns 1–7 for printed rows 1–59, every numeric cell checked against the render (b); SM column separately checked as Hi/Lo. |
| Table 1B, a_FeO±uncertainty, -log10 fO2, and CO2:H2:N2 flow cells | 6 / 2636 | 5 | OCR-redo VLM table 4; columns 1–5 for printed rows 1–5 and the shared fO2 header uncertainty checked against the render (b). Render resolves caption label `1B` (VLM read `18`). The shared header prints ±0.02. |
| Table 2, X_Matrix and four Matrix component fractions | 6 / 2636 | 9 | OCR-redo VLM table 5; columns 2–6 for printed rows 1–9, every numeric cell checked against the render (b). Footnotes: Nockolds (1954), Arndt (1977), Longhi et al. (1978). |

Reconciled printed counts: Table 1A has 167 body rows total (58+50+59), Table 1B has 5, and Table 2 has 9. Table 1A contributes 1,169 numeric cells (a_FeO plus six wt% oxide columns); Table 1B contributes 31 numeric cells including the shared ±0.02 header; Table 2 contributes 45. The 1A and 1B tables print 172 activity cells: 167 Table 1A observations and five repeated setpoint values retained as Table 1B context parents.

The table comparison is committed as `doyle-1989-tio2-feo-activity-magma.compare.csv`: 167 Table 1A activity values matched their page-4 draft cells, with zero no-match, mismatch, locator-error, or unverifiable results. The comparison helper's `tools/mineru_table_draft.py::_phase_matches_column` accepts only `gas`, `liquid`, and `solid`, while this extract correctly uses the migrator phase token `l`. To compare the numeric cells without changing the extract or tool, the scratch comparison input omitted phase and supplied CSV row/column coordinates from the printed panel/row; all actual phase tokens and locators are independently pinned by the focused test. The focused test also pins every Table 1A composition cell and every numeric Table 1B and Table 2 cell against the render-backed expected rows.

Locators use the printed table labels `1A`, `1B`, and `2`, absolute PDF pages 4 and 6, panel labels 4A/4B/4C, and row numbers within each panel/table. The extract also locates Equation (8) and the standard-state statement on PDF p. 3 / printed p. 2633, and Figures 4–5 captions on PDF p. 5 / printed p. 2635.

The layout text mangles the model equations in the theory section. The PDF p. 2 render resolves Equation (6) as `a_FeO^R = a_FeO^si · exp(-w s t/RT)`; Equation (6a) is retained as printed from the results section. Both equations, the definitions of `s` and `t`, and the printed interaction values for Figures 2–3 and 5 are attached as a located equation context record. The p. 2 render check is for the corrupted equation text, not a table-cell transcription.

## Activity method and controller ruling

### Scoring-gap review (2026-11-03)

The paper does print the method for the 167 Table 1A activities. The controlled
oxygen-fugacity equilibration and metallic-iron activity statements are on PDF
p. 2 / printed p. 2632, under “Control of the activity of FeO.” Equation (8)
and the molten Fe0.947O standard state are on PDF p. 3 / printed p. 2633.
“Furnace arrangement” on that page prints the Fe-wire loop, CO2-H2-N2 gas,
quench furnace, and that hot-zone fugacities are calculated by Brinkley’s
method. The Table 1B caption on PDF p. 6 / printed p. 2636 reads “Compositions
of the input gases (c.c./min).” Each clause is stored with its own page or
table locator in the experiment and every row’s derivation. This is gas
equilibration with an iron buffer, not Knudsen effusion.
Controller ruling seq. 2 directs `experiment.method: quench_equilibration`;
no schema change is needed. The shared experiment and all 167 rows carry the
located method detail, including the printed metal superscript in
`a_Fe^{metal} = 1`. The standard-state values remain verbatim and are not
rebased. Doyle 1988 stores
`gas_metal_equilibration` as free text; that sibling extract is outside this
task and the controller will queue it separately.

Equation (8) is printed as `a_FeO = f_O2^0.5 · exp(28719.2/T − 5.837)` and specifies absolute temperature. The text says that the activity follows from equilibrium of metallic Fe with O2/FeO and a known equilibrium constant. Gas fugacities are calculated by Brinkley's method using the Van Zeggern and Storey algorithm; the paper assumes ideal gases. The standard state is explicitly “molten Fe0.947O.” Temperature accuracy is better than ±2 K; Tables 1A and 1B caption the experiments at 1600±2 K.

Table 1A has 167 activity cells and per-row run-product glass compositions. Its caption says all melts coexist with metallic iron and a CO2-H2-N2 gas (Table 1B). Table 1B gives five `-log10 fO2` values keyed by the same printed a_FeO values. Controller ruling seq 8 approves joining the Table 1B condition to each Table 1A row with the exact same printed a_FeO key because of the explicit caption cross-reference. No fuzzy match is used; the exact key and cross-reference basis are recorded in each joined fO2 locator. All 167 Table 1A keys match one of the five setpoints. The positive printed `-log10 fO2` and its shared ±0.02 header are also retained in the Table 1B context row. The pressure reference is unstated; each value has two decimal places, so the requested migrator bar default is applicable, with that unstated reference retained in the row note.

The Table 1B shared header uncertainty `±0.02` is attached to each Table 1B fO2 entry and each exact-key joined Table 1A `point_conditions.fO2_log` condition, with the header's PDF p. 6 / Table 1B locator. `confidence level not stated` is recorded. The migrator's joined fO2 locator note retains the printed uncertainty; the focused test pins it there as well as on the raw condition record. The central log values stay unchanged (for example, printed `11.92±0.02` remains condition value `-11.92` with separate uncertainty text); no uncertainty is added to the central value. The Table 1B a_FeO uncertainties (0.01 or 0.02) are retained separately as each Table 1A activity observation's printed uncertainty, with the same no-confidence notice.

Each Table 1B setpoint is a `typed_context` parent. The 167 Table 1A activities are `measured_reduced`, with the matching Table 1B context parent in `derived_from` and a derivation locator for Equation (8) on PDF p. 3. The setpoint's printed activity uncertainty is attached to each matching Table 1A activity with the notice that confidence level is not stated. This follows the same-author 1988 Doyle extract pattern at `ead11ed77:data/literature/extracts/doyle-1988-feo-activity-multicomponent-magma.yaml`. No schema or migrator change was made.

The run-product glass analyses are stored as mass-percent composition conditions, with the actual equilibrium activity phase `l`. The source says fine quench crystals in some FeO-rich glasses do not affect the analyses because a 40 µm beam is maintained for most samples. This is recorded as asserted preservation of composition analysis only, not preservation of activity through quench. The text gives no total pressure; no total pressure was added. Figure 4 points duplicate Table 1A values. Figure 2/3 external-data curves and Figure 5 projected values remain figure-only and undigitised.

The exact printed reference is “molten Fe0.947O.” It is kept verbatim in `standard_state` and `standard_state_as_printed`; the non-stoichiometric liquid is not converted to stoichiometric FeO. `simulator/battery/enums.py::ReferenceStateConvention` has no exact token for this reference, so the migrated identity reference state remains unknown and each row carries a schema-gap note, following the Doyle 1988 precedent.

## Table 2 schema decision

Table 2 reports author-recalculated mole fractions within a normalized `Matrix` pseudocomponent, plus `X_Matrix`; it is not a whole measured phase composition. Its nine rows and 45 values are preserved as table context with a schema-gap explanation. The closed observation-type path is `tools/validate_literature_extracts.py::OBSERVATION_TYPES`; the composition-series type has no default quantity in `simulator/battery/migrate.py::TYPE_QUANTITY`, and `simulator/battery/migrate.py::QUANTITY_ALIASES` has no exact Matrix-pseudocomponent quantity. `simulator/battery/enums.py::PHASE_BASIS_TOKENS` includes `mole_fraction`, but `simulator/battery/enums.py::Quantity.EQUILIBRIUM_PHASE_COMPOSITION` is the nearest phase-composition quantity. The migrator’s `simulator/battery/migrate.py::_is_printed_phase_row`, `_phase_role_token`, and `_phase_row_rejection` require a closed physical phase role such as melt, slag, glass, metal, or matte; Matrix is a normalized subcomponent rather than a phase. Mapping the Table 2 values to that near-match would change their printed meaning. The composition is author-recalculated from cited rock analyses, and the extract does not invent a temperature, fO2, pressure, or physical phase for it.

## Isolated migration and verification

The updated isolated production migration validates the extract with 0 document issues and returns 167 numeric activity observations, 0 unavailable activity observations, 0 hard issues, 0 registry issues, and 334 queue entries. All observations link to the declared `quench_equilibration` experiment, and every row carries a located derivation method. The extraction queue still has 167 row-level method-missing entries because observation `regime`/`values.method` remain absent; these are distinct from the typed experiment method that resolves the worklist field. The other 167 queue entries are the documented non-stoichiometric reference-state schema gap. There are 0 quantity-unknown observations and 0 phase-unknown observations. Three inspected Table 1A rows map phase `l`, 1600 K, their own composition, and fO2 through the exact-key join; total pressure remains unknown. Their reference state remains unknown in identity with the verbatim print and schema gap retained. The Table 1B two-decimal `-log10 fO2` values map via the instructed bar default.

The focused test `tests/test_doyle_1989_tio2_feo_activity_magma_extract.py` passes (2 tests), including the isolated production migration. It pins all 167 Table 1A observations and their six composition cells, all Table 1B setpoint values and uncertainties, all Table 2 row values and source markers, the no-suffix finding, both printed and joined fO2 uncertainty, and the new experiment and derivation method bindings. Its interior mutations of a Table 1A activity and fO2 condition both fail as expected.

`tests/test_yaml_flow_scalar_commas.py` passes (17 tests). The selected `tests/test_literature_extracts.py -k repo_extracts_validate_green` run was interrupted after 440 seconds, exceeding the dispatch 300-second cap, and is UNVERIFIED-local. The optional `ps` process inspection was denied by the sandbox.

Expected store delta: one declared experiment (`quench_equilibration`) with 167 activity observations linked to it, and 167 located derivation-method annotations. The 167 numeric activity values and existing context set stay unchanged. No battery-store regeneration was performed.

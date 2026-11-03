# Shi et al. (2025) extraction audit

## Scope and row reconciliation

The six printed tables contain 33 data rows: Table 1 has 4, Table 2 has 10, Table 3 has 5, Table 4 has 8 phase rows, Table 5 has 2, and Table 6 has 4. The 132-cell audit is in `shi-2025-mno-activity-eaf-spinel.cells.csv`: 124 numeric cells and the 8 dashes printed in the spinel Ca and Si columns. It gives each page, printed row, column, value, confirmation method, and extract destination.

MinerU and the born-digital pdftotext layer agree on the table cell digits. The row and column layouts for Tables 1–6 were checked against rendered pages 2–6. The cell audit marks render confirmation for Tables 1, 2, 4, and 6, including the decimal-looking cells flagged by table QA; Tables 3 and 5 use the exact matching machine reads with the rendered layout check. The table draft comparison reports 9 matched points, no mismatches or locator errors, and one prose equation result as unverifiable against table cells. The latter is independently located in the page 4 prose audit below.

| Printed table | Rows | Printed contents | Reconciliation |
| --- | ---: | --- | --- |
| 1, page 2 | 4 | Six initial slag oxide mol% values per row | All 24 composition cells retained as located initial-charge context. |
| 2, page 3 | 10 | Cooling-temperature intervals and printed (P_{CO}/P_{CO_2}) ratios | All 20 cells retained. The caption says CO2/CO while the column header says (P_{CO}/P_{CO_2}); the printed header and values are preserved without inversion. |
| 3, page 3 | 5 | Al2O3 content and FactSage activity | All 10 cells retained. Table outputs are `quoted_attributed`: their model input context is stored, but those context rows cannot be resolvable lineage parents under the current migrator. |
| 4, page 4 | 8 | Four Spinel and four Matrix rows, seven element columns | All component cells and dashes retained. The four Matrix rows are admitted as measured glass composition; four solid Spinel rows remain typed refusals because the closed phase-role schema has no solid/spinel role. |
| 5, page 5 | 2 | Quenching and slow-cooling spinel compositions | All ten numeric cells retained as context with the source's assertion that those compositions are nearly identical. The phase-role gap prevents typing these as solid spinel observations. |
| 6, page 6 | 4 | Al2O3 content, measured alloy (X_{Mn}), and reduced (a_{MnO}) | All 12 cells retained in eight observations: four direct ICP-OES (X_{Mn}) values and four MnO activities reduced from those values. |

Table 4 Matrix row 2 sums to 100.072 mol% as printed. It is not renormalized.

## Prose evidence and methods

- Page 2, section 2.3 states 1673 K, 28 h equilibration with Ag–Mn alloy in an iron crucible, immediate water quenching, and (pO_2=10^{-13}) atm. The reported CO–CO2 mix is approximately (P_{CO}/P_{CO_2}=151:1) at 1673 K with Ar carrier. The paper does not state total pressure; gas flow and gas ratios are not substituted for it.
- Table 2 is a slow-cooling gas-ratio schedule, not a set of new equilibrium activity observations.
- Page 3, section 3.1 identifies FactSage 8.3 with FToxid 8.3 and FactPS 8.3, (pO_2=10^{-13}) atm, 1673 K, fixed MgO/FeO/MnO amounts, and basicity 0.82. Table 3 outputs remain `quoted_attributed` because their inputs are stored as context, not as resolvable observation parents. Table 3 row 5 reports only the CaO/SiO2 ratio and not separate CaO and SiO2 amounts, so no composition is reconstructed.
- Page 4 prose reports a maximum MnAl2O4 precipitation ratio of 80.32% at 33.64 mol% Al2O3, calculated by Eqs. (7)–(9) from EDS balances. It is retained as a typed refusal with a descriptive non-token quantity: `Quantity.DISTRIBUTION_RATIO` is a phase-to-phase concentration ratio, `Quantity.YIELD_FRACTION` is a yield, and `tools/validate_literature_extracts.py::OBSERVATION_TYPES` has no exact precipitation-ratio type.
- Table 6 (X_{Mn}) is the authors' ICP-OES measurement. Their printed Eqs. (13) and (14) reduce it to (a_{MnO}); each activity is `measured_reduced` and links to its matching stored (X_{Mn}) row. The activity is kept in the printed form.
- Figure 2 contains a figure-read candidate for activity/composition values. Those plot values are not extracted in this table wave.

## Phase, composition, and identity decisions

The Table 4 Matrix is identified by the paper as a glassy matrix. Its measured state is stored as phase `glass`; the experiment is water-quenched, but the source does not say Matrix composition is preserved, so each row records `not_stated` and the page 2 quench statement. Table 5 says the water-quenched and slow-cooled spinel compositions are nearly identical, recorded as `asserted_preserved` on page 5. Table 4 Spinel rows and Table 5 remain context because `simulator/battery/enums.py::PhaseRole` has no solid role, `simulator/battery/migrate.py::_phase_role_token` rejects roles outside the closed set, and `simulator/battery/migrate.py::PHASE_MAP` maps crystal identity but does not supply a solid phase role.

The initial Table 1 charge is retained at its printed table locator. It is not substituted for the post-equilibrium bulk slag or Matrix composition. For Table 6 activity and XMn rows, the post-equilibrium composition is not published; the initial charge is retained under `point_conditions.printed_composition`, with `point_conditions.composition` unknown. For Table 3 rows 1–4, the matching initial recipe is an explicit FactSage input and reaches Identity. Table 3 row 5 has no separately printed CaO and SiO2 amounts, so Identity composition remains unknown.

| Migrated observation | Identity composition | Identity T | Identity total P | Identity fO2 |
| --- | --- | ---: | --- | ---: |
| Table 3 row 1 Al2O3 activity, page 3 | Printed Table 1 row 1 recipe reaches Identity as mole fractions | 1673 K | Unknown; not printed | (1.01325\times10^{-8}) Pa |
| Table 4 row 1 Matrix O, page 4 | Unknown; post-equilibrium bulk composition is not printed; initial Table 1 charge remains separately located in point conditions | 1673 K | Unknown; not printed | (1.01325\times10^{-8}) Pa |
| Table 6 row 1 MnO activity, page 6 | Unknown; post-equilibrium melt composition is not printed; initial Table 1 charge remains separately located in point conditions | 1673 K | Unknown; not printed | (1.01325\times10^{-8}) Pa |
| Table 3 row 5 Al2O3 activity, page 3 | Unknown; only basicity 0.82 is printed, without separate CaO and SiO2 amounts | 1673 K | Unknown; not printed | (1.01325\times10^{-8}) Pa |

## Schema and migration findings

- Table 1 is an initial-charge recipe, not a post-equilibrium phase result. It is retained as context and separately located starting-charge evidence. The closed `tools/validate_literature_extracts.py::OBSERVATION_TYPES` and `simulator/battery/migrate.py::QUANTITY_ALIASES` do not provide an exact initial-charge-only result type; the existing `composition_role=initial_charge_only` path is used where appropriate.
- The Matrix component values themselves have supported tokens: `composition_series`, `equilibrium_phase_composition`, phase `glass`, and `mole_fraction` in `simulator/battery/enums.py::PHASE_BASIS_TOKENS`. The printed output is elemental rather than oxide-based; Identity's oxide map `simulator/battery/migrate.py::_COMPOSITION_COMPONENTS` does not accept O/Ca/Si/Al/Mg/Fe/Mn as a bulk slag vector. The extract preserves each element as a typed phase-composition observation and leaves the distinct bulk-composition axis unknown.
- Isolated migration yields 42 observations, 0 hard issues, 0 registry issues, 1 unavailable typed-refusal value, 0 phase-unknown observations, and exactly one quantity-unknown observation: the prose precipitation ratio described above.
- The single-extract validator reports the YAML valid, including its fidelity samples. The focused test confirms the table render pins, the 33-row count, the Table 3 identity composition, known condition axes, and exact schema-refusal set. Its independent mutation test passes. The focused migration test fails at the first Table 4 mole-percent component: 51.93 is migrated instead of 0.5193.
- `tests/test_yaml_flow_scalar_commas.py`: 17 passed. The repo-wide `tests/test_literature_extracts.py` is `UNVERIFIED-local`: the first full run reached 129 passing tests and exposed a missing root `review_status`, which has been added; a second attempt to run the repository validation test alone was interrupted at the 300-second local cap without a result.
- The required mol% rule cannot currently be satisfied for Table 4 Matrix values without a migrator change. The extract correctly stores each printed value with canonical unit `%` and phase basis `mole_fraction`; however, `simulator/battery/migrate.py::QUANTITY_SOURCE_FIELDS` selects `component_amount`, `simulator/battery/migrate.py::select_declared_source` routes it through `simulator/battery/migrate.py::_convert_compilation_amount`, and that converter returns the amount as published for `Quantity.EQUILIBRIUM_PHASE_COMPOSITION`. Isolated migration therefore returns 51.93 for the first Matrix O cell; the required value is 0.5193. Hand division is prohibited, and extraction work must not edit `simulator/` or `tools/`. The focused migration test intentionally asserts the required converted value and fails on this defect. Stop for the controller's migrator-change decision.

No generated observations-v2/battery store was committed. Expected store delta after the migrator fix and regeneration: 42 observations and 4 experiment records.

# Cell audit — D’Ans and Löffler (1930)

Source: `raw/dans-loffler-1930-na-silicate-zirconia-phase-equilibria/dans-loffler-1930-na-silicate-zirconia-phase-equilibria.pdf`; source data were read first from MinerU tables/markdown and pdftotext-layout. The PDF was used only as page renders for cell and layout checks.

The audit contains 373 distinct numeric source cells. Every listed cell was checked by method (b), rendered page; the `rendered_token` is the spelling transcribed from the render. The MinerU draft CSV text is retained beside it for comparison. All in-scope table pages (9, 12, 13, 18, 26, 28, 30, 34, 35) were checked, including each table’s row/column assignment.

## Printed row counts and column mapping

| Page | Printed table/section | Printed body rows | Source columns and stored data |
|---:|---|---:|---|
| 9 | 1 | 12 | 3–4 composition; 5 Na2CO3 addition; 6–10 thermal effects; transition_temperature; columns 7/9 are cr solid transitions; column 10 is measured_reduced with same-row primary temperature parents |
| 12 | 2 | 12 | 2 temperature; 3–4 composition; context only: mixed Morey and Bowen and author results have no row-wise attribution |
| 13 | 3 | 18 | 3–5 composition; 6 Na2CO3 addition; 7–10 thermal effects; transition_temperature; thermal effects measured_direct; point compositions cross-listed in page 35 summary |
| 18 | 4 | 13 | 3–5 composition; 6–9 thermal effects; transition_temperature; measured_direct; indistinct U values retain source qualifier |
| 26 | 5 | 2 | 3–5 composition; 6–8 thermal effects; transition_temperature; measured_direct; parentheses and U qualifiers retained |
| 28 | 6 | 16 | 3–5 composition; 6 observation/temperature text; transition_temperature for 12 detectable rows; four no-temperature rows retained as context |
| 30 | 7 | 2 | 3–5 composition; 6–9 thermal effects; transition_temperature; measured_direct; Table 7 column assignment retained for point 50 |
| 34 | un-numbered pure-compound summary | 7 | 3 melting/transition description; transition_temperature; l for melt, cr for the beta-to-alpha solid transition |
| 34 | un-numbered binary invariant summary | 3 | 3 temperature; 4–6 composition; transition_temperature and equilibrium_phase_composition; measured_direct |
| 35 | un-numbered ternary invariant summary | 6 | 3 temperature; 4–6 composition; transition_temperature and equilibrium_phase_composition; measured_direct |
| 35 | un-numbered binary-boundary summary | 4 | 3 temperature; 4–6 composition; transition_temperature and equilibrium_phase_composition; measured_direct |

Table 2 has 12 printed rows and remains context because it mixes Morey and Bowen values with the authors’ measurements and gives no row-wise attribution. Table 3 has 18 printed rows; its seven point-composition rows are preserved as related locators on page 35 component observations, including its J label for the summary’s I composition. Table 4 has 13 printed body lines, including point 12 and point 29 continuation lines; point N and the continuation with runs 15–17 have no temperature and remain context. Table 6 has 16 rows; four rows with no detectable temperature retain their compositions as context. The un-numbered summaries on pages 34–35 are mapped by section and page, never assigned MinerU block ordinals as printed table numbers.

The per-cell audit in the sibling CSV gives absolute PDF page, printed table label where present, printed point/row, MinerU draft index and CSV coordinates, render token, stored field/observation ids, and check method. The compare-only mapping records table and column selectors plus render-verified header cells. MinerU comparison cannot parse comma-decimal cells or temperatures embedded in descriptive text; these are resolved from the renders, not treated as clean matches.

The committed MinerU compare output reports 9 matched and 158 mismatch statuses (zero no-match, locator-error, or unverifiable). The mismatches are comparator limitations rather than unresolved cell differences: 157 stored observations use the canonical phase token `l`, which `tools/mineru_table_draft.py:1071-1083` rejects because `_phase_matches_column` accepts only `gas`, `liquid`, or `solid` (it normalizes `cr` to `solid`, but not `l` to `liquid`); the remaining `cr` row is the page 34 beta-to-alpha cell printed as text `960° → α-Na4SiO4`. The exact `l` token is required by the migrator, so it is preserved. All 158 compare mismatch ids map to the sibling cell audit and were confirmed against the page renders.

## Render resolutions

| Page/table | CSV row/column | Resolution |
|---|---:|---|
| 13 / 3 | 6 / 8 | Point 14 eutectic is 959 °C in the render. |
| 18 / 4 | 8 / 7 | Point 23 eutectic is 1076 °C in the render. |
| 28 / 6 | 7 / 5 | Point 36 ZrO2 is 37,5 mol%; pdftotext reads 37,6. |
| 30 / 7 | 5 / 3–5 | Point 50 table columns read 47,7 Na2O / 3,0 SiO2 / 49,3 ZrO2. The nearby prose gives a different 3 mol% ZrO2 test composition; the printed table column assignment is retained. |
| 35 / un-numbered binary-boundary summary | 5 / 6 | Point Q ZrO2 is printed as 31.4 with a decimal point. |

Other numeric cells on these pages agreed with the values recorded in the audit after checking their render row and column. The MinerU QA pre-screen scored the printed Tables 1–7 and summary tables poorly (numeric agreement 0.113–0.667 where numeric cells were available); this is why the complete in-scope tables were rendered rather than accepting machine agreement as confirmation. The four extra MinerU tables on pages 15, 21, 21, and 25 are solubility/solution-analysis tables, outside liquidus and invariant-point scope; none was used as phase-equilibrium data.

## Evidence and schema

Own thermal measurements and reported invariant compositions use `measured_direct`. Table 1 corrected primary liquidus values use `measured_reduced`: the authors graphically correct the soda-depressed measurements; the stored same-row primary liquidus observations are the lineage parents. Table 2 values stay context because attribution is not resolved row by row. No matching closed method token describes the thermocouple Tiegelofen heating/cooling-curve procedure, so method is left unstated rather than assigned DTA or another unsupported token.

No schema gap applies. `tools/validate_literature_extracts.py:96-118` defines the closed `OBSERVATION_TYPES`, including `transition_point`, `composition_series`, and `equilibrium_phase_composition`. `simulator/battery/enums.py:102,108,113` defines exact `Quantity` tokens `transition_temperature`, `liquidus_composition`, and `equilibrium_phase_composition`; `simulator/battery/migrate.py:6647-6652` accepts exact enum-value tokens, while `TYPE_QUANTITY` at 442-451 maps `transition_point` to `Quantity.TRANSITION_TEMPERATURE`. The canonical tokens are recognized by the direct enum-value branch; no near-miss alias is used. `liquidus_composition` is not applied to the Table 1/3/4/5/6/7 mixture columns: those are printed charge-composition conditions; the page 34-35 invariant summaries print coexisting phase compositions and use `equilibrium_phase_composition`. The latter token also appears in the existing precedent `data/literature/extracts/park-2021-vanadium-thermodynamics-thesis.yaml:253`. `simulator/battery/migrate.py:387-393` includes the phase tokens `cr` and `l`; `simulator/battery/enums.py:313-324` defines phase roles including `PhaseRole.MELT`, and lines 676-681 list `mole_fraction` in `PHASE_BASIS_TOKENS`. Melt observations use phase `l` and role `melt`; the alpha/beta solid transition uses phase `cr`.

Every observation carries a printed Celsius temperature and same-row composition where available. No total pressure or oxygen fugacity is printed. Each source observation records both as `not_printed` with a locator; the migrator omits profile-irrelevant pressure from transition-temperature point conditions and marks fO2 not applicable on that identity profile. No pressure or fO2 value is present. Table 1/Table 3 Na2CO3 additions remain separate row annotations and are not folded into the oxide-basis melt composition. No figure values were digitised; Figures 1–6 remain figure-read candidates.

## Isolated migration

Real migrator result: 167 observations, 0 hard issues, 0 unavailable missing-input work items, 0 quantity-unknown rows, and 0 phase-unknown rows. Quantities are 131 `transition_temperature` and 36 `equilibrium_phase_composition`; phases are 157 `l` and 10 `cr`. All 167 temperatures reach `point_conditions.temperature_K`. The 36 composition observations retain `total_pressure_Pa` unknown with `not_printed`; the 131 transition rows omit that profile-irrelevant point-condition key, while their identity pressure remains unknown with `not_printed`. `fO2_Pa` is unknown/not_printed in point conditions for all rows; the transition-temperature identity profile marks fO2 not applicable. No pressure or fO2 value is present.

| Migrated observation | composition | total_pressure_Pa | fO2_Pa | temperature_K |
|---|---|---|---|---|
| p.9 Table 1 experiment 1, run 135/6, 1075 °C | Na2O 0.663; SiO2 0.337 | unknown: not_printed | not_applicable | not_applicable |
| p.34 invariant O, approximately 1600 °C | Na2O 0.57; ZrO2 0.43 | unknown: not_printed | not_applicable | not_applicable |
| p.35 invariant K, Na2O component, 1007 °C | Na2O 0.54; SiO2 0.45; ZrO2 0.01 | unknown: source does not print P | unknown: source does not print fO2 | 1280.15 K |

The isolated migrator was run with an empty index. Its 334 queue entries split into 167 unstated method-token items and 167 missing-INDEX-asset items; the latter are expected in this no-regen isolated run because `data/literature/INDEX.yaml` is derived and does not yet list the new source. These are not hard issues or unavailable observations. The extraction leaves the measurement method unstated because the source names no matching closed method token.

The extract has 167 observations: 131 transition temperatures and 36 equilibrium phase-composition components. The expected generated store delta is +167 observations; no store regeneration was committed.

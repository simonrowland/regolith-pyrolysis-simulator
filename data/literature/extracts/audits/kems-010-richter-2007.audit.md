# Richter et al. (2007) condition recovery audit

## Scope and source coverage

This patch adds experimental condition metadata to the existing Richter et al. (2007) extraction. It does not re-extract numeric table values. Table 1 contains 51 experimental runs: 36 on absolute PDF page 6 (printed page 5549) and 15 on page 7 (printed page 5550), plus one `Starting material` row on page 6. The row labels and oxide headings were checked against the page renders. The migrated residue composition count is 204 rows, 51 each for MgO, SiO2, Al2O3, and CaO.

MinerU and pdftotext agree on all numeric cells checked by `tools/mineru_table_qa.py`: Table 1 page 6 has 468 numeric cells, Table 1 continuation page 7 has 205, and Table 2 page 16 has 19. Agreement was 1.000 for each page. Renders were used to verify row and column placement on pages 6 and 7 and to check the distinct prose, figure, and Table 2 locators on pages 5, 8, 9, 10, 12, and 16. No individual cell required a render because the machine reads agreed and no cell was flagged. Eleven invented MinerU `<details>` chart blocks were ignored.

The committed compare output contains only its CSV header: `tools/mineru_table_draft.py compare` does not inspect this extract's existing nested `values.series` records. It therefore emitted no candidate cells and is not treated as a cell-level pass. The independent QA digit comparison and the render layout checks above are the numeric audit evidence.

## Conditions recovered

Section 3.1 on absolute PDF page 5 (printed page 5548) identifies the current experiments as vacuum evaporation in a Hashimoto high-temperature furnace. Samples were partially melted on 0.25 mm iridium wire loops, exposing a free surface. The printed chamber condition is `<10^-6 Torr` outside the heating elements and heat shields surrounding the samples. This is stored as a pressure upper bound converted to Pa; the extract does not turn that bound into an exact sample-surface pressure. The sample-side exact pressure and cell-internal pressure remain typed unknown. The paper does not state a sweep-gas condition, so sweep gas remains typed unknown with reason `not_published`.

The 2007 experimental rows do not print an absolute fO2, pO2, buffer, or gas ratio. Each observation retains a typed unknown oxygen condition with the page 5 locator. “Vacuum” is kept as the printed atmosphere and is not converted to an oxygen fugacity. The paper also compares the 2007 vacuum runs with earlier Richter et al. (2002) experiments in hydrogen, including Figure 2 and Figure 3 material on pages 9 and 10. Those comparison points are not Table 1 observations from this study; no H2 pressure or fO2 was transferred to the 2007 rows. Figure-only comparison values remain out of scope.

All 51 Table 1 experiment records now point to their row-specific printed temperature and run time. The initial composition is linked to the single Table 1 `Starting material` row on page 6 (11.48 wt% MgO, 46.00 wt% SiO2, 19.39 wt% Al2O3, and 23.12 wt% CaO). All 50 mass-loss observation composition links cite that row with both absolute PDF page 6 and printed page 5549. The 8 numeric surface-area rows now link to the matching run experiment and temperature instead of a broad temperature range or the wrong run.

The paper states on page 5 that the residues cooled to room temperature and were quenched to glass. Table 1 residue compositions therefore use phase `glass`, and their quench state is `not_stated`: the paper does not claim that quenching preserved or altered composition. All 51 residue observation row locators now carry the physical Table 1 page so their row-level temperatures retain page provenance through migration. Evaporation loss and flux observations remain phase `l`, because those values describe the hot run.

## Migration and identity axes

The isolated migration is summarized below. Complete identity means every axis required by the migrated quantity profile has a value; unknown schema-gap quantities cannot be complete.

| Quantity | Before rows | Before complete identity | After rows | After complete identity |
| --- | ---: | ---: | ---: | ---: |
| `mass_loss_fraction` | 50 | 0 | 50 | 0 |
| `quantity_unknown` | 96 | 0 | 96 | 0 |
| `residue_component_composition` | 204 | 0 | 204 | 0 |

| Migration result | Before | After |
| --- | ---: | ---: |
| Observations | 350 | 350 |
| Hard issues | 0 | 0 |
| Unavailable values | 92 | 92 |
| Queue rows | 398 | 396 |
| Phase-unknown observations | 2 | 0 |

The migrated identity axes were checked on three rows:

| Row | Composition | Total pressure | fO2 | Temperature |
| --- | --- | --- | --- | ---: |
| Mg mass loss `R3-10` | starting charge composition mapped | unknown: only an upper bound is printed outside furnace elements and shields | unknown: not printed | 2173.15 K |
| Mg mass loss `R2-13` | starting charge composition mapped | unknown: only an upper bound is printed outside furnace elements and shields | unknown: not printed | 1873.15 K |
| MgO residue `R3-10` | starting charge composition mapped | unknown: exact sample-side pressure is not printed | unknown: not printed | 2173.15 K |

The pressure bound is preserved on each observation's point condition. Identity pressure remains unknown because `simulator/battery/identity.py::Identity` represents the identity pressure axis as an exact decimal, while the source gives only an upper bound for the surrounding furnace and does not report an exact sample-surface value. The existing pressure path is `simulator/battery/migrate.py::_pressure_total_from_extract`; no migrator change was made. Temperature, composition, pressure condition, and oxygen condition all reach the migrated point or experiment records through existing paths.

The migration retains exactly 96 quantity-unknown observations, all from the five source labels listed in the mapping file. These are deliberate schema gaps rather than near-match mappings. The closed sets checked were `tools/validate_literature_extracts.py::OBSERVATION_TYPES`, `simulator/battery/migrate.py::QUANTITY_ALIASES`, `simulator/battery/migrate.py::TYPE_QUANTITY`, and `simulator/battery/enums.py::Quantity`. `rate_series` is an accepted observation type, but it does not define these exact quantities; descriptive non-token labels prevent an automatic `rate_series` inference from changing the source meaning. The phase basis set and roles were checked at `simulator/battery/enums.py::PHASE_BASIS_TOKENS` and `simulator/battery/enums.py::PhaseRole`. The existing `park-2021-vanadium-thermodynamics-thesis.yaml` stores measured equilibrium phase compositions, and the existing `georges-2000-potassium-evaporation-thesis.yaml` uses `residue_component_composition`; therefore residue composition has a valid quantity token and is not listed as a schema gap.

The separate direct mass-loss observations remain `measured_direct`; residue composition is direct chemical analysis. The paper's `JMg` and `JSi` columns are calculated evaporation fluxes derived from measured mass, surface area, composition, and time, so a separately represented flux quantity would be `measured_reduced`. The existing composite and reduced-flux rows remain typed-refused under the unchanged legacy extraction; their values and legacy method-class fields were not rewritten in this condition-only census.

## Value preservation and figures

The existing Table 1 series values were not re-extracted. The focused test pins migrated mass-loss fractions of 0.32 for R3-10, 0.317 for R-14, and 0.367 for R2-13 to Table 1 page 6, Table 1 page 6, and the continuation on page 7, respectively. Celsius temperatures and minute durations are also pinned after migration. `vacuum_evaporation_flux_J_Mg_vs_T` remains one typed-refused figure-read candidate; no new figure values were added.

The compare artifact is `data/literature/extracts/audits/kems-010-richter-2007.compare.csv`. The mapping and page-by-page cell counts are in `data/literature/extracts/audits/kems-010-richter-2007.mapping.yaml`.

The score sample used the declared OpenIMCC pin 66ec4a3. The imported module resolved to `openimcc/__init__.py` in that pinned source tree; its Python source-tree hash was `308672269d6e`.

The first ten lexically ordered `mass_loss_fraction` rows were scored through `score.py` before and after the migration. No numeric candidates were produced in either run. Five rows changed refusal from `attempted_unavailable` to `underdetermined_apparatus`; the rest retained their refusal:

| Row | Before | After |
| --- | --- | --- |
| R-11 | `attempted_unavailable` | `underdetermined_apparatus` |
| R-14 | `underdetermined_apparatus` | `underdetermined_apparatus` |
| R-15 | `underdetermined_apparatus` | `underdetermined_apparatus` |
| R-16 | `identity_incomplete` | `identity_incomplete` |
| R-17 | `underdetermined_apparatus` | `underdetermined_apparatus` |
| R-18 | `attempted_unavailable` | `underdetermined_apparatus` |
| R-2 | `attempted_unavailable` | `underdetermined_apparatus` |
| R-3 | `attempted_unavailable` | `underdetermined_apparatus` |
| R-4 | `attempted_unavailable` | `underdetermined_apparatus` |
| R-6 | `identity_incomplete` | `identity_incomplete` |

The targeted mutation check removed the printed vacuum pressure condition from a copied migrated observation; the focused condition invariant rejected the modified row. This confirms the test detects loss of the row-level pressure evidence.

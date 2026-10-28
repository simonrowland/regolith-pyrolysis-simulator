# Shornikov (2018) spinel KEMS conditions audit

Source: Shornikov, S. I. (2018), “Mass Spectrometry Study of the Thermodynamic Properties of Spinel Solid Solutions,” *Russian Journal of Physical Chemistry A* 92(8), 1447–1456, DOI 10.1134/S0036024418080253. The relevant work record is `shornikov-2018-spinel-solid-solutions`.

## Scope and adjacent 2018 abstracts

The task-named abstract 6137, `shornikov-2018-cai-complex-gas-species`, contains no scored observation. It summarizes earlier KEMS work, names several systems and qualitative trends, and gives a gas-fraction bound without a row composition. It does not contain pressure rows to condition. `shornikov-2018-cai-double-inversion` is a separate 2173 K W-cell study with five initial-composition rows and deferred figure reads, but no vapor-pressure observations. `shornikov-2018-thermodynamic-evaporation-model` is the abstract for a broad lunar and meteoritic modeling study, duplicated by the 2019 journal paper; it contains no pressure rows. Neither abstract shares the spinel study's KEMS run.

The existing spinel extract contains the matching Table 1 KEMS vapor-pressure series. I kept its printed data values and added conditions to its existing 168 observations. No rows were added from the task-named abstract or the adjacent extracts.

## Printed tables and cell audit

The three tables are on PDF pages 6, 6, and 8 (journal pages 1452, 1452, and 1454); the table labels below are the printed labels, not MinerU block indices.

| Printed table | Printed layout | Existing values retained | Cell reconciliation |
| --- | --- | ---: | --- |
| 1 | 12 composition rows × 8 species-pressure columns | 96 | MinerU and pdftotext agree on digits, signs, and exponents; page 6 render confirms row and column assignments. |
| 2 | 12 composition rows × 5 calculated atomic-oxygen pressure columns | 12 | The existing extract retains only the Eqs. (22)–(23) column. Both machine reads agree; page 6 render confirms layout. The other four columns were not re-extracted. |
| 3 | 12 composition rows × 5 activity columns | 60 | 57 cells agree between machine reads. Three starred cells were checked against the page 8 render. |

The printed total is 12 rows per table. The retained scored data count is 96 + 12 + 60 = 168 observations. Table-tool QA reports digit agreement of 1.000 for Tables 1–3 (108, 72, and 72 numeric cells including composition axes). The committed cell audit contains 207 rows: the 168 retained numeric cells plus the printed composition and temperature axes. The committed MinerU compare reports 165 matched cells and three starred-cell mismatches, all resolved against the page render; there are zero locator errors.

The three render-verified cells are Table 3, page 8: x(Al2O3)=50.00, MgO Eq. (41), printed `3.50 × 10^-1*`; and x(Al2O3)=76.25, Al2O3 Eqs. (29) and (42), each printed `1.00*`. The asterisk is the table's accepted-value marker, not part of the numeric value. The render agrees with the stored numbers 0.35 and 1.00. The cell-by-cell evidence and storage values are in `shornikov-2018-spinel-solid-solutions-cell-audit.csv`; the column mapping and explicit flagged cells are in `shornikov-2018-spinel-solid-solutions.mapping.yaml`.

The exact text layer and MinerU agree on the numeric cells. The only render-based numeric decisions were those three footnoted cells. Page 6 was also rendered to check the Table 1 and Table 2 row/column layout. Pages 4 and 5 were rendered for the experimental-condition and apparatus statements. Existing context locators were corrected where the folio was off: six page-1 references now use 1447, three page-5 references use 1451, and five page-8 references use 1454. In the extract, every table locator uses the printed label `1`, `2`, or `3`.

## Conditions carried into the migration

- The study uses stationary KEMS evaporation from a molybdenum Knudsen cell (PDF page 4, journal page 1450), over the MgO–Al2O3 binary. The sample's starting composition is stoichiometric MgAl2O4, MgO=0.5 and Al2O3=0.5, linked on the experiment sample. The table rows each carry their own printed x(Al2O3) composition.
- Tables 1–3 report 2100 K. Each of the 168 observations now has a located `temperature_K` and `composition` point condition. The migration consumes these conditions through `simulator/battery/migrate.py::_point_condition_from_plain` and binds identity axes through `simulator/battery/identity.py::profile_for`.
- Table 1's printed pO2 values are matched by the same x(Al2O3) to the other vapor-pressure rows and the activity rows. They are converted from atm by the migrator's unit handling and kept with their published-unit value. Table 2's calculated atomic-oxygen pressures are outputs, not fO2 conditions. The 12 Table 1 pO2 output rows have typed unknown fO2 to avoid using their own output as their condition. The oxygen mapping follows `simulator/battery/migrate.py::printed_oxygen_decision`.
- The paper does not report total or ambient pressure, identify a vacuum or carrier-gas pressure, state a carrier gas, or give a total run duration. Each row carries a typed unknown `pressure_kind` with the page 4 experimental locator. Shared experiment `pressure_environment.total_pressure_Pa` is typed unknown with that locator and migrates to every row identity. The migrator intentionally removes unknown row-level total-pressure conditions from `Observation.point_conditions`, so no redundant point value is stored; see `simulator/battery/migrate.py::_point_condition_from_plain` and `simulator/battery/migrate.py::_without_unprinted_total_pressure`. The experiment also keeps sweep gas, pressure regime, and duration unknown. No vacuum or standard-pressure value was assumed; duration is read by `simulator/battery/migrate.py::_thermal_schedule_from_plain`.
- The cell is identified, but the orifice diameter and channel length are not printed (PDF page 5, journal page 1451), so both remain typed unknown. The apparatus method is Knudsen effusion. The flow regime and pressure kind remain unknown because the paper gives no geometry value or chamber-pressure condition.
- Table 1 pressures are calculated from integrated ion-current inputs using Eqs. (17)–(18), but the current inputs are not tabulated. The eight Table 1 pressure series therefore carry `quoted_attributed` with attribution to the authors' Table 1 results; they are not labeled as measured reductions with unstored parents. The stored Table 2 Eq. (22)/(23) pressure series remains `model_derived`. Table 3 values retain their existing measured-reduced classification and lineage.
- The five Table 3 activity series now use canonical phase `cr` (instead of the unsupported `condensed_solid` spelling); `simulator/battery/migrate.py::PHASE_MAP` maps that token to the crystalline solid state. Numeric values are unchanged.

No schema gap was encountered. The stored quantity tokens are `partial_pressure` and `activity`, which migrate to `p_partial` and `activity`; no descriptive or unknown quantity or phase remains.

## Isolated migration

The base and candidate each migrate 168 observations with zero hard issues and zero unavailable values. The candidate has zero new hard issues, zero quantity-unknown observations, and zero phase-unknown observations. Temperature and composition are located on all 168 observations both before and after.

| Quantity | Rows | Complete identity before | Complete identity after | fO2 identity values before | fO2 identity values after |
| --- | ---: | ---: | ---: | ---: | ---: |
| `activity` | 60 | 60/60 | 60/60 | 0/60 | 60/60 |
| `p_partial` | 108 | 0/108 | 0/108 | 0/108 | 96/108 |

All partial-pressure rows remain identity-incomplete because total pressure is not printed. The twelve pO2 outputs correctly have no fO2 condition; other partial-pressure rows have fO2 values for 96/108 observations, including the twelve Table 2 atomic-oxygen outputs.

For the three partial-pressure rows pinned in the focused test, the profile requires `composition`, `fO2_Pa`, `temperature_K`, and `total_pressure_Pa`:

| Row at x(Al2O3)=50.00 | Composition | Temperature | fO2 | Total pressure |
| --- | --- | --- | --- | --- |
| Table 1 pMg | MgO 0.5, Al2O3 0.5 | 2100 K | 0.032424 Pa | unknown: not published |
| Table 2 atomic O, Eqs. (22)–(23) | MgO 0.5, Al2O3 0.5 | 2100 K | 0.032424 Pa | unknown: not published |
| Table 1 pO2 | MgO 0.5, Al2O3 0.5 | 2100 K | unknown: row is the pO2 output | unknown: not published |

## Ten-row score sample

The sample is the eight Table 1 species rows at x(Al2O3)=50.00, the stored Table 2 atomic-O row at that composition, and the Table 3 MgO Eq. (27) activity row. Scoring used the declared OpenIMCC pin 66ec4a3 (imported package tree hash prefix `308672269d6e`) without tuning.

| Migration | Produced | Refused | Refusal reasons |
| --- | ---: | ---: | --- |
| Base | 0 | 10 | `unsupported`: 9; `underdetermined_apparatus`: 1 |
| Candidate | 0 | 10 | `unsupported`: 9; `effusion_regime_unverified`: 1 |

No sample row becomes scoreable because the source does not print total pressure or a verified flow regime. For Table 3 MgO Eq. (27), the refusal changes from `underdetermined_apparatus` on the base to `effusion_regime_unverified` on the candidate; the other nine remain `unsupported`. All ten remain refused. The scores were not tuned; scoring used `simulator/battery/score.py::score_store`.

## Expected store delta

No observation is added and no generated store is committed. The 168 existing rows gain located pressure-kind and oxygen point conditions; experiment-level total pressure remains typed unknown and maps to all 168 identities. The pressure kind remains unknown on all rows. Of these, 156 rows receive matched pO2-derived fO2 and the twelve pO2 output rows retain typed unknown fO2. The eight Table 1 series are attributed to their source because the raw ion-current inputs are absent. Existing pressure and activity values are unchanged. The committed phase and locator corrections are as described above.

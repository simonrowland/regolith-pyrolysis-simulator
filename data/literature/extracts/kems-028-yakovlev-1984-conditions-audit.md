# Yakovlev et al. (1984): condition recovery and extraction audit

## Scope and conditions

The extract already contains 24 `partial_pressure` source observations. I added source-located conditions to those records and their table/figure points without creating observations or changing measured values, evidence classes, or phase labels. A baseline comparison across all 27 source observations found zero value-payload changes (excluding nested condition metadata), zero phase-label changes, and zero evidence-class changes. Page 945 names Murchison chondrite and Efremovka CAI inclusions of types A and B and states that the measurements used a Knudsen effusion cell. It does not print a numerical bulk oxide analysis or oxide basis for either sample, the cell orifice size, the experimental total/ambient pressure, or an absolute or controlled oxygen fugacity. Those axes remain typed unknown. The sample labels identify the named material, but no printed analysis can be linked from this abstract.

The abstract gives an overall experimental temperature range of 1000–2500 °C; the prose rows and plotted series retain their stated temperatures. The page 946 table reports calculated `p(O2)` values derived using `AlO = Al + O` and `O2 = 2 O`. They are calculated outputs, not the experiment's independently imposed `fO2` or its total pressure. The closing comparison describes one sample as vaporizing under relatively more oxidizing conditions, but provides no per-run absolute value or buffer-relative setting. `fO2_log` therefore remains unknown for each observation.

The existing migrator's `_partial_pressure_identity_fields` and `_point_condition_from_plain` carry source-grounded axes into identity and point conditions. `_without_unprinted_total_pressure` intentionally removes an unknown total pressure from migrated point conditions because it is not a set point; the identity retains its typed unknown. This leaves composition, total pressure, and `fO2_Pa` unavailable while preserving temperature. The quantity profile and score refusal are handled by `simulator/battery/identity.py::profile_for` and `simulator/battery/score.py::score_store`.

## Alkali rows with values and unavailable rows

| Existing row with a printed value | Printed value | Migrated score |
| --- | ---: | --- |
| `yakovlev_1984_murchison_Na_P_1225C` | Na: 2.7×10⁻⁴ torr at 1225 °C | Refused `identity_incomplete`: no printed bulk composition, total pressure, or absolute `fO2` |
| `yakovlev_1984_murchison_K_P_1225C` | K: 4.2×10⁻⁵ torr at 1225 °C | Refused `identity_incomplete`: no printed bulk composition, total pressure, or absolute `fO2` |

The ticket's remaining six rows were a census estimate; no six corresponding observation IDs or separate numeric Na/K records exist in the current extract. The existing Fig. 1 aggregate Na record has no point values, and no K points were digitized in the existing companion audit. No values or rows were added from figures, and those six estimated rows are unavailable to score individually.

## Page 946 table audit

The table is headed “Table” and its caption begins with `p(O2)`; it has no printed table number. Locators therefore use published page 946 and zero-based PDF page index 1 without a `table` field. The MinerU draft contains seven body rows, four columns, 11 filled pressure cells, and 10 blank pressure cells. Table QA found only 0.100 numeric agreement across 18 cells (11 LaTeX-number flags and five letter-for-digit flags), so every stored numeric cell was checked against the page render. The row and column assignments below were checked against the same render. QA also identified two invented `<details>` tables in chart blocks; they were ignored. Blank cells remain unavailable and are not stored as zero.

| Draft CSV row | Page | T (°C), column 1 | Murchison, column 2 | Efremovka A, column 3 | Efremovka B, column 4 | Audit |
| ---: | ---: | ---: | ---: | ---: | ---: | --- |
| 2 | 946 | 2020 | 1.4×10⁻⁶ | blank | blank | Render (b), all numeric cells |
| 3 | 946 | 2072 | 1.6×10⁻⁶ | blank | blank | Render (b), all numeric cells |
| 4 | 946 | 2130 | 1.5×10⁻⁶ | 2.0×10⁻⁵ | 3.4×10⁻⁴ | Render (b), all numeric cells |
| 5 | 946 | 2180 | blank | 4.7×10⁻⁶ | blank | Render (b), all numeric cells |
| 6 | 946 | 2205 | blank | 3.6×10⁻⁶ | 2.8×10⁻⁴ | Render (b), all numeric cells |
| 7 | 946 | 2267 | blank | 5.5×10⁻⁵ | 4.1×10⁻⁵ | Render (b), all numeric cells |
| 8 | 946 | 2348 | blank | 1.9×10⁻⁴ | blank | Render (b), all numeric cells |

The three Murchison pressure cells use a typewriter `I` where the numeral 1 is intended; the render confirms 1. The Efremovka B cell on row 7 has a typewriter `I` in `4.1`, also confirmed against the render. The converter's row/column-aware compare emitted no matched rows because the printed table is unnumbered and the extract correctly has no table locator; its output is committed as `audits/kems-028-yakovlev-1984-compare.csv`. Manual cell verification is recorded in `audits/kems-028-yakovlev-1984.mapping.yaml`.

## Figure marker audit

Figures 1 and 2 are on page 946 (PDF index 1). Their species labels are printed inline beside the curves rather than in a separate legend. At 600 dpi the printed label-to-marker bindings are SiO(g) → open circle, Mg(g) → open square, Ca(g) → filled square, and Al(g) → open triangle. The mapping audit records these pairings separately for both figures. All 28 existing Fig. 1 points and 41 existing Fig. 2 points retain their two pixel-center reads and axis fits in the companion figure CSV. The focused test checks the marker on every stored point, pins each series' first pressure to the rendered figure, and verifies that mutating a marker or pressure fails the fidelity matcher.

## Schema gap retained as a typed refusal

The two existing Efremovka rows `yakovlev_1984_CAI_Al_suboxide_fraction` and `yakovlev_1984_CAI_Al2O_in_suboxide_fraction` report the combined AlO + Al2O partial-pressure fraction of total aluminium pressure (type A 0.18–0.20; type B mean 0.4). This is a ratio of gas partial pressures, not a `p_partial` value, a mole fraction within a condensed phase, or a phase-to-phase distribution ratio. `simulator/battery/enums.py::Quantity` has no exact quantity token for it; `simulator/battery/migrate.py::QUANTITY_ALIASES` has no exact alias. `simulator/battery/migrate.py::TYPE_QUANTITY` maps `psat_series` to `p_sat`, which is not an exact token for this ratio, so the explicit descriptive label prevents that fallback. `simulator/battery/enums.py::PHASE_BASIS_TOKENS` and `simulator/battery/enums.py::PhaseRole` describe phase-composition bases and roles such as melt/glass, not this gas ratio. `tools/validate_literature_extracts.py::OBSERVATION_TYPES` includes `equilibrium_phase_composition`, which the existing `park-2021-vanadium-thermodynamics-thesis.yaml` uses for composition within a phase; that is not this quantity. The descriptive non-token `values.quantity: Al_suboxide_pressure_fraction` is retained so both rows migrate as quantity-unknown and remain refused. No near-miss token or schema change was introduced.

## Isolated migration and score

The pinned OpenIMCC import was from the declared 66ec4a3 source tree (Python-source digest `308672269d6e6090023f4a86c4fc0d83c4c24688`). The before/after migration and source score counts are unchanged:

| Measure | Before | After |
| --- | ---: | ---: |
| Migrated observations | 98 | 98 |
| Total validation issues | 261 | 261 |
| Hard validation issues | 0 | 0 |
| New hard issues | — | 0 |
| Registry issues | 0 | 0 |
| Score residuals | 94 | 94 |
| Produced candidates | 0 | 0 |
| Queue | 60 | 60 |

The three requested identity snapshots after migration are:

| Row | Composition | Total pressure | `fO2_Pa` | Temperature |
| --- | --- | --- | --- | ---: |
| `yakovlev_1984_murchison_Na_P_1225C` | unknown: numerical bulk composition not published | unknown: in-cell total pressure not derivable | unknown: source gives no absolute `fO2` | 1498.15 K |
| `yakovlev_1984_murchison_K_P_1225C` | unknown: numerical bulk composition not published | unknown: in-cell total pressure not derivable | unknown: source gives no absolute `fO2` | 1498.15 K |
| `yakovlev_1984_murchison_Fe_troilite_1050C` | unknown: numerical bulk composition not published | unknown: in-cell total pressure not derivable | unknown: source gives no absolute `fO2` | 1323.15 K |

Neither alkali row is produced: both remain refused `identity_incomplete`, because the profile requires composition, total pressure, absolute oxygen pressure, and temperature. Temperature is present; the other axes are correctly unknown. The isolated migration's quantity-unknown identity set has 11 unchanged rows: the two suboxide schema-gap rows, four existing range/bound rows (`Ca_max`, `Fe_max`, `Mg_max`, and `SiO_Mg_max`), and five existing method/ordering/categorical rows. Seven of those rows produce score residuals: the two schema gaps and five method/ordering/categorical records. The focused test pins both exact sets. The descriptive phase strings remain untouched under the controller's b-1080 ruling; phase-unknown results are reported there and are not counted as a defect in this task.

No battery store was regenerated. Expected store delta is zero new observations, zero changed values or evidence classes, and zero produced candidates; only source condition/locator metadata and audit/test records change.

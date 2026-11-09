# Wood & Wade (2013) trace activity extraction audit

## Scope and printed row count

The paper prints three numeric activity coefficients for trace oxide components in the silica-saturated silicate melt: PbO 0.22, CuO0.5 3.5, and InO1.5 0.02. All are author calculations from the BW1102 high-pressure Fe-rich metal–silicate partitioning experiment. The printed Table 1 is the only data table in the paper. Its MinerU block has 21 rows, including headings and labels, and 16 numeric data cells. The complete numeric-cell audit is in `wood-wade-2013-trace-activities.table1-cell-audit.csv`.

## Machine reads, render, and table reconciliation

The MinerU markdown and pdftotext layer agree on all 16 Table 1 numeric cells, including the parenthetical widths and the comma in `2,270 (37)`. The MinerU table QA reports 1.000 numeric digit agreement with pdftotext. The draft incorrectly classifies the first eight source rows as headers, so the physical PDF page 8 render was checked to confirm the row/column assignments. All 21 printed row slots and the six component rows, total, gamma FeO, trace analyses, and metal analyses match the rendered table. The render also explicitly confirms the digits in SiO2 `59.4 (0.4)`, silicate Cu `51 (7)`, and metal Cu `2,270 (37)`; the focused test pins those three rendered cells independently of the CSV audit. No digit mismatch or flagged numeric cell remained.

The Table 1 footnote says `Value in brackets is 1 std dev`. Each parenthetical input width is retained as sigma with `one_sigma` in the linked context. The three calculated gamma values have no printed uncertainty; the input-analysis widths are not treated as output uncertainty.

The BW1102 run is captured as one experiment and linked from all three coefficients. The run's printed `1,650 °C` is retained as `T_C`; the coefficient statements print the corresponding reporting temperature `1,923 K`, retained as `T_K`. The experiment's total pressure is mapped from the Table 1 caption into `pressure_environment.total_pressure_Pa` as 1,500,000,000 Pa. No total pressure, temperature, fO2 or composition is added beyond the source. The starting recipe is retained as `Diopside28–Forsterite22–Anorthite50` with role `initial bulk`, but its basis and printed sum are absent; the run's Cu, Ag, and In/Pb dopants are named without initial amounts. Table 1 analyzed silicate components and sum are retained as printed, with their basis unknown. Those missing bases leave point composition unknown through migration.

The source describes the technique as metal–silicate partitioning, but the closed `MethodToken` set has no exact token for this technique (`simulator/battery/enums.py::MethodToken`). The experiment therefore carries an explicit unknown method state, while the printed technique remains in `method_as_printed` and the coefficient-level derivation descriptions. The SiO2 glass capsule is captured under the migrator's `cell_material_and_liner` field. The remaining procedure details are referred to Tuff et al. (2011), and the paper gives no fO2 control or sweep gas.

## Locator page checks

Every listed phrase was searched page by page in the pdftotext layer with `pdftotext -f N -l N`. The physical PDF page is one-based, the PDF index is zero-based, and the published page is the journal folio.

| Target | Proof string found by page search | Physical PDF page | PDF index | Published page |
| --- | --- | ---: | ---: | ---: |
| Figure 1 candidate | `Fig. 1 Activity coefficient` | 3 | 2 | 913 |
| Figure 2 candidate | `Fig. 2 Activity coefficient` | 5 | 4 | 915 |
| Figure 3 candidate | `Fig. 3 Activity coefficient` | 6 | 5 | 916 |
| Figure 4 candidate | `Fig. 4 Activity coefficient` | 7 | 6 | 917 |
| Run pressure and temperature | `1.5 GPa and 1,650 °C` | 8 | 7 | 918 |
| Table 1 composition and BW1102 | `Compositions of phases produced at 1.5 GPa/1,650 °C` | 8 | 7 | 918 |
| Starting recipe and its unstated basis/sum | `starting silicate composition of Diopside28–Forsterite22–Anorthite50` | 8 | 7 | 918 |
| Starting trace dopants and absent initial amounts | `doped with a mixture of trace elements (Cu, Ag, In, Pb predominantly)` | 8 | 7 | 918 |
| Capsule material | `The capsule was made of SiO2 glass` | 8 | 7 | 918 |
| Standard state | `standard state of pure liquid at 1 bar` | 8 | 7 | 918 |
| PbO gamma | `0.22 at 1,923 K` | 8 | 7 | 918 |
| CuO0.5 gamma | `3.5 in the silicate melt at 1,923 K` | 8 | 7 | 918 |
| InO1.5 gamma | `0.02 in the SiO2-rich liquid at 1,923 K` | 9 | 8 | 919 |
| Volatility definition and figure | `Volatilities of Pb, Cu and In from the silica-saturated melt` | 9 | 8 | 919 |
| Cu comparison read from Holzheid and Lodders' graph | `4.2 at 1573 K` | 8 | 7 | 918 |

The coefficient locators point to their derivation sentences in prose, not to Table 1. Table 1 is linked as the measured-input context. No numeric output locator is labeled as Table 1.

`mineru_table_draft.py compare` produced two `mismatch` results and one `no-match` result because the three gamma outputs are prose values on pages 8–9 and are not cells in Table 1. The compare output is committed in `wood-wade-2013-trace-activities.compare.txt`. The source-page searches above confirm the output statements and their row/component assignments; all 16 Table 1 cells are audited separately against both machine reads and the page 8 render.

## Values, derivations, standard state, and limits

- PbO: 0.22 at 1,923 K. The located sentence says the value follows by applying the Pb result and the printed Pb–Fe exchange `log K`.
- CuO0.5: 3.5 at 1,923 K. The located sentence applies the Cu activity coefficient in Fe to the Cu metal–silicate partitioning data. The paper extrapolates the cited liquid-Fe gamma of 11.0 at 1,823 K to 9.7 at 1,923 K by assuming constant `RT ln gamma`.
- InO1.5: 0.02 at 1,923 K. The located sentence applies the In partitioning data. The authors first assume regular-solution behavior for dilute Fe–In alloys, adopt `W_In = 52 kJ mol-1`, and calculate a liquid-Fe gamma of 25.8 at 1,923 K. Since the final value depends on that model-derived input, this row is `model_derived`.

The standard-state sentence says all components have the standard state of pure liquid at 1 bar and the temperature of interest. The rows carry a typed Raoultian pure-liquid endmember for their oxide formula, with reference pressure 1 bar. That is a reference state, not the experiment's chamber pressure.

Each coefficient is printed as a direct linear `gamma` value. The numeric value is stored without a transformation token, and `printed_form_as_published: linear` retains its source form; the migrator's `printed_form` field is reserved for its supported logarithmic transforms.

Each calculated row's lineage points to the fully qualified stored Table 1 input context and method/constants context records. The paper uses the Table 1 phase concentrations together with the exchange constants and liquid-metal activity-coefficient inputs; no excluded paper or figure row is used as a parent.

All three coefficients apply to one silica-saturated composition at 1,923 K. The associated experiment was at 1.5 GPa and 1,650 °C, using a starting recipe of Diopside28–Forsterite22–Anorthite50. Table 1 reports analyzed major-oxide values `59.4`, `10.7`, `11.3`, `10.4`, and `7.2`, with total `99.0`, but prints no unit or composition basis for these values. They are preserved verbatim as inputs; no wt% assumption or mole conversion is made. The paper gives no BW1102 oxygen-fugacity value or control. It says pressure effects on the exchange reactions are small enough to neglect, but the printed experimental pressure is retained. No wider temperature, composition, or fO2 validity range is stated for the three coefficients.

The run is linked through the single experiment ID `wood_wade_2013_bw1102` and Table 1 identifier `BW1102`. The capsule is printed as SiO2 glass; the remaining procedures are referred to Tuff et al. (2011). A quench condition is not stated in this paper, so no measured-glass row is created; the reported coefficients are for the high-temperature silicate liquid at 1,923 K. There is no carrier-gas or flow condition for this high-pressure partitioning experiment.

No separate binary supplement is available for this source; no supplementary dataset was used.

## Other printed and figure-only quantities

Table 1's gamma FeO value 0.92 is calculated from Eq. 4 and retained as a partitioning input, not treated as a trace-oxide output. The table reports Ag metal and silicate concentrations but no Ag melt activity coefficient. The liquid-Fe gamma inputs for Pb, Cu, and In are retained in the method context and are not silicate-melt outputs.

The authors quote approximately 4.2 at 1,573 K and approximately 3.2 at 1,923 K from a graph by Holzheid and Lodders (2001) as a CuO0.5 comparator. These are figure-read candidates, not values derived from this paper's BW1102 partitioning experiment, and are not extracted. Figures 1–4 contain activity-coefficient curves/contours for FeO, NiO, MoO2, and WO3; their numerical values are figure-only and were not digitized.

Figure 5 plots `Pi(gas)/Xi(melt)` for Pb, Cu, and In at 1,600 K. The caption defines this as the sum of partial pressures of gas species containing element i divided by the melt mole fraction of its oxide. The paper states that the curves assume completely dry, sulphur-free conditions. It does not tabulate numeric volatility or vapor-pressure values, so no volatility observation is extracted; the plot and definition are retained as context.

## Engine readiness

The target quantity is `activity_coefficient`, on the `melt_activity` rail (`simulator/battery/enums.py::MELT_ACTIVITY_QUANTITIES`, `simulator/battery/score.py::rail_for_quantity`). The prediction consumer is `simulator.chemistry.melt_activity.melt_oxide_activity`. For these fixed-valence oxides the identity profile does not compare fO2, while pressure is required because 1.5 GPa exceeds the activity pressure limit (`simulator/battery/identity.py::_melt_activity_uncompared_axes`).

| Observable | Rows | Engine-ready | Missing inputs | Other required axes retained |
| --- | ---: | ---: | --- | --- |
| Melt activity coefficient | 3 | 0 | `identity.composition` and `identity.fO2_Pa` for 3/3 rows: Table 1 major-oxide basis and the initial bulk recipe basis/sum are absent; without a normalized composition the profile cannot establish fixed valence, so fO2 remains required, and the paper does not print it | `temperature_K=1923`, point condition `T_C=1650`, `total_pressure_Pa=1500000000`, and typed pure-liquid reference state reach each row |

## Isolated migration

The focused test runs the isolated migration and pins all three values, quantity/species/phase identities, 1650 °C run condition, 1923 K reporting condition, 1.5 GPa experimental pressure, printed reference state, unknown fO2/composition, and the experiment FK. Migration yields 3 observations, 0 hard issues, and 0 unavailable values; all three printed coefficients remain numeric observations. The engine cannot score them as ready: `identity.composition` is unknown because the source prints no composition basis, and with composition unknown the activity profile requires the also-unprinted fO2 value. There are zero quantity-unknown and zero phase-unknown observations. Migrated output uncertainty counts are kind `printed`: 3 and confidence `not_present`: 3; each record carries the note that no coefficient uncertainty width is printed. The linked Table 1 context retains the input-analysis parenthetical widths and their printed `one_sigma` interpretation. The expected generated-store delta is one source work record, one experiment, three linked context records, and three numeric activity-coefficient observations; no generated battery store is committed.

## Verification results

The required remote gate ran `tests/test_wood_wade_2013_trace_activities.py`, `tests/test_yaml_flow_scalar_commas.py`, and `tests/test_literature_extracts.py` on the Studio runner. It reported 955 passed and 3 failed in 2,643.55 seconds, with no collection or import errors. The extraction test and YAML comma guard passed. The three failures were `test_repo_extracts_validate_green`, `test_coverage_pending_stubs_not_found`, and `test_pilot_extract_count_exact_and_merge_smoke`; each reported `wall-clock hang guard exceeded (1800.0s)`. The wrapper reported no local fallback. The remote wrapper rejected the focused node-ID command, so the isolated migration node was run locally under the specified environment and passed (1 passed). That test also mutates the pinned Pb coefficient from 0.22 to 0.23 and confirms the independent source-value assertion fails.

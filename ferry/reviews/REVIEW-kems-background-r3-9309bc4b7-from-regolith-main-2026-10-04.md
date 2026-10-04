engines/engines.local.toml is ABSENT in the review worktree and the G clone. With the supplied Python, ThermoEngine, openimcc and internal-analytical resolve at both revisions. VapoRock fails warm-pool initialization at both revisions with the same slot-0 hard timeout of 60 seconds; a standalone file-based retry with writable MPLCONFIGDIR also times out. AlphaMELTS is unavailable (no usable installation), and MAGEMin is unavailable (binary missing or initialization failed). ThermoEngine uses its legacy path-based identity. These results do not establish behavior of unavailable engines.

Target: 9309bc4b71223c2596dab3fa189643a7e990fb95. G: 05b6309d30cfed84d5e2ecd0467106d5702ce628. Python: /Users/simonrowland/Repos/regolith-pyrolysis-simulator/.venv/bin/python; each process uses its matching tree as PYTHONPATH. openimcc resolves IMCC-SF04 0.1.0.dev0, pack v1.0.2, digest f2b479cd54e3c82704a5863fcc06836f72045375d9a8c7f8d2fad19e98f75d05. The supplied worktree remains at the requested commit with no tracked-file edits. All historical checkouts and code-only reverts occurred in disposable shared clones under /private/tmp. The steer mailbox and authoritative brief were read; no steer messages were present.

Committed content needs to change: YES
VERDICT ON COMMIT: FIX-FIRST

The two round-2 functional findings are fixed. One maintainability/layering finding fails the explicitly supplied D-062 landing criterion. It does not represent an observed scored regression.

**Finding requiring committed changes**

1. **P2, confidence 10/10: pressure admission is duplicated, with one copy in generator wiring.** `simulator/battery/waypoints.py:1402–1413`, in `_pressure_for_oxygen_derivation`, and `simulator/battery/generators/bench.py:125–136`, in `_prediction_pressure_waypoint`, independently implement the same selection: retain selected evidence unless it is Knudsen `printed_run_pressure`; then scan for the first route not tagged `printed_run_pressure`, otherwise return absent. After normalizing only `experiment.method` versus `inputs.method`, their selection bodies have identical Python ASTs. `rg` finds both copies, including the identical `if route.route != "printed_run_pressure"` branch.

   `_knudsen_effusion_chamber_background_not_for_identity` has one definition, but sharing that token predicate does not share the route-selection logic. This is exactly the logic added in the latest oxygen fix. The author report's D-062 answer 1 therefore incorrectly says NO. The `88e2bc26d` commit body describes a helper shared among oxygen derivations; it neither names `_prediction_pressure_waypoint` as the other copy nor explains why two implementations should remain. Under the canonical checklist, YES to question 1 requires FIX-FIRST absent that named justification.

   The generator copy also independently enforces a source-pressure admission rule in a wiring module. The shared migration predicate only classifies the method; it does not own the pressure-selection decision. This answers YES to canonical question 2 as well as question 1. No numerical physics formula is newly computed there, but the question expressly includes a rule.

   Required change: put this existing route-selection decision in one private helper in the waypoint layer, using the existing WaypointResult and method state, and have these two real callers use it. The generator should consume that selection. Preserve raw `pressure_boundary` for validity/exterior consumers and preserve selection order, non-Knudsen behavior, and row-pressure evidence. Keep the seven existing pins and referencing tests green. This calls for reuse across two current callers, not another adapter, configuration option, or public API.

**Round-2 findings, one by one**

- **P1: FIXED-WITH-NEW-DEFECT.** `waypoints._pressure_for_oxygen_derivation` now filters the chamber waypoint before vacuum oxygen bounds, observation O2 mole fraction times total pressure, Frost buffer pressure, and graphite C–CO total-pressure fallback. `bench.activity_request_for_engine` consequently supplies no chamber-derived `fO2_log`. In an actual `score.predict_with_engine(Engine.OPENIMCC, ...)` FeO–SiO2 activity probe with only chamber vacuum 0.0001 Pa, G makes an engine call with `physical_pressure_bar=None` and `Po2Request(mode='commanded', po2_bar=1e-9)`; HEAD makes zero calls and refuses `identity_incomplete`, with an oxygen-condition `missing_evidence` gap including `total_pressure_Pa`. Source-grounded row oxygen remains eligible. The separately reported duplicated selector is the new D-062 defect; the functional oxygen route is closed.
- **P2: FIXED.** The shared `case()` now states observation sample pressure. The multicomponent readiness test does the same explicitly. Tests that intentionally derive oxygen from experiment pressure use Langmuir free evaporation and clear observation sample pressure. The original assertions in all four previously failing files are unchanged: AST comparison of every test's assert statements against G returns no assertion changes in test_waypoints.py, test_bench_generators.py, test_engine_intensive_charge.py or test_wave2_batch0_loader.py. Test results and individual fixture meanings appear below.

**Round-1 findings still hold**

1. **FIXED:** both observation-identity inheritance sites and the residue point-condition projection exclude Knudsen experiment chamber total. Engine-point readiness and payload construction use the filtered selection. The numeric fixture and row-pressure/non-Knudsen controls pass at HEAD; historical and code-only reverts fail. Ichise 1989's 280 chamber-pressure engine payloads disappear; Plante's 2,681 survive unchanged.
2. **FIXED:** Halwax's measurement-time background is unknown, with the printed pre-heating threshold retained as located startup context. The paper assessment below confirms this.
3. **FIXED:** pins discriminate code rather than merely the corrected Halwax extract. All three historical pin commits fail before their fixes. The HEAD oxygen-only revert and the complete G-code revert with HEAD tests/data also fail.
4. **FIXED:** the schema no longer prohibits independently stated in-cell pressure. Its current sentence specifically excludes experiment chamber total from identity, engine-point system pressure and sample oxygen derivations, including the vacuum bound. That sentence agrees with the repaired code.

**Diff and commit ordering**

`git diff --stat G HEAD`: 11 files, 827 insertions, 51 deletions. `git diff --check G HEAD` passes. All code, extract, documentation and test changes were read.

| File | Added / removed lines | Purpose |
| --- | ---: | --- |
| data/literature/extracts/SCHEMA.md | 1 / 1 | Precise chamber convention and excluded prediction routes |
| data/literature/extracts/kems-031-halwax-2024.yaml | 21 / 5 | Unknown measurement background; located startup evidence retained |
| data/literature/extracts/kems-169-nakazawa-1976.yaml | 5 / 5 | Separates pump-down, hot residual pressure and Figure 5 locators |
| simulator/battery/consumer_inputs.py | 6 / 1 | Carries experiment method to generator |
| simulator/battery/generators/bench.py | 49 / 6 | Engine-point pressure selection, readiness and payload |
| simulator/battery/migrate.py | 52 / 16 | Shared method predicate; two identity fills and residue projection |
| simulator/battery/waypoints.py | 37 / 6 | Four oxygen derivations filter chamber evidence |
| tests/battery/test_b556_missing_evaluated.py | 9 / 5 | Accurate Knudsen sample-pressure gap text |
| tests/battery/test_bench_generators.py | 48 / 2 | Explicit sample pressure; non-Knudsen oxygen controls |
| tests/battery/test_kems_background_not_identity.py | 567 / 0 | Seven deterministic pins and controls |
| tests/battery/test_waypoints.py | 32 / 4 | Physical pressure fixtures; original assertions retained |

test_wave2_batch0_loader.py and test_engine_intensive_charge.py themselves are unchanged; their tests consume the repaired shared `case()` fixture. No skip, xfail, baseline entry or relaxed guard is added. The removed b556 `total_pressure_Pa not in missing` assertion is replaced by an exact missing tuple containing that axis; the deleted redundant assertion is implied by the old exact tuple and the new intended condition is asserted explicitly. It does not reduce coverage.

Fix parents are their tests-only pin commits: `da8a8a740` has parent `2f2750c8d`; `0aac526ae` has parent `c69f3f9c6`; `88e2bc26d` has parent `96ced669f`. The final merge changes none of simulator/battery/, the affected extracts/schema, or tests/battery/ relative to `88e2bc26d`. G and HEAD both include the AlphaMELTS/grid identity work from `05b6309d3`; comparisons do not attribute that upstream engine work to this repair.

| Independent pin/revert check | Result | Evidence |
| --- | --- | --- |
| 2f2750c8d, before da8a8a740 | 1 failed, 2 passed | Halwax inherits 0.00100 Pa |
| c69f3f9c6, before 0aac526ae | 2 failed, 3 passed | Residue point total remains 0.0001 Pa; seven chamber engine payloads |
| 96ced669f, before 88e2bc26d | 1 failed, 6 passed | Chamber reaches vacuum_total_pressure_upper_bound |
| HEAD with only waypoints.py restored to 96ced669f | 1 failed, 6 passed | The oxygen pin fails on the chamber vacuum route |
| HEAD tests/data with G migrate.py, consumer_inputs.py, bench.py and waypoints.py | 3 failed, 4 passed | Primary identity inherits chamber; engine-point and oxygen pins fail |
| Restored HEAD in disposable clone | 7 passed | All pins and controls |

An attempted three-file identity/system revert while keeping HEAD waypoints.py could not collect because the latter imports the absent G predicate. It is not counted as behavioral red evidence; restoring all four dependent code files produces the three genuine failures above.

**Complete pressure-reader inventory and route assessment**

The inventory uses worktree `rg` over every `total_pressure_Pa`, `pressure_environment` and `pressure_boundary` reference in simulator/. The G→HEAD entries below describe inputs and behavior for a closed-token Knudsen experiment. Other methods retain the original code path; the whole-store identity comparison independently verifies that preservation.

| Reader / downstream consumer | Knudsen at G → HEAD | Non-Knudsen |
| --- | --- | --- |
| migrate.py:1337 identity decoder; :1877 pressure-environment decoder; records.py:984–993 typed pressure construction | Decode supplied values at both revisions; Halwax's supplied experiment state changes from point to unknown | Unchanged |
| migrate.py:6423–6501 source-row total / partial sums / congruent-vaporization total | Source-grounded in-cell pressure remains; no chamber dependency | Unchanged |
| migrate.py:6691–6704, :11898, :13043 identity-to-partial-point projection | Projects surviving identity pressure; upstream inherited chamber pressure disappears | Unchanged |
| migrate.py:8978–8994 merge; :9184–9252 legacy lab evidence; :10360–10366 registry/unknown handling | Keep chamber evidence, except Halwax's corrected measurement state; these do not predict | Unchanged |
| migrate.py:9734–9758 point_lab_conditions | Reads independent series/sample-matched raw evidence rather than the experiment record; no new experiment lift | Unchanged |
| migrate.py:10538–10575 _experiment_identity_fields | Exact chamber point can fill absent identity at G; shared Knudsen predicate blocks it at HEAD | Existing experiment and sweep-pressure inheritance retained |
| migrate.py:10583–10618 C–CO oxygen-condition-to-identity lift | G's total-P C–CO oxygen could reach identity.fO2_Pa; HEAD's waypoint filter blocks that chamber input, while stated CO partial pressure remains | Existing derived-oxygen lift retained |
| migrate.py:11670–11714 explicit/transition pressure bases | Independently stated pressure survives | Unchanged |
| migrate.py:12800–12921 residue identity fill | Chamber point becomes identity total at G; unknown at HEAD | Existing run-pressure identity fill retained |
| migrate.py:12930–12950 same-work fallback and residue point conditions | Fallback evidence remains discoverable; chamber copying into point conditions is blocked at HEAD | Existing fallback/copy retained |
| validity.py:657 effusion_regime_unverified; :1124 background_pressure_high | Read chamber bounds at both revisions; all 2,061 applicable row gate records, including details, are identical | Existing method/quantity scoping retained |
| validity.py:1081 in-cell-total fallback | Reads observation point conditions; source-grounded in-cell evidence survives | Unchanged |
| validate.py:1033–1036, _reconcile_identity_axis | Reads identity total and point total, falling back to experiment total for consistency checks; no prediction input is emitted | Unchanged logic |
| identity.py:421, profiles :478–730, uncompared axes :777/:804, comparisons :831 | Reads surviving identity state for construction/comparison; formerly inherited chamber values are absent | Unchanged logic and all non-Knudsen identity pressures |
| score.py:2413 missing-input reporting; :2665 candidate identity completion | Reads row identity or existing scorer assumption; never reads experiment total directly | Unchanged |
| score.py:2706–2737 total_pressure_bar_for_score; engine calls :3173/:3417 | Identity pressure / 1e5, otherwise existing 1e-6 bar assumption. Halwax helper would switch 1e-8 to 1e-6 bar, but its live scoring refuses before engine calls at both revisions | Unchanged |
| waypoints.py:1047–1080 pressure_boundary | Raw chamber remains as printed_run_pressure; row total and independent Q/S routes remain available | Unchanged |
| consumer_inputs.py:131–132; recursive evidence index | Collects point/run raw pressure waypoints, method and Located evidence; forwarding itself makes no prediction | Unchanged evidence collection |
| bench.py:116–136, :74–98, :389–403 engine-point selection/readiness/pressure payload | G uses chamber-selected total; HEAD excludes it and uses another eligible route or a gap | Original selection and point/bound rules retained |
| waypoints.py:1371, :1470, :1541, :1566 oxygen derivations and :1635 gap reporting | G permits chamber total as sample P; HEAD excludes it before C–CO fallback, x_O2*P, Frost P and vacuum-bound oxygen | Original oxygen values and missing-input logic retained |
| bench.py:618 melt-activity payload → score.py:3202 commanded Po2Request | G can command oxygen from chamber vacuum; HEAD refuses that missing oxygen evidence before any engine call | Non-Knudsen vacuum and independently stated row oxygen remain usable |
| waypoints.py:1821–1890 _orifice_knudsen → :1980–2035 _consumer_constraints | Raw run chamber still feeds mean-free-path/Kn consistency and KEMS readiness notices; it is not engine prediction pressure | Existing readiness behavior retained |
| bench.py:693–720 KEMS exterior_chamber_pressure; :772 RPS chamber schedule | Raw chamber evidence remains eligible for exterior transport/run scheduling | Unchanged pressure wiring |
| residue.py:999/1235/1449 Hashimoto and :1685/1818/1961 Sossi raw-extract pressure reads | Source-specific non-Knudsen cohort paths; not called for the Knudsen sources | Langmuir run pressure behavior retained |
| generators/janaf.py:1768/:2288 transition identities | Independent generated/source transition pressure; no experiment chamber lift | Unchanged |
| core.py, evaporation.py, capacity_coupling.py, vapour_rail/{request,catalog,melt_activity_resolver}.py | Process-state pressure, gas partial sums, vessel ratings and catalog requests; not readers of Experiment.pressure_environment or observation identity | Unchanged |

No route remains from experiment.total_pressure_Pa to Knudsen prediction system or commanded oxygen pressure in the audited functions. The independent Q/S route is not derived from that field and is unchanged; the prohibition is not widened to unrelated pressure evidence.

The author's table correctly describes its listed consumer behavior. It omits these actual readers/derived consumers: `validate_observation`'s identity/experiment consistency check; `_orifice_knudsen` and its KEMS consistency/readiness notice path; the C–CO oxygen waypoint lift back into `identity.fO2_Pa`; and identity's own constructor/profile/comparison machinery. The omissions do not reopen a prediction route, but the table is not an exhaustive reader inventory. Its claimed NO-copy D-062 conclusion is the actionable error identified above.

**Whole-store migration and engine-point effect**

The same script instantiated the real Migrator independently for every extract in both matching code trees, using index={} and aliases={} to isolate each extract, called _migrate_extract() and finalize(), and compared observations by ID. This is a migration comparison, not a claim that the unrelated strict whole-store validation profile is green. Both runs migrate 266 extracts, 13,392 observations, with zero migration errors. There are 3,145 Knudsen observations across 54 sources.

| Source / method | Identity pressures different | Change |
| --- | ---: | --- |
| kems-031-halwax-2024 / knudsen_effusion | 8 | G's inherited 0.001 Pa → absent/unknown at HEAD |
| Every other source/method | 0 | Identical |
| All non-Knudsen methods combined | 0 | Identical |

HEAD retains 384 valued Knudsen identities: 383 Plante 1979 values and one Ichise 1977 value. Every retained identity is identical to G. Plante's source rows supply P_K and the stated congruent-vaporization premise; migration derives in-cell P_total=(1+0.2262)*P_K, explicitly excluding chamber background. Ichise's author-estimate row explicitly supplies P_total_Torr=1.2, from P_Al2O=0.8 and P_Al=0.4 Torr, at 1500 C; the locator is published page 423/PDF 7. Its 159.9868421052631578947368421 Pa identity is therefore row evidence, not chamber inheritance. An independently stated in-cell total is not discarded.

Engine-point payload counts: Plante 2,681 at G and HEAD, with complete GeneratedInput equality; Ichise 1989 280 at G, zero at HEAD. Other current Knudsen sources emit no engine-point payload at either revision. Full GeneratedInput records differ for 320 observations: Bischof 2023 kems-137 (162), Ichise 1989 (85), Furukawa 1975 (31), Bischof kems-138 (29), Halwax (8), Nakazawa (5). These include refusal/gap changes, not 320 lost predictions.

Both gate functions were called with the same actual migrated rows and point-observation groups. Among 2,061 quantity-known Knudsen rows, background_pressure_high fails zero rows at both revisions; effusion_regime_unverified fails the same 597 rows. Every gate record and detail is equal. Halwax's quantity-unknown rows do not gain or lose a live quantity-scoped gate result.

**Four oxygen derivations, real-function controls**

`/private/tmp/rev-kems-background-r3-oxygen.py` calls the actual oxygen_condition with identical synthetic inputs at both revisions, varying only method and independently stated row pressure. Each derivation has a chamber-only Knudsen case, a Langmuir control and a Knudsen in-cell row-pressure control.

| Derivation / fixture | Knudsen chamber-only G → HEAD | Non-Knudsen G and HEAD | Knudsen stated sample P, G and HEAD |
| --- | --- | --- | --- |
| vacuum_total_pressure_upper_bound, 0.0001 Pa with during-run vacuum locator | log fO2=-9 → absent | Same route/value -9 | Same row-based route/value -9 |
| observation_gas_composition, x_O2=.2 and total 1e5 Pa | log fO2=log10(.2) → absent | Same route/value about -0.69897 | Same row-based value |
| buffer_relation, IW at 1000 K and 1e5 Pa | log fO2=-20.787 → absent | Same route/value -20.787 | Same row-based value |
| graphite_c_co_buffer, CO stated but no partial P, graphite-CO at 1373.15 K, total 1e5 Pa | log fO2=-11.564522096582184 → absent | Same route/value | Same row-based value |

Direct row `fO2_log` and stated sweep oxygen/CO partial-pressure routes retain their original precedence and remain allowed. The live activity scorer probe makes zero engine calls at HEAD for the chamber-only case. The non-Knudsen and printed-row-oxygen pin also verifies emitted melt payloads with -9 and -7 respectively.

**Scoring comparison**

The same script uses the real Migrator, ScoreContext, engine handles, score_store and residual_to_plain in matching G and HEAD trees. All 54 required Knudsen sources completed at both revisions, producing 10,692 residual records each. The saved source sets exactly match the migration census. The script's optional later Sossi/Hashimoto scoring controls were interrupted after the complete Knudsen snapshots were written; no result from those unfinished controls is claimed. Non-Knudsen preservation is established by the whole-store identity census, real oxygen-function controls and passing referencing tests above/below.

There are **162 score-eligible residuals at G and 162 at HEAD, with zero lost, zero gained, and complete residual-record equality for every scored row**. All belong to Plante/openimcc: 112 match and 50 mismatch. Plante produces 1,326 records at either revision (221 observation rows × six engines); its 162 openimcc numeric/scored rows and their residuals are identical. The remaining 59 openimcc rows retain the bulk-not-liquid-composition refusal. Plante's engine-point GeneratedInputs are independently identical as noted above.

Across all engines, 10,642 of the 10,692 complete records are identical. The 50 diagnostic differences all involve an engine_timeout on one side, wrapped as attempted_unavailable. No differing row is score-eligible on either side, and no pair of successful numeric results differs. The executions record 120 such timeouts at G versus 118 at HEAD; these are execution limits, not a claim of byte-identical diagnostic availability.

| Source | Different diagnostic records | Engines / reason |
| --- | ---: | --- |
| kems-042-plante-1979 | 2 | internal-analytical, G 5-second timeout versus HEAD numeric; both ineligible |
| kems-053-stolyarova-1991 | 19 | 17 internal-analytical / 2 openimcc; 5-/15-second timeout versus numeric or unmatched-species refusal |
| shornikov-1997-cao-alumina-vapor | 11 | internal-analytical; 5-second timeout versus numeric |
| stolyarova-1995-cao-alumina-kems | 12 | 11 internal-analytical / 1 openimcc; timeout versus unmatched-species refusal or numeric |
| stolyarova-1996-cao-alumina-silica-kems | 6 | internal-analytical; 5-second timeout versus numeric, three in each direction |

The two Plante diagnostics are plante1979_table2_s1104_r021_quoted:T1640 and plante1979_table2_s1115_r022_quoted:T1411. A separate real predict_with_engine retry of both rows times out at five seconds at both G and HEAD, confirming that the initial one-sided outcome is not reliable regression evidence. None affects the 162 openimcc scored rows. Halwax retains exactly the same 24 identity_unknown refusals and zero engine calls/results at both revisions despite its eight corrected migration identities. There is no row that scored at G but not HEAD, or the reverse.

**Halwax, Nakazawa and the schema**

Read the supplied corpus PDF, including Halwax's experimental page 825 and all vacuum/mbar/Torr occurrences. The paper puts the below-10^-5-mbar threshold before the start of heating, then describes heating to about 775 K, electron bombardment and later high-temperature measurements. Its general ultra-high-vacuum description supplies no numerical measurement-time chamber background. The printed during-experiment 5×10^-4 Torr passage concerns Gourishankar's earlier free-evaporation apparatus, not Halwax's own KEMS runs. Assigning that pressure here would be wrong.

Unknown/not_published measurement-time background is correct. The edit preserves the original startup threshold, its <0.001 Pa conversion, pre-heating timing, page/section locator and quote. Removing the unqualified numeric chamber field prevents its misclassification as run evidence; no stated threshold is lost.

Nakazawa's changed evidence distinguishes about 10^-4 Pa pump-down before heater energization on page 528/PDF 3, hot residual pressure in the 10^-5 Pa range, and the Figure 5 example about 3×10^-5 Pa on page 529/PDF 4. The quoted heater-start instruction is not reinterpreted as the hot residual pressure. The edit changes locators/context, not a numeric experiment background.

SCHEMA.md:408 agrees with the current code: the closed Knudsen experiment total is chamber background retained for validity, excluded from identity and both prediction pressure frames. Observation in-cell pressure and oxygen remain usable, as the real controls and Plante census demonstrate.

**The sixteen previously red tests, individually**

In the table, sample P means an explicit observation `point_conditions.total_pressure_Pa` in a Knudsen fixture; experiment P remains chamber background. Each original test assertion is retained verbatim in AST terms. The physical restatement repairs the unrelated test setup without weakening its original purpose.

| File / exact test ID (tests/battery/) | Physical fixture now stated | Original assertion retained |
| --- | --- | --- |
| test_waypoints.py::test_multicomponent_engine_charge_is_not_structural_failure | Explicit observation sample P=0.1 Pa | Aggregate engine readiness, engine list and multicomponent charge not structurally vetoed |
| test_waypoints.py::test_readiness_report_passes_modelling_inputs_to_consumer_path | Printed arm has sample P; vacuum arm is Langmuir, with sample P cleared | CLI/readiness routes receive modelling inputs and preserve reported statuses |
| test_wave2_batch0_loader.py::test_feot_maps_to_feo_with_notice_and_printed_map_keeps_feot | Shared case sample P=1 Pa | FeOT→FeO numeric mapping, retained printed FeOT, notice/inference and seven populated payloads/provenance |
| test_wave2_batch0_loader.py::test_trace_non_oxides_are_omitted_and_named | Shared case sample P=1 Pa | S/Cl omissions named; printed map retained; normalized oxide ratios and payload notices |
| test_wave2_batch0_loader.py::test_amount_basis_is_enforced_before_mass_conversion | Shared case sample P=1 Pa | Mole fractions retained, invalid/unknown bases refused, seven refusals or payloads as appropriate |
| test_engine_intensive_charge.py::test_massless_normalized_composition[mole_fraction] | Sample P=1 Pa, no absolute mass | .25/.75 normalized composition, derived authority, no mass input, no charges, populated intensive payloads |
| test_engine_intensive_charge.py::test_massless_normalized_composition[mol_inventory] | Sample P=1 Pa, stated mole inventory | Same ratios/authority, inventory charges present, populated intensive payloads |
| test_engine_intensive_charge.py::test_massless_wt_percent_derivation | Sample P=1 Pa, no mass | Approximately .5/.5 derived composition, no absolute charges, payloads retained |
| test_engine_intensive_charge.py::test_payload_scale_invariance | Sample P=1 Pa, scaled printed recipe and unknown mass | Payload equality for scale 1 versus 10; normalized mole sum 1 |
| test_engine_intensive_charge.py::test_printed_mole_fraction_precedes_wt_derivation | Sample P=1 Pa with explicit mole fractions | .25/.75 precedence and exact emitted composition |
| test_engine_intensive_charge.py::test_losing_single_species_charge_does_not_veto_intensive | Sample P=1 Pa, only MgO absolute charge survives | Both MgO/SiO2 remain normalized; ready payload; no single-species veto |
| test_bench_generators.py::test_vacuum_total_pressure_supplies_flagged_oxygen_bound_to_engine | Langmuir, chamber/run vacuum 1e-4 Pa, no row sample P | Oxygen extrapolation/notice, readiness, bound pressure and emitted -9 oxygen unchanged |
| test_bench_generators.py::test_engine_inputs_active_set_and_provenance | Knudsen sample P=1 Pa | Seven engines, 1126.85 C, 1e-5 bar, -9 fO2, bench and temperature provenance |
| test_bench_generators.py::test_sossi_measured_compositions_replace_recipe_and_preserve_recipe_notice | Shared case explicitly has sample P | 36 measured compositions, recipe versus measured lineage, species and notice/provenance checks |
| test_bench_generators.py::test_production_wt_pct_relation_reaches_engine_notice | Shared case sample P | Calculated class, printed oxide wt% relation and exact notice/provenance agreement |
| test_bench_generators.py::test_cli_generates_schema_inputs_with_numeric_json[engine_point] | Shared case sample P | CLI succeeds, generated count remains len(ENGINE_POINT_CONSUMERS)=7, numeric JSON temperature |

**Referencing tests and classification against G**

Worktree rg selected every test file referring to the edited pressure/oxygen/input symbols, including the additional test_internal_analytical_battery_engine.py, test_printed_fo2.py, test_score.py and test_migrate_r7.py that were not in the author's shorter core list. The previous broad regression and import-boundary files were included as well. Runs use the supplied Python, matching PYTHONPATH and -p no:cacheprovider, with normal project timeouts. They use -n0 except the standalone migration-r7 rerun, which uses four pytest workers per revision. Each failure is compared by exact test ID to G.

| File (tests/battery/ unless indicated) | G | HEAD |
| --- | ---: | ---: |
| test_waypoints.py | 73 passed | 73 passed |
| test_wave2_batch0_loader.py | 23 passed | 23 passed |
| test_engine_intensive_charge.py | 15 passed | 15 passed |
| test_melt_activity_requirements.py | 65 passed | 65 passed |
| test_converted_provenance.py | 15 passed | 15 passed |
| test_schema_admission.py | 423 passed | 423 passed |
| test_observation_waypoints.py | 7 passed | 7 passed |
| test_t955_janaf_engine_point_na.py | 6 passed | 6 passed |
| test_bench_generators.py | 59 passed | 59 passed |
| test_b556_missing_evaluated.py | 16 passed | 16 passed |
| test_furukawa_bench.py | 1 passed | 1 passed |
| tests/test_import_boundary.py | 14 passed | 14 passed |
| test_kems_background_not_identity.py | Absent at G; historical red-before evidence above | 7 passed |
| test_sweep_gas.py | 45 passed | 45 passed |

These completed files total 762 passing tests at G and 769 at HEAD. All sixteen original core test cases pass, with their original assertions preserved. The additional sweep-gas file exercises explicit Knudsen chamber evidence and Sossi's non-Knudsen inheritance controls.

| Additional referencing file | G | HEAD |
| --- | ---: | ---: |
| test_internal_analytical_battery_engine.py | 4 passed, 1 failed | 4 passed, 1 failed |
| test_printed_fo2.py | 13 passed | 13 passed |
| test_score.py | 175 passed | 175 passed |

The single internal-analytical failure is test_internal_analytical_observation_uses_core_vapor_pressure_intent: prediction.value is None, failing the same assertion at line 75 at G and HEAD. It is pre-existing, not introduced by this diff. These three files completed all 193 collected cases before their combined run entered the 2,254-case migration-r7 file. With them, 954 tests pass at G and 961 at HEAD, plus the same one existing failure; migration-r7 is accounted for separately below.

| Migration referencing file | G | HEAD |
| --- | ---: | ---: |
| test_migrate_r7.py, all 2,254 collected cases | 2,250 passed, 4 failed | 2,250 passed, 4 failed |

The complete four-worker runs fail the exact same four test IDs, all at the unchanged 300-second timeout: test_g1_whole_store_absence_claims_match_sources[janaf-B.yaml], [janaf-Cl.yaml], [janaf-F.yaml], and [robie-hemingway-fisher-1978-usgs-b1452-0003.yaml]. They time out while loading/traversing compilation data in absence_audit, not in a changed pressure function. The observations-v2 stores and raw compilations they read have no G→HEAD diff. The earlier serial run encountered the same four plus timeouts on janaf-Al, janaf-Ba, janaf-Br and janaf-C at both revisions before being interrupted to restart this file in parallel. Its partial migration counts are not added to the completed-run totals.

Every referencing file has now completed. Across the selected files, the unique completed totals are **G: 3,204 passed, 5 failed; HEAD: 3,211 passed, 5 failed**. The seven additional HEAD passes are the new pins. All five HEAD failure IDs also fail at G; there is no new regression-test failure, skip, xfail or baseline entry. These results are not described as an entirely green environment.

Supplementary tests/chemistry/test_extract_store_reproduction.py is not a changed-symbol referencing file. Its original combined run at HEAD aborted inside pytest traceback formatting when the normal 300-second timeout fired; the G combined run was stopped after its core files had completed. A paired short-traceback retry was stopped after both revisions reached the same 86 passes, 6 failures, 2 setup errors and 1 existing skip. The six failures are pre-existing whole-store extract-validation errors: test_wetzel_stale_duplicate_hits_system_class_and_form_gate, test_measured_rate_series_executes_hkl, test_numeric_activity_executes_melt_activity_model, test_engine_value_mutation_moves_residual_outside_band_goes_red, test_hkl_assumption_diagnostic_is_not_promoted_or_pinned, and test_recovered_gibbs_evidence_is_covered_but_never_pin_bearing[stolyarova_1992_binary_wilson_model_parameters_table2-typed-refusal:model_output_not_measurement]. The same two fixture setup timeouts affect test_store_yields_adopted_target_type_observations and test_pinned_residuals_cover_all_live_comparable_points. The existing no-observation skip is test_adopted_observation_reproduction[store-load-failed]; its long generated parameter differs only in code-tree paths. No new skip or guard change was used. This supplementary suite is not claimed fully completed or green.

**D-062 REVIEWER CHECKLIST — canonical five answers**

1. **YES.** `_pressure_for_oxygen_derivation` and `_prediction_pressure_waypoint` contain identical chamber-exclusion and alternative-selection bodies after only method-expression normalization. Worktree/repository rg and AST comparison confirm both definitions. The shared method predicate has one home, but route filtering has two. The fix commit does not name and justify that copy. This is finding 1 and mandates FIX-FIRST under the supplied criterion.
2. **YES.** The generator's _prediction_pressure_waypoint independently applies the rule that Knudsen printed_run_pressure is inadmissible for prediction, then chooses replacement evidence. That source-pressure admission decision is in generator wiring, even though the method predicate is in migrate.py. No new numerical threshold/formula is computed there; the canonical question also covers a rule. This is the same finding as question 1, resolved by the same shared waypoint-layer selector.
3. **NO.** Both new imports reuse existing edges to migrate.py: bench.py already imported to_plain, and waypoints.py already imported _OXIDE_COMPONENT_KEYS. The pre-existing migrate→oxygen waypoint call is a local runtime import after modules load. No new forbidden edge/import-time cycle or baseline change appears; import-boundary checks are included in the test table.
4. **YES.** Tests-only pin commits precede all three code fixes; independent historical runs are red, HEAD is green, and dependent code-only reverts are red. No behavior-preserving code move is made in this diff.
5. **NO.** No guard or physics threshold is relaxed, no baseline entry is added, and no code is moved to a module with already-red guards. The four formerly failing files preserve their original assertions. The gap-text changes explicitly report the newly missing Knudsen sample pressure. Existing broad failures are classified against G in the test table rather than hidden or waived.

**Evidence and exact landing requirement**

Reproducible scripts: `/private/tmp/rev-kems-background-r3-probe.py`, `rev-kems-background-r3-score.py`, `rev-kems-background-r3-compare.py`, `rev-kems-background-r3-oxygen.py`, `rev-kems-background-r3-summary.py`, `rev-kems-background-r3-engine-retry.py`, and `rev-kems-background-r3-targeted.py`. Matching-code migration/score snapshots use `/private/tmp/rev-kems-background-r3-{green,head}-{migration,score}.json`. Historical pin, oxygen-only revert, complete G-code revert, restored HEAD and referencing-suite JUnit files use the same prefix; the completed migration file is recorded in `rev-kems-background-r3-{green,head}-r7.xml`. Disposable clone paths are `/private/tmp/rev-kems-background-r3-green` and `/private/tmp/rev-kems-background-r3-history`.

Before landing, consolidate the duplicated pressure selector in the waypoint layer for its two existing callers, preserve all functional repairs, and keep the pins and relevant regression checks at least as green as the verified baseline. No additional physics mechanism, extract change, engine configuration or public API is requested by this review.

Committed content needs to change: YES
VERDICT ON COMMIT: FIX-FIRST

!COMPLETE: rev-kems-background-r3 — FIX-FIRST, 1 findings

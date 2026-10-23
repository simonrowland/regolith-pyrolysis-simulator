# nexttrain-asm-1023 assembly report

Branch `worktree/nexttrain-asm-1023`. Base `cb13f9f927527d41517b81a82c027466984cb633`. Assembly tip before this report `62b72776978c038d40702e8e131a9e0cbdfde144`. Family car `49fddc0e7`, car A, and car B are not in this tip. Nothing was pushed. No derived store, index, or ack file is in these commits.

## Manifest

Merged with `git merge --no-ff`, one member at a time. None was already an ancestor. No add/add on `data/literature/extracts/*.yaml`. No derived-file conflict, so no `--ours` checkout was required.

The brief sha `e39ca4fbc04d8134b64c26c0fd1823c392ded8e5` is not an object. The unique prefix `e39ca4fbc` is `e39ca4fbc3e3c679b07a7d274760a15486a623e0`, tip of `worktree/d255-fresh-1022` and `origin/review/d255-fresh`. That object was merged.

| order | member | resolved sha | merge commit |
|---|---|---|---|
| 1 | d255-fresh | `e39ca4fbc3e3c679b07a7d274760a15486a623e0` | `a7d1028ac` |
| 2 | t-1473 | `6e96ad16d9fbbe093c309a67d7c7cf95b341edf0` | `d8cc85351` |
| 3 | F51 | `ed5a9258f73bd3acaf49dd4a9889dec2d7556b3b` | `c9f44992f` |
| 3 | F52 | `66da403266f09ae58a7be4819e623dad5bf9e2e4` | `0a4239090` |
| 3 | F53 | `0b235103664f395e35341157209d6704a0bb97cb` | `f0f4e321d` |
| 3 | F54 | `c6ab3a759b3eae20a941e14f636b15acb14113ef` | `236498057` |
| 3 | F57 | `d557214bc88b1acf7c375c7e95e583f45d24c3ba` | `da9fe76b5` |
| 3 | F58 | `6686f902d3e11d4d8baced6d8107387a94549a36` | `462e95388` |
| 3 | F59 | `b8c8ed5845a9331327d02de7693befb38507a8c5` | `10c0c94ec` |
| 3 | F60 | `21de588e8c638b03079b2e53fd47948c20f70b9c` | `c587fd1c6` |
| 3 | F61 | `897877939ced929781a9e09f7d8e920a54b6db57` | `54b3999f1` |
| 3 | F62 | `36de8fce85a0510fcf7a69a89633f4203a91a156` | `b40368c01` |
| 4 | retype55 (contains F55 `635c9b53ce3da9daab07c03c4f08ff0c8bf2d262`) | `b67a1b90835bbb5af88dff1ef484994fc83de33e` | `04ff4ad97` |
| 5 | t-1482 (contains retype56 `4dec97e9881bb8d1827c77de3d11d2b17f8dbf11`, F56) | `d17567e477302edf691e503ac2d555e7f4f33d5f` | `a91ca83d3` |
| 6 | F63 | `f6ee95a3e024d6f5c20b9c07f9f64335a57b2261` | `f44e449ba` |
| 6 | F64 | `3b4e0795b3ecc776cde5b7a1fb46f07b9e42a238` | `513cd1e24` |
| 6 | F65 | `416677e41248abdec4bf6ec6d1d47e958f9532ce` | `0c21c4f8f` |
| 6 | F66 | `d5b6eccdfb1914e8c866b1103697df1320f452f4` | `94ccf7493` |
| 6 | F67 | `c343da7101553cd563b9e84671b9b82f57b7b410` | `f681ed5c7` |
| 7 | t-1485 | `57fa3226e09554103ed7fdee32b7dc5919d5405d` | `62b727769` |

## Conflicts and resolutions

Content conflicts were only in t-1473 (`simulator/battery/score.py`, `simulator/battery/migrate.py`). Every other member auto-merged. An added-line check of t-1473 against merge-base `2e651d55` found no dropped addition after the constant reinsert below.

`score.py` alignment. The gated set keeps `phase_role`, `counter_phase_role`, and green's `valence_basis`, and adds t-1473's `normalising_element`. The group-key loop uses `_PROFILE_GATED_ALIGNMENT_AXES`.

`score.py` `_axis_alignment_value`. A not-applicable state on an axis that is both required and permitted not-applicable stays in the group key. t-1473 removed `and not required` for `element_fraction_remaining` / `normalising_element`. Green had not changed that line after the common base. Residue fO2 was already dual-listed there.

`score.py` predictors. Green's `_predict_mass_loss_with_residue` stays. t-1473 adds the alpha-evidence catalog, evaporation-flux prediction, and element-fraction-remaining prediction. `_PREDICTED_RESIDUE_KEY` and `ALPHA_EVIDENCE_QUANTITIES` sit immediately before `alpha_evidence_record` (the first conflict extraction had started at the function and dropped them; they were put back). `predict_with_engine` dispatches mass-loss, then condensation coefficient, evaporation flux, and element fraction remaining, then the existing redox and phase paths. The quantity sets do not overlap.

`migrate.py` for t-1473. Green's valence mass-fraction helpers stay, then the t-1473 flow, selection, and identity helpers. `_selection_from_named_field` auto-merged the three quantity selections in front of activity and pressure.

retype55. Printed t-1473 compositions become `Located` values. `_composition_state_from_printed` was renamed to `_composition_located_from_plain`. `t1473_identity_kwargs` returns the located composition beside the identity kwargs. The old name has no remaining caller.

t-1482. A finite non-negative `values.RSD_percent_as_printed` is copied onto the observation uncertainty as basis `relative_sd`, value `RSD/100`. `ELEMENT_FRACTION_REMAINING` scores on an absolute metric. `relative_sd` is treated like `relative` and `fraction`: absolute sigma is the absolute reference times that fraction. SCHEMA drops the sentence that no seated extract prints `element_fraction_remaining`.

F64. The merge message only names the Lee oxygen condition. The hunks, with no content conflict, are: SCHEMA allows printed flux unit `μg cm^-2 s^-1` and states the migrator converts it with `1 μg cm^-2 s^-1 = 1e-5 kg m^-2 s^-1`. `_CONVERSION_META` key `ug_cm2_s_to_kg_m2_s` and `_flux_unit_spec` implement that scale. `mol m^-2 s^-1` and `kg m^-2 s^-1` stay factor 1 and trail `as_published`. `values.flow` is omitted when the source prints no complete flow block. `EVAPORATION_FLUX` joins the quantities that keep a printed unit trail. The Lee extract, audit, and tests are additions.

t-1485. No content conflict. An unprinted fusion-polymorph notice with no prediction still returns `METHOD_UNKNOWN` for internal-analytical when the experiment method is unknown, and now returns `IDENTITY_INCOMPLETE` for every other engine. The crystalline-sample invalid-source return below that is unchanged. `fusion_comparison_reference` still treats an unspecified printed solid polymorph as a missing comparison input; the JANAF import stays below that return.

d255. No content conflict. `--pre-gate` is added. r16 drops the offer-time stale assertion. r17 keeps it and says an offer may be stale before the train regen.

## Expected store delta

Regenerated in scratch worktrees only. Not committed. `battery_migrate.py` exits 1 whenever `hard_issues` is non-zero; both trees did, and the counts below are the printed headers.

| | base | tip | delta |
|---|---:|---:|---:|
| rows_in | 51641 | 52507 | +866 |
| observations | 128946 | 129984 | +1038 |
| works | 371 | 388 | +17 |
| experiments | 3863 | 3931 | +68 |
| queue | 88695 | 90127 | +1432 |
| hard_issues | 3278 | 3285 | +7 |

Hard-issue census: `conditional_field:derived_from` 2131 to 2137 (+6), `conditional_field:derivation` 1143 to 1144 (+1), `invalid_source:initial_composition` 4 unchanged. The first 50 hard-issue lines are the same observations on both reports, all sources that already existed on green. The seven new issues are outside that prefix. They were not listed observation by observation.

`build_index.py --write-store-summary` was run in both scratch trees (tip exit 0, base exit 0). `INDEX.md` and `INDEX.yaml` were not written.

Per-source rows / observations / queued. Only these 18 sources differ. New sources have no base row.

| source | base rows, obs, queued | tip rows, obs, queued | delta rows, obs, queued |
|---|---|---|---|
| baud-2003-sic-alumina-vaporization-condensation.yaml | — | 16, 16, 16 | new |
| conradt-scholze-1986-glass-volatilization.yaml | — | 39, 39, 78 | new |
| davis-1990-forsterite-evaporation-isotopes.yaml | — | 69, 69, 69 | new |
| gerasimov-2005-laser-impact-melts.yaml | — | 2, 2, 6 | new |
| gibson-1971-lunar-gas-release.yaml | — | 20, 25, 20 | new |
| gunther-1958-vapor-pressure-evaporation.yaml | — | 6, 40, 12 | new |
| kems-003-pound-1972.yaml | 18, 18, 65 | 18, 18, 61 | 0, 0, −4 |
| konta-1975-moldavite-oxide-volatility.yaml | — | 209, 209, 489 | new |
| lee-1976-chromium-oxide-vaporization.yaml | — | 16, 16, 0 | new |
| neuman-2021-k-cu-evaporation-isotope-fractionation.yaml | — | 63, 120, 143 | new |
| neumann-1954-liquid-k-evaporation-coefficient.yaml | — | 13, 17, 18 | new |
| ni-2021-laser-levitation-evaporation.yaml | — | 53, 53, 61 | new |
| norris-2016-trace-volatility-thesis.yaml | — | 240, 240, 304 | new |
| piacente-matousek-1973-sodium-disilicate-kems.yaml | — | 14, 86, 10 | new |
| sindland-2021-sio-production-quartz-silicon.yaml | — | 30, 30, 60 | new |
| takigawa-2015-corundum-kinetics.yaml | — | 18, 18, 36 | new |
| wetzel-pucci-gail-2012-sio-evaporation-coefficient.yaml | — | 36, 36, 48 | new |
| wilcox-rohsenow-1970-k-film-condensation.yaml | — | 22, 22, 66 | new |

The new-source rows sum to +866 and the observations sum to +1038. Their queued counts sum to +1436, and Pound's −4 leaves the header queue delta of +1432.

Pound's row and observation counts are unchanged. Four queued observations leave the unknown-quantity queue because t-1473 makes `condensation_coefficient` a real quantity. The removed reasons are the unsupported-quantity refusals for the Chupka Table 3 condensation coefficients (`pound_1972_w_condensation_alpha_chupka_2200k`, `pound_1972_pt_condensation_alpha_chupka_1500k`, `pound_1972_rh_condensation_alpha_chupka_1500k`, and the same W row). The rows are still in the store.

## Validation

Scratch worktrees of the tip and of base `cb13f9f92`, each after `battery_migrate.py` and `build_index.py --write-store-summary`. Pytest was split by file, `-n 0`, project timeout 300 s, venv Python, `PYTHONPATH` prepending `/Users/simonrowland/.cache/regolith-engine/oimcc-e975edd/src`. OpenIMCC import file is `/Users/simonrowland/.cache/regolith-engine/oimcc-e975edd/src/openimcc/__init__.py`. That tree is not a git checkout. Its `EXPORTED_COMMIT` file is `e975edd229b7653ceac5dbac9558d0156e51c2f6`. The module attribute of that name is absent.

Tip: 92 `tests/battery/test_*.py` plus `test_literature_extracts.py`, `test_absolute_path_ratchet.py`, `test_literature_index.py`, and `test_yaml_flow_scalar_commas.py` (96 files). Base: the same set without the three tip-only battery files `test_gunther_1958_extract.py`, `test_store_freshness.py`, and `test_t1473_quantities.py` (93 files). `tests/test_yaml_flow_scalar_commas.py` passed on both. `tests/battery/test_t1473_quantities.py` passed (17 tests at merge time, and the file again in this gate).

### New failures

`tests/battery/test_migrate.py::test_validate_corpus_zero_hard_issues_on_migrated_store`

`TypeError: cannot convert <class 'dict'> to Decimal` at `simulator/battery/records.py:103`.

`tests/battery/test_validity.py::test_full_store_validity_gates_do_not_raise[5]`

Same first error line.

Cause, reproduced by loading the regenerated tip store: Ni 2021 observation `ni-2021-laser-levitation-evaporation::ni_2021_cu_1_10_cu_residue::T=2273.15:row=cu-1.10:h=88f06a693f3e` stores `point_conditions.atmosphere.state.value` as a sweep record `{species: Ar, flow_sccm: unknown, partial_pressure_Pa: unknown}`. The writer copies `pressure_environment.sweep_gas` into `atmosphere` when the point has no printed atmosphere. The reader special-cases `atmosphere`, `gas_mix`, and `fO2_control` as a string or a decimal (`migrate.py` `_point_condition_from_plain`). That special case and the writer are both already on green. The regenerated base store has no `flow_sccm` value, so the green tests pass. Ni is the extract that takes the branch. Sindland stores the same sweep shape under `sweep_gas`, which has a loader, and is not this crash. The reader was not changed here: treating `atmosphere` as a `SweepIdentity` is a schema choice this assembly does not settle.

`tests/battery/test_migrate.py::test_j01_store_census_series_numeric_matches_declared_field`

`AssertionError: assert 1533 == 1518`

`assert not mismatches` had already passed, and the earlier census pins in that test passed. The +15 is Gibson 1971: fifteen `residue_component_composition` series cells (`residue_ppm`) in `gibson-1971-lunar-gas-release.yaml`. No other new extract adds cells of that token. The pin was left at 1518.

`tests/battery/test_migrate.py::test_l05c1_store_alpha_values_lie_in_unit_interval`

`AssertionError: ['neumann-1954-liquid-k-evaporation-coefficient::neumann_1954_k_alpha_table2::T=377.65:h=4b2e20e3e417 alpha=1.01']`

The extract's audit records printed Table 2 alpha `1.01` (104.5 °C row). The gate requires `0 < alpha <= 1`. The printed value was left as extracted.

`tests/test_absolute_path_ratchet.py::test_path_value_keys_match_the_extract_tree`

`AssertionError: path-carrying keys outside PATH_VALUE_KEYS: ['audit_path']`

The only such key is `extraction.audit_path` in `neuman-2021-k-cu-evaporation-isotope-fractionation.yaml`. It was not added to `PATH_VALUE_KEYS`.

`tests/test_literature_index.py::test_regenerated_index_matches_committed`

`assert {'counts': ...} == {'counts': ...}`

Count keys that differ: `extracts` 368 to 385, `extracts_without_pdf` 368 to 385, `sources` 370 to 387. That is the 17 new extracts, whose PDFs are not in the worktree. `INDEX.*` was not built in the seat. The test compares a live rebuild with the committed index.

### Expected freshness, and failures already on the base

`tests/battery/test_r17_glass_store_regen.py::test_committed_store_has_no_stale_migrate_input_commits` fails on the tip only:

`AssertionError: unacknowledged post-regen migrate inputs: {'7171b7401', ...}`

The set is this batch's migrate-input commits. d255 removed that assertion from r16 and kept it on r17, with the note that an offer may be stale before the train regen. Scratch regeneration does not move the last committed store-output commit, so the git-history check still fails. r16 passed on the tip. `test_store_freshness.py` passed. The r16 and r17 mutation tests in those files passed (r17's only failure is the stale-input assertion).

These fail on the tip and on the base, so they are not new:

`tests/battery/test_compilation_tier.py::test_vaporock_gas_shomate_values_are_janaf_fidelity_checks` (and the two sibling vaporock tests in that file): `NameError: ObjC Class b'LiquidMelts' couldn't be found.`

`tests/battery/test_redox_extract_scoring.py::test_openimcc_sossi_aithala_redox_residuals_remain_bit_identical`: `AssertionError: assert '121005818512...3eabe7a8bbc82' == 'c79971dce81a...d24c1627b8f44'`

## Left as reported

The Ni atmosphere round-trip, the j01 pin, the Neumann alpha gate, and the `audit_path` ratchet each need a judgement this assembly does not make. The index mismatch is the unbuilt `INDEX.*`.

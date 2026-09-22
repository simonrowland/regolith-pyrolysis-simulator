# S8 — tests that cannot fail

**Repo tip (write branch):** `empirical/reviews-2026-09-22` (main clone).  
**Code base audited:** `origin/review/janaf-batch-2026-09-22` (`a49cef012`) via worktree `/workspace/repos/wt/slot-08`. READ-ONLY on product tree; results only.  
**Scope:** `tests/battery/`, `tests/test_*backend*`, `tests/test_*vapour*` (start set from class brief).  
**Predicate:** a test whose assertion holds whatever the code under test does — fakes that ignore inputs, asserting a value against itself, over-broad `pytest.raises(Exception)`, assertions inside loops that never run / vacuous `all()`/`any()`, skipped-by-default tests guarding real behaviour, snapshot tests regenerated from the code under test.  
**Seed:** a “values unchanged” commissioning test whose fake engine ignored inputs — **fixed** on this tip as `tests/test_vaporock_backend.py:308` `test_vaporock_commissioning_notice_preserves_prediction_values` (composition-/T-dependent fake + A/B bare-notice equality; comment at :422–423 explicitly rejects the old tautology). Not scored below.

| site (file:line / test) | predicate match | surviving mutation | live? | severity |
|---|---|---|---|---|
| `tests/test_alphamelts_backend.py:119` `test_alphamelts_python_liquidus_finder_uses_findliq_gate` | `FakeFinderBackend._find_petthermotools_liquidus_C` ignores `comp_wt` / `pressure_bar` / `seed_T_C` (always `1300.0`). Asserted liquidus/solidus come from the T-linear `equilibrate` fake via fraction bisection, not from findLiq. No call counter / findLiq-value pin. | Delete the `python_api` findLiq call (or make findLiq return `9999.0`); leave fraction bisection — `liquidus_T_C≈1300` / `solidus_T_C≈1000` still green. | live | P1 |
| `tests/test_backends.py:138` `test_real_backend_out_of_domain_is_not_typed_backend_unavailable` | `pytest.raises(Exception)` with only a follow-up `reason_code` check. | Raise any `Exception` subclass carrying `reason_code="real_backend_out_of_domain"` (wrong type / hierarchy) — test stays green. | live | P2 |
| `tests/test_backends.py:195` `test_cached_real_config_errors_are_input_not_backend_unavailable` | Same over-broad `pytest.raises(Exception)` pattern (despite negative-control arms). | Raise a non-domain `Exception` with `reason_code="invalid_run_input"` — green. | live | P2 |
| `tests/test_vapour_rail_engine_crosscheck.py:118` `test_runner_hard_requires_vr5_warm_pool` | `pytest.raises(Exception, match="warm pool")` instead of `EngineCrosscheckError`. | Raise `RuntimeError("warm pool required")` (or any Exception matching) instead of the typed runner error — green. | live | P2 |
| `tests/test_magemin_backend.py:1443` `test_magemin_live_smoke_runs_real_binary` | `@pytest.mark.skipif(_LIVE_MAGEMIN_BINARY is None, …)` — on this tip binary locate is empty, so the only real-binary smoke for ledger_transition=None / silicate liquid never runs in default CI. | Break live smoke invariants (wrong liquid / non-None ledger_transition on real binary path) — CI never executes the guard. | live | P1 |
| `tests/test_magemin_backend.py:1503` `test_magemin_live_subliquidus_run_reports_crystalline_phases` | Same skipif; sole live crystalline-phase guard. | Ship a binary path that never reports crystals — skipped in default CI. | live | P1 |
| `tests/test_magemin_backend.py:1549` `test_magemin_live_liquidus_finder_lunar_mare_low_ti_sane` | Same skipif; sole live lunar-mare liquidus bracket guard. | Drift liquidus finder outside the sane bracket on real binary — skipped. | live | P1 |
| `tests/test_magemin_backend.py:1717` `test_magemin_live_buffer_n_sign_and_magnitude_round_trip` | Same skipif; **only** live check that `--buffer_n` sign/magnitude matches QFM translation (GAM[O]). Offline fakes do not exercise the real binary formula. | Invert `buffer_n = fO2_log - QFM(T)` (or ignore magnitude) in the live adapter — default CI never runs the round-trip. | live | P1 |
| `tests/test_magemin_backend.py:1761` `test_magemin_live_adapter_path_fO2_changes_shadow_response` | Same skipif; sole live fO2→shadow response probe on adapter path. | Stop forwarding fO2 into live MAGEMin shadow — skipped. | live | P1 |
| `tests/test_magemin_backend.py:2012` `test_magemin_live_subprocess_does_not_append_dump_in_engine_tree` | Same skipif; sole live in-tree dump growth guard. | Re-enable `_pseudosection_output.txt` append in engine tree — skipped. | live | P1 |
| `tests/test_alphamelts_backend.py:7581` `test_project_local_alphamelts_reports_liquidus_when_installed` | Runtime `pytest.skip` when project-local alphaMELTS app missing — default CI without the app never guards live liquidus reporting. | Break live liquidus reporting in the installed-app path — skipped by default. | live | P2 |
| `tests/test_alphamelts_backend.py:7613` `test_project_local_alphamelts_populates_full_table_suite_when_installed` | Same skip-by-default for full table suite. | Drop table population on live app path — skipped. | live | P2 |
| `tests/test_alphamelts_backend.py:7648` `test_project_local_alphamelts_cold_c0_step_returns_when_installed` | Same skip-by-default for cold C0 step. | Fail cold C0 live step — skipped. | live | P2 |
| `tests/test_alphamelts_backend.py:7689` `test_live_alphamelts_non_tenth_T_roundtrips_without_isothermal_refusal` | Same skip-by-default for non-tenth-T roundtrip. | Reintroduce isothermal refusal on non-tenth T — skipped. | live | P2 |
| `tests/test_alphamelts_backend.py:6410` `test_thermoengine_transport_equilibrates_live_when_installed` | Runtime skip when ThermoEngine transport unavailable — live equilibrate guard absent in default CI. | Break live ThermoEngine equilibrate — skipped. | live | P2 |
| `tests/test_alphamelts_backend.py:6454` `test_thermoengine_live_fo2_near_spinel_boundary_is_unique_or_fails_loud` | Same skip-by-default for spinel-boundary fO2 uniqueness. | Accept non-unique fO2 near spinel silently — skipped. | live | P2 |
| `tests/test_alphamelts_backend.py:6565` `test_thermoengine_absolute_fo2_shadow_parity_against_subprocess_when_available` | Uses `_initialize_thermoengine_for_parity()` which skips when TE missing — live TE↔subprocess fO2 parity unguarded by default. | Drift absolute fO2 shadow vs subprocess — skipped. | live | P2 |
| `tests/test_vaporock_backend.py:742` `test_vaporock_shadow_parity_with_builtin_antoine_for_basalt` | `pytest.skip` when optional `vaporock` dep unavailable — live shadow↔Antoine parity unguarded by default. | Break live VapoRock vs Antoine basalt parity — skipped. | live | P2 |
| `tests/test_vaporock_backend.py:953` `test_vaporock_iw_literature_grid_residuals_are_explicit` | Same optional-dep skip for IW literature residual grid. | Hide / drop IW residual disclosure on live path — skipped. | live | P2 |
| `tests/test_vaporock_backend.py:1935` `test_warm_pool_owns_system_lifecycle_live` | `importorskip("vaporock")` + skip on warm-pool start failure — live warm-pool lifecycle unguarded by default. | Close / leak warm-pool systems incorrectly on live path — skipped. | live | P2 |
| `tests/test_vapour_rail_kinetics_anchors.py:191` `test_fedkin_k_alpha_is_langmuir_not_kems` | `assert all(r.species != "K" for r in kems)` and Fedkin-not-in-KEMS `all(...)` with **no** `assert kems` nonempty (unlike sibling test at :34). Vacuous `all([])` is True. | Make `load_kems_anchors()` return `[]` (or empty the KEMS sidecar) while leaving the Langmuir Fedkin row — both `all(...)` asserts stay green; only `len(fedkin)==1` still bites the Langmuir side. | live | P3 |
| `tests/test_vapour_rail_sf04_high_t.py:925` `test_snapshot_boundary_claim_is_explicitly_scoped` | Guards the snapshot **claim** only via `__doc__` + decision markdown string contains; does not exercise abort/read-only snapshot behaviour. | Keep the docstring/decision wording, remove or no-op the runtime snapshot abort in `evaluate_vaporock` — **this** test still passes (sibling behaviour tests may still catch; this row is the claim-only vacuity). | live | P3 |

## Counts

- Sites: 22  
- Live: 22  
- P0: 0 · P1: 7 · P2: 13 · P3: 2  

## No-hit areas (audited; not scored)

- **Seed fixed:** `test_vaporock_commissioning_notice_preserves_prediction_values` — fake depends on composition/T; A/B bare-notice equality is non-tautological.  
- `tests/test_vapour_rail_engine_crosscheck.py` `_FakeRailProvider` / `_FakeWarmVapoRock` — fakes are T/fO2-dependent; runner assertions pin magnitudes/slopes.  
- `tests/test_alphamelts_backend.py:220` `test_alphamelts_subprocess_liquidus_finder_selects_native_mode` — asserts `subprocess_run_mode is LIQUIDUS_FINDER` (gate actually pinned).  
- `tests/test_vapour_rail_catalog.py` `evaluator_for_hot_train("K") is evaluator_for_hot_train("K")` — intentional cache identity, not a value-vs-self tautology.  
- `tests/battery/test_score.py` dump `a == b` from two serialisations of the same residual — determinism pin.  
- `tests/battery/` migrate store `pytest.skip("migrated store not generated yet")` — **store present on this tip**, so those tests run; not skipped-by-default here.  
- Battery factories / schema / migrate unit fixtures — no `raises(Exception)`, no input-blind SUT fakes scoring the predicate.  
- Snapshot/golden regen: U0 manifest optional regen compares builder∪sources to **checked-in fixture** (not in-test rewrite of expected from SUT); SF04 snapshot behaviour tests use independent fixtures + mutation probes.  
- No `assert x == x` / empty-literal `for` bodies with asserts in the start set.  
- `tests/test_backend_status_owner.py`, `test_backend_selection.py`, `test_backend_kg_adapters.py`, `test_imcc_backend_resolution.py`, `test_melt_backend_result_contract.py`, `test_cached_real_backend.py` — no S8 hits beyond ordinary test doubles that the assertions actually pin.

SWEEP: S8 | sites=22 | live=22 | P0=0 P1=7 P2=13 P3=2 | no-hit areas: vaporock commissioning seed (fixed); vapour engine-crosscheck input-dependent fakes; alphamelts subprocess findLiq mode pin; vapour catalog cache identity; battery score dump determinism; battery migrate store present on tip; battery factories/schema; U0/SF04 fixtures not in-test SUT-regen; no assert-x-eq-x / empty-for; other backend selection/kg/status/imcc/cached-real files

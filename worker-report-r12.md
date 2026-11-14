# b-906 landing merge r12

## Merge and conflict resolution

Merged green `be76d28ad8b73d0563ee34fddf452ec77b3d6de5` into b-906 `6aa8d1d013447067e441e3e46077927826b87186` without rebasing. `git fetch origin work-v064-green` could not resolve `github.com`; the exact green commit was already present locally and was used for the merge.

- `simulator/core.py`: kept b-906’s per-hour transport pO2 consumption and clearing, including `consumed_transport_pO2_bar`; retained green’s high-temperature activity capture and diagnostic handling. Kept buffer calculations in `simulator/oxygen_buffers.py` and pressure conversion in `simulator/physical_constants.py`.
- `simulator/optimize/objective.py`: kept the b-906 wall refusal code and `wall_deposit_figure_availability` call from its owner. Retained green’s thermal-train and physical-constant imports for oxygen properties and gas constants; no duplicate constants or wall availability logic were added.
- `tests/test_optimizer_study.py`: retained green’s lowered wall search floor, cold priced candidates, and hot `s003` SiO refusal assertions, plus b-906’s refused-supply upper-bound booking and CLI authorized-zero CrO2 booking. Updated assertions where b-906 deposits can change the furnace summary and where the booked refusal is represented in sticking authority rather than `wall_deposit_refusal`.

No golden files changed. The owner APIs `oxygen_buffers`, `physical_constants`, `thermal_train`, `wall_deposit_availability`, and `cost_energy` remain the single sources used by the merged code. The old tie test name `test_winner_tie_determinism_uses_cache_key_then_candidate_id` and local `cro2_booked` have no remaining references. The deleted `saturation_log10` reference is absent from `simulator/optimize/objective.py`; grep finds a same-named local in a separate `simulator/core.py` calculation, which remains in its own scope.

## Verification

The exact 11-path remote gate ran on `rpp-ctrl` before the final study expectation corrections: **4,064 passed, 6 failed, 1 skipped**. Its failing node IDs were:

- `tests/chemistry/test_builtin_evaporation_flux_provider.py::test_evaporation_caller_wiring_matches_shared_helper_across_short_run[lunar_mare_low_ti-None]@magemin_fullrun_a`
- `tests/chemistry/test_builtin_evaporation_flux_provider.py::test_evaporation_caller_wiring_matches_shared_helper_across_short_run[mars_basalt-additives_kg1]@magemin_fullrun_a`
- `tests/chemistry/test_builtin_evaporation_flux_provider.py::test_evaporation_caller_wiring_matches_shared_helper_across_short_run[s_type_asteroid_silicate-None]@magemin_fullrun_a`
- `tests/chemistry/test_evaporation_freeze_gate.py::test_shipped_runner_freeze_gate_curve_outputs_are_pinned`
- `tests/test_optimizer_study.py::test_parallel_composition_target_stub_study_completes`
- `tests/test_optimizer_study.py::test_cli_help_unknowns_and_budget_one_stub_run`

The two study failures were test expectation mismatches and were corrected. A subsequent exact gate request for the corrected tree, request ID `04e87ff977f0457da2175e255990582e`, did not return test results: the remote runner reported that `rpp-gate`’s lease address `192.168.2.56` differed from pinned peer `192.168.2.39`, then refused local execution because the request had already been submitted. The controller directed that laptop pytest must not be used and that this same request be monitored; its status remains `LOCAL_FALLBACK_REQUIRED`, with no test receipt. Final-tree test counts are therefore unavailable. The three evaporation parity cases failed on Na at step 3 (`625 C`); the freeze pin showed a curve mismatch. A green comparison was attempted for the evaporation and freeze-gate files, but Studio setup failed because `be76d28…` could not be fetched and verified; no baseline tests ran. Green-vs-candidate status for those four failures is therefore **not established**.

Ruff is unavailable on reachable hosts and network access is disabled, per controller steer. The prescribed substitute passed: `py_compile` on all 27 Python files touched by the b-906 series; one `python3 -c` imported all 15 touched `simulator/` and `engines/` modules; grep confirmed the removed/renamed conflict names have no stale references. The separate `saturation_log10` match in `core.py` is an unrelated same-named local, not a reference to the removed objective local.

The full merge diff has whitespace inherited from green outside the conflict-resolved paths; the scoped conflict-file whitespace check passed. No cleanup of those unrelated green lines was made.

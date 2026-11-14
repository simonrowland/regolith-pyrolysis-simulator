# b-906 landing merge r12

## Merge and conflict resolution

Merged green `be76d28ad8b73d0563ee34fddf452ec77b3d6de5` into b-906 `6aa8d1d013447067e441e3e46077927826b87186` without rebasing. `git fetch origin work-v064-green` could not resolve `github.com`; the exact green commit was already present locally and was used for the merge.

- `simulator/core.py`: kept b-906’s per-hour transport pO2 consumption and clearing, including `consumed_transport_pO2_bar`; retained green’s high-temperature activity capture and diagnostic handling. Kept buffer calculations in `simulator/oxygen_buffers.py` and pressure conversion in `simulator/physical_constants.py`.
- `simulator/optimize/objective.py`: kept the b-906 wall refusal code and `wall_deposit_figure_availability` call from its owner. Retained green’s thermal-train and physical-constant imports for oxygen properties and gas constants; no duplicate constants or wall availability logic were added.
- `tests/test_optimizer_study.py`: retained green’s lowered wall search floor, cold priced candidates, and hot `s003` SiO refusal assertions, plus b-906’s refused-supply upper-bound booking and CLI authorized-zero CrO2 booking. Updated assertions where b-906 deposits can change the furnace summary and where the booked refusal is represented in sticking authority rather than `wall_deposit_refusal`.

No golden files changed. The owner APIs `oxygen_buffers`, `physical_constants`, `thermal_train`, `wall_deposit_availability`, and `cost_energy` remain the single sources used by the merged code. The old tie test name `test_winner_tie_determinism_uses_cache_key_then_candidate_id` and local `cro2_booked` have no remaining references. The deleted `saturation_log10` reference is absent from `simulator/optimize/objective.py`; grep finds a same-named local in a separate `simulator/core.py` calculation, which remains in its own scope.

## Verification

The request ledger is below. The two completed 11-path gates ran before the final study expectation corrections; the corrected-tree request did not execute tests.

- `4d00544cafac44d0a3fb5805a0a11563`: initial invocation used the absent `/Users/simonrowland/.goal-flight/remote-ci.json`. The helper fell back to local host `SimonMaookProM5` and the command exited 4 during pytest setup (0 passed, 1 command failure, no test node IDs). This was a configuration error, not test evidence.
- `759ef03e92aa4d70a58df41827438bb9`: exact 11-path gate on `rpp-ctrl`: **4,064 passed, 6 failed, 1 skipped**. Failing node IDs:

- `tests/chemistry/test_builtin_evaporation_flux_provider.py::test_evaporation_caller_wiring_matches_shared_helper_across_short_run[lunar_mare_low_ti-None]@magemin_fullrun_a`
- `tests/chemistry/test_builtin_evaporation_flux_provider.py::test_evaporation_caller_wiring_matches_shared_helper_across_short_run[mars_basalt-additives_kg1]@magemin_fullrun_a`
- `tests/chemistry/test_builtin_evaporation_flux_provider.py::test_evaporation_caller_wiring_matches_shared_helper_across_short_run[s_type_asteroid_silicate-None]@magemin_fullrun_a`
- `tests/chemistry/test_evaporation_freeze_gate.py::test_shipped_runner_freeze_gate_curve_outputs_are_pinned`
- `tests/test_optimizer_study.py::test_parallel_composition_target_stub_study_completes`
- `tests/test_optimizer_study.py::test_cli_help_unknowns_and_budget_one_stub_run`

- `9bc40f8827834cc18e711367071147f3`: attempted green pair for the evaporation and freeze-gate files; Studio setup could not fetch and verify `be76d28ad8b73d0563ee34fddf452ec77b3d6de5`, so no baseline tests ran.
- `4d531062e5f0411ca11bff5415f8e808`: repeated exact 11-path gate on `rpp-ctrl`: **4,064 passed, 6 failed, 1 skipped**, with the same six node IDs listed above. The two study failures were test expectation mismatches and were corrected afterward.
- `04e87ff977f0457da2175e255990582e`: exact 11-path gate for the corrected tree did not return test results. The remote runner reported that `rpp-gate`'s lease address `192.168.2.56` differed from pinned peer `192.168.2.39` and refused local fallback because the request had already been submitted (`LOCAL_FALLBACK_REQUIRED`). The controller directed that laptop pytest not be used and that it would gate the merge directly. Corrected-tree counts are unavailable.

The three evaporation parity cases in both completed candidate runs failed on Na at step 3 (`625 C`); the freeze pin showed a curve mismatch. Green-vs-candidate status for these four failures is **not established** because the pair request failed during setup. The two study failures were not rerun at green, and their green status is also **not established**.

Ruff is unavailable on reachable hosts and network access is disabled, per controller steer. The prescribed substitute passed: `py_compile` on all 27 Python files touched by the b-906 series; one `python3 -c` imported all 15 touched `simulator/` and `engines/` modules; grep confirmed the removed/renamed conflict names have no stale references. The separate `saturation_log10` match in `core.py` is an unrelated same-named local, not a reference to the removed objective local.

The full merge diff has whitespace inherited from green outside the conflict-resolved paths; the scoped conflict-file whitespace check passed. No cleanup of those unrelated green lines was made.

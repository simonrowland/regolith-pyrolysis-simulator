# b1134c — held gate notice projections

Started from `e3796faf5`. The two C3 hold-adjustment curve records and the
redox regime diagnostic now copy mapping-valued gate notices through the
existing `copy_gate_curve_notices` helper. Curve bounds, paths, window
calculations, selection values, and non-held output shapes remain unchanged.
No new production notice-key list was added.

## Test-first evidence at `e3796faf5`

These assertions were added and run before production edits. The seat remained
at `e3796faf5` during these runs.

BASE-TEST: `tests/chemistry/test_builtin_metallothermic_step_provider.py -k
'test_staged_na_fe_hold_recomputes_stranded_temperature_by_fe_yield_sweep or
test_staged_na_fe_hold_refuses_non_monotone_liquid_fraction_island'` — 2 held
variants failed because all four notices were absent; 2 non-held variants
passed.

BASE-TEST: `tests/test_runner_smoke.py -k
test_runner_c3_na_hold_adjustment_metadata_projects_held_curve_notices` — 1
held variant failed because all four notices were absent; the non-held variant
passed.

BASE-TEST: `tests/chemistry/test_evaporation_freeze_gate.py -k
test_held_gate_notices_reach_redox_regime_diagnostic_without_other_changes` —
failed because all four held notices were absent from the regime diagnostic.

## Local tests

All commands used the project interpreter, `MPLCONFIGDIR=/private/tmp/claude-501/main-review/mplcache`, and `-n 0`.

LOCAL-TEST: `tests/chemistry/test_builtin_metallothermic_step_provider.py -k 'test_staged_na_fe_hold_recomputes_stranded_temperature_by_fe_yield_sweep or test_staged_na_fe_hold_refuses_non_monotone_liquid_fraction_island'` — PASS, 4 passed, 72 deselected.

LOCAL-TEST: `tests/test_runner_smoke.py -k test_runner_c3_na_hold_adjustment_metadata_projects_held_curve_notices` — PASS, 2 passed, 96 deselected.

LOCAL-TEST: `tests/chemistry/test_evaporation_freeze_gate.py -k test_held_gate_notices_reach_redox_regime_diagnostic_without_other_changes` — PASS, 1 passed, 203 deselected.

LOCAL-TEST: `tests/chemistry/test_evaporation_freeze_gate.py` — PASS, 204 passed, 9 warnings, 679.17 s.

## Engine configuration and process check

Copied the main checkout's `engines/engines.local.toml` into this seat. It is
gitignored. Both configured alphaMELTS and MAGEMin executable paths exist and
are executable, so both engines were available to the project's runner. No
engine binary was started directly.

The final `pgrep -fl alphamelts` and `pgrep -fl MAGEMin` checks were refused by
the sandbox (`sysmond service not found` / `Cannot get process list`, exit 3).
The controller must verify whether any test-owned engine process remains.

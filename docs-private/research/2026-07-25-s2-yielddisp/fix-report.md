# S2 rev5 yield-disposition review-fix report

## TL;DR

- All pinned production defects fixed; focused fix gate is 15/15 green.
- Failure envelopes preserve the primary error and attach disposition failure as secondary.
- Origin tracking is mol-native in the hourly sidecar; closure dust no longer invents reagent lineage.
- Stage-0 missing origin always raises `OriginUnresolvedError`; known metal/holdup accounts are mapped.
- Golden churn remains five nodes across three unchanged fixtures; none regenerated.
- Real C2A retries hit MAGEMin's 300-second timeout; exact roundoff receipt is pinned green.

## Finding dispositions

| Finding | Disposition | Evidence |
|---|---|---|
| P0/P1 failure construction masks primary failure | Fixed | `_runner_failure_result` projects only with a ledger surface, catches secondary projection errors, returns `yield_disposition: null`, and appends the secondary exception without replacing status/reason/primary text. New forced-raise regression plus both existing hostile failure tests pass. |
| P1 clean-C2A kg/mol dust becomes reagent origin | Fixed | Reagent-inventory subtraction collapses only remainders within `total * 5e-14`; derivation is beside the branch. Exact `0.22746831458806405 - 0.22746831458806402 mol` regression emits no reagent row and closes. |
| P1 Stage-0 replay guesses from account name | Fixed | Missing reconstructed origin now unconditionally raises `OriginUnresolvedError`; reagent-account negative-draw regression proves the refusal. |
| P1 hourly sidecar stores kg-keyed internal state | Fixed | Capture converts observer kg once at the boundary and stores `non_feedstock_reagent_element_mol_atoms_by_account`; campaign replay consumes mol-atoms directly. Targeted artifact guards pass 2/2. |
| P1 known `process.metal_phase` / condensation holdup unmapped | Fixed | `process.metal_phase` maps to `metal_phase_retained`; `process.condensation_retained_holdup` maps to neutral `overhead_terminal_inventory`. Regression covers Fe, Si, and O. |
| P2 verification understated failures | Fixed | Main report now records the pre-fix nine-node minimum, current focused passes, C2A timeout inconclusives, unrelated artifact-guard failure, and exact five-node golden churn. |

## Verification receipts

```text
focused review-fix suite: 14 passed in 5.95s
live runner schema node: 1 passed, 7 warnings in 233.74s
mol-native artifact guards: 2 passed in 1.25s
py_compile: pass
git diff --check: pass
```

Full artifact guard: `7 passed, 1 failed`; unrelated
`data/flowsheet.yaml:1 byte=0x80`. Two clean-C2A VPR retries timed out inside
MAGEMin/engine-pool waits before disposition assertions, so no green claim.

## Golden churn — enumerated, not regenerated

1. `tests/test_runner_smoke.py::test_runner_golden_fixture_matches[lunar_mare_low_ti_C0_24h]`
2. `tests/test_runner_smoke.py::test_runner_golden_fixture_matches[mars_basalt_C2A_12h]`
3. `tests/test_runner_smoke.py::test_runner_golden_fixture_matches[ci_carbonaceous_chondrite_C2B_12h]`
4. `tests/test_recipe_io.py::test_no_recipe_run_matches_committed_golden_text`
5. `tests/test_cost_ledger.py::test_cost_rollup_metadata_is_golden_neutral_for_runner_fixture`

Fixture files remain unchanged:

- `tests/fixtures/runner/lunar_mare_low_ti_C0_24h.json`
- `tests/fixtures/runner/mars_basalt_C2A_12h.json`
- `tests/fixtures/runner/ci_carbonaceous_chondrite_C2B_12h.json`

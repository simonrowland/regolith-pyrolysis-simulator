# Fix 6 — staged yield-disposition gate delta

## TL;DR

- Kept all seven triage fixes; no golden was regenerated.
- Na/K cleanup/main withdrawals and Mg feedstock/reagent withdrawals now use one proportional-pool allocator.
- Pool withdrawals close by construction and all affected rows report `attribution_method="pool_ratio"`.
- Origin dust is the ledger relative closure tolerance times the run's charged atom inventory; 0.99× passes and 1.01× refuses.
- The observed `1.68425158412e-07 mol` O residual is 0.382% of the applicable threshold, not close to it.
- Former condensation refusals and focused accounting/Knudsen/EvalSpec gates are green; exact evidence below.

## Controller rulings applied

### One shared physical-pool mechanism

`simulator/accounting/lots.py::allocate_pool_withdrawal` is the sole ratio
allocator used by both:

1. `AtomLedger` feedstock/reagent origin withdrawals, including the already
   sanctioned additive + recovered-Mg reagent pool; and
2. yield-disposition cleanup/main replay for the sanctioned Na/K condensation
   pool.

The condensation replay tracks cumulative inputs and withdrawals separately
for cleanup and main origin classes. A withdrawal from a live two-origin Na/K
pool is allocated by the declared conservation law. For withdrawal \(W\) and
known inputs \(I_i\), each share is \(W I_i/\sum I\), so the shares sum to
\(W\); the final share absorbs floating-point summation dust. Non-sanctioned
mixed-origin partial withdrawals still raise `OriginUnresolvedError`.

Any affected fraction-table, link, stream, or reagent-cycle row merges to
`attribution_method="pool_ratio"`.

### One scale-relative origin-dust threshold

The threshold is:

```text
C = sum(feedstock-origin charged mol-atoms + reagent-origin charged mol-atoms)
r = AtomLedger.relative_tolerance
D = r * C
```

This uses the ledger's established relative closure basis at the run's actual
working scale; it introduces no absolute mol magic number. Correctly typed tiny
streams remain projected. Only unresolved-origin differences at or below
`D` are treated as numerically indistinguishable from ledger roundoff. Above
`D`, typed refusal is unchanged.

The final disposition closure still applies the strict
`5e-14` fractional gate. If that normalized gate is exceeded, the unscaled
absolute atom residual must still be at or below `D`; otherwise
`YieldDispositionError` is raised. The payload reports both residuals and both
limits. No scale-to-close or invented origin is applied.

For the 1000 kg lunar EvalSpec charge:

```text
C = 44088.2968938 mol-atoms
r = 1e-9
D = 4.40882968938e-05 mol-atoms
observed O residual = 1.68425158412e-07 mol-atoms = 0.00382018 D
```

The observed O residual is therefore not uncomfortably close to the threshold;
it is 0.382% of it. The `1.38777878078e-17 mol` Knudsen residue is smaller
still. The regression exercises `0.99 D` (accepted) and `1.01 D` (typed
refusal).

## Per-item disposition

| Delta item | Final disposition |
|---|---|
| Lunar Mg metallothermic full run | Existing Mg pool sanction kept; now routes through the shared allocator. Prior exact focused node passed. |
| Mars Mg metallothermic full run | Same shared Mg pool path. Prior exact focused node passed. |
| Backend reagent-provenance observer | Existing typed-ledger compatibility reprojection fix kept; focused node passed. |
| Batch/CLI/web mol-ledger parity | **Passed** after Na/K campaign-pool attribution. |
| Engine-worker close timeout | Known `t-422` flake; no yield-disposition code change. |
| Recovered-K compatibility decrement | Existing typed-ledger reprojection fix kept; focused node passed. |
| Free-molecular Knudsen continuity | **Passed** with scale-relative unresolved-origin dust. |
| Viscous-run no-Knudsen-warning | **Passed** with the same threshold. |
| Pressure/coating Knudsen diagnostic | **Passed** with the same threshold. |
| In-window C2A Na product / cleaned-melt O residual | **Passed**; observed residual is 0.382% of `D`. |
| Carbonaceous runner golden | Expected churn; not regenerated here. |
| Runner operator-decision shadow trace | **Passed** after Na/K campaign-pool attribution. |
| Every-gate pause/resume ledger identity | **Passed** after Na/K campaign-pool attribution. |
| Alternate Path B pause/resume | **Passed** after absolute residual was evaluated against the same scale-relative dust band. |

## Verification

- `tests/test_yield_disposition.py` plus the three Knudsen nodes and EvalSpec
  node: **26 passed** in `48.84 s`.
- Backend provenance and recovered-K focused nodes: **2 passed** in `9.17 s`.
- Cross-surface parity plus every-gate pause/resume: **2 passed** in the
  grouped full-run invocation.
- Alternate Path B pause/resume: **1 passed** in `83.30 s`.
- Runner operator-decision shadow trace: **1 passed** in `759.61 s`.
- `git diff --check` and `git diff --cached --check`: passed.

## Exact controller golden regeneration set

Regenerate exactly:

1. `tests/fixtures/runner/lunar_mare_low_ti_C0_24h.json`
2. `tests/fixtures/runner/mars_basalt_C2A_12h.json`
3. `tests/fixtures/runner/ci_carbonaceous_chondrite_C2B_12h.json`

Expected affected nodes:

1. `test_runner_golden_fixture_matches[lunar_mare_low_ti_C0_24h]`
2. `test_runner_golden_fixture_matches[mars_basalt_C2A_12h]`
3. `test_runner_golden_fixture_matches[ci_carbonaceous_chondrite_C2B_12h]`
4. `test_no_recipe_run_matches_committed_golden_text`
5. `test_cost_rollup_metadata_is_golden_neutral_for_runner_fixture`

No golden file was edited.

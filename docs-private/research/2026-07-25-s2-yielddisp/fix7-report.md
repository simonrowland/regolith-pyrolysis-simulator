# Fix 7 — pool/dust correctness

## TL;DR

- Physical atom closure is independent of origin attribution and remains gated at `5e-14`.
- Real but unattributable atoms now remain explicit in `origin_unattributed`; no dust-band mass deletion.
- Ordered gross pool events preserve deposit/withdrawal order; cumulative provenance debt counts new mass once.
- Pool state persists across campaigns, resets between batches, and rolls back with a refused hour.
- Pool over-withdrawal and invalid numeric inputs raise typed; `pool_ratio` is never relabeled `tracked`.
- Focused gates and two independent refutation reviews pass; all fix-7 paths are staged, with no commit.

## Contract closure

### Physical closure versus attribution

`build_yield_disposition()` now computes full-element physical closure from all
feedstock, reagent, and initially unattributed inputs against all terminal
accounts. That gate uses only the `5e-14` closure limit. The attribution dust
band is used only to decide whether an origin can be named.

The payload contains a top-level `origin_unattributed` object with:

- initial, terminal, and unique cumulative mol-atoms by element;
- terminal mol-atoms by account;
- the attribution limit used by the typed refusal.

### Gross pool history and salami gate

`AtomLedger` now records both cumulative gross inputs/withdrawals and an ordered
event journal. Campaign replay consumes events in real order, so
withdraw-then-input differs correctly from input-then-withdraw within one
snapshot interval.

A separate exact provenance-debt inventory moves unresolved atoms between
accounts without recounting recirculation. Only newly introduced debt increments
the monotonic cumulative counter. Twelve individually sub-band crumbs are
retained in gross withdrawal history and trip the cumulative typed refusal at
`1.08 × D`; cycling one `0.6 × D` lot twice remains `0.6 × D`.

### State and refusal behavior

- Batch reload creates a fresh ledger and clears disposition snapshots.
- Gross counters, ordered events, and cumulative provenance debt persist across
  campaign snapshots within one run.
- Typed hour rollback owns deep copies of the new mutable accounting state.
- `allocate_pool_withdrawal()` rejects invalid/non-finite arguments and any
  actual over-withdrawal with `PoolWithdrawalError`.
- The ledger wrapper surfaces pool overdraw as `OverdraftError` without mutating
  gross counters.
- Numerical attribution shortage within the dust band is added explicitly to
  campaign replay input; the allocator never silently truncates it.

## Realistic full-run receipt

Scenario: 18-hour `lunar_mare_low_ti` C2A run using the
`internal-analytical` backend with the 1400–1450 °C window from
`test_in_window_c2a_run_captures_na_product`.

| Metric | Result |
|---|---:|
| Full atom-closure maximum residual fraction | `4.876687075494601e-16` |
| Attribution threshold `D` | `4.408829689378486e-05 mol-atoms` |
| Worst cumulative unattributed element | `O` |
| Worst cumulative unattributed total | `1.998677083963044e-16 mol-atoms` |
| Worst total / `D` | `4.533350627669172e-12` |
| Worst total as percent of `D` | `4.533350627669172e-10 %` |

The cumulative gate therefore bounds the run; the measured worst case is about
2.21e11 times below the refusal threshold.

## Regression coverage

- closure remains strict with unattributed mass present;
- pure and mixed unattributed stream schema values;
- input-before-withdraw and withdraw-before-input event order;
- below/equal/above attribution-band pool liveness;
- cumulative salami refusal and non-double-counted recirculation;
- within-run persistence plus between-run reset of snapshots, gross history,
  and cumulative state;
- rollback isolation for gross history and cumulative state;
- direct allocator invalid/overdraw cases and ledger-level typed overdraw with
  unchanged counters.

## Verification

- `107 passed` — yield disposition, ledger API/admissibility, runner schema,
  Mars Stage-0 disposition, and two C2A Knudsen paths.
- `1 passed` — realistic 18-hour C2A Na-product run.
- `2 passed` — mol-native artifact guards.
- `git diff --check` and `git diff --cached --check` — clean.
- API/schema refutation review — `APPROVE`.
- testing/conservation refutation review — `APPROVE`.

Review artifacts:

- `docs-private/research/2026-07-25-s2-yielddisp/review-fix7-api.md`
- `docs-private/research/2026-07-25-s2-yielddisp/review-fix7-testing.md`

## Delivery

Exact fix-7 files are staged. No commit or push was performed.

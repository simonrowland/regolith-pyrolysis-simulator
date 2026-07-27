# Fix 7 testing/conservation review

## Verdict

**REWORK**

Physical closure is now separated correctly from material-origin attribution,
and the focused test file is green. One remaining campaign-attribution bug and
three regression gaps leave the fix insufficiently pinned.

## Findings

### P1 — Gross counters still lose event order inside one snapshot interval

**Confidence: 10/10**

**Evidence:** `simulator/accounting/yield_disposition.py:1121-1147` always
adds the interval's complete gross input delta before allocating the interval's
complete gross withdrawal delta. The counters preserve amounts, but not event
order. `tests/test_yield_disposition.py:702-744` covers only the favorable
`input -> withdrawal -> snapshot` order.

Counterexample:

1. C0 leaves `2.0 mol` cleanup Na in the condensation train.
2. During the next C2A interval, withdraw `0.6 mol` first.
3. Deposit `1.0 mol` main-campaign Na second.
4. Capture the snapshot.

At withdrawal time only cleanup material exists, so the correct retained split
is cleanup `1.4 mol`, main `1.0 mol`. Replay sees gross deltas
`input=1.0`, `withdrawal=0.6`, credits main first, then allocates the withdrawal
over a synthetic `2:1` mixed pool. It reports cleanup `1.6 mol`, main
`0.8 mol`, and a pool-ratio event that never occurred.

**Required fix:** retain ordered condensation-account flow events, or establish
and enforce an input-before-withdrawal invariant at the ledger/snapshot
boundary. Add the reverse-order regression alongside the existing same-interval
test.

### P1 — The new “salami” test does not exercise the campaign salami defect

**Confidence: 10/10**

**Evidence:** `tests/test_yield_disposition.py:747-780` creates twelve
unattributed initial accounts, moves all pieces directly to
`terminal.offgas`, captures no snapshots, and never touches
`process.condensation_train`. It therefore tests terminal aggregation in
`_cumulative_origin_unattributed()`, not repeated sub-limit campaign
withdrawals through `_campaign_retained_pool()`.

The original failure class was a sequence of individually sub-limit
condensation withdrawals whose cleanup/main attribution error accumulated
across snapshots. Restoring the old per-delta dust skip in campaign replay
would not make this new test fail.

**Required fix:** build a cleanup/main condensation pool, capture a snapshot,
apply repeated individually sub-limit withdrawals with snapshots across the
run, then assert the cumulative retained split or typed refusal. The test must
exercise gross withdrawal deltas and campaign classification.

### P2 — Typed allocator rejection is tested in only one direction

**Confidence: 9/10**

**Evidence:** `simulator/accounting/lots.py:31-56` adds distinct typed
rejections for invalid balances, non-finite withdrawals, negative withdrawals,
and above-available withdrawals. `tests/test_yield_disposition.py:783-788`
tests only the last case.

A regression to the former `max(0, withdrawal)` behavior for negative values,
or acceptance of `NaN`/`inf`, would pass. The ledger wrapper at
`simulator/accounting/ledger.py:900-920` also has no fix-local assertion that a
failed allocation surfaces as `OverdraftError` without incrementing gross
counters.

**Required fix:** parameterize negative, `NaN`, positive/negative infinity, and
invalid-balance cases; add one ledger-level overdraw assertion that checks the
typed wrapper/cause and unchanged gross counters.

### P2 — Reset regression never dirties a gross condensation counter

**Confidence: 9/10**

**Evidence:** `tests/test_yield_disposition.py:808-823` saves
`first_gross`, then only captures two snapshots before loading the second
batch. Snapshot capture does not mutate ledger gross counters. The test proves
the snapshot map reset at `simulator/core.py:1098-1099`, but its gross-flow
assertion compares two equivalent post-load baselines rather than proving
old-run flow history was discarded.

**Required fix:** after saving the first-run baseline, perform a condensation
input and withdrawal, assert gross counters changed, then load the second batch
and assert those old-run deltas are absent while the new baseline remains.

## Verified behavior

- `simulator/accounting/yield_disposition.py:1485-1545` computes physical atom
  closure from feedstock, reagent, and initially unattributed inputs against
  all terminal atoms. Feedstock-origin residuals remain separately reported.
- `tests/test_yield_disposition.py:181-215` would fail if the
  origin-unattributed amount were again treated as a physical closure gap.
- `simulator/accounting/ledger.py:669-672` records gross counters only after
  transition validation and state projection succeed.
- Focused verification:
  `.venv/bin/python -m pytest tests/test_yield_disposition.py -q` -> **26 passed
  in 10.30 s**.

## Closing refutation review

### Verdict

**REWORK**

The ordered event journal closes the prior production event-order bug, and the
forward/reverse tests distinguish the two physically different histories.
Allocator typing, ledger overdraw rollback, recirculation uniqueness, and gross
run reset are materially improved. One P1 regression gap remains; two P2
boundary/ownership gaps keep the new state surfaces incompletely pinned.

### Closed — Ordered input/withdrawal history

**Confidence: 10/10**

`simulator/accounting/ledger.py:700-746` records ordered, deep-copyable gross
events after transition validation. `simulator/accounting/yield_disposition.py:
1112-1176` requires journal prefix monotonicity, reconciles journal totals to
gross counters, and replays only new events in recorded order.

`tests/test_yield_disposition.py:717-809` now covers both histories:

- input then withdrawal -> cleanup/main retained `1.6/0.8 mol`,
  `pool_ratio`;
- withdrawal then input -> cleanup/main retained `1.4/1.0 mol`,
  `tracked`.

This refutes the prior event-order finding.

### P1 — Campaign salami replay still has no effective regression

**Confidence: 10/10**

`tests/test_yield_disposition.py:874-930` places twelve pieces totaling
`1.08 * limit` in `AtomLedger(initial_balances=...)`. The ledger therefore
records the over-limit cumulative unattributed amount at construction. In
`build_yield_disposition()`, the cumulative attribution refusal runs before
`_terminal_portions()` and campaign replay
(`simulator/accounting/yield_disposition.py:302-320`).

The snapshots and condensation moves in this test are unreachable as evidence
for `_campaign_retained_pool()`: the expected exception is raised before that
function runs. Reintroducing the original bug that discards repeated
individually sub-limit campaign withdrawals could still leave this test green.

**Required fix:** add a typed 50:50 cleanup/main pool with both classes well
above the liveness band, make repeated individually sub-limit withdrawals
across snapshots, and assert the exact cumulative cleanup/main retained split.
Keep the existing unattributed salami test as the separate unique-mass guard.

### P2 — “Dirty” reset and rollback do not dirty cumulative attribution state

**Confidence: 9/10**

The reset test mutates gross flows/events with a typed external load
(`tests/test_yield_disposition.py:1097-1127`) but never changes
`cumulative_origin_unattributed_atom_moles()`. The rollback test likewise moves
typed Na after taking the snapshot
(`tests/test_yield_disposition.py:1047-1076`), so the cumulative-state equality
assertion compares the same unchanged value.

The implementation is structurally correct:
`simulator/core.py:1098-1099` installs a new ledger and clears snapshots, while
`simulator/run_executor.py:78-84` deep-copies gross state and copies cumulative
state. The tests, however, would not catch omission of the cumulative reset or
copy.

**Required fix:** dirty cumulative unattributed state before both boundaries
and assert the old value is absent after batch reset and restored after an
actual refused-hour rollback.

### P2 — Liveness and allocator tolerances are bracketed, not boundary-pinned

**Confidence: 8/10**

`tests/test_yield_disposition.py:811-871` probes `0.5x` and `1.1x` the nominal
campaign liveness band. It does not pin the implementation's exact `>`
predicate at `simulator/accounting/yield_disposition.py:1148-1152`, and the
test's nominal limit omits the dust charge itself from the actual
`relative_tolerance * total_charged_atoms` denominator.

`tests/test_yield_disposition.py:992-1014` covers invalid allocator arguments
and a clearly negative withdrawal, but not the accepted negative numerical
dust boundary at `simulator/accounting/lots.py:47-60`. The direct
above-available path is correctly strict, and the ledger-level test confirms
`OverdraftError` with `PoolWithdrawalError` cause and unchanged aggregate
counters.

**Required fix:** compute the exact self-inclusive liveness threshold and use
`math.nextafter()` at/beyond it; similarly pin negative withdrawal at and just
beyond the declared allocator tolerance.

### Closed — Recirculation, overdraw, and gross reset

**Confidence: 9/10**

- `tests/test_yield_disposition.py:933-981` proves one unattributed lot can
  leave, re-enter, and leave the condensation account without its unique
  cumulative mass being counted repeatedly.
- `tests/test_yield_disposition.py:992-1044` covers typed invalid allocator
  arguments, above-available rejection, ledger exception translation, and
  unchanged gross counters on failed apply.
- `tests/test_yield_disposition.py:1079-1128` now dirties gross condensation
  state and proves the snapshot map, gross totals, and ordered journal reset to
  the fresh-run baseline.

### Closing verification

- `.venv/bin/python -m pytest tests/test_yield_disposition.py
  tests/test_runner_smoke.py::test_runner_schema_shape_contract -q`:
  **45 passed, 7 dependency deprecation warnings in 21.03 s**.
- `git diff --check`: clean.

## Final closing pass

### Verdict

**APPROVE**

No remaining correctness or test blocker in the current unstaged fix 7 delta.
This verdict supersedes the earlier REWORK verdicts in this artifact.

### Closure evidence

- **Campaign salami:** `tests/test_yield_disposition.py:911-976` now proves the
  ordered condensation journal contains twelve distinct unattributed
  withdrawal events, each `0.09D`, and that their gross sum is `1.08D` before
  the typed cumulative refusal. The replay loop at
  `simulator/accounting/yield_disposition.py:1132-1179` processes every event;
  it has no per-withdrawal dust skip.
- **Recirculation:** `tests/test_yield_disposition.py:979-1027` proves one
  unattributed lot can leave and re-enter the condensation account without
  multiplying the unique cumulative amount. Per-account debt projection in
  `simulator/accounting/ledger.py:1917-1964` distinguishes carried debt from
  newly unattributed mass.
- **Rollback/reset:** `tests/test_yield_disposition.py:1093-1131` now creates a
  real within-balance-band origin shortfall and proves the rollback snapshot
  owns the earlier cumulative value. `tests/test_yield_disposition.py:
  1134-1198` dirties gross counters, ordered events, and cumulative
  unattributed state before proving a new batch restores the clean baseline.
  `simulator/run_executor.py:78-87` copies all three new mutable state surfaces,
  including per-account unattributed debt.
- **Liveness:** `tests/test_yield_disposition.py:844-908` now covers below,
  equality-side, and above-band nonpoolable campaign behavior. The production
  predicate remains explicit at
  `simulator/accounting/yield_disposition.py:1148-1154`.
- **Strict allocator and replay roundoff:** the allocator remains fail-loud for
  positive over-withdrawal. Replay first admits real below-liveness balances,
  then assigns only a shortage within the attribution band to the current
  campaign (`simulator/accounting/yield_disposition.py:1155-1175`). Larger
  shortages still reach the strict allocator and raise. The focused real C2A
  path passes with the observed `6.9e-18` replay roundoff.
- **Verification:** `.venv/bin/python -m pytest
  tests/test_yield_disposition.py
  tests/test_runner_smoke.py::test_runner_schema_shape_contract -q`:
  **46 passed, 7 dependency deprecation warnings in 22.52 s**.
  `git diff --check`: clean.

# Fix 7 API / schema review

## Verdict

**REWORK — 2 P1, 3 P2 findings.**

Scope: only the unstaged fix-7 delta from `git diff`, interpreted against the
already-staged yield-disposition implementation. Review was read-only except for
this required artifact. No long suite was run; each behavioral finding below has
a focused executable reproduction.

## Findings

### P1 — gross-flow journal survives a rolled-back hour

**Confidence: 10/10**

**Files:** `simulator/accounting/ledger.py:332-338`,
`simulator/accounting/ledger.py:669-672`,
`simulator/run_executor.py:68-78`, `simulator/run_executor.py:274-283`

Fix 7 adds mutable nested state at `AtomLedger._gross_account_flows` and mutates
it in place on every committed debit and credit:

```python
for lot in transition.debits:
    self._record_gross_lot("withdrawals", lot)
for lot in transition.credits:
    self._record_gross_lot("inputs", lot)
```

The runner's hour rollback uses `copy(ledger)` and deep-copies selected mutable
fields, but does not copy `_gross_account_flows`. The pre-hour snapshot and live
ledger therefore share the same journal. A typed refusal restores balances from
before the hour while retaining gross inputs/withdrawals produced by the refused
hour. Later disposition can split cleanup/main using flows that never committed.

Focused reproduction:

```text
snapshot_credit_after_live_mutation {'Na': 0.9999999999999999}
same_nested_object True
```

**Required fix:** deep-copy `_gross_account_flows` in
`_snapshot_atom_ledger()` and add a refusal rollback regression asserting
balances, origin state, gross counters, and subsequent yield disposition all
match the pre-hour snapshot.

### P1 — “cumulative origin-unattributed” counts repeated circulation as new uncertainty

**Confidence: 10/10**

**Files:** `simulator/accounting/ledger.py:669-672`,
`simulator/accounting/yield_disposition.py:1444-1464`

`_cumulative_origin_unattributed()` takes the maximum of terminal unresolved
inventory and the condensation train's cumulative gross withdrawals. Gross
withdrawals are throughput, not a unique-atom or newly-unattributed counter. The
same unresolved atoms can cross the condensation boundary repeatedly and are
counted once per pass.

Focused reproduction cycled one `0.6 * limit` Na lot through the condensation
train twice. Unique unresolved inventory stayed below the limit, but gross
withdrawal reached `1.2 * limit`:

```text
unique_unattributed 0.0006 limit 0.001
gross_condensation_withdrawal 0.0012
verdict OriginUnresolvedError cumulative origin_unattributed exceeds attribution limit for Na: total=0.0012, limit=0.001 mol-atoms
```

This can falsely refuse an otherwise admissible whole run, especially for a
recirculating alkali shuttle.

**Required fix:** enforce the salami bound against unique unresolved inventory
or a monotonic counter of *newly introduced* unresolved atoms. Do not use gross
movement through one account as a provenance-debt counter. Add a repeated-cycle
regression proving the same atoms are not accumulated twice.

### P2 — non-finite allocator tolerance disables strict overdraw rejection

**Confidence: 10/10**

**File:** `simulator/accounting/lots.py:23-57`

`absolute_tolerance` is converted but never validated before:

```python
tolerance = max(
    float(absolute_tolerance),
    available * POOL_WITHDRAWAL_RELATIVE_TOLERANCE,
)
```

`NaN` and `inf` make the overdraw comparison false or unbounded; line 57 then
silently clamps the request to available inventory. Invalid numeric strings in
balances, withdrawal, or tolerance also escape as raw `ValueError`, despite the
new typed `PoolWithdrawalError` API.

Focused reproduction:

```text
absolute_tolerance=inf, withdrawal=2, available=1 -> {'feedstock': 1.0}
absolute_tolerance=nan, withdrawal=2, available=1 -> {'feedstock': 1.0}
balance='bad' -> ValueError
withdrawal='bad' -> ValueError
```

**Required fix:** coerce all numeric inputs inside a guarded conversion, require
finite non-negative `absolute_tolerance`, and raise `PoolWithdrawalError` for
every invalid allocator argument. Preserve the strict `withdrawal >
available + tolerance` refusal.

### P2 — campaign split treats arbitrarily small positive dust as a live class

**Confidence: 9/10**

**Files:** `simulator/accounting/yield_disposition.py:1000-1017`,
`simulator/accounting/yield_disposition.py:1020-1053`,
`simulator/accounting/yield_disposition.py:1079-1136`

The three campaign helpers still accept `origin_dust_mol_atoms`, but Fix 7 no
longer passes any tolerance into `_campaign_retained_pool()`. Its commingling
test uses `amount > 0.0`. A sub-tolerance cleanup residue therefore makes both
cleanup and main live and refuses every non-poolable key.

Focused cumulative-counter reproduction:

```text
cleanup Fe input = 1e-16
main Fe input = 1.0
main interval withdrawal = 0.5
verdict OriginUnresolvedError mixed Fe
```

The prior implementation explicitly compared live balances with a per-key
derived tolerance. Removing that threshold reintroduces numerical false
refusals.

**Required fix:** restore a scale-derived tolerance for live-class detection
and retained-output filtering while leaving real allocator overdraw checks
strict. Add below/above-boundary cases for species, typed-origin atoms, and
origin-unattributed atoms.

### P2 — valid new stream value violates the declared schema value set

**Confidence: 10/10**

**Files:** `simulator/accounting/yield_disposition.py:17`,
`simulator/accounting/yield_disposition.py:1262-1304`,
`tests/test_runner_smoke.py:879-894`

For a valid below-limit stream containing only unresolved-origin atoms, Fix 7
emits:

```json
{
  "origin_scope": "origin_unattributed",
  "attribution_method": "origin_unattributed"
}
```

The runner's schema contract for the unchanged yield-disposition schema version
`5.0` permits only `tracked` and `pool_ratio` in
`terminal_species_streams[*].attribution_method`. A focused valid artifact
produced exactly the new third value, so schema consumers and the declared
contract disagree.

**Required fix:** choose one contract and make it complete. Prefer keeping
`attribution_method` for allocation algorithms (`tracked` / `pool_ratio`) and
representing unavailable provenance solely in a separate typed status/scope
field. If `origin_unattributed` is intentionally a third method, update the
schema/version, all allowlists/consumers, and positive contract tests for both
pure and mixed typed/unattributed streams.

## Targeted evidence summary

- Gross journal rollback alias: reproduced.
- Repeated-atom cumulative false refusal: reproduced.
- `NaN` / `inf` allocator overdraw bypass: reproduced.
- Sub-tolerance live-class refusal: reproduced.
- Schema third-value artifact: reproduced.

---

## Refutation-stance closing review

### Verdict

**APPROVE — all five prior findings are closed.**

No residual blocking or follow-up finding survived the closing pass. The
corrected delta preserves strict allocator failure, distinguishes ordered pool
events from cumulative counters, counts newly unresolved atoms once, owns the
new journal state across rollback/reset, and declares the third stream value.

### Prior-finding closure

| Prior finding | Closure evidence | Refutation result |
|---|---|---|
| P1 gross journal survives rollback | `simulator/run_executor.py:78-84` now deep-copies `_gross_account_flows`, `_gross_account_flow_events`, and `_cumulative_origin_unattributed_atom_moles`. `tests/test_yield_disposition.py:1047` mutates the live ledger after snapshot and proves all three snapshot surfaces remain unchanged. | **Closed.** The former alias reproduction no longer holds. |
| P1 gross withdrawal double-counts recirculated uncertainty | `simulator/accounting/ledger.py:364-371` seeds the cumulative counter once from initial unresolved atoms; `simulator/accounting/ledger.py:663-667` computes only newly introduced uncertainty; `simulator/accounting/yield_disposition.py:1535-1544` reads that counter rather than gross condensation throughput. `tests/test_yield_disposition.py:933` cycles the same `0.6 * limit` Na lot through condensation twice and asserts cumulative remains `0.6 * limit`; `tests/test_yield_disposition.py:874` separately proves twelve unique `0.09 * limit` lots refuse cumulatively. | **Closed.** Recirculation is not counted twice, while salami-sliced new unresolved mass still accumulates. |
| P2 non-finite tolerance disables overdraw rejection | `simulator/accounting/lots.py:31-66` routes balances, withdrawal, and tolerance through finite-number validation, rejects negative tolerance, then performs the strict overdraw comparison before clamping within tolerance. `tests/test_yield_disposition.py:984-1015` covers overdraw, strings, NaN, infinity, negatives, and invalid tolerance; `tests/test_yield_disposition.py:1017` proves a ledger overdraw raises with `PoolWithdrawalError` as cause and leaves gross counters unchanged. | **Closed.** No tested invalid input or overdraw silently clamps. |
| P2 dust becomes a live campaign class | `simulator/accounting/yield_disposition.py:1139-1175` derives per-key tolerance, excludes sub-band balances from liveness, restores real dust balance only when needed to satisfy an otherwise valid withdrawal, and passes the same tolerance to the strict allocator. `tests/test_yield_disposition.py:811` pins below-band acceptance and above-band refusal. | **Closed.** Dust no longer creates a second live class. Physical and typed-origin shortages reach the strict allocator; an unattributed-lens gap is recorded explicitly as new provenance debt and is bounded by the whole-run cumulative gate executed before campaign splitting. |
| P2 stream enum incomplete | `tests/test_runner_smoke.py:891-896` now declares `origin_unattributed` alongside `tracked` and `pool_ratio`; `simulator/accounting/yield_disposition.py:1363-1405` emits the corresponding `origin_scope` / `attribution_method` pair for pure unattributed streams and the mixed scope for mixed streams. The live schema contract passes. | **Closed.** Producer and declared consumer allowlist agree. |

### Gross-flow journal semantics

The closing pass also attacked ordering and counter reconciliation:

- `capture_ledger_snapshot()` persists ordered condensation events as well as
  aggregate counters.
- `_campaign_retained_pool()` rejects a changed/decreased journal prefix,
  re-sums events against both aggregate directions, and replays only the suffix
  since the preceding snapshot.
- Withdrawal-before-input and input-before-withdrawal in one snapshot interval
  have separate regressions, so net-counter cancellation cannot reverse pool
  provenance.
- Failed ledger applies record neither aggregate flows nor events because
  journal mutation occurs only after projection, conservation, and policy
  validation succeed.

### Verification

```text
.venv/bin/python -m pytest tests/test_yield_disposition.py -q
43 passed in 10.30s

.venv/bin/python -m pytest \
  tests/test_runner_smoke.py::test_runner_schema_shape_contract -q
1 passed, 7 warnings in 21.00s

git diff --check
clean
```

The initial system-Python attempt was environment-inconclusive because that
interpreter lacks the pytest xdist/timeout plugins required by
`pyproject.toml`; the repository `.venv` supplied the valid receipts above.

---

## Final debt-projection refutation

### Verdict

**APPROVE — no debt-accounting or rollback blocker remains.**

The replacement at `simulator/accounting/ledger.py:1917-1964` carries live
origin-unattributed debt per account, removes only debt actually backed by each
debit account, transfers that debt across the transition by element, and adds
only debit or credit excess to the cumulative counter. Commit ordering at
`simulator/accounting/ledger.py:670-708` keeps the projected debt and cumulative
increment outside live state until conservation, balance, and account-policy
validation have succeeded.

Adversarial results:

- Repeated movement through condensation and a recycle buffer leaves cumulative
  Na at the original `0.6 * attribution_limit`, while terminal unresolved Na
  remains the same fragment.
- Twelve distinct initial fragments of `0.09 * attribution_limit` still sum to
  `1.08 * attribution_limit` and refuse.
- A direct split `1.0 -> 0.4 + 0.6`, merge `0.4 + 0.6 -> 1.0`, and recirculation
  probe preserved cumulative O at exactly `1.0`; live debt followed the two
  destination accounts and then the merged account.
- Two independent within-policy origin shortfalls increased cumulative Na by
  their sum; moving each resulting unresolved fragment again did not increase
  it a second time.

Rollback ownership is explicit at `simulator/run_executor.py:68-88`: the gross
counter tree, ordered event journal, per-account debt tree, and cumulative
counter are all copied. A direct alias probe confirmed both the outer debt map
and its nested account maps have distinct identities; mutating live nested debt
left the snapshot unchanged.

The real C2A replay correction at
`simulator/accounting/yield_disposition.py:1161-1175` restores a current-campaign
input for an accepted attribution-band shortage before calling the strict pool
allocator. It does not weaken `allocate_pool_withdrawal()` itself. The focused
C2A roundoff regression passes and does not invent a feedstock/reagent origin.

### Final verification

```text
.venv/bin/python -m pytest -q \
  tests/test_yield_disposition.py::test_clean_c2a_recovered_reagent_roundoff_does_not_invent_origin \
  tests/test_yield_disposition.py::test_origin_unattributed_salami_sequence_refuses_cumulatively \
  tests/test_yield_disposition.py::test_origin_unattributed_recirculation_counts_unique_mass_once \
  tests/test_yield_disposition.py::test_hour_rollback_snapshot_owns_gross_and_cumulative_state
4 passed in 2.16s

adversarial split/merge/recirculation and nested rollback-ownership probe
PASS

adversarial two-new-fragment then recirculation probe
PASS

git diff --check
clean
```

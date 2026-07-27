# Staged yield-disposition fix 4 — typed origin at source

## TL;DR

- External material loads now require typed `material_origin` (`feedstock` or `reagent`) and fail closed when unstamped.
- Origin follows ledger moves and chemistry transitions; the commit observer consumes typed debit atoms, never account-name inference.
- Disposition supports `tracked`, sanctioned `pool_ratio`, and typed `OriginUnresolvedError`; every emitted row carries its method.
- Canonical lunar, Mars, and CI runs close with only `tracked` rows; the Na pool test closes at the known 2:1 input ratio.
- Focused accounting, commit, runner, shuttle, serialization, roundoff, and mol-native guards pass; no golden was regenerated.

## Producers stamped

AST sweep over active `simulator/`, `engines/`, and `tests/`: 259
`load_external` / `load_external_mol` calls; the only unstamped call is the
intentional fail-closed test at `tests/test_yield_disposition.py`.

Runtime producers:

| Producer | Origin |
|---|---|
| fO2/O2 bubbler external reservoir load | `reagent` |
| batch additive reservoir loads | `reagent` |
| `_load_ledger_account` cleaned melt, residual, and terminal feedstock loads | `feedstock` |
| Stage 0 volatile feed | `feedstock` |
| Stage 0 controlled O2 oxidant | `reagent` |
| Stage 0 carbonate feed | `feedstock` |
| Stage 0 process-gas CO2 | `reagent` |
| Stage 0 carbon-cleanup non-process-gas feeds | `feedstock` |
| Stage 0 perchlorate feed | `feedstock` |
| C7 external Al credit | `reagent` |

All active test producers were stamped explicitly. An AST comparison against
the prior staged versions proves the 44 mechanically updated test files are
semantically identical after removing only the new `material_origin` keyword.

## Threading path

1. `AtomLedger.load_external*` validates the typed origin, stores it on
   `MaterialLot`, and records per-element external input atom totals.
2. `AtomLedger.apply` derives debit origin from live typed balances, enriches
   debit and credit lots, allocates conserved credit atoms with the existing
   per-element transition math, and commits typed balances atomically with the
   physical projection.
3. Normal `move`/`transfer` operations preserve the source balance. Explicit
   reagent typing remains only for genuine negative credit-line draws; normal
   `draw_*_reagent_to_process` transfers preserve the reservoir origin.
4. `_observe_reagent_provenance_transition` sums reagent-origin atoms from
   transition debits and feeds the existing atom-conserving credit allocator.
5. Yield disposition reads only ledger external-origin inputs, terminal typed
   balances, unresolved balances, and attribution methods.

No origin decision uses account membership, account names, position, ordering,
or a default-bin complement.

## Three-tier contract

- `tracked`: one exact typed origin, an explicitly typed debit, or a complete
  withdrawal whose full mixed typed balance is known.
- `pool_ratio`: only an explicitly marked, physically amalgamated pool may make
  a partial proportional withdrawal. The implementation records the sanctioned
  method on surviving and withdrawn balances.
- unknown: unstamped external loads raise `MaterialOriginError`; an unresolvable
  track or an unsanctioned partial mixed-origin withdrawal raises
  `OriginUnresolvedError`.

Pool closure is enforced by the in-code derivation
`W*F/(F+R) + W*R/(F+R) = W`.

## Canonical closure and attribution receipts

All listed rows have an `attribution_method`.

| Scenario | Feedstock max residual | Reagent max residual | `tracked` fraction / links / melt / reagent / streams | `pool_ratio` | unknown |
|---|---:|---:|---:|---:|---:|
| Lunar | `2.220446049250313e-16` | `0.0` | `12 / 32 / 12 / 0 / 30` | `0` | `0` |
| Mars | `2.220446049250313e-16` | `0.0` | `13 / 19 / 10 / 2 / 16` | `0` | `0` |
| CI | `2.220446049250313e-16` | `0.0` | `12 / 18 / 9 / 1 / 14` | `0` | `0` |

The synthetic Na amalgamated-pool test loads `1.0 kg` feedstock Na and
`0.5 kg` reagent Na, withdraws `0.75 kg`, reports `2/3` feedstock and `1/3`
reagent origin, labels fraction/reagent/link/stream rows `pool_ratio`, and
closes both feedstock and reagent residuals at `0.0`.

## Verification

- `tests/test_yield_disposition.py`: 15 passed.
- Canonical lunar schema plus Mars/CI typed-origin closure: 3 passed.
- Accounting/admissibility/kernel commit/runner serialization batch:
  98 passed; one unrelated existing failure,
  `data/flowsheet.yaml:1 byte=0x80`, from the broad data-YAML hygiene guard.
- Required mol-native artifact guards isolated: 2 passed.
- Recovered-reagent zero-sum, no-double-spend, and real S1C Na recycle:
  3 passed.
- `compileall simulator tests` and `git diff --check`: passed.

## Golden churn

No golden was regenerated. Comparison against the lunar, Mars, and CI runner
goldens changed only the new top-level `yield_disposition` payload; no other
top-level output changed.

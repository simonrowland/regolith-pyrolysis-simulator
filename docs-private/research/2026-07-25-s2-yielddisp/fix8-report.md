# Fix8 staged yield-disposition closure

## Status

READY. Changes staged; no commit or push.

## Findings closed

- P1 refused-hour rollback: `snapshot_atom_ledger()` now clones the ledger's
  complete nested mutable-container graph in one authoritative implementation.
  Both `PyrolysisSimulator.step()` and `RunExecutor` use it, so physical
  balances, gross events, provenance debt, origin maps, external-origin maps,
  and pool declarations roll back together.
- P2 dust-edge method: campaign replay marks `pool_ratio` from the allocation's
  actual positive shares, independent of liveness-band classification.
- P2 extreme finite allocator inputs: stable ratio arithmetic avoids
  intermediate overflow; conversion, total, share, and closure overflow paths
  raise `PoolWithdrawalError`.

## Regression coverage

- Two consecutive real terminal refusals through `SimSession.advance()`,
  followed by a successful retry, equal the same run with refused hours never
  attempted.
- Direct `RunExecutor` refusal contaminates gross flow, external reagent
  provenance, and a first pool declaration before raising; rollback restores
  the complete pre-hour ledger state.
- The below-band second-class allocation reports `pool_ratio`.
- `5e307 + 5e307` allocation remains finite; non-representable totals and a
  10,000-digit integer raise typed.

## Verification

- `.venv/bin/python -m pytest tests/test_yield_disposition.py tests/accounting/test_ledger_api.py tests/accounting/test_ledger_admissibility_hardfail.py -q`
  — 109 passed.
- `.venv/bin/python -m pytest tests/test_bughunt_phys_regressions.py -k 'typed_refusal or refusal_snapshot' -q`
  — 5 passed.
- `git diff --check` — clean before staging.

# Fix9 — yield-disposition Class A/B/C closure

## TL;DR

- Class A now uses the ledger's configured per-element atom-conservation tolerance for origin withdrawals.
- Accepted origin shortfall is carried as `origin_unattributed`; no physical atoms disappear.
- A withdrawal beyond that band still raises `OverdraftError` caused by `PoolWithdrawalError`.
- Class B maps only the proven C/K/Mg/Na reagent reservoirs and C7 Al credit; unknown accounts still crash.
- Reagent atoms remain outside feedstock fractions and close through `reagent_cycle`.
- Class C regenerated exactly three fixtures; each diff only adds executable `yield_disposition`.
- All authored focused laptop gates pass; Studio bakeout, web QA, and full gate remain deferred.
- Fix9 paths are staged explicitly; no commit or push was performed.

## Class A boundary proof

`AtomLedger._project_origin_transition()` now derives one withdrawal tolerance
from the strictest physical element gate already configured on the ledger:
`_atom_tolerance_for_element(element, atom_tolerance_mol,
mass_tolerance_kg)`, plus the pre-existing origin and balance dust terms.
The same value decides full withdrawal and is passed to
`allocate_pool_withdrawal()`.

The allocator rejects only when `withdrawal - available` exceeds that derived
tolerance. An accepted numerical excess clamps to the available origin pool;
the ledger records the difference explicitly in `origin_unattributed` and
propagates it to the credited account.

Paired regression:

- `0.5e-6 mol-atoms` origin shortfall with a configured `1e-6 mol-atoms`
  tolerance succeeds. Destination accounting contains `1.0` feedstock
  mol-atoms plus `0.5e-6` unattributed mol-atoms, exactly matching physical
  destination atoms.
- `2.0e-6 mol-atoms` shortfall with the same configuration fails with
  `OverdraftError`; its cause is `PoolWithdrawalError`.

This does not grant a reservoir-wide origin bypass. Every origin shortfall is
bounded by the same per-element conservation tolerance, normal-account species
policy still runs, and a reservoir overdraft beyond the origin band fails
before mutation.

## Class B destination proof

Write-site tracing found the live additive reservoirs seeded as
`reservoir.reagent.{C,K,Mg,Na}` and the imported C7 Al line seeded as
`process.c7_al_credit`, all with `material_origin="reagent"`.

| Explicit account | Rev5 terminal home |
|---|---|
| `reservoir.reagent.C` | `charge_unprocessed` |
| `reservoir.reagent.K` | `charge_unprocessed` |
| `reservoir.reagent.Mg` | `charge_unprocessed` |
| `reservoir.reagent.Na` | `charge_unprocessed` |
| `process.c7_al_credit` | `charge_unprocessed` |

These balances are unused imported inputs still parked in their source/credit
accounts. Origin-first projection excludes their atoms from the feedstock
denominator regardless of terminal home. A five-case regression proves each
account appears in terminal streams while its element appears only in
`reagent_cycle`, with input equal to terminal-excluded atoms and zero
reconciliation residual.

No `reservoir.reagent.*` prefix catch-all was added. The explicit
`reservoir.reagent.future` nonzero case and the existing future process-account
case both retain the typed `unknown nonzero terminal account` refusal.

## Class C fixture inventory

`scripts/regenerate_runner_goldens.py` emitted exactly:

| Fixture | Added lines | Removed lines | Structural comparison |
|---|---:|---:|---|
| `lunar_mare_low_ti_C0_24h.json` | 1713 | 0 | added `yield_disposition`; no removed/changed top-level keys |
| `mars_basalt_C2A_12h.json` | 1407 | 0 | added `yield_disposition`; no removed/changed top-level keys |
| `ci_carbonaceous_chondrite_C2B_12h.json` | 1298 | 0 | added `yield_disposition`; no removed/changed top-level keys |

All three payloads are executable `status="ok"` outputs with yield schema
`5.0`. Their maximum closure residual fractions are respectively
`3.251124716996401e-16`, `1.1827166789314194e-16`, and
`1.1374317709559755e-16`.

## Focused test receipts

- `117 passed` — `test_yield_disposition`, ledger API, and ledger
  admissibility/hard-fail unit files.
- `12 passed` — full `test_physics_trace`, monotonic
  extraction-completeness node, and both `headspace_po2` files.
- `6 passed, 28 deselected` — `test_run_executor.py -k c6`.
- `2 passed` — C7 schema parity and transport-refusal runner-smoke nodes.
- `3 passed, 64 deselected` — executable runner golden fixture comparisons.
- `git diff --check` — clean.

A supplementary broader accounting/yield sweep reached `216 passed` with no
failure, then one long case remained in `copy.py`; it was manually interrupted
after `462.19 s` and is recorded as inconclusive, not green.

## Self-review

- **True-overdraft masking:** rejected. The accepted band is the ledger's
  existing element tolerance, not `allow_negative`; the paired beyond-band
  reservoir case fails typed.
- **Feedstock-yield theft:** rejected. Explicit reagent-origin regression leaves
  the fraction table containing only the independently loaded feedstock
  element, while every reagent element reconciles outside the denominator.
- **Unrelated golden drift:** rejected. Index-versus-working JSON comparison
  found one added top-level key and zero removed or changed keys in each of the
  three generated fixtures.

## Deferred Studio gates

- Prior bakeout hang: deferred to the controller's Studio run.
- `web_functional_qa@serial`: deferred to the controller's Studio run.
- Full pytest gate: deferred to the controller's Studio run.

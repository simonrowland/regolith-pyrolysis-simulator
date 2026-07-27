# S2 yield-disposition diagnose-then-fix round 3

## TL;DR

- Serialization now runs inside the same guarded construct-then-attach boundary; a serialization-time `LookupError` preserves the primary failure.
- Reagent-inventory disposition refuses any physical atoms not covered by recorded feedstock plus reagent provenance; the offsetting-origin swap is pinned.
- Mars and CI were observer recording gaps: explicit Stage-0 source-account debits now propagate real provenance and both live closures return zero residual.
- Focused tests pass 18/18, Mars/CI live tests 2/2, mol-native guards 2/2; compile and diff checks pass.

## Diagnosis before code

The canonical Mars C2A and CI C2B runs were executed with a read-only
observer wrapper that recorded each committed pure-reagent debit before the
existing observer ran. Both failures had source identity at the commit
boundary; neither required inference or a narrowed payload contract.

| Scenario | Missing observer source | Element | Debited source mass | Commit transition | Element-bearing terminal credit | Pre-fix receipt |
|---|---|---:|---:|---|---|---|
| `mars_basalt_C2A_12h` | `reservoir.stage0_process_gas` | C | 21.4309031968 kg | `stage0_boudouard_carbon_cleanup` | `terminal.offgas` | observer-recorded source before debit: 0 kg; reagent input 4281.98344824 mol-atoms, terminal excluded 2497.7104321, residual -0.416693113765 |
| `mars_basalt_C2A_12h` | `reservoir.stage0_process_gas` | O | 57.0931679704 kg | `stage0_boudouard_carbon_cleanup` | `terminal.offgas` | observer-recorded source before debit: 0 kg |
| `ci_carbonaceous_chondrite_C2B_12h` | `reservoir.stage0_oxidant` | O | 71.6926415422 kg | `stage0_complete_oxidation_carbonaceous_organic` | `terminal.offgas` (`process.solid_char_carbon` carries no O) | observer-recorded source before debit: 0 kg; reagent input 4481.07016327 mol-atoms, terminal excluded 0, residual -1 |

Temporary honest propagation of only those committed source debits produced:

- Mars: C input = terminal = 4281.98344824 mol-atoms; O input =
  terminal = 3568.54603228 mol-atoms; reagent residual 0.
- CI: O input = terminal = 4481.07016327 mol-atoms; reagent residual 0.

Decision: recording-gap branch (option i). The production observer now treats
the two Stage-0 source accounts as direct non-feedstock provenance sources and
allocates each debited element over that same committed transition's
element-bearing credits.

## Finding closure

| Pinned item | Resolution | Regression |
|---|---|---|
| Serialization replaces primary failure | `_runner_failure_result()` builds the complete primary payload with `yield_disposition: null`, then performs snapshot lookup, disposition construction, and `_json_safe()` serialization inside one `try` before attaching. | A dict whose `items()` raises `LookupError("disposition serialization unavailable")` leaves `status=failed`, `reason=primary_failure`, and the primary message intact while attaching the secondary error. |
| Default-bin cancellation | `process.reagent_inventory` now reconciles physical atoms against recorded feedstock-recovered atoms plus observer-recorded reagent atoms. Any remainder above the derived tolerance raises `OriginUnresolvedError`; it is never assigned by complement. | One mol feedstock Na parked in reagent inventory plus one mol reagent Na vented with neither provenance surface now refuses at `process.reagent_inventory.Na`, despite independently cancelling cycle totals. |
| Mars/CI live closures | `_observe_reagent_provenance_transition()` seeds provenance from the explicit committed `reservoir.stage0_process_gas` and `reservoir.stage0_oxidant` debits, then uses the existing element-conserving credit allocator. | A two-case live regression asserts `status=ok`, feedstock closure at or below 5e-14, reagent closure at or below 5e-14, and the exact reagent element sets (Mars C/O; CI O). |

## Verification

```text
Focused yield-disposition + failure-envelope suite:
18 passed in 0.92s

Canonical Mars C2A + CI C2B live closure regression:
2 passed, 7 warnings in 30.12s

Mol-native artifact guards:
2 passed in 0.43s

py_compile:
pass

git diff --check:
pass
```

No runner golden fixtures were regenerated. All requested source, test, and
report changes are staged; no commit or push was performed.

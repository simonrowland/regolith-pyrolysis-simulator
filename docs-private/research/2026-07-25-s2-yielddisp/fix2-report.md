# S2 yield-disposition review-fix round 2

## TL;DR

- Failure disposition construction now has one guarded boundary; primary status, reason, and error always survive, with the secondary exception class and text attached.
- Stage-0 reagent origin now comes only from the runtime provenance observer: its state is cleared before Stage 0, retained afterward, and the account-membership replay is deleted.
- Roundoff coverage now probes 0.99x and 1.01x the derived `1 mol-atoms * 5e-14` boundary.
- Fast focused checks pass 16/16, live three-scenario schema passes 1/1, mol-native guards pass 2/2, and staged diff check passes.

## Finding closure

| Finding | Resolution | Regression |
|---|---|---|
| P1 failure envelope | Removed the pre-`try` ledger eligibility lookup. `build_yield_disposition()` and snapshot argument construction now run inside one `try`; every caught exception appends `yield disposition unavailable: <class>: <text>`. | A simulator whose `atom_ledger` property raises `LookupError("ledger unavailable")` retains `status=failed`, `reason=primary_failure`, and the primary error. |
| P1 origin authority | Moved provenance-map clearing to before the new ledger is seeded, preserving observer-recorded Stage-0 terminal lineage. Deleted Stage-0 replay and all account/name-based replay seeding. | Feedstock-origin carbon externally loaded into `process.reagent_inventory` would be classifiable by membership, but without observer provenance now raises `OriginUnresolvedError`. |
| P2 tolerance edge | Retained the production threshold `total * 5e-14` and added paired probes immediately below and above it. | `0.99 * boundary` emits no reagent row; `1.01 * boundary` is classified as reagent-origin and raises the typed missing-input error. |

## Verification

```text
MPLCONFIGDIR=/private/tmp/rps-yielddisp-mpl .venv/bin/python -m pytest -q -n 0 \
  tests/test_yield_disposition.py \
  tests/test_runner_smoke.py::test_runner_preserves_primary_failure_when_poison_enrichment_fails \
  tests/test_runner_smoke.py::test_runner_failure_preserves_primary_when_yield_disposition_raises \
  tests/test_runner_smoke.py::test_runner_failure_preserves_primary_when_atom_ledger_lookup_raises \
  tests/test_runner_smoke.py::test_runner_detail_fallback_preserves_refused_status_and_live_rows
16 passed

MPLCONFIGDIR=/private/tmp/rps-yielddisp-mpl .venv/bin/python -m pytest -q -n 0 \
  tests/test_runner_smoke.py::test_runner_schema_shape_contract
1 passed, 7 warnings

MPLCONFIGDIR=/private/tmp/rps-yielddisp-mpl .venv/bin/python -m pytest -q -n 0 \
  tests/test_artifact_guards.py::test_simulator_has_no_forbidden_internal_kg_mutations \
  tests/test_artifact_guards.py::test_forbidden_internal_kg_mutation_patterns_match_samples
2 passed

.venv/bin/python -m py_compile simulator/core.py \
  simulator/accounting/yield_disposition.py simulator/runner/__init__.py \
  tests/test_yield_disposition.py tests/test_runner_smoke.py
pass

git diff --staged --check
pass
```

All requested changes are staged. No commit created.

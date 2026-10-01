# REVIEW — redox chunk 1 (shared noop threshold)

- **Reviewer:** regolith-empirical (frontier of record)
- **Seat:** `/workspace/repos/wt/slot-b565`
- **Branch tip:** `2120e8ffa01b758252ee0e019f65f3783ec90aa9` on `empirical/review-stack2-redox-c2` (tracks `review/stack2-redox`)
- **Range:** `0ba4fb428..9277ef6ed` (two commits)
  - `80d737365` Align Fe redox noop threshold
  - `9277ef6ed` Move redox threshold to shared module
- **REQ:** `/workspace/ferry-inbox/REQ-reviews-from-regolith-physics-2026-09-30.md` §1; design r3 §6 chunk 1 “One noop”
- **Date:** 2026-09-30 ~20:36–20:50 ET
- **Mode:** read-only; extracts not edited; no force-push. Targeted tests only (no full W3).

## Scope

Unify the Fe-redox provider’s former `NOOP_MOL = 1e-12` with the core classifier’s `OXYGEN_RESERVOIR_NOOP_MOL = 1e-15`, defined once in `simulator/fe_redox.py`. Independent check that the relation is derived, the mutation goes red, and no provider debit/credit comparison relied on the old larger cushion in a load-bearing way.

## Mapping to design / REQ

| Claim | Tip evidence |
| --- | --- |
| Single shared definition in `simulator/fe_redox.py` | `OXYGEN_RESERVOIR_NOOP_MOL = 1e-15` at `fe_redox.py:9`; removed from `core.py`; provider imports from `fe_redox` (not core) after `9277ef6ed` |
| Provider comparisons at 1e-15 | `fe_redox_respeciation.py` lines 144, 278, 312–314, 338–339, 351, 377 all use `OXYGEN_RESERVOIR_NOOP_MOL` |
| No leftover local `NOOP_MOL = 1e-12` | `rg` on provider: no local constant; only shared import |
| Behaviour change is the threshold only | Diff is constant rename/relocate + test; no other control-flow change in `80d737365` / `9277ef6ed` |
| Acceptance relation | `5e-14` mol Fe₂O₃ is not `no_oxidized_iron`; `1e-15` mol total Fe is (`test_provider_uses_shared_noop_for_trace_iron`) |

## Provider debit/credit cushion (load-bearing check)

REQ asked whether any former `+ 1e-12` debit/credit tolerance was load-bearing for float round-off that `1e-15` would now refuse.

| Site | Role of NOOP | Judgment |
| --- | --- | --- |
| L144 `total_fe_mol <= NOOP` | Inventory presence | Intentional threshold change (design acceptance) |
| L278 `abs(delta) <= NOOP` | No-op delta | Intentional; traces in (1e-15, 1e-12] now apply |
| L312–314 / L338–339 debit vs inventory + NOOP | Round-off cushion on FeO / O₂ debit | Tighter. At O(1) mol Fe, float64 ulp ≈ 2×10⁻¹⁶; 1e-15 still covers ~5 ulp. No tip fixture shows a refuse that only 1e-12 absorbed |
| L351 partial remainder | Marks partial when remainder > NOOP | Slightly more sensitive; not a refuse path |
| L377 Fe₂O₃ debit vs inventory + NOOP | Same as FeO debit | Same ulp reasoning |

No evidence of a load-bearing debit/credit case that fails solely because of the tighter cushion. Non-blocking note only: at very large inventories (~10⁴ mol) ulp approaches 1e-12; that regime is outside this chunk’s fixtures and outside the design’s stated acceptance.

## Targeted tests (VPS, `-o addopts=`)

| Test | Result |
| --- | --- |
| `tests/chemistry/test_builtin_fe_redox_respeciation_provider.py::test_provider_uses_shared_noop_for_trace_iron` | **passed** |

## Mutation (temporary local edit, reverted)

Restore provider threshold to `1e-12` (local override after import). Re-run the shared-noop test:

- **Goes red:** `5e-14` Fe₂O₃ returns `respeciation_status == "no_oxidized_iron"` (assert `!=` fails).
- Tree restored with `git checkout -- engines/builtin/fe_redox_respeciation.py` (clean after).

## Non-blocking notes

- Atom-balance asserts in the same provider test file still use `tol=1e-12` / `abs=1e-12`; those are test tolerances, not the inventory noop, and are out of chunk scope.
- Design r3 L622 still cites `simulator/core.py:826` for the constant; after `9277ef6ed` the definition lives in `fe_redox.py` (core re-exports via import). Doc lag only.

## Verdict

VERDICT: LAND 2120e8ffa01b758252ee0e019f65f3783ec90aa9

— regolith-empirical

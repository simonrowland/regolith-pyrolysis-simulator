# REVIEW — redox chunk 2 (unavailable Fe-FeO buffer equality)

- **Reviewer:** regolith-empirical (frontier of record)
- **Seat:** `/workspace/repos/wt/slot-b565`
- **Branch tip:** `2120e8ffa01b758252ee0e019f65f3783ec90aa9` on `empirical/review-stack2-redox-c2` (tracks `review/stack2-redox`)
- **Range:** `9277ef6ed..2120e8ffa` (two commits)
  - `c6ae532df` Return absent Fe-FeO buffer equality
  - `2120e8ffa` Preserve absent redox equality in consumers
- **REQ:** `/workspace/ferry-inbox/REQ-redox-c2-from-regolith-physics-2026-09-30.md`; design r3 §6 chunk 2 “Unavailable buffer, returned” (~L854); M2 table ~L639
- **Prior design confirm:** `REVIEW-redox-design-r3-confirm-2026-09-30.md` **CONVERGED**
- **Date:** 2026-09-30 ~20:36–20:50 ET
- **Mode:** read-only; extracts not edited; no force-push. Targeted tests only (no full W3).

## Scope

When retained metal + FeO coexist but `a_FeO` is non-positive, melt equality must be **absent** with basis `fe_feo_buffer_activity_unavailable` — not `log10(P_transport)`, not Kress inverse. Absence must survive `_current_melt_redox_fO2_log` (no stale cache / −9) and consumers (respeciation skip; equilibrium `float(None)` guard).

## Mapping to design / REQ

| Claim | Tip evidence |
| --- | --- |
| M2 unavailable → absent equality, basis `fe_feo_buffer_activity_unavailable` | `core.py` ~7111–7130: records domain with `fO2_log=None`, that basis, `status_override='out_of_domain'`, reason names `kress91_inverse_not_evaluated`; `return None` before reservoir / Kress path |
| Typed basis in `RedoxDomainRecord` | `fe_redox.py` Literal includes `fe_feo_buffer_activity_unavailable` |
| Does not copy transport / call Kress | Acceptance test monkeypatches Kress inverse to raise; fixture transport `1e-8` (would be −8 if copied) |
| Absence survives public reader | `_ferrous_free_scalar_absent` includes the new basis; `_current_melt_redox_fO2_log` returns `None` before cached scalar / −9 fallback |
| Respeciation consumer | `unavailable_buffer_activity` → `skipped_fe_feo_buffer_activity_unavailable` / `fe_feo_buffer_activity_unavailable_has_no_equality` |
| Equilibrium `float(None)` | `equilibrium.py` uses `_ferrous_free_scalar_absent()` before `float(raw_intrinsic_fO2_log)` |
| Endpoint gas-copy still reachable for OTHER cases | Near-ferric / `endpoint_clamped` gas-owned blocks at ~7421–7440 and ~7461+ unchanged; M2 unavailable returns earlier. `test_nonzero_release_with_no_ferric_inventory_follows_the_gas` **passed** (chunk 4 removes those blocks later) |

## Consumer scan (substituting a number for this absence)

Readers that gate on `_ferrous_free_scalar_absent()` (now includes this basis) preserve absence:

| Consumer | Behaviour on tip |
| --- | --- |
| `_current_melt_redox_fO2_log` | Returns `None` (no cache / −9) |
| `equilibrium.py` intrinsic path | Keeps `None`; skips dissociation clamp |
| `evaporation.py` freeze-gate / liquidus | Refuses rather than inventing 0/−9 |
| `extraction.py` MRE controls | Passes `melt_fO2_log=None` |
| `reduced_real_determinism._authoritative_melt_fO2_log` | Raises `PT0InvalidControls` (“has no equilibrium fO2”) |
| Respeciation writer | Skips with dedicated status/reason |

No remaining melt-fO₂ / `_last_redox_domain` consumer on the tip path was found that substitutes a numeric equality for `fe_feo_buffer_activity_unavailable`. Warning/refusal **strings** still often say `ferrous_free_lower_bound` (shared helper name); they do not invent a number — non-blocking wording lag for chunk 5’s “equality absent” rename.

## Acceptance fixture (design L854)

Metal 1 mol, FeO 1 mol, ferric 0, forced non-positive activity, transport `1e-8`, transfer 0:

| Check | Observed |
| --- | --- |
| Equality | `None` |
| Value not −8 | `fO2_log != -8.0` |
| Basis | `fe_feo_buffer_activity_unavailable` (not `no_melt_redox_buffer`) |
| Inverse not called | monkeypatch raises if entered |
| Interface unchanged | stays at fixture `4.0e-7` |

## Targeted tests (VPS, `-o addopts=`)

| Test | Result |
| --- | --- |
| `test_unavailable_fe_feo_buffer_returns_absent_without_gas_or_kress` | **passed** |
| `test_unavailable_buffer_absence_does_not_fall_back_to_cached_scalar` (−8.0 and None) | **2 passed** |
| `test_unavailable_buffer_absence_survives_one_simulated_hour` | **passed** |
| `test_nonzero_release_with_no_ferric_inventory_follows_the_gas` (gas-copy other case) | **passed** |

## Mutations (temporary local edits, both reverted)

1. **Remove** the early return / domain record at the unavailable-activity site (`c6ae` change).  
   - **Goes red:** `reported=-8.0, basis='no_melt_redox_buffer'` — exact design reproduction.
2. **Revert** `_ferrous_free_scalar_absent` to ferrous-free-only (`2120` consumer change).  
   - **Goes red:** stale fallback returns `-8.0` (cached) and `-9.0` (default when cache None).

Both restored with `git checkout -- simulator/core.py` (clean after).

## Non-blocking notes

- Refusal/warning text in evaporation / PT-0 / equilibrium still names `ferrous_free_lower_bound` when the shared absent helper fires for this new basis. Behaviour is correct (no numeric substitute); rename belongs with chunk 5’s absence consolidation.
- `no_melt_redox_buffer` remains a live basis for other gas-owned paths until chunk 4 — intentional per design (“Leave 7437-7459 until chunk 4”).

## Verdict

VERDICT: LAND 2120e8ffa01b758252ee0e019f65f3783ec90aa9

— regolith-empirical

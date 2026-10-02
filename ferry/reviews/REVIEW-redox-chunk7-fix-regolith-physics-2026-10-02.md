# REVIEW — redox chunk-7 fix (M2 surface_activity_unavailable on apply)

- **Reviewer:** regolith-empirical (frontier delta of record)
- **Seat:** `/workspace/repos/wt/slot-z14`
- **Tip:** `364d375114b71fd72c13bbb1af0335123c007115` on `review/redox-c7-fix`
- **Prior REVISE tip (ancestor):** `910ec1779330c2478f8418eff8ede5c8f39fc8d6` (verified `merge-base --is-ancestor`)
- **Range:** one commit `910ec1779..364d37511` — `Handle unavailable M2 activity during oxygen apply`
- **Files:** `simulator/core.py` +54/−20; `tests/test_redox_authority_floor.py` +70
- **REQ:** `/workspace/ferry-inbox/REQ-redox-c7-fix-from-regolith-physics-2026-10-02.md`
- **Prior REVIEW (must fold):** `REVIEW-redox-chunk7-regolith-physics-2026-10-02.md` (REVISE — apply re-raise after shadow flag)
- **Ack:** `STATUS-req-redox-c7-fix-acked-2026-10-02.md`
- **Date:** 2026-10-02 ~17:19–17:30 ET
- **Mode:** read-only for product extracts; reviewing product code; targeted VPS unit tests only (no full W3 / full pytest). No push of empirical feature branches.

## Scope

Answers prior P1: when M2 surface activity is unavailable (`_fe_saturation_bound_fO2_log → None`), shadow already predict-and-flagged zero transfer, but `_apply_oxygen_reservoir_exchange` re-entered `_oxygen_interface_state` unprotected and aborted the hour. Fix keeps transfer at zero, publishes regime `surface_activity_unavailable` with the gas interface, skips the repeated root, and pins a full-hour regression.

## Attack results (NOT-FIXED lens)

### (1) Every apply-path consumer of shadow flagged states — **PASS**

**Raise inventory (`oxygen_interface_activity_fixed_point_nonconverged`):** nine sites in `simulator/core.py` — finite-root M2 surface (no root / invalid a_FeO / residual / derivative interval / derivative ≤0 / derivative non-finite), shadow successor Fe-FeO (no root / residual), shadow evaluate (omitted pressure derivative). Shadow catch at ~6606 is **reason-only** (all nine). Apply gate at ~11607 keys the primary flag `oxygen_exchange_activity_fixed_point_nonconverged` (covers all nine), not the secondary string.

**Prior hole closed (seat probes tip `364d37511`):**
```
A_SHADOW ok 0.0 [activity_fixed_point_nonconverged, surface_activity_unavailable, candidate_solve_incomplete]
A_APPLY  ok 0.0 iface=2e-07 regime=surface_activity_unavailable
A_HOUR   ok True exchange=0.0 regime=surface_activity_unavailable
B2_TIP_SKIP_CALLS 0   # _oxygen_interface_state never entered when flagged
```

**Other flagged states still fail-closed without abort:**
| Flag path | Apply evidence |
| --- | --- |
| `oxygen_exchange_root_tolerance_unmet` | `test_exponential_interface_root_miss_is_predicted_and_flagged` **passed**; seat probe D apply ok with both root-miss + refinement_exhausted flags |
| `oxygen_exchange_refinement_exhausted` | `test_exponential_refinement_exhaustion_is_predicted_and_flagged` **passed** (apply commits, atom drift ≤5e-12) |
| activity_fixed_point (primary) + candidate_solve_incomplete | full-hour test + probes A/B1 |
| ferrous-free / unavailable-buffer | `test_ferrous_free_refinement_prediction_preserves_bound_consumers`, `test_unavailable_buffer_activity_failure_is_predicted_and_flagged` **passed** |
| E0 hard-vacuum | `test_exponential_e0_returns_without_interface_roots` **passed** |

**Only apply re-entry to `_oxygen_interface_state`:** the gated call at ~11628 (else branch). Post-transfer `_fe_saturation_bound_fO2_log` at ~11716 returns `None` and pops activity — does **not** raise. No second unprotected root.

**Non-blocking label nuance:** secondary flag `surface_activity_unavailable` is string-gated to *"M2 surface FeO activity fixed point returned no root"* only; invalid/residual/derivative/successor failures still skip apply via the **primary** flag and still publish regime `surface_activity_unavailable`. Fail-closed is correct; secondary-flag coverage is slightly narrower than the regime label. Not a re-raise hole.

**Out of P1:** non-activity `OxygenInterfaceConfigurationError` reasons (invalid pressure/transport/config) remain hard aborts by design — shadow does not catch them.

### (2) Zero transfer + visible flag — **PASS**

Zero transfer is the right prediction when activity cannot certify a surface pressure: no physical J_m without a_FeO; holding a stale prior interface would invent drive. Tip forces `transfer_mol = 0`, gas `interface_pO2_bar`, regime `surface_activity_unavailable`.

**Hour-summary visibility (seat):** `HourSnapshot.oxygen_reservoir` carries:
- `interface_pO2_limiting_regime == "surface_activity_unavailable"`
- `shadow_oxygen_transfer.prediction_flags` with both primary + `surface_activity_unavailable` + `candidate_solve_incomplete`
- `_last_oxygen_interface_diagnostic.limiting_regime` / `surface_activity_available=False`

Ledger unchanged across exchange; atom drift unchanged to `5e-12` (pinned by `test_m2_unavailable_surface_activity_completes_full_hour_flagged`).

### (3) Ordinary (activity available) hour unchanged vs `910ec1779` — **PASS**

Diff is only: (a) secondary flag append in shadow on the specific no-root string; (b) apply if/else gate around the **same** `_oxygen_interface_state(transport_pO2, intrinsic_fO2_log=base_fO2_log)` call; (c) publish path when `interface_diagnostic is None`. Ordinary hours take the else branch → identical call.

**Bit-identical M2 fixture apply** (tip vs detached worktree at `910ec1779`, cleaned after):
```
TIP   transfer=-0.00033394887591478475  iface=1.0046216890813919e-07  regime=gas_side_limited  iface_calls=1  flags=[]
PRIOR transfer=-0.00033394887591478475  iface=1.0046216890813919e-07  regime=gas_side_limited  iface_calls=1  flags=[]
```

RH03[2,24] not re-run on VPS (same else-branch code path; heavy vapour-batch). Feasibility note only — not a blocker.

### (4) Mutation: restore the raise → red — **PASS**

Seat mutation: strip `prediction_flags` from shadow result so apply takes the else branch while `_fe_saturation_bound_fO2_log → None`:
```
B3_MUTATION_RED  oxygen_interface_activity_fixed_point_nonconverged
B3H_MUTATION_RED OxygenInterfaceConfigurationError (hour abort)
```
Restoring the unprotected re-entry reproduces the prior REVISE hole. Worker claim that restoring the raise turns the new full-hour test red is consistent with this probe.

## Targeted VPS results (tip `364d37511`)

| Nodes | Result |
| --- | --- |
| `test_m2_unavailable_surface_activity_completes_full_hour_flagged` | **passed** |
| `test_exponential_m2_activity_failure_is_predicted_and_flagged` | **passed** |
| `test_exponential_refinement_exhaustion_is_predicted_and_flagged` | **passed** |
| `test_exponential_interface_root_miss_is_predicted_and_flagged` | **passed** |
| `test_exponential_e0_returns_without_interface_roots` | **passed** |
| `test_unavailable_buffer_activity_failure_is_predicted_and_flagged` | **passed** |
| `test_ferrous_free_refinement_prediction_preserves_bound_consumers` | **passed** |
| `test_exponential_linear_relaxation_is_exact` (6) | **passed** |
| `test_exponential_binding_caps_stop_outward_and_publish_successor` | **passed** |
| Seat probes A–D (apply/hour/mutation/ordinary/root-miss) | **all green as above** |

Known out of scope (per REQ): 170 h vapour-batch timeout / same-tick headspace batch CPU (evap-batch-reuse).

## Non-blocking notes

- Secondary `surface_activity_unavailable` flag string-gated; primary flag + regime cover all activity_fixed_point sites.
- RH03[2,24] ordinary-hour numeric re-diff deferred (code-path identity + M2 bit-identical).
- Seat left idle on `review/redox-c7-fix` at tip.

## Verdict

**LAND** `364d375114b71fd72c13bbb1af0335123c007115`

Prior P1 closed: apply no longer re-raises after shadow flags unavailable M2 surface activity; zero-transfer + gas interface + `surface_activity_unavailable` regime published on the hour; ordinary activity-available path unchanged vs `910ec1779`; mutation restores the abort. Predict-and-flag holds for root-miss, refinement exhaustion, and activity-fixed-point families on the apply path.

— regolith-empirical

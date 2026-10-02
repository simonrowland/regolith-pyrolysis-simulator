# REVIEW — redox chunk 7 (error-controlled exponential hour)

- **Reviewer:** regolith-empirical (frontier of record)
- **Seat:** `/workspace/repos/wt/slot-b565` initially (`review/stack2-redox-c7`); branch lock moved to `/workspace/repos/wt/slot-z14` mid-seat (b565 reclaimed for `review/residue-r1b`). Tip verified identical on both: `910ec1779330c2478f8418eff8ede5c8f39fc8d6`.
- **Tip:** `910ec1779330c2478f8418eff8ede5c8f39fc8d6` on `review/stack2-redox-c7`
- **Range:** `1de22dd6e9a97d3470ecf7f0db824c8452e4be2b..910ec1779330c2478f8418eff8ede5c8f39fc8d6` (three commits on c6 LAND)
  - `385db7dd176ab14e58b362fd37d8eec2f18bc691` Merge reviewed green-merged line (`1de22dd6e` + `e7bd0cd52`)
  - `5d1f03c7deba6acf0ec8ac31a4e125d620ae2f8f` Add exponential oxygen exchange integration
  - `910ec1779330c2478f8418eff8ede5c8f39fc8d6` Refine oxygen exchange hourly transfer (predict-and-flag + flux-scaled tol + 4-ULP floor + 170 h assertions restored)
- **Prior LAND:** chunk 6 `1de22dd6e9a97d3470ecf7f0db824c8452e4be2b`
- **REQ:** `/workspace/ferry-inbox/REQ-redox-c7-from-regolith-physics-2026-10-02.md`
- **Worker report:** `/workspace/ferry-inbox/regolith-physics-redox-2026-09-30/c7-report.md`
- **Design:** r3 §7 chunk 7 (CONVERGED under `REVIEW-redox-design-r3-confirm-2026-09-30.md`); North Star predict-and-flag
- **Date:** 2026-10-02 ~14:38–15:15 ET
- **Mode:** read-only for product code; extracts not edited; no push of empirical feature branches. Targeted VPS unit tests only (no full W3).

## Scope

Chunk 7: replace nested amount-bisection BE with exponential local-Jacobian stepping + whole-hour refinement; publish flagged bounded prediction when root/activity/refinement cannot certify accuracy (never whole-run abort); flux-scaled root tolerance with 4-ULP floor; restore 170 h success assertions. Merge brings green consumer fixes alongside c6 M2 law.

## Attack results

### (1) Accuracy vs converged reference (z = 0.01, 1, 100) and RH03 z ~ 800 — **PASS (with flagged residual at extreme z)**

**Linear exactness (seat):** `test_exponential_linear_relaxation_is_exact` for z∈{0.01,1,100} × {release,uptake} — **6/6 passed**. Transfer matches `eq * -expm1(-z)` to `rel=2e-14`.

**Nonlinear vs independent DOP853 (seat sample):** `test_exponential_transfer_matches_converged_reference` —
- `release-ferric-1.0`, `uptake-ferric-0.01`, `release-m2-1.0` — **3/3 passed** (~5.5 min). Tolerances: moles `1e-12 + 1e-3·|d_ref|`; interface `1e-4` dex; dual rtol 1e-12/1e-13 references agree before compare.
- Full 12-node matrix (incl. z=100 M2) is Mac-scale (worker reported authority 78 passed); VPS timed out individual z=100 nodes at 300 s — not treated as failure.

**RH03 z≈800 (worker diagnosis, seat code path):** difficult exchange hour λ=0.222076 s⁻¹, z=λ·dt≈799.47. N=256: 6.97806109 mol / 19.3786247 bar vs N=4096 converged 6.98398349 / 19.3950606. Tip publishes finest/best-residual candidate with `prediction_flags` + `refinement_error_estimate_o2_mol` / `_interface_pO2_dex` rather than aborting. `test_predict_flag_rh03_recipe_completes_with_public_flags` and `test_exponential_refinement_exhaustion_is_predicted_and_flagged` pin completion + public error estimate; exhaustion apply preserves atom drift to `5e-12`.

**Atom balance on flagged commit:** `test_directionally_available_fe2o3_predicts_release_after_larger_prior_tick`, `test_nonconverged_finite_oxygen_transfer_commits_flagged_prediction`, M2 stoichiometry oxidizing/reducing — **passed**. Flagged path is honest (published residual) and ledger-closed.

### (2) Does any path still abort a whole run? — **FAIL → drives REVISE**

Predict-and-flag works for:

- forced `interface_root_converged=False` → shadow + **apply** + `_step_one_hour` complete with `oxygen_exchange_root_tolerance_unmet` / `refinement_exhausted` flags (seat probe).
- refinement exhaustion mutation (authority floor) — apply commits, atom balance closed.
- ferrous-free / buffer-activity-unavailable `_step_one_hour` path — flagged zero transfer (authority floor).

**Hole:** M2 surface `_fe_saturation_bound_fO2_log` returning `None` now **raises** `oxygen_interface_activity_fixed_point_nonconverged` (tip replaced chunk-6 `surface_activity_unavailable` zero-transfer return). Shadow refinement catches that reason and returns `status=ok` with flags + `transfer=0`. **`_apply_oxygen_reservoir_exchange` then unconditionally re-enters `_oxygen_interface_state` → `_oxygen_finite_interface_root` without the catch (core.py ~11597), and the hour aborts.**

Seat probe on tip:

```
shadow status ok flags [activity_fixed_point_nonconverged, candidate_solve_incomplete] transfer 0.0
CONFIRMED APPLY ABORT: oxygen_interface_activity_fixed_point_nonconverged
CONFIRMED HOUR ABORT: oxygen_interface_activity_fixed_point_nonconverged
```

`test_exponential_m2_activity_failure_is_predicted_and_flagged` only asserts the **shadow** helper — it never calls apply/`_step_one_hour`, so the hole is unpinned. This violates REQ/North Star “never a whole-run abort” and regresses chunk-6 LAND’s unavailable-activity zero passive transfer.

**Required fix (not done here):** either restore M2 `surface_activity_unavailable` zero-flux return for `None`, or catch `oxygen_interface_activity_fixed_point_nonconverged` in apply’s interface diagnostic re-entry and publish gas/zero from the already-flagged shadow. Pin with apply/`_step_one_hour` assertion.

### (3) Tolerance floor: scale-aware, not loosened acceptance? — **PASS**

`scaled_flux_tolerance` (core.py ~4435–4449):

```
max(flux_residual_tolerance_mol_m2_s, 1e-15 * scale, 4.0 * math.ulp(scale))
```

Caller budget remains `min(1e-14, 0.01*budget/(A*dt))`. Relative/ULP terms are representability limits on flux subtraction, not a wider physical mole budget. Seat algebra: large scale → `4*ulp` wins; tiny scale → absolute `1e-14` budget wins. Docstring on `_oxygen_shadow_transfer` matches. Not a loosened acceptance band.

### (4) Interface/amount counters; E0 zero roots — **PASS**

| Contract | Evidence |
| --- | --- |
| Interior ≤520; amount_bisections==0 | linear exact + converged-reference asserts; all return paths hardcode `amount_bisections: 0` |
| Cap successor ≤529 | `test_exponential_binding_caps_stop_outward_and_publish_successor` **passed** |
| E0 root count 0 | `test_exponential_e0_returns_without_interface_roots` **passed** (`hard_vacuum_no_passive_exchange`, forbidden root never called) |

No amount-bisection loop remains in the exponential path.

### (5) SSO-R pressure shift 1e-9 → 1.0819e-9 bar — **PASS (physical)**

`tests/test_sso_r_validation_map.py` (tip):

- Asserts `SiO_provider_pO2_bar ≈ 1.0819011398556054e-9` with comment: exponential finite-film leaves **5.777179862e-10 mol** O₂ in owner headspace.
- Golden native-Fe pool advances by `2 * oxygen_reservoir_exchange_o2_mol` (~1.1553e-9 mol) — M2 stoichiometry Fe + ½O₂ ↔ FeO, not a tolerance artifact.
- Seat: `test_sio_vapor_pressure_responds_to_requested_po2` **passed** (~215 s) at tip while still on c7.

Physical committed transfer, not a defect.

### (6) Merge kept c6 M2 law + green consumer fixes; `rg` + targeted tests — **PASS (merge) / blocked only by (2)**

- Ancestry: `1de22dd6e` and `e7bd0cd52` both ancestors of tip; merge `385db7dd1` parents those two; `--remerge-diff` empty per worker.
- M2: `m2_metal_reaction` dispatch retained; finite metal film root + stoichiometry tests **passed** (oxidizing/reducing + design fixture complementarity).
- Green consumer: merge brought `fe_redox` / vapor / optimizer / OpenIMCC consumer side; c7 commits touch `simulator/core.py` + redox tests only after merge.

**Targeted VPS results (tip `910ec1779`):**

| Suite / nodes | Result |
| --- | --- |
| linear exact + caps + predict-flag + E0 + unavailable-buffer + ferrous-free | **13 passed** (~40 s) |
| M2 film fixture + stoichiometry | **3 passed** |
| DOP853 sample (ferric 1.0 / 0.01, m2 1.0) | **3 passed** (~326 s) |
| mass_balance oxygen + overhead flagged commit | **6 passed** |
| SSO-R SiO pO₂ shift | **1 passed** (~215 s) |
| z14 reconfirm (E0, root-miss, exhaustion, M2 stoich, mass/overhead) | **7 passed** |
| 170 h lunar C2A | **timeout >180 s on VPS** (worker Mac: 170 snapshots restored) — policy: not a VPS full-hero gate |
| CPU 1.83× green | evaporation batch path per profile; **not alone a REVISE** per REQ |

Changed symbols (`expm1`, `prediction_flags`, `refinement_error_estimate_*`, `amount_bisections`, `scaled_flux_tolerance`, `m2_metal_reaction`) referenced from authority floor, mass_balance, overhead, SSO-R map, condensation RH03, R20 state — exercised above.

## Non-blocking notes

- SiO wall-temperature absolute anchor drift vs green is pre-existing at base (worker); wall invariance holds. Out of chunk-7 acceptance.
- VPS cannot economically run full DOP853 z=100 M2 matrix or 170 h; Mac worker evidence accepted for those nodes given seat samples green.
- Seat contention: `slot-b565` was switched to `review/residue-r1b` during this review; `slot-z14` holds `review/stack2-redox-c7` clean at tip.

## Verdict

**REVISE** tip `910ec1779330c2478f8418eff8ede5c8f39fc8d6`

Exponential integrator, z-regime accuracy samples, counters/E0, scale-aware ULP floor, SSO-R physical pressure shift, and merge retention of M2+green are sound. **Blocker:** M2 surface activity `None` is caught and flagged in shadow, then **re-raised from apply’s unprotected `_oxygen_interface_state` re-entry**, aborting the hour — violates predict-and-flag / “never whole-run abort” and regresses chunk-6 unavailable-activity zero transfer. Fix apply (or restore unavailable return) + pin with apply/`_step_one_hour` test before LAND.

— regolith-empirical

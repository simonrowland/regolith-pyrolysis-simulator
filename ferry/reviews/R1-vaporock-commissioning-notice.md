# R1 — VapoRock commissioning-band notice

**Repo:** regolith-pyrolysis-simulator  
**Base (green):** `fbe3491b2` (`origin/work-v064-green`)  
**Tip under review:** `ce8d6a7f6` on `origin/review/t959-vaporock-notice`  
**Intent:** VapoRock rides the MELTS liquid model, so it inherits the MELTS commissioning band (`data/engine_commissioning.yaml`, SiO2 [30, 80] wt%, T [1073.15, 1700] K) and, outside it, the same notice + `authority=extrapolated` + `certified_band` that alphaMELTS/ThermoEngine already attach. Prediction values must not change; no new refusals.

**Files:** `simulator/melt_backend/vaporock.py`, `data/engine_commissioning.yaml`, `tests/test_vaporock_backend.py` (no change to `engines/engine_commissioning.py`; `vaporock` was already in `_OPTIONAL_ENGINES`).

**Method:** Static review of tip vs base plus narrow pytest and adversarial probes on a detached worktree of `ce8d6a7f6`. Green branch left untouched.

---

## Summary

The tip correctly wires `assess_engine_commissioning(self.name, …)` (`name='vaporock'`), adds a `vaporock` row that copies the MELTS certified band, and preserves fake-engine speciation across in-/out-of-band SiO2. Doctrine predict-and-flag is the right shape for the success path, and the T ceiling matches alphaMELTS.

One real defect: the notice is assessed **before** the forbidden-species / empty-composition typed refusals, so out-of-band refused cells get `authority=extrapolated` and the warning text `engine will run` even though the engine never runs. That is inconsistent with alphaMELTS (commissioning after domain gate) and contradicts the notice string. Fix patch moves assessment to immediately before the engine call and extends the unit test for T-axis + refuse-path cleanliness.

No silent physics change found on run paths. Cache **keys** do not include diagnostics/warnings; cached **payload values** and any golden that serializes full `EquilibriumResult` will grow the new fields (same pattern alphaMELTS already uses).

---

## Findings

### P2 — Commissioning notice + "engine will run" stamped onto typed input refusals

**Where:** tip `simulator/melt_backend/vaporock.py` ~1779–1798 (assessment placed after non-finite gate, **before** dropped-species / empty-composition returns ~1799–1841).

**Evidence (concrete):**

```text
equilibrate(1400.0 °C, composition_kg={SiO2:20, Na2O:70, WeirdStuff:10},
            fO2_log=-8.25, pressure_bar=2e-6)
→ status=out_of_domain
→ diagnostics.backend_status_reason=forbidden_species
→ diagnostics.authority='extrapolated'
→ diagnostics.commissioning_notice.reason='silicate_window'
→ warnings contain
   'CommissioningNotice: out of certified band; authority=extrapolated; engine will run'
```

SiO2 after drop/renormalize is ~22.22 wt% (outside [30, 80]); the engine is never called. Peer alphaMELTS applies commissioning only after `_domain_gate` returns `None` (`alphamelts.py` ~1405–1415), so refused cells do not claim extrapolated run authority.

**Why it matters:** Doctrine is notice+**run**. Consumers that key on `diagnostics['authority']=='extrapolated'` or the `CommissioningNotice` warning without checking `status` can mis-bucket a typed refusal as a completed extrapolated prediction. The warning text is factually false on this path.

**Fix:** Move the `assess_engine_commissioning` block to after the empty-composition refusal and immediately before `_call_vaporock`. Optional patch: `ferry/reviews/R1-fix.patch` (also adds T-axis + refuse-path assertions). Verified: narrow test passes with the patch applied on the tip worktree.

### P3 — Unit test never exercises the T axis of the shared band

**Where:** tip `tests/test_vaporock_backend.py` `test_vaporock_commissioning_notice_preserves_prediction_values` (~308–353): only `1400.0` °C (= 1673.15 K, inside certified T) varying SiO2.

**Evidence:** Probe at `1500.0` °C (= 1773.15 K) with SiO2=50 wt% correctly yields `reason=temperature_range`, `authority=extrapolated`, speciation unchanged — but tip CI would not catch a regression that dropped the T check. Covered in `R1-fix.patch`.

### P3 — Out-of-band diagnostics enter equilibrium cache *values* (not keys)

**Where:** `simulator/reduced_real_determinism.py` `equilibrium_payload` ~2610–2632 serializes all `EquilibriumResult` fields including `warnings` and `diagnostics`; `_is_cache_inert_diagnostic_key` ~2728–2734 does **not** strip `commissioning_notice` / `authority` / `certified_band`.

**Evidence:** Cache key is `canonical_replay_key(...)` from live controls (`_equilibrium_key` ~526–537) — unchanged by this tip. Payload digests / any golden that snapshots full diagnostics will change for out-of-band vaporock cells. Same attachment shape as alphaMELTS; VapoRock is vapor-side / not an active melt backend, so blast radius is small. Not a physics change; noted for golden owners.

---

## Attack checklist

**(a) Does the T axis make almost every high-T pyrolysis run carry a notice, and is that consistent with alphaMELTS?**

Yes on frequency, and yes on consistency. VapoRock hard gate is [1350, 1950] K; certified T is [1073.15, 1700] K — **~41.7%** of the admitted VapoRock T window notices on T alone. Recipe-scale melts at ≥1500 °C (1773.15 K) always notice while still running (probe above). AlphaMELTS/ThermoEngine use the identical certified T row in `data/engine_commissioning.yaml`; this tip copies that row for `vaporock` by design. Not a defect under the stated intent (predict-and-flag).

**(b) Do new diagnostics / warning strings change downstream (cache keys, digests, goldens, scoring routing, non-authoritative consumers)?**

- **Cache keys:** no (controls-only).
- **Cached payload / digests / full-result goldens:** yes for out-of-band cells (new diagnostic keys + `CommissioningNotice` warning) — peer-consistent; see P3.
- **Scoring routing:** no direct vaporock→battery `cell.authority` wiring found; battery `Authority.EXTRAPOLATED` is driven by cell-level `authority`, not this adapter field. AlphaMELTS vapor-bridge path consumes only `vapor_pressures_Pa` / status from `helper.equilibrate` and already carries its own MELTS commissioning notice — no double-stamp into AM diagnostics observed.
- **Refuse-path false `authority=extrapolated`:** yes, pre-fix tip (P2).

**(c) Any `equilibrate()` return path that skips the notice for an out-of-band melt that runs?**

No. Every path that reaches `_call_vaporock` (success, `not_converged` exception, sum-pressure refuse after the call, empty speciation) goes through the assessment and keeps `projection_diagnostics` / `prior_warnings`. Early hard refusals (unavailable, VR T gate, liquid fraction, P, fO2, non-finite) never run the engine, so skipping notice there is correct. The bug is the opposite: notice on refuse paths that never run (P2).

**(d) Is `comp_wt` really oxide wt% on the band's basis?**

Yes. `comp_wt = projection.oxide_wt_pct` from `project_melt_to_oxide_projection(..., oxide_basis=_VAPOROCK_MELT_BASIS)` (`base.py` ~247–254): filtered to the oxide/volatile basis, renormalized to 100 wt%. `comp_wt.get('SiO2', 0.0)` matches alphaMELTS' missing-SiO2 → 0.0 posture for the band assessor (AM additionally hard-refuses SiO2≤0 before noticing; VapoRock historically does not — tip correctly notices rather than inventing a new refusal).

---

## Method

- Static: tip diff `fbe3491b2..ce8d6a7f6`; `vaporock.equilibrate` control flow; `assess_engine_commissioning`; alphaMELTS/ThermoEngine notice attachment; reduced-real cache key/payload; score/authority consumers.
- Ran tests: `tests/test_vaporock_backend.py::test_vaporock_commissioning_notice_preserves_prediction_values` on tip worktree (1 passed); same test after `R1-fix.patch` (1 passed). Engines not required (fake import). Adversarial probes for T-axis notice and forbidden-species refuse path.

Optional fix (not applied): `ferry/reviews/R1-fix.patch`.

---

VERDICT: R1 | LAND-WITH-FIXES | P0=0 P1=0 P2=1 P3=2 | ran-tests

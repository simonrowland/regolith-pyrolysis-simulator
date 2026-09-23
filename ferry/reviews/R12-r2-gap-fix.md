# R12 — readiness gap naming (r2-gap-fix)

**Repo:** regolith-pyrolysis-simulator  
**Green tip:** `fbe3491b2` (`origin/work-v064-green`)  
**Commit under review:** `b50438e0fe997cc44e8fd89846e5dcab4f2cea87` on `origin/review/r2-gap-fix` (not on green; parent `2e9e17c3d138fdfba9269c493f82974a71153fa5`)  
**Files:** `simulator/battery/waypoints.py`, `tests/battery/test_b556_missing_evaluated.py`

**Intent (claim):** Readiness gap lists — free-text fO2 buffers not counted present; unusable thermal schedules listed; never an empty missing list. Must resolve NOTHING differently (resolution unchanged; only gap naming/listing).

**Attack surface:** free-text buffer still counted present; empty missing list still possible; resolution path changed; published-but-route-unusable buffer treated as present.

**Method:** Static tip-vs-parent on `_result`, `thermal_path`, `oxygen_condition`, charge/G1 presence fallbacks. Parent-vs-tip `selected.route` identity probes. Live constructs: extract-style free-text buffers, IW below Frost T domain, multi-T series + IW, incomplete thermal hold, categorical charge, regime readiness empties. Pytest on tip. Mode: **ran-tests**.

---

## Findings

### P2 — Published buffer counted present when buffer route cannot fire (out-of-domain T / multi-T)

**Evidence (on `b50438e0f`):**

- Buffer **route** requires published coeffs, resolved thermal, resolved pressure, and point T inside the published window (single-T series only) — `waypoints.py:961–969`.
- Gap **presence** uses a weaker check — published name only — `waypoints.py:992–1000`:
  ```python
  buffer_known = str(control.buffer.state.value).upper() in PUBLISHED_BUFFERS
  control_present = buffer_known
  ```
- **Trigger:** `fO2_control.buffer="IW"`, `conditions.temperature_K=738.15` (IW domain starts 838.15 K), kems pressure present, no observation fO2.
  - `oxygen_condition(...).selected is None`
  - `absence.missing == ("fO2_log",)` — `experiment.fO2_control` omitted while the only oxygen control is an out-of-domain buffer.
- Same omission with a two-point thermal series (1000 K and 1200 K): buffer relation refuses multi-T; control still marked present; missing again `("fO2_log",)`.

**Why P2:** The named free-text attack fails (claim holds there). Residual is the same class the commit message claimed to fix (“located fO2 control that no route accepts”): a located published buffer the route rejects is reported present, so the gap mis-names the blocker. Status remains GAP (no readiness flip) — not P0/P1.

**Fix:** `ferry/reviews/R12-fix.patch` — treat control as present only when the buffer name is published **and** thermal or pressure is still absent; once both resolve and no route fired, keep `experiment.fO2_control` in the missing list. Tip tests stay green under the patch; OOD then yields `("fO2_log", "experiment.fO2_control")`; IW with missing T still omits control and names `temperature_K`.

---

### P3 — `_result(present={})` can still emit `missing=()`

**Evidence:** `waypoints.py:171–178` — empty `present` mapping + no selection → `evaluated=()` and `tuple(present)=()`. No production caller passes `{}` (oxygen/thermal/charge/G1 maps always have keys). Regime gaps (`BELOW_PRESSURE_FLOOR`, `OUTSIDE_PRESSURE_REGIME`) still use default `missing=()` by design — not `MISSING_EVIDENCE` OR-set gaps.

**Why P3:** Latent hole in the “never empty” fallback; live regime empties are intentional. Does not reopen the OR-set all-True empty-list bug this commit fixed.

---

## Attack checklist

| Attack | Result |
| --- | --- |
| Free-text fO2 buffer still counted present | **Fail (safe).** Extract-style prose, non-keys (`FMQ`, `QFI`, `NOT_A_REAL_BUFFER`) keep `experiment.fO2_control` in `absence.missing`. Exact case-fold published keys (`iw`) correctly count as published. Zero/categorical pO2 no longer count as present (gap text vs parent changes; selection unchanged). |
| Empty missing list still possible | **Partial.** `MISSING_EVIDENCE` OR-set all-present → empty is fixed (`_result` names the whole set; thermal uses the fixed OR tuple `("experiment.thermal_schedule", "experiment.conditions.temperature_K")`; charge/G1 covered by tests). Residual: empty `present={}` (P3, unreachable) and intentional empty regime gaps (out of OR-set scope). |
| Resolution path changed | **Fail (safe).** Parent-vs-tip `selected.route` identical across obs fO2, pO2, unknown/free-text/OOD buffer, incomplete hold, interval T, point T, ramp, categorical charge. Route bodies unchanged; only gap `present` / bare missing tuples differ. |
| Unusable thermal schedule omitted | **Fail (safe).** Incomplete hold / interval conditions T / partial schedule → `missing == ("experiment.thermal_schedule", "experiment.conditions.temperature_K")`; engine `temperature_K` surfaces the same OR. |
| Published buffer unusable at T/P still “present” | **Hit — P2** (OOD / multi-T). |

---

## What looks sound

- Free-text / unknown buffer presence narrowed from “any located buffer or pO2” to published buffer name (`:992–997`); unusable pO2 correctly enters the gap.
- `fO2_log` forced `False` on the no-route path (`:999`) — stops inventing presence from `experiment.conditions.fO2_log`.
- Thermal gap text always names both OR members when no route fires (`:871–875`).
- `_result` all-True present map → name whole set (`:176–177`) closes empty `MISSING_EVIDENCE` lists for charge / volume / G1-style maps.
- “Route selection and readiness status unchanged” holds on probed inputs.

---

## Tests

Tip worktree at `b50438e0f`, repo `.venv`, `-o addopts=`:

- `tests/battery/test_b556_missing_evaluated.py`: **16 passed** (unpatched tip)
- Adversarial probes (free-text, OOD IW@738.15 K, multi-T IW, incomplete thermal, parent route identity): free-text / thermal / resolution **safe**; P2 **confirmed**
- Optional fix patch + tip tests: **16 passed**; OOD/multi-T gap text corrected (patch not applied to the review branch)

Optional fix: `ferry/reviews/R12-fix.patch`. Not applied to `review/r2-gap-fix`; do not push.

---

## Verdict rationale

Named attacks on free-text presence, thermal listing, and resolution do not land. The OR-set empty-missing bug for `MISSING_EVIDENCE` is fixed. One residual contract hole remains: presence ≡ published buffer **name**, not “buffer route could accept this control,” so out-of-domain / multi-T published buffers are omitted from the gap while only `fO2_log` is named. **LAND-WITH-FIXES** — tighten the presence predicate to match the route gate before trusting oxygen gap text for buffer-controlled runs.

VERDICT: R12 | LAND-WITH-FIXES | P0=0 P1=0 P2=1 P3=1 | ran-tests

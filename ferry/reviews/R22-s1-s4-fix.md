# R22 — S1/S4 fix (cross-work bench/obs + Clausing gap + P=Q/S)

**Repo:** regolith-pyrolysis-simulator  
**Green tip:** `fbe3491b2` (`work-v064-green`)  
**Commit under review:** `d004cd510bce162cb62b1f18f2d6bd7ee4f3560d` on `origin/review/s1-s4-fix` (detached)  
**Worktree:** `/workspace/repos/wt/slot-09` @ detached `d004cd510`  
**Ferry tip check:** SHA matches; exactly **3 commits** `07ad01dae..d004cd510`:
- `88288f738` battery: refuse a cross-work bench or experiment reference
- `f6a0e1712` battery: list only the missing Clausing factor on a geometric escape gap
- `d004cd510` battery: drop chamber volume from the steady pressure route  

**Files (3-commit tip range):** `simulator/battery/migrate.py`, `simulator/battery/validate.py`, `simulator/battery/waypoints.py`, `tests/battery/test_migrate.py`, `tests/battery/test_waypoints.py`

**Intent:** (1) Migrator refuses qualified bench/experiment ids outside the current work and writes no foreign link; validate flags the same mismatch for hand-edited stores; same-work qualified ids still resolve. (2) KEMS geometric-only escape readiness gap names only `bench.geometry.clausing_factor` (not orifice area). (3) Steady pressure route is `P = Q/S`; chamber volume is not an input and not a gap.

**Attack surface:** Cross-work steal via FQ `::` ids; same-work false refusal; observation remint after refuse; P=Q/S algebra / volume still required somewhere; escape gap still lies about present area; live corpus number change.

**Method:** Static read of 3-commit diff vs parent `07ad01dae`; adversarial probes (prefix/kind/case FQ, P=Q/S with/without volume, interval bounds, zero speed, area/diameter/clausing gap shapes, live-pattern orifice-area works); pytest on tip. Mode: **ran-tests**.

---

## Findings

### P2 — Same-work FQ ids are case-sensitive against lowercased `work_id`

**Evidence (file:line on `d004cd510`):**

- `simulator/battery/migrate.py:6347-6360` — `_registry_id` accepts a `::` token only when `qualified.startswith(f"{work_id}::{kind}::")` (byte/case exact)
- `simulator/battery/migrate.py:472-486` — `extract_doi` / `canonicalize_doi` mint lowercased work ids (probe: DOI `10.1234/SHARED` → work `10.1234/shared`, bench `10.1234/shared::bench::…`)
- Probe: second extract of the same work with `bench_id: "10.1234/SHARED::bench::shared-bench"` (citation case) → `bench_id is None`, registry `outside work '10.1234/shared'`; observation reminted to `10.1234/shared::I` instead of the shared series

**Why P2 (latent, not P0/P1):** Live extracts have **0** FQ `bench_id` / `experiment:` tokens with `::` (S1 corpus check still holds). The blessed same-work path (IDs copied from a prior migrate, as in `test_same_work_qualified_bench_and_experiment_resolve`) uses canonical lowercase and passes. A hand-authored FQ that keeps the printed DOI letter-case falsely refuses a same-work link. No wrong score/ledger number today.

**Fix direction:** Casefold (or re-canonicalize the work segment) before the prefix check; optionally reject non-canonical FQ with an explicit “use canonical work_id” detail.

---

## Residual notes (not severity / out of 3-commit claim)

- **Observation after cross-work refuse remints locally.** `_registry_id` → `None` then `_migrate_extract_observation` uses `declared_experiment_id or _experiment_id(...)` (`migrate.py:7017-7049`). Foreign link is not written (claim met); attacker rows still land under the attacker work. Detail text says “no link written” while a *different* local experiment link is created — wording only.
- **S1 residuals outside this claim:** `scripts/bench_generate.py` / `scripts/bench_readiness.py` still global `benches.get` without work match (S1 P1 latent); `derived_from` FQ (S1 P2). Equipment cross-work refusal is on parent `07ad01dae`, not these three commits.
- **Steady `P=Q/S` has no dwell gate.** Comment at `waypoints.py:613-616` states `tau=V/S` is not checked. Algebra `0=Q-S·P ⇒ P=Q/S` is sound for constant-V steady state. Live corpus: **no** `gas_load_Pa_m3_s` in `data/literature/` — unlock is latent; cannot ship a wrong pressure from this route today.
- **Escape status unchanged.** Geometric-only still `ReadinessStatus.GAP`; only `missing` tuple shrinks. Live S4 works (`10.2355_isijinternational.32.1276`, `10.6028_nbs.ir.81-2279`, `10.3406_bulmi.1983.7673`) have orifice area and no clausing key — gap text fix is the live S4 remediation.

---

## Attack checklist

| Attack | Result |
| --- | --- |
| Cross-work bench registry / experiment.bench_id / observation.experiment steal | **Fail (safe).** Stolen FQ bench not lifted; atk experiment `bench_id is None`; obs `experiment_id != victim`; `referential_integrity` “outside work” on bench, `.bench_id`, observation `.experiment`, and stolen experiment id paths (`test_migrate` cross-work + probe). |
| Same-work qualified bench + experiment resolve | **Mostly fail (safe) on canonical ids.** Shared-DOI second extract keeps `bench_id` and obs → shared experiment; no “outside work”. **Hit (latent P2):** non-canonical DOI letter-case FQ falsely refused (above). |
| Kind-confused FQ (`work::experiment::x` as bench) | **Fail (safe).** Prefix requires `::{kind}::`; refused. |
| Work-id prefix spoof (`10.1/FO` vs `10.1/FOO`) | **Fail (safe).** `startswith` does not accept longer DOI as child of shorter. |
| Validate hand-edited cross-work bench / obs | **Fail (safe).** `belongs to work` hard issues on `.bench_id` and observation `.experiment_id` (`test_validate_flags_cross_work_bench_and_observation`). |
| P=Q/S wrong / still needs volume | **Fail (safe).** Without volume: route `steady_gas_load_over_pump_speed`, `P=1e-3` for Q=1e-5, S=1e-2; `relevant_volume` absent from inputs; volume present does not change P. Zero S → no derived route. Interval Q/S bounds `5e-4..2e-3`. Printed pressure still wins when present. |
| Escape gap still names present orifice area | **Fail (safe).** Area-only and diameter-only geometric routes → `missing == ("bench.geometry.clausing_factor",)` only; with clausing, effective route and no escape gap; empty geometry does not take the GEOMETRIC_ONLY clausing-only branch. Live-pattern area/no-clausing matches. |

---

## What looks sound

- `_registry_id` vs `_qualify_registry_id` split: refuse path records issues and returns `None`; callers `continue` / leave `bench_id=None` / fall through without attaching foreign experiments (`migrate.py:6335-6361, 6382-6441, 6661-6674`).
- Validate mirrors migrate for bench ownership and observation source-owner vs experiment work (`validate.py:718-738, 811-889`); ambiguous multi-work sources omitted from owners (no false positive).
- `_effective_escape_gap` only fires Clausing-only text when a geometric route is already selected (`waypoints.py:1188-1202`) — so area/diameter evidence is present by construction.
- Steady route inputs are exactly Q and S; units comment Pa·m³/s ÷ m³/s = Pa; sanity constant matches tests.

---

## Tests

Tip worktree at `d004cd510`, repo `.venv` via `/workspace/repos/regolith-pyrolysis-simulator/.venv`, `-o addopts=`:

- `tests/battery/test_waypoints.py`: **65 passed**
- `tests/battery/test_migrate.py -k 'cross_work or same_work or equipment or Duplicate or validate_flags'`: **14 passed**
- Adversarial probes (FQ prefix/kind/case, P=Q/S volume±, interval/zero-S, escape shapes, live-pattern gap): **passed** (case mismatch documents P2)

No push. No fix patch (optional casefold only).

---

## Verdict rationale

Named attacks do not land a live wrong pressure, a live false escape “area missing” claim, or a successful cross-work bench/observation link on the migrator/validate paths claimed. Derivation `P=Q/S` and Clausing-only gap hold under probe; same-work canonical FQ links hold. One latent same-work footgun remains (DOI letter-case vs canonical `work_id`). Per Batch Y: not P0. **LAND.**

VERDICT: R22 | LAND | P0=0 P1=0 P2=1 P3=0 | ran-tests

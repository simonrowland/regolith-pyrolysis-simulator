# R9 — interval-valued printed conditions

**Repo:** regolith-pyrolysis-simulator  
**Green tip:** `fbe3491b2` (`work-v064-green`)  
**Commit under review:** `a49cef012` on `origin/review/janaf-batch-2026-09-22` (NOT landed on green)

**Intent:** `engine_point` readiness now refuses an interval-valued printed temperature / pressure / fO2 with typed reason `interval_needs_point` (never a midpoint) instead of the generic `unsupported_print_form`. Bounds and other non-point forms keep the generic reason. The interval itself stays preserved in provenance.

**Attack surface:** a non-interval stamped with `interval_needs_point`; an interval reaching the engine as a point; any change in what resolves.

**Method:** Static read of tip `simulator/battery/generators/bench.py` (`_requirements`, `_point`, `engine_point_requests`), `simulator/battery/waypoints.py` (`GapReason`, `_log_pressure`, `_point_condition`), `simulator/battery/consumer_inputs.py` (SERIES→`temperature_K` collapse), `simulator/battery/records.py` (`Value` INTERVAL invariant). Narrow pytest on tip worktree at `a49cef012`. Adversarial probes for BOUND / SERIES / ORDERING / CATEGORICAL / EXPRESSION / UNAVAILABLE / RELATIVE_SERIES / equal-bound interval / `fO2_Pa`→log interval / kems isolation / multi-interval / ready numeric baselines.

---

## Diff (exact)

`GapReason.INTERVAL_NEEDS_POINT = "interval_needs_point"` added in `waypoints.py` (comment: typed refusal says “interval printed, engine needs a point” instead of hiding behind the generic unsupported kind).

In `_requirements` (`bench.py`), for `consumer == "engine_point"` and `name != "normalized_composition"` when a value is selected and is not a POINT:

```python
selected = waypoint.selected.value
if not isinstance(selected, Value) or selected.kind is not ValueKind.POINT:
    reason = (GapReason.INTERVAL_NEEDS_POINT
              if isinstance(selected, Value) and selected.kind is ValueKind.INTERVAL
              else GapReason.UNSUPPORTED_PRINT_FORM)
    gaps.append(ReadinessGap(name, reason, (name,)))
```

Parent always appended `UNSUPPORTED_PRINT_FORM` for every non-POINT form. Payload path unchanged: GAP → `payload is None`; `_point` still raises `UnsupportedValue` for non-POINT; provenance still serializes the interval kind/bounds; no midpoint algebra introduced.

Tests added in `tests/battery/test_bench_generators.py`:
- `test_engine_interval_refusal_is_typed_interval_needs_point` — parametrized over observation keys `temperature_K` / `total_pressure_Pa` / `fO2_log` (waypoints `temperature_K` / `pressure_boundary` / `oxygen_condition`)
- `test_engine_bound_refusal_stays_unsupported_print_form` — BOUND on `fO2_log` keeps generic reason

Pre-existing `test_engine_bounds_are_preserved_and_refused_without_midpoint` still asserts provenance `kind == "interval"` and `payload is None`.

---

## Findings

No P0 / P1 / P2 / P3 defects found on the stated attack surface.

---

## Attack checklist

| Attack | Result |
| --- | --- |
| Non-interval stamped `interval_needs_point` | **Fail (safe).** BOUND / ORDERING / CATEGORICAL / EXPRESSION / UNAVAILABLE / RELATIVE_SERIES → `unsupported_print_form` only. Multi-value SERIES on observation `temperature_K` empties the `temperature_K` consumer waypoint (`missing_evidence`) via pre-existing SERIES→single-T collapse in `collect_consumer_inputs` — never the new reason. |
| Interval reaches engine as a point | **Fail (safe).** `_requirements` marks GAP before `engine_point_requests` builds a payload; all eight engines get `payload is None`. Equal-bound interval `[1400,1400]` stays `kind=interval` and refuses (`Value` does not coerce equal bounds to POINT). Observation interval on pressure / fO2 is ranked `routes[0]` ahead of experiment POINT routes, so the interval is selected and refused rather than silently falling through. `_log_pressure` preserves INTERVAL when converting `fO2_Pa` → log; probe: Pa `[1,100]` → log `[-5,-3]` → `interval_needs_point`. Provenance retains `interval_low`/`interval_high`, no `point`. |
| Change in what resolves | **Fail (safe).** POINT baseline still READY with `temperature_C=1126.85`, `pressure_bar=1e-5`, `fO2_log=-9`. Bound / other non-point still GAP with generic reason and `payload is None`. Only the interval gap *reason string* changes (`unsupported_print_form` → `interval_needs_point`). kems with interval oxygen still refuses under `unsupported_print_form` (engine_point-only retype; intentional). Multi-interval stamps both waypoints. |

---

## What looks sound

- Kind check is strict (`isinstance(selected, Value) and selected.kind is ValueKind.INTERVAL`); non-Value selected values stay generic.
- Doctrine alignment with migrate “no midpoint invented” comments elsewhere; this commit only types the refusal.
- No exhaustive `GapReason` switch / serializer found that would break on the new StrEnum member.
- Gap dedup via `dict.fromkeys` unchanged.

### Residual notes (not severity)

- `_refused` (default reason `UNSUPPORTED_PRINT_FORM`) / `_point` secondary path still would stamp the generic reason if readiness were ever bypassed. Unreachable today: readiness already GAPs intervals before payload build; frozen `Value`s prevent TOCTOU. Defense-in-depth only.
- New bound regression covers `fO2_log` only; probes confirm T/P bounds also stay generic.
- Pre-existing midpoint-preservation test still exercises intervals for provenance only; typed reason covered by the new test.

---

## Tests

Tip worktree at `a49cef012`, repo `.venv`, `-o addopts=`:

- `tests/battery/test_bench_generators.py -k 'interval or bound or midpoint or engine_inputs or uncontrolled_oxygen or single_species'`: **12 passed**
- `test_bench_generators.py` + `test_b560_unsupported_print_form.py` + `test_engine_intensive_charge.py`: **67 passed**
- Adversarial probe script (kinds × attacks above): **all probes passed**

No optional fix patch (nothing to fix).

---

VERDICT: R9 | LAND | P0=0 P1=0 P2=0 P3=0 | ran-tests

# Independent review — bench-c2-fix2c

Reviewed staged seven-file repair diff against `7a3d12305`, prior `c2-fix.md`, authoritative assembled prompt, and schema-approval steer seq 1. Applied the gstack review checklist read-only; no source edits, mutations, commits, controller operations, or dependency changes. Binding DESIGN was absent from this seat. Findings below reproduce against the working files matching the staged patch at review time.

## Changes required

### P1 — incomplete thermal points bypass N3 refusal (confidence 10/10)

`simulator/battery/waypoints.py:557` guards refusal with `and not schedule.points`. Presence of an unusable point is not a complete printed path. Add one point with unknown time and temperature to the existing one-ramp/interval-hold example: no point route is constructed, the guard is skipped, and `printed_ramps` is selected. The [500,600] s hold disappears; KEMS, RPS, and all eight engines report READY again.

Fix: bypass mixed-segment refusal only when a usable complete printed point path was actually constructed, rather than whenever the points tuple is nonempty. Include this sibling in N3 regression/mutation coverage.

### P1 — cited pumping evidence loses cited identity (confidence 10/10)

`scripts/bench_readiness.py:157`: `for evidence in _located_evidence(apparatus):`. `_implicit_bench` copies `pressure_environment.pumping.pumping_speed_m3_s` into the Bench but never inspects that evidence for external-document locators. A pumping speed explicitly located at paper-B.pdf page 7 therefore produces `inferred_from_embedded_evidence`, `ref=None`, while retaining that external locator on the copied speed. Steer seq 1(d) requires cited_by_author when an external apparatus reference occurs anywhere; inference is last resort.

Fix: scan the embedded apparatus evidence actually consumed from pressure_environment as well. Do not change unrelated experimental sample provenance into an apparatus citation.

### P2 — real failing segment of Knudsen schedule emits no notice (confidence 9/10)

`simulator/battery/waypoints.py:876`: `if high is not None and high < threshold:`. The range is constructed from min/max of actual thermal-path points, not merely uncertain measurement bounds. A printed 300→1500 K ramp, Ar, P=5 Pa, d=0.5 mm produces Kn=[2.9723678,14.8618389]. Its initial segment definitively fails Kn>=10, but the notice tuple is empty because only the highest temperature is tested. This violates the failing-Kn diagnostic requirement while retaining READY, as demonstrated below.

Fix: detect failure at actual supported thermal points (preserving uncertainty/bound direction for uncertain pressure/diameter). Do not turn the notice into a readiness refusal.

## Runnable independent reproduction

From repository root, run with the parent repository Python (seat `.venv` lacks pytest):

```sh
PY='/Users/simonrowland/Library/CloudStorage/Dropbox/Starship Mission Design/Regolith Processing/regolith-pyrolysis-simulator/.venv/bin/python'
"$PY" - <<'PY'
from dataclasses import replace
from decimal import Decimal
from scripts.bench_readiness import _implicit_bench
from tests.battery import factories as f
from tests.battery.test_waypoints import _knudsen_case
from simulator.battery.records import Value, Located, State, Locator, ThermalSchedule, ThermalRamp, ThermalSetpoint, ThermalPoint
from simulator.battery.enums import ValueKind
from simulator.battery.waypoints import thermal_path, pressure_boundary, consumer_readiness, _orifice_knudsen

x,b = _knudsen_case(Value.point_of('0.1'))
external = Locator(source_path='paper-B.pdf', page=7)
x = replace(x, pressure_environment=replace(x.pressure_environment,
    pumping={'pumping_speed_m3_s': Located(State.of(Value.point_of('0.1')), locator=external)}))
a = _implicit_bench(x)
print('external pumping:', a.identity.basis.value, a.identity.ref, a.pumping_speed_m3_s.locator.source_path)

ramp = ThermalRamp(f.located(Value.point_of('2')), f.located(Value.point_of('300')), f.located(Value.point_of('1500')))
hold = ThermalSetpoint(f.located(Value.point_of('1500')),
    f.located(Value(ValueKind.INTERVAL, interval_low=Decimal('500'), interval_high=Decimal('600'))))
x = replace(x, thermal_schedule=ThermalSchedule(ramps=(ramp,), setpoints_and_holds=(hold,),
    points=(ThermalPoint(Located(State.unknown('not_published')), Located(State.unknown('not_published'))),)))
r = thermal_path(x,b)
print('incomplete points:', r.selected.route if r.selected else None,
    [(y.consumer,y.status.value) for y in consumer_readiness(x,b)])

x,b = _knudsen_case(Value.point_of('5'))
x = replace(x, thermal_schedule=ThermalSchedule(ramps=(ramp,),
    setpoints_and_holds=(replace(hold,hold_duration_s=f.located(Value.point_of('600'))),)))
kn = _orifice_knudsen(x,b,thermal_path(x,b),pressure_boundary(x,b))[0]
y = consumer_readiness(x,b)[0]
print('ramp Kn:',kn.interval_low,kn.interval_high,'status:',y.status.value,'notices:',y.notices)
PY
```

Observed: external pumping `inferred_from_embedded_evidence None paper-B.pdf`; incomplete points `printed_ramps`, all 10 results `ready`; ramp Kn `2.972367776691779801912381443 14.86183888345889900956190721`, status `ready`, notices `()`.

## Verification boundaries

Clausing raw-area flag/preference, partial-wt% overlapping-species test, unavailable/categorical mass regression, mixed-source blocker assertions, eight-engine identity, and RPS crossing-bound logic were reviewed without additional defects found in those paths. Independent probes above execute directly and do not override pytest addopts. Full battery suite and mutation scripts remain the repair worker's gate; scripts were being added concurrently and were not run by this reviewer.

## Follow-up after concurrent worker fixes

Re-executed the exact reproduction above after the worker incorporated the first two findings. External pumping now returns `cited_by_author` with `BenchReference(...cited_as='paper-B.pdf'...)`. The incomplete-point schedule now returns no selected route and all 10 consumers/engines report GAP. Those two findings are resolved on the working tree; source changes were made by the repair worker, not this reviewer.

The ramp Kn probe still returns the same [2.9723678,14.8618389] range, READY, and no notices.

## Final runtime follow-up

Re-executed the same reproduction after the minimum-actual-temperature correction. The ramp probe now remains READY and emits a `KnudsenConsistencyNotice` containing the full [2.9723678,14.8618389] interval, threshold 10, and located T/P/d inputs. All three runtime findings are resolved by independent rerun.

## Mutation-runner review — P1 false-red acceptance

Read the new runner without executing source mutations (battery tests were active). Green control, exact pytest exit 1, at least one JUnit failure, and no JUnit errors are necessary but insufficient to establish an assertion-sensitive regression proof.

The N3 `interval_hold` mutation disables only `if ramp_series is None or hold_series is None`. The next order guard evaluates `hold_series[0][1]` even when the interval hold produced `hold_series=None`. Thus it produces a TypeError in the test body instead of restoring the original selected-ramp/READY defect. Pytest records exceptions raised during test calls as JUnit `<failure>` entries; `<error>` entries cover setup/teardown and collection errors. The runner's `if result.returncode != 1 or not failures or errors` therefore accepts this crash as proof.

Fix N3's mutation to restore the original behavior by bypassing the complete new refusal block, and require assertion failure identity/type rather than accepting any test-call exception. No mutation was run during this review; the concrete `None` indexing follows directly from the restored guard and the test's interval hold.

Runtime review: **no unresolved findings**. Commit acceptance: **changes required** until the mutation proof's false-red hole is closed and the prescribed proofs run.

## Mutation-runner follow-up

Reviewed the revised runner without executing mutations. N3 now disables the entire added mixed-schedule refusal block, restoring the original ramp-only selection rather than indexing a missing hold series. The harness additionally requires every JUnit failure to identify an assertion or missing expected exception; TypeError call failures no longer meet the acceptance condition. Green controls, pytest exit 1, nonempty failure nodes, no error nodes, and finally-based original-byte restoration remain intact.

The reported false-red hole is resolved by code inspection. **Final review verdict: no unresolved findings; suitable to commit once the repair worker's default-addopts battery and required mutation runs pass.** This review does not claim those runs were executed by the reviewer.

## Narrow reporting-proof follow-up

Statically reviewed the added nonempty blocker assertion, two-source blocker-ranking fixture, source-count sort mutation, parent-venv interpreter preference, and `bench.identity.reason` vocabulary entry. The explicit nonempty assertion correctly replaces the accidental IndexError red. The new ranking fixture distinguishes a blocker affecting two sources/two experiments from a blocker affecting one source/nine experiments, so reversing source-count ordering necessarily violates its expected list. These are scoped acceptance-proof improvements; no new findings. No mutations or tests were run during this static follow-up because the worker's mutation run was active. Final verdict above remains unchanged.

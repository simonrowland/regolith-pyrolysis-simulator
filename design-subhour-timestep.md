# Sub-hour process timestep (t-1886)

The one-hour call inside `PyrolysisSimulator.step` is a numerical timestep, not a physical limit. Lab programs that this engine has to reproduce as time series are shorter than that step and are logged faster than that step: Robinot 2026 is a 1 h run at 1 s with features at 10, 15, 17 and 25 min; Sossi holds are 15 min; Hashimoto and Zhang holds are minutes. One engine step of one hour cannot place a sample on any of those features.

This chunk adds a process timestep in seconds, default 3600 s, and threads it through the live integrators. `dt = 3600` s keeps the current hourly expressions, so hourly outputs stay bit-identical. A shorter step is a flux times `dt`. It is not a short program stretched onto one hour.

## Clock

`melt.hour` stays the integer step index. It increments by 1 on every step, including a sub-hour step, so `snapshot.hour` and the summary field `"hour"` stay integers.

`melt.campaign_hour` is elapsed hours at the start of the step.

- One-hour path: `campaign_hour += 1` after the step, the same integer increment as today.
- Shorter path: after the step-index increment, `campaign_hour = (melt.hour - _campaign_start_hour) * dt_h`. `_campaign_start_hour` is the step index `start_campaign` recorded. The value is the campaign's integer step count times `dt_h`. It is not a running sum, and it is not the run's global step index.

The sample for pressure, pO2, and temperature is the end of the step.

- One-hour path, pressure and pO2: `float(campaign_hour) + 1.0`, still uncapped. That is the expression at `core.py` today.
- One-hour path, lab-schedule temperature: `min(duration_h, max(0, campaign_hour + 1))`, still the capped sampler in `schedule_sample_time_h`.
- Shorter path, both clocks: elapsed hours plus `dt_h`, capped at the schedule duration when a schedule is active.

Those two hourly clocks are different rules. A 0.25 h program at `campaign_hour = 0` is 1.0 uncapped and 0.25 capped. The uncapped 1.0 sits outside the declared window, which is why a fractional preset at the default hour refuses. The hourly path keeps both expressions. The shorter path caps both, because the sample has to land inside the printed window.

`drive_session` still stops on an integer step count. `PyrolysisRun.hours` and `PresetRunSpec.hours` are that step count. At `dt = 3600` s and an integer number of hours, the step count equals the duration in hours, which is today's `hours` value. A 1 h program at 60 s has `hours = 60` and records `duration_h = 1` and `process_dt_s = 60` on the preset provenance and on `run_metadata`. Those two keys are absent when `dt` is 3600 s, so hourly run metadata does not grow a field.

## Inventory

43 places treat one hour as the process step. Eight nearby `3600` expressions are not a process step; they are listed so they are not "fixed" by accident.

Counters and clocks:

1. `core.py` increments `melt.hour` and `melt.campaign_hour` by 1 at the end of the step.
2. `core.py` pressure and pO2 sample is `campaign_hour + 1`, uncapped.
3. `core.py` lab-surface sample is `melt.hour + 1`.
4. `lab_schedule.schedule_sample_time_h` caps `campaign_hour + 1` at `duration_h`.
5. `campaigns.get_temp_target` calls that sampler.
6. `campaigns._completed_campaign_hour_for_endpoint` adds 1 because the check runs before the increment.
7. C2A stage elapsed is `campaign_hour - stage_start + 1`.
8. C2A stamps the next stage start at `campaign_hour + 1`.
9. C2A stage length is `max(1, int(duration_h))`, also at the other `max(1, int(...))` stage clamps. Left in this chunk.
10. C4 `process_elapsed_hr` adds `1.0`.
11. C4 in-window time counts snapshots, one snapshot per hour. Left in this chunk.
12. C5 and MRE baseline each do `mre_low_current_hours += 1`.
13. MRE baseline and the C5 ladder each do `_mre_hold_hours += 1`.
14. Campaign summary duration is `melt.hour - start + 1`, captured before the increment.
15. `record.total_hours = melt.hour` at finalize.
16. The vacuum preset loader requires `duration_h` to be an integer hour, and `PresetRunSpec.hours` is both that duration and the step count.
17. `PyrolysisRun.hours`, `SimSessionConfig.hours`, and `drive_session` stop the loop on that integer.

Integrators:

18. Evaporation ledger mass is `rate_kg_hr * 1.0`, and the remaining vapor mass is scaled by the same `1.0`.
19. The evaporation-transition control dict passes `dt_hr: 1.0` on both the dispatch and the commit.
20. The condensation-route provider reads `dt_hr` and does not use it. The evaporation caller passes step mass `(rate_kg_hr - remaining_kg_hr) * dt_h`. The provider debits those kilograms. MRE gas already passes step kilograms and is not multiplied again.
21. Analytic evaporation depletion is called at the default `dt_hr=1.0` from the two live flux sites and from the capacity-shadow rate cache.
22. The capacity-shadow solver is called with `dt_hr=1.0`.
23. Overhead bleed controls pass `dt_hr: 1.0`.
24. Turbine utilization treats capacity as `kg/hr * 1.0 h`.
25. The two live controlled-O2 transport calls use the method default `dt_hr=1.0`.
26. Oxygen-reservoir exchange uses `alpha = 1 - exp(-3600 / tau_s)`.
27. The inert-sweep residual pO2 projection bleeds `conductance * fraction * 3600` s.
28. Native-Fe vapor takes `min(available, capacity_mol_hr)`, one hour of the Hertz-Knudsen capacity.
29. The native-Fe residual capacity subtracts the committed vapor from that hourly budget.
30. Subsolidus bakeout flux uses `SUBSOLIDUS_STEP_DURATION_HR` (`1.0`) as `dt_hr`.
31. The temperature ramp cap is the `°C/hr` rate applied for one hour.
32. `EnergyTracker.calculate_hour` sets `turbine_kWh = shaft_kW` and `condenser_kWh = W/1000`, both implicit times one hour.
33. `evaporation_enthalpy_budget` treats `species_kg_hr` as the mass evaporated in one hour.
34. Metal-phase stratification's live `stratify` control uses `dt_hr: 1.0`.
35. `PlantMREIntervalControl` is built with `dt_h=1.0`. Faraday charge is `I * dt` once that value is the process step. Effective current already divides by `dt_hr * 3600`.
36. Shuttle K and Na metallothermic controls pass `dt_hr: 1.0` (four sites).
37. Thermite metallothermic controls pass `dt_hr: 1.0` (six sites).
38. C7 Ca transport integrates `hold_time_h` (default 1 h) times area times flux on every call.
39. `electrolysis.step_hour` hardcodes `dt_hr: 1.0`. Left. The live plant path uses `PlantMREIntervalControl`, not `step_hour`.
40. The C3 temperature-scan metallothermic probe passes `dt_hr: 1.0`. Left. It asks a one-hour feasibility question, not the process step.

Outputs:

41. `HourSnapshot` is constructed with `duration_h=1.0`.
42. `build_per_hour_summary` records `"hour"` and no time in seconds.
43. The thermal-train cost model sets `run_hours` from the snapshot count on the basis `one_hour_per_snapshot`. A step that is not one hour is refused. The hourly count stays.

Inspected, not a process step:

44. Condensation residence inventory is `available_kg / molar_mass / 3600 * residence_s`. That converts kg/h to a residence holdup.
45. Overhead kg/h ↔ kg/s conversions (`/ 3600` and `* 3600` on conductance) are unit changes, not the step length.
46. The C0 forecast passes `dt_hr=hold_hr` for a forecast window.
47. Metal-stratification restore passes `dt_hr: 0.0`.
48. Char FeO reduction and Fe redox respeciation are equilibrium one-shots. A second call sees no driving force. They are not scaled and not suppressed.
49. MRE reproduction presets use `hours = int(program.duration_h)` and their own interval grid. Left.
50. The optimizer `EvalSpec` cache has no dt dimension. Left. Lab presets do not go through that cache.
51. Web and panel copy that says "one-hour interval" is presentation. Left.

Ledger cadence is not an hour. `commit_batch` is the only ledger writer and it runs once per transition per step. `UnbalancedTransitionError` refuses an unbalanced proposal. The runner gate remains `RUNNER_MASS_BALANCE_LIMIT_PCT = 5e-12` percent, the same limit as `CONFIDENCE_MAX_MASS_BALANCE_RESIDUAL_PCT`. Closure is per transition. It holds at a short step when both sides of the transition use the same `dt`. The commit does not become once per clock hour.

Hour-assumption inventory count: 43. Nearby non-step expressions inspected: 8 (items 44–51).

## Design

Owner: `simulator/process_timestep.py`. No other module owns the step length.

- `PROCESS_HOUR_S = 3600`.
- `ProcessTimestep` is frozen. `ONE_HOUR` has `dt_s == 3600` and `dt_h` equal to the literal `1.0`. `is_one_hour` is true only when `dt_s == 3600`.
- `resolve_process_timestep(dt_s)` returns `ONE_HOUR` for 3600. Any other finite value `> 0` is `dt_h = dt_s / 3600`. A bool, a non-number, a non-finite value, or a value `<= 0` raises `ProcessTimestepRefusal` with reason `process_dt_not_positive`. There is no maximum dt and no silent fallback to one hour.
- `duration_step_count(duration_h, timestep)` returns the positive integer step count. `ratio = duration_h * 3600 / dt_s`. The count is `round(ratio)` when that integer is at least 1 and `math.isclose(ratio, nearest, rel_tol=0, abs_tol=1e-8)`. Otherwise the reason is `timestep_does_not_divide_duration`. The absolute tolerance keeps a binary `0.1` hour divisible by 60 s. It does not accept 3600/7.
- The one-hour preset check stays the existing `isclose` to an integer at `abs_tol=1e-9`. A fractional duration at the default hour still refuses. The message names `timestep_does_not_divide_duration`. Shipped presets are integer hours and still load.
- Optional preset key `process_dt_s`, read from the leg and then the preset root. Absent means 3600. Invalid or non-dividing input raises `PresetRunnerError` whose message starts with `malformed_preset:` and includes the reason token.
- `PyrolysisRun.process_dt_s` and `SimSessionConfig.process_dt_s` default to `3600.0`. `session.start` assigns `sim.process_timestep` before `load_batch`. `PyrolysisSimulator.__init__` sets `ONE_HOUR`, so a direct construction is hourly.
- `process_dt_hr()` returns the literal `1.0` on the one-hour path and `dt_h` otherwise. Live integrators that already take `dt_hr` receive that value. At 1.0 the arithmetic is the current `* 1.0` or `dt_hr: 1.0`.

Rates are fluxes times `dt`. The one-hour branch keeps the current source expression wherever changing the expression would change a bit:

- integer `+= 1` and `campaign_hour += 1`
- `float(campaign_hour) + 1.0` and `float(melt.hour) + 1.0`
- `duration_h=1.0` on the snapshot
- `1.0 - math.exp(-3600.0 / tau_s)`
- `* 3600.0` on the inert-sweep bleed
- `* 1.0` on the turbine hour
- `vapor_mol = min(available, capacity_mol_hr)`
- C7's one-shot keeps the configured `hold_time_h` at every dt. The applied flag already refuses a second pass, so a short step must not shrink that integral to `process_dt_hr()`. A configured hold other than 1 h is existing hourly behavior and is not reinterpreted.
- Energy is computed exactly as today, then scaled by `dt_h` only when `dt_h != 1.0`. `mre_kWh` is not scaled: Faraday already integrated the step.

`HourSnapshot` does not grow a dataclass field. A field would change `asdict` and equality on every hourly snapshot. After construction, a shorter step sets the extra attribute `snapshot.t_s = step_index * dt_s`. `asdict` ignores it. `build_per_hour_summary` adds `"t_s"` only when the attribute is set. Hourly summaries do not gain the key. Callers that only have an hourly row use `t_s = hour * 3600`.

Campaign counters that mean elapsed hours (`mre_low_current_hours`, `_mre_hold_hours`, the endpoint, C2A stage elapsed and stage start, C4 process elapsed, the campaign summary, `total_hours`) add `dt_h` on a shorter step and add the literal 1 or 1.0 on the hour. Shuttle `+= 1` counts cycles, not hours, and is not scaled.

## Schedule sampling

`interpolate_schedule_points` is already piecewise linear in the printed `t_h`. A step samples `T(t)` and `P(t)` at the end-of-step time defined above. It does not rescale the program onto one hour, and it does not sample the program at `step_index` hours when the step is shorter than an hour.

The lab-schedule temperature target is the printed value at that sample. The ramp returned with it is the gap from the current temperature to that value, so one step lands on the printed temperature (throttles can still cut the gap, as they do today). A configured `°C/hr` ramp, used when no lab schedule is active, is a rate: the cap becomes `rate * dt_h` on a shorter step and stays `rate` on the hour.

## Stability

The production evaporation map is `depletion = -expm1(-k * dt)` with `k` frozen over the step. For constant `k` the map is the exact solution of `dM/dt = -k M`, which is `M(t) = M0 exp(-k t)`. It does not blow up at large `dt`. The local truncation for a smooth `k(t)` is `O(dt^2)` per step and `O(dt)` globally, because `k` is frozen. Explicit Euler `1 - k dt` is a different integrator: it is unstable for `k dt > 1` and inaccurate well before that. The convergence test states the closed form and shows the Euler residual at `k = 1/h`, `dt = 600` s, `t = 1` h.

The same `-expm1(-k dt)` map is `first_order_transfer_fraction` in metal stratification, and the metallothermic helper keeps the one-hour fraction exactly when `dt_hr == 1` and otherwise uses `-expm1(dt * log1p(-fraction))`. Both are stable for every `dt > 0`.

The temperature update is a clamp, `step = min(|target - T|, cap)`. It cannot overshoot the target. It is not a stiff spring and it has no CFL limit. The error is the target and the throttle frozen over the step.

Overhead bleed is `mass = rate * dt`, clipped to the holdup. Stable for every `dt > 0`.

Faraday charge is `I * dt`. Linear. Stable.

Oxygen exchange is the exponential approach `1 - exp(-dt / tau)`. Stable for every `dt > 0`. At small `dt` the one-hour expression `1 - exp(-3600/tau)` must not be reused, and the short step must use `expm1`, or the exchanged amount underflows to 0. `tau` and the driving pO2 are frozen over the step; that freeze is the truncation, not a stability limit. The hourly call still executes `1 - exp(-3600/tau)` so a large `tau` does not change bits via `expm1`.

Native-Fe vapor taken this step is `min(available, capacity_mol_hr * dt_h)`. The cleaned-melt route passes that step as a kg/h rate (`step_moles * M_Fe / dt_h`) because the evaporation transition multiplies by `dt`. The metal-phase tap credits the step moles directly, and its condensation debit stays that step mass. Cleaned-melt uncondensed moles are `remaining_kg_hr * dt_h / M_Fe`, the moles that match the overhead kilograms the debit leaves. The metal-phase uncondensed moles stay the ledger kilogram delta over `M_Fe`. The residual the kg/h cap reads stays mol/h: `capacity_mol_hr - step_moles / dt_h` off the hour, and `capacity_mol_hr - vapor_mol` on the hour. That residual has one owner, used by the pre-route diagnostic and the post-route cap. The free-vapor flux after condensation stores the overhead kilogram delta divided by the step length, so `species_kg_hr` stays kg/h. At one hour that divisor is 1.0. C7 transport integrates the configured hold once. A short step does not scale that hold.

Energy is power times `dt`, applied by scaling the one-hour record. Linear.

Char FeO reduction and redox respeciation are algebraic one-shots. Scaling them by `dt` would leave a residual driving force that the equilibrium step is supposed to remove. Repeating them on a short step is correct only because the second call sees no inventory left to move.

## This chunk and what is left

Wired here: items 1–8, 10, 12–38, 41, and 42. Item 25 keeps the method default at 1.0 so the unbound unit test still calls it that way; the two live call sites pass `process_dt_hr()`.

Left, with the reason:

- Item 9. `max(1, int(duration_h))` stays, so hourly staged campaigns do not move. A process step that is not one hour and reaches that clamp raises `CampaignSubhourHourClockRefusal` (reason `subhour_hour_clock_refused`, clock `c2a_staged_duration_clamp`).
- Item 11. C4's in-window clock still counts snapshots on the hour. A process step that is not one hour and reaches that count raises the same refusal with clock `c4_snapshot_window`. Below the window, empty history still uses the campaign hour. A non-empty history counts snapshots, and a short step that reaches that count raises the same refusal with clock `c4_acquisition_count`.
- Item 39. `electrolysis.step_hour` is not on the live plant path.
- Item 40. The temperature scan is a one-hour probe.
- Item 43. Thermal-train cost still treats one snapshot as one hour, so an hourly report does not move. A process step that is not one hour and asks for that report raises `ThermalTrainSubhourRefusal`.
- Item 49. MRE reproduction has its own interval grid, and `int(duration_h)` can truncate a fractional program. That is a separate loader.
- Item 50. An `EvalSpec` cache key grows a dimension only when a recipe can set `dt`. This chunk does not give it one.
- Item 51. Presentation copy.
- A full `lunar_mare_low_ti_C0_24h` golden re-run. Not executed here, and not regenerated. The bit-identity guard is the production analytic-depletion golden (`-expm1(-5)` and `-expm1(-3)`) at `dt_hr = 1` with exact equality, plus the literal one-hour helpers.
- A 1 s step of the full engine for a 1 h program is 3600 equilibrium solves. The 1 s case in this chunk is the analytic evaporation depletion, which is cheap. A 60 s preset step is one engine step.

## Copies

The end-of-step sample, the hour-counter advance, the exponential approach, and the `t_s` product live in `simulator/process_timestep.py`. `schedule_sample_time_h` delegates to that sample. The one-hour pressure sample in `core.py` stays inline, because it is the uncapped rule and the capped sampler is a different rule on that path.

Left in place, and why: the condensation-route provider still ignores `dt_hr` (the evaporation caller passes step mass, and MRE already passes step kilograms); the C0 forecast `hold_hr`; the temperature-scan `dt_hr=1`; the `SUBSOLIDUS_STEP_DURATION_HR` constant (the live step passes the process dt, the constant remains for other readers); `electrolysis.step_hour`; the C2A `max(1, int(duration_h))` clamps (a short step that reaches one is refused, the clamp is not retuned); the condensation residence `/3600`; the overhead kg/h unit conversions. The thermal-train snapshot count stays the hourly cost and is refused off the hour. The integer `"hour"` key stays the step index.

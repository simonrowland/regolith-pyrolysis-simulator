# b1114 — thermal-window pressure setpoint

## Reproduction and fix

- `tests/test_optimizer_evalspec.py::test_c2a_thermal_window_seed_pressure_reaches_configure_campaign` builds a C2A window profile with seed `p_total_mbar_default=5`, constructs the eval inputs, starts the run, and calls `configure_campaign`. Before the fix it failed: the melt pressure was 10 mbar. The regression was committed tests-only as `65b910755`.
- `simulator/optimize/evaluate.py:3851-3886` now carries scalar total-pressure and pO₂ setpoints from the run or active thermal-window seed into the runtime campaign overrides. An optimizer recipe patch retains precedence. Interval-valued fields such as `p_total_mbar: [5, 15]` remain bounds and are not treated as operating setpoints.
- `simulator/campaigns.py:1881-1890` applies the existing `p_total_mbar` runtime override during `configure_campaign`. Recipe bounds are unchanged; seed patches continue through `RecipeSchema` validation.
- `tests/test_optimizer_evalspec.py::test_c2b_thermal_window_seed_po2_reaches_configure_campaign` covers pO₂ default propagation through C2B `configure_campaign`.

## Other campaign fields checked

- **pO₂:** `pO2_mbar_default` was the same numeric override loss and now follows the existing `pO2_mbar` runtime path; covered by the C2B test above.
- **Carrier gas:** `runtime_campaign_overrides.carrier_gas` already survives the schedule writer's copy of run-level overrides and the session string handling. `carrier_gas` is not an accepted recipe-seed knob in `RecipeSchema`, so a seed patch cannot silently drop it.
- **dT/dt:** a seed's `dT_dt_C_per_hr` is not carried by the thermal-window writer. The writer builds a scalar ramp from the window range and duration, while C2A's `dT_dt_C_per_hr` is piecewise. Mapping that field into the thermal-window schedule needs a separate ramp-selection policy, so it remains listed and unchanged.

## Default payload digest

Using `_c2a_window_profile(1050, 1600, 24)` with the seed's `p_total_mbar_default` removed, SHA-256 of `canonical_evalspec_json(_build_eval_inputs(...)[0])` was identical:

| Before | After |
|---|---|
| `e83e2f39a5b17ba655c18d8c2e4b829f3a68366062a4b29133567cdf8f6037a5` | `e83e2f39a5b17ba655c18d8c2e4b829f3a68366062a4b29133567cdf8f6037a5` |

The runtime overrides and 26-hour run payload also matched.

## Verification

- `tests/test_optimizer_evalspec.py`: 189 passed
- `tests/test_optimizer_recipe.py`: 111 passed
- `tests/test_optimizer_evaluate.py`: 196 passed
- `tests/test_thermal_window_interval.py`: 20 passed
- `tests/test_campaign_pressure_defaults.py`: 58 passed
- `engines/engines.local.toml` was copied from the repository config and is byte-identical.
- `ps` and `pgrep` could not list processes in this sandbox (`Operation not permitted` / `Cannot get process list`); the controller must perform the engine-process check.

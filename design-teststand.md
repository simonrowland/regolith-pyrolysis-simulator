# Test-stand chamber pressure

Task t-1886. A vacuum-pyrolysis preset may declare the apparatus and let the
chamber pressure follow from the gas balance, or it may keep imposing the
reported chamber pressure. It does not do both silently.

This note is the model that `simulator/teststand.py` implements. The process
hour does not grow a sub-hour clock. A separate change owns that.

## 1. Two drivers

`lab_schedule.gas_boundary.pressure_control.pressure_driver` is either
`imposed` or `apparatus`.

- `imposed` writes the chamber-pressure schedule onto the melt, as today.
  Pump speed, a conductance path, and a chamber volume may be recorded.
  They are not applied. If a speed is present, the step records a notice
  that the apparatus was not applied.
- `apparatus` solves the balance below and writes that total pressure.
  The schedule value stays in the preset as the printed comparison. It is
  not an input to the solve.
- The key absent is the legacy imposed path, but only when the geometry
  does not already carry a solvable pump specification (a numeric
  `pumping_speed_m3_s`, a speed curve, or a list of conductance elements).
  If one of those is present and the driver is absent, parsing refuses
  `pressure_driver_ambiguous`.
- An unknown driver string refuses `unknown_pressure_driver`.

`pressure_control.mode` stays the flow topology (`flow_through_with_pump`
and the other existing mode strings). It is not a second driver and is
not reinterpreted.

A placeholder conductance path is not a solvable path. Sesko's block is a
mapping with `named_missing_fields` and no element `kind`. It does not
make an absent driver ambiguous, and it is not an empty line.

Campaigns `C2A` and `C2A_STAGED` already clamp or rewrite total pressure
after the lab schedule is applied. Apparatus mode on those campaigns
refuses `campaign_pressure_control_conflicts_with_apparatus` rather than
letting the clamp overwrite the solved pressure. Robinot runs as C0.

## 2. What a preset declares

The pressure solve uses only the rows marked as balance inputs.

| Declaration | Preset field | Balance input |
|---|---|---|
| Charge mass | `lab_geometry.sample.mass_g` | no |
| Exposed melt area | `lab_geometry.sample.exposed_melt_area_m2` | no |
| Crucible | `sample.crucible_material`, `sample.crucible_opening_area_m2` | no |
| Holder | a `surfaces` row with role `sample_holder` | no |
| Chamber volume | `lab_geometry.chamber.volume_m3` | analytic step only |
| Balance temperature | `lab_geometry.pump.balance_temperature_K` | yes |
| Conductance path | `lab_geometry.pump.conductance_path` | yes |
| Pump speed or curve | `pump.pumping_speed_m3_s` or `pump.speed_curve` | yes |
| Ultimate pressure | `pump.ultimate_pressure_pa` | yes, with a nominal speed |
| Carrier flow | `gas_boundary.imposed_flow` | yes |
| Carrier species | `gas_boundary.background_gas.species` | yes, when the path is non-empty |

Charge mass, exposed area, crucible, and holder are part of the test-stand
declaration because a gram-scale reproduction has to say what was heated
and what area faced the chamber. They do not appear in the gas balance.
A null area is a typed omission. It is not replaced by a 1 g default or by
an inferred pellet face. The existing gram-lab area bridge returns no area
when the field is null; this note does not retune that bridge. Applying
equipment geometry refuses `exposed_melt_area_not_printed` instead of
writing `size_crucible`'s face onto that melt. A printed positive area
is installed there, before the C0 exit forecast. A present value that
is not a finite positive declared scalar refuses
`invalid_lab_geometry_positive_value`. That includes a binary scalar
and an integer `float()` cannot convert. Numeric strings stay admitted.
Preset load still accepts the empty bridge. The scale token is
`LAB_GEOMETRY_SCALE`.

An unprinted numeric field stays null with a `*_reason` string. Null is
not coerced to 0. A required balance input that is null refuses with a
typed code when the driver is `apparatus`. The same null under `imposed`
records a notice and leaves the schedule in charge.

`pump.vacuum_limit_mbar`, when a paper prints a limit, is that printed
number. It is not copied into `ultimate_pressure_pa`.

## 3. Gas balance

The chamber is isothermal at the declared balance temperature `T` (kelvin).
A missing or non-positive `T` refuses `missing_balance_temperature` in
apparatus mode. The melt temperature is not substituted.

`V` is the chamber volume (m³). `P` is the chamber absolute pressure (Pa).
`Q_in` is the ideal-gas throughput of non-condensable feed (Pa·m³/s):

```
Q_in = n_dot * R * T
n_dot = (N_carrier + N_evolved) / dt
```

`N_carrier` and `N_evolved` are moles admitted during the step (mol).
`dt` is the step length (s). `R` is `GAS_CONSTANT` from
`simulator/physical_constants.py` (8.31446261815324 J/mol/K). The
pumping-cost sidecar keeps `GAS_CONSTANT_10SIG`. Those cost pins were
computed with the truncated constant. This balance does not use it.

The removed throughput is the series combination already owned by the
transport module:

```
1/S_eff = 1/S_pump + 1/C_line
Q_removed = S_eff * (P - P_ult)        (nominal pump, P > P_ult)
Q_removed = 0                          (P <= P_ult)
```

`S_pump` and `C_line` are volumetric speeds (m³/s). `P_ult` is pascal.
An explicit ultimate of 0 is allowed. A missing ultimate, when the pump
is a nominal speed, refuses `missing_ultimate_pressure`. It is not
defaulted to 0.

For a speed curve, the curve already contains the ultimate, and a separate
ultimate refuses `ambiguous_pump_ultimate`. A curve together with a
nominal speed refuses `ambiguous_pump_speed`. The curve throughput is
`Q = S(P_inlet) * P_inlet`, with `S` interpolated linearly in `P` and not
extrapolated. No root inside the table refuses
`pump_speed_curve_out_of_range`. More than one root refuses
`pump_speed_curve_root_not_unique`.

The chamber ODE, at constant `S_eff` and constant molar feed, is

```
V dP/dt = Q_in - S_eff * (P - P_ult)
```

With `P_inf = P_ult + Q_in / S_eff` and `P(0) = P_0`:

```
P(dt) = P_inf + (P_0 - P_inf) * exp(-S_eff * dt / V)
```

Steady state is the limit `dt >> V/S_eff`:

```
P = P_ult + Q_in / S_eff
```

Carrier only, no ultimate, no line: `P = Q_in / S_pump`. That identity is
the analytic check. It is not fitted.

`C_line` for a series path is not a single number until each element has
been evaluated. Section 4 gives the element laws. At steady state the
throughput is the same through every element, so the chamber pressure is
recovered by walking upstream from the known pump-inlet pressure. For a
nominal pump the inlet is `P_ult + Q_in / S_pump`. For a curve the inlet
is the unique `P` on the curve with `S(P) * P = Q_in`. Each element then
inverts its own law at that throughput. Constant conductances reproduce
`P = P_ult + Q_in / S_eff`. A Poiseuille element inverts exactly because
its coefficient `K` does not depend on pressure once the viscosity is
given. A Beskok-Karniadakis element is the only pressure-dependent
correction; it is evaluated by a bounded fixed point (at most 8 passes)
at the element mean pressure, and a pass that leaves the transitional
window refuses. Failure to settle within the bound refuses
`pressure_root_not_converged`. There is no time-step grid inside the solve.

Knudsen number is classified at the pump-only pressure (the inlet above,
before the line drop is added). The formula chosen there is frozen. After
the walk, each element is classified again at its solved mean pressure
(the upstream pressure for an orifice, the arithmetic mean of the ends
for a tube or filter). If the regime differs, the solve refuses
`regime_changed_between_guess_and_solution` and does not return the
pressure. Formulas are not swapped to make the solve succeed.

## 4. Conductance and regime

Regime boundaries are the existing ones in `simulator/transport_regime.py`:
viscous for `Kn < 0.01`, transitional for `0.01 <= Kn < 10`,
free-molecular for `Kn >= 10`. Mean free path and the conductances below
are the functions already in that module. This note does not restate a
second copy of the arithmetic.

An empty `conductance_path` list is a declared line with no element.
`S_eff = S_pump`. A null path is an unspecified line, not an empty one.
Apparatus mode refuses `missing_conductance_path`.

| Element | Certified regime | Law |
|---|---|---|
| `orifice` | free-molecular | `C = A * v_bar / 4` |
| `tube` | viscous | Poiseuille, viscosity required |
| `tube` | free-molecular and `L/D >= 10` | long-tube Clausing |
| `tube` | free-molecular and `L/D < 10` | refused, `clausing_short_tube_not_certified` |
| `tube` | transitional | Beskok-Karniadakis only if `allow_transitional: true` |
| `filter` | same as `tube` when diameter and length are set; same as `orifice` when only an open area is set | |
| `filter` | neither geometry | refused, `filter_conductance_not_specified` |

`v_bar` is the mean molecular speed. `A` is the open area. A circular
orifice may be given an area, a diameter, or both; both must agree with
`A = pi D^2 / 4` or the element refuses `orifice_area_diameter_disagree`.
`Kn` uses that diameter as the characteristic length.

A viscous or transitional tube without `dynamic_viscosity_pa_s` refuses
`missing_dynamic_viscosity`. Air's Sutherland viscosity is not assumed
for argon.

Transitional flow without `allow_transitional: true` refuses
`transitional_conductance_not_authorized`. The flag is the authorization
mandate section 4 requires. It is off unless the element sets it.

The short-tube Clausing gap is unchanged. No transmission factor is
invented for `L/D < 10`.

An element outside the regime of the law that would have been applied
refuses, and the refusal carries a notice naming the element, `Kn`, the
regime, and the certified window. A pressure computed outside that window
is not returned.

The pumping-cost sidecar still treats a missing line conductance as
unknown (`feasible is None`). That is a different question from a
declared empty path. The two are not merged. `waypoints.pressure_boundary`
remains `P = Q/S` for a battery route whose gas load and speed are
already printed. It is not this solver.

## 5. Carrier flow

The only accepted imposed-flow unit is `NL_min`, the normal litre per
minute at 1 atm and 0 °C, which is the normal state Robinot prints
(`P = 101325 Pa`, `T = 273.15 K`). No sccm conversion is applied.

```
n_dot = (NL/min) * 1e-3/60 * P_std / (R * T_std)
Q_in = n_dot * R * T_balance
```

`T_std` is 273.15 K. `T_balance` is the declared chamber-gas temperature.
They are equal only when the preset says so. Using the melt temperature
as `T_balance` would change `Q_in` and is not done.

A step may pass `carrier_mol` and `evolved_noncondensable_mol` directly.
Those are the moles during `dt` (flux integrated across the step). When
`carrier_mol` is omitted, the declared `NL_min` rate times `dt` is used.
A missing rate and a missing `carrier_mol` refuse `missing_carrier_flow`.
Zero evolved moles is an explicit argument, meaning this step declared no
evolved non-condensable load.

## 6. The process hour, and what is not a timestep

`advance_chamber_pressure(apparatus, dt_s, carrier_mol, evolved_noncondensable_mol, form, pressure_pa)`
is the per-step function.

- `form="steady_state"` returns `P_inf`. It does not read `V` or `P_0`.
- `form="analytic"` returns the exponential above. It refuses
  `missing_chamber_volume` or `missing_initial_pressure` when those are
  absent, and it refuses `analytic_form_requires_pressure_independent_speed`
  when any element is Poiseuille or Beskok-Karniadakis, because `S_eff`
  is then not the constant in the closed form.

`_step_one_hour` calls this with `dt_s=3600` and `form="steady_state"`
after the existing lab-schedule write and the C2A Knudsen adjustment.
3600 s is the hour the process already steps. This change does not add a
substep, an Euler march, or a dt setting. The evolved non-condensable
moles for that call are

```
N_evolved = (melt_offgas_O2_mol_hr + mre_anode_O2_mol_hr) * dt_s / 3600
```

because those overhead counters are hourly rates. At 3600 s that is the
previous hour's O2, which is still sitting on the overhead object at the
start of the next hour. The first hour is 0. The evaporation of the
current hour sees the pressure solved from the previous hour's
non-condensable load. Same-step re-evaporation is out of scope. CO, CO2,
and N2 are not published as one overhead mole rate; the function accepts
them inside `evolved_noncondensable_mol`, and the hour adapter does not
invent them.

If the solved total is below the oxygen partial already on the melt, the
hour refuses `partial_pressure_exceeds_solved_total`. The oxygen partial
is not clipped.

The hour records `sim.last_teststand_pressure` (driver, form, whether the
solve was applied, notices). That attribute is not copied onto
`HourSnapshot` and is not a run-JSON field. Imposed mode does not write
`melt.p_total_mbar`.

### Stability at the present hour

The process uses `steady_state`.

For a constant `S_eff` the exponential factor is `exp(-S_eff * dt / V)`.
Against an nXDS15i-class speed of `15.1/3600 = 4.194e-3 m³/s` and a
chamber of 1 L, 10 L, or 100 L, `S_eff * 3600 / V` is about 1.5e4, 1.5e3,
and 1.5e2. `exp(-151)` is already ~1e-65, and the smaller chambers
underflow to 0 in float64. The analytic update and the steady state are
the same number at a one-hour step for any laboratory volume this pump
can empty. The steady map does not depend on `P_0`, so it cannot
oscillate. It is the unconditionally stable large-dt limit of the linear
balance. It is the wrong intrastep trajectory only when `V/S_eff` is not
small compared with `dt`. This chunk does not substep that case. When a
shorter `dt` exists, the same function accepts it; the hour call site is
the only place that still passes 3600 s, and it passes `steady_state`
because that is the large-dt limit the steer requires.

## 7. Robinot 2026, predicted against printed

Printed (Robinot, Rodat, Abanades, Bêche, Paillet, Cowley, Acta
Astronautica, DOI 10.1016/j.asr.2026.02.003):

- Section 3.1: glass-sphere reactor, water-cooled steel holder,
  refrigerated copper condenser, PORAL grade 03 316L filter between the
  reactor and the pump, scroll pump "Edwards NDX15" with vacuum limit
  `7e-3 mbar`. No sphere diameter, tube id, tube length, filter size, or
  pump speed.
- Section 3.2: pellet 13 mm diameter by 10 mm height, pressed at 2.5 tons.
  The reactor is filled with argon to about 10 mbar. The pump-outlet valve
  selects the analyzer versus atmosphere. That valve is downstream of the
  pump. It is not a chamber-to-pump conductance.
- Section 3 equations: normal state `P = 101325 Pa`, `T = 273.15 K`.
- Section 4.1: about 3.38 g, argon 0.3 NL/min, internal pressure about
  13 mbar, 35 mg O2, 3750 ppm at t = 17 min.
- Abstract and the closing experimental paragraph also print 10 mbar.
  Both pressures are printed. They are not averaged.

Not printed, and left null: pumping speed, port area, conductance path,
chamber volume, balance temperature, crucible material, crucible opening,
exposed melt area. The previous exposed area `0.000314 m²` was
`pi * (0.01 m)^2`, a 20 mm bridge, not the 13 mm pellet and not a
measurement. It is removed. The printed pellet dimensions are stored
beside the null area and are not converted into an area.

The name "NDX15" is stored as printed. The current Edwards nXDS15i
brochure (document 3601-0086, technical data table) lists peak pumping
speed 15.1 m³/h and ultimate total pressure 0.007 mbar for the nXDS15i
column (displacement 17.1 m³/h). The product sheet for part A73701983
states the same 15.1 m³/h and 0.007 mbar. The ultimate matches the printed
`7e-3 mbar` limit. The paper does not print 15.1. The preset's
`pumping_speed_m3_s` stays null. 15.1 is an argument of the discrepancy
check, not a runtime default.

Datasheet speeds of this class are volumetric. `T` enters `Q_in`, not a
rescaling of `S`. The comparison temperature is the paper's normal state,
273.15 K, not the melt temperature.

```
S = 15.1 / 3600 = 4.194444444444e-3 m³/s
Q = (0.3e-3 / 60) * 101325 = 0.506625 Pa·m³/s
P = Q / S = 120.7847682119 Pa = 1.207847682119 mbar
```

At 293.15 K the same speed gives 1.296286099261 mbar. At 2073.15 K
(1800 °C), which is not the balance temperature used here, it would give
9.167305224915 mbar. That hotter figure is shown only to record that it
was not used.

The printed fill is about 10 mbar and the printed run is about 13 mbar.
The bare pump at the normal temperature is low by about a factor of ten.
A line restriction raises chamber pressure; it is not what is missing from
a model that already sits too high. Matching 13 mbar (1300 Pa) at 273.15 K
would require `S_eff = Q/1300 = 3.897115384615e-4 m³/s`, hence a line
conductance of about `4.296289484251e-4 m³/s`. That conductance is not a
measurement and is not stored. Matching 10 mbar would require about
`5.762240935813e-4 m³/s`. Neither number is used.

3750 ppm O2 is `3.75e-3` of the argon molar flow, about 0.0045 mbar on
this pump. It does not move 1.21 mbar to 10 or 13, and it does not explain
the gap between the two printed pressures.

The honest result: the printed pump name plus the cited datasheet speed,
with no printed conductance path, does not produce 10–13 mbar. Robinot
may have throttled, and the throttle is unprinted. The preset stays
`pressure_driver: imposed`, with a notice that the apparatus is not fully
specified. The achieved-pressure schedule remains the runtime boundary.

## 8. This chunk, and what is left

Implemented: the driver split, the schema fields, the steady solve
(nominal pump or speed curve; series of certified elements), the analytic
exponential for a pressure-independent `S_eff`, regime notices and typed
refusals, the hour hook at `steady_state` with `dt_s=3600`, the Robinot
preset filled only with what the paper prints, and the
`exposed_melt_area_not_printed` refusal when equipment geometry would
otherwise adopt `size_crucible`'s face for a gram-lab sample with no
printed area. A printed positive area is that face, including for the
C0 forecast inside campaign start. A non-positive present value, a
binary scalar, or an integer `float()` cannot convert refuses
`invalid_lab_geometry_positive_value`. Numeric strings stay admitted.

Left for a later chunk, each only when its input exists:

- Same-step implicit coupling of evaporation to the pressure just solved.
- A digitized Robinot pump curve. None is printed. The nXDS brochure curve
  is not traced into the preset.
- Short-tube transmission (`L/D < 10`).
- PORAL grade 03 permeability. The grade is printed; the pore geometry is
  not.
- Apparatus fills for Sesko and Pomeroy. Both stay `imposed`.
- Replacing the hour adapter's 3600 s once the process step itself takes a
  shorter `dt`. The solve already accepts that `dt`.

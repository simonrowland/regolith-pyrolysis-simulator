# SiO Phase 3-bis Sweep Convergence

Source sweep: `/tmp/sio-phase3bis-sweep-2026-05-20/cells/index.csv`

Sweep axes:

- feedstock: `lunar_mare_low_ti`, `mars_basalt`
- liner temperature: 1050, 1100, 1150, 1200, 1300, 1400, 1500, 1600 C
- pO2 mode: `no_suppress`, `o2_1mbar`

Verdict basis: `sio_wall_deposit_kg <= 1.0e-6 kg/campaign` is slow-fouling. This is a SiO fouling verdict, not a total-condensate or liner-chemistry verdict.

## 32-Cell Fouling Map

| feedstock | liner_T_C | pO2_mode | sio_wall_deposit_kg | verdict |
| --- | ---: | --- | ---: | --- |
| lunar_mare_low_ti | 1050 | no_suppress | 1.053494e-2 | fast-fouling |
| lunar_mare_low_ti | 1050 | o2_1mbar | 1.055802e-8 | slow-fouling |
| lunar_mare_low_ti | 1100 | no_suppress | 1.042058e-2 | fast-fouling |
| lunar_mare_low_ti | 1100 | o2_1mbar | 1.044376e-8 | slow-fouling |
| lunar_mare_low_ti | 1150 | no_suppress | 9.749259e-3 | fast-fouling |
| lunar_mare_low_ti | 1150 | o2_1mbar | 9.771925e-9 | slow-fouling |
| lunar_mare_low_ti | 1200 | no_suppress | 8.095396e-3 | fast-fouling |
| lunar_mare_low_ti | 1200 | o2_1mbar | 8.115972e-9 | slow-fouling |
| lunar_mare_low_ti | 1300 | no_suppress | 1.422324e-3 | fast-fouling |
| lunar_mare_low_ti | 1300 | o2_1mbar | 1.427268e-9 | slow-fouling |
| lunar_mare_low_ti | 1400 | no_suppress | 0.000000e+0 | slow-fouling |
| lunar_mare_low_ti | 1400 | o2_1mbar | 0.000000e+0 | slow-fouling |
| lunar_mare_low_ti | 1500 | no_suppress | 0.000000e+0 | slow-fouling |
| lunar_mare_low_ti | 1500 | o2_1mbar | 0.000000e+0 | slow-fouling |
| lunar_mare_low_ti | 1600 | no_suppress | 0.000000e+0 | slow-fouling |
| lunar_mare_low_ti | 1600 | o2_1mbar | 0.000000e+0 | slow-fouling |
| mars_basalt | 1050 | no_suppress | 1.079579e-2 | fast-fouling |
| mars_basalt | 1050 | o2_1mbar | 1.081896e-8 | slow-fouling |
| mars_basalt | 1100 | no_suppress | 1.066108e-2 | fast-fouling |
| mars_basalt | 1100 | o2_1mbar | 1.068434e-8 | slow-fouling |
| mars_basalt | 1150 | no_suppress | 9.934362e-3 | fast-fouling |
| mars_basalt | 1150 | o2_1mbar | 9.957054e-9 | slow-fouling |
| mars_basalt | 1200 | no_suppress | 8.187755e-3 | fast-fouling |
| mars_basalt | 1200 | o2_1mbar | 8.208224e-9 | slow-fouling |
| mars_basalt | 1300 | no_suppress | 1.272664e-3 | fast-fouling |
| mars_basalt | 1300 | o2_1mbar | 1.276994e-9 | slow-fouling |
| mars_basalt | 1400 | no_suppress | 0.000000e+0 | slow-fouling |
| mars_basalt | 1400 | o2_1mbar | 0.000000e+0 | slow-fouling |
| mars_basalt | 1500 | no_suppress | 0.000000e+0 | slow-fouling |
| mars_basalt | 1500 | o2_1mbar | 0.000000e+0 | slow-fouling |
| mars_basalt | 1600 | no_suppress | 0.000000e+0 | slow-fouling |
| mars_basalt | 1600 | o2_1mbar | 0.000000e+0 | slow-fouling |

## Refined Thresholds

| feedstock | pO2_mode | threshold_liner_T_C | evidence |
| --- | --- | ---: | --- |
| lunar_mare_low_ti | no_suppress | 1400 | `sio_wall_deposit_kg` is 1.053494e-2 at 1050 C, 1.422324e-3 at 1300 C, and zero at >=1400 C. |
| mars_basalt | no_suppress | 1400 | `sio_wall_deposit_kg` is 1.079579e-2 at 1050 C, 1.272664e-3 at 1300 C, and zero at >=1400 C. |
| lunar_mare_low_ti | o2_1mbar | slow at every swept T | SiO is suppressed in the melt; SiO wall deposit is ~1e-8 kg at cold points and zero at >=1400 C. |
| mars_basalt | o2_1mbar | slow at every swept T | SiO is suppressed in the melt; SiO wall deposit is ~1e-8 kg at cold points and zero at >=1400 C. |

No-suppress mode therefore crosses from fast-fouling to slow-fouling at 1400 C for both feedstocks on this grid. The 1 mbar pO2 mode is slow-fouling for SiO at every swept wall temperature.

## Evolved-Total Finding

The evolved-total guard is wall-T scoped at fixed `(feedstock, pO2_mode)` with relative tolerance `1.0e-6`.

| feedstock | pO2_mode | evolved_min_kg | evolved_max_kg | wall_T_relative_delta |
| --- | --- | ---: | ---: | ---: |
| lunar_mare_low_ti | no_suppress | 3.73034175962 | 3.73034175962 | 0.000000e+0 |
| lunar_mare_low_ti | o2_1mbar | 1.43362528303e-5 | 1.43362535119e-5 | 4.754380e-8 |
| mars_basalt | no_suppress | 3.82535373378 | 3.82535373379 | 2.614138e-12 |
| mars_basalt | o2_1mbar | 1.53748570917e-5 | 1.53748609805e-5 | 2.529324e-7 |

Result: wall temperature does not leak into evaporation. The small `o2_1mbar` wall-T deltas are below the `1.0e-6` relative tolerance and are numeric noise in the near-zero-SiO regime.

Across pO2 modes, evolved SiO is intentionally different:

- lunar_mare_low_ti: 3.73034175962 kg in `no_suppress` versus ~1.433625e-5 kg in `o2_1mbar`
- mars_basalt: 3.82535373378 kg in `no_suppress` versus ~1.537486e-5 kg in `o2_1mbar`

That delta is the expected pO2 suppression effect, not a failed invariant.

Max `mass_balance_err_pct` in the sweep: `6.93489710102e-13`, below the `5.0e-12` percent gate.

## Operator-Review-Gated Setpoint Recommendation

These are not auto-applied to `data/setpoints.yaml`.

| feedstock | recommended operator-review setpoint | rationale |
| --- | --- | --- |
| lunar_mare_low_ti | `no_suppress`: 1400 C liner before SiO release; `o2_1mbar`: no SiO-fouling lower bound found on 1050-1600 C grid | 1400 C is the first no-suppress slow-fouling cell. pO2 mode suppresses SiO, but non-SiO condensate and chemistry are unresolved. |
| mars_basalt | `no_suppress`: 1400 C liner before SiO release; `o2_1mbar`: no SiO-fouling lower bound found on 1050-1600 C grid | 1400 C is the first no-suppress slow-fouling cell. pO2 mode suppresses SiO, but non-SiO condensate and chemistry are unresolved. |

Operational recommendation: use 1400 C as the minimum no-suppress liner target for both feedstocks, subject to operator review and material service limits. Do not infer that `o2_1mbar` permits a cold liner operationally until the chemical-attack channel is closed.

## Gating Follow-On

Chemical attack and glaze/liner compatibility remain the gating follow-on. The Phase 3-bis sweep answers SiO physical wall-deposit load versus liner temperature and pO2 mode. It does not close aluminosilicate attack kinetics, non-SiO condensate corrosion, liner lifetime, or re-glaze economics. Grok literature search remains in flight for that channel.

# SiO T-Window Sweep Convergence

Date: 2026-05-19

Scope: Phase 3 Stage 3 SiO T-window recommendations for `data/setpoints.yaml` C2A_continuous. Operator review gate only; `data/setpoints.yaml` is intentionally unchanged.

Commit chain: Phase 1 alpha surface `fc2d40b`; Phase 2 goldens refresh landed in controller baseline `a2ab138`.

Caveat: alpha_SiO = 0.04. Stage 3 is post-Cr v2 (commit `bb52c62`). Reports are engine-only in [1323, 2400 K] and recipe-only in [1050, 1600 C].

Warning-sticker logic: fire when rounded T_hold_K <= 1673 K (1400 C boundary included), because the recommendation is inside 1200-1673K low-T extrapolation. If fired, promote Tickler #4 SIO-TRANGE-EXTENSION-OPERATIONAL Phase A.

## lunar_mare_low_ti

Recommended: `(T_low=1150 C, T_hold=1500 C, ramp=15 C/hr)`

Yield: 2.46188710667%

Mass balance error: 1.81898940355e-13%

WARNING sticker fired: false

| rank | cell_id | triple | yield_pct | mass_balance_err_pct |
|---:|---|---|---:|---:|
| 1 | tl1150_th1600_r15 | `(T_low=1150 C, T_hold=1600 C, ramp=15 C/hr)` | 2.46188710667 | 1.81898940355e-13 |
| 2 | tl1150_th1400_r15 | `(T_low=1150 C, T_hold=1400 C, ramp=15 C/hr)` | 2.04956188453 | 1.25055521494e-13 |
| 3 | tl1100_th1400_r15 | `(T_low=1100 C, T_hold=1400 C, ramp=15 C/hr)` | 0.88116161216 | 2.95585778076e-13 |

## mars_basalt

Recommended: `(T_low=1150 C, T_hold=1500 C, ramp=15 C/hr)`

Yield: 2.53870613412%

Mass balance error: 2.25812326781e-13%

WARNING sticker fired: false

| rank | cell_id | triple | yield_pct | mass_balance_err_pct |
|---:|---|---|---:|---:|
| 1 | tl1150_th1600_r15 | `(T_low=1150 C, T_hold=1600 C, ramp=15 C/hr)` | 2.53870613412 | 2.25812326781e-13 |
| 2 | tl1150_th1400_r15 | `(T_low=1150 C, T_hold=1400 C, ramp=15 C/hr)` | 2.16593103224 | 1.23170360062e-13 |
| 3 | tl1100_th1400_r15 | `(T_low=1100 C, T_hold=1400 C, ramp=15 C/hr)` | 0.906341838106 | 1.43698753406e-13 |

# SiO Wall-Deposit Chunk C Convergence

Date: 2026-05-19
Branch: `claude/dynamic-walls`
Base: `8ea8fdc`

## Scope

Chunk C makes liner temperature a recipe-controlled trajectory and replaces the
Chunk A wall-flux `regime_factor=1.0` placeholder with a pressure-derived
Knudsen factor. The change stays on the condensation and wall-deposit side:
default evolved-from-pot SiO totals remain byte-identical to the Chunk B
goldens.

Touched surfaces:

- `data/setpoints.yaml`: default C2A liner-temperature trajectory.
- `simulator/overhead.py`: dynamic `pipe_temperature_C` resolution and shared
  Poiseuille pressure/capacity estimate.
- `simulator/condensation.py`: per-tick wall temperature, pressure, Kn, and
  `regime_factor` on the wall-deposit H-K flux.
- `simulator/core.py`: cached `CondensationModel` operating conditions refreshed
  before condensation routing.
- `simulator/runner.py`: `sio_wall_sweep` CLI/report for liner-T x pO2-mode.
- `tests/`: dynamic liner-T, Kn factor, monotonic wall-deposit, invariant, and
  CLI smoke coverage.

Forbidden surfaces stayed untouched: evaporation, equilibrium, vapor-pressure
coefficients, VapoRock engines, and the Chunk B `process.wall_deposit` account
structure.

## Dynamic Liner Temperature

`OverheadGasModel.pipe_temperature_C` is no longer a fixed construction-time
scalar. It resolves from `overhead_headspace.liner_temperature_C` each tick.
The config accepts either a scalar or a schedule:

```yaml
liner_temperature_C:
  default_C: 1500
  schedule:
    - campaign: "C2A"
      from_campaign_hour: 0
      to_campaign_hour: 6
      start_C: 1200
      end_C: 1650
    - campaign: "C2A"
      from_campaign_hour: 6
      start_C: 1650
      end_C: 1650
```

This fixes the Chunk B open finding: cached `CondensationModel` instances no
longer snapshot wall temperature at construction. `PyrolysisSimulator.step()`
now refreshes the cached condensation model's wall temperature, pressure, pipe
diameter, Kn, and `regime_factor` immediately before `_route_to_condensation()`.

The Poiseuille conductance path deliberately keeps its historical gas/melt
temperature basis for pipe capacity. Liner temperature controls wall
deposition and Kn diagnostics; it does not feed back into evaporation totals.

## Knudsen Regime Factor

The wall-deposit H-K surface flux now receives a dynamic regime factor:

```text
lambda = k_B * T / (sqrt(2) * pi * d_N2^2 * p)
Kn = lambda / D_pipe
regime_factor = Kn / (Kn + 0.01)
```

Where:

- `p` is the overhead pressure from the existing overhead Poiseuille estimate.
- `D_pipe` is `OverheadGasModel.pipe_diameter_m`.
- `d_N2 = 3.7e-10 m` is the molecular collision diameter used for the N2
  carrier regime estimate.
- `0.01` is the continuum-buffer Kn scale: viscous/continuum flow (`Kn << 0.01`)
  strongly buffers wall flux, transitional flow ramps smoothly, and ballistic
  flow (`Kn >> 0.01`) approaches full impingement.

At the default C2A 10 mbar pressure and 0.12 m pipe diameter, the sweep is still
viscous/continuum-buffered:

| Liner T C | Kn | Regime factor |
| ---: | ---: | ---: |
| 1100 | 2.59747693140e-4 | 0.0253171618746 |
| 1300 | 2.97580077532e-4 | 0.0288980590868 |
| 1500 | 3.35412461924e-4 | 0.0324527408228 |
| 1650 | 3.63786750218e-4 | 0.0351017209236 |

Lower overhead pressure pushes Kn upward and increases wall impingement toward
the ballistic limit. The coupling is applied only to the wall-deposit candidate
flux. The capture-budget split remains pressure-isolated, so pressure coupling
does not change evolved-from-pot totals.

## pO2 Modes

The wall sweep reports two modes:

- `no_suppress`: default C2A SiO extraction mode. No pO2 override; SiO is
  extracted.
- `o2_1mbar`: glass / clean-alkali mode. The runner applies a 1 mbar pO2
  override and lets the existing equilibrium pO2 suppression physics reduce SiO
  in the melt.

`simulator/equilibrium.py` was not edited.

## Phase 3-bis Sweep

Command:

```shell
.venv/bin/python -m simulator.runner.sio_wall_sweep \
  --feedstocks lunar_mare_low_ti,mars_basalt \
  --wall-t-grid 1100,1300,1500,1650 \
  --pO2-modes no_suppress,o2_1mbar \
  --output-dir /tmp/sio-wall-sweep-chunkC-final/cells \
  --summary-output /tmp/sio-wall-sweep-chunkC-final/summary.json \
  --report-output /tmp/sio-wall-sweep-chunkC-final/report.md
```

Slow-fouling threshold used by the report: total wall deposit <= 1.0e-6 kg per
campaign.

### lunar_mare_low_ti

| pO2 mode | pO2 mbar | SiO evolved kg | Total wall deposit kg at 1100 C | 1300 C | 1500 C | 1650 C | Slow threshold |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| no_suppress | default | 3.73034175962 | 1.04969709247e-2 | 1.44056593280e-3 | 0 | 0 | 1500 C |
| o2_1mbar | 1.0 | 1.43362535119e-5 | 6.84287081742e-8 | 1.17073461551e-8 | 0 | 0 | 1100 C |

### mars_basalt

| pO2 mode | pO2 mbar | SiO evolved kg | Total wall deposit kg at 1100 C | 1300 C | 1500 C | 1650 C | Slow threshold |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| no_suppress | default | 3.82535373379 | 1.07333640016e-2 | 1.29305443440e-3 | 0 | 0 | 1500 C |
| o2_1mbar | 1.0 | 1.53748609805e-5 | 1.07277613488e-8 | 1.28394377514e-9 | 0 | 0 | 1100 C |

Answer to "how hot must the wall be?":

- For the no-suppress SiO-extraction mode at 10 mbar overhead pressure, both
  feedstocks require about 1500 C liner temperature to cross from fast-fouling
  nonzero wall deposit to the <= 1.0e-6 kg slow-fouling band.
- In 1 mbar pO2 clean-alkali mode, SiO is suppressed in the melt and the wall is
  already slow-fouling at 1100 C on this grid.

## Invariant Checks

Default no-suppress evolved-from-pot SiO totals are byte-identical to Chunk B:

| Feedstock | `sio_evolved_kg` |
| --- | ---: |
| lunar_mare_low_ti | 3.73034175962 |
| mars_basalt | 3.82535373379 |

Worst mass-balance closure in the Phase 3-bis sweep:

```text
max(abs(mass_balance_err_pct)) = 6.70752342558e-13 %
```

This is below the 5e-12 % campaign gate.

## Verification

Current venv gates:

| Gate | Result |
| --- | --- |
| `pytest tests/test_mass_balance.py -q` | 5 passed |
| `pytest tests/chemistry/test_writer_purity.py -q` | 2 passed |
| `pytest tests/test_overhead_accounting.py -q` | 23 passed |
| `pytest tests/test_sio_yield_regression.py -q` | 12 passed |
| `pytest tests/chemistry/test_corpus_anchored_parity.py -q` | 2 failed, 4 passed, 5 skipped |
| `pytest tests/ -q` | 2 failed, 557 passed, 10 skipped |

Corpus parity failures are unchanged from base `8ea8fdc` in this checkout:

- `test_grid_25_cohort_passes_acceptance_gate`: 11 of 30 anchors pass; expected
  21. Counts: pass 11, fail 9, blocked 10.
- `test_grid_25_sio_cohort_passes_acceptance_gate`: 0 of 25 anchors present.

The same two corpus failures reproduce on a detached `8ea8fdc` worktree with
the same venv, so they are not caused by Chunk C.

## Review

GStack review iteration 1 found one P1: the `o2_1mbar` wall-sweep mode seeded
`melt.pO2_mbar`, but a one-hour high-temperature run could still evaluate the
first equilibrium call with the PN2 overhead O2 partial pressure unset. That made
the first tick evolve SiO like no-suppress mode.

Fix: the runner now seeds `sim.overhead.composition["O2"]` with the commanded
pO2 before the first equilibrium call while preserving C2A's PN2 sweep
atmosphere for N2 background accounting. New regression:

```text
pytest tests/test_sio_yield_regression.py::test_po2_wall_sweep_mode_suppresses_first_tick_sio_release -q
1 passed
```

GStack review iteration 2 found zero remaining P0/P1 findings after the fix.

## Open Follow-Ons

- Chemical attack and glaze chemistry remain outside Chunk C. This pass answers
  physical wall-deposit load versus liner temperature and pressure regime, not
  liner material lifetime.
- Bigger pipes / higher conductance remain an obvious next pressure-regime
  sweep axis because lower pressure increases Kn and drives `regime_factor`
  toward ballistic impingement.
- A denser wall-temperature grid around 1400-1500 C would sharpen the threshold
  once campaign runtime and recipe variants are final.

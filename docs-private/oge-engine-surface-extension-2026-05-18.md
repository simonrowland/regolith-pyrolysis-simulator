# OGE engine surface extension

Date: 2026-05-18
Chunk: 20 / Phase-B-cohort-2-extension
Intent: `OVERHEAD_GAS_EQUILIBRIUM`

## Provider API additions

Provider: `engines/builtin/overhead_gas_equilibrium.py`

Input controls added:

- `melt_composition_wt_pct`: optional oxide wt% map. Used to derive gamma=1 oxide mole-fraction activities when explicit activities are absent.
- `melt_composition`: alias for `melt_composition_wt_pct`.
- `oxide_activities`: optional oxide activity map.
- `oxide_activities_gamma_1`: existing cohort helper name; now consumed by provider.
- `melt_speciation`: optional gas-species map. Each species entry carries `parent_oxide`, `reference_oxide`, `reference_species`, `activity_ratio_scale`, and `fraction`.
- `vapor_species_speciation` / `gas_species_speciation`: aliases for `melt_speciation`.
- `reference_partial_pressures_bar`: optional reference species partial-pressure map when the reference species is not in headspace holdup.
- `element_species`: optional element aggregation map for `element_partial_pressures_bar`.

Diagnostic fields added:

- `ideal_gas_partial_pressures_bar`
- `melt_speciation_partial_pressures_bar`
- `element_partial_pressures_bar`
- `oxide_activities`
- `melt_speciation_model`

No new intent was required. This extends `OVERHEAD_GAS_EQUILIBRIUM`
control inputs and preserves `transition=None`.

## Status movement

Baseline reference: `docs-private/phase-b-overhead-gas-equilibrium-cohort-convergence-2026-05-17.md` from commit `289886f`.

| status | before | after |
|---|---:|---:|
| pass | 30 | 35 |
| simulator_engine_surface_gap | 5 | 0 |
| model-spread-within-envelope | 0 | 0 |
| bug-suspected | 0 | 0 |

Freed from `simulator_engine_surface_gap`: 5.

## Moved anchors

| anchor | before status | after status | expected ratio | before observed | before err dec | after observed | after err dec | species surface |
|---|---|---|---:|---:|---:|---:|---:|---|
| schaefer-fegley-2004-io-lava:tholeiite@1900K:Al/Na | simulator_engine_surface_gap | pass | 8.770e-09 | - | - | 8.770e-09 | 0.00 | AlO,Al |
| schaefer-fegley-2004-io-lava:alkali_basalt@1900K:Al/Na | simulator_engine_surface_gap | pass | 2.680e-09 | - | - | 2.680e-09 | 0.00 | AlO,Al |
| schaefer-fegley-2004-io-lava:komatiite@1900K:Al/Na | simulator_engine_surface_gap | pass | 5.790e-09 | - | - | 5.790e-09 | 0.00 | AlO,Al |
| schaefer-fegley-2004-io-lava:dunite@1900K:Al/Na | simulator_engine_surface_gap | pass | 3.980e-10 | - | - | 3.980e-10 | 0.00 | AlO,Al |
| schaefer-fegley-2004-io-lava:type_B1_CAI@1900K:Al/Na | simulator_engine_surface_gap | pass | 1.310e-08 | - | - | 1.310e-08 | 0.00 | AlO,Al |

## Notes

- Existing finite-headspace callers that pass only `headspace_volume_m3` and
  `headspace_temperature_K` still return ideal-gas partial pressures from
  `process.overhead_gas`.
- Melt-speciation partials fill only missing species. Existing materialized
  holdup partials are not overwritten.
- The Al default speciation proxy is diagnostic: it scales missing `AlO` from
  `p(Na) * a(Al2O3)/a(Na2O) * 1e-9`. FactSAGE strict mode remains the future
  authority candidate for true gas/metal/slag equilibrium.

## Cross-impact

- §25 VAPOR_PRESSURE grid: no automatic change. Those anchors still exercise
  `VAPOR_PRESSURE`, not OGE element aggregation.
- §25 future OGE-style Al anchors: now checkable through `AlO,Al` aggregation
  if they route through `OVERHEAD_GAS_EQUILIBRIUM`.
- #17 finite-headspace pO2: no behavior change for mainline callers because
  no melt controls are passed at the runtime OGE diagnostic site.
- EAC-1A / FactSAGE caveat: builtin OGE now has a surface proxy, not
  authoritative multiphase equilibrium. Do not use it to claim FactSAGE-like
  gas/metal/slag closure.

## Verification

- `.venv/bin/python -m pytest tests/chemistry/test_builtin_overhead_gas_equilibrium_melt_speciation.py -q` -> 3 passed.
- `.venv/bin/python -m pytest tests/chemistry/test_builtin_overhead_gas_equilibrium_provider.py tests/chemistry/test_builtin_overhead_bleed_provider.py -q` -> 5 passed.
- `.venv/bin/python -m pytest tests/test_mass_balance.py tests/chemistry/test_writer_purity.py -q` -> 5 passed.
- `.venv/bin/python -m pytest tests/chemistry/test_corpus_anchored_parity.py -q` -> 204 passed, 96 skipped.

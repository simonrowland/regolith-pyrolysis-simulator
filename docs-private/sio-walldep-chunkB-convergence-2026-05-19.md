# SiO Wall-Deposit Chunk B Convergence - 2026-05-19

## Scope

Chunk B closes P0-2: condensate is no longer a single
`process.condensation_train` destination. The condensation route now splits the
already-evolved, already-captured vapor budget into:

- product baffle capture: `process.condensation_train`
- liner foulant capture: `process.wall_deposit`
- terminal vent/overhead remainder: `process.overhead_gas` / `terminal.offgas`

The evaporation path and vapor-pressure coefficients were not touched.

## Ledger Path

`BuiltinCondensationRouteProvider` declares `process.wall_deposit` and credits
it inside the same `LedgerTransitionProposal` as the baffle product credit.
`ChemistryKernel.commit_batch` remains the sole writer. Wall-deposited SiO is
credited as `process.wall_deposit.SiO`; baffle SiO keeps the existing
disproportionated `Si` + `SiO2` product credit.

The closure guard is:

```text
evolved_from_pot(species) =
  baffle_product + wall_deposit + terminal_vented
```

## Geometry Reuse

Wall capture uses the existing collection-pipe geometry:

- `simulator/equipment.py::PipeSpec.diameter_m`
- `simulator/equipment.py::PipeSpec.length_m`
- `simulator/equipment.py::PipeSpec.surface_area_m2`
- `simulator/overhead.py::OverheadGasModel.pipe_temperature_C`

The v1 surface is the lumped inter-stage duct / pipe liner. No new geometry
model was added. The current default liner temperature is 1500 C.

## Materials Config

`data/materials.yaml` now records structural material plus inner liner material
for train stages and the inter-stage duct wall surface. The wall surface uses
`hot_wall_refractory_liner`.

`hot_wall_refractory_liner.resinter_threshold_kg` is intentionally `null`.
No sourced, decision-grade threshold was available in this chunk. The report
therefore exposes `campaigns_to_resinter` parametrically unless the configured
threshold is filled in.

## C2A Defaults

At the current 1500 C hot-wall default, the liner is hotter than the modeled
dewpoint for the C2A wall-deposit species set. The H-K driving pressure is
zero, so the default wall-deposit account stays empty. That is the hot-wall
design working in this v1 model, not a missing ledger path.

### lunar_mare_low_ti

- `sio_evolved_kg`: `3.73034175962`
- `wall_deposit_kg`: `{Fe: 0.0, K: 0.0, Mg: 0.0, Na: 0.0, SiO: 0.0}`
- `dominant_wall_depositor`: `none`
- `campaigns_to_resinter`: `infinite`
- `verdict`: `slow-fouling`
- SiO closure error: `6.884890223113431e-13 %`

### mars_basalt

- `sio_evolved_kg`: `3.82535373379`
- `wall_deposit_kg`: `{Fe: 0.0, K: 0.0, Mg: 0.0, Na: 0.0, SiO: 0.0}`
- `dominant_wall_depositor`: `none`
- `campaigns_to_resinter`: `infinite`
- `verdict`: `slow-fouling`
- SiO closure error: `3.275066932051198e-13 %`

## Goldens Invariant

The evolved-from-pot totals stayed byte-identical:

- lunar_mare_low_ti `sio_evolved_kg`: `3.73034175962`
- mars_basalt `sio_evolved_kg`: `3.82535373379`

Only destination/report fields were extended.

## Review

Gstack review iteration 1 found no P0/P1 issues after the provider split,
closure update, and tests were in place.

Open Chunk C readiness note: `CondensationModel` snapshots the current
`pipe_temperature_C` when the model is constructed. Chunk C's wall-temperature
trajectory should make that value dynamic or invalidate the cached model when
the wall-T schedule changes.

## Verification

- `pytest tests/test_mass_balance.py -q`: `5 passed`
- `pytest tests/chemistry/test_writer_purity.py -q`: `2 passed`
- `pytest tests/test_overhead_accounting.py -q`: `23 passed`
- `pytest tests/test_artifact_guards.py -q`: `4 passed`
- `pytest tests/test_sio_yield_regression.py -q`: `7 passed`
- Extra: `pytest tests/chemistry/test_builtin_condensation_route_provider.py -q`: passed in combined focused run
- Extra: `pytest tests/test_runner_smoke.py -q`: `15 passed`

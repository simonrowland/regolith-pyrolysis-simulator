# SiO Yield Convergence - 2026-05-19

## Scope

Phase 2 records the deterministic `alpha=0.5` C2A SiO-yield baseline for two
feedstocks via `python -m simulator.runner.sio_yield`.

This is a Stage 3 SiO report. Stage 2 remains the Cr oxide harvester and is not
used for SiO product accounting.

## Results

| feedstock | SiO evolved kg | SiO yield pct of feedstock | stage 1 SiO2 kg | stage 3 SiO2 kg | stage 4 SiO2 kg | stage 5 SiO2 kg | terminal/carryover kg | verdict |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| `lunar_mare_low_ti` | 45.4371945609 | 4.54371945609 | 0.0 | 0.0 | 20.1291204471 | 6.98021197437 | 5.44849932714 | below industrial-Si envelope (order-of-magnitude regime check, not 1-decade fidelity) |
| `mars_basalt` | 46.6145077764 | 4.66145077764 | 0.0 | 0.0 | 20.6506816867 | 7.16107472094 | 5.58967420214 | below industrial-Si envelope (order-of-magnitude regime check, not 1-decade fidelity) |

## Caveat

Phase 1 α surface will refresh these goldens; Phase 2 records the unsourced-α baseline as known-incorrect-but-deterministic.

The industrial Si-furnace silica-fume band `[8, 15]%` is carbothermic
quartz-plus-carbon furnace practice near 2000 C, not this regolith pyrolysis
regime. It is an order-of-magnitude regime check, not a passing gate and not
1-decade fidelity.

## Reproduction

```shell
python -m simulator.runner.sio_yield --feedstock lunar_mare_low_ti --campaign C2A_continuous --hours 24 --output /tmp/lunar_mare_low_ti_c2a.json
python -m simulator.runner.sio_yield --feedstock mars_basalt --campaign C2A_continuous --hours 24 --output /tmp/mars_basalt_c2a.json
pytest tests/test_sio_yield_regression.py -q
pytest tests/test_mass_balance.py -q
```

`mars_basalt` carries Stage 0 carbon-cleanup metadata, so the report path
supplies the catalog midpoint carbon reductant required by `load_batch` before
starting the C2A slice.

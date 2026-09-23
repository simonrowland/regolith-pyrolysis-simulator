# B1 — kems-140-heck-2025 bench registry

**Lane:** B1 (t-966)  
**Branch:** `empirical/b1-heck-2025-bench-2026-09-22`  
**Tip:** `757d0336872024ff12821795e85f903d7b7f7608`  
**Base:** `origin/work-v064-green` (`2e9e17c3d`)  
**Pushed:** yes (`origin/empirical/b1-heck-2025-bench-2026-09-22`)  
**Commit:** 1 — `extracts: add Heck 2025 bench/experiment registry from printed apparatus facts`  
**PDF committed:** no

## Problem

`kems-140-heck-2025` already carried printed per-run `log10 fO2` (81 runs in Tables 1/S1) but had **no** `benches:` / `experiments:` registry, so `engine_point` stayed **bench-gapped**.

## Sources actually used (no invented provenance)

| Fact | Printed where | Used as |
|---|---|---|
| Apparatus family “gas-mixing furnace” | Publisher / LSU abstract | `benches[].apparatus_family` |
| Container = Pt or Re wire loop (not Knudsen) | Table S1 `Loop Comp` | `apparatus.cell_material_and_liner` + sample form |
| Loop diameters 0.061 / 0.075 / 0.076 / 0.079 mm | Table S1 `Loop Size, (mm)` | `other_facts` categorical (unpaired to runs) |
| Atmosphere = atmospheric air **or** CO/CO₂/N₂/SO₂ flows | Table 1/S1 footnote `atm=atmospheric air`; flow columns | series `sweep_gas` left `unknown` (matrix stays in obs rows); `total_pressure_Pa` 101325 from 1 atm footnote (SI definition only) |
| Gas flow unit cm³/min | Table S1 footnote | `other_facts` |
| Quench = drop or air | Table S1 `quench type` | `thermal_schedule.cooling_or_quench` |
| T 1480–1773 K; t 5–1398 min | Abstract + Table S1 | series intervals |
| Powder mass 15–50 mg | Table S1 | `sample.mass_kg` interval (mg→kg only) |
| fO₂ channel gas mix / air; log₁₀ fO₂ −9 … −0.68 | Tables 1/S1 + abstract | `fO2_control` |
| Heating element / TC / calibration | **not printed** in abstract or table headers; typeset Methods PDF **not obtained** (ScienceDirect Cloudflare; LSU “not available here”; `batch-z/pdfs/` has no Heck PDF) | `heating_method`, `temperature_measurement`, `temperature_calibration` → `unknown` / `not_published` |

Pattern-matched `benches`/`experiments` shape against `kems-012-sossi-2019.yaml` (open 1-atm gas-mixing furnace + wire loop).

## Change

File: `data/literature/extracts/kems-140-heck-2025.yaml`

- Bench `gas-mixing-furnace-1atm` (`identity.basis: described_in_this_work`)
- Experiment `open-furnace-mvce-degassing-series` (`method: langmuir_free_evaporation`)
- `experiment: open-furnace-mvce-degassing-series` on all **53** observations

One series experiment (not 81) because per-run loop/flow/quench/T already live in Table 1/S1 observation payloads; inventing 81 experiment IDs without paper-named series would over-claim.

## Validation

```text
python tools/validate_literature_extracts.py data/literature/extracts/kems-140-heck-2025.yaml
→ OK: 1 extract file(s) valid
```

`scripts/battery_migrate.py` + full `bench_readiness.py` not finished in-lane (full-corpus migrate too slow for this turn). Extract-level FK/schema validate is clean.

## Gaps left (honest)

1. **Methods PDF still absent** — heating hardware, thermocouple, ramp protocol not stamped.
2. **Series-level `sweep_gas` remains `unknown`** — printed per-run CO/CO₂/N₂/SO₂ (or air with zero flows) stays in S1 rows; SweepGas cannot carry the 81-row matrix without picking one mixture.
3. **Multiple bulk compositions** — `printed_composition` unknown at series level; labels remain on run rows / Table 2.

## Tip / push

| Item | Value |
|---|---|
| Tip | `757d0336872024ff12821795e85f903d7b7f7608` |
| Commits on branch | 1 ahead of `work-v064-green` |
| Push | `origin/empirical/b1-heck-2025-bench-2026-09-22` |

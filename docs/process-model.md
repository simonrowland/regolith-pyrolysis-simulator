# Process Model

The model explores whether solar-thermal regolith refining can reduce the problems normally associated with hard-vacuum pyrolysis and with direct molten regolith electrolysis.

The central control variable is overhead oxygen pressure. Hard-vacuum pyrolysis can drive silica toward SiO vapor, which can foul ducts, windows, condensers, turbines, and product streams. A managed oxygen partial pressure suppresses the `SiO2 -> SiO + 1/2 O2` pathway while still allowing selected volatile and metal extraction.

## Refinery Ladder

The simulated process is organized as staged campaigns:

- `C0` volatile bakeoff and hot-duct gas handling.
- `C0b` mild oxidative cleanup for phosphorus and volatile residues.
- `C2A` low-pO2 or sweep-gas pyrolysis for Fe and SiO-bearing flows.
- `C2B` pO2-managed Fe pyrolysis that preserves more silica-rich glass.
- `C3` Na/K metallothermic shuttle chemistry for residual Fe, Ti, Cr, and Si conditioning.
- `C4` selective Mg pyrolysis.
- `C5` limited MRE for Si and selected metals.
- `C6` Mg thermite reduction for aluminum-rich residues.
- `MRE_BASELINE` full electrolysis comparison path.

## What the Simulator Tracks

Each hourly step updates:

- Melt temperature and composition.
- Vapor pressures and evaporation fluxes.
- Overhead gas pressure and partial pressures.
- Pipe transport saturation and ramp throttling.
- Turbine load, O2 compression, venting, and accumulator inventory.
- Condensed products by species.
- MRE voltage, current, metal production, and electrical energy.
- Total mass balance and cumulative products.

## Why Pretreatment Matters for MRE

MRE remains an important comparison path, but direct electrolysis pushes the full melt inventory through electrical hardware and corrosion-limited cells. Thermal pretreatment can remove or reduce volatile, alkali, sulfur, halide, iron, and gas-handling burdens before MRE. The model therefore treats pyrolysis and MRE as composable steps, not only as competing oxygen-production technologies.


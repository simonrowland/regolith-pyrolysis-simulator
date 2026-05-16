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
- Turbine load, O2 compression, venting, and accumulator inventory, with
  melt/offgas O2 and MRE anode O2 kept as separate mol bins.
- Condensed products by species.
- MRE voltage, current, metal production, and electrical energy.
- Total mass balance and cumulative products.

The batch preserves a raw feedstock inventory outside `MeltState`. Melt
chemistry consumes the supported silicate and compatible trace oxides,
including `NiO` and `CoO`. Raw water, organics/carbon, sulfur,
halides/perchlorates, salts, native metals, sulfides, and refractory trace
extras are routed into explicit inventory buckets.

With `overhead_headspace.enabled: true`, evaporation transitions route
melt-released vapor and its O2 coproduct through `process.overhead_gas` before
`OVERHEAD_BLEED` moves material to terminal accounts. The four O2 terminal bins
remain distinct: Stage 0, MRE anode, stored melt offgas, and vented melt
offgas. With the toggle OFF, the legacy drain-each-tick behavior is preserved
through the same kernel-committed bleed provider.

## Stage 0 Preprocessing Contract

Stage 0 is the preprocessing transform between raw feedstock inventory and
basalt-style melt modelling. It separates volatile, salt, sulfide, drain-tap
metal, and inert terminal slag inventories from the cleaned oxide inventory
used by `MeltState`.

For carbonaceous and icy feedstocks, `anhydrous_silicate_after_degassing` is the
handoff into C1-C6 melt processing. Bulk `H2O`, carbon/organics, sulfur, and
other volatile or trap products are routed to Stage 0 product buckets. Native
metal and alloy material is recorded in `metal_alloy_kg` / `drain_tap_kg` as
separated phase inventory, while `MeltState` receives only the cleaned
anhydrous oxide composition.

For Mars feedstocks, the Stage 0 profile is a CO2-backed carbon cleanup that
extends to 1050 C. Carbon pre-reduction handles sulfate/carbonate cleanup,
perchlorates and halides route to gas/salt products, and the remaining cleaned
oxide inventory feeds the melt model. The inventory records the required carbon
reductant from the feedstock process notes when a `kg C/t` range is present.

Unknown or unsupported cleaned-melt components remain in
`residual_components_kg`.

The melt model receives the cleaned oxide inventory, while Stage 0 products,
drain-tap metal, terminal-slag ceramic components, and unresolved residuals
remain outside `MeltState`.

The Stage 0 sulfate/sulfide bucketing is refined by an optional sulfur-saturation
gate backed by PySulfSat: when the `[sulfur]` extra is installed and the cleaned
melt composition falls inside the SCSS (Smythe 2017) and SCAS (Chowdhury &
Dasgupta 2019) calibration windows, the gate reports SCSS, SCAS, and the
S6+/S2- partitioning fraction (Jugo 2010) so the sulfide- and sulfate-bearing
shares can be apportioned against the model's saturation caps. The gate never
mutates the atom ledger; when PySulfSat is absent or the composition is
out-of-range, Stage 0 falls back to the builtin sulfate/sulfide bucketing with a
warning recorded on the gate result so the diagnostic surfaces in the UI and
telemetry.

## Why Pretreatment Matters for MRE

MRE remains an important comparison path, but direct electrolysis pushes the full melt inventory through electrical hardware and corrosion-limited cells. Thermal pretreatment can remove or reduce volatile, alkali, sulfur, halide, iron, and gas-handling burdens before MRE. The model therefore treats pyrolysis and MRE as composable steps, not only as competing oxygen-production technologies.

MRE anode O2 is recorded as `terminal.oxygen_mre_anode_stored`. It is a
separate electrolysis outlet and does not load the pyrolysis gas-train turbine
or its melt-offgas vent path. Melt/offgas O2 is recorded under
`terminal.oxygen_melt_offgas_stored` and can be moved to
`terminal.oxygen_melt_offgas_vented_to_vacuum` when turbine capacity is
exceeded.

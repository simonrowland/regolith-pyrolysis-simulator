# Feedstocks

The simulator is intended to compare feedstock families, not optimize one idealized lunar composition. Different bodies offer different product streams, pressure environments, volatile hazards, and energy tradeoffs.

## Lunar Feedstocks

Lunar mare and highland materials are the baseline cases. They are useful for studying oxygen, Fe extraction, SiO suppression, glass preservation, Mg recovery, refractory residues, and MRE pretreatment.

Important questions:

- How much Fe and O2 can be extracted before electrolysis?
- When does pO2-managed pyrolysis preserve useful glass rather than boiling off SiO?
- How much Mg or Al-bearing residue remains for later stages?

## Asteroid Feedstocks

Asteroid material broadens the materials ledger.

- M-type material can behave like an Fe-Ni-Co alloy source with silicate byproduct.
- S-type material can resemble silicate refining with different Mg, Fe, and volatile balances.
- C-type material introduces water, organics, sulfur, carbon, and volatile-management complexity.

The model is useful for separating product opportunities from gas-handling problems.

## Mars Feedstocks

Mars feedstocks are not processed in hard vacuum. The simulator uses `surface_pressure_mbar` to give Mars material a CO2 pressure floor in Stage 0. That changes the early bakeoff and hot-duct problem.

Mars cases make these issues visible:

- CO2 backpressure and altitude-dependent pumpdown.
- Sulfate, perchlorate, chlorine, and halide handling.
- SO2 sorbents, HCl/HF scrubbers, and salt traps.
- Higher alkali inventory compared with many lunar cases.
- CO/CO2 chemistry as both constraint and process resource.

## Feedstock Inventory Contract

`composition_wt_pct` may contain more than the melt model consumes. The
simulator projects only these cleaned silicate melt oxides into
`MeltState`: `SiO2`, `TiO2`, `Al2O3`, `FeO`, `Fe2O3`, `MgO`, `CaO`,
`Na2O`, `K2O`, `Cr2O3`, `MnO`, `P2O5`, `NiO`, and `CoO`.

Apollo-derived lunar bulk chemistry usually reports total iron as `FeO` or
`FeO_T`, not as a separate `FeO`/`Fe2O3` split. Keep lunar `FeO` values as
the source-normalized total iron convention unless a source explicitly reports
ferric iron separately.

Other raw components are preserved in the batch inventory instead of being
silently dropped. Examples include `H2O`, `C`, `S`, `SO3`, `Cl`, `ClO4`,
native `Fe`/`Ni`/`Co`, `ZrO2`, and `REE_oxides`. The inventory also has
reserved buckets for Stage 0 gas/volatile products, salts, sulfide matte,
drain-tap metal/alloy material, and inert terminal slag/ceramic components.
Metal/alloy entries are separated phase inventory, not volatilized Stage 0
products. Those buckets are provenance and routing surfaces for process
chemistry; they do not make the melt backend a whole-regolith equilibrium
model.

If a feedstock provides `anhydrous_silicate_after_degassing`, it must also
declare `stage0_profile: carbonaceous_degas_cleanup` and an explicit
`stage0_temp_range_C`. That composition is the cleaned melt input after Stage
0. The bulk volatile-rich composition is kept as raw inventory and product yield
provenance. If no explicit surviving silicate mass is listed, the simulator
uses the CI-family default of 725 kg/t for the handoff.

Mars feedstocks with CO2 atmosphere, sulfate, chloride, perchlorate, or carbon
pre-reduction notes use the Mars carbon-cleanup Stage 0 profile. That profile
runs to 1050 C, records carbon reagent demand from `kg C/t` process notes, and
routes sulfate/halide/perchlorate products outside `MeltState`.

## Adding Feedstocks

Built-in feedstocks live in `data/feedstocks.yaml`. A feedstock should include
a label, source/confidence notes, composition, and any environment fields such
as `surface_pressure_mbar` and `atmosphere`. Use silicate oxide names for
material that enters melt calculation. Use explicit raw component names for
volatiles, salts, native metals, sulfides, and trace extras preserved for
preprocessing chemistry.

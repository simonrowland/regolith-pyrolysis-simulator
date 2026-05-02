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

## Adding Feedstocks

Built-in feedstocks live in `data/feedstocks.yaml`. A feedstock should include a label, source/confidence notes, oxide composition, and any environment fields such as `surface_pressure_mbar` and `atmosphere`.


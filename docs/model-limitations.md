# Model Limitations

This project is a comparative process simulator. It is not a validated engineering design, a process guarantee, or a substitute for thermodynamic and hardware testing.

## Current Approximation Layers

- The fallback equilibrium model is simplified and uses approximate Ellingham and Antoine behavior.
- Oxide activities are approximated when external melt backends are unavailable.
- Heat transfer is simplified: solar concentration is assumed to maintain target temperatures rather than fully modeling radiative, conductive, and convective losses.
- Pipe conductance and turbine behavior are simplified feedback controls, not detailed CFD or turbomachinery design.
- Finite overhead headspace pO2 is available behind `overhead_headspace.enabled` and defaults OFF. When enabled, only melt-released evaporation O2 is held in `process.overhead_gas`, converted to ideal-gas partial pressures, then bled through the existing Poiseuille pipe conductance model. Stage 0 oxygen and MRE anode oxygen still bypass the headspace. Molecular-flow conductance and validated hardware control remain out of scope.
- Condensation routing is a staged engineering approximation.
- MRE behavior is a reduced voltage/current/product model, not a full electrochemical cell simulator.
- The builtin K-shuttle reaction (`2K + FeO -> K2O + Fe`) is temperature-independent. The `C2A_staged` recipe cools below ~1200 C before C3_K to match the Ellingham crossover qualitatively, but the engine does not yet enforce that temperature gate. Temperature-gated shuttle fidelity is future engine work.
- **Metal-phase settling and drain-tap are NOT modelled.** Reduced metal accumulates in `process.metal_phase` indefinitely, and `product_ledger()` reports that account directly as final product. There is no `process.settled_metal_pool` account and no `metal_settling` / `tap_settled_metal` stage, so the gravitational settling of dense metal out of the melt and the drain-tap that terminalizes it are not simulated.
- Feedstock values include literature-derived ranges and estimates.

## Good Uses

- Compare feedstock classes.
- Explore pressure-management effects on SiO boiloff.
- Evaluate pyrolysis as MRE pretreatment.
- Identify product streams and hazard streams.
- Build intuition for process sequencing.

## Bad Uses

- Claim verified product yields.
- Size flight hardware directly.
- Certify corrosion, fouling, or safety behavior.
- Treat fallback thermodynamics as final melt chemistry.
- Compare economics without adding real hardware, operations, and logistics models.

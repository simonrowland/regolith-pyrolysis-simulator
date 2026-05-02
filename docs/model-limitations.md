# Model Limitations

This project is a comparative process simulator. It is not a validated engineering design, a process guarantee, or a substitute for thermodynamic and hardware testing.

## Current Approximation Layers

- The fallback equilibrium model is simplified and uses approximate Ellingham and Antoine behavior.
- Oxide activities are approximated when external melt backends are unavailable.
- Heat transfer is simplified: solar concentration is assumed to maintain target temperatures rather than fully modeling radiative, conductive, and convective losses.
- Pipe conductance and turbine behavior are simplified feedback controls, not detailed CFD or turbomachinery design.
- Condensation routing is a staged engineering approximation.
- MRE behavior is a reduced voltage/current/product model, not a full electrochemical cell simulator.
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


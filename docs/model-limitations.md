# Model Limitations

This project is a comparative process simulator. It is not a validated engineering design, a process guarantee, or a substitute for thermodynamic and hardware testing.

## Current Approximation Layers

- The fallback equilibrium model is simplified and uses approximate Ellingham and Antoine behavior.
- Oxide activities are approximated when external melt backends are unavailable.
- Heat transfer is simplified: solar concentration is assumed to maintain target temperatures rather than fully modeling radiative, conductive, and convective losses.
- Pipe conductance and turbine behavior are simplified feedback controls, not detailed CFD or turbomachinery design.
- **The turbine-control pO2 feedback loop is NOT wired.** The pO2 that drives the SiO suppression (`equilibrium.py::_commanded_pO2_bar`) is the *commanded* setpoint, not a tracked gas inventory: `overhead.composition['O2']` is itself `max(gas O2, setpoint)` written by `overhead.py`. Melt-released O2 is credited to `terminal.oxygen_melt_offgas_stored`, never back to `process.overhead_gas`, and `process.overhead_gas` is drained to `terminal.offgas` every tick. So there is no finite-headspace pO2 that accumulates from melt offgas and self-suppresses SiO. Under uncontrolled atmospheres (`HARD_VACUUM`, `PN2_SWEEP`) the effective pO2 is the numerical vacuum floor (~1e-9 bar) for the whole campaign — it is not a lagged-then-converging feedback value. A proper finite-headspace pO2 model (volume / pressure / temperature / bleed / storage coupling) is tracked as a separate goal, `FINITE-HEADSPACE-PO2-MODEL`.
- Condensation routing is a staged engineering approximation.
- MRE behavior is a reduced voltage/current/product model, not a full electrochemical cell simulator.
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


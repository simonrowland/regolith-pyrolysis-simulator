# Model Limitations

This project is a comparative process simulator. It is not a validated engineering design, a process guarantee, or a substitute for thermodynamic and hardware testing.

## Current Approximation Layers

- The fallback equilibrium model is simplified and uses approximate Ellingham and Antoine behavior. The VapoRock authoritative path is always live in 0.5.0 (`vaporock` is in `[project.dependencies]`); the fallback only fires when the kernel is launched with `chemistry_kernel.allow_fallback_vapor: true` AND the upstream `vaporock` import is unavailable on the host. Silent fallback is forbidden; `vapor_pressure_source_report` in the runner output tells you which authority answered for each species.
- Oxide activities are approximated when external melt backends are unavailable. With the default `[project.dependencies]` install, the live ThermoEngine activity path is used (`MELTS activity convention`, `a_i = exp((μ_i − μ_i0) / RT)`); the legacy pseudo-activity path remains only as a documented fallback.
- Heat transfer is simplified: solar concentration is assumed to maintain target temperatures rather than fully modeling radiative, conductive, and convective losses.
- Pipe conductance and turbine behavior are simplified feedback controls, not detailed CFD or turbomachinery design.
- Evaporation depletion is a one-hour analytic integration model: the HKL driving force and vapor pressures are evaluated once at the start of the tick, then parent-oxide and shared-O2 pools deplete as first-order reservoirs within that tick. This smooths the time integration but is not a new thermodynamic equilibrium solve.
- Finite overhead headspace pO2 is available behind `overhead_headspace.enabled` and defaults OFF. When enabled, only melt-released evaporation O2 is held in `process.overhead_gas`, converted to ideal-gas partial pressures, then bled through the existing Poiseuille pipe conductance model. Stage 0 oxygen and MRE anode oxygen still bypass the headspace. Molecular-flow conductance and validated hardware control remain out of scope.
- **Viscous-regime mass transfer is not modelled.** F3 (Knudsen-regime enforcement) applied `regime_factor = Kn / (Kn + 0.01)` to the band-integration HKL flux, which is the physics-correct attenuation: HKL is the free-molecular limit and goes to zero in the viscous regime. Real refineries collect condenser product in viscous regime via boundary-layer mass transfer; the simulator currently under-predicts viscous-regime stage condensation rather than over-predicting it. A Sherwood-number compensating term `(1 − regime_factor) × mass_transfer_flux` is queued post-0.5.0 (see project tickler). Until then, stage-3 SiO yield numbers in viscous regime should be read as a conservative lower bound, not an operational ceiling.
- Condensation routing is a staged engineering approximation. F1's canonical species → stage registry and F2's per-pipe-segment wall temperatures pin the routing surface (see `stage_purity_report` in the runner output), but cold-spot effects on real hardware geometry require physical validation.
- MRE behavior is a reduced voltage/current/product model, not a full electrochemical cell simulator.
- **The S1b shuttle T-acceptance gate is engine-strict but the shuttle reactions themselves are temperature-independent inside the gate.** The V1c JANAF Ellingham refit puts the FeO crossovers at K/Fe ≈ 832 °C and Na/Fe ≈ 1173 °C; the executable gate refuses any shuttle dispatch with non-positive thermodynamic margin at the dispatch T. Under V1c-JANAF this refuses K→FeO across the practical melt window and refuses Na→FeO above 1173 °C; refusals are recorded in the runner output's `shuttle_refusal_history`. The recipe catalog has been retuned to match (C3 default is C3_NA Na-only; C2A_staged cools to 1150 °C for the Na cleanup). Self-re-flux (S1c, intra-C3 alkali recycle) and Kress91 temperature-gated ferric/ferrous redox remain future engine work.
- **Metal-phase settling and drain-tap are NOT modelled.** Reduced metal accumulates in `process.metal_phase` indefinitely, and `product_ledger()` reports that account directly as final product. There is no `process.settled_metal_pool` account and no `metal_settling` / `tap_settled_metal` stage, so the gravitational settling of dense metal out of the melt and the drain-tap that terminalizes it are not simulated.
- **The evaporation-α surface has tiered coverage.** Tier 1 species (Na, K, Fe, Mg, SiO) carry measured α with citation. Tier 2 species (Ca, Ti) use a Zhang 2014 CaTiO₃ proxy; Al uses a broad conflicting-proxy envelope. Tier 3 species (Cr, Mn, CrO₂) intentionally have no numeric α — the engine returns `missing_alpha` and fails loud rather than silently assuming α = 1. Prototype runs can opt into a fallback with `setpoints.chemistry_kernel.allow_unmeasured_alpha_fallback: true`; outputs then record `unmeasured_alpha_fallback_species`. See [`docs/output-interpretation.md`](output-interpretation.md).
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

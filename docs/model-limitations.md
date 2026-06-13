# Model Limitations

This project is a comparative process simulator. It is not a validated engineering design, a process guarantee, or a substitute for thermodynamic and hardware testing.

## Current Approximation Layers

- The fallback equilibrium model is simplified and uses approximate Ellingham and Antoine behavior. The VapoRock authoritative path is always live in 0.5.0 (`vaporock` is in `[project.dependencies]`); the fallback only fires when the kernel is launched with `chemistry_kernel.allow_fallback_vapor: true` AND the upstream `vaporock` import is unavailable on the host. Silent fallback is forbidden; `vapor_pressure_source_report` in the runner output tells you which authority answered for each species.
- Oxide activities are approximated when external melt backends are unavailable. With the default `[project.dependencies]` install, the live ThermoEngine activity path is used (`MELTS activity convention`, `a_i = exp((μ_i − μ_i0) / RT)`); the legacy pseudo-activity path remains only as a documented fallback.
- Heat transfer is simplified: solar concentration is assumed to maintain target temperatures rather than fully modeling radiative, conductive, and convective losses.
- Pipe conductance and turbine behavior are simplified feedback controls, not detailed CFD or turbomachinery design.
- Evaporation depletion is a one-hour analytic integration model: the HKL driving force and vapor pressures are evaluated once at the start of the tick, then parent-oxide and shared-O2 pools deplete as first-order reservoirs within that tick. This smooths the time integration but is not a new thermodynamic equilibrium solve.
- Finite overhead headspace pO2 is available behind `overhead_headspace.enabled` and defaults OFF. When enabled, only melt-released evaporation O2 is held in `process.overhead_gas`, converted to ideal-gas partial pressures, then bled through the existing Poiseuille pipe conductance model. Stage 0 oxygen and MRE anode oxygen still bypass the headspace. Molecular-flow conductance and validated hardware control remain out of scope.
- **Viscous-regime mass transfer model uses the canonical series-resistance form (0.5.2 Phase B).** F3 (Knudsen-regime enforcement) applies `regime_factor = Kn / (Kn + 0.01)` so that the boundary-layer resistance is weighted in/out by Knudsen regime. 0.5.0 → 0.5.1 added a Sherwood-number boundary-layer companion flux (initially as an additive `J = J_HKL × w + J_MT × (1 − w)` blend). 0.5.2 Phase A1 added per-species Chapman-Enskog `D_AB(T, P)` so the MT term is no longer a fixed `1e-2 m²/s` constant. 0.5.2 Phase B replaced the additive blend with the canonical Bird/Stewart/Lightfoot series-resistance composition: `1/k_total = 1/(α_s × k_HKL) + (1 − f) / k_MT`, where `f = regime_factor` lets the boundary-layer resistance vanish in free-molecular regime (no continuum boundary layer) and dominate in viscous regime (where gas-phase diffusion is rate-limiting). Phase B also lifted the Sherwood number off the laminar pipe asymptote (`Sh = 3.66`) onto an induction-stirring-enhanced form `Sh_eff = 3.66 × √stir_factor` (Frössling-style forced-convection correction), driven by `melt.stir_factor` from the campaign recipe (`setpoints.yaml § induction_stirring`, default `stir_factor = 6` for C2A → `Sh_eff ≈ 9`, operator-capped at the "melt-flying-out-of-the-pot" upper bound near `stir_factor = 10`). Net behaviour: in viscous regime k_MT (boundary-layer) rate-limits the wall flux instead of HKL's free-molecular impingement magnitude, which is the physically honest accounting. Stage-3 SiO yield numbers in viscous regime track Sh enhancement directly; with default C2A stirring more SiO lands at the designated condenser stage.
- **Vapor-pressure fit_target convention** (per-species metadata in `data/vapor_pressures.yaml`). Each `metals` entry declares one of two `fit_target` modes:
  - **`pure_component_psat`** (Fe / Mg / Ca / Al / Ti / Mn / Cr): the Antoine fit reproduces pure-metal saturation pressure `P_sat(T)`. The melt's metal-vapor partial pressure is then `P_metal = a_M(l) × P_sat`, where `a_M(l)` is the liquid metal activity computed from the oxide-decomposition equilibrium constant `K = exp(−ΔG_f / RT)` with the per-species `n_M`, `n_ox`, and the prevailing `pO₂`. Single-counted by construction.
  - **`pseudo_psat_backsolved_from_vaporock`** (Na / K / Cr / Mn): the Antoine fit is a pseudo-`P_sat` whose `A` coefficient is back-solved on a fixed VapoRock calibration grid (`lunar_mare_low_ti`, Kress91 IW fO₂, single-feedstock reference) so that `a_M × P_sat_pseudo ≈ P_metal_VapoRock` at the calibration point. The chain is still single-counted (γ_M lives inside the pseudo-A coefficient), but the fit residual relative to VapoRock grows with feedstock and fO₂ distance from the calibration grid. The fallback is gamma-deficient by construction outside that grid; VapoRock (the authoritative path) remains live by default and `vapor_pressure_source_report` in the runner output tells you which authority answered for each species.
- Condensation routing is a staged engineering approximation. F1's canonical species → stage registry and F2's per-pipe-segment wall temperatures pin the routing surface (see `stage_purity_report` in the runner output), but cold-spot effects on real hardware geometry require physical validation.
- MRE behavior is a reduced voltage/current/product model, not a full electrochemical cell simulator.
- **The S1b shuttle T-acceptance gate is engine-strict but the shuttle reactions themselves are temperature-independent inside the gate.** The V1c JANAF Ellingham refit puts the FeO crossovers at K/Fe ≈ 832 °C and Na/Fe ≈ 1173 °C; the executable gate refuses any shuttle dispatch with non-positive thermodynamic margin at the dispatch T. Under V1c-JANAF this refuses K→FeO across the practical melt window and refuses Na→FeO above 1173 °C; refusals are recorded in the runner output's `shuttle_refusal_history`. The recipe catalog has been retuned to match (C3 default is C3_NA Na-only; C2A_staged cools to 1150 °C for the Na cleanup). Self-re-flux (S1c, intra-C3 alkali recycle) and Kress91 temperature-gated ferric/ferrous redox remain future engine work.
- **Metal-phase settling and drain-tap are NOT modelled.** Reduced metal accumulates in `process.metal_phase` indefinitely, and `product_ledger()` reports that account directly as final product. There is no `process.settled_metal_pool` account and no `metal_settling` / `tap_settled_metal` stage, so the gravitational settling of dense metal out of the melt and the drain-tap that terminalizes it are not simulated.
- **The evaporation-α surface has tiered coverage.** Tier 1 species (Na, K, Fe, Mg, SiO) carry measured α with citation. Tier 2 species (Ca, Ti) use a Zhang 2014 CaTiO₃ proxy; Al uses a broad conflicting-proxy envelope. Tier 3 species (Cr, Mn, CrO₂) intentionally have no numeric α — the engine returns `missing_alpha` and fails loud rather than silently assuming α = 1. Prototype runs can opt into a fallback with `setpoints.chemistry_kernel.allow_unmeasured_alpha_fallback: true`; outputs then record `unmeasured_alpha_fallback_species`. See [`docs/output-interpretation.md`](output-interpretation.md).
- Feedstock values include literature-derived ranges and estimates.

## Stage-0 bakeout: unlimited-reductant assumption and non-rock clearance

Stage 0 is meant to strip non-rock species (volatiles, salts, sulfides, native metals, refractory trace) from the feedstock before the cleaned silicate oxide composition reaches `MeltState` and downstream melt backends. The operator-facing simplification is that unlimited C, CO, and O₂ reductant/oxidant are available during bakeout. **As coded, that assumption does not drive thermodynamic clearance for most species.** The audit in [`docs-private/research/2026-06-13-stage0-unblocked-audit/stage0-bakeout-chemistry.md`](../docs-private/research/2026-06-13-stage0-unblocked-audit/stage0-bakeout-chemistry.md) (per-species table + file:line anchors) is the source for the verdicts below.

### Mechanism

Stage-0 clearance is primarily **name-routing** (clean-by-fiat), not reductant-driven thermodynamics. Raw feedstock components are matched by normalized name strings against constant sets in `simulator/core.py` and dropped whole into terminal buckets (`terminal.offgas`, `terminal.stage0_salt_phase`, `terminal.stage0_sulfide_matte`, `terminal.drain_tap_material`, `terminal.slag`) with no reaction and no reagent debit. `MeltState` receives only the 14 `OXIDE_SPECIES` oxides — a structural filter, not a chemistry outcome.

Reagent-consuming stoichiometry exists in **four gated reaction families only** (kernel `STAGE0_PRETREATMENT` intent):

1. **complete_oxidation** — organics/tar: C/H/O/N atoms → CO₂, H₂O, N₂; O₂ drawn from `reservoir.stage0_oxidant` when O-deficient. Raises on any atom outside CHON.
2. **sulfate_carbon** — `SO3 + C → SO2 + CO` (requires SO₃ already in the salt bucket and an explicit per-feedstock carbon recipe).
3. **boudouard** — `C + CO2 → 2 CO` (requires a declared CO₂ atmosphere/source).
4. **perchlorate** — `ClO4 → Cl + 2 O2`; O₂ banked, Cl credited to the salt-phase residue.

Every other non-rock species is removed by string-matching into a bucket. The unlimited C/CO/O₂ assumption is therefore an assertion in the routing tables, not a consequence of modeled bakeout thermodynamics against stubborn species.

### Defensible as coded

These paths match what a carbothermal/oxidative bake at furnace-survivable Stage-0 temperatures (ramps capped ~950–1050 °C) would reasonably deliver:

- **Organics / hydrocarbons / C / CH₄ / NH₃ / HCN** — `complete_oxidation` with unlimited O₂; atom-gated to CHON (raises on organo-metallic or organo-S/Cl content rather than silently mis-clearing).
- **H₂O and other volatiles** — routed to `terminal.offgas`; dehydration and vapor release are trivially correct.
- **Sulfate as SO₃ surrogate + carbon recipe** — `SO3 + C → SO2 + CO` is real carbothermal sulfate reduction (~600–1050 °C) when the feedstock declares bulk SO₃ and the sulfate carbon reaction.
- **Perchlorate decomposition** — thermal `ClO4 → O₂ + chloride` is real and easy (~300–500 °C); O₂ is banked. (The chloride product is separated to a salt bucket, not gasified — see resistant list.)
- **Native Fe, Ni, Co, FeNi alloy** — physically separate from the oxide melt; name-routed to `terminal.drain_tap_material`. NiO/CoO oxides still enter the melt separately.
- **P₂O₅ in the melt (intended exception)** — `P2O5 ∈ OXIDE_SPECIES` and is not in any Stage-0 removal set; phosphate stays in the cleaned silicate composition for igneous analytic ingestion. Igneous-correct.

### Resistant or mis-routed species

The model asserts clearance that a real carbothermal/oxidative bake at furnace-survivable temperature will **not** deliver, or routes products to the wrong ledger destination. Ranked by whether the error corrupts the melt composition handed to MELTS/MAGEMin:

**P1 — corrupts melt cation inventory**

- **Carbonates** (`carbonate`, `carbonates`, `carbonate_salts` surrogate) — routed whole to `terminal.stage0_salt_phase` by name. Real bake: `MCO₃ → MO + CO₂↑`; CO₂ should offgas but the **Ca/Mg/Na oxide should remain in the melt** (rump-forming cations). The model deletes the entire carbonate mass to a salt bucket, under-feeding the melt with alkaline-earth and alkali oxides. Affects carbonaceous (CI/CM/Ceres/comet) and Mars-carbonate feedstocks.
- **Alkaline-earth sulfates (CaSO₄/MgSO₄) not pre-cracked to SO₃** — only the `SO3` surrogate is carbothermally reduced; a literal `CaSO4`/`MgSO4` name falls through generic `sulfate` → salt phase, removing the Ca/Mg cation with the sulfur. Real carbothermal reduction leaves **CaS** (→ sulfide matte) or **CaO** (→ melt), not a clean offgas. Mars feedstocks declare bulk SO₃ and dodge this, but the surrogate masks the cation-routing error.

**P2 — clearance overstated or unmodeled**

- **Fluorides (CaF₂)** — matched by bare name `f` and routed to salt phase as "cleared." CaF₂ is refractory (b.p. ~2530 °C); C/CO/O₂ at furnace-survivable T will not gasify it. It belongs in the refractory rump or melt, not a removed salt phase.
- **Chlorides (Cl, NaCl, KCl, halide)** — separated to a salt bucket, not gasified. NaCl (b.p. ~1465 °C) and KCl (~1420 °C) **volatilize under mbar vacuum** at Stage-0 temperatures and **re-condense on cold walls** — the same wall-fouling failure mode tracked elsewhere in the simulator. Perchlorate decomposition is real, but the chloride product lands in the salt residue; calling this "cleared" overstates gasification.
- **Nitrates** — zero coverage: no name match, no constant, no reaction, no catalog entry. A declared nitrate would land in `residual_components_kg` (carried, never cleared) or raise. Real decomposition (`MNO₃ → MO + NOₓ↑`) is easy chemistry at 400–900 °C but unmodeled. Low impact for typical regolith feedstocks, but an honest coverage hole.

For file:line anchors and the full per-species verdict table, see the audit linked above.

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

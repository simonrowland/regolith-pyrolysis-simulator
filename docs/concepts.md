# Concepts

This document explains the physical and chemical basis for the simulator's process model. It is the *why* companion to the *how-to* content in [`docs/recipe-playbook.md`](recipe-playbook.md).

## What this simulator is for

The simulator is a workbench for testing whether solar-thermal regolith refining can do most useful work — metals, silica glass, ceramics, and O₂ — using pressure and temperature as control variables, before committing large electrical loads to molten regolith electrolysis (MRE). Current ISRU literature treats regolith primarily as a single-output oxygen source and routes the full melt inventory through electrode hardware. The premise here is different: overhead pressure control plus a staged thermal ramp can pull alkalis, iron, silicon-bearing vapors, and magnesium out of regolith in a chosen sequence, into useful product forms, with the residue conditioned for whatever comes next. The simulator is the means to test whether that sequencing works and to make the full materials ledger — not just O₂ — visible.

## The four product classes

A full-sequence run on a silicate feedstock yields four product classes. Which ones come out and in what form depends on recipe choices; the last is physically unavoidable only on routes that do not deliberately consume the refractory oxides.

1. **Metals + O₂** — the main extraction targets. Na and K condense in Stage 4 (alkali cyclone), Fe condenses in Stage 1 (Fe condenser), Mg in Stage 4, Al via Mg thermite in C6. Under the V1c JANAF refit, Na can clean residual FeO only in the cool ~1150 °C window; K is a volatile product/recyclable alkali stock, not a practical FeO reductant in the melt window. Disproportionation and reduction reactions release O₂, accumulated at the terminal as the by-product of extracting metal from oxide. This is the sequence the default recipe (C0 → C2A → C3 → C4 → C5 → C6) is designed to deliver.

2. **Pure silica glass** — Stage 3 fused-silica baffles capture SiO on-demand. The trigger is switching overhead gas cover from pO₂ hold to pN₂ sweep (Path A, C2A_continuous). Under pO₂ control SiO is suppressed >300× at 1 mbar; under pN₂ sweep it evolves freely and condenses as high-purity fused silica on the Stage 3 removable cartridge. The silica comes out when the operator chooses to allow it, not whenever the melt is hot.

3. **Industrial mixed glass** — an early-tap option. Stop the sequence after alkali and Fe extraction, before the SiO release window; tap the remaining melt. The result is a Ca–Mg–Al–Si silicate glass — less selective than the full sequence, but demonstrating recipe flexibility.

4. **Refractory ceramic (the rump)** — conditional on recipe choice; see `CLAUDE.md` §5. In Branch Two default extraction, Ca, REEs, un-thermited Al₂O₃, TiO₂, and other oxides with very large negative Ellingham values do not vaporise at any temperature the furnace itself can sustain. In that route, the rump is the oxide-stability floor and becomes the natural feedstock for hot-duct liners and refractory furnace components. In Branch One C5/MRE routes, the operator can additionally consume Ca/Al/Mg/Si by electrolysis or thermite, so the remaining rump is a recipe outcome that must be checked against the ledger rather than assumed.

## Two senses of "Ellingham" in this project

The word "Ellingham" appears throughout this codebase and means two related but operationally distinct things. Both come from the same underlying plot (the Ellingham–Richardson diagram of ΔG_f° per mol O₂ vs T), but they answer different questions about a recipe.

**Sense 1 — Oxygen-affinity ladder (reduction ordering).** At a given T, the species whose oxide is more stable per mol O₂ has higher oxygen affinity. Read at fixed T, the ladder tells you which metal can chemically reduce which oxide. This is the operative concept for the **alkali shuttle** and **Mg thermite**:
- Na₂O more stable than FeO at 1150 °C → Na can take O from FeO (`2 Na + FeO → Na₂O + Fe`).
- MgO more stable than Al₂O₃ per mol O₂ below the ~1573 °C V1c crossover → Mg can take O from Al₂O₃ (C6 thermite). Above that crossover, C6 needs a kinetic/local-heating basis, not standard-state equilibrium alone.
- Ranking at moderate T: Ca > Mg > Al > Ti > Mn > Cr > Fe > Na > K (per V1c JANAF refit). The Fe/Na/K corner is unstable across T — the K/Fe crossover sits at 832 °C, Na/Fe at 1173 °C, so above those Ts the ladder inverts and the shuttle no longer drives the reduction.

This sense is sometimes called the "naive Ellingham" because it ignores pressure. It is correct as far as it goes — for picking a reductant at a given T.

**Sense 2 — Pressure-modified Ellingham (evolution under vacuum).** The same diagram modified for non-standard pO₂. Each oxide's dissociation threshold drops as pO₂ falls, but with a species-specific slope set by the reaction stoichiometry:

```
d log(a_M) / d log(pO₂) = −1 / n_M
```

where `n_M` is the moles of metal per mole O₂ in the formation reaction. The slopes:

| Species | Formation (per mol O₂) | n_M | pO₂ slope |
|---|---|---:|---:|
| Na, K | 4 M + O₂ → 2 M₂O | 4 | −0.25 |
| Fe, Mg, Ca | 2 M + O₂ → 2 MO | 2 | −0.50 |
| Cr, Al | (4/3) M + O₂ → (2/3) M₂O₃ | 4/3 | −0.75 |
| Ti | M + O₂ → MO₂ | 1 | −1.00 |

A drop of 11 decades in pO₂ (1 atm → ntorr) scales metal activity by ~560× for Na/K, ~3×10⁵× for Fe/Mg/Ca, ~5.6×10⁸× for Cr/Al, and ~10¹¹× for Ti. **Vacuum helps the more-oxidized species most.** This is what unlocks the "full ladder is accessible at solar-furnace temperatures" claim in `CLAUDE.md` §4 — without low pO₂, none of these activities would be high enough to drive evaporation at temperatures the crucible can survive.

**Sense 2 does not, by itself, predict evolution order.** Evolution is `P_eff = a_M × P_sat`, and `P_sat` (pure-metal vapor pressure) varies by ~13 orders of magnitude across the metal set at recipe T (Na/K: ~10⁶ Pa at 1500 K; Fe: ~10⁻¹; Ti: ~10⁻⁹). So Na/K evolve first despite their *shallowest* pO₂ slopes — because they are *volatile elements* whose pure-metal P_sat at moderate T is enormous, swamping the activity differences. Fe needs ~1700–1900 °C before its `P_sat × a_M` becomes meaningful; Ca/Al/Ti are below the volatility floor at any furnace-survivable T.

### Which sense applies where

| Operational question | Sense | Inputs |
|---|---|---|
| Will Na reduce FeO at this T? | 1 — affinity ladder | ΔG(T) of the two oxides at fixed pO₂ |
| Will Fe (already reduced) evaporate from the melt at this T and pO₂? | 2 — pressure-modified | a_M(T, pO₂) × P_sat(T) |
| At what T does the shuttle become physically defended? | 1 — ladder crossover | T where ΔG_red = ΔG_target |
| In what order do species come out as T rises? | 2 + Antoine | dominant: P_sat ordering |
| Can low pO₂ alone unlock Ti or Ca? | 2 — slope analysis + P_sat floor | requires *both* favourable slope *and* P_sat at recipe T |

When this documentation says "Ellingham diagram" generically, it usually means the underlying plot; the two senses above describe how the plot is read.

## The three levers

The extraction sequence is driven by three control axes acting on the Ellingham diagram (ΔG of oxidation vs T). The axes are pO₂, pN₂, and temperature.

### pO₂

pO₂ is inside the SiO₂ ⇌ SiO + ½ O₂ equilibrium directly. The suppression law is:

```
p(SiO) = K(T) × a(SiO₂) / √pO₂
```

At hard vacuum (pO₂ ~ 1×10⁻⁹ bar) SiO vapor pressure at 1600 °C reaches 0.5–2 mbar, which is a significant fouling flux. At 1 mbar pO₂, SiO is suppressed ~300× conservatively (1000× theoretical) to <0.005 mbar — effectively zero transport toward the condenser stages.

The species-specific pO₂ slopes derived from Sense 2 above govern *how strongly vacuum amplifies each oxide's activity*:

| Species | Sense-2 slope `d log(a_M) / d log(pO₂)` | Interpretation |
|---|---:|---|
| Na, K | −0.25 | Weakly amplified by vacuum; volatile at any pO₂ above hard vacuum because pure-metal `P_sat` is enormous |
| Fe, Mg, Ca | −0.5 | Standard stoichiometric amplification |
| Cr, Al | −0.75 | Strongly amplified; combined with low `P_sat` these are still not practically pyrolysable but vacuum helps a lot |
| Ti | −1.0 | Maximum vacuum amplification, but `P_sat × a_M` still below threshold at any furnace-survivable T |

These are the Sense-2 ladder slopes (theoretical, from stoichiometry). The builtin authoritative vapor-pressure provider uses `pO2_exponent` per-species in `data/vapor_pressures.yaml` (currently set only for the SiO₂ ⇌ SiO + ½ O₂ branch, where pO₂ is *the* lever) and computes metal activities from the Ellingham table. VapoRock may report a diagnostic shadow gas-speciation surface, but it does not provide the authoritative `a_M(T, pO₂)` surface consumed by evaporation.

pO₂ is controlled actively via Fe-granule oxygen sorbent and precision O₂ micro-bleed, with turbine-speed feedback. Precision in the viscous regime is ±0.1–0.3 mbar (`data/setpoints.yaml` §5).

### pN₂ (sweep gas)

pN₂ is the overhead sweep gas. The canonical symbol is N₂ but any inert works — Ar, or CO₂ as a natural choice on Mars feedstocks. Its primary role is transport control: it keeps the Knudsen number well below 0.01 so evolved vapor is swept toward designated condensers rather than crossing the pipe ballistically and landing on whatever cold surface it encounters first.

The target band is **5–15 mbar**, typically 8–12 mbar, which keeps the mean-free-path short relative to the pipe diameter (12 cm typical for a hot-wall Stage 0 duct, per `data/setpoints.yaml` §6). Below this viscous-flow band, molecules travel ballistically and cold-wall fouling becomes effectively uncontrolled regardless of gas chemistry. Above it, the sweep gas begins to dilute vapor concentrations and reduces the thermodynamic driving force at the condenser.

The pN₂ setpoint is calibrated against the mean-free-path equation (`Kn = λ / L ≪ 0.01`), not chosen by feel.

### Temperature

Temperature determines which species are above their vapor-pressure threshold at a given moment. The fundamental sequence is driven by vapor pressures:

- **Na, K**: volatile by 1250–1350 °C (vapor pressures >> any pO₂ setpoint above hard vacuum)
- **Fe**: 0.01–0.1 mbar at 1400–1600 °C — adequate for selective harvest
- **SiO**: 0.5–2 mbar at 1600 °C under vacuum; the problem species for fouling
- **Mg**: ~0.5 bar at 1600 °C — highly volatile once conditions allow
- **Al**: boiling point 2519 °C; negligible vapor pressure at any furnace-survivable temperature; MRE only
- **Ca**: significant above ~1500 °C but poor selectivity vs other species present at that temperature; extraction marginal

The practical implication is that temperature alone sequences Na/K → Fe → SiO (overlapping) → Mg cleanly in time, but Ca, Al, and Ti are below their practical vapor threshold at any temperature the crucible survives. The rump is the physical residue of that floor only for routes that did not later consume those oxides by C5/MRE or C6 thermite.

## The alkali shuttle

Na and K sit low on the Ellingham diagram at low temperature, but the V1c JANAF refit moves the practical melt-window story. Na₂O is still more stable than FeO at 1150 °C, so elemental Na can strip oxygen from residual FeO in a narrow cool cleanup window. K₂O crosses FeO near 832 °C, below practical melt operation; K is therefore refused as a FeO reductant in the staged recipe.

```
2Na + FeO → Na₂O + Fe
```

This is the surviving alkali shuttle: alkalis evolved during C2 (or dosed externally) are condensed in Stage 4, then Na can be looped back into the melt as the cool FeO reductant. The alkali is consumed as a reducing agent, the target oxide is stripped to metal, and the alkali oxide dissolves into the melt — where, at bakeout temperature, it re-evaporates and is recovered again. The same alkali inventory recycles across multiple cycles before final product recovery.

The shuttle serves two distinct roles:

1. **Oxygen reductant**: Na chemically frees Fe from FeO only below the Na/Fe crossover. Cr₂O₃, TiO₂, and MnO are refused in the current C3 temperature window by the executable thermodynamic gate.

2. **Selectivity tool**: Fe and SiO vapor-pressure windows overlap in the ~1500–1700 °C band, and pO₂ alone cannot separate them (both require low pO₂ or vacuum, so the SiO₂ ⇌ SiO + ½O₂ lever is the same for both). The shuttle reduces FeO chemically at a temperature where SiO activity is lower, allowing Fe to be extracted at a different point in the sequence and keeping condenser stages clean.

Honest limits: the `C2A_staged` recipe cools to 1150 °C for Na FeO cleanup because the V1c JANAF Na/Fe crossover is 1173.4 °C, leaving only a thin positive margin. K/FeO is refused above its 832 °C crossover, and Cr/Ti targets are refused at C3 temperatures. See `docs/model-limitations.md` for the explicit caveat. The shuttle also cannot chemically free Ca, Mg (in the main melt), Al, or Ti — those oxides are more stable than the available alkali-oxide path in the process window.

## Hot walls and viscous flow as design invariants

Two engineering requirements must hold for the recipe to work at all, regardless of how the three levers are set.

**Hot walls upstream of the designated condenser.** Stage 0 duct and upstream piping are maintained above ~1400 °C (doloma-REE ceramic, max service temperature 1750 °C; `data/setpoints.yaml` §7). A cold spot upstream of the designated condenser means the vapor condenses on the pipe wall rather than reaching Stage 1 (Fe), Stage 3 (SiO), or Stage 4 (alkali/Mg). Wall deposits of SiO on ceramic piping are particularly invasive: SiO disproportionates to Si + SiO₂ on cold surfaces, and the silica reacts with refractory oxides at high temperature. Na and K deposits on cold transfer ducts are the second-worst class.

**Buffer gas in the viscous-flow regime.** The 5–15 mbar pN₂ band is not a target — it is a physical requirement for directional vapor transport. Below this band, the mean-free-path exceeds the pipe diameter and molecules travel ballistically to whatever cold surface they encounter. The mbar setpoint is calibrated from `Kn = λ / L ≪ 0.01`, where λ is the gas mean-free-path and L is pipe diameter.

Neither invariant is a recipe knob. Both are preconditions that must hold for the extraction sequence to reach the designated condenser stages rather than coating the pipe.

## Wall deposits as instrumentation

The simulator tracks `wall_deposit_kg` per species as a mol-native ledger account. This is the fraction of evolved vapor that lands on walls instead of reaching the designated condenser, parameterized by gas-phase conditions (pN₂ / Knudsen regime) and wall temperature. The per-species breakdown exposes which species is the fouling problem at a given operating point.

The operational target is not zero deposited mass but long-term operation without re-sintering. A run that completes once and then requires furnace rebuild before the next batch is not a working recipe. The `wall_deposit_kg` account exists to make the fouling story legible without running physical hardware.

## The two failure modes

Any recipe that does not avoid both failure modes simultaneously is not a working recipe.

**Incomplete extraction** — the run ends with most of the target species still in the melt. Temperature did not reach threshold, or the sequence did not run long enough. Outcome: no selective products, a melt full of mixed cations, the refractory rump never reached because the non-refractory species were never removed.

**Furnace coating** — Na, SiO, Fe, or Mg vapor condenses on cold pipe walls or pressure-vessel internals instead of reaching the designated condenser stage. Outcome: the furnace is coated, and the deposit reacts with the refractory at temperature. In practice the furnace must be re-sintered before the next run, which destroys operational continuity.

A ~100 % extraction target is an *and* condition with a near-zero wall-deposit target. Neither alone suffices.

## Stage 0 cleanup as simplifier

Stage 0 (C0 and optional C0b) separates and reports material that should not be handed directly to the silicate melt model before the main extraction sequence begins. For lunar feedstocks this means CHNOPS volatiles, nanophase Fe⁰ (magnetically separated), native metals, sulfides, and salts. For Mars feedstocks it also covers perchlorates, halides, sulfates, and carbon-bearing cleanup under a CO₂ pressure floor to 1050 °C. For carbonaceous asteroid bodies (CI, CM, Ceres, comets), the Stage 0 degassing cascade tracks water, organics, sulfides, carbonates, and residual carbon, leaving a dehydrated anhydrous silicate at roughly 650–800 kg per original tonne.

The result is a cleaned silicate input plus an explicit residual-foulant ledger. Downstream chemistry receives the silicate-melt handoff; Stage 0 keeps contaminant disposition, warning flags, and unresolved residuals visible as diagnostics. The feedstock is simplified for C2A, not asserted to be fully clean.

See [`docs/process-model.md`](process-model.md) for the Stage 0 contract in detail, and [`docs/feedstocks.md`](feedstocks.md) for the feedstock inventory categories.

## Pyrolysis as MRE pretreatment

Pyrolysis and MRE are not competing technologies in this model — they are composable steps. MRE is electrically intensive and pushes the full melt through corrosive electrodes. Removing alkalis, Fe, volatiles, and halides before the electrolysis cell reduces both the electrochemical load (fewer species to reduce) and corrosion exposure (alkali and sulfur are particularly hostile to electrode materials). The C5 campaign (limited MRE under O₂ backpressure, max 1.6 V Branch Two) operates on a melt that has already had its most reactive components removed — electrodes last 5–10× longer, and the electrical energy budget is 600–1200 kWh/t versus 2650–4050 kWh/t for full-scope MRE without pretreatment. See `data/setpoints.yaml` §8 for the MRE voltage sequence.

The `MRE_BASELINE` track in the simulator models full electrolysis without pyrolysis pretreatment, as a comparison point.

## What this simulator does not model

The simulator is a comparative process estimator, not a validated engineering design. Key limitations:

- **Heat transfer is simplified.** Solar concentration is assumed to maintain target temperatures; radiative, conductive, and convective losses are not fully modelled.
- **Kress91 Fe³⁺/Fe²⁺ redox** (fO₂-coupled ferric/ferrous glass model) is not yet implemented. The intrinsic fO₂ is derived from cleaned melt composition as a diagnostic surface only.
- **Metal-phase settling and drain-tap are not modelled.** Reduced metal accumulates in `process.metal_phase` indefinitely; gravitational settling of dense metal out of the melt is not simulated.
- **Finite overhead headspace** (toggle `overhead_headspace.enabled`) defaults OFF. When ON, evaporation O₂ is held in `process.overhead_gas` and bled through a Poiseuille conductance model; molecular-flow conductance and validated hardware control are out of scope.
- **The S1b shuttle T-acceptance gate is strict but the shuttle reactions themselves are temperature-independent inside the gate.** The engine refuses dispatch when the dispatch-T thermodynamic margin is non-positive (and records the refusal verbatim in `shuttle_refusal_history`), but it does not interpolate yields across the crossover band. See `docs/model-limitations.md`.

For the full list, see [`docs/model-limitations.md`](model-limitations.md).

The truth-seeking discipline for this project: when the model disagrees with reference data, the correct response is investigation, not parameter-tuning to force agreement. See `CLAUDE.md` §1 for the project mandate.

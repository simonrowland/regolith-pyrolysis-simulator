# Model Limitations

This project is a comparative process simulator. It is not a validated engineering design, a process guarantee, or a substitute for thermodynamic and hardware testing.

This page states the current limitations as facts, with the physical or modeling reason for each. It is the companion to [`docs/chemistry-methods.md`](chemistry-methods.md), which describes how the chemistry is computed, and to [`docs/chemistry-provenance.yaml`](chemistry-provenance.yaml), which carries the machine-readable provenance for every grounded coefficient referenced below.

## Current Approximation Layers

- The authoritative vapor-pressure model is the builtin Antoine + Ellingham provider. It is simplified, and its per-species fit/source labels are reported through `vapor_pressure_source_report`. VapoRock is installed as a diagnostic-only shadow when available; its full gas speciation is reported under `vaporock_full_speciation_Pa` and does not decide the authoritative `VAPOR_PRESSURE` surface. Silent fallback to backend vapor pressures remains forbidden unless `chemistry_kernel.allow_fallback_vapor: true` is set.
- The oxide activities the vapor-pressure path consumes come from the builtin analytic treatment, not from an external phase-equilibrium engine: non-iron oxides use **constant table gamma** (`a = gamma* * X` for mid-range composition) with a thin pure-endmember continuity shell that enforces Raoultian `a -> 1` only for `X > 0.99`, and iron uses the Kress/CALPHAD FeO activity described under *Iron redox in the melt* below. The one-parameter pseudo-binary regular solution (`ln gamma = ln gamma* (T*/T)(1-X)^2`) is **held** after enghar median regression attribution (chemact-root 2026-08-05). This analytical provider is the **`internal-analytical`** model in trust-architecture vocabulary (legacy backend name `stub`, still accepted on input and canonicalized to the `internal-analytical` serialization token); it is denylisted from certification gates and never holds ledger authority for a certified claim. When ThermoEngine/MELTS is installed it supplies a Gibbs-minimized silicate-equilibrium activity (`MELTS activity convention`, `a_i = exp((mu_i - mu_i0) / RT)`) as a diagnostic and phase-context shadow; it is not promoted into the authoritative vapor-pressure activity slot. Multi-component excess G remains the real activity gap. The current alkali anchors are grounded to Sossi et al. 2019 for Na and to DeMaria et al. 1971 as carried by Sossi & Fegley 2018 for K; other rows use Sossi & Fegley 2018.
- **Alkali temperature dependence is not invented.** The live Na/K anchors are on the single-cation basis (`NaO0.5`, `KO0.5`) at 1673 K and 1500 K. Runtime applies those table values as constant mid-range gamma (no analytical `T*/T` scaling). Outside the point-anchor temperature domain the numeric result still drives flux but is status-bearing `out_of_gamma_domain`, never a clean certified point.
- **The Na vapor rail is an uncertified coherent L&H standard-state pair.** The active high-T path is the liquid-NaO0.5 `standard_reaction_term` assembled from Lamoreaux & Hildenbrand 1984 Tables 2/4 (DOI 10.1063/1.555706; dual-extracted, minimax Antoine residual 1.18e-4 dex on 1405–1600 K) plus the constant-table `gamma_NaO0.5=1e-3` anchor (Sossi 2019 magnitude, coherent by provenance in the L&H frame). The prior Chase/JANAF gas-standard fugacity + gamma compensating-errors surface (Alternative B) is retired; the full-VapoRock/TE-μ0 candidate is a `status_bearing_non_authoritative` shadow bracket only (the TE-μ0 class is retired, as for the demoted Ca/Mg rows). A review of the earlier comparison showed the old "3.5× over DeMaria" line was **misbound**, and it must not be reused. Machine-readable `authority_class: uncertified` + `coherent_pair` + `shadow_bracket` emit on the Na provenance/diagnostic. **DeMaria 12022 and 12065 are held-out validation only** with pre-registered digitization σ; do not retune gamma or the standard term to close those rows. K remains independent on the same L&H literature basis.

- **The runtime K path uses the liquid KO0.5 convention but is not DeMaria-closed.** The consumed vapor path uses the `gamma*_KO0.5 = 3.5e-5` point anchor from the DeMaria et al. 1971 Apollo 12022 KEMS inversion carried by Sossi & Fegley 2018 at 1500 K as a **constant table gamma**, and a liquid KO0.5 standard-reaction term assembled from Lamoreaux & Hildenbrand 1984 K2O(l), K(g), and O2(g) tables. This remains UNCERTIFIED because K2O thermodynamic data are limited, the comparison is sub-liquidus lunar basalt, and a multi-component high-temperature activity surface is not fitted.
- **Both DeMaria anchors are sub-liquidus for Apollo 12022.** The estimated liquidus is about 1573 K, so the fully molten model is being compared against partial-melt data at the 1429 K anchor. Sub-liquidus offgassing undercount and melt-fraction gating are tracked as open work.
- **The pure-endmember discontinuity is removed via a local shell, but multicomponent fidelity remains limited.** A thin continuity shell for `X > 0.99` gives `gamma -> 1` and `a -> 1` continuously as `X -> 1`, eliminating the former branch from `gamma*X` to 1 (31.1x for the Cr2O3 anchor) without mid-range regular-solution curvature. Cross-interaction parameters and assemblage changes remain unmodeled.
- Heat transfer is simplified: solar concentration is assumed to maintain target temperatures rather than fully modeling radiative, conductive, and convective losses.
- **Sweep-gas sensible heating is unmodeled for every carrier.** The energy ledger includes no carrier heat-capacity term, so it cannot distinguish monatomic Kr, Ar, or He (`C_p = 5/2 R` for an ideal gas) from diatomic N₂ (`C_p = 7/2 R`). Carrier choice therefore changes the modeled transport and separation behavior, but not furnace or recirculation heating duty.
- Pipe conductance is a simplified bleed input, not detailed CFD. Cold-train hardware ratings reduce to the authoritative capacity `C`; the bleed provider partitions O₂ among admitted/stored, accumulated, relieved, and held legs, while turbine fields are read-only projections rather than an independent cap. Turbomachinery remains a reduced model rather than a validated design.
- Evaporation depletion is a one-hour analytic integration model: the HKL driving force and vapor pressures are evaluated once at the start of the tick, then parent-oxide and shared-O2 pools deplete as first-order reservoirs within that tick. This smooths the time integration but is not a new thermodynamic equilibrium solve.
- Finite overhead headspace pO2 is controlled by `overhead_headspace.enabled` and defaults ON. When enabled, `process.overhead_gas` holds melt-released evaporation O2 plus O2 credited by native-Fe saturation and reducing Fe redox re-speciation, converts that inventory to ideal-gas partial pressures, and hands it to the authoritative cold-train capacity/accumulator/relief partition. Poiseuille conductance is an input to the bleed calculation, not the disposition authority. Stage 0 oxygen and MRE anode oxygen still bypass the headspace. Pipe conductance is Poiseuille-only; transitional and molecular conductance is an upcoming feature. Above `VISCOUS_KNUDSEN_MAX`, evolved `P_bulk` still comes from the viscous Poiseuille carrier, so the evaporation flux is computed as a finite continuation of the continuum fit and flagged `extrapolated` (a transport-model validity flag, not a Kn safety or coating gate). The optimizer's ~1 mbar floor (Kn≈0.004) stays inside the viscous domain.
- **Source evaporation uses a series-resistance form with HKL upper-bound authority.** The flux denominator is `r_interface + r_gas + r_melt` with `r_interface = 1/(α·k_HKL)`, `r_gas = 1/k_g` for continuum `Kn < 0.01` (`k_g = M·Sh·D_AB/(L·R·T)`; in transitional Kn the continuum expression is extended and flagged `extrapolated`, and in free-molecular flow `r_gas = 0`), and `r_melt = 0` until species/state melt-transfer inputs exist. The former universal `1e-4·√axial_stir` melt conductance is refused (`uncertified_melt_resistance_model`); axial stir does **not** drive source-side `r_melt` under the current policy. Emitted diagnostics and Pareto replay artifacts carry `authority_class: "upper-bound"` and `authority_reason: "missing-species-state-dependent-melt-transfer-inputs"`. Fuchs–Sutugin is not used on the source-flux path. On the condensation/wall side the Sherwood number may still use radial-stir enhancement (`Sh_eff = 3.66 × √radial`); the default radial component is `1.0` (laminar). This is a reduced series-resistance model, not resolved boundary-layer CFD.
- **The selected inert carrier is a static overhead pressure, not a flow rate.** Its pressure enters the transport model (setting the Knudsen number and gas-side resistance) and the pressure-and-conductance bleed of headspace oxygen; there is no commanded flow rate, volumetric flow, or residence-time input. Two consequences follow. First, the advective removal of evolved vapor and co-evolved oxygen that a faster flowing carrier would provide is not resolved separately from static-pressure diffusion resistance — the model can represent that raising carrier pressure impedes diffusion, but not that increasing flow would carry vapor away and lower surface back-pressure. Second, co-evolved-oxygen self-poisoning (an evolving species releases O₂, which raises effective pO₂ and suppresses further evolution) acts with a one-step lag through the headspace ledger — oxygen credited this tick suppresses the *next* tick's solve — rather than as an instantaneous local-surface balance in which oxygen from the current dissociation solve poisons that same solve. A small related impurity: inert overhead pressure leaks weakly into the iron equilibrium through the Kress91 FeO-activity path (about a 10⁻⁷ relative dependence at 5–15 mbar, iron only; SiO and the alkalis are clean). Neutral pressure should touch transport but no equilibrium; this is a known low-severity decoupling to fix.
- **Wall re-evaporation uses a per-species reactivity class plus a cross-species wall-chemistry model.** Physisorbing species use the reversible pure-species `P_sat(T_wall)` backstop, so a sufficiently hot wall rebounds the deposit. SiO is treated as reactive: wall capture disproportionates it to physical products (`SiO -> 0.5 SiO2 + 0.5 Si`) and uses an effective product `P_sat ~= 0`. The wall chemistry also routes Mg against wall SiO2 (`2 Mg + SiO2 -> 2 MgO + Si`) and Fe against free wall Si (`Fe + Si -> FeSi`). Na and K credit elemental ledger species only, but carry a diagnostic activity-depression / binding state with a grounded disilicate saturation anchor (`0.5 mol Na2O/K2O per mol SiO2`, Kracek-family source surface). Ca, Mn, Cr, Al, Ti, and CrO2 remain reversible physisorbers. Residual gaps remain: FeSi2 and fO₂-dependent silicide/fayalite partition, the Na/K activity rate-law beyond the saturation cap, Mg passivation, Mn/Cr fouled-wall reactivity, a non-interpolated Na/K saturation temperature band fixed at the cold-wall 0.5 anchor, and the run-to-run fouling lifecycle beyond the transient per-wall state hook.
- **Vapor-pressure fit_target convention** (per-species metadata in `data/vapor_pressures.yaml`). Each `metals` entry declares one of three `fit_target` modes:
  - **`pure_component_psat`** (Ca / Al / Ti / Cr / Mn): the Antoine fit reproduces pure-metal saturation pressure `P_sat(T)`. The melt's metal-vapor partial pressure is then `P_metal = a_M(l) × P_sat`, where `a_M(l)` is the liquid metal activity computed from the oxide-decomposition equilibrium constant `K = exp(−ΔG_f / RT)` with the per-species `n_M`, `n_ox`, and the prevailing `pO₂`. Single-counted by construction.
  - **`standard_reaction_term`** (K / Na): the Antoine-form row is a fitted representation of an explicit standard-reaction term — liquid `KO0.5(l) = K(g) + 0.25 O2(g)` and liquid `NaO0.5(l) = Na(g) + 0.25 O2(g)` from Lamoreaux & Hildenbrand 1984 Tables 2/4. The consumer applies `a_MO0.5` and `pO2^-0.25` explicitly; wall condensation does not reuse this melt-source term as pure-species `P_sat`.
  - **`pseudo_psat_backsolved_from_vaporock`** (Mg / Fe / SiO; Na historical only): the Antoine fit is a pseudo-`P_sat` whose `A` coefficient is back-solved on a fixed VapoRock calibration grid (`lunar_mare_low_ti`, Kress91 IW fO₂, single-feedstock reference) so that `a_M × P_sat_pseudo ≈ P_metal_VapoRock` at the calibration point. The chain is still single-counted (γ_M lives inside the pseudo-A coefficient), but the fit residual relative to VapoRock grows with feedstock and fO₂ distance from the calibration grid. Na's pseudo row is inactive provenance only (the active rail is the L&H `standard_reaction_term`). The builtin provider remains authoritative; VapoRock can shadow the run diagnostically but has no ledger or `vapor_pressure_source_report` authority.
- Condensation routing is a staged engineering approximation. A canonical species-to-stage registry, combined with a per-pipe-segment wall-temperature model, pins the routing surface (see `stage_purity_report` in the runner output), but cold-spot effects on real hardware geometry require physical validation. Stage purity is reported as a diagnostic; a full condenser-train model is an upcoming feature.
- MRE behavior is a reduced voltage/current/product model, not a full electrochemical cell simulator.
- **The metallothermic-shuttle temperature-acceptance gate is engine-strict, but the shuttle reactions themselves are temperature-independent inside the gate.** The JANAF-4th multiphase Ellingham re-ground (2026-07-09) that grounds the shuttle puts the FeO crossovers at K/Fe ≈ 836 °C and Na/Fe ≈ 1181.5 °C; the executable gate refuses any shuttle dispatch with non-positive thermodynamic margin at the dispatch temperature. Under that re-ground the gate refuses K→FeO across the practical melt window and refuses Na→FeO above 1181.5 °C; refusals are recorded in the runner output's `shuttle_refusal_history`. The recipe catalog has been tuned to match — the default metallothermic-polish recipe is sodium-only, and the paired melt-cleanup recipe cools to 1150 °C for the sodium cleanup. Shuttle self-reflux (intra-stage recycling of freshly reduced alkali back into the same melt) remains future engine work. Kress–Carmichael 1991 ferric/ferrous redox is live for the Fe `a_FeO` used by the shuttle, but the intrinsic fO₂ source and the pure-FeO IW switch anchor remain limitations (see *Iron redox in the melt* below).
- **Metal-phase stratification is diagnostic-only; general drain-tap behavior is not modelled.** Builtin-authored reduced metal is disposed at hour boundaries into mol-native `process.metal_phase_bottom_pool` and `process.metal_phase_float_layer` accounts, with a first-order contact diagnostic and density-contrast verdict. C6 is the narrow exception: each tick's net thermite Al is atom-conservingly routed from `process.metal_phase` to the existing `terminal.drain_tap_material` product account after back-reduction. If a backend-authored transition touches `process.metal_phase`, that account remains authoritative and the builtin stratifier emits read-only bottom/float projections without committing them. Builtin pool accounts are temporarily restored to legacy `process.metal_phase` staging before chemistry ticks and campaign initialization, so no evaporation, redox, or gas-escape behavior reads the diagnostic split. `product_ledger()` aggregates all three metal-phase accounts plus the terminal tap. Correlations used outside their cited temperature ranges are labeled as extrapolations. A general behavior-changing settling or tap gate remains future work.
- **MRE decomposition-voltage ladder:** NiO (0.3864 V at 1873.15 K) plus FeO, Cr2O3, MnO, SiO2, TiO2, and Al2O3 are raw-thermo reanchored (`E = -DeltaGf(T)/(nF)`; NIST-JANAF/Chase 1998 and companion evaluations, with phase-correct NiO from Mah & Pankratz 1976 USBM Bulletin 668). Fe2O3's old 0.90 V full-reduction rung is reference-only; live MRE can reduce ferric Fe2O3 to FeO through an uncertified ferric-to-ferrous diagnostic path, not a Fe2O3 -> Fe metal full-reduction rung. Na2O/K2O retain static fallback anchors pending activity/vapor-aware grounding because Na/K are volatile at 1873 K. Voltages are standard-state; runtime applies the Nernst melt-activity + pO2 correction.
- CoO is intentionally excluded from the MRE ladder: CoO E_decomp is about 0.49 V (Holmes 1986), above the NiO 0.39 V floor, and modeled cobalt feedstock is trace siderophile/native Fe-Ni-Co metal rather than a CoO MRE target.
- **The evaporation-α surface has tiered coverage.** K, Fe, Mg, and Cr carry source-specific measured α with citation. Na does not: the unsupported Sossi `[0.9,1.0]` interval is withdrawn, and runtime `alpha_e=1` is a marked analytical ceiling with physical envelope `[0,1]`, never a measurement or residual pin. Cr is `0.9 ± 0.1` over `1318–1563 K`, from Pound's selected McCabe–Hudson–Paxton solid-Cr Langmuir/Knudsen measurement. It is a clean pure-solid-Cr coefficient, not a silicate-melt coefficient: Cr melt activity remains in `P_sat`, and oxygen-contaminated surfaces can lower α. Al, Mn, and the current SiO hot-source form are proxy classifications. Ca and Ti are weaker than a proxy: the Zhang et al. 2014 CaTiO₃ activity proxy that used to back them was **withdrawn** (2026-08-08) as a mis-tagged HKL α, and runtime now carries `α=1.0` as an explicit `analytical_upper_bound` over `1200–2500 K` with envelope `[0.01,1.0]` — a Hertz–Knudsen ceiling with no measurement behind it, status-bearing and never certifying. The same withdrawal cascaded to the CaO, Ca2, TiO, and TiO₂ gas rows. Mn is the monoatomic-metal class proxy `α=1.0`, envelope `[0.5,1.0]`, over the liquid process band `1519–2334.526 K`; it is not a Mn-specific measurement. CrO₂ is the worst-placed of the set, and worse than earlier revisions of this page claimed. `data/vapor_pressures.yaml` carries a tier-2 broad proxy inherited wholesale from the Cr metal family (`0.9`, envelope `[0.48,1.2]`, `1400–2273.15 K`, tagged `broad_proxy_not_intrinsic`) — proxy evidence, not a measurement. Since 2026-08-28 the tag is wired into flux admission: `_load_evaporation_alpha_by_species` now emits such rows as provenance-carrying mappings (`alpha_proxy_tag`, never a bare number), and the provider-facing `_measured_alpha_control_view` strips them so the presence-based `_alpha_is_unmeasured` gate refuses — CrO₂ enters `missing_alpha` again (the fail-loud refusal fires, retained condensed) unless `chemistry_kernel.allow_unmeasured_alpha_fallback` is explicitly set, in which case it takes the marked alpha=1.0 prototype path recorded in `unmeasured_alpha_fallback_species`. (Pre-fix, measured 2026-08-28: the bare `0.9` satisfied the gate and the refusal never fired.) Where the fallback does engage, for species with no α row at all, it records `unmeasured_alpha_fallback_species`. Melt activity remains in `P_sat`, not α. See [`docs/output-interpretation.md`](output-interpretation.md).
- **SiO hot-source and cold-wall coefficients have different authority.** Wetzel & Gail 2013 use `0.52*exp(-3685/T)` as a solid-SiO particle-growth coefficient, not silicate-melt free-evaporation evidence. Runtime retains that form only as an explicitly UNCERTIFIED hot-source proxy over 1000–1800 K with envelope 0.003–0.067; a grounded melt coefficient remains absent. COLD-WALL condensation below the range separately uses the cited Pound 1972 high-supersaturation unity condensation coefficient (`alpha_c=1.0`), carrying the `[0.016, 1.0]` band and the no-direct-cold-wall-SiO-measurement gap.
- **Robinot-class O2 accuracy is an error budget, not a tuned score.** The lab-validation diagnostic reports a WARN-only `robinot_o2_error_budget` sidecar under `lab_oxygen_atom_partition`; it does not gate, reroute oxygen, debit reagents, or alter any ledger outcome. The two headline miss factors use different published normalizations against Robinot exp. 1 analyzer-visible O2 (`35 mg`): raw faithful source-side O2 potential is `0.881913 g`, or `25.20x`; the literature-alpha/top-area forward prediction is `0.656204 g`, or `18.75x` central (`18.25x-19.04x` area band). The diagnostic decomposes the remaining daylight into sourced terms: plume oxidation (`unquantified`, could lower simulated free O2), deposit gettering (`unquantified`, could lower simulated free O2), melt-redox retention (`runtime_accounted` when the terminal partition exposes it, but Robinot allocation remains unmeasured), post-run air oxidation (`unquantified`, can raise recovered-deposit oxygen but cannot close in-run analyzer O2), and analyzer/flow/baseline integration (`quantified_anchor`: exp. 1 `35 mg`, exp. 2 `39.229 mg`, about `11%` reproducibility floor). The central residual is explicit: `0.621204 g` O2-equivalent remains unallocated among sink channels. Negative result accepted: without position-resolved gas sampling and air-isolated deposit oxidation-state data, the residual is unexplained rather than fit away.
- Feedstock values include literature-derived ranges and estimates.

## Residuals against validation data after the multiphase re-ground

These are disagreements and regressions exposed by replacing linear thermochemistry with the phase-aware/JANAF surface. They are not calibration targets. The confidence tiers below distinguish a reproducible internal counterfactual from an external experimental reproduction; neither tier certifies product yield.

| Validated system | Current residual and direction | Decomposed error budget | Classification and confidence |
|---|---|---|---|
| KEMS/time-series-lake Mg-versus-SiO endpoint ranking | The original comparison disagreed on `2/34` comparable endpoint pairs. The JANAF-4th multiphase re-ground increased that to `3/34`; the later phase-basis/two-rail/Kress surface increased it again to `4/34` (`0.117647`). The observed literature rows did not change: the model ordering moved in the worse direction. The tracked regression pin is `tests/test_timeseries_validation_lake.py::test_endpoint_rank_metric_is_not_labeled_as_kinetic_ordering`. | The first added inversion is attributed to the multiphase Mg/SiO endpoint ordering; the second to the combined phase-basis/two-rail/Kress value surface. The available tracked artifacts do **not** separate that second inversion among those three engine changes, so assigning percentages would be invented. | **Known model disagreement; moderate confidence.** The `4/34` pin is reproducible, but this is a scale-free endpoint-rank residual, not a partial-pressure error, a dex fit, or sequential kinetic-order validation. |
| No-additives full track, final cleaned-melt Na2O | Final Na2O increases from `0.085182 kg` with the legacy Na segment to `0.305797 kg` with current multiphase Na thermochemistry: `+0.220615 kg` retained, so clean Na removal is about `0.22 kg` per track harder than the linear model claimed. | At track entry the current surface retains `+0.321319 kg` more Na2O; a `-0.166274 kg` smaller spent-reductant return partly offsets it; the higher electrochemical floor adds `+0.065570 kg`, closing to `+0.220615 kg`. Separately, liquid-fraction gating improves the current result from `0.366145` to `0.305797 kg`, a `-0.060348 kg` credit. That credit is a within-current-surface comparison and must not be subtracted from the already closed `+0.220615 kg` legacy-to-current delta. | **Known model consequence; high confidence for this executable counterfactual, low confidence as an experimental yield claim.** The harness changed only the Na thermochemistry and rejected a late-cooldown mutation; no matching end-to-end experiment establishes that either residual is physically correct. The supporting investigation artifact is not distributed in the tracked repository, so this historical counterfactual is not independently reproducible from a clean checkout. |

The builtin vapor tier also carries only the monatomic `Na(g)` source term. VapoRock-family equilibrium enumerates both `Na(g)` and `NaO(g)`, and the AlphaMELTS adapter can retain both species, while the builtin fit target is specifically VapoRock `Na(g)`. This is a **known analytic-tier model limitation**, not a curation artifact; its yield impact is unquantified, so no numeric correction is applied. Tracked implementation sources: `data/vapor_pressures.yaml`, `simulator/melt_backend/vaporock.py`, and `simulator/melt_backend/alphamelts.py`.

No quantitative Richter, gas-mixing, FeO-SiO2, or Sesko *process-reproduction* residual is claimed here: no matched mass-loss, partial-pressure, or time-series observable with a defensible engine/input/extrapolation split. What the reproduction battery (below) does score against these campaigns is narrower — *coefficient* residuals, and they are not the same claim. Richter contributes 14 comparable points (2 ordering-pass; 12 α matches, Mg residuals `0.0235–0.1396` absolute and SiO `−0.0601` to `+0.0039`). The Sossi 2019 gas-mixing corpus contributes 3 comparable points of 53 observations, 2 of them FINDINGs (K α literature `1` vs engine `0.13`; K γ `2.2e-4` vs `3.5e-5`, `0.798` dex). Šeško (6 observations) and the Plante 1992 FeO-SiO2 rows (4 observations) are still **0 comparable**. Those two remain thin or ungrounded validation gaps, not zero-error results, and an evaporation-coefficient match is not a process reproduction.

### Distribution paper comparison error budget

The distribution presets expose matched observables without retuning. The
2026-07-27 deterministic AlphaMELTS-backed runs below use the preset-declared
schedule and geometry; all Robinot records remain `assumed-input` because the
paper does not supply the full temperature-time profile, exposed melt area, or
analyzer recovery model.

| Paper | Observable | Published | Runtime | Residual | Status / dominant error budget |
|---|---|---:|---:|---:|---|
| Robinot 2026 | final O2 mass, kg | 0.000035 | 0.000344816 | +0.000309816 | `assumed-input`; source-side potential versus analyzer-visible O2, assumed schedule/area |
| Robinot 2026 | O2/feed mass fraction | 0.0105 | 0.102016 | +0.0915165 | `assumed-input`; same scope mismatch and assumptions |
| Robinot 2026 | feed-oxygen extraction fraction | 0.0247 | 0.242920 | +0.218220 | `assumed-input`; feed oxide oxygen denominator is represented, gas recovery is not |
| Pomeroy/Cardiff 2006 | non-condensed MLS-1A mass-loss fraction | 0.0117 | — | — | `unsupported-observable`; runner exports no pump-outlet mass and gross source vapor is not a substitute |
| Pomeroy/Cardiff 2006 | RGA Si/SiO/O2/H2O windows | qualitative signal | — | — | `observed/not-representable`; uncalibrated analyzer channels are not kg/h time series |

Dynamic chamber pressure and spatial post-run deposit composition remain
unsupported. Pomeroy achieved pressure is not reported, so its runtime pressure
is explicitly assumed and cannot certify any pressure-sensitive residual.
Šeško is blocked by the named dynamic-pressure certification gate. Paper-scale
MRE now has a separate intent-isolated reproduction surface; it does not run
through these vacuum-pyrolysis schedules.

### Yu 2025 hollow-anode MRE comparison error budget

The Yu reproduction replays the paper's measured cell-potential response
because the builtin electrolysis intent does not solve galvanostatic voltage.
That voltage is an input, not a predicted observable. The checked-in package
refuses execution until Figure 2(b) is digitized from authoritative pixels and
independently checked; no approximate voltage is supplied to make the case run.

| Observable | Runtime quantity | Published quantity | Disposition / dominant error budget |
|---|---|---|---|
| Applied current and charge | Executed 0.5 A intervals; 1800/5400/21600 C nominal | 0.5 A for 1/3/12 h | Execution audit only; tautological, not chemistry validation |
| Cell potential | Figure 2(b) measured-response replay | Figure 2(b) measured potential | `unsupported-observable` as a prediction; replay points cannot earn a voltage match |
| Faradaic efficiency | Committed source-side electron charge / applied charge | Exterior-RGA collected O2 basis, 47/53/42% | `out-of-domain`; hollow-anode collection, YSZ electronic leakage, Mo oxidation, gas residence, and RGA response are absent |
| O2 mass and time series | `terminal.oxygen_mre_anode_stored` source-side ledger O2 | Exterior-RGA collected O2; Table 2 reports 0.070/0.237/0.749 g | `out-of-domain`; Yu states exterior collection is a lower bound on total production |
| Cathodic Fe/Si/Ti/Mn presence | Bulk committed metal mol by species | Table 2 qualitative cathodic elements | Qualitative represented/not-represented only; Mn decomposition authority remains diagnostic |
| Cathodic P presence | No P2O5-to-P electrolysis mapping | P reported at 3 h and 12 h | `unsupported-speciation`; P is not dropped or remapped |
| Cathodic EDS fractions | No spatial Mo-alloy or cathode-region state | Supplement Figures S4-S6 local EDS regions | `unsupported-observable`; every source region must remain independent and must not be averaged |
| Outlet O2 volume fraction | No gas-volume, collection, or analyzer transport model | Figure 2(a) exterior RGA signal | `unsupported-observable`; no source-kg-to-outlet-vol% conversion is fabricated |

## DeMaria 1971 K Validation Rows

The K validation rows are held out from the Lamoreaux & Hildenbrand standard-term fit. Citation: DeMaria, G., Balducci, G., Guido, M. & Piacente, V. (1971), Apollo 12022 Knudsen-effusion mass-spectrometry vaporization study (REF-049), Proceedings of the Second Lunar Science Conference, vol. 2, pp. 1367-1380, ADS bibcode 1971LPSC....2.1367D.

Basis: Apollo 12022 lunar basalt; `log10 pK` is K(g) pressure in atm from the DeMaria KEMS comparison row; `log10 pO2` is bar after Table 1 O2-pressure interpolation/conversion; runtime comparison uses `X_KO0.5 = 8.516800e-4` and `gamma_KO0.5 = 3.5e-5`.

| T K | log10 pK atm | log10 pO2 bar | model-minus-DeMaria K residual dex |
|---:|---:|---:|---:|
| 1470.588235 | -8.600000 | -7.351965 | +1.152912 |
| 1449.275362 | -8.700000 | -7.608791 | +1.198283 |
| 1428.571429 | -8.800000 | -7.858279 | +1.241499 |
| 1408.450704 | -8.900000 | -8.100739 | +1.282637 |

## Vapour-rail / MELTS-family engine cross-check

The 2026-08-03 engine cross-check is the second external baseline beside the
mass-spec residuals above. It compares the live builtin vapour rail with
VapoRock at the **same** `lunar_mare_low_ti` oxide-mol composition and absolute
fO2. All 42 VapoRock evaluations used the VR-5 warm pool: 14 temperatures across
the externally validated 1350--1950 K envelope and fixed admitted
`log10(fO2/bar) = -9, -8, -7`. The fixed grid avoids silently clamping the live
rail's 1e-9 bar transport floor; IW-1 lies below that floor at lower
temperatures.

Liquid state is **unverified, not asserted** in this campaign. VapoRock used its
internal melt solve; the harness did not fabricate `liquid_fraction = 1` to
bypass the external liquid-fraction gate. Therefore the low-temperature rows
remain diagnostic pressure comparisons, not evidence that the feedstock is a
fully admitted liquid at every cell.

This is a measured-divergence report, not a validation verdict. Signed delta is
`log10(P_rail/P_VapoRock)`. The difference is retained as the observed
polymerisation/activity correction; no coefficient was calibrated or changed.
The temperature trend is the fitted delta slope at `log10(fO2/bar) = -8`.
Magnitude labels are descriptive only: **wild** means at least one matched cell
differs by 2 dex (100x) or more.

| Species | matched cells | VapoRock-only cells | median delta dex | delta range dex | delta T trend dex/100 K | median rail fO2 slope | median VapoRock fO2 slope | measured finding |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| Al | 31 | 11 | -0.425 | -0.486 to -0.364 | -0.027 | -0.750 | -0.750 | below 0.5 dex |
| Ca | 42 | 0 | +0.546 | **-2.317** to +0.856 | -0.626 | -0.500 | -0.500 | **wild** at 1950 K, log fO2 -9 |
| Cr | 42 | 0 | -0.194 | -0.273 to -0.148 | +0.019 | -0.750 | -0.750 | below 0.5 dex |
| CrO2 | 33 | 9 | -0.180 | -0.227 to -0.148 | +0.017 | +0.250 | +0.250 | below 0.5 dex with low-T coverage gaps |
| Fe | 42 | 0 | +0.014 | -0.212 to +0.341 | +0.022 | **-0.547** | -0.500 | magnitude small; slope differs by -0.047 |
| K | 42 | 0 | +0.775 | -0.399 to **+2.207** | -0.429 | -0.250 | -0.250 | **wild** at 1350 K, log fO2 -8 |
| Mg | 42 | 0 | -0.444 | -0.484 to +0.144 | -0.048 | -0.500 | -0.500 | below 0.5 dex |
| Na | 42 | 0 | +0.660 | -0.090 to **+1.536** | +0.654 | -0.250 | -0.250 | large (1--2 dex); L&H Pref, residual REPORT-ONLY vs VapoRock peer |
| SiO | 39 | 3 | -0.017 | -0.041 to -0.002 | +0.007 | -0.500 | -0.500 | close magnitude; three coverage gaps |
| Ti | 15 | 27 | -0.162 | -0.176 to -0.156 | +0.007 | -1.000 | -1.000 | close where shared; VapoRock answers much more of the grid |

Coverage is itself a finding. Of the 11 pressure species declared by both
surfaces, 10 produced matched points. Mn was rail-only in all 42 cells;
VapoRock did not answer it. VapoRock answered while the rail did not for Al
(11 cells), CrO2 (9), SiO (3), and Ti (27). VapoRock also produced 24 molecular,
atomic-oxygen, dimer, and oxide-gas species with no executable rail counterpart,
including O, O2, Si, NaO, KO, AlO, TiO, and the namespaced oxide gases. These
are coverage asymmetries, not zero pressures.

alphaMELTS and MAGEMin are relevant condensed-state context, not additional gas
pressure comparators. The VapoRock family uses alphaMELTS for the melt solve,
but alphaMELTS exposes no independent gas-pressure API; a separate direct call
would also risk its unpooled VapoRock helper. MAGEMin supplies phase assemblage
and liquid-fraction context but no vapour pressures, and the matched
MAGEMin-plus-ThermoEngine activity-evidence conversion is not runtime-wired.
No pressure parity is fabricated for either engine.


## Stage-0 bakeout: unlimited-reductant assumption and non-rock clearance

Stage 0 is meant to strip non-rock species (volatiles, salts, sulfides, native metals, refractory trace) from the feedstock before the cleaned silicate oxide composition reaches `MeltState` and the downstream melt backends. The operator-facing simplification is that unlimited C, CO, and O₂ reductant/oxidant are available during bakeout. **As coded, that assumption does not drive thermodynamic clearance for most species**, and the sections below make that structural fact explicit rather than leaving it implied by the ledger.

### Mechanism: routing by name, not clearance by reagent

Stage-0 clearance is primarily **name-routing**. Raw feedstock components are matched by normalized name strings against constant sets in the simulator core and dropped whole into terminal buckets (`terminal.offgas`, `terminal.stage0_salt_phase`, `terminal.stage0_chloride_salt_phase`, `terminal.stage0_sulfide_matte`, `terminal.drain_tap_material`, `terminal.slag`) with no reaction and no reagent debit. `MeltState` then receives only the oxides in the modeled oxide set — a structural filter, not a chemistry outcome.

Reagent-consuming stoichiometry exists in a **small number of gated reaction families only**:

1. **Complete oxidation** — organics and tar: C/H/O/N atoms → CO₂, H₂O, N₂, with O₂ drawn from the Stage-0 oxidant reservoir when the feed is oxygen-deficient. It raises on any atom outside C/H/O/N rather than silently mis-clearing organo-metallic or organo-halide content.
2. **Carbothermal sulfate reduction** — `SO3 + C → SO2 + CO`, requiring bulk SO₃ already declared in the salt bucket and an explicit per-feedstock carbon recipe.
3. **Boudouard equilibrium** — `C + CO2 → 2 CO`, requiring a declared CO₂ atmosphere or source.
4. **Perchlorate decomposition** — `ClO4 → Cl + 2 O2`; the O₂ is banked and the chlorine is credited to the chloride-salt residue.
5. **Carbonate decomposition** — a thermally driven `MCO3 → MO + CO2↑` with a temperature-dependent extent (see the carbonate note below), so that the metal oxide is returned to the melt rather than deleted.

Every other non-rock species is removed by matching its name into a terminal bucket. The "unlimited C/CO/O₂" statement is therefore an **assertion in the routing tables, not a consequence of modeled bakeout thermodynamics** against stubborn species. Where a reagent is genuinely consumed (the families above), it is consumed against a defined stoichiometry; everywhere else the reagent supply is irrelevant to the outcome because no reaction is evaluated.

### Per-species clearance table

The table below records, for each non-rock class, how the code handles it, whether the model reports it as cleared, the real reaction and product a furnace-survivable bake (roughly 950–1050 °C ramps) would deliver, whether reagent is actually consumed, and whether the handling is physically defensible.

| Species class | Code handling | Model clears? | Real reaction + product | Reagent consumed? | Physically defensible? |
|---|---|---|---|---|---|
| **Phosphate — P₂O₅** | Kept in the modeled oxide set; enters `MeltState` | No (intended) | Normal silicate-melt oxide component | n/a | **Yes** — igneous-correct; phosphate belongs in the melt. |
| **Sulfate as SO₃ surrogate (+ carbon recipe)** | `SO3 + C → SO2↑ + CO↑` | Yes | Carbothermal sulfate reduction (~600–1050 °C) | Yes (carbon) | **Yes** for the modeled SO₃ surrogate. |
| **Cation sulfate (CaSO₄/MgSO₄/FeSO₄)** | Dedicated cation-sulfate routing; optional carbon cleanup yields either the oxide (CaO/MgO/Fe₂O₃ → melt, with SO₂ + CO offgas) or the sulfide (CaS/MgS/FeS → sulfide matte, with CO) | Only with a declared carbon-cleanup recipe | Carbothermal reduction yields **CaS or CaO** (and equivalents), not a clean offgas | Yes (carbon), when cleanup is declared | **Reasonable** for the modeled product mode; but the oxide-vs-sulfide split is a recipe choice, and without a cleanup recipe the sulfate is carried in its own bucket rather than reduced. |
| **Perchlorate (ClO₄)** | `ClO4 → Cl + 2 O2` | Yes (decomposed) | Real thermal decomposition (~300–500 °C); product is a metal chloride | No (thermal) | **Partial** — decomposition is real, but the chloride lands as salt residue, not gasified. |
| **Chloride (Cl, NaCl, KCl, halide)** | Name-routed to the chloride-salt phase | Yes (as separated salt) | NaCl/KCl **volatilize under mbar vacuum** and **re-condense on cold walls** | No | **Weak** — the chloride is separated, not cleared; it is the same fouling mode the simulator otherwise tracks. |
| **Carbonate (MCO₃)** | Thermally driven `MCO3 → MO + CO2↑`; oxide → melt, CO₂ → offgas, undecomposed residual → salt phase | Partly (extent-limited) | `MCO3 → MO + CO2↑` at 400–900 °C; the metal oxide stays in the melt | No (thermal) | **Approximate** — cation returned to the melt, but decomposition is not fully speciated (see note). |
| **Native metals (Fe, Ni, Co, FeNi)** | Name-routed to the drain-tap material | Yes | Physically separate metal phase; drain-tap is real | No | **Yes** — native metal correctly bypasses the oxide melt. |
| **Organics / hydrocarbons / C / CH₄ / NH₃ / HCN** | Complete oxidation (or native-mixture pass-through) | Yes | Combustion/pyrolysis to CO₂ + H₂O + N₂, genuinely complete with the modeled O₂ | Yes (O₂) | **Yes** — the unlimited-O₂ assumption is genuinely modeled here; atom-restricted to C/H/O/N. |
| **H₂O and other volatiles** | Name-routed to offgas | Yes | Dehydration and vapor release (~100–700 °C) | No | **Yes** — trivially correct. |
| **Nitrate (NO₃, nitrate salts)** | Explicit unmodeled-nitrate marker; a declared nitrate **raises** | No (fails loud) | `MNO3 → MO + NOₓ↑` at 400–900 °C | n/a | **Gap, surfaced** — unmodeled, but fails loud rather than silently mis-clearing. |
| **Refractory fluoride (CaF₂/MgF₂/fluorite)** | Explicit keys only → slag (bare `f` raises) | Yes (to slag) | CaF₂ (b.p. ~2530 °C) is **refractory and does not clear** | No | **Weak** — genuinely stubborn; it belongs in the rump/melt, not a removed phase. |
| **Alkali fluoride (NaF, generic fluoride)** | Explicit keys only → chloride/salt phase (bare `f` raises) | Yes (as separated salt) | NaF (b.p. ~1700 °C) can volatilize/re-condense; not a clean thermal clearance | No | **Weak** — routed to the same separated-salt/fouling-risk bucket as chlorides, not gasified. |
| **Refractory trace (ZrO₂, REE, ThO₂, UO₂)** | Name/prefix-routed to slag | Yes (to slag) | Refractory; do not vaporize | No | **Mostly** — a routing choice; REE also partition into the melt, so pre-melt removal is a simplification. |

The salt-volatilization diagnostic is **warn-tier, not a clearance certificate.** When it evaluates NaCl/KCl
vapor outside the Antoine row's valid temperature range, it still returns an escape/retained split for operator
visibility but labels the result out-of-range and extrapolated and emits a warning — the non-oxide policy is to
*warn*, since salts have no first-principles engine coverage, rather than fail-closed. That warning must never
be promoted into a certified Stage-0 clearance claim.

### Where the open gaps are

Ranked by whether the error corrupts the melt composition handed to the silicate-equilibrium engines:

- **Cation-sulfate reduction is recipe-conditional and its product split is a choice.** A named cation sulfate (`CaSO4`/`MgSO4`/`FeSO4`) is routed to a dedicated cation-sulfate bucket, not the generic salt phase, so the Ca/Mg/Fe cation is not silently deleted. When a carbon-cleanup recipe is declared, that bucket is carbothermally reduced to either the oxide (returned to the melt) or the sulfide (routed to the sulfide matte), depending on the recipe's product mode. The limitations are that the oxide-versus-sulfide outcome is set by the recipe rather than by melt thermodynamics, and that without a declared cleanup recipe the sulfate is carried in its own bucket rather than reduced — so its cation neither reaches the melt nor is reported as unreduced feedstock. The separate bulk-SO₃ surrogate path handles feedstocks (such as the Mars cases) that pre-express their sulfur as SO₃.
- **Carbonate decomposition is approximate, not fully speciated.** The metal oxide is returned to the melt and the CO₂ offgases, so the cation is **not** wholesale lost, but the decomposition extent is a temperature-driven approximation (with an SiO₂-availability gate for Na₂CO₃) rather than a full carbonate-melt equilibrium. Any undecomposed residual is carried to the salt phase. Affects carbonaceous (CI/CM/Ceres/comet) and Mars-carbonate feedstocks.
- **Fluoride clearance is asserted, not achievable, and the two fluoride families route differently.** Refractory alkaline-earth fluorides (`CaF2`/`MgF2`/`fluorite`) are routed to the slag phase, and alkali fluorides (`NaF`, generic `fluoride`) to the separated chloride/salt phase; a bare `f` key raises rather than being routed. Neither route is real thermal clearance: CaF₂ is refractory (b.p. ~2530 °C) and C/CO/O₂ at furnace temperature will not gasify it, so it should remain in the rump/melt rather than a removed phase; NaF can volatilize and re-condense on cold walls, the same fouling failure mode as the chlorides it is bucketed with. The HF-route defluorination (SiO₂ + steam) is out of scope.
- **Chloride is separated, not cleared.** Perchlorate decomposition is real, but the chloride product is separated to a salt bucket rather than gasified. NaCl (b.p. ~1465 °C) and KCl (~1420 °C) volatilize under mbar vacuum at Stage-0 temperatures and re-condense on cold walls — the wall-fouling failure mode the simulator otherwise tracks — and the model still labels this "cleared."
- **Nitrates are unmodeled, and fail loud.** There is no nitrate reaction path; a declared nitrate component raises rather than being silently carried or mis-cleared. `MNO3 → MO + NOₓ↑` is easy chemistry at 400–900 °C, so this is a genuine coverage hole, but it is surfaced as an explicit error rather than a silent one. Impact is low for typical regolith feedstocks.

The structural point behind all of these: with unlimited reductant *assumed*, the code does not model that reductant *driving additional clearance reactions*. Reagent is consumed only in the gated families above; every other non-rock species is removed by name-routing. The reagent-supply assumption is a routing convenience, and the resistant species are exactly the cases where a real bake would push back against it.

## Iron redox in the melt (residual gaps)

Melt iron redox is not future work: it is live. The Fe vapor-pressure path consumes Kress–Carmichael 1991 ferric/ferrous splitting as the `a_FeO` authority, with the FeO activity assembled across the oxygen-fugacity range: a Holzheid/CALPHAD metal-saturated FeO activity below the pure-FeO iron–wüstite (IW) point, the Kress91 ferric/ferrous activity above IW(pure-FeO)+1, and a smooth one-log-unit blend between them. The consumed activity is clamped at the pure-FeO ceiling (`a_FeO ≤ 1.0`). Native-iron saturation is likewise computed, not stubbed: the extent of metal precipitation is derived from the ferrous FeO activity against the saturation activity implied by the prevailing fO₂ (`FeO(l) = Fe(metal) + ½ O₂`, Holzheid et al. 1997), and that authoritative extent sizes the ledger move that pulls Fe⁰ out of the melt. What remains limited is not whether these quantities are computed, but the reference axes they rest on:

- **The intrinsic-fO₂ seed carries ungrounded offsets.** The melt oxygen fugacity is seeded from a composition-to-fO₂ initializer that still applies ungrounded alkali and ferric `redox_offset` terms. Reduced lunar basalt conditions (Tuthill & Sato 1970, REF-038) and the reduced Martian basalt range (Herd et al. 2002, REF-039) provide planetary context, but neither source grounds these runtime offset coefficients. These perturb the fO₂ *input* only; the Kress91 fO₂→ferric/ferrous mapping downstream of it is grounded, but the starting point it maps from is not fully anchored.
- **The metal-saturation switch uses a self-consistent melt anchor on the wüstite basis.** The solver evaluates the central CALPHAD activity on its `stoichiometric_FeO_l` basis, converts it to Holzheid Eq. 9's iron-saturated wüstite basis using the code's `gamma_stoich/gamma_wustite` multiplier, and solves the Fe-saturation fO₂. Native-Fe precipitation uses that same conversion, so its ledger extent reaches zero at the anchor. At 1673.15 K the corrected anchors are `log10(fO₂/bar) = -11.5605` for LMS-1 and `-11.3186` for MGS-1. Pure-FeO IW remains the reference term in the saturation equation; the unchanged one-log blend window starts at the solved melt anchor. The intrinsic fO₂ seed offsets above remain ungrounded.
- **Ferric reduction in electrolysis is a diagnostic path, not a validated model.** The electrolysis path can reduce ferric Fe₂O₃ to FeO rather than driving the old over-reducing full-reduction rung, but that ferric-to-ferrous step is explicitly uncertified/diagnostic and is not a validated ferric-current-partition model.
- **The iron/silica de-confliction is one-sided.** The Fe vapor activity responds to melt fO₂, but the `SiO2 ⇌ SiO` lever still reads the headspace pO₂ rather than the melt fO₂. Full Fe/SiO de-confliction — using one consistent oxygen scale on both sides of the overlap — requires coupling the SiO side to the same melt fO₂, which is not yet done.

## Claim classes: what kind of number each output is

Every number this simulator emits belongs to one of four claim classes, and the class —
not the subsystem that produced it — determines how much of the error budget it inherits
and what kind of evidence can validate it. The Good Uses / Bad Uses lists below are
applications of this taxonomy.

| Class | Examples | Error behaviour | What validates it |
|---|---|---|---|
| **Ordinal** (sequence, order, which-first) | Na/K before Fe before SiO; "does skipping MRE lose anything?" | Robust: rides vapor-pressure ratios spanning many orders of magnitude, so it survives activity errors that would destroy an absolute prediction | Rank agreement with KEMS series and depletion experiments; cross-engine order agreement |
| **Ratio** (purity, selectivity, relative split) | Stage-tap contamination; Fe:SiO co-evolution in the 1500–1700 °C overlap | Partially self-correcting: systematic factors genuinely SHARED by both fluxes (HKL prefactor, geometry) cancel in the ratio. Alpha does NOT cancel — it is species-specific in this project (DS-007), so a cross-species ratio retains alpha_A/alpha_B | Cross-engine ratio agreement; paired-species measurements |
| **Inventory** (end-state, converged-hold) | Final yields at a depletion-verified hold; rump composition; stoichiometric O₂ total | Capped by conservation — but review (2026-08-19) forced precision here: ledger closure (≤5e-12 %) bounds the SUM of accounts, not the SPLIT between them, and "held long enough" is only meaningful when the hold criterion is inventory exhaustion itself, not wall-clock — a criterion the inventory instrument now reports per hour (`would_be_inventory_advance`) but which no campaign endpoint yet enforces: C0 still advances on temperature/wall-clock, and the `inventory_depletion` endpoint type is an upcoming feature. Given a depletion-verified hold, endpoint errors are capped by inventory; the split between destinations still inherits selectivity error | Atom/mass closure (bounds the sum); depletion-verified hold criterion; speciation cross-checks (bound the split) |
| **Cardinal** (absolute flux, absolute rate) | Wall-coating rate; time-to-depletion; cold-train peak mass flow; `campaigns_to_resinter` | Inherits the full error budget multiplicatively: activity × P_sat × alpha × transport × geometry; nothing cancels | Only physical measurement. No amount of simulation cross-checking certifies a cardinal number |

Two corollaries worth stating as limitations in their own right:

- **The mandate's two failure modes sit in different classes.** Incomplete extraction is
  ordinal + inventory and therefore comparatively robust at current error bars. Furnace
  coating is cardinal, and the in-tree validation lake currently contains **no deposition
  dataset at all** (DS-001/003/006 note "geometry/inventory absent"; the others have
  different gaps — none is a deposition measurement) — so the no-coating half
  of any conclusion inherits the full, unvalidated cardinal budget. Treat coating outputs
  as the least-certified numbers this simulator produces.
- **Converged-hold ledgers and rate trajectories deserve different confidence, even from
  the same run.** An end-state ledger at a generous hold is inventory-class; the per-hour
  trajectory that produced it is cardinal-class. Quoting a trajectory number with the
  end-state's confidence is a category error.

**The threshold reformulation of the no-coating claim, and
what it costs.** "Wall coating rate" is cardinal, but "no coating at all" can be a
*threshold* claim: if every wall segment upstream of the designated condenser stays above
the local dew point of every species present (P_sat(T_wall) > partial pressure), nothing
condenses — and dew points ride the vapor-pressure curves, which are among the
best-grounded data in the project. A conservative version needs only an UPPER BOUND on
partial pressure (total evolved inventory over volume suffices), so the claim inherits
threshold-class robustness rather than the full cardinal flux budget. Two limits:
(1) the *margin* to the dew point is still cardinal — how close you may run is a
flux-and-transport question; (2) the threshold covers CONDENSATION only. Reactive
deposition and chemical attack (alkali penetration of silica, SiO reaction with oxide
walls — the `reactive_exchange_templates` and `chemical_attack` entries in
`data/wall_materials.yaml`) do not require supersaturation and are *accelerated*, not
prevented, by hot walls. Hot-wall design therefore trades a condensation-fouling claim
(threshold, robust) for a corrosion claim — whose current in-tree grounding is severity
labels with an evidence census of 18 direct / 35 analogous / 27 uncharacterized, i.e. a
rate model does not exist yet.

A useful screen when reading any output: mass-loss and depletion experiments constrain the
**product** gamma × P_sat × alpha jointly (DS-001/003/005/006 in
`validation-data/timeseries/`), which is what inventory- and ordinal-class outputs ride on;
they do not decompose it. Claims that need the decomposition — anything transferring a
coefficient to a new composition, temperature, or geometry — are cardinal-adjacent and need
the individual factors, which is what KEMS activity data and evaporation-coefficient rows
(DS-007) supply.

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

## Extract-store reproduction battery

<a id="extract-store-reproduction-battery"></a>

The reproduction battery replays every in-scope literature observation (vapour-pressure and
rate series, activity coefficients, evaporation coefficients, Gibbs tables and transition
points) through the engines and reports the residuals. At this release it evaluates **2085
observations, 21 of them comparable**; the rest carry typed skip reasons. Mismatches are reported
as findings, and tolerances are never widened to pass. The full generated residual tables, coverage
breakdowns and findings are on [Validation battery rollup](validation-battery-rollup.md).
The cross-engine scoring behind these residuals is described in
[Validation Battery](validation-battery.md).

## Transition-metal monoxide gas carriers

Four transition-metal monoxide gas carriers now have executable, status-bearing catalog rows.
The class-level thermochemistry omission is closed, while pressure-reference and kinetic authority
remain deliberately limited:

| carrier | source thermochemistry | catalog row | nature of the gap |
|---|---|---|---|
| `FeO(g)` | **available** (NASA CEA) | present | diagnostic-only CEA gas-association composition; never debits or certifies |
| `NiO(g)` | **available** (NASA CEA) | present | diagnostic-only CEA gas-exchange composition; never debits or certifies |
| `MnO(g)` | **available** (reviewed IVTAN table 1436 Shomate fit, 1000–3000 K) | present | composed on the existing liquid-MnO reference; executable only on the 1519–2273.15 K intersection; typed refusal outside |
| `CoO(g)` | **available** (reviewed IVTAN table 1335 Shomate fit, 1000–3000 K) | present | composed on the reviewed CoO(cr)-to-Co(g) screen; executable only on the 1400–2000 K intersection; typed refusal outside |

`FeO(g)` is the consequential one for oxidising recipes. Its landed association row
derives the `+0.5` O2-channel exponent from `Fe(g) + 0.5 O2(g) -> FeO(g)` and exposes the
competitor in the oxidative-volatility screen. It remains an upper-screen instrument:
the Fe base is an activity-folded effective-pressure fit and the FeO kinetic coefficient
is an explicit Hertz–Knudsen `alpha=1` ceiling, not form- and class-matched validation.
The resulting pressure is observable, but the channel is never flux-eligible and never
debits inventory. No selective high-fO2 extraction window may be certified from this row.

### Transition-metal monoxide gas channels: coverage closed, authority still limited

The VapoRock gas set still carries Mn and Co as atomic gas only, but the simulator catalog no longer
inherits that omission. It adopts the independently reviewed IVTAN MnO(g)/CoO(g) candidate
rows without retuning their coefficients. Both channels compose the gas-only association against an
existing condensed-reference screen, derive congruent activity exponent 1 and oxygen exponent 0 from
stoichiometry, and preserve the full gas-fit domain and uncertainty receipt. Runtime evaluation refuses
outside the narrower executable pressure intersection before any continuation can reach the flux path.

The retained dissociation-energy widths are 1.8 kcal mol⁻¹ for MnO and 0.13 eV for CoO (the wider
Sorensen/Pedley–Marshall envelope, not IVTAN’s ±2.51 kcal shorthand). At 1500/2000/2500 K these
become ±0.262/0.197/0.157 dex for MnO and ±0.437/0.328/0.262 dex for CoO. The earlier
omission screen remains useful context for the physical importance of the newly visible channels:

\[
f_{\mathrm{MO}} = \frac{r}{1+r},\quad r = K_p(T)\,p_{\mathrm{O_2}}^{1/2}.
\]

| condition | MnO association-envelope context | CoO association-envelope context |
|-----------|----------------------------------|----------------------------------|
| Deep vacuum floor (\(p_{\mathrm{O_2}}\sim 10^{-12}\) bar) | **negligible–minor** (≲4% in gate band; ≪1% above ~2000 K) | **negligible** (≪0.1%) |
| C2A vacuum ceiling (\(p_{\mathrm{O_2}}\sim 10^{-8}\) bar) | **MATERIAL below ~1950 K** at Pedley upper (up to tens of %; nominal is minor); minor above ~2100 K | **negligible** (≤~1% at cold gate edge) |
| Elevated pO₂ ≳ 0.1 mbar (C2B/C3/C4/C6 Si-hold, C5 MRE 0.01–0.1 bar) | **MATERIAL** all T bands (upper often ≳50–100%) | **MATERIAL** in gate/recipe bands; minor–MATERIAL in planned-raise band |

Association is exothermic: **hotter T at fixed pO₂ reduces** \(f_{\mathrm{MO}}\); oxidizing
overhead, not an HT raise, strengthens the monoxide competitor.

**What not to claim:** authoritative Mn/Co flux, inventory depletion, condenser-stage purity, or a
certified selective extraction window. All four monoxide rows share the diagnostic `alpha=1` upper-bound
policy, remain flux-dormant at every temperature, and retain C5 ledger gaps until form- and
system-class-matched kinetic evidence exists.

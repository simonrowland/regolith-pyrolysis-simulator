# Model Limitations

This project is a comparative process simulator. It is not a validated engineering design, a process guarantee, or a substitute for thermodynamic and hardware testing.

This page states the current limitations as facts, with the physical or modeling reason for each. It is the companion to [`docs/chemistry-methods.md`](chemistry-methods.md), which describes how the chemistry is computed, and to [`docs/chemistry-provenance.yaml`](chemistry-provenance.yaml), which carries the machine-readable provenance for every grounded coefficient referenced below.

## Current Approximation Layers

- The authoritative vapor-pressure model is the builtin Antoine + Ellingham provider. It is simplified, and its per-species fit/source labels are reported through `vapor_pressure_source_report`. VapoRock is installed as a diagnostic-only shadow when available; its full gas speciation is reported under `vaporock_full_speciation_Pa` and does not decide the authoritative `VAPOR_PRESSURE` surface. Silent fallback to backend vapor pressures remains forbidden unless `chemistry_kernel.allow_fallback_vapor: true` is set.
- The oxide activities the vapor-pressure path consumes come from the builtin analytic treatment, not from an external phase-equilibrium engine: non-iron oxides use **constant table gamma** (`a = gamma* * X` for mid-range composition) with a thin pure-endmember continuity shell that enforces Raoultian `a -> 1` only for `X > 0.99`, and iron uses the Kress/CALPHAD FeO activity described under *Iron redox in the melt* below. The one-parameter pseudo-binary regular solution (`ln gamma = ln gamma* (T*/T)(1-X)^2`) is **held** after enghar median regression attribution (chemact-root 2026-08-05). This analytical provider is the **`internal-analytical`** model in trust-architecture vocabulary (legacy backend name `stub`, still accepted on input and canonicalized to the `internal-analytical` serialization token); it is denylisted from certification gates and never holds ledger authority for a certified claim. When ThermoEngine/MELTS is installed it supplies a Gibbs-minimized silicate-equilibrium activity (`MELTS activity convention`, `a_i = exp((mu_i - mu_i0) / RT)`) as a diagnostic and phase-context shadow; it is not promoted into the authoritative vapor-pressure activity slot. Multi-component excess G remains the real activity gap (MC-5 / t-529). The current alkali anchors are grounded to Sossi et al. 2019 for Na and to DeMaria et al. 1971 as carried by Sossi & Fegley 2018 for K; other rows use Sossi & Fegley 2018.
- **CF-3 alkali temperature dependence is not invented.** The live Na/K anchors are on the single-cation basis (`NaO0.5`, `KO0.5`) at 1673 K and 1500 K. Runtime applies those table values as constant mid-range gamma (no analytical `T*/T` scaling). Outside the point-anchor temperature domain the numeric result still drives flux but is status-bearing `out_of_gamma_domain`, never a clean certified point.
- **Na vapor rail is uncertified coherent L&H standard-state pair (t-383, 2026-08-08).** The active high-T path is the liquid-NaO0.5 `standard_reaction_term` assembled from Lamoreaux & Hildenbrand 1984 Tables 2/4 (DOI 10.1063/1.555706; dual-extracted, minimax Antoine residual 1.18e-4 dex on 1405–1600 K) plus the constant-table `gamma_NaO0.5=1e-3` anchor (Sossi 2019 magnitude, coherent by provenance in the L&H frame). The prior Chase/JANAF gas-standard fugacity + gamma compensating-errors surface (Alternative B) is retired; the full-VapoRock/TE-μ0 candidate is a `status_bearing_non_authoritative` shadow bracket only (ADR-001 TE-μ0 class retirement, same class as demoted Ca/Mg). Investigation (`docs-private/research/2026-07-20-na-investigation/report.md`) and the ratify refresh (`docs-private/reviews/2026-08-08-t383-ratify/kimi-review.md`) showed the old "3.5× over DeMaria" line was **misbound** and must not be reused. Machine-readable `authority_class: uncertified` + `coherent_pair` + `shadow_bracket` emit on the Na provenance/diagnostic. **DeMaria 12022 and 12065 are held-out validation only** with pre-registered digitization σ; do not retune gamma or the standard term to close those rows. K remains independent on the same L&H literature basis.

- **The runtime K path uses the liquid KO0.5 convention but is not DeMaria-closed.** The consumed vapor path uses the `gamma*_KO0.5 = 3.5e-5` point anchor from the DeMaria et al. 1971 Apollo 12022 KEMS inversion carried by Sossi & Fegley 2018 at 1500 K as a **constant table gamma**, and a liquid KO0.5 standard-reaction term assembled from Lamoreaux & Hildenbrand 1984 K2O(l), K(g), and O2(g) tables. This remains UNCERTIFIED because K2O thermodynamic data are limited, the comparison is sub-liquidus lunar basalt, and a multi-component high-temperature activity surface is not fitted.
- **Both DeMaria anchors are sub-liquidus for Apollo 12022.** The estimated liquidus is about 1573 K, so the fully molten model is being compared against partial-melt data at the 1429 K anchor. Sub-liquidus offgassing undercount and melt-fraction gating are tracked under t-109.
- **The pure-endmember discontinuity is removed via a local shell, but multicomponent fidelity remains limited.** A thin continuity shell for `X > 0.99` gives `gamma -> 1` and `a -> 1` continuously as `X -> 1`, eliminating the former branch from `gamma*X` to 1 (31.1x for the Cr2O3 anchor) without mid-range regular-solution curvature. Cross-interaction parameters and assemblage changes remain unmodeled (MC-5).
- Heat transfer is simplified: solar concentration is assumed to maintain target temperatures rather than fully modeling radiative, conductive, and convective losses.
- **Sweep-gas sensible heating is unmodeled for every carrier.** The energy ledger includes no carrier heat-capacity term, so it cannot distinguish monatomic Kr, Ar, or He (`C_p = 5/2 R` for an ideal gas) from diatomic N₂ (`C_p = 7/2 R`). Carrier choice therefore changes the modeled transport and separation behavior, but not furnace or recirculation heating duty.
- Pipe conductance is a simplified bleed input, not detailed CFD. Cold-train hardware ratings reduce to the authoritative capacity `C`; the bleed provider partitions O₂ among admitted/stored, accumulated, relieved, and held legs, while turbine fields are read-only projections rather than an independent cap. Turbomachinery remains a reduced model rather than a validated design.
- Evaporation depletion is a one-hour analytic integration model: the HKL driving force and vapor pressures are evaluated once at the start of the tick, then parent-oxide and shared-O2 pools deplete as first-order reservoirs within that tick. This smooths the time integration but is not a new thermodynamic equilibrium solve.
- Finite overhead headspace pO2 is controlled by `overhead_headspace.enabled` and defaults ON. When enabled, `process.overhead_gas` holds melt-released evaporation O2 plus O2 credited by native-Fe saturation and reducing Fe redox re-speciation, converts that inventory to ideal-gas partial pressures, and hands it to the authoritative cold-train capacity/accumulator/relief partition. Poiseuille conductance is an input to the bleed calculation, not the disposition authority. Stage 0 oxygen and MRE anode oxygen still bypass the headspace. **Molecular-flow / transitional pipe conductance is out of scope until 0.7 (t-379)**; until then, ledger-authoritative evaporation yields refuse when Kn exceeds `VISCOUS_KNUDSEN_MAX` at nonzero overhead because evolved `P_bulk` still uses the viscous-only Poiseuille carrier (transport-model validity domain — not a Kn safety or coating gate). The 0.6.3 optimizer floor (~1 mbar, Kn≈0.004) never enters that domain.
- **Source evaporation uses a series-resistance form with HKL upper-bound authority.** The flux denominator is `r_interface + r_gas + r_melt` with `r_interface = 1/(α·k_HKL)`, `r_gas = 1/k_g` only for continuum `Kn < 0.01` (`k_g = M·Sh·D_AB/(L·R·T)`), and `r_melt = 0` until species/state melt-transfer inputs exist. The former universal `1e-4·√axial_stir` melt conductance is refused (`uncertified_melt_resistance_model`); axial stir does **not** drive source-side `r_melt` under the current policy. Emitted diagnostics and Pareto replay artifacts carry `authority_class: "upper-bound"` and `authority_reason: "missing-species-state-dependent-melt-transfer-inputs"`. Fuchs–Sutugin is not used on the source-flux path. On the condensation/wall side the Sherwood number may still use radial-stir enhancement (`Sh_eff = 3.66 × √radial`); the default radial component is `1.0` (laminar). This is a reduced series-resistance model, not resolved boundary-layer CFD.
- **The inert sweep is a static overhead pressure, not a flow rate.** The pN₂ cover enters the transport model as an overhead pressure (setting the Knudsen number and the gas-side resistance) and as a pressure-and-conductance bleed of headspace oxygen; there is no commanded sweep *rate*, volumetric flow, or residence-time input. Two consequences follow. First, the advective removal of evolved vapor and co-evolved oxygen that a faster flowing sweep would provide is not resolved separately from the static-pressure diffusion resistance — the model can represent that raising the inert *pressure* impedes diffusion, but not that a flowing sweep sped up would carry vapor away and lower the surface back-pressure. Second, the co-evolved-oxygen self-poisoning (an evolving species releases O₂, which raises the effective pO₂ and suppresses further evolution) acts with a one-step lag through the headspace ledger — oxygen credited this tick suppresses the *next* tick's solve — rather than as an instantaneous local-surface balance in which oxygen from the current dissociation solve poisons that same solve. A small related impurity: the inert overhead (total) pressure leaks weakly into the iron equilibrium through the Kress91 FeO-activity path (about a 10⁻⁷ relative dependence at 5–15 mbar, iron only; SiO and the alkalis are clean). Neutral pressure should touch transport but no equilibrium; this is a known low-severity decoupling to fix.
- **Wall re-evaporation uses a per-species reactivity class plus a cross-species wall-chemistry model.** Physisorbing species use the reversible pure-species `P_sat(T_wall)` backstop, so a sufficiently hot wall rebounds the deposit. SiO is treated as reactive: wall capture disproportionates it to physical products (`SiO -> 0.5 SiO2 + 0.5 Si`) and uses an effective product `P_sat ~= 0`. The wall chemistry also routes Mg against wall SiO2 (`2 Mg + SiO2 -> 2 MgO + Si`) and Fe against free wall Si (`Fe + Si -> FeSi`). Na and K credit elemental ledger species only, but carry a diagnostic activity-depression / binding state with a grounded disilicate saturation anchor (`0.5 mol Na2O/K2O per mol SiO2`, Kracek-family source surface). Ca, Mn, Cr, Al, Ti, and CrO2 remain reversible physisorbers. Residual gaps remain: FeSi2 and fO₂-dependent silicide/fayalite partition, the Na/K activity rate-law beyond the saturation cap, Mg passivation, Mn/Cr fouled-wall reactivity, a non-interpolated Na/K saturation temperature band fixed at the cold-wall 0.5 anchor, and the run-to-run fouling lifecycle beyond the transient per-wall state hook.
- **Vapor-pressure fit_target convention** (per-species metadata in `data/vapor_pressures.yaml`). Each `metals` entry declares one of three `fit_target` modes:
  - **`pure_component_psat`** (Ca / Al / Ti / Cr / Mn): the Antoine fit reproduces pure-metal saturation pressure `P_sat(T)`. The melt's metal-vapor partial pressure is then `P_metal = a_M(l) × P_sat`, where `a_M(l)` is the liquid metal activity computed from the oxide-decomposition equilibrium constant `K = exp(−ΔG_f / RT)` with the per-species `n_M`, `n_ox`, and the prevailing `pO₂`. Single-counted by construction.
  - **`standard_reaction_term`** (K / Na): the Antoine-form row is a fitted representation of an explicit standard-reaction term — liquid `KO0.5(l) = K(g) + 0.25 O2(g)` and liquid `NaO0.5(l) = Na(g) + 0.25 O2(g)` from Lamoreaux & Hildenbrand 1984 Tables 2/4. The consumer applies `a_MO0.5` and `pO2^-0.25` explicitly; wall condensation does not reuse this melt-source term as pure-species `P_sat`.
  - **`pseudo_psat_backsolved_from_vaporock`** (Mg / Fe / SiO; Na historical only): the Antoine fit is a pseudo-`P_sat` whose `A` coefficient is back-solved on a fixed VapoRock calibration grid (`lunar_mare_low_ti`, Kress91 IW fO₂, single-feedstock reference) so that `a_M × P_sat_pseudo ≈ P_metal_VapoRock` at the calibration point. The chain is still single-counted (γ_M lives inside the pseudo-A coefficient), but the fit residual relative to VapoRock grows with feedstock and fO₂ distance from the calibration grid. Na's pseudo row is inactive provenance-only after t-383 (active rail is L&H `standard_reaction_term`). The builtin provider remains authoritative; VapoRock can shadow the run diagnostically but has no ledger or `vapor_pressure_source_report` authority.
- Condensation routing is a staged engineering approximation. A canonical species-to-stage registry, combined with a per-pipe-segment wall-temperature model, pins the routing surface (see `stage_purity_report` in the runner output), but cold-spot effects on real hardware geometry require physical validation.
- MRE behavior is a reduced voltage/current/product model, not a full electrochemical cell simulator.
- **The metallothermic-shuttle temperature-acceptance gate is engine-strict, but the shuttle reactions themselves are temperature-independent inside the gate.** The JANAF-4th multiphase Ellingham re-ground (2026-07-09) that grounds the shuttle puts the FeO crossovers at K/Fe ≈ 836 °C and Na/Fe ≈ 1181.5 °C; the executable gate refuses any shuttle dispatch with non-positive thermodynamic margin at the dispatch temperature. Under that re-ground the gate refuses K→FeO across the practical melt window and refuses Na→FeO above 1181.5 °C; refusals are recorded in the runner output's `shuttle_refusal_history`. The recipe catalog has been tuned to match — the default metallothermic-polish recipe is sodium-only, and the paired melt-cleanup recipe cools to 1150 °C for the sodium cleanup. Shuttle self-reflux (intra-stage recycling of freshly reduced alkali back into the same melt) remains future engine work. Kress–Carmichael 1991 ferric/ferrous redox is live for the Fe `a_FeO` used by the shuttle, but the intrinsic fO₂ source and the pure-FeO IW switch anchor remain limitations (see *Iron redox in the melt* below).
- **Metal-phase stratification is diagnostic-only; general drain-tap behavior is not modelled.** Builtin-authored reduced metal is disposed at hour boundaries into mol-native `process.metal_phase_bottom_pool` and `process.metal_phase_float_layer` accounts, with a first-order contact diagnostic and density-contrast verdict. C6 is the narrow exception: each tick's net thermite Al is atom-conservingly routed from `process.metal_phase` to the existing `terminal.drain_tap_material` product account after back-reduction. If a backend-authored transition touches `process.metal_phase`, that account remains authoritative and the builtin stratifier emits read-only bottom/float projections without committing them. Builtin pool accounts are temporarily restored to legacy `process.metal_phase` staging before chemistry ticks and campaign initialization, so no evaporation, redox, or gas-escape behavior reads the diagnostic split. `product_ledger()` aggregates all three metal-phase accounts plus the terminal tap. Correlations used outside their cited temperature ranges are labeled as extrapolations. A general behavior-changing settling or tap gate remains future work.
- **MRE decomposition-voltage ladder:** NiO (0.3864 V at 1873.15 K) plus FeO, Cr2O3, MnO, SiO2, TiO2, and Al2O3 are raw-thermo reanchored (`E = -DeltaGf(T)/(nF)`; NIST-JANAF/Chase 1998 and companion evaluations, with phase-correct NiO from Mah & Pankratz 1976 USBM Bulletin 668). Fe2O3's old 0.90 V full-reduction rung is reference-only; live MRE can reduce ferric Fe2O3 to FeO through an uncertified ferric-to-ferrous diagnostic path, not a Fe2O3 -> Fe metal full-reduction rung. Na2O/K2O retain static fallback anchors pending activity/vapor-aware grounding because Na/K are volatile at 1873 K. Voltages are standard-state; runtime applies the Nernst melt-activity + pO2 correction.
- CoO is intentionally excluded from the MRE ladder: CoO E_decomp is about 0.49 V (Holmes 1986), above the NiO 0.39 V floor, and modeled cobalt feedstock is trace siderophile/native Fe-Ni-Co metal rather than a CoO MRE target.
- **The evaporation-α surface has tiered coverage.** K, Fe, Mg, and Cr carry source-specific measured α with citation. Na does not: the unsupported Sossi `[0.9,1.0]` interval is withdrawn, and runtime `alpha_e=1` is a marked analytical ceiling with physical envelope `[0,1]`, never a measurement or residual pin. Cr is `0.9 ± 0.1` over `1318–1563 K`, from Pound's selected McCabe–Hudson–Paxton solid-Cr Langmuir/Knudsen measurement. It is a clean pure-solid-Cr coefficient, not a silicate-melt coefficient: Cr melt activity remains in `P_sat`, and oxygen-contaminated surfaces can lower α. Al, Mn, and the current SiO hot-source form are proxy classifications. Ca and Ti are weaker than a proxy: the Zhang et al. 2014 CaTiO₃ activity proxy that used to back them was **withdrawn** (b-136/t-559, 2026-08-08) as a mis-tagged HKL α, and runtime now carries `α=1.0` as an explicit `analytical_upper_bound` over `1200–2500 K` with envelope `[0.01,1.0]` — a Hertz–Knudsen ceiling with no measurement behind it, status-bearing and never certifying. The same withdrawal cascaded to the CaO, Ca2, TiO, and TiO₂ gas rows. Mn is the owner-ratified monoatomic-metal class proxy `α=1.0`, envelope `[0.5,1.0]`, over the liquid process band `1519–2334.526 K`; it is not a Mn-specific measurement. CrO₂ is the worst-placed of the set, and worse than earlier revisions of this page claimed. `data/vapor_pressures.yaml` carries a tier-2 broad proxy inherited wholesale from the Cr metal family (`0.9`, envelope `[0.48,1.2]`, `1400–2273.15 K`, tagged `broad_proxy_not_intrinsic`) — proxy evidence, not a measurement. b-314 (2026-08-28) wired the tag into flux admission: `_load_evaporation_alpha_by_species` now emits such rows as provenance-carrying mappings (`alpha_proxy_tag`, never a bare number), and the provider-facing `_measured_alpha_control_view` strips them so the presence-based `_alpha_is_unmeasured` gate refuses — CrO₂ enters `missing_alpha` again (the SC-67 fail-loud refusal fires, retained condensed) unless `chemistry_kernel.allow_unmeasured_alpha_fallback` is explicitly set, in which case it takes the marked alpha=1.0 prototype path recorded in `unmeasured_alpha_fallback_species`. (Pre-fix, measured 2026-08-28: the bare `0.9` satisfied the gate and the refusal never fired.) Where the fallback does engage, for species with no α row at all, it records `unmeasured_alpha_fallback_species`. Melt activity remains in `P_sat`, not α. See [`docs/output-interpretation.md`](output-interpretation.md).
- **SiO hot-source and cold-wall coefficients have different authority.** Wetzel & Gail 2013 use `0.52*exp(-3685/T)` as a solid-SiO particle-growth coefficient, not silicate-melt free-evaporation evidence. Runtime retains that form only as an explicitly UNCERTIFIED hot-source proxy over 1000–1800 K with envelope 0.003–0.067; a grounded melt coefficient remains absent. COLD-WALL condensation below the range separately uses the cited Pound 1972 high-supersaturation unity condensation coefficient (`alpha_c=1.0`), carrying the `[0.016, 1.0]` band and the no-direct-cold-wall-SiO-measurement gap.
- **Robinot-class O2 accuracy is an error budget, not a tuned score.** The lab-validation diagnostic reports a WARN-only `robinot_o2_error_budget` sidecar under `lab_oxygen_atom_partition`; it does not gate, reroute oxygen, debit reagents, or alter any ledger outcome. The two headline miss factors use different published normalizations against Robinot exp. 1 analyzer-visible O2 (`35 mg`): raw faithful source-side O2 potential is `0.881913 g`, or `25.20x`; the literature-alpha/top-area forward prediction is `0.656204 g`, or `18.75x` central (`18.25x-19.04x` area band). The diagnostic decomposes the remaining daylight into sourced terms: plume oxidation (`unquantified`, could lower simulated free O2), deposit gettering (`unquantified`, could lower simulated free O2), melt-redox retention (`runtime_accounted` when the terminal partition exposes it, but Robinot allocation remains unmeasured), post-run air oxidation (`unquantified`, can raise recovered-deposit oxygen but cannot close in-run analyzer O2), and analyzer/flow/baseline integration (`quantified_anchor`: exp. 1 `35 mg`, exp. 2 `39.229 mg`, about `11%` reproducibility floor). The central residual is explicit: `0.621204 g` O2-equivalent remains unallocated among sink channels. Negative result accepted: without position-resolved gas sampling and air-isolated deposit oxidation-state data, the residual is unexplained rather than fit away.
- Feedstock values include literature-derived ranges and estimates.

## Honest residuals after the multiphase re-ground

These are disagreements and regressions exposed by replacing flattering linear thermochemistry with the phase-aware/JANAF surface. They are not calibration targets. The confidence tiers below distinguish a reproducible internal counterfactual from an external experimental reproduction; neither tier certifies product yield.

| Validated system | Current residual and direction | Decomposed error budget | Classification and confidence |
|---|---|---|---|
| KEMS/time-series-lake Mg-versus-SiO endpoint ranking | The original comparison disagreed on `2/34` comparable endpoint pairs. The JANAF-4th multiphase re-ground increased that to `3/34`; the later phase-basis/two-rail/Kress surface increased it again to `4/34` (`0.117647`). The observed literature rows did not change: the model ordering moved in the worse direction. The tracked regression pin is `tests/test_timeseries_validation_lake.py::test_endpoint_rank_metric_is_not_labeled_as_kinetic_ordering`. | The first added inversion is attributed to the multiphase Mg/SiO endpoint ordering; the second to the combined phase-basis/two-rail/Kress value surface. The available tracked artifacts do **not** separate that second inversion among those three engine changes, so assigning percentages would be invented. | **Known model disagreement; moderate confidence.** The `4/34` pin is reproducible, but this is a scale-free endpoint-rank residual, not a partial-pressure error, a dex fit, or sequential kinetic-order validation. |
| No-additives full track, final cleaned-melt Na2O | Final Na2O increases from `0.085182 kg` with the legacy Na segment to `0.305797 kg` with current multiphase Na thermochemistry: `+0.220615 kg` retained, so clean Na removal is about `0.22 kg` per track harder than the linear model claimed. | At track entry the current surface retains `+0.321319 kg` more Na2O; a `-0.166274 kg` smaller spent-reductant return partly offsets it; the higher electrochemical floor adds `+0.065570 kg`, closing to `+0.220615 kg`. Separately, liquid-fraction gating improves the current result from `0.366145` to `0.305797 kg`, a `-0.060348 kg` credit. That credit is a within-current-surface comparison and must not be subtracted from the already closed `+0.220615 kg` legacy-to-current delta. | **Known model consequence; high confidence for this executable counterfactual, low confidence as an experimental yield claim.** The harness changed only the Na thermochemistry and rejected a late-cooldown mutation; no matching end-to-end experiment establishes that either residual is physically correct. The supporting investigation artifact is not distributed in the tracked repository, so this historical counterfactual is not independently reproducible from a clean checkout. |

The builtin vapor tier also carries only the monatomic `Na(g)` source term. VapoRock-family equilibrium enumerates both `Na(g)` and `NaO(g)`, and the AlphaMELTS adapter can retain both species, while the builtin fit target is specifically VapoRock `Na(g)`. This is a **known analytic-tier model limitation**, not a curation artifact; its yield impact is unquantified, so no numeric correction is applied. Tracked implementation sources: `data/vapor_pressures.yaml`, `simulator/melt_backend/vaporock.py`, and `simulator/melt_backend/alphamelts.py`.

No quantitative Richter, gas-mixing, FeO-SiO2, or Sesko *process-reproduction* residual is claimed here: no matched mass-loss, partial-pressure, or time-series observable with a defensible engine/input/extrapolation split. What the t-512 battery below does score against these campaigns is narrower — *coefficient* residuals, and they are not the same claim. Richter contributes 14 comparable points (2 ordering-pass; 12 α matches, Mg residuals `0.0235–0.1396` absolute and SiO `−0.0601` to `+0.0039`). The Sossi 2019 gas-mixing corpus contributes 3 comparable points of 53 observations, 2 of them FINDINGs (K α literature `1` vs engine `0.13`; K γ `2.2e-4` vs `3.5e-5`, `0.798` dex). Šeško (6 observations) and the Plante 1992 FeO-SiO2 rows (4 observations) are still **0 comparable**. Those two remain thin or ungrounded validation gaps, not zero-error results, and an evaporation-coefficient match is not a process reproduction.

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

The K validation rows are held out from the Lamoreaux & Hildenbrand standard-term fit. Citation: DeMaria, G., Balducci, G., Guido, M. & Piacente, V. (1971), Apollo 12022 Knudsen-effusion mass-spectrometry vaporization study, Proceedings of the Second Lunar Science Conference, vol. 2, pp. 1367-1380, ADS bibcode 1971LPSC....2.1367D.

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
| Na | 42 | 0 | +0.660 | -0.090 to **+1.536** | +0.654 | -0.250 | -0.250 | large (1--2 dex); t-383 L&H Pref, residual REPORT-ONLY vs VapoRock peer |
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

The full per-cell pressures, censored/refused observations, coverage labels,
per-temperature fO2 slopes, and exact wild-divergence coordinates are in
[`docs-private/research/2026-08-03-vapour-rail-engine-crosscheck/engine_crosscheck_report.json`](../docs-private/research/2026-08-03-vapour-rail-engine-crosscheck/engine_crosscheck_report.json),
with the human-readable companion
[`engine_crosscheck_report.md`](../docs-private/research/2026-08-03-vapour-rail-engine-crosscheck/engine_crosscheck_report.md).

## Stage-0 bakeout: unlimited-reductant assumption and non-rock clearance

Stage 0 is meant to strip non-rock species (volatiles, salts, sulfides, native metals, refractory trace) from the feedstock before the cleaned silicate oxide composition reaches `MeltState` and the downstream melt backends. The operator-facing simplification is that unlimited C, CO, and O₂ reductant/oxidant are available during bakeout. **As coded, that assumption does not drive thermodynamic clearance for most species**, and the sections below make that structural fact explicit rather than leaving it implied by the ledger.

### Mechanism: routing by name, not clearance by reagent

Stage-0 clearance is primarily **name-routing** — clean-by-fiat. Raw feedstock components are matched by normalized name strings against constant sets in the simulator core and dropped whole into terminal buckets (`terminal.offgas`, `terminal.stage0_salt_phase`, `terminal.stage0_chloride_salt_phase`, `terminal.stage0_sulfide_matte`, `terminal.drain_tap_material`, `terminal.slag`) with no reaction and no reagent debit. `MeltState` then receives only the oxides in the modeled oxide set — a structural filter, not a chemistry outcome.

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
| **Chloride (Cl, NaCl, KCl, halide)** | Name-routed to the chloride-salt phase | Yes (as separated salt) | NaCl/KCl **volatilize under mbar vacuum** and **re-condense on cold walls** | No | **Weak** — "cleared" overstates; the chloride is exactly the fouling failure mode the simulator otherwise tracks. |
| **Carbonate (MCO₃)** | Thermally driven `MCO3 → MO + CO2↑`; oxide → melt, CO₂ → offgas, undecomposed residual → salt phase | Partly (extent-limited) | `MCO3 → MO + CO2↑` at 400–900 °C; the metal oxide stays in the melt | No (thermal) | **Approximate** — cation returned to the melt, but decomposition is not fully speciated (see note). |
| **Native metals (Fe, Ni, Co, FeNi)** | Name-routed to the drain-tap material | Yes | Physically separate metal phase; drain-tap is real | No | **Yes** — native metal correctly bypasses the oxide melt. |
| **Organics / hydrocarbons / C / CH₄ / NH₃ / HCN** | Complete oxidation (or native-mixture pass-through) | Yes | Combustion/pyrolysis to CO₂ + H₂O + N₂, genuinely complete with the modeled O₂ | Yes (O₂) | **Yes** — the unlimited-O₂ assumption is genuinely modeled here; atom-restricted to C/H/O/N. |
| **H₂O and other volatiles** | Name-routed to offgas | Yes | Dehydration and vapor release (~100–700 °C) | No | **Yes** — trivially correct. |
| **Nitrate (NO₃, nitrate salts)** | Explicit unmodeled-nitrate marker; a declared nitrate **raises** | No (fails loud) | `MNO3 → MO + NOₓ↑` at 400–900 °C | n/a | **Gap, surfaced honestly** — unmodeled, but fails loud rather than silently mis-clearing. |
| **Refractory fluoride (CaF₂/MgF₂/fluorite)** | Explicit keys only → slag (bare `f` raises) | Yes (to slag) | CaF₂ (b.p. ~2530 °C) is **refractory and does not clear** | No | **Weak** — genuinely stubborn; it belongs in the rump/melt, not a removed phase. |
| **Alkali fluoride (NaF, generic fluoride)** | Explicit keys only → chloride/salt phase (bare `f` raises) | Yes (as separated salt) | NaF (b.p. ~1700 °C) can volatilize/re-condense; not a clean thermal clearance | No | **Weak** — routed to the same separated-salt/fouling-risk bucket as chlorides, not gasified. |
| **Refractory trace (ZrO₂, REE, ThO₂, UO₂)** | Name/prefix-routed to slag | Yes (to slag) | Refractory; do not vaporize | No | **Mostly** — a routing choice; REE also partition into the melt, so pre-melt removal is a simplification. |

The salt-volatilization diagnostic is **warn-tier, not a clearance certificate.** When it evaluates NaCl/KCl
vapor outside the Antoine row's valid temperature range, it still returns an escape/retained split for operator
visibility but labels the result out-of-range and extrapolated and emits a warning — the non-oxide policy is to
*warn*, since salts have no first-principles engine coverage, rather than fail-closed. That warning must never
be promoted into a certified Stage-0 clearance claim.

### Where the honest gaps are

Ranked by whether the error corrupts the melt composition handed to the silicate-equilibrium engines:

- **Cation-sulfate reduction is recipe-conditional and its product split is a choice.** A named cation sulfate (`CaSO4`/`MgSO4`/`FeSO4`) is routed to a dedicated cation-sulfate bucket, not the generic salt phase, so the Ca/Mg/Fe cation is not silently deleted. When a carbon-cleanup recipe is declared, that bucket is carbothermally reduced to either the oxide (returned to the melt) or the sulfide (routed to the sulfide matte), depending on the recipe's product mode. The limitations are that the oxide-versus-sulfide outcome is set by the recipe rather than by melt thermodynamics, and that without a declared cleanup recipe the sulfate is carried in its own bucket rather than reduced — so its cation neither reaches the melt nor is honestly reported as unreduced feedstock. The separate bulk-SO₃ surrogate path handles feedstocks (such as the Mars cases) that pre-express their sulfur as SO₃.
- **Carbonate decomposition is approximate, not fully speciated.** The metal oxide is returned to the melt and the CO₂ offgases, so the cation is **not** wholesale lost, but the decomposition extent is a temperature-driven approximation (with an SiO₂-availability gate for Na₂CO₃) rather than a full carbonate-melt equilibrium. Any undecomposed residual is carried to the salt phase. Affects carbonaceous (CI/CM/Ceres/comet) and Mars-carbonate feedstocks.
- **Fluoride clearance is asserted, not achievable, and the two fluoride families route differently.** Refractory alkaline-earth fluorides (`CaF2`/`MgF2`/`fluorite`) are routed to the slag phase, and alkali fluorides (`NaF`, generic `fluoride`) to the separated chloride/salt phase; a bare `f` key raises rather than being routed. Neither route is real thermal clearance: CaF₂ is refractory (b.p. ~2530 °C) and C/CO/O₂ at furnace temperature will not gasify it, so it should remain in the rump/melt rather than a removed phase; NaF can volatilize and re-condense on cold walls, the same fouling failure mode as the chlorides it is bucketed with. The HF-route defluorination (SiO₂ + steam) is out of scope.
- **Chloride "clearance" is overstated.** Perchlorate decomposition is real, but the chloride product is separated to a salt bucket rather than gasified. NaCl (b.p. ~1465 °C) and KCl (~1420 °C) volatilize under mbar vacuum at Stage-0 temperatures and re-condense on cold walls — the wall-fouling failure mode the simulator otherwise tracks — and the model still labels this "cleared."
- **Nitrates are unmodeled, and fail loud.** There is no nitrate reaction path; a declared nitrate component raises rather than being silently carried or mis-cleared. `MNO3 → MO + NOₓ↑` is easy chemistry at 400–900 °C, so this is a genuine coverage hole, but it is surfaced as an explicit error rather than a silent one. Impact is low for typical regolith feedstocks.

The structural point behind all of these: with unlimited reductant *assumed*, the code does not model that reductant *driving additional clearance reactions*. Reagent is consumed only in the gated families above; every other non-rock species is removed by name-routing. The reagent-supply assumption is a routing convenience, and the resistant species are exactly the cases where a real bake would push back against it.

## Iron redox in the melt (residual gaps)

Melt iron redox is not future work: it is live. The Fe vapor-pressure path consumes Kress–Carmichael 1991 ferric/ferrous splitting as the `a_FeO` authority, with the FeO activity assembled across the oxygen-fugacity range: a Holzheid/CALPHAD metal-saturated FeO activity below the pure-FeO iron–wüstite (IW) point, the Kress91 ferric/ferrous activity above IW(pure-FeO)+1, and a smooth one-log-unit blend between them. The consumed activity is clamped at the pure-FeO ceiling (`a_FeO ≤ 1.0`). Native-iron saturation is likewise computed, not stubbed: the extent of metal precipitation is derived from the ferrous FeO activity against the saturation activity implied by the prevailing fO₂ (`FeO(l) = Fe(metal) + ½ O₂`, Holzheid et al. 1997), and that authoritative extent sizes the ledger move that pulls Fe⁰ out of the melt. What remains limited is not whether these quantities are computed, but the reference axes they rest on:

- **The intrinsic-fO₂ seed carries ungrounded offsets.** The melt oxygen fugacity is seeded from a composition-to-fO₂ initializer that still applies ungrounded alkali and ferric `redox_offset` terms. These perturb the fO₂ *input* only; the Kress91 fO₂→ferric/ferrous mapping downstream of it is grounded, but the starting point it maps from is not fully anchored.
- **The metal-saturation switch axis is pure-FeO IW, not a self-consistent basaltic anchor.** The IW reference used to gate between the CALPHAD and Kress91 limbs is the pure-FeO iron–wüstite point. A basaltic melt with `a_FeO < 1` actually reaches metal saturation at a *lower* fO₂ than pure FeO does, so anchoring the switch at IW(pure-FeO) is a deliberate conservative bias — roughly 0.8–1.2 log units too oxidizing. Closing this requires a self-consistent melt-`a_FeO` saturation anchor rather than the pure-FeO tie-point, and that self-consistent anchor is deferred.
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
| **Inventory** (end-state, converged-hold) | Final yields at a depletion-verified hold; rump composition; stoichiometric O₂ total | Capped by conservation — but review (2026-08-19) forced precision here: ledger closure (≤5e-12 %) bounds the SUM of accounts, not the SPLIT between them, and "held long enough" is only meaningful when the hold criterion is inventory exhaustion itself, not wall-clock — a criterion the t-699 INSTRUMENT now reports per hour (`would_be_inventory_advance`) but which no campaign endpoint yet enforces: C0 still advances on temperature/wall-clock, and the `inventory_depletion` endpoint type is future, owner-gated work. Given a depletion-verified hold, endpoint errors are capped by inventory; the split between destinations still inherits selectivity error | Atom/mass closure (bounds the sum); depletion-verified hold criterion; speciation cross-checks (bound the split) |
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

**The threshold reformulation of the no-coating claim (owner-framed 2026-08-19), and
what it costs.** "Wall coating rate" is cardinal, but "no coating at all" can be a
*threshold* claim: if every wall segment upstream of the designated condenser stays above
the local dew point of every species present (P_sat(T_wall) > partial pressure), nothing
condenses — and dew points ride the vapor-pressure curves, which are among the
best-grounded data in the project. A conservative version needs only an UPPER BOUND on
partial pressure (total evolved inventory over volume suffices), so the claim inherits
threshold-class robustness rather than the full cardinal flux budget. Two honest limits:
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

<!-- BEGIN t-512 extract-store reproduction rollup -->

<a id="extract-store-reproduction-battery"></a>

### Extract-store single-species reproduction battery (t-512)

Generated from production priority-winner observations plus every KEMS
extract observation of type `psat_series` / `rate_series` /
`activity_coefficient` / `alpha` / `gibbs_table` / `transition_point`. Residuals
are the deliverable (doctrine: *Headline accuracy is the product*).
Engine refusals surface as typed skips; mismatches are FINDINGs —
tolerances are **not** widened to pass. Geometry: tools/motzfeldt.py available; geometry inversion is used only with complete numeric inputs, otherwise a typed capability/data gap is reported.

Observations: **570 total / 67 comparable / 503 skipped**. Comparable residual points: **105**; explicit gap records: **532**. Extrapolated-alpha FINDINGs: **21**.

- In-scope observations evaluated: **570**
- Comparable observations: **67**
- Skipped observations with typed reasons: **503**
- Species with FINDING (mismatch outside stated/default budget): **15**

| Species | Types | N pts | Match | Mismatch | Skip/gap | Max residual (dex) | Mean residual (dex) | Classification |
|---|---|---:|---:|---:|---:|---:|---:|---|
| Ag | activity_coefficient | 2 | 0 | 0 | 2 | — | — | engine-or-payload-skip |
| Al | activity_coefficient,rate_series,transition_point | 7 | 1 | 1 | 5 | — | — | FINDING-mismatch |
| Al2O | psat_series | 14 | 0 | 0 | 14 | — | — | engine-or-payload-skip |
| Al2O3 | alpha | 1 | 0 | 0 | 1 | — | — | engine-or-payload-skip |
| AlO | gibbs_table,psat_series | 6 | 0 | 0 | 6 | — | — | engine-or-payload-skip |
| As | activity_coefficient | 1 | 0 | 0 | 1 | — | — | engine-or-payload-skip |
| As4O6 | activity_coefficient,psat_series | 7 | 0 | 0 | 7 | — | — | engine-or-payload-skip |
| BaO | activity_coefficient | 1 | 0 | 0 | 1 | — | — | engine-or-payload-skip |
| Bi | activity_coefficient | 1 | 0 | 0 | 1 | — | — | engine-or-payload-skip |
| Br2 | transition_point | 1 | 0 | 0 | 1 | — | — | engine-or-payload-skip |
| CH4 | transition_point | 1 | 0 | 0 | 1 | — | — | engine-or-payload-skip |
| CO | transition_point | 2 | 0 | 0 | 2 | — | — | engine-or-payload-skip |
| CO2 | transition_point | 1 | 0 | 0 | 1 | — | — | engine-or-payload-skip |
| Ca | activity_coefficient,gibbs_table,rate_series,transition_point | 8 | 2 | 1 | 5 | — | — | FINDING-mismatch |
| CaO | gibbs_table,rate_series | 1 | 0 | 0 | 1 | — | — | engine-or-payload-skip |
| Cd | activity_coefficient | 2 | 0 | 0 | 2 | — | — | engine-or-payload-skip |
| Cl2 | transition_point | 2 | 0 | 1 | 1 | — | — | FINDING-mismatch |
| Co | activity_coefficient | 1 | 0 | 0 | 1 | — | — | engine-or-payload-skip |
| Cr | activity_coefficient,alpha,gibbs_table,psat_series,rate_series,transition_point | 11 | 0 | 1 | 10 | — | — | FINDING-mismatch |
| Cs2O | activity_coefficient | 1 | 0 | 0 | 1 | — | — | engine-or-payload-skip |
| Cu | activity_coefficient,alpha,gibbs_table,rate_series | 8 | 0 | 0 | 8 | — | — | engine-or-payload-skip |
| Eu_metal_and_EuO | activity_coefficient | 2 | 0 | 0 | 2 | — | — | engine-or-payload-skip |
| Fe | activity_coefficient,alpha,gibbs_table,rate_series,transition_point | 38 | 0 | 15 | 23 | 1.1 | 1.07 | FINDING-mismatch |
| FeO | activity_coefficient | 2 | 0 | 0 | 2 | — | — | engine-or-payload-skip |
| Ga | activity_coefficient,gibbs_table | 2 | 0 | 0 | 2 | — | — | engine-or-payload-skip |
| Ga2O | activity_coefficient | 1 | 0 | 0 | 1 | — | — | engine-or-payload-skip |
| Gd | rate_series | 1 | 0 | 0 | 1 | — | — | engine-or-payload-skip |
| Ge | activity_coefficient,gibbs_table | 3 | 0 | 0 | 3 | — | — | engine-or-payload-skip |
| GeO2 | activity_coefficient | 1 | 0 | 0 | 1 | — | — | engine-or-payload-skip |
| H2O | transition_point | 3 | 0 | 0 | 3 | — | — | engine-or-payload-skip |
| H2S | transition_point | 3 | 0 | 0 | 3 | — | — | engine-or-payload-skip |
| HCHO | transition_point | 3 | 0 | 0 | 3 | — | — | engine-or-payload-skip |
| HCl | transition_point | 1 | 0 | 0 | 1 | — | — | engine-or-payload-skip |
| HF | transition_point | 2 | 1 | 0 | 1 | — | — | within-budget |
| In | activity_coefficient | 1 | 0 | 0 | 1 | — | — | engine-or-payload-skip |
| K | activity_coefficient,alpha,gibbs_table,psat_series,rate_series,transition_point | 17 | 2 | 4 | 11 | 0.886 | 0.597 | FINDING-mismatch |
| K2O | activity_coefficient | 174 | 0 | 0 | 174 | — | — | engine-or-payload-skip |
| KCl | transition_point | 2 | 1 | 0 | 1 | — | — | within-budget |
| La | rate_series | 1 | 0 | 0 | 1 | — | — | engine-or-payload-skip |
| Li | activity_coefficient,gibbs_table | 3 | 0 | 0 | 3 | — | — | engine-or-payload-skip |
| Li2O | activity_coefficient | 1 | 0 | 0 | 1 | — | — | engine-or-payload-skip |
| Mg | activity_coefficient,alpha,psat_series,rate_series,transition_point | 42 | 8 | 13 | 21 | 0.52 | 0.164 | FINDING-mismatch |
| MgCl2 | transition_point | 1 | 0 | 0 | 1 | — | — | engine-or-payload-skip |
| MgO | alpha,gibbs_table,psat_series,rate_series | 3 | 0 | 0 | 3 | — | — | engine-or-payload-skip |
| Mn | activity_coefficient,alpha,gibbs_table,psat_series,rate_series,transition_point | 13 | 1 | 0 | 12 | — | — | within-budget |
| Mo | activity_coefficient,gibbs_table | 2 | 0 | 0 | 2 | — | — | engine-or-payload-skip |
| MoO2 | activity_coefficient | 1 | 0 | 0 | 1 | — | — | engine-or-payload-skip |
| MoO3 | activity_coefficient | 1 | 0 | 0 | 1 | — | — | engine-or-payload-skip |
| N2 | transition_point | 3 | 0 | 0 | 3 | — | — | engine-or-payload-skip |
| NH3 | transition_point | 1 | 0 | 0 | 1 | — | — | engine-or-payload-skip |
| NO2 | transition_point | 2 | 0 | 1 | 1 | — | — | FINDING-mismatch |
| Na | activity_coefficient,alpha,gibbs_table,psat_series,rate_series,transition_point | 32 | 0 | 1 | 31 | — | — | FINDING-mismatch |
| Na2O | activity_coefficient | 28 | 0 | 1 | 27 | 0.797 | 0.797 | FINDING-mismatch |
| Na2SO4 | transition_point | 1 | 0 | 0 | 1 | — | — | engine-or-payload-skip |
| NaCl | transition_point | 2 | 1 | 0 | 1 | — | — | within-budget |
| NaF | psat_series,transition_point | 3 | 0 | 0 | 3 | — | — | engine-or-payload-skip |
| Ni | activity_coefficient | 1 | 0 | 0 | 1 | — | — | engine-or-payload-skip |
| O | psat_series | 13 | 0 | 0 | 13 | — | — | engine-or-payload-skip |
| O2 | psat_series,rate_series,transition_point | 13 | 0 | 0 | 13 | — | — | engine-or-payload-skip |
| P | activity_coefficient | 1 | 0 | 0 | 1 | — | — | engine-or-payload-skip |
| P4O10 | activity_coefficient | 1 | 0 | 0 | 1 | — | — | engine-or-payload-skip |
| Pb | activity_coefficient,gibbs_table,transition_point | 5 | 0 | 0 | 5 | — | — | engine-or-payload-skip |
| Rb | activity_coefficient,gibbs_table | 2 | 0 | 0 | 2 | — | — | engine-or-payload-skip |
| Rb2O | activity_coefficient | 1 | 0 | 0 | 1 | — | — | engine-or-payload-skip |
| S | alpha,rate_series | 4 | 0 | 0 | 4 | — | — | engine-or-payload-skip |
| S2 | psat_series | 1 | 0 | 0 | 1 | — | — | engine-or-payload-skip |
| SO2 | transition_point | 2 | 0 | 0 | 2 | — | — | engine-or-payload-skip |
| SO3 | psat_series | 1 | 0 | 0 | 1 | — | — | engine-or-payload-skip |
| Sb4O6 | activity_coefficient | 1 | 0 | 0 | 1 | — | — | engine-or-payload-skip |
| Sc | rate_series | 1 | 0 | 0 | 1 | — | — | engine-or-payload-skip |
| Se_n_ladder | psat_series | 3 | 0 | 0 | 3 | — | — | engine-or-payload-skip |
| Si | alpha,rate_series,transition_point | 5 | 0 | 1 | 4 | — | — | FINDING-mismatch |
| SiO | activity_coefficient,alpha,rate_series | 43 | 7 | 13 | 23 | 0.41 | 0.233 | FINDING-mismatch |
| SiO2 | activity_coefficient,alpha,gibbs_table | 29 | 10 | 15 | 4 | 4.75 | 1.2 | FINDING-mismatch |
| Sn | activity_coefficient,alpha,rate_series | 6 | 0 | 0 | 6 | — | — | engine-or-payload-skip |
| SrO | activity_coefficient | 1 | 0 | 0 | 1 | — | — | engine-or-payload-skip |
| Ti | psat_series,rate_series,transition_point | 5 | 0 | 1 | 4 | — | — | FINDING-mismatch |
| TiO | rate_series | 1 | 0 | 0 | 1 | — | — | engine-or-payload-skip |
| TiO2 | activity_coefficient,rate_series | 2 | 0 | 0 | 2 | — | — | engine-or-payload-skip |
| V | activity_coefficient,rate_series | 4 | 0 | 0 | 4 | — | — | engine-or-payload-skip |
| VO_VO2 | activity_coefficient,psat_series | 3 | 0 | 2 | 1 | 1.11 | 1.04 | FINDING-mismatch |
| WO2 | activity_coefficient | 1 | 0 | 0 | 1 | — | — | engine-or-payload-skip |
| WO3 | activity_coefficient | 2 | 0 | 0 | 2 | — | — | engine-or-payload-skip |
| Yb | rate_series | 1 | 0 | 0 | 1 | — | — | engine-or-payload-skip |
| Yb_metal_and_YbO | activity_coefficient,psat_series | 5 | 0 | 0 | 5 | — | — | engine-or-payload-skip |
| Zn | activity_coefficient,gibbs_table | 3 | 0 | 0 | 3 | — | — | engine-or-payload-skip |
| Zr | rate_series | 1 | 0 | 0 | 1 | — | — | engine-or-payload-skip |

**Typed observation skips (roadmap, one primary reason per skipped observation):**

- `typed-refusal:analytical_upper_bound_not_measurement`: **4**
- `typed-refusal:form_unresolved`: **9**
- `typed-refusal:gibbs_table_not_runtime_observable`: **29**
- `typed-refusal:missing_capability:documented_melt_activity_coefficient:FeO`: **2**
- `typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O`: **173**
- `typed-refusal:missing_capability:documented_melt_activity_coefficient:Na2O`: **23**
- `typed-refusal:missing_capability:gas_speciation_ladder`: **14**
- `typed-refusal:missing_capability:melt_activity_gamma:AgO0.5`: **1**
- `typed-refusal:missing_capability:melt_activity_gamma:AsO1.5`: **1**
- `typed-refusal:missing_capability:melt_activity_gamma:BiO1.5`: **1**
- `typed-refusal:missing_capability:melt_activity_gamma:CdO`: **1**
- `typed-refusal:missing_capability:melt_activity_gamma:CoO`: **1**
- `typed-refusal:missing_capability:melt_activity_gamma:CrO`: **1**
- `typed-refusal:missing_capability:melt_activity_gamma:CuO0.5`: **2**
- `typed-refusal:missing_capability:melt_activity_gamma:FeO`: **1**
- `typed-refusal:missing_capability:melt_activity_gamma:GeO`: **1**
- `typed-refusal:missing_capability:melt_activity_gamma:GeO2`: **2**
- `typed-refusal:missing_capability:melt_activity_gamma:InO1.5`: **1**
- `typed-refusal:missing_capability:melt_activity_gamma:LiO0.5`: **1**
- `typed-refusal:missing_capability:melt_activity_gamma:MoO2`: **1**
- `typed-refusal:missing_capability:melt_activity_gamma:MoO3`: **1**
- `typed-refusal:missing_capability:melt_activity_gamma:NiO`: **1**
- `typed-refusal:missing_capability:melt_activity_gamma:PbO`: **2**
- `typed-refusal:missing_capability:melt_activity_gamma:RbO0.5`: **1**
- `typed-refusal:missing_capability:melt_activity_gamma:SnO`: **1**
- `typed-refusal:missing_capability:melt_activity_gamma:V2O3`: **2**
- `typed-refusal:missing_capability:melt_activity_gamma:WO2`: **1**
- `typed-refusal:missing_capability:melt_activity_gamma:WO3`: **1**
- `typed-refusal:missing_capability:melt_activity_gamma:ZnO`: **2**
- `typed-refusal:missing_condition:melt_composition`: **1**
- `typed-refusal:missing_condition:melt_density_to_convert_specific_evaporation_constant`: **9**
- `typed-refusal:missing_condition:pO2_boundary`: **8**
- `typed-refusal:missing_numeric_activity`: **14**
- `typed-refusal:missing_numeric_species_rate`: **13**
- `typed-refusal:missing_numeric_species_rate:geometry_without_measured_flux`: **19**
- `typed-refusal:model_output_not_measurement`: **2**
- `typed-refusal:no_engine_melting_point_model`: **27**
- `typed-refusal:no_engine_solid_solid_transition_model`: **1**
- `typed-refusal:no_engine_triple_point_model`: **14**
- `typed-refusal:no_pure_component_saturation_curve`: **7**
- `typed-refusal:no_usable_rate_series_payload`: **2**
- `typed-refusal:not_comparable_condensed_form:crystalline`: **7**
- `typed-refusal:not_comparable_condensed_form:crystalline:straddles_transition`: **1**
- `typed-refusal:not_comparable_condensed_form:partially_molten`: **2**
- `typed-refusal:not_comparable_condensed_form:partially_molten:straddles_transition`: **2**
- `typed-refusal:not_comparable_system_class:molten_metal`: **12**
- `typed-refusal:not_comparable_system_class:pure_element_condensed`: **1**
- `typed-refusal:not_comparable_system_class:pure_element_condensed+not_comparable_condensed_form:crystalline`: **5**
- `typed-refusal:not_comparable_system_class:solid_film_growth+not_comparable_condensed_form:glass_amorphous`: **3**
- `typed-refusal:pointer_or_anchor_without_numeric_points`: **11**
- `typed-refusal:pure_psat_out_of_certified_range`: **4**
- `typed-refusal:pure_solid_thermochemistry_not_melt_activity`: **2**
- `typed-refusal:self_agreement_excluded`: **9**
- `typed-refusal:thermodynamic_model_parameter_not_activity_measurement`: **2**
- `typed-refusal:unknown_transition_property_kind:missing`: **1**
- `typed-refusal:unsupported_observable:clausing_factor_not_species_rate`: **3**
- `typed-refusal:unsupported_observable:deposit_composition_not_species_rate`: **2**
- `typed-refusal:unsupported_observable:figure_only_not_digitized`: **11**
- `typed-refusal:unsupported_observable:logKstar_not_activity_coefficient`: **17**
- `typed-refusal:unsupported_observable:methodology_guidance_not_observable`: **1**
- `typed-refusal:unsupported_observable:ordering_claim_unparsed`: **2**
- `typed-refusal:unsupported_observable:pure_oxide_speciation_index`: **1**
- `typed-refusal:unsupported_observable:species_detected_absolute_P_not_tabulated`: **2**
- `typed-refusal:unsupported_observable:species_not_reported_among_detected`: **1**
- `typed-refusal:unsupported_observable:vapour_species_map_no_numeric_pressures`: **3**

**Coverage by observation type:**

| Type | Observations | Comparable | Skipped | Comparable points | Gap points | Typed skip reasons |
|---|---:|---:|---:|---:|---:|---|
| activity_coefficient | 311 | 27 | 284 | 27 | 284 | `typed-refusal:form_unresolved` ×2; `typed-refusal:missing_capability:documented_melt_activity_coefficient:FeO` ×2; `typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O` ×173; `typed-refusal:missing_capability:documented_melt_activity_coefficient:Na2O` ×23; `typed-refusal:missing_capability:gas_speciation_ladder` ×14; `typed-refusal:missing_capability:melt_activity_gamma:AgO0.5` ×1; `typed-refusal:missing_capability:melt_activity_gamma:AsO1.5` ×1; `typed-refusal:missing_capability:melt_activity_gamma:BiO1.5` ×1; `typed-refusal:missing_capability:melt_activity_gamma:CdO` ×1; `typed-refusal:missing_capability:melt_activity_gamma:CoO` ×1; `typed-refusal:missing_capability:melt_activity_gamma:CrO` ×1; `typed-refusal:missing_capability:melt_activity_gamma:CuO0.5` ×2; `typed-refusal:missing_capability:melt_activity_gamma:FeO` ×1; `typed-refusal:missing_capability:melt_activity_gamma:GeO` ×1; `typed-refusal:missing_capability:melt_activity_gamma:GeO2` ×2; `typed-refusal:missing_capability:melt_activity_gamma:InO1.5` ×1; `typed-refusal:missing_capability:melt_activity_gamma:LiO0.5` ×1; `typed-refusal:missing_capability:melt_activity_gamma:MoO2` ×1; `typed-refusal:missing_capability:melt_activity_gamma:MoO3` ×1; `typed-refusal:missing_capability:melt_activity_gamma:NiO` ×1; `typed-refusal:missing_capability:melt_activity_gamma:PbO` ×2; `typed-refusal:missing_capability:melt_activity_gamma:RbO0.5` ×1; `typed-refusal:missing_capability:melt_activity_gamma:SnO` ×1; `typed-refusal:missing_capability:melt_activity_gamma:V2O3` ×2; `typed-refusal:missing_capability:melt_activity_gamma:WO2` ×1; `typed-refusal:missing_capability:melt_activity_gamma:WO3` ×1; `typed-refusal:missing_capability:melt_activity_gamma:ZnO` ×2; `typed-refusal:missing_numeric_activity` ×14; `typed-refusal:model_output_not_measurement` ×1; `typed-refusal:self_agreement_excluded` ×9; `typed-refusal:unsupported_observable:figure_only_not_digitized` ×2; `typed-refusal:unsupported_observable:logKstar_not_activity_coefficient` ×17 |
| alpha | 63 | 17 | 46 | 45 | 51 | `typed-refusal:analytical_upper_bound_not_measurement` ×4; `typed-refusal:form_unresolved` ×7; `typed-refusal:no_usable_rate_series_payload` ×2; `typed-refusal:not_comparable_condensed_form:crystalline` ×7; `typed-refusal:not_comparable_condensed_form:crystalline:straddles_transition` ×1; `typed-refusal:not_comparable_condensed_form:partially_molten` ×2; `typed-refusal:not_comparable_condensed_form:partially_molten:straddles_transition` ×2; `typed-refusal:not_comparable_system_class:molten_metal` ×12; `typed-refusal:not_comparable_system_class:pure_element_condensed` ×1; `typed-refusal:not_comparable_system_class:pure_element_condensed+not_comparable_condensed_form:crystalline` ×5; `typed-refusal:not_comparable_system_class:solid_film_growth+not_comparable_condensed_form:glass_amorphous` ×3 |
| gibbs_table | 33 | 0 | 33 | 0 | 0 | `typed-refusal:gibbs_table_not_runtime_observable` ×29; `typed-refusal:pure_solid_thermochemistry_not_melt_activity` ×2; `typed-refusal:thermodynamic_model_parameter_not_activity_measurement` ×2 |
| psat_series | 23 | 1 | 22 | 2 | 76 | `typed-refusal:missing_condition:pO2_boundary` ×7; `typed-refusal:pointer_or_anchor_without_numeric_points` ×11; `typed-refusal:pure_psat_out_of_certified_range` ×4 |
| rate_series | 75 | 7 | 68 | 16 | 71 | `typed-refusal:missing_condition:melt_composition` ×1; `typed-refusal:missing_condition:melt_density_to_convert_specific_evaporation_constant` ×9; `typed-refusal:missing_condition:pO2_boundary` ×1; `typed-refusal:missing_numeric_species_rate` ×13; `typed-refusal:missing_numeric_species_rate:geometry_without_measured_flux` ×19; `typed-refusal:model_output_not_measurement` ×1; `typed-refusal:unsupported_observable:clausing_factor_not_species_rate` ×3; `typed-refusal:unsupported_observable:deposit_composition_not_species_rate` ×2; `typed-refusal:unsupported_observable:figure_only_not_digitized` ×9; `typed-refusal:unsupported_observable:methodology_guidance_not_observable` ×1; `typed-refusal:unsupported_observable:ordering_claim_unparsed` ×2; `typed-refusal:unsupported_observable:pure_oxide_speciation_index` ×1; `typed-refusal:unsupported_observable:species_detected_absolute_P_not_tabulated` ×2; `typed-refusal:unsupported_observable:species_not_reported_among_detected` ×1; `typed-refusal:unsupported_observable:vapour_species_map_no_numeric_pressures` ×3 |
| transition_point | 65 | 15 | 50 | 15 | 50 | `typed-refusal:no_engine_melting_point_model` ×27; `typed-refusal:no_engine_solid_solid_transition_model` ×1; `typed-refusal:no_engine_triple_point_model` ×14; `typed-refusal:no_pure_component_saturation_curve` ×7; `typed-refusal:unknown_transition_property_kind:missing` ×1 |

**Coverage by comparison family:**

| Comparison family | Observations | Comparable | Skipped | Comparable points | Gap points | Typed skip reasons |
|---|---:|---:|---:|---:|---:|---|
| activity_coefficient | 285 | 27 | 258 | 27 | 258 | `typed-refusal:form_unresolved` ×2; `typed-refusal:missing_capability:documented_melt_activity_coefficient:FeO` ×2; `typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O` ×173; `typed-refusal:missing_capability:documented_melt_activity_coefficient:Na2O` ×23; `typed-refusal:missing_capability:melt_activity_gamma:AgO0.5` ×1; `typed-refusal:missing_capability:melt_activity_gamma:AsO1.5` ×1; `typed-refusal:missing_capability:melt_activity_gamma:BiO1.5` ×1; `typed-refusal:missing_capability:melt_activity_gamma:CdO` ×1; `typed-refusal:missing_capability:melt_activity_gamma:CoO` ×1; `typed-refusal:missing_capability:melt_activity_gamma:CrO` ×1; `typed-refusal:missing_capability:melt_activity_gamma:CuO0.5` ×2; `typed-refusal:missing_capability:melt_activity_gamma:FeO` ×1; `typed-refusal:missing_capability:melt_activity_gamma:GeO` ×1; `typed-refusal:missing_capability:melt_activity_gamma:GeO2` ×2; `typed-refusal:missing_capability:melt_activity_gamma:InO1.5` ×1; `typed-refusal:missing_capability:melt_activity_gamma:LiO0.5` ×1; `typed-refusal:missing_capability:melt_activity_gamma:MoO2` ×1; `typed-refusal:missing_capability:melt_activity_gamma:MoO3` ×1; `typed-refusal:missing_capability:melt_activity_gamma:NiO` ×1; `typed-refusal:missing_capability:melt_activity_gamma:PbO` ×2; `typed-refusal:missing_capability:melt_activity_gamma:RbO0.5` ×1; `typed-refusal:missing_capability:melt_activity_gamma:SnO` ×1; `typed-refusal:missing_capability:melt_activity_gamma:V2O3` ×2; `typed-refusal:missing_capability:melt_activity_gamma:WO2` ×1; `typed-refusal:missing_capability:melt_activity_gamma:WO3` ×1; `typed-refusal:missing_capability:melt_activity_gamma:ZnO` ×2; `typed-refusal:missing_numeric_activity` ×14; `typed-refusal:unsupported_observable:logKstar_not_activity_coefficient` ×17 |
| activity_self_agreement | 9 | 0 | 9 | 0 | 9 | `typed-refusal:self_agreement_excluded` ×9 |
| alpha | 63 | 17 | 46 | 45 | 51 | `typed-refusal:analytical_upper_bound_not_measurement` ×4; `typed-refusal:form_unresolved` ×7; `typed-refusal:no_usable_rate_series_payload` ×2; `typed-refusal:not_comparable_condensed_form:crystalline` ×7; `typed-refusal:not_comparable_condensed_form:crystalline:straddles_transition` ×1; `typed-refusal:not_comparable_condensed_form:partially_molten` ×2; `typed-refusal:not_comparable_condensed_form:partially_molten:straddles_transition` ×2; `typed-refusal:not_comparable_system_class:molten_metal` ×12; `typed-refusal:not_comparable_system_class:pure_element_condensed` ×1; `typed-refusal:not_comparable_system_class:pure_element_condensed+not_comparable_condensed_form:crystalline` ×5; `typed-refusal:not_comparable_system_class:solid_film_growth+not_comparable_condensed_form:glass_amorphous` ×3 |
| alpha_in_legacy_rate_series | 3 | 3 | 0 | 12 | 0 | — |
| gibbs_table | 33 | 0 | 33 | 0 | 0 | `typed-refusal:gibbs_table_not_runtime_observable` ×29; `typed-refusal:pure_solid_thermochemistry_not_melt_activity` ×2; `typed-refusal:thermodynamic_model_parameter_not_activity_measurement` ×2 |
| ordering_activity | 17 | 0 | 17 | 0 | 17 | `typed-refusal:missing_capability:gas_speciation_ladder` ×14; `typed-refusal:model_output_not_measurement` ×1; `typed-refusal:unsupported_observable:figure_only_not_digitized` ×2 |
| ordering_bound | 26 | 4 | 22 | 4 | 22 | `typed-refusal:model_output_not_measurement` ×1; `typed-refusal:unsupported_observable:deposit_composition_not_species_rate` ×2; `typed-refusal:unsupported_observable:figure_only_not_digitized` ×9; `typed-refusal:unsupported_observable:methodology_guidance_not_observable` ×1; `typed-refusal:unsupported_observable:ordering_claim_unparsed` ×2; `typed-refusal:unsupported_observable:pure_oxide_speciation_index` ×1; `typed-refusal:unsupported_observable:species_detected_absolute_P_not_tabulated` ×2; `typed-refusal:unsupported_observable:species_not_reported_among_detected` ×1; `typed-refusal:unsupported_observable:vapour_species_map_no_numeric_pressures` ×3 |
| psat_series | 23 | 1 | 22 | 2 | 76 | `typed-refusal:missing_condition:pO2_boundary` ×7; `typed-refusal:pointer_or_anchor_without_numeric_points` ×11; `typed-refusal:pure_psat_out_of_certified_range` ×4 |
| rate_hkl | 45 | 0 | 45 | 0 | 48 | `typed-refusal:missing_condition:melt_density_to_convert_specific_evaporation_constant` ×9; `typed-refusal:missing_condition:pO2_boundary` ×1; `typed-refusal:missing_numeric_species_rate` ×13; `typed-refusal:missing_numeric_species_rate:geometry_without_measured_flux` ×19; `typed-refusal:unsupported_observable:clausing_factor_not_species_rate` ×3 |
| relative_volatility | 1 | 0 | 1 | 0 | 1 | `typed-refusal:missing_condition:melt_composition` ×1 |
| transition_point | 65 | 15 | 50 | 15 | 50 | `typed-refusal:no_engine_melting_point_model` ×27; `typed-refusal:no_engine_solid_solid_transition_model` ×1; `typed-refusal:no_engine_triple_point_model` ×14; `typed-refusal:no_pure_component_saturation_curve` ×7; `typed-refusal:unknown_transition_property_kind:missing` ×1 |

**Coverage by species:**

| Species | Observations | Comparable | Skipped | Comparable points | Gap points | Typed skip reasons |
|---|---:|---:|---:|---:|---:|---|
| Ag | 2 | 0 | 2 | 0 | 2 | `typed-refusal:missing_capability:melt_activity_gamma:AgO0.5` ×1; `typed-refusal:unsupported_observable:logKstar_not_activity_coefficient` ×1 |
| Al | 7 | 2 | 5 | 2 | 5 | `typed-refusal:no_engine_melting_point_model` ×1; `typed-refusal:no_engine_triple_point_model` ×1; `typed-refusal:self_agreement_excluded` ×1; `typed-refusal:unsupported_observable:figure_only_not_digitized` ×1; `typed-refusal:unsupported_observable:species_detected_absolute_P_not_tabulated` ×1 |
| Al2O | 1 | 0 | 1 | 0 | 14 | `typed-refusal:missing_condition:pO2_boundary` ×1 |
| Al2O3 | 1 | 0 | 1 | 0 | 1 | `typed-refusal:not_comparable_condensed_form:crystalline:straddles_transition` ×1 |
| AlO | 2 | 0 | 2 | 0 | 6 | `typed-refusal:gibbs_table_not_runtime_observable` ×1; `typed-refusal:missing_condition:pO2_boundary` ×1 |
| As | 1 | 0 | 1 | 0 | 1 | `typed-refusal:missing_capability:melt_activity_gamma:AsO1.5` ×1 |
| As4O6 | 3 | 0 | 3 | 0 | 7 | `typed-refusal:missing_capability:gas_speciation_ladder` ×1; `typed-refusal:pure_psat_out_of_certified_range` ×2 |
| BaO | 1 | 0 | 1 | 0 | 1 | `typed-refusal:missing_capability:gas_speciation_ladder` ×1 |
| Bi | 1 | 0 | 1 | 0 | 1 | `typed-refusal:missing_capability:melt_activity_gamma:BiO1.5` ×1 |
| Br2 | 1 | 0 | 1 | 0 | 1 | `typed-refusal:no_engine_melting_point_model` ×1 |
| CH4 | 1 | 0 | 1 | 0 | 1 | `typed-refusal:no_engine_melting_point_model` ×1 |
| CO | 2 | 0 | 2 | 0 | 2 | `typed-refusal:no_engine_triple_point_model` ×1; `typed-refusal:no_pure_component_saturation_curve` ×1 |
| CO2 | 1 | 0 | 1 | 0 | 1 | `typed-refusal:no_engine_triple_point_model` ×1 |
| Ca | 9 | 3 | 6 | 3 | 5 | `typed-refusal:gibbs_table_not_runtime_observable` ×1; `typed-refusal:missing_condition:melt_composition` ×1; `typed-refusal:no_engine_melting_point_model` ×1; `typed-refusal:no_engine_triple_point_model` ×1; `typed-refusal:self_agreement_excluded` ×1; `typed-refusal:unsupported_observable:figure_only_not_digitized` ×1 |
| CaO | 2 | 0 | 2 | 0 | 1 | `typed-refusal:missing_numeric_species_rate:geometry_without_measured_flux` ×1; `typed-refusal:pure_solid_thermochemistry_not_melt_activity` ×1 |
| Cd | 2 | 0 | 2 | 0 | 2 | `typed-refusal:missing_capability:melt_activity_gamma:CdO` ×1; `typed-refusal:unsupported_observable:logKstar_not_activity_coefficient` ×1 |
| Cl2 | 2 | 1 | 1 | 1 | 1 | `typed-refusal:no_engine_triple_point_model` ×1 |
| Co | 1 | 0 | 1 | 0 | 1 | `typed-refusal:missing_capability:melt_activity_gamma:CoO` ×1 |
| Cr | 11 | 1 | 10 | 1 | 10 | `typed-refusal:gibbs_table_not_runtime_observable` ×1; `typed-refusal:missing_capability:melt_activity_gamma:CrO` ×1; `typed-refusal:missing_condition:melt_density_to_convert_specific_evaporation_constant` ×1; `typed-refusal:missing_condition:pO2_boundary` ×1; `typed-refusal:no_engine_melting_point_model` ×1; `typed-refusal:not_comparable_system_class:molten_metal` ×1; `typed-refusal:not_comparable_system_class:pure_element_condensed+not_comparable_condensed_form:crystalline` ×2; `typed-refusal:self_agreement_excluded` ×1; `typed-refusal:unsupported_observable:logKstar_not_activity_coefficient` ×1 |
| Cs2O | 1 | 0 | 1 | 0 | 1 | `typed-refusal:missing_capability:gas_speciation_ladder` ×1 |
| Cu | 9 | 0 | 9 | 0 | 8 | `typed-refusal:gibbs_table_not_runtime_observable` ×1; `typed-refusal:missing_capability:melt_activity_gamma:CuO0.5` ×2; `typed-refusal:missing_condition:melt_density_to_convert_specific_evaporation_constant` ×2; `typed-refusal:not_comparable_system_class:molten_metal` ×3; `typed-refusal:unsupported_observable:logKstar_not_activity_coefficient` ×1 |
| Eu_metal_and_EuO | 2 | 0 | 2 | 0 | 2 | `typed-refusal:missing_capability:gas_speciation_ladder` ×2 |
| Fe | 32 | 6 | 26 | 15 | 23 | `typed-refusal:gibbs_table_not_runtime_observable` ×3; `typed-refusal:missing_capability:documented_melt_activity_coefficient:FeO` ×2; `typed-refusal:missing_capability:melt_activity_gamma:FeO` ×1; `typed-refusal:missing_numeric_activity` ×2; `typed-refusal:missing_numeric_species_rate` ×2; `typed-refusal:missing_numeric_species_rate:geometry_without_measured_flux` ×3; `typed-refusal:no_engine_melting_point_model` ×1; `typed-refusal:not_comparable_condensed_form:crystalline` ×3; `typed-refusal:not_comparable_system_class:pure_element_condensed+not_comparable_condensed_form:crystalline` ×3; `typed-refusal:unsupported_observable:clausing_factor_not_species_rate` ×3; `typed-refusal:unsupported_observable:figure_only_not_digitized` ×2; `typed-refusal:unsupported_observable:species_detected_absolute_P_not_tabulated` ×1 |
| FeO | 2 | 0 | 2 | 0 | 2 | `typed-refusal:missing_numeric_activity` ×1; `typed-refusal:unsupported_observable:figure_only_not_digitized` ×1 |
| Ga | 3 | 0 | 3 | 0 | 2 | `typed-refusal:gibbs_table_not_runtime_observable` ×1; `typed-refusal:unsupported_observable:logKstar_not_activity_coefficient` ×2 |
| Ga2O | 1 | 0 | 1 | 0 | 1 | `typed-refusal:missing_capability:gas_speciation_ladder` ×1 |
| Gd | 1 | 0 | 1 | 0 | 1 | `typed-refusal:missing_numeric_species_rate:geometry_without_measured_flux` ×1 |
| Ge | 4 | 0 | 4 | 0 | 3 | `typed-refusal:gibbs_table_not_runtime_observable` ×1; `typed-refusal:missing_capability:melt_activity_gamma:GeO` ×1; `typed-refusal:missing_capability:melt_activity_gamma:GeO2` ×1; `typed-refusal:unsupported_observable:logKstar_not_activity_coefficient` ×1 |
| GeO2 | 1 | 0 | 1 | 0 | 1 | `typed-refusal:missing_capability:melt_activity_gamma:GeO2` ×1 |
| H2O | 3 | 0 | 3 | 0 | 3 | `typed-refusal:no_engine_melting_point_model` ×1; `typed-refusal:no_engine_triple_point_model` ×1; `typed-refusal:no_pure_component_saturation_curve` ×1 |
| H2S | 3 | 0 | 3 | 0 | 3 | `typed-refusal:no_engine_melting_point_model` ×1; `typed-refusal:no_engine_triple_point_model` ×1; `typed-refusal:no_pure_component_saturation_curve` ×1 |
| HCHO | 3 | 0 | 3 | 0 | 3 | `typed-refusal:no_engine_melting_point_model` ×1; `typed-refusal:no_engine_triple_point_model` ×1; `typed-refusal:no_pure_component_saturation_curve` ×1 |
| HCl | 1 | 0 | 1 | 0 | 1 | `typed-refusal:no_engine_melting_point_model` ×1 |
| HF | 2 | 1 | 1 | 1 | 1 | `typed-refusal:no_engine_melting_point_model` ×1 |
| In | 1 | 0 | 1 | 0 | 1 | `typed-refusal:missing_capability:melt_activity_gamma:InO1.5` ×1 |
| K | 18 | 5 | 13 | 6 | 11 | `typed-refusal:form_unresolved` ×4; `typed-refusal:gibbs_table_not_runtime_observable` ×2; `typed-refusal:no_engine_melting_point_model` ×1; `typed-refusal:no_engine_triple_point_model` ×1; `typed-refusal:pointer_or_anchor_without_numeric_points` ×2; `typed-refusal:unsupported_observable:deposit_composition_not_species_rate` ×1; `typed-refusal:unsupported_observable:logKstar_not_activity_coefficient` ×1; `typed-refusal:unsupported_observable:ordering_claim_unparsed` ×1 |
| K2O | 174 | 0 | 174 | 0 | 174 | `typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O` ×173; `typed-refusal:missing_numeric_activity` ×1 |
| KCl | 2 | 1 | 1 | 1 | 1 | `typed-refusal:no_engine_melting_point_model` ×1 |
| La | 1 | 0 | 1 | 0 | 1 | `typed-refusal:missing_numeric_species_rate:geometry_without_measured_flux` ×1 |
| Li | 4 | 0 | 4 | 0 | 3 | `typed-refusal:gibbs_table_not_runtime_observable` ×1; `typed-refusal:missing_capability:melt_activity_gamma:LiO0.5` ×1; `typed-refusal:unsupported_observable:logKstar_not_activity_coefficient` ×2 |
| Li2O | 1 | 0 | 1 | 0 | 1 | `typed-refusal:missing_capability:gas_speciation_ladder` ×1 |
| Mg | 19 | 8 | 11 | 21 | 21 | `typed-refusal:missing_condition:pO2_boundary` ×2; `typed-refusal:missing_numeric_activity` ×1; `typed-refusal:missing_numeric_species_rate` ×2; `typed-refusal:missing_numeric_species_rate:geometry_without_measured_flux` ×1; `typed-refusal:no_engine_melting_point_model` ×1; `typed-refusal:no_engine_triple_point_model` ×1; `typed-refusal:not_comparable_condensed_form:partially_molten` ×1; `typed-refusal:not_comparable_condensed_form:partially_molten:straddles_transition` ×1; `typed-refusal:self_agreement_excluded` ×1 |
| MgCl2 | 1 | 0 | 1 | 0 | 1 | `typed-refusal:no_engine_melting_point_model` ×1 |
| MgO | 4 | 0 | 4 | 0 | 3 | `typed-refusal:missing_numeric_species_rate:geometry_without_measured_flux` ×1; `typed-refusal:not_comparable_condensed_form:crystalline` ×1; `typed-refusal:pointer_or_anchor_without_numeric_points` ×1; `typed-refusal:pure_solid_thermochemistry_not_melt_activity` ×1 |
| Mn | 13 | 1 | 12 | 1 | 12 | `typed-refusal:gibbs_table_not_runtime_observable` ×1; `typed-refusal:missing_condition:melt_density_to_convert_specific_evaporation_constant` ×2; `typed-refusal:missing_condition:pO2_boundary` ×1; `typed-refusal:missing_numeric_species_rate:geometry_without_measured_flux` ×2; `typed-refusal:no_engine_melting_point_model` ×1; `typed-refusal:not_comparable_system_class:molten_metal` ×3; `typed-refusal:self_agreement_excluded` ×1; `typed-refusal:thermodynamic_model_parameter_not_activity_measurement` ×1 |
| Mo | 3 | 0 | 3 | 0 | 2 | `typed-refusal:gibbs_table_not_runtime_observable` ×1; `typed-refusal:unsupported_observable:logKstar_not_activity_coefficient` ×2 |
| MoO2 | 1 | 0 | 1 | 0 | 1 | `typed-refusal:missing_capability:melt_activity_gamma:MoO2` ×1 |
| MoO3 | 1 | 0 | 1 | 0 | 1 | `typed-refusal:missing_capability:melt_activity_gamma:MoO3` ×1 |
| N2 | 3 | 0 | 3 | 0 | 3 | `typed-refusal:no_engine_melting_point_model` ×1; `typed-refusal:no_engine_triple_point_model` ×1; `typed-refusal:no_pure_component_saturation_curve` ×1 |
| NH3 | 1 | 0 | 1 | 0 | 1 | `typed-refusal:no_engine_melting_point_model` ×1 |
| NO2 | 2 | 1 | 1 | 1 | 1 | `typed-refusal:no_engine_melting_point_model` ×1 |
| Na | 39 | 1 | 38 | 1 | 31 | `typed-refusal:analytical_upper_bound_not_measurement` ×4; `typed-refusal:form_unresolved` ×4; `typed-refusal:gibbs_table_not_runtime_observable` ×8; `typed-refusal:missing_numeric_activity` ×1; `typed-refusal:missing_numeric_species_rate` ×4; `typed-refusal:missing_numeric_species_rate:geometry_without_measured_flux` ×2; `typed-refusal:model_output_not_measurement` ×1; `typed-refusal:no_engine_melting_point_model` ×1; `typed-refusal:no_engine_triple_point_model` ×1; `typed-refusal:no_usable_rate_series_payload` ×1; `typed-refusal:pointer_or_anchor_without_numeric_points` ×4; `typed-refusal:self_agreement_excluded` ×1; `typed-refusal:unsupported_observable:deposit_composition_not_species_rate` ×1; `typed-refusal:unsupported_observable:figure_only_not_digitized` ×2; `typed-refusal:unsupported_observable:logKstar_not_activity_coefficient` ×1; `typed-refusal:unsupported_observable:methodology_guidance_not_observable` ×1; `typed-refusal:unsupported_observable:ordering_claim_unparsed` ×1 |
| Na2O | 28 | 1 | 27 | 1 | 27 | `typed-refusal:missing_capability:documented_melt_activity_coefficient:Na2O` ×23; `typed-refusal:missing_numeric_activity` ×4 |
| Na2SO4 | 1 | 0 | 1 | 0 | 1 | `typed-refusal:unknown_transition_property_kind:missing` ×1 |
| NaCl | 2 | 1 | 1 | 1 | 1 | `typed-refusal:no_engine_melting_point_model` ×1 |
| NaF | 3 | 0 | 3 | 0 | 3 | `typed-refusal:no_engine_melting_point_model` ×1; `typed-refusal:no_pure_component_saturation_curve` ×1; `typed-refusal:pointer_or_anchor_without_numeric_points` ×1 |
| Ni | 1 | 0 | 1 | 0 | 1 | `typed-refusal:missing_capability:melt_activity_gamma:NiO` ×1 |
| O | 1 | 0 | 1 | 0 | 13 | `typed-refusal:missing_condition:pO2_boundary` ×1 |
| O2 | 7 | 0 | 7 | 0 | 13 | `typed-refusal:missing_condition:pO2_boundary` ×1; `typed-refusal:missing_numeric_species_rate` ×2; `typed-refusal:model_output_not_measurement` ×1; `typed-refusal:no_engine_melting_point_model` ×1; `typed-refusal:no_engine_triple_point_model` ×1; `typed-refusal:no_pure_component_saturation_curve` ×1 |
| P | 1 | 0 | 1 | 0 | 1 | `typed-refusal:self_agreement_excluded` ×1 |
| P4O10 | 1 | 0 | 1 | 0 | 1 | `typed-refusal:missing_capability:gas_speciation_ladder` ×1 |
| Pb | 6 | 0 | 6 | 0 | 5 | `typed-refusal:gibbs_table_not_runtime_observable` ×1; `typed-refusal:missing_capability:melt_activity_gamma:PbO` ×2; `typed-refusal:no_engine_melting_point_model` ×1; `typed-refusal:unsupported_observable:logKstar_not_activity_coefficient` ×2 |
| Rb | 3 | 0 | 3 | 0 | 2 | `typed-refusal:gibbs_table_not_runtime_observable` ×1; `typed-refusal:missing_capability:melt_activity_gamma:RbO0.5` ×1; `typed-refusal:unsupported_observable:logKstar_not_activity_coefficient` ×1 |
| Rb2O | 1 | 0 | 1 | 0 | 1 | `typed-refusal:missing_capability:gas_speciation_ladder` ×1 |
| S | 4 | 0 | 4 | 0 | 4 | `typed-refusal:missing_condition:melt_density_to_convert_specific_evaporation_constant` ×2; `typed-refusal:not_comparable_system_class:molten_metal` ×2 |
| S2 | 1 | 0 | 1 | 0 | 1 | `typed-refusal:pointer_or_anchor_without_numeric_points` ×1 |
| SO2 | 2 | 0 | 2 | 0 | 2 | `typed-refusal:no_engine_melting_point_model` ×1; `typed-refusal:no_engine_triple_point_model` ×1 |
| SO3 | 1 | 0 | 1 | 0 | 1 | `typed-refusal:pointer_or_anchor_without_numeric_points` ×1 |
| Sb4O6 | 1 | 0 | 1 | 0 | 1 | `typed-refusal:missing_capability:gas_speciation_ladder` ×1 |
| Sc | 1 | 0 | 1 | 0 | 1 | `typed-refusal:missing_numeric_species_rate:geometry_without_measured_flux` ×1 |
| Se_n_ladder | 1 | 0 | 1 | 0 | 3 | `typed-refusal:pure_psat_out_of_certified_range` ×1 |
| Si | 5 | 1 | 4 | 1 | 4 | `typed-refusal:missing_numeric_species_rate` ×1; `typed-refusal:no_engine_melting_point_model` ×1; `typed-refusal:not_comparable_system_class:pure_element_condensed` ×1; `typed-refusal:unsupported_observable:species_not_reported_among_detected` ×1 |
| SiO | 25 | 6 | 19 | 20 | 23 | `typed-refusal:missing_numeric_species_rate` ×2; `typed-refusal:missing_numeric_species_rate:geometry_without_measured_flux` ×2; `typed-refusal:no_usable_rate_series_payload` ×1; `typed-refusal:not_comparable_condensed_form:crystalline` ×3; `typed-refusal:not_comparable_condensed_form:partially_molten` ×1; `typed-refusal:not_comparable_condensed_form:partially_molten:straddles_transition` ×1; `typed-refusal:not_comparable_system_class:solid_film_growth+not_comparable_condensed_form:glass_amorphous` ×3; `typed-refusal:unsupported_observable:figure_only_not_digitized` ×2; `typed-refusal:unsupported_observable:pure_oxide_speciation_index` ×1; `typed-refusal:unsupported_observable:vapour_species_map_no_numeric_pressures` ×3 |
| SiO2 | 34 | 25 | 9 | 25 | 4 | `typed-refusal:form_unresolved` ×1; `typed-refusal:gibbs_table_not_runtime_observable` ×4; `typed-refusal:missing_numeric_activity` ×2; `typed-refusal:self_agreement_excluded` ×1; `typed-refusal:thermodynamic_model_parameter_not_activity_measurement` ×1 |
| Sn | 6 | 0 | 6 | 0 | 6 | `typed-refusal:missing_capability:melt_activity_gamma:SnO` ×1; `typed-refusal:missing_condition:melt_density_to_convert_specific_evaporation_constant` ×2; `typed-refusal:not_comparable_system_class:molten_metal` ×3 |
| SrO | 1 | 0 | 1 | 0 | 1 | `typed-refusal:missing_capability:gas_speciation_ladder` ×1 |
| Ti | 5 | 1 | 4 | 1 | 4 | `typed-refusal:missing_numeric_species_rate:geometry_without_measured_flux` ×1; `typed-refusal:no_engine_melting_point_model` ×1; `typed-refusal:no_engine_solid_solid_transition_model` ×1; `typed-refusal:pointer_or_anchor_without_numeric_points` ×1 |
| TiO | 1 | 0 | 1 | 0 | 1 | `typed-refusal:unsupported_observable:figure_only_not_digitized` ×1 |
| TiO2 | 2 | 0 | 2 | 0 | 2 | `typed-refusal:self_agreement_excluded` ×1; `typed-refusal:unsupported_observable:figure_only_not_digitized` ×1 |
| V | 4 | 0 | 4 | 0 | 4 | `typed-refusal:missing_capability:melt_activity_gamma:V2O3` ×2; `typed-refusal:missing_numeric_activity` ×1; `typed-refusal:missing_numeric_species_rate:geometry_without_measured_flux` ×1 |
| VO_VO2 | 2 | 1 | 1 | 2 | 1 | `typed-refusal:missing_capability:gas_speciation_ladder` ×1 |
| WO2 | 1 | 0 | 1 | 0 | 1 | `typed-refusal:missing_capability:melt_activity_gamma:WO2` ×1 |
| WO3 | 2 | 0 | 2 | 0 | 2 | `typed-refusal:missing_capability:melt_activity_gamma:WO3` ×1; `typed-refusal:missing_numeric_activity` ×1 |
| Yb | 1 | 0 | 1 | 0 | 1 | `typed-refusal:missing_numeric_species_rate:geometry_without_measured_flux` ×1 |
| Yb_metal_and_YbO | 3 | 0 | 3 | 0 | 5 | `typed-refusal:missing_capability:gas_speciation_ladder` ×2; `typed-refusal:pure_psat_out_of_certified_range` ×1 |
| Zn | 4 | 0 | 4 | 0 | 3 | `typed-refusal:gibbs_table_not_runtime_observable` ×1; `typed-refusal:missing_capability:melt_activity_gamma:ZnO` ×2; `typed-refusal:unsupported_observable:logKstar_not_activity_coefficient` ×1 |
| Zr | 1 | 0 | 1 | 0 | 1 | `typed-refusal:missing_numeric_species_rate:geometry_without_measured_flux` ×1 |

**Coverage by source:**

| Source | Observations | Comparable | Skipped | Comparable points | Gap points | Typed skip reasons |
|---|---:|---:|---:|---:|---:|---|
| behrens-rosenblatt-1972 | 1 | 0 | 1 | 0 | 3 | `typed-refusal:pure_psat_out_of_certified_range` ×1 |
| berkowitz-chupka-inghram-1957 | 1 | 1 | 0 | 2 | 0 | — |
| costa-jacobson-2015 | 2 | 0 | 2 | 0 | 2 | `typed-refusal:not_comparable_condensed_form:crystalline` ×2 |
| fedkin-grossman-ghiorso-2006 | 8 | 6 | 2 | 24 | 2 | `typed-refusal:form_unresolved` ×2 |
| habermann-daane-1964 | 1 | 0 | 1 | 0 | 3 | `typed-refusal:pure_psat_out_of_certified_range` ×1 |
| janaf-4th | 25 | 8 | 17 | 8 | 17 | `typed-refusal:no_engine_melting_point_model` ×10; `typed-refusal:no_engine_solid_solid_transition_model` ×1; `typed-refusal:no_engine_triple_point_model` ×1; `typed-refusal:no_pure_component_saturation_curve` ×1; `typed-refusal:pointer_or_anchor_without_numeric_points` ×4 |
| kems-001-homma-1966 | 10 | 0 | 10 | 0 | 10 | `typed-refusal:missing_condition:melt_density_to_convert_specific_evaporation_constant` ×3; `typed-refusal:missing_numeric_species_rate:geometry_without_measured_flux` ×1; `typed-refusal:not_comparable_system_class:molten_metal` ×6 |
| kems-002-ohno-1967 | 12 | 0 | 12 | 0 | 12 | `typed-refusal:missing_condition:melt_density_to_convert_specific_evaporation_constant` ×6; `typed-refusal:not_comparable_system_class:molten_metal` ×6 |
| kems-003-pound-1972 | 6 | 0 | 6 | 0 | 6 | `typed-refusal:not_comparable_system_class:pure_element_condensed+not_comparable_condensed_form:crystalline` ×5; `typed-refusal:unsupported_observable:clausing_factor_not_species_rate` ×1 |
| kems-005-fedkin-2006 | 10 | 5 | 5 | 14 | 5 | `typed-refusal:form_unresolved` ×4; `typed-refusal:no_usable_rate_series_payload` ×1 |
| kems-006-zhang-2021 | 4 | 1 | 3 | 2 | 4 | `typed-refusal:analytical_upper_bound_not_measurement` ×1; `typed-refusal:missing_numeric_species_rate` ×1; `typed-refusal:missing_numeric_species_rate:geometry_without_measured_flux` ×1 |
| kems-007-costa-2015 | 4 | 0 | 4 | 0 | 4 | `typed-refusal:not_comparable_condensed_form:crystalline` ×4 |
| kems-008-schaefer-fegley-2004 | 5 | 2 | 3 | 3 | 3 | `typed-refusal:form_unresolved` ×1; `typed-refusal:not_comparable_condensed_form:crystalline` ×1; `typed-refusal:not_comparable_condensed_form:crystalline:straddles_transition` ×1 |
| kems-009-safarian-2013 | 2 | 0 | 2 | 0 | 2 | `typed-refusal:missing_numeric_species_rate` ×1; `typed-refusal:not_comparable_system_class:pure_element_condensed` ×1 |
| kems-010-richter-2007 | 6 | 4 | 2 | 8 | 5 | `typed-refusal:missing_condition:pO2_boundary` ×1; `typed-refusal:missing_numeric_species_rate` ×1 |
| kems-011-wetzel-gail-2013 | 3 | 0 | 3 | 0 | 5 | `typed-refusal:missing_numeric_species_rate` ×1; `typed-refusal:not_comparable_system_class:solid_film_growth+not_comparable_condensed_form:glass_amorphous` ×2 |
| kems-012-sossi-2019 | 53 | 3 | 50 | 3 | 37 | `typed-refusal:analytical_upper_bound_not_measurement` ×2; `typed-refusal:gibbs_table_not_runtime_observable` ×12; `typed-refusal:missing_capability:melt_activity_gamma:AgO0.5` ×1; `typed-refusal:missing_capability:melt_activity_gamma:CdO` ×1; `typed-refusal:missing_capability:melt_activity_gamma:CuO0.5` ×1; `typed-refusal:missing_capability:melt_activity_gamma:GeO2` ×1; `typed-refusal:missing_capability:melt_activity_gamma:LiO0.5` ×1; `typed-refusal:missing_capability:melt_activity_gamma:PbO` ×1; `typed-refusal:missing_capability:melt_activity_gamma:RbO0.5` ×1; `typed-refusal:missing_capability:melt_activity_gamma:ZnO` ×1; `typed-refusal:missing_numeric_species_rate:geometry_without_measured_flux` ×8; `typed-refusal:no_usable_rate_series_payload` ×1; `typed-refusal:self_agreement_excluded` ×1; `typed-refusal:thermodynamic_model_parameter_not_activity_measurement` ×1; `typed-refusal:unsupported_observable:logKstar_not_activity_coefficient` ×17 |
| kems-014-drowart-2005 | 2 | 0 | 2 | 0 | 1 | `typed-refusal:gibbs_table_not_runtime_observable` ×1; `typed-refusal:unsupported_observable:methodology_guidance_not_observable` ×1 |
| kems-015-hashimoto-1983 | 9 | 2 | 7 | 2 | 7 | `typed-refusal:missing_condition:melt_composition` ×1; `typed-refusal:missing_numeric_species_rate` ×2; `typed-refusal:missing_numeric_species_rate:geometry_without_measured_flux` ×4 |
| kems-016-stolyarova-1992 | 10 | 0 | 10 | 0 | 4 | `typed-refusal:gibbs_table_not_runtime_observable` ×5; `typed-refusal:missing_numeric_activity` ×3; `typed-refusal:thermodynamic_model_parameter_not_activity_measurement` ×1; `typed-refusal:unsupported_observable:species_detected_absolute_P_not_tabulated` ×1 |
| kems-017-stolyarova-2013 | 1 | 0 | 1 | 0 | 1 | `typed-refusal:unsupported_observable:vapour_species_map_no_numeric_pressures` ×1 |
| kems-018-stolyarova-2012 | 1 | 0 | 1 | 0 | 1 | `typed-refusal:unsupported_observable:vapour_species_map_no_numeric_pressures` ×1 |
| kems-020-hastie-1981-nbsir | 6 | 0 | 6 | 0 | 6 | `typed-refusal:model_output_not_measurement` ×1; `typed-refusal:pointer_or_anchor_without_numeric_points` ×4; `typed-refusal:unknown_transition_property_kind:missing` ×1 |
| kems-021-plante-1992-feo | 4 | 0 | 4 | 0 | 4 | `typed-refusal:missing_numeric_activity` ×1; `typed-refusal:missing_numeric_species_rate` ×1; `typed-refusal:unsupported_observable:figure_only_not_digitized` ×2 |
| kems-022-demaria-1971 | 23 | 0 | 23 | 0 | 67 | `typed-refusal:gibbs_table_not_runtime_observable` ×2; `typed-refusal:missing_capability:documented_melt_activity_coefficient:FeO` ×2; `typed-refusal:missing_condition:pO2_boundary` ×7; `typed-refusal:missing_numeric_activity` ×1; `typed-refusal:pointer_or_anchor_without_numeric_points` ×2; `typed-refusal:unsupported_observable:figure_only_not_digitized` ×5; `typed-refusal:unsupported_observable:ordering_claim_unparsed` ×2; `typed-refusal:unsupported_observable:species_detected_absolute_P_not_tabulated` ×1; `typed-refusal:unsupported_observable:species_not_reported_among_detected` ×1 |
| kems-027-plante-hastie-1983 | 1 | 0 | 1 | 0 | 1 | `typed-refusal:missing_numeric_species_rate:geometry_without_measured_flux` ×1 |
| kems-031-halwax-2024 | 5 | 0 | 5 | 0 | 3 | `typed-refusal:missing_numeric_species_rate:geometry_without_measured_flux` ×2; `typed-refusal:pointer_or_anchor_without_numeric_points` ×1; `typed-refusal:pure_solid_thermochemistry_not_melt_activity` ×2 |
| kems-032-copland-jacobson-2010 | 5 | 0 | 5 | 0 | 4 | `typed-refusal:gibbs_table_not_runtime_observable` ×1; `typed-refusal:missing_numeric_species_rate:geometry_without_measured_flux` ×2; `typed-refusal:unsupported_observable:clausing_factor_not_species_rate` ×2 |
| kems-035-sauerborn-2005 | 9 | 0 | 9 | 0 | 6 | `typed-refusal:gibbs_table_not_runtime_observable` ×3; `typed-refusal:missing_numeric_species_rate` ×5; `typed-refusal:unsupported_observable:figure_only_not_digitized` ×1 |
| kems-036-sesko-2024 | 6 | 0 | 6 | 0 | 4 | `typed-refusal:gibbs_table_not_runtime_observable` ×2; `typed-refusal:model_output_not_measurement` ×1; `typed-refusal:unsupported_observable:deposit_composition_not_species_rate` ×2; `typed-refusal:unsupported_observable:figure_only_not_digitized` ×1 |
| kems-037-richter-2002 | 4 | 0 | 4 | 0 | 4 | `typed-refusal:not_comparable_condensed_form:partially_molten` ×2; `typed-refusal:not_comparable_condensed_form:partially_molten:straddles_transition` ×2 |
| kems-038-matchett-2006 | 3 | 0 | 3 | 0 | 2 | `typed-refusal:gibbs_table_not_runtime_observable` ×1; `typed-refusal:missing_numeric_species_rate` ×1; `typed-refusal:unsupported_observable:figure_only_not_digitized` ×1 |
| kems-040-stolyarova-2015 | 2 | 0 | 2 | 0 | 2 | `typed-refusal:unsupported_observable:figure_only_not_digitized` ×1; `typed-refusal:unsupported_observable:vapour_species_map_no_numeric_pressures` ×1 |
| kems-041-sossi-fegley-2018 | 32 | 0 | 32 | 0 | 32 | `typed-refusal:form_unresolved` ×2; `typed-refusal:missing_capability:melt_activity_gamma:AsO1.5` ×1; `typed-refusal:missing_capability:melt_activity_gamma:BiO1.5` ×1; `typed-refusal:missing_capability:melt_activity_gamma:CoO` ×1; `typed-refusal:missing_capability:melt_activity_gamma:CrO` ×1; `typed-refusal:missing_capability:melt_activity_gamma:CuO0.5` ×1; `typed-refusal:missing_capability:melt_activity_gamma:FeO` ×1; `typed-refusal:missing_capability:melt_activity_gamma:GeO` ×1; `typed-refusal:missing_capability:melt_activity_gamma:GeO2` ×1; `typed-refusal:missing_capability:melt_activity_gamma:InO1.5` ×1; `typed-refusal:missing_capability:melt_activity_gamma:MoO2` ×1; `typed-refusal:missing_capability:melt_activity_gamma:MoO3` ×1; `typed-refusal:missing_capability:melt_activity_gamma:NiO` ×1; `typed-refusal:missing_capability:melt_activity_gamma:PbO` ×1; `typed-refusal:missing_capability:melt_activity_gamma:SnO` ×1; `typed-refusal:missing_capability:melt_activity_gamma:V2O3` ×2; `typed-refusal:missing_capability:melt_activity_gamma:WO2` ×1; `typed-refusal:missing_capability:melt_activity_gamma:WO3` ×1; `typed-refusal:missing_capability:melt_activity_gamma:ZnO` ×1; `typed-refusal:missing_numeric_activity` ×2; `typed-refusal:self_agreement_excluded` ×8; `typed-refusal:unsupported_observable:pure_oxide_speciation_index` ×1 |
| kems-042-plante-1979 | 162 | 0 | 162 | 0 | 162 | `typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O` ×162 |
| kems-ms2000-044 | 50 | 25 | 25 | 25 | 23 | `typed-refusal:gibbs_table_not_runtime_observable` ×2; `typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O` ×11; `typed-refusal:missing_capability:documented_melt_activity_coefficient:Na2O` ×11; `typed-refusal:missing_numeric_activity` ×1 |
| nist-webbook | 45 | 7 | 38 | 7 | 42 | `typed-refusal:no_engine_melting_point_model` ×17; `typed-refusal:no_engine_triple_point_model` ×13; `typed-refusal:no_pure_component_saturation_curve` ×6; `typed-refusal:pure_psat_out_of_certified_range` ×2 |
| richter-et-al-2007 | 2 | 2 | 0 | 6 | 0 | — |
| sossi-et-al-2019 | 1 | 0 | 1 | 0 | 1 | `typed-refusal:analytical_upper_bound_not_measurement` ×1 |
| sossi-fegley-2018 | 14 | 0 | 14 | 0 | 14 | `typed-refusal:missing_capability:gas_speciation_ladder` ×14 |
| ts1985 | 16 | 1 | 15 | 1 | 15 | `typed-refusal:missing_capability:documented_melt_activity_coefficient:Na2O` ×12; `typed-refusal:missing_numeric_activity` ×3 |
| wetzel-gail-2013-sio-arrhenius | 1 | 0 | 1 | 0 | 3 | `typed-refusal:not_comparable_system_class:solid_film_growth+not_comparable_condensed_form:glass_amorphous` ×1 |
| yam1983 | 3 | 0 | 3 | 0 | 3 | `typed-refusal:missing_numeric_activity` ×3 |

**Uncertainty ledger:** extract-side terms are propagated when the
source supplies a quantitative form (for example Arrhenius activation-energy
uncertainty). The engine paths expose no quantitative joint uncertainty for
vapor pressure, activity, composition/redox, or grounded alpha; therefore the
combined propagated uncertainty is reported as **not computable**, not replaced
with an invented model error bar. `Residual / literature budget` uses only the
stated/default literature-side budget.

**Default tolerances** (used only when the extract carries no usable
numeric uncertainty; each defaulted comparison carries
`defaulted: true` on the uncertainty dict and still scores
match/mismatch against that documented budget):

- `psat_series`: `log10_decades` = 0.5 (extract observation has no usable numeric uncertainty; default half-dex high-T vapor-pressure envelope (t-512))
- `rate_series` (measured flux): `log10_decades` = 0.5 (extract rate observation has no usable numeric uncertainty; default half-dex digitized high-temperature flux envelope (t-512))
- `alpha`: `absolute` = 0.05 (extract observation has no usable numeric uncertainty; default absolute α envelope ±0.05 (t-512))
- `activity_coefficient`: `relative_fraction` = 0.5 (extract observation has no usable numeric uncertainty; default 50% relative activity/γ envelope (t-512))
- `transition_point`: `absolute` = 1.0 K (extract transition_point has no usable numeric uncertainty; default 1 K absolute envelope (TRC-typical NBP u; match/mismatch budget, not a regression band and not a dex envelope))

**FINDINGS (mismatches outside budget — not tuned away):**

- FINDING mismatch Al Al_normal_boiling_point T_obs=2793 K T_engine=2328.48 K residual_K=-464.518 target_P=101325 Pa budget={'kind': 'absolute', 'value': 1.0, 'defaulted': True, 'rationale': 'extract transition_point has no usable numeric uncertainty; default 1 K absolute envelope (TRC-typical NBP u; match/mismatch budget, not a regression band and not a dex envelope)'} extrapolated: true
- FINDING mismatch Ca Ca_normal_boiling_point T_obs=1757 K T_engine=1717.78 K residual_K=-39.2161 target_P=101325 Pa budget={'kind': 'absolute', 'value': 1.0, 'defaulted': True, 'rationale': 'extract transition_point has no usable numeric uncertainty; default 1 K absolute envelope (TRC-typical NBP u; match/mismatch budget, not a regression band and not a dex envelope)'} extrapolated: true
- FINDING mismatch Cl2 Cl2_normal_boiling_point T_obs=239.5 K T_engine=240.588 K residual_K=1.08773 target_P=101325 Pa budget={'kind': 'absolute', 'value': 1.0, 'defaulted': True, 'rationale': 'extract transition_point has no usable numeric uncertainty; default 1 K absolute envelope (TRC-typical NBP u; match/mismatch budget, not a regression band and not a dex envelope)'}
- FINDING mismatch Cr Cr_normal_boiling_point T_obs=2952.08 K T_engine=2753.35 K residual_K=-198.725 target_P=101325 Pa budget={'kind': 'absolute', 'value': 1.0, 'defaulted': True, 'rationale': 'extract transition_point has no usable numeric uncertainty; default 1 K absolute envelope (TRC-typical NBP u; match/mismatch budget, not a regression band and not a dex envelope)'} extrapolated: true
- FINDING mismatch Fe α T=1973K expected=0.23 actual=0.02 budget={'kind': 'absolute', 'value': 0.02, 'defaulted': False, 'source': 'point.sigma', 'components': []}
- FINDING mismatch Fe α T=2073K expected=0.25 actual=0.02 budget={'kind': 'absolute', 'value': 0.02, 'defaulted': False, 'source': 'point.sigma', 'components': []}
- FINDING mismatch Fe α T=2173K expected=0.24 actual=0.02 budget={'kind': 'absolute', 'value': 0.02, 'defaulted': False, 'source': 'point.sigma', 'components': []}
- FINDING mismatch Fe α T=2273K expected=0.23 actual=0.02 budget={'kind': 'absolute', 'value': 0.02, 'defaulted': False, 'source': 'point.sigma', 'components': []}
- FINDING mismatch Fe α T=1973K expected=0.23 actual=0.02 budget={'kind': 'absolute', 'value': 0.02, 'defaulted': False, 'source': 'point.sigma', 'components': []}
- FINDING mismatch Fe α T=2073K expected=0.25 actual=0.02 budget={'kind': 'absolute', 'value': 0.02, 'defaulted': False, 'source': 'point.sigma', 'components': []}
- FINDING mismatch Fe α T=2173K expected=0.24 actual=0.02 budget={'kind': 'absolute', 'value': 0.02, 'defaulted': False, 'source': 'point.sigma', 'components': []}
- FINDING mismatch Fe α T=2273K expected=0.23 actual=0.02 budget={'kind': 'absolute', 'value': 0.02, 'defaulted': False, 'source': 'point.sigma', 'components': []}
- FINDING mismatch Fe Fe_normal_boiling_point T_obs=3134 K T_engine=3135.15 K residual_K=1.14989 target_P=101325 Pa budget={'kind': 'absolute', 'value': 1.0, 'defaulted': True, 'rationale': 'extract transition_point has no usable numeric uncertainty; default 1 K absolute envelope (TRC-typical NBP u; match/mismatch budget, not a regression band and not a dex envelope)'}
- FINDING mismatch Fe α T=2123K expected=0.24 actual=0.02 budget={'kind': 'absolute', 'value': 0.05, 'defaulted': True, 'rationale': 'extract observation has no usable numeric uncertainty; default absolute α envelope ±0.05 (t-512)'}
- FINDING mismatch Fe α T=1973K expected=0.23 actual=0.02 budget={'kind': 'absolute', 'value': 0.02, 'defaulted': False, 'source': 'point.sigma', 'components': []}
- FINDING mismatch Fe α T=2073K expected=0.25 actual=0.02 budget={'kind': 'absolute', 'value': 0.02, 'defaulted': False, 'source': 'point.sigma', 'components': []}
- FINDING mismatch Fe α T=2173K expected=0.24 actual=0.02 budget={'kind': 'absolute', 'value': 0.02, 'defaulted': False, 'source': 'point.sigma', 'components': []}
- FINDING mismatch Fe α T=2273K expected=0.23 actual=0.02 budget={'kind': 'absolute', 'value': 0.02, 'defaulted': False, 'source': 'point.sigma', 'components': []}
- FINDING ordering-fail Fe hashimoto_1983_fcmas_qualitative_volatility_order asserted=Fe > Mg|Si > Ca > Al pairs_ok=23.0/27.0
- FINDING mismatch K α T=1473.15K expected=0.05 actual=0.13 budget={'kind': 'absolute', 'value': 0.02, 'defaulted': False, 'source': 'point.sigma', 'components': []}
- FINDING mismatch K α T=1698.15K expected=1 actual=0.13 budget={'kind': 'absolute', 'value': 0.05, 'defaulted': True, 'rationale': 'extract observation has no usable numeric uncertainty; default absolute α envelope ±0.05 (t-512)'}
- FINDING mismatch K gamma expected=0.00022 actual=3.5e-05 residual_dex=0.7983546364719307 budget={'kind': 'relative_fraction', 'value': 0.5, 'defaulted': True, 'rationale': 'extract observation has no usable numeric uncertainty; default 50% relative activity/γ envelope (t-512)'}
- FINDING mismatch K K_normal_boiling_point T_obs=1037 K T_engine=1029.75 K residual_K=-7.25395 target_P=101325 Pa budget={'kind': 'absolute', 'value': 1.0, 'defaulted': True, 'rationale': 'extract transition_point has no usable numeric uncertainty; default 1 K absolute envelope (TRC-typical NBP u; match/mismatch budget, not a regression band and not a dex envelope)'} extrapolated: true
- FINDING mismatch Mg α T=1973K expected=0.24 actual=0.2 budget={'kind': 'absolute', 'value': 0.01, 'defaulted': False, 'source': 'point.sigma', 'components': []}
- FINDING mismatch Mg α T=2073K expected=0.28 actual=0.2 budget={'kind': 'absolute', 'value': 0.01, 'defaulted': False, 'source': 'point.sigma', 'components': []}
- FINDING mismatch Mg α T=2173K expected=0.28 actual=0.2 budget={'kind': 'absolute', 'value': 0.01, 'defaulted': False, 'source': 'point.sigma', 'components': []}
- FINDING mismatch Mg α T=2273K expected=0.27 actual=0.2 budget={'kind': 'absolute', 'value': 0.01, 'defaulted': False, 'source': 'point.sigma', 'components': []}
- FINDING mismatch Mg α T=1973K expected=0.24 actual=0.2 budget={'kind': 'absolute', 'value': 0.01, 'defaulted': False, 'source': 'point.sigma', 'components': []}
- FINDING mismatch Mg α T=2073K expected=0.28 actual=0.2 budget={'kind': 'absolute', 'value': 0.01, 'defaulted': False, 'source': 'point.sigma', 'components': []}
- FINDING mismatch Mg α T=2173K expected=0.28 actual=0.2 budget={'kind': 'absolute', 'value': 0.01, 'defaulted': False, 'source': 'point.sigma', 'components': []}
- FINDING mismatch Mg α T=2273K expected=0.27 actual=0.2 budget={'kind': 'absolute', 'value': 0.01, 'defaulted': False, 'source': 'point.sigma', 'components': []}
- FINDING mismatch Mg α T=1973K expected=0.24 actual=0.2 budget={'kind': 'absolute', 'value': 0.01, 'defaulted': False, 'source': 'point.sigma', 'components': []}
- FINDING mismatch Mg α T=2073K expected=0.28 actual=0.2 budget={'kind': 'absolute', 'value': 0.01, 'defaulted': False, 'source': 'point.sigma', 'components': []}
- FINDING mismatch Mg α T=2173K expected=0.28 actual=0.2 budget={'kind': 'absolute', 'value': 0.01, 'defaulted': False, 'source': 'point.sigma', 'components': []}
- FINDING mismatch Mg α T=2273K expected=0.27 actual=0.2 budget={'kind': 'absolute', 'value': 0.01, 'defaulted': False, 'source': 'point.sigma', 'components': []}
- FINDING mismatch Mg Mg_normal_boiling_point T_obs=1363 K T_engine=1360.7 K residual_K=-2.3035 target_P=101325 Pa budget={'kind': 'absolute', 'value': 1.0, 'defaulted': True, 'rationale': 'extract transition_point has no usable numeric uncertainty; default 1 K absolute envelope (TRC-typical NBP u; match/mismatch budget, not a regression band and not a dex envelope)'} extrapolated: true
- FINDING mismatch NO2 NO2_normal_boiling_point T_obs=295.08 K T_engine=293.47 K residual_K=-1.61035 target_P=101325 Pa budget={'kind': 'absolute', 'value': 1.0, 'defaulted': True, 'rationale': 'extract transition_point has no usable numeric uncertainty; default 1 K absolute envelope (TRC-typical NBP u; match/mismatch budget, not a regression band and not a dex envelope)'}
- FINDING mismatch Na Na_normal_boiling_point T_obs=1156 K T_engine=1179.58 K residual_K=23.5847 target_P=101325 Pa budget={'kind': 'absolute', 'value': 1.0, 'defaulted': True, 'rationale': 'extract transition_point has no usable numeric uncertainty; default 1 K absolute envelope (TRC-typical NBP u; match/mismatch budget, not a regression band and not a dex envelope)'} extrapolated: true
- FINDING mismatch Na2O activity expected=3.24e-08 actual=2.03233e-07 residual_dex=0.7974484745603244 budget={'kind': 'relative_fraction', 'value': 0.5, 'defaulted': True, 'rationale': 'extract observation has no usable numeric uncertainty; default 50% relative activity/γ envelope (t-512)'}
- FINDING mismatch Si Si_normal_boiling_point T_obs=3504.62 K T_engine=2560.19 K residual_K=-944.43 target_P=100000 Pa budget={'kind': 'absolute', 'value': 1.0, 'defaulted': True, 'rationale': 'extract transition_point has no usable numeric uncertainty; default 1 K absolute envelope (TRC-typical NBP u; match/mismatch budget, not a regression band and not a dex envelope)'} extrapolated: true
- FINDING mismatch SiO α T=1973K expected=0.12 actual=0.08032771227334354 budget={'kind': 'absolute', 'value': 0.01, 'defaulted': False, 'source': 'point.sigma', 'components': []} extrapolated: true
- FINDING mismatch SiO α T=2073K expected=0.17 actual=0.08790105714998556 budget={'kind': 'absolute', 'value': 0.01, 'defaulted': False, 'source': 'point.sigma', 'components': []} extrapolated: true
- FINDING mismatch SiO α T=2173K expected=0.2 actual=0.09539408461828996 budget={'kind': 'absolute', 'value': 0.01, 'defaulted': False, 'source': 'point.sigma', 'components': []} extrapolated: true
- FINDING mismatch SiO α T=2273K expected=0.21 actual=0.10278334807870564 budget={'kind': 'absolute', 'value': 0.01, 'defaulted': False, 'source': 'point.sigma', 'components': []} extrapolated: true
- FINDING mismatch SiO α T=1973K expected=0.12 actual=0.08032771227334354 budget={'kind': 'absolute', 'value': 0.01, 'defaulted': False, 'source': 'point.sigma', 'components': []} extrapolated: true
- FINDING mismatch SiO α T=2073K expected=0.17 actual=0.08790105714998556 budget={'kind': 'absolute', 'value': 0.01, 'defaulted': False, 'source': 'point.sigma', 'components': []} extrapolated: true
- FINDING mismatch SiO α T=2173K expected=0.2 actual=0.09539408461828996 budget={'kind': 'absolute', 'value': 0.01, 'defaulted': False, 'source': 'point.sigma', 'components': []} extrapolated: true
- FINDING mismatch SiO α T=2273K expected=0.21 actual=0.10278334807870564 budget={'kind': 'absolute', 'value': 0.01, 'defaulted': False, 'source': 'point.sigma', 'components': []} extrapolated: true
- FINDING mismatch SiO α T=1973K expected=0.12 actual=0.08032771227334354 budget={'kind': 'absolute', 'value': 0.01, 'defaulted': False, 'source': 'point.sigma', 'components': []} extrapolated: true
- FINDING mismatch SiO α T=2073K expected=0.17 actual=0.08790105714998556 budget={'kind': 'absolute', 'value': 0.01, 'defaulted': False, 'source': 'point.sigma', 'components': []} extrapolated: true
- FINDING mismatch SiO α T=2173K expected=0.2 actual=0.09539408461828996 budget={'kind': 'absolute', 'value': 0.01, 'defaulted': False, 'source': 'point.sigma', 'components': []} extrapolated: true
- FINDING mismatch SiO α T=2273K expected=0.21 actual=0.10278334807870564 budget={'kind': 'absolute', 'value': 0.01, 'defaulted': False, 'source': 'point.sigma', 'components': []} extrapolated: true
- FINDING mismatch SiO α T=2273K expected=0.2 actual=0.10278334807870564 budget={'kind': 'absolute', 'value': 0.08, 'defaulted': False, 'source': 'observation.values.alpha_range', 'components': ['published alpha range half-width']} extrapolated: true
- FINDING mismatch SiO2 activity expected=0.00644 actual=0.333333 residual_dex=1.7139928779205253 budget={'kind': 'relative_fraction', 'value': 0.5, 'defaulted': True, 'rationale': 'extract observation has no usable numeric uncertainty; default 50% relative activity/γ envelope (t-512)'}
- FINDING mismatch SiO2 activity expected=0.00969 actual=0.372684 residual_dex=1.5850165007680914 budget={'kind': 'relative_fraction', 'value': 0.5, 'defaulted': True, 'rationale': 'extract observation has no usable numeric uncertainty; default 50% relative activity/γ envelope (t-512)'}
- FINDING mismatch SiO2 activity expected=0.0111 actual=0.419446 residual_dex=1.5773535089852415 budget={'kind': 'relative_fraction', 'value': 0.5, 'defaulted': True, 'rationale': 'extract observation has no usable numeric uncertainty; default 50% relative activity/γ envelope (t-512)'}
- FINDING mismatch SiO2 activity expected=0.0187 actual=0.459854 residual_dex=1.390778375760676 budget={'kind': 'relative_fraction', 'value': 0.5, 'defaulted': True, 'rationale': 'extract observation has no usable numeric uncertainty; default 50% relative activity/γ envelope (t-512)'}
- FINDING mismatch SiO2 activity expected=0.0267 actual=0.459854 residual_dex=1.2361087209325996 budget={'kind': 'relative_fraction', 'value': 0.5, 'defaulted': True, 'rationale': 'extract observation has no usable numeric uncertainty; default 50% relative activity/γ envelope (t-512)'}
- FINDING mismatch SiO2 activity expected=0.101 actual=0.508296 residual_dex=0.7017949986839229 budget={'kind': 'relative_fraction', 'value': 0.5, 'defaulted': True, 'rationale': 'extract observation has no usable numeric uncertainty; default 50% relative activity/γ envelope (t-512)'}
- FINDING mismatch SiO2 activity expected=3.77e-06 actual=0.211387 residual_dex=4.748737003490594 budget={'kind': 'relative_fraction', 'value': 0.5, 'defaulted': True, 'rationale': 'extract observation has no usable numeric uncertainty; default 50% relative activity/γ envelope (t-512)'}
- FINDING mismatch SiO2 activity expected=4.76e-05 actual=0.236094 residual_dex=3.6954778929149623 budget={'kind': 'relative_fraction', 'value': 0.5, 'defaulted': True, 'rationale': 'extract observation has no usable numeric uncertainty; default 50% relative activity/γ envelope (t-512)'}
- FINDING mismatch SiO2 activity expected=0.000138 actual=0.253918 residual_dex=3.264815249420232 budget={'kind': 'relative_fraction', 'value': 0.5, 'defaulted': True, 'rationale': 'extract observation has no usable numeric uncertainty; default 50% relative activity/γ envelope (t-512)'}
- FINDING mismatch SiO2 activity expected=0.00038 actual=0.273885 residual_dex=2.8577852065535425 budget={'kind': 'relative_fraction', 'value': 0.5, 'defaulted': True, 'rationale': 'extract observation has no usable numeric uncertainty; default 50% relative activity/γ envelope (t-512)'}
- FINDING mismatch SiO2 activity expected=0.00175 actual=0.313198 residual_dex=2.2527804270177767 budget={'kind': 'relative_fraction', 'value': 0.5, 'defaulted': True, 'rationale': 'extract observation has no usable numeric uncertainty; default 50% relative activity/γ envelope (t-512)'}
- FINDING mismatch SiO2 activity expected=0.0204 actual=0.355014 residual_dex=1.2406147620708052 budget={'kind': 'relative_fraction', 'value': 0.5, 'defaulted': True, 'rationale': 'extract observation has no usable numeric uncertainty; default 50% relative activity/γ envelope (t-512)'}
- FINDING mismatch SiO2 activity expected=0.0703 actual=0.401542 residual_dex=0.756775323832919 budget={'kind': 'relative_fraction', 'value': 0.5, 'defaulted': True, 'rationale': 'extract observation has no usable numeric uncertainty; default 50% relative activity/γ envelope (t-512)'}
- FINDING mismatch SiO2 activity expected=0.199 actual=0.454545 residual_dex=0.35872424276808706 budget={'kind': 'relative_fraction', 'value': 0.5, 'defaulted': True, 'rationale': 'extract observation has no usable numeric uncertainty; default 50% relative activity/γ envelope (t-512)'}
- FINDING mismatch SiO2 activity expected=0.00601 actual=0.333333 residual_dex=1.744004273277598 budget={'kind': 'relative_fraction', 'value': 0.5, 'defaulted': True, 'rationale': 'extract observation has no usable numeric uncertainty; default 50% relative activity/γ envelope (t-512)'}
- FINDING mismatch Ti Ti_normal_boiling_point T_obs=3630.96 K T_engine=3560.15 K residual_K=-70.806 target_P=101325 Pa budget={'kind': 'absolute', 'value': 1.0, 'defaulted': True, 'rationale': 'extract transition_point has no usable numeric uncertainty; default 1 K absolute envelope (TRC-typical NBP u; match/mismatch budget, not a regression band and not a dex envelope)'} extrapolated: true
- FINDING mismatch VO_VO2 BIC57_VO_absolute_points T=1875K expected=0.155 Pa actual=0.012068570930116797 residual_dex=1.108675850931988 budget={'kind': 'log10_decades', 'value': 0.5, 'defaulted': True, 'rationale': 'extract observation has no usable numeric uncertainty; default half-dex high-T vapor-pressure envelope (t-512)'}
- FINDING mismatch VO_VO2 BIC57_VO_absolute_points T=1876K expected=0.116 Pa actual=0.012291612528798502 residual_dex=0.9748491277871426 budget={'kind': 'log10_decades', 'value': 0.5, 'defaulted': True, 'rationale': 'extract observation has no usable numeric uncertainty; default half-dex high-T vapor-pressure envelope (t-512)'}

Comparable per-observation residuals and uncertainty ledger:

| Source | Observation | Type | Species | Coordinate | Literature | Literature uncertainty | Engine uncertainty | Combined propagated uncertainty | Engine | Residual | Residual dex | Residual / literature budget | Status |
|---|---|---|---|---|---:|---|---|---|---:|---:|---:|---:|---|
| kems-010-richter-2007 | richter_2007_al_non_loss_until_mg_exhausted | rate_series | Al | T_min_K=1873.15, T_max_K=2173.15, n_T=3 | 3 | absolute=0.0 (ordering pairwise count (exact integer)) | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 3 | 0 | 0 | 0 | ordering-pass |
| nist-webbook | Al_normal_boiling_point | transition_point | Al | property_kind=normal_boiling_point, target_pressure_Pa=101325 | 2793 | absolute=1.0 (documented default) | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 2328.48 | -464.518 | — | 464.518 | mismatch |
| kems-010-richter-2007 | richter_2007_ca_non_loss_until_mg_exhausted | rate_series | Ca | T_min_K=1873.15, T_max_K=2173.15, n_T=3 | 3 | absolute=0.0 (ordering pairwise count (exact integer)) | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 3 | 0 | 0 | 0 | ordering-pass |
| kems-015-hashimoto-1983 | hashimoto_1983_cao_al2o3_residue_enrichment | rate_series | Ca | T_min_K=1873.15, T_max_K=2273.15, n_T=3 | 6 | absolute=0.0 (ordering pairwise count (exact integer)) | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 6 | 0 | 0 | 0 | ordering-pass |
| nist-webbook | Ca_normal_boiling_point | transition_point | Ca | property_kind=normal_boiling_point, target_pressure_Pa=101325 | 1757 | absolute=1.0 (documented default) | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 1717.78 | -39.2161 | — | 39.2161 | mismatch |
| nist-webbook | Cl2_normal_boiling_point | transition_point | Cl2 | property_kind=normal_boiling_point, target_pressure_Pa=101325 | 239.5 | absolute=1.0 (documented default) | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 240.588 | 1.08773 | — | 1.08773 | mismatch |
| janaf-4th | Cr_normal_boiling_point | transition_point | Cr | property_kind=normal_boiling_point, target_pressure_Pa=101325 | 2952.08 | absolute=1.0 (documented default) | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 2753.35 | -198.725 | — | 198.725 | mismatch |
| fedkin-grossman-ghiorso-2006 | fedkin_2006_table3_fe_hashimoto_langmuir | alpha | Fe | temperature_K=1973 | 0.23 | absolute=0.02 (point.sigma) | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 0.02 | -0.21 | 1.0607 | 10.5 | mismatch |
| fedkin-grossman-ghiorso-2006 | fedkin_2006_table3_fe_hashimoto_langmuir | alpha | Fe | temperature_K=2073 | 0.25 | absolute=0.02 (point.sigma) | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 0.02 | -0.23 | 1.09691 | 11.5 | mismatch |
| fedkin-grossman-ghiorso-2006 | fedkin_2006_table3_fe_hashimoto_langmuir | alpha | Fe | temperature_K=2173 | 0.24 | absolute=0.02 (point.sigma) | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 0.02 | -0.22 | 1.07918 | 11 | mismatch |
| fedkin-grossman-ghiorso-2006 | fedkin_2006_table3_fe_hashimoto_langmuir | alpha | Fe | temperature_K=2273 | 0.23 | absolute=0.02 (point.sigma) | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 0.02 | -0.21 | 1.0607 | 10.5 | mismatch |
| fedkin-grossman-ghiorso-2006 | fedkin_2006_table3_fe_hashimoto_langmuir_per_T_alpha_series | rate_series | Fe | temperature_K=1973 | 0.23 | absolute=0.02 (point.sigma) | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 0.02 | -0.21 | 1.0607 | 10.5 | mismatch |
| fedkin-grossman-ghiorso-2006 | fedkin_2006_table3_fe_hashimoto_langmuir_per_T_alpha_series | rate_series | Fe | temperature_K=2073 | 0.25 | absolute=0.02 (point.sigma) | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 0.02 | -0.23 | 1.09691 | 11.5 | mismatch |
| fedkin-grossman-ghiorso-2006 | fedkin_2006_table3_fe_hashimoto_langmuir_per_T_alpha_series | rate_series | Fe | temperature_K=2173 | 0.24 | absolute=0.02 (point.sigma) | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 0.02 | -0.22 | 1.07918 | 11 | mismatch |
| fedkin-grossman-ghiorso-2006 | fedkin_2006_table3_fe_hashimoto_langmuir_per_T_alpha_series | rate_series | Fe | temperature_K=2273 | 0.23 | absolute=0.02 (point.sigma) | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 0.02 | -0.21 | 1.0607 | 10.5 | mismatch |
| janaf-4th | Fe_normal_boiling_point | transition_point | Fe | property_kind=normal_boiling_point, target_pressure_Pa=101325 | 3134 | absolute=1.0 (documented default) | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 3135.15 | 1.14989 | — | 1.14989 | mismatch |
| kems-005-fedkin-2006 | fedkin_2006_fe_class_b1 | alpha | Fe | temperature_K=2123 | 0.24 | absolute=0.05 (documented default) | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 0.02 | -0.22 | 1.07918 | 4.4 | mismatch |
| kems-005-fedkin-2006 | fedkin_2006_fe_hashimoto_langmuir_table3 | alpha | Fe | temperature_K=1973 | 0.23 | absolute=0.02 (point.sigma) | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 0.02 | -0.21 | 1.0607 | 10.5 | mismatch |
| kems-005-fedkin-2006 | fedkin_2006_fe_hashimoto_langmuir_table3 | alpha | Fe | temperature_K=2073 | 0.25 | absolute=0.02 (point.sigma) | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 0.02 | -0.23 | 1.09691 | 11.5 | mismatch |
| kems-005-fedkin-2006 | fedkin_2006_fe_hashimoto_langmuir_table3 | alpha | Fe | temperature_K=2173 | 0.24 | absolute=0.02 (point.sigma) | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 0.02 | -0.22 | 1.07918 | 11 | mismatch |
| kems-005-fedkin-2006 | fedkin_2006_fe_hashimoto_langmuir_table3 | alpha | Fe | temperature_K=2273 | 0.23 | absolute=0.02 (point.sigma) | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 0.02 | -0.21 | 1.0607 | 10.5 | mismatch |
| kems-015-hashimoto-1983 | hashimoto_1983_fcmas_qualitative_volatility_order | rate_series | Fe | T_min_K=1873.15, T_max_K=2273.15, n_T=3 | 27 | absolute=0.0 (ordering pairwise count (exact integer)) | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 23 | -4 | 0.0696359 | — | ordering-fail |
| nist-webbook | HF_normal_boiling_point | transition_point | HF | property_kind=normal_boiling_point, target_pressure_Pa=101325 | 292.7 | absolute=1.0 (documented default) | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 293.042 | 0.341686 | — | 0.341686 | match |
| kems-006-zhang-2021 | zhang_2021_table4_K_evaporation_coefficients | alpha | K | temperature_K=1673.15 | 0.13 | absolute=0.1 (point.sigma) | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 0.13 | 0 | 0 | 0 | match |
| kems-006-zhang-2021 | zhang_2021_table4_K_evaporation_coefficients | alpha | K | temperature_K=1473.15 | 0.05 | absolute=0.02 (point.sigma) | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 0.13 | 0.08 | 0.414973 | 4 | mismatch |
| kems-012-sossi-2019 | sossi_2019_k_class_b1 | alpha | K | temperature_K=1698.15 | 1 | absolute=1.55 (observation.values.alpha_range); published alpha range half-width | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 0.13 | -0.87 | 0.886057 | 0.56129 | match |
| kems-012-sossi-2019 | sossi_2019_k_open_furnace_alpha_e_context | alpha | K | temperature_K=1698.15 | 1 | absolute=0.05 (documented default) | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 0.13 | -0.87 | 0.886057 | 17.4 | mismatch |
| kems-012-sossi-2019 | sossi_2019_k_table4_gamma_this_work | activity_coefficient | K | temperature_K=1673.15 | 0.00022 | relative_fraction=0.5 (documented default) | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 3.5e-05 | -0.000185 | 0.798355 | 1.68182 | mismatch |
| nist-webbook | K_normal_boiling_point | transition_point | K | property_kind=normal_boiling_point, target_pressure_Pa=101325 | 1037 | absolute=1.0 (documented default) | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 1029.75 | -7.25395 | — | 7.25395 | mismatch |
| janaf-4th | KCl_normal_boiling_point | transition_point | KCl | property_kind=normal_boiling_point, target_pressure_Pa=101325 | 1693.15 | absolute=1.0 (documented default) | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 1693.86 | 0.708951 | — | 0.708951 | match |
| fedkin-grossman-ghiorso-2006 | fedkin_2006_table3_mg_hashimoto_langmuir | alpha | Mg | temperature_K=1973 | 0.24 | absolute=0.01 (point.sigma) | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 0.2 | -0.04 | 0.0791812 | 4 | mismatch |
| fedkin-grossman-ghiorso-2006 | fedkin_2006_table3_mg_hashimoto_langmuir | alpha | Mg | temperature_K=2073 | 0.28 | absolute=0.01 (point.sigma) | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 0.2 | -0.08 | 0.146128 | 8 | mismatch |
| fedkin-grossman-ghiorso-2006 | fedkin_2006_table3_mg_hashimoto_langmuir | alpha | Mg | temperature_K=2173 | 0.28 | absolute=0.01 (point.sigma) | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 0.2 | -0.08 | 0.146128 | 8 | mismatch |
| fedkin-grossman-ghiorso-2006 | fedkin_2006_table3_mg_hashimoto_langmuir | alpha | Mg | temperature_K=2273 | 0.27 | absolute=0.01 (point.sigma) | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 0.2 | -0.07 | 0.130334 | 7 | mismatch |
| fedkin-grossman-ghiorso-2006 | fedkin_2006_table3_mg_hashimoto_langmuir_per_T_alpha_series | rate_series | Mg | temperature_K=1973 | 0.24 | absolute=0.01 (point.sigma) | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 0.2 | -0.04 | 0.0791812 | 4 | mismatch |
| fedkin-grossman-ghiorso-2006 | fedkin_2006_table3_mg_hashimoto_langmuir_per_T_alpha_series | rate_series | Mg | temperature_K=2073 | 0.28 | absolute=0.01 (point.sigma) | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 0.2 | -0.08 | 0.146128 | 8 | mismatch |
| fedkin-grossman-ghiorso-2006 | fedkin_2006_table3_mg_hashimoto_langmuir_per_T_alpha_series | rate_series | Mg | temperature_K=2173 | 0.28 | absolute=0.01 (point.sigma) | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 0.2 | -0.08 | 0.146128 | 8 | mismatch |
| fedkin-grossman-ghiorso-2006 | fedkin_2006_table3_mg_hashimoto_langmuir_per_T_alpha_series | rate_series | Mg | temperature_K=2273 | 0.27 | absolute=0.01 (point.sigma) | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 0.2 | -0.07 | 0.130334 | 7 | mismatch |
| kems-005-fedkin-2006 | fedkin_2006_mg_class_b1 | alpha | Mg | temperature_K=2123 | 0.24 | absolute=0.05 (documented default) | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 0.2 | -0.04 | 0.0791812 | 0.8 | match |
| kems-005-fedkin-2006 | fedkin_2006_mg_hashimoto_langmuir_table3 | alpha | Mg | temperature_K=1973 | 0.24 | absolute=0.01 (point.sigma) | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 0.2 | -0.04 | 0.0791812 | 4 | mismatch |
| kems-005-fedkin-2006 | fedkin_2006_mg_hashimoto_langmuir_table3 | alpha | Mg | temperature_K=2073 | 0.28 | absolute=0.01 (point.sigma) | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 0.2 | -0.08 | 0.146128 | 8 | mismatch |
| kems-005-fedkin-2006 | fedkin_2006_mg_hashimoto_langmuir_table3 | alpha | Mg | temperature_K=2173 | 0.28 | absolute=0.01 (point.sigma) | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 0.2 | -0.08 | 0.146128 | 8 | mismatch |
| kems-005-fedkin-2006 | fedkin_2006_mg_hashimoto_langmuir_table3 | alpha | Mg | temperature_K=2273 | 0.27 | absolute=0.01 (point.sigma) | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 0.2 | -0.07 | 0.130334 | 7 | mismatch |
| kems-008-schaefer-fegley-2004 | schaefer_fegley_2004_mg_forsterite_alpha_s_survey | alpha | Mg | temperature_K=2243 | 0.2 | absolute=0.0049999999999999906 (observation.values.alpha_range); published alpha range half-width | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 0.2 | 0 | 0 | 0 | match |
| kems-010-richter-2007 | richter_2007_mg_cai_langmuir_alpha_arrhenius | alpha | Mg | temperature_K=1873 | 0.0603586 | absolute=0.2054316518789593 (point.sigma); Arrhenius E uncertainty propagated as sigma_alpha/alpha=sigma_E/(R*T) | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 0.2 | 0.139641 | 0.520291 | 0.679746 | match |
| kems-010-richter-2007 | richter_2007_mg_cai_langmuir_alpha_arrhenius | alpha | Mg | temperature_K=2023 | 0.107388 | absolute=0.3383970051344181 (point.sigma); Arrhenius E uncertainty propagated as sigma_alpha/alpha=sigma_E/(R*T) | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 0.2 | 0.0926118 | 0.270073 | 0.273678 | match |
| kems-010-richter-2007 | richter_2007_mg_cai_langmuir_alpha_arrhenius | alpha | Mg | temperature_K=2173 | 0.176453 | absolute=0.5176490661392505 (point.sigma); Arrhenius E uncertainty propagated as sigma_alpha/alpha=sigma_E/(R*T) | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 0.2 | 0.0235469 | 0.0544007 | 0.0454881 | match |
| nist-webbook | Mg_normal_boiling_point | transition_point | Mg | property_kind=normal_boiling_point, target_pressure_Pa=101325 | 1363 | absolute=1.0 (documented default) | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 1360.7 | -2.3035 | — | 2.3035 | mismatch |
| richter-et-al-2007 | richter_2007_mg_cai_arrhenius_langmuir | alpha | Mg | temperature_K=1873 | 0.0603586 | absolute=0.2054316518789593 (point.sigma); Arrhenius E uncertainty propagated as sigma_alpha/alpha=sigma_E/(R*T) | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 0.2 | 0.139641 | 0.520291 | 0.679746 | match |
| richter-et-al-2007 | richter_2007_mg_cai_arrhenius_langmuir | alpha | Mg | temperature_K=2023 | 0.107388 | absolute=0.3383970051344181 (point.sigma); Arrhenius E uncertainty propagated as sigma_alpha/alpha=sigma_E/(R*T) | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 0.2 | 0.0926118 | 0.270073 | 0.273678 | match |
| richter-et-al-2007 | richter_2007_mg_cai_arrhenius_langmuir | alpha | Mg | temperature_K=2173 | 0.176453 | absolute=0.5176490661392505 (point.sigma); Arrhenius E uncertainty propagated as sigma_alpha/alpha=sigma_E/(R*T) | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 0.2 | 0.0235469 | 0.0544007 | 0.0454881 | match |
| janaf-4th | Mn_normal_boiling_point | transition_point | Mn | property_kind=normal_boiling_point, target_pressure_Pa=101325 | 2334.53 | absolute=1.0 (documented default) | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 2334.53 | -1.69166e-10 | — | 1.69166e-10 | match |
| nist-webbook | NO2_normal_boiling_point | transition_point | NO2 | property_kind=normal_boiling_point, target_pressure_Pa=101325 | 295.08 | absolute=1.0 (documented default) | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 293.47 | -1.61035 | — | 1.61035 | mismatch |
| janaf-4th | Na_normal_boiling_point | transition_point | Na | property_kind=normal_boiling_point, target_pressure_Pa=101325 | 1156 | absolute=1.0 (documented default) | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 1179.58 | 23.5847 | — | 23.5847 | mismatch |
| kems-ms2000-044 | ms2000_044_na2o_activity_xsio2_0709_t1673 | activity_coefficient | Na2O | temperature_K=1673 | 3.24e-08 | relative_fraction=0.5 (documented default) | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 2.03233e-07 | 1.70833e-07 | 0.797448 | 10.5452 | mismatch |
| janaf-4th | NaCl_normal_boiling_point | transition_point | NaCl | property_kind=normal_boiling_point, target_pressure_Pa=101325 | 1738.15 | absolute=1.0 (documented default) | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 1738.44 | 0.289953 | — | 0.289953 | match |
| janaf-4th | Si_normal_boiling_point | transition_point | Si | property_kind=normal_boiling_point, target_pressure_Pa=100000 | 3504.62 | absolute=1.0 (documented default) | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 2560.19 | -944.43 | — | 944.43 | mismatch |
| fedkin-grossman-ghiorso-2006 | fedkin_2006_table3_sio_hashimoto_langmuir | alpha | SiO | temperature_K=1973 | 0.12 | absolute=0.01 (point.sigma) | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 0.0803277 | -0.0396723 | 0.174316 | 3.96723 | mismatch |
| fedkin-grossman-ghiorso-2006 | fedkin_2006_table3_sio_hashimoto_langmuir | alpha | SiO | temperature_K=2073 | 0.17 | absolute=0.01 (point.sigma) | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 0.0879011 | -0.0820989 | 0.286455 | 8.20989 | mismatch |
| fedkin-grossman-ghiorso-2006 | fedkin_2006_table3_sio_hashimoto_langmuir | alpha | SiO | temperature_K=2173 | 0.2 | absolute=0.01 (point.sigma) | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 0.0953941 | -0.104606 | 0.321509 | 10.4606 | mismatch |
| fedkin-grossman-ghiorso-2006 | fedkin_2006_table3_sio_hashimoto_langmuir | alpha | SiO | temperature_K=2273 | 0.21 | absolute=0.01 (point.sigma) | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 0.102783 | -0.107217 | 0.310297 | 10.7217 | mismatch |
| fedkin-grossman-ghiorso-2006 | fedkin_2006_table3_sio_hashimoto_langmuir_per_T_alpha_series | rate_series | SiO | temperature_K=1973 | 0.12 | absolute=0.01 (point.sigma) | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 0.0803277 | -0.0396723 | 0.174316 | 3.96723 | mismatch |
| fedkin-grossman-ghiorso-2006 | fedkin_2006_table3_sio_hashimoto_langmuir_per_T_alpha_series | rate_series | SiO | temperature_K=2073 | 0.17 | absolute=0.01 (point.sigma) | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 0.0879011 | -0.0820989 | 0.286455 | 8.20989 | mismatch |
| fedkin-grossman-ghiorso-2006 | fedkin_2006_table3_sio_hashimoto_langmuir_per_T_alpha_series | rate_series | SiO | temperature_K=2173 | 0.2 | absolute=0.01 (point.sigma) | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 0.0953941 | -0.104606 | 0.321509 | 10.4606 | mismatch |
| fedkin-grossman-ghiorso-2006 | fedkin_2006_table3_sio_hashimoto_langmuir_per_T_alpha_series | rate_series | SiO | temperature_K=2273 | 0.21 | absolute=0.01 (point.sigma) | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 0.102783 | -0.107217 | 0.310297 | 10.7217 | mismatch |
| kems-005-fedkin-2006 | fedkin_2006_sio_hashimoto_table3_complete_b1 | alpha | SiO | temperature_K=1973 | 0.12 | absolute=0.01 (point.sigma) | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 0.0803277 | -0.0396723 | 0.174316 | 3.96723 | mismatch |
| kems-005-fedkin-2006 | fedkin_2006_sio_hashimoto_table3_complete_b1 | alpha | SiO | temperature_K=2073 | 0.17 | absolute=0.01 (point.sigma) | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 0.0879011 | -0.0820989 | 0.286455 | 8.20989 | mismatch |
| kems-005-fedkin-2006 | fedkin_2006_sio_hashimoto_table3_complete_b1 | alpha | SiO | temperature_K=2173 | 0.2 | absolute=0.01 (point.sigma) | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 0.0953941 | -0.104606 | 0.321509 | 10.4606 | mismatch |
| kems-005-fedkin-2006 | fedkin_2006_sio_hashimoto_table3_complete_b1 | alpha | SiO | temperature_K=2273 | 0.21 | absolute=0.01 (point.sigma) | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 0.102783 | -0.107217 | 0.310297 | 10.7217 | mismatch |
| kems-008-schaefer-fegley-2004 | schaefer_fegley_2004_sio_alpha_s_survey | alpha | SiO | temperature_K=2273 | 0.04 | absolute=0.08 (observation.values.alpha_range); published alpha range half-width | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 0.102783 | 0.0627833 | 0.409863 | 0.784792 | match |
| kems-008-schaefer-fegley-2004 | schaefer_fegley_2004_sio_alpha_s_survey | alpha | SiO | temperature_K=2273 | 0.2 | absolute=0.08 (observation.values.alpha_range); published alpha range half-width | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 0.102783 | -0.0972167 | 0.289107 | 1.21521 | mismatch |
| kems-010-richter-2007 | richter_2007_sio_cai_langmuir_alpha_arrhenius | alpha | SiO | temperature_K=1873 | 0.0687787 | absolute=0.1634119997736591 (point.sigma); Arrhenius E uncertainty propagated as sigma_alpha/alpha=sigma_E/(R*T) | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 0.072704 | 0.0039253 | 0.0241043 | 0.0240209 | match |
| kems-010-richter-2007 | richter_2007_sio_cai_langmuir_alpha_arrhenius | alpha | SiO | temperature_K=2023 | 0.106584 | absolute=0.2344569366817506 (point.sigma); Arrhenius E uncertainty propagated as sigma_alpha/alpha=sigma_E/(R*T) | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 0.0841227 | -0.0224611 | 0.102778 | 0.0958007 | match |
| kems-010-richter-2007 | richter_2007_sio_cai_langmuir_alpha_arrhenius | alpha | SiO | temperature_K=2173 | 0.155477 | absolute=0.3183997506581433 (point.sigma); Arrhenius E uncertainty propagated as sigma_alpha/alpha=sigma_E/(R*T) | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 0.0953941 | -0.0600825 | 0.212143 | 0.188701 | match |
| richter-et-al-2007 | richter_2007_si_cai_arrhenius_langmuir | alpha | SiO | temperature_K=1873 | 0.0687787 | absolute=0.1634119997736591 (point.sigma); Arrhenius E uncertainty propagated as sigma_alpha/alpha=sigma_E/(R*T) | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 0.072704 | 0.0039253 | 0.0241043 | 0.0240209 | match |
| richter-et-al-2007 | richter_2007_si_cai_arrhenius_langmuir | alpha | SiO | temperature_K=2023 | 0.106584 | absolute=0.2344569366817506 (point.sigma); Arrhenius E uncertainty propagated as sigma_alpha/alpha=sigma_E/(R*T) | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 0.0841227 | -0.0224611 | 0.102778 | 0.0958007 | match |
| richter-et-al-2007 | richter_2007_si_cai_arrhenius_langmuir | alpha | SiO | temperature_K=2173 | 0.155477 | absolute=0.3183997506581433 (point.sigma); Arrhenius E uncertainty propagated as sigma_alpha/alpha=sigma_E/(R*T) | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 0.0953941 | -0.0600825 | 0.212143 | 0.188701 | match |
| kems-ms2000-044 | ms2000_044_sio2_activity_k_system_xsio2_0500_t1473 | activity_coefficient | SiO2 | temperature_K=1473 | 0.00644 | relative_fraction=0.5 (documented default) | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 0.333333 | 0.326893 | 1.71399 | 101.52 | mismatch |
| kems-ms2000-044 | ms2000_044_sio2_activity_k_system_xsio2_0543_t1473 | activity_coefficient | SiO2 | temperature_K=1473 | 0.00969 | relative_fraction=0.5 (documented default) | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 0.372684 | 0.362994 | 1.58502 | 74.9213 | mismatch |
| kems-ms2000-044 | ms2000_044_sio2_activity_k_system_xsio2_0591_t1323 | activity_coefficient | SiO2 | temperature_K=1323 | 0.0111 | relative_fraction=0.5 (documented default) | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 0.419446 | 0.408346 | 1.57735 | 73.5759 | mismatch |
| kems-ms2000-044 | ms2000_044_sio2_activity_k_system_xsio2_0630_t1323 | activity_coefficient | SiO2 | temperature_K=1323 | 0.0187 | relative_fraction=0.5 (documented default) | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 0.459854 | 0.441154 | 1.39078 | 47.1822 | mismatch |
| kems-ms2000-044 | ms2000_044_sio2_activity_k_system_xsio2_0630_t1523 | activity_coefficient | SiO2 | temperature_K=1523 | 0.0267 | relative_fraction=0.5 (documented default) | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 0.459854 | 0.433154 | 1.23611 | 32.446 | mismatch |
| kems-ms2000-044 | ms2000_044_sio2_activity_k_system_xsio2_0674_t1373 | activity_coefficient | SiO2 | temperature_K=1373 | 0.101 | relative_fraction=0.5 (documented default) | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 0.508296 | 0.407296 | 0.701795 | 8.06526 | mismatch |
| kems-ms2000-044 | ms2000_044_sio2_activity_k_system_xsio2_0722_t1323 | activity_coefficient | SiO2 | temperature_K=1323 | 0.381 | relative_fraction=0.5 (documented default) | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 0.564945 | 0.183945 | 0.171081 | 0.965592 | match |
| kems-ms2000-044 | ms2000_044_sio2_activity_k_system_xsio2_0722_t1673 | activity_coefficient | SiO2 | temperature_K=1673 | 0.389 | relative_fraction=0.5 (documented default) | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 0.564945 | 0.175945 | 0.162057 | 0.904603 | match |
| kems-ms2000-044 | ms2000_044_sio2_activity_k_system_xsio2_0770_t1073 | activity_coefficient | SiO2 | temperature_K=1073 | 0.597 | relative_fraction=0.5 (documented default) | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 0.626016 | 0.0290163 | 0.0206113 | 0.0972069 | match |
| kems-ms2000-044 | ms2000_044_sio2_activity_k_system_xsio2_0811_t1173 | activity_coefficient | SiO2 | temperature_K=1173 | 0.745 | relative_fraction=0.5 (documented default) | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 0.682086 | -0.0629142 | 0.0383173 | 0.168897 | match |
| kems-ms2000-044 | ms2000_044_sio2_activity_k_system_xsio2_0848_t1573 | activity_coefficient | SiO2 | temperature_K=1573 | 0.808 | relative_fraction=0.5 (documented default) | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 0.736111 | -0.0718889 | 0.040468 | 0.177943 | match |
| kems-ms2000-044 | ms2000_044_sio2_activity_k_system_xsio2_0892_t1723 | activity_coefficient | SiO2 | temperature_K=1723 | 0.88 | relative_fraction=0.5 (documented default) | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 0.805054 | -0.0749458 | 0.0386576 | 0.170331 | match |
| kems-ms2000-044 | ms2000_044_sio2_activity_na_system_xsio2_0349_t1373 | activity_coefficient | SiO2 | temperature_K=1373 | 3.77e-06 | relative_fraction=0.5 (documented default) | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 0.211387 | 0.211383 | 4.74874 | 112140 | mismatch |
| kems-ms2000-044 | ms2000_044_sio2_activity_na_system_xsio2_0382_t1383 | activity_coefficient | SiO2 | temperature_K=1383 | 4.76e-05 | relative_fraction=0.5 (documented default) | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 0.236094 | 0.236046 | 3.69548 | 9917.91 | mismatch |
| kems-ms2000-044 | ms2000_044_sio2_activity_na_system_xsio2_0405_t1423 | activity_coefficient | SiO2 | temperature_K=1423 | 0.000138 | relative_fraction=0.5 (documented default) | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 0.253918 | 0.25378 | 3.26482 | 3677.98 | mismatch |
| kems-ms2000-044 | ms2000_044_sio2_activity_na_system_xsio2_0430_t1473 | activity_coefficient | SiO2 | temperature_K=1473 | 0.00038 | relative_fraction=0.5 (documented default) | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 0.273885 | 0.273505 | 2.85779 | 1439.5 | mismatch |
| kems-ms2000-044 | ms2000_044_sio2_activity_na_system_xsio2_0477_t1373 | activity_coefficient | SiO2 | temperature_K=1373 | 0.00175 | relative_fraction=0.5 (documented default) | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 0.313198 | 0.311448 | 2.25278 | 355.94 | mismatch |
| kems-ms2000-044 | ms2000_044_sio2_activity_na_system_xsio2_0524_t1573 | activity_coefficient | SiO2 | temperature_K=1573 | 0.0204 | relative_fraction=0.5 (documented default) | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 0.355014 | 0.334614 | 1.24061 | 32.8053 | mismatch |
| kems-ms2000-044 | ms2000_044_sio2_activity_na_system_xsio2_0573_t1473 | activity_coefficient | SiO2 | temperature_K=1473 | 0.0703 | relative_fraction=0.5 (documented default) | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 0.401542 | 0.331242 | 0.756775 | 9.42366 | mismatch |
| kems-ms2000-044 | ms2000_044_sio2_activity_na_system_xsio2_0625_t1573 | activity_coefficient | SiO2 | temperature_K=1573 | 0.199 | relative_fraction=0.5 (documented default) | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 0.454545 | 0.255545 | 0.358724 | 2.5683 | mismatch |
| kems-ms2000-044 | ms2000_044_sio2_activity_na_system_xsio2_0671_t1173 | activity_coefficient | SiO2 | temperature_K=1173 | 0.342 | relative_fraction=0.5 (documented default) | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 0.504891 | 0.162891 | 0.169171 | 0.952578 | match |
| kems-ms2000-044 | ms2000_044_sio2_activity_na_system_xsio2_0709_t1673 | activity_coefficient | SiO2 | temperature_K=1673 | 0.536 | relative_fraction=0.5 (documented default) | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 0.549187 | 0.0131867 | 0.0105552 | 0.049204 | match |
| kems-ms2000-044 | ms2000_044_sio2_activity_na_system_xsio2_0753_t1273 | activity_coefficient | SiO2 | temperature_K=1273 | 0.735 | relative_fraction=0.5 (documented default) | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 0.603849 | -0.131151 | 0.0853588 | 0.356873 | match |
| kems-ms2000-044 | ms2000_044_sio2_activity_na_system_xsio2_0805_t1473 | activity_coefficient | SiO2 | temperature_K=1473 | 0.86 | relative_fraction=0.5 (documented default) | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 0.67364 | -0.18636 | 0.10607 | 0.433395 | match |
| ts1985 | ts1985_sio2_gibbs_duhem_1200C_X0500 | activity_coefficient | SiO2 | temperature_K=1473.15 | 0.00601 | relative_fraction=0.5 (documented default) | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 0.333333 | 0.327323 | 1.744 | 108.926 | mismatch |
| janaf-4th | Ti_normal_boiling_point | transition_point | Ti | property_kind=normal_boiling_point, target_pressure_Pa=101325 | 3630.96 | absolute=1.0 (documented default) | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 3560.15 | -70.806 | — | 70.806 | mismatch |
| berkowitz-chupka-inghram-1957 | BIC57_VO_absolute_points | psat_series | VO | temperature_K=1875 | 0.155 | log10_decades=0.5 (documented default) | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 0.0120686 | -0.142931 | 1.10868 | 2.21735 | mismatch |
| berkowitz-chupka-inghram-1957 | BIC57_VO_absolute_points | psat_series | VO | temperature_K=1876 | 0.116 | log10_decades=0.5 (documented default) | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 0.0122916 | -0.103708 | 0.974849 | 1.9497 | mismatch |

Assumption-only engine diagnostics (visible negative results, but excluded from comparable coverage, headlines, and residual pins):

| Source | Observation | Type | Species | Coordinate | Literature | Assumption-only engine value | Raw residual dex | Typed gaps | Status |
|---|---|---|---|---|---:|---:|---:|---|---|
| kems-041-sossi-fegley-2018 | sossi_fegley_2018_table2_gamma_Al__AlO_1_5_ | activity_coefficient | Al | temperature_K=1673 | 0.32187 | 0.322 | 0.000175994 | typed-refusal:self_agreement_excluded | self-agreement-excluded (excluded) |
| kems-041-sossi-fegley-2018 | sossi_fegley_2018_table2_gamma_Ca_CaO | activity_coefficient | Ca | temperature_K=1873 | 0.0122474 | 0.012 | 0.00886438 | typed-refusal:self_agreement_excluded | self-agreement-excluded (excluded) |
| kems-041-sossi-fegley-2018 | sossi_fegley_2018_table2_gamma_Cr__CrO_1_5_ | activity_coefficient | Cr | temperature_K=1773 | 31.0805 | 31.1 | 0.000271826 | typed-refusal:self_agreement_excluded | self-agreement-excluded (excluded) |
| kems-022-demaria-1971 | demaria_1971_fe_activity_multi_rotating_cell | activity_coefficient | Fe | window=temperature-not-stated | 1 | 0.135318 | 0.868646 | typed-refusal:missing_capability:documented_melt_activity_coefficient:FeO; typed-refusal:missing_condition:source_sample_composition; typed-refusal:missing_capability:reference_state_conversion:pure_Fe_to_FeO; typed-refusal:unsupported_observable:qualitative_activity_not_point | assumed-input (excluded) |
| kems-022-demaria-1971 | demaria_1971_fe_lunar_basalt_kems_main_cell | activity_coefficient | Fe | temperature_K=1550 | 1 | 0.135318 | 0.868646 | typed-refusal:missing_capability:documented_melt_activity_coefficient:FeO; typed-refusal:missing_condition:source_sample_composition; typed-refusal:missing_capability:reference_state_conversion:pure_Fe_to_FeO; typed-refusal:unsupported_observable:qualitative_activity_not_point | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1104_000_1302K | activity_coefficient | K2O | temperature_K=1302 | 1.8869e-16 | 3.0622e-10 | 6.21028 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1104_001_1356K | activity_coefficient | K2O | temperature_K=1356 | 3.33576e-15 | 3.06096e-10 | 4.96266 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1104_002_1414K | activity_coefficient | K2O | temperature_K=1414 | 4.73593e-14 | 3.05972e-10 | 3.81028 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1104_003_1475K | activity_coefficient | K2O | temperature_K=1475 | 4.79233e-13 | 3.04978e-10 | 2.80372 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1104_004_1504K | activity_coefficient | K2O | temperature_K=1504 | 1.35549e-12 | 3.03986e-10 | 2.35076 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1104_005_1419K | activity_coefficient | K2O | temperature_K=1419 | 4.17466e-14 | 3.0349e-10 | 3.86152 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1104_006_1366K | activity_coefficient | K2O | temperature_K=1366 | 2.82572e-15 | 3.03242e-10 | 5.03066 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1104_007_1311K | activity_coefficient | K2O | temperature_K=1311 | 1.58223e-16 | 3.03242e-10 | 6.28252 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1104_008_1320K | activity_coefficient | K2O | temperature_K=1320 | 2.52992e-16 | 3.03242e-10 | 6.07868 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1104_009_1359K | activity_coefficient | K2O | temperature_K=1359 | 2.09532e-15 | 3.02994e-10 | 5.16018 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1104_010_1414K | activity_coefficient | K2O | temperature_K=1414 | 3.28134e-14 | 3.02499e-10 | 3.96467 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1104_011_1462K | activity_coefficient | K2O | temperature_K=1462 | 3.36319e-13 | 3.01881e-10 | 2.95308 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1104_012_1493K | activity_coefficient | K2O | temperature_K=1493 | 1.18002e-12 | 3.00522e-10 | 2.40598 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1104_013_1407K | activity_coefficient | K2O | temperature_K=1407 | 3.76898e-14 | 2.98673e-10 | 3.89897 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1104_014_1473K | activity_coefficient | K2O | temperature_K=1473 | 3.42445e-13 | 2.97073e-10 | 2.93827 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1104_015_1524K | activity_coefficient | K2O | temperature_K=1524 | 2.38271e-12 | 2.95109e-10 | 2.09291 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1104_016_1571K | activity_coefficient | K2O | temperature_K=1571 | 1.58223e-11 | 2.93762e-10 | 1.26873 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1104_017_1571K | activity_coefficient | K2O | temperature_K=1571 | 1.13064e-11 | 2.8864e-10 | 1.40703 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1104_018_1601K | activity_coefficient | K2O | temperature_K=1601 | 2.91228e-11 | 2.8864e-10 | 0.996122 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1104_019_1601K | activity_coefficient | K2O | temperature_K=1601 | 2.49749e-11 | 2.82707e-10 | 1.05383 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1104_020_1640K | activity_coefficient | K2O | temperature_K=1640 | 8.98522e-11 | 2.82707e-10 | 0.497808 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1104_021_1640K | activity_coefficient | K2O | temperature_K=1640 | 7.81545e-11 | 2.74315e-10 | 0.545295 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1104_022_1594K | activity_coefficient | K2O | temperature_K=1594 | 1.8869e-11 | 2.74315e-10 | 1.1625 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1104_023_1594K | activity_coefficient | K2O | temperature_K=1594 | 1.50955e-11 | 2.70392e-10 | 1.25315 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1104_024_1542K | activity_coefficient | K2O | temperature_K=1542 | 2.66853e-12 | 2.67317e-10 | 2.00075 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1104_025_1466K | activity_coefficient | K2O | temperature_K=1466 | 1.22378e-13 | 2.63903e-10 | 3.33374 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1104_026_1436K | activity_coefficient | K2O | temperature_K=1436 | 2.53956e-14 | 2.63316e-10 | 4.01572 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1104_027_1396K | activity_coefficient | K2O | temperature_K=1396 | 3.25978e-15 | 2.62964e-10 | 4.90671 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1110_000_1352K | activity_coefficient | K2O | temperature_K=1352 | 2.37808e-16 | 2.6273e-10 | 6.04328 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1110_001_1389K | activity_coefficient | K2O | temperature_K=1389 | 2.0665e-15 | 2.6273e-10 | 5.10427 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1110_002_1456K | activity_coefficient | K2O | temperature_K=1456 | 5.88093e-14 | 2.62378e-10 | 3.64948 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1110_003_1508K | activity_coefficient | K2O | temperature_K=1508 | 6.00382e-13 | 2.61559e-10 | 2.63914 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1110_004_1560K | activity_coefficient | K2O | temperature_K=1560 | 4.14866e-12 | 2.59923e-10 | 1.79694 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1110_005_1615K | activity_coefficient | K2O | temperature_K=1615 | 2.40958e-11 | 2.56198e-10 | 1.02663 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1110_006_1657K | activity_coefficient | K2O | temperature_K=1657 | 7.81545e-11 | 2.4973e-10 | 0.504516 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1110_007_1630K | activity_coefficient | K2O | temperature_K=1630 | 2.4653e-11 | 2.44579e-10 | 0.996549 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1110_008_1580K | activity_coefficient | K2O | temperature_K=1580 | 5.6309e-12 | 2.42872e-10 | 1.6348 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1110_009_1547K | activity_coefficient | K2O | temperature_K=1547 | 1.20607e-12 | 2.41736e-10 | 2.30197 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1110_010_1513K | activity_coefficient | K2O | temperature_K=1513 | 3.93865e-13 | 2.41056e-10 | 2.78677 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1110_011_1308K | activity_coefficient | K2O | temperature_K=1308 | 4.48671e-17 | 2.40829e-10 | 6.72978 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1110_012_1342K | activity_coefficient | K2O | temperature_K=1342 | 1.75986e-16 | 2.40716e-10 | 6.13603 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1110_013_1369K | activity_coefficient | K2O | temperature_K=1369 | 7.03913e-16 | 2.40716e-10 | 5.53399 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1110_014_1388K | activity_coefficient | K2O | temperature_K=1388 | 1.63743e-15 | 2.40602e-10 | 5.16714 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1110_015_1416K | activity_coefficient | K2O | temperature_K=1416 | 6.80847e-15 | 2.40602e-10 | 4.54825 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1110_016_1292K | activity_coefficient | K2O | temperature_K=1292 | 1.71858e-17 | 2.40489e-10 | 7.14593 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1110_017_1413K | activity_coefficient | K2O | temperature_K=1413 | 4.37593e-15 | 2.40376e-10 | 4.73982 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1110_018_1379K | activity_coefficient | K2O | temperature_K=1379 | 9.88135e-16 | 2.4015e-10 | 5.38567 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1110_019_1400K | activity_coefficient | K2O | temperature_K=1400 | 1.89857e-15 | 2.39923e-10 | 5.10165 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1110_020_1411K | activity_coefficient | K2O | temperature_K=1411 | 3.53034e-15 | 2.3981e-10 | 4.83205 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1110_021_1432K | activity_coefficient | K2O | temperature_K=1432 | 9.61977e-15 | 2.39697e-10 | 4.3965 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1110_022_1489K | activity_coefficient | K2O | temperature_K=1489 | 1.089e-13 | 2.39358e-10 | 3.34202 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1110_023_1537K | activity_coefficient | K2O | temperature_K=1537 | 7.98253e-13 | 2.38229e-10 | 2.47485 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1110_024_1585K | activity_coefficient | K2O | temperature_K=1585 | 5.2427e-12 | 2.35078e-10 | 1.65166 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1110_025_1625K | activity_coefficient | K2O | temperature_K=1625 | 1.75332e-11 | 2.30495e-10 | 1.1188 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1110_026_1663K | activity_coefficient | K2O | temperature_K=1663 | 5.37066e-11 | 2.24073e-10 | 0.620362 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1110_027_1720K | activity_coefficient | K2O | temperature_K=1720 | 1.98147e-10 | 2.13819e-10 | 0.0330592 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1110_028_1674K | activity_coefficient | K2O | temperature_K=1674 | 4.61261e-11 | 2.05563e-10 | 0.648999 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1110_029_1624K | activity_coefficient | K2O | temperature_K=1624 | 1.0811e-11 | 2.0196e-10 | 1.2714 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1110_030_1585K | activity_coefficient | K2O | temperature_K=1585 | 2.24703e-12 | 1.99537e-10 | 1.94841 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1110_031_1550K | activity_coefficient | K2O | temperature_K=1550 | 5.0216e-13 | 1.97858e-10 | 2.59551 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1110_032_1503K | activity_coefficient | K2O | temperature_K=1503 | 7.72016e-14 | 1.97021e-10 | 3.40689 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1110_033_1450K | activity_coefficient | K2O | temperature_K=1450 | 7.66017e-15 | 1.96707e-10 | 4.40958 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1110_034_1408K | activity_coefficient | K2O | temperature_K=1408 | 1.1625e-15 | 1.96603e-10 | 5.2282 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1110_035_1366K | activity_coefficient | K2O | temperature_K=1366 | 1.55777e-16 | 1.96394e-10 | 6.10062 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1110_036_1343K | activity_coefficient | K2O | temperature_K=1343 | 4.45793e-17 | 1.96394e-10 | 6.64399 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1110_037_1289K | activity_coefficient | K2O | temperature_K=1289 | 3.67616e-18 | 1.96394e-10 | 7.72773 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1115_000_1259K | activity_coefficient | K2O | temperature_K=1259 | 1.50333e-18 | 1.96394e-10 | 8.11607 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1115_001_1283K | activity_coefficient | K2O | temperature_K=1283 | 3.67616e-18 | 1.96394e-10 | 7.72773 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1115_002_1320K | activity_coefficient | K2O | temperature_K=1320 | 1.62294e-17 | 1.96289e-10 | 7.08259 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1115_003_1404K | activity_coefficient | K2O | temperature_K=1404 | 1.37652e-15 | 1.96289e-10 | 5.15411 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1115_004_1435K | activity_coefficient | K2O | temperature_K=1435 | 3.69079e-15 | 1.96185e-10 | 4.72555 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1115_005_1496K | activity_coefficient | K2O | temperature_K=1496 | 6.79802e-14 | 1.95768e-10 | 3.45936 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1115_006_1544K | activity_coefficient | K2O | temperature_K=1544 | 3.67616e-13 | 1.95142e-10 | 2.72496 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1115_007_1592K | activity_coefficient | K2O | temperature_K=1592 | 2.58488e-12 | 1.9296e-10 | 1.87303 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1115_008_1631K | activity_coefficient | K2O | temperature_K=1631 | 9.13651e-12 | 1.89963e-10 | 1.31789 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1115_009_1681K | activity_coefficient | K2O | temperature_K=1681 | 3.57245e-11 | 1.86474e-10 | 0.717651 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1115_010_1723K | activity_coefficient | K2O | temperature_K=1723 | 1.14229e-10 | 1.80481e-10 | 0.198656 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1115_011_1719K | activity_coefficient | K2O | temperature_K=1719 | 9.51676e-11 | 1.72682e-10 | 0.258758 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1115_012_1765K | activity_coefficient | K2O | temperature_K=1765 | 2.79122e-10 | 1.64346e-10 | 0.230036 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1115_013_1722K | activity_coefficient | K2O | temperature_K=1722 | 6.59654e-11 | 1.57895e-10 | 0.379053 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1115_014_1677K | activity_coefficient | K2O | temperature_K=1677 | 1.90058e-11 | 1.54001e-10 | 0.908637 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1115_015_1633K | activity_coefficient | K2O | temperature_K=1633 | 3.85263e-12 | 1.51365e-10 | 1.59427 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1115_016_1595K | activity_coefficient | K2O | temperature_K=1595 | 1.04281e-12 | 1.49867e-10 | 2.1575 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1115_017_1549K | activity_coefficient | K2O | temperature_K=1549 | 1.90781e-13 | 1.48748e-10 | 2.89192 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1115_018_1517K | activity_coefficient | K2O | temperature_K=1517 | 4.69844e-14 | 1.47911e-10 | 3.49805 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1115_019_1482K | activity_coefficient | K2O | temperature_K=1482 | 1.10512e-14 | 1.47447e-10 | 4.12522 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1115_020_1445K | activity_coefficient | K2O | temperature_K=1445 | 2.36558e-15 | 1.47261e-10 | 4.79415 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1115_021_1411K | activity_coefficient | K2O | temperature_K=1411 | 6.03304e-16 | 1.47076e-10 | 5.387 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1115_022_1388K | activity_coefficient | K2O | temperature_K=1388 | 2.06223e-16 | 1.47076e-10 | 5.8532 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1115_023_1361K | activity_coefficient | K2O | temperature_K=1361 | 4.9624e-17 | 1.47076e-10 | 6.47185 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1115_024_1328K | activity_coefficient | K2O | temperature_K=1328 | 9.49336e-18 | 1.46983e-10 | 7.18985 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1115_025_1292K | activity_coefficient | K2O | temperature_K=1292 | 2.47146e-18 | 1.46983e-10 | 7.77431 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1122_000_1294K | activity_coefficient | K2O | temperature_K=1294 | 1.31545e-18 | 1.4689e-10 | 8.04792 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1122_001_1325K | activity_coefficient | K2O | temperature_K=1325 | 3.24266e-18 | 1.4689e-10 | 7.65609 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1122_002_1369K | activity_coefficient | K2O | temperature_K=1369 | 2.34346e-17 | 1.4689e-10 | 6.79714 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1122_003_1403K | activity_coefficient | K2O | temperature_K=1403 | 1.50358e-16 | 1.4689e-10 | 5.98987 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1122_004_1465K | activity_coefficient | K2O | temperature_K=1465 | 3.4128e-15 | 1.46798e-10 | 4.63361 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1122_005_1506K | activity_coefficient | K2O | temperature_K=1506 | 1.88699e-14 | 1.4652e-10 | 3.89013 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1122_006_1550K | activity_coefficient | K2O | temperature_K=1550 | 1.0921e-13 | 1.45965e-10 | 3.12599 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1122_007_1585K | activity_coefficient | K2O | temperature_K=1585 | 5.09945e-13 | 1.44858e-10 | 2.45342 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1122_008_1626K | activity_coefficient | K2O | temperature_K=1626 | 1.86823e-12 | 1.43205e-10 | 1.88453 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1122_009_1669K | activity_coefficient | K2O | temperature_K=1669 | 7.70765e-12 | 1.40557e-10 | 1.26093 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1122_010_1717K | activity_coefficient | K2O | temperature_K=1717 | 3.29867e-11 | 1.37029e-10 | 0.618475 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1122_011_1760K | activity_coefficient | K2O | temperature_K=1760 | 8.47189e-11 | 1.32385e-10 | 0.193858 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1122_012_1705K | activity_coefficient | K2O | temperature_K=1705 | 2.00511e-11 | 1.27808e-10 | 0.804422 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1122_013_1666K | activity_coefficient | K2O | temperature_K=1666 | 6.93437e-12 | 1.24853e-10 | 1.25539 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1122_014_1621K | activity_coefficient | K2O | temperature_K=1621 | 1.75119e-12 | 1.23128e-10 | 1.84703 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1122_015_1568K | activity_coefficient | K2O | temperature_K=1568 | 2.67905e-13 | 1.22356e-10 | 2.65964 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1122_016_1527K | activity_coefficient | K2O | temperature_K=1527 | 5.18009e-14 | 1.21927e-10 | 3.37176 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1122_017_1496K | activity_coefficient | K2O | temperature_K=1496 | 1.20173e-14 | 1.21756e-10 | 4.00568 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1123_000_1337K | activity_coefficient | K2O | temperature_K=1337 | 1.67035e-17 | 1.21585e-10 | 6.86207 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1123_001_1388K | activity_coefficient | K2O | temperature_K=1388 | 1.52753e-16 | 1.21585e-10 | 5.90089 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1123_002_1428K | activity_coefficient | K2O | temperature_K=1428 | 8.81209e-16 | 1.21414e-10 | 5.13919 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1123_003_1469K | activity_coefficient | K2O | temperature_K=1469 | 5.38349e-15 | 1.0468e-10 | 4.2888 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1123_004_1521K | activity_coefficient | K2O | temperature_K=1521 | 3.70377e-14 | 1.20731e-10 | 3.51317 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1123_005_1562K | activity_coefficient | K2O | temperature_K=1562 | 2.04056e-13 | 1.20135e-10 | 2.76992 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1123_006_1620K | activity_coefficient | K2O | temperature_K=1620 | 1.41244e-12 | 1.18862e-10 | 1.92507 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1123_007_1660K | activity_coefficient | K2O | temperature_K=1660 | 6.00147e-12 | 1.16668e-10 | 1.2887 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1123_008_1713K | activity_coefficient | K2O | temperature_K=1713 | 2.65379e-11 | 1.12997e-10 | 0.629199 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1123_009_1758K | activity_coefficient | K2O | temperature_K=1758 | 8.47189e-11 | 1.06936e-10 | 0.101144 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1123_010_1568K | activity_coefficient | K2O | temperature_K=1568 | 2.13206e-13 | 1.02923e-10 | 2.68371 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1123_011_1352K | activity_coefficient | K2O | temperature_K=1352 | 2.79701e-17 | 1.02605e-10 | 6.56447 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1123_012_1403K | activity_coefficient | K2O | temperature_K=1403 | 2.47332e-16 | 1.02605e-10 | 5.61789 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1123_013_1451K | activity_coefficient | K2O | temperature_K=1451 | 5.41808e-18 | 1.02446e-10 | 7.27665 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1123_014_1484K | activity_coefficient | K2O | temperature_K=1484 | 6.86731e-15 | 1.02366e-10 | 4.17337 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1123_015_1535K | activity_coefficient | K2O | temperature_K=1535 | 5.1208e-14 | 1.02128e-10 | 3.29981 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1123_016_1584K | activity_coefficient | K2O | temperature_K=1584 | 2.89676e-13 | 1.01494e-10 | 2.54453 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1123_017_1634K | activity_coefficient | K2O | temperature_K=1634 | 1.70241e-12 | 1.00232e-10 | 1.76994 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1123_018_1681K | activity_coefficient | K2O | temperature_K=1681 | 7.1231e-12 | 9.77275e-11 | 1.13735 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1123_019_1722K | activity_coefficient | K2O | temperature_K=1722 | 2.36243e-11 | 9.44825e-11 | 0.601993 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1123_020_1770K | activity_coefficient | K2O | temperature_K=1770 | 9.16036e-11 | 8.98548e-11 | 0.0083711 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1123_021_1717K | activity_coefficient | K2O | temperature_K=1717 | 2.04786e-11 | 8.5476e-11 | 0.620544 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1123_022_1679K | activity_coefficient | K2O | temperature_K=1679 | 5.05481e-12 | 8.29239e-11 | 1.21498 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1123_023_1634K | activity_coefficient | K2O | temperature_K=1634 | 1.14159e-12 | 8.12654e-11 | 1.8524 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1123_024_1587K | activity_coefficient | K2O | temperature_K=1587 | 1.78038e-13 | 8.02631e-11 | 2.654 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1123_025_1537K | activity_coefficient | K2O | temperature_K=1537 | 2.84749e-14 | 7.98352e-11 | 3.44773 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1123_026_1484K | activity_coefficient | K2O | temperature_K=1484 | 4.24211e-15 | 7.96928e-11 | 4.27384 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1123_027_1446K | activity_coefficient | K2O | temperature_K=1446 | 8.1397e-16 | 7.95505e-11 | 4.99003 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1123_028_1398K | activity_coefficient | K2O | temperature_K=1398 | 1.02814e-16 | 7.95505e-11 | 5.88859 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1126_000_1409K | activity_coefficient | K2O | temperature_K=1409 | 1.62557e-16 | 7.94084e-11 | 5.68886 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1126_001_1443K | activity_coefficient | K2O | temperature_K=1443 | 6.17109e-16 | 7.92663e-11 | 5.10873 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1126_002_1487K | activity_coefficient | K2O | temperature_K=1487 | 3.85553e-15 | 7.91244e-11 | 4.31223 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1126_003_1538K | activity_coefficient | K2O | temperature_K=1538 | 3.28134e-14 | 7.88408e-11 | 3.3807 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1126_004_1583K | activity_coefficient | K2O | temperature_K=1583 | 1.90781e-13 | 7.84163e-11 | 2.61387 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1126_005_1637K | activity_coefficient | K2O | temperature_K=1637 | 1.28628e-12 | 7.73595e-11 | 1.77918 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1126_006_1676K | activity_coefficient | K2O | temperature_K=1676 | 5.17961e-12 | 7.56122e-11 | 1.16429 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1126_007_1732K | activity_coefficient | K2O | temperature_K=1732 | 2.81583e-11 | 7.1627e-11 | 0.40547 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1126_008_1783K | activity_coefficient | K2O | temperature_K=1783 | 1.04437e-10 | 6.62227e-11 | 0.197849 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1126_009_1743K | activity_coefficient | K2O | temperature_K=1743 | 3.32738e-11 | 6.13884e-11 | 0.265984 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1126_010_1702K | activity_coefficient | K2O | temperature_K=1702 | 8.44595e-12 | 5.83191e-11 | 0.839162 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1126_011_1656K | activity_coefficient | K2O | temperature_K=1656 | 1.78417e-12 | 5.5927e-11 | 1.49619 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1126_012_1613K | activity_coefficient | K2O | temperature_K=1613 | 3.61223e-13 | 5.42375e-11 | 2.17652 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1126_013_1656K | activity_coefficient | K2O | temperature_K=1656 | 1.70241e-12 | 5.28673e-11 | 1.49212 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1126_014_1612K | activity_coefficient | K2O | temperature_K=1612 | 3.42445e-13 | 5.15715e-11 | 2.17782 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1129_000_1589K | activity_coefficient | K2O | temperature_K=1589 | 1.69835e-13 | 4.91956e-11 | 2.4619 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1129_001_1636K | activity_coefficient | K2O | temperature_K=1636 | 1.05486e-12 | 4.82255e-11 | 1.66008 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1129_002_1681K | activity_coefficient | K2O | temperature_K=1681 | 4.5156e-12 | 4.6479e-11 | 1.01254 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1129_003_1723K | activity_coefficient | K2O | temperature_K=1723 | 1.53958e-11 | 4.40508e-11 | 0.456551 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1129_004_1767K | activity_coefficient | K2O | temperature_K=1767 | 6.03304e-11 | 4.07307e-11 | 0.170614 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1129_005_1800K | activity_coefficient | K2O | temperature_K=1800 | 1.66251e-10 | 3.58762e-11 | 0.665958 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1129_006_1741K | activity_coefficient | K2O | temperature_K=1741 | 2.85943e-11 | 3.14939e-11 | 0.0419472 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-042-plante-1979 | plante1979_table2_k2o_s1129_007_1693K | activity_coefficient | K2O | temperature_K=1693 | 6.78556e-12 | 2.89741e-11 | 0.630424 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-ms2000-044 | ms2000_044_k2o_activity_xsio2_0500_t1473 | activity_coefficient | K2O | temperature_K=1473 | 5.38e-08 | 5.44444e-10 | 1.99483 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-ms2000-044 | ms2000_044_k2o_activity_xsio2_0543_t1473 | activity_coefficient | K2O | temperature_K=1473 | 3.5e-08 | 4.82069e-10 | 1.86096 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-ms2000-044 | ms2000_044_k2o_activity_xsio2_0591_t1323 | activity_coefficient | K2O | temperature_K=1323 | 1.35e-09 | 4.12877e-10 | 0.514513 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-ms2000-044 | ms2000_044_k2o_activity_xsio2_0630_t1323 | activity_coefficient | K2O | temperature_K=1323 | 5.84e-10 | 3.57403e-10 | 0.213254 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-ms2000-044 | ms2000_044_k2o_activity_xsio2_0630_t1523 | activity_coefficient | K2O | temperature_K=1523 | 2.01e-08 | 3.57403e-10 | 1.75004 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-ms2000-044 | ms2000_044_k2o_activity_xsio2_0674_t1373 | activity_coefficient | K2O | temperature_K=1373 | 7.54e-11 | 2.96172e-10 | 0.594173 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-ms2000-044 | ms2000_044_k2o_activity_xsio2_0722_t1323 | activity_coefficient | K2O | temperature_K=1323 | 1.15e-12 | 2.31859e-10 | 2.30453 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-ms2000-044 | ms2000_044_k2o_activity_xsio2_0722_t1673 | activity_coefficient | K2O | temperature_K=1673 | 8.8e-10 | 2.31859e-10 | 0.579259 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-ms2000-044 | ms2000_044_k2o_activity_xsio2_0770_t1073 | activity_coefficient | K2O | temperature_K=1073 | 1.87e-16 | 1.71333e-10 | 5.962 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-ms2000-044 | ms2000_044_k2o_activity_xsio2_0811_t1173 | activity_coefficient | K2O | temperature_K=1173 | 2.5e-15 | 1.2381e-10 | 4.69482 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-ms2000-044 | ms2000_044_k2o_activity_xsio2_0848_t1573 | activity_coefficient | K2O | temperature_K=1573 | 1.45e-11 | 8.53057e-11 | 0.76961 | typed-refusal:missing_capability:documented_melt_activity_coefficient:K2O | assumed-input (excluded) |
| kems-010-richter-2007 | richter_2007_mg_rate_series_geometry | rate_series | Mg | temperature_K=2173.15 | 0.00251189 | 0.536913 | 2.3299 | typed-refusal:missing_condition:pO2_boundary | assumed-input (excluded) |
| kems-010-richter-2007 | richter_2007_mg_rate_series_geometry | rate_series | Mg | temperature_K=2073.15 | 0.000630957 | 0.0799963 | 2.10307 | typed-refusal:missing_condition:pO2_boundary | assumed-input (excluded) |
| kems-010-richter-2007 | richter_2007_mg_rate_series_geometry | rate_series | Mg | temperature_K=1973.15 | 0.000125893 | 0.0097594 | 1.88942 | typed-refusal:missing_condition:pO2_boundary | assumed-input (excluded) |
| kems-010-richter-2007 | richter_2007_mg_rate_series_geometry | rate_series | Mg | temperature_K=1873.15 | 1.58489e-05 | 0.000926307 | 1.76675 | typed-refusal:missing_condition:pO2_boundary | assumed-input (excluded) |
| kems-041-sossi-fegley-2018 | sossi_fegley_2018_table2_gamma_Mg_MgO | activity_coefficient | Mg | temperature_K=1873 | 1 | 1 | 0 | typed-refusal:self_agreement_excluded | self-agreement-excluded (excluded) |
| kems-041-sossi-fegley-2018 | sossi_fegley_2018_table2_gamma_Mn_MnO | activity_coefficient | Mn | temperature_K=1873 | 1.89737 | 1.9 | 0.000602351 | typed-refusal:self_agreement_excluded | self-agreement-excluded (excluded) |
| kems-006-zhang-2021 | zhang_2021_table4_this_study_evaporation_coefficients | alpha | Na | temperature_K=1673.15 | 0.14 | 1 | 0.853872 | typed-refusal:analytical_upper_bound_not_measurement | assumed-input (excluded) |
| kems-006-zhang-2021 | zhang_2021_table4_this_study_evaporation_coefficients | alpha | Na | temperature_K=1473.15 | 0.08 | 1 | 1.09691 | typed-refusal:analytical_upper_bound_not_measurement | assumed-input (excluded) |
| kems-012-sossi-2019 | sossi_2019_na_alpha_e_authors_adopted_unity | alpha | Na | temperature_K=1698.15 | 1 | 1 | 0 | typed-refusal:analytical_upper_bound_not_measurement | assumed-input (excluded) |
| kems-012-sossi-2019 | sossi_2019_na_class_and_transport_b1 | alpha | Na | temperature_K=1698.15 | 1 | 1 | 0 | typed-refusal:analytical_upper_bound_not_measurement | assumed-input (excluded) |
| kems-012-sossi-2019 | sossi_2019_na_table4_gamma_this_work | activity_coefficient | Na | temperature_K=1673.15 | 0.001 | 0.001 | 0 | typed-refusal:self_agreement_excluded | self-agreement-excluded (excluded) |
| sossi-et-al-2019 | sossi_2019_na_open_furnace_apparent | alpha | Na | temperature_K=1698.15 | 1 | 1 | 0 | typed-refusal:analytical_upper_bound_not_measurement | assumed-input (excluded) |
| kems-ms2000-044 | ms2000_044_na2o_activity_xsio2_0349_t1373 | activity_coefficient | Na2O | temperature_K=1373 | 8.62e-05 | 6.2191e-07 | 2.14178 | typed-refusal:missing_capability:documented_melt_activity_coefficient:Na2O | assumed-input (excluded) |
| kems-ms2000-044 | ms2000_044_na2o_activity_xsio2_0382_t1383 | activity_coefficient | Na2O | temperature_K=1383 | 2.43e-05 | 5.83552e-07 | 1.61953 | typed-refusal:missing_capability:documented_melt_activity_coefficient:Na2O | assumed-input (excluded) |
| kems-ms2000-044 | ms2000_044_na2o_activity_xsio2_0405_t1423 | activity_coefficient | Na2O | temperature_K=1423 | 2.01e-05 | 5.56638e-07 | 1.55762 | typed-refusal:missing_capability:documented_melt_activity_coefficient:Na2O | assumed-input (excluded) |
| kems-ms2000-044 | ms2000_044_na2o_activity_xsio2_0430_t1473 | activity_coefficient | Na2O | temperature_K=1473 | 1.83e-05 | 5.27242e-07 | 1.54044 | typed-refusal:missing_capability:documented_melt_activity_coefficient:Na2O | assumed-input (excluded) |
| kems-ms2000-044 | ms2000_044_na2o_activity_xsio2_0477_t1373 | activity_coefficient | Na2O | temperature_K=1373 | 1.32e-06 | 4.71697e-07 | 0.44691 | typed-refusal:missing_capability:documented_melt_activity_coefficient:Na2O | assumed-input (excluded) |
| kems-ms2000-044 | ms2000_044_na2o_activity_xsio2_0524_t1573 | activity_coefficient | Na2O | temperature_K=1573 | 1.53e-06 | 4.16008e-07 | 0.56559 | typed-refusal:missing_capability:documented_melt_activity_coefficient:Na2O | assumed-input (excluded) |
| kems-ms2000-044 | ms2000_044_na2o_activity_xsio2_0573_t1473 | activity_coefficient | Na2O | temperature_K=1473 | 1.02e-07 | 3.58152e-07 | 0.545468 | typed-refusal:missing_capability:documented_melt_activity_coefficient:Na2O | assumed-input (excluded) |
| kems-ms2000-044 | ms2000_044_na2o_activity_xsio2_0625_t1573 | activity_coefficient | Na2O | temperature_K=1573 | 7.63e-08 | 2.97521e-07 | 0.590993 | typed-refusal:missing_capability:documented_melt_activity_coefficient:Na2O | assumed-input (excluded) |
| kems-ms2000-044 | ms2000_044_na2o_activity_xsio2_0671_t1173 | activity_coefficient | Na2O | temperature_K=1173 | 4.67e-11 | 2.45133e-07 | 3.72008 | typed-refusal:missing_capability:documented_melt_activity_coefficient:Na2O | assumed-input (excluded) |
| kems-ms2000-044 | ms2000_044_na2o_activity_xsio2_0753_t1273 | activity_coefficient | Na2O | temperature_K=1273 | 5.29e-11 | 1.56935e-07 | 3.47227 | typed-refusal:missing_capability:documented_melt_activity_coefficient:Na2O | assumed-input (excluded) |
| kems-ms2000-044 | ms2000_044_na2o_activity_xsio2_0805_t1473 | activity_coefficient | Na2O | temperature_K=1473 | 7.19e-10 | 1.06511e-07 | 2.17066 | typed-refusal:missing_capability:documented_melt_activity_coefficient:Na2O | assumed-input (excluded) |
| ts1985 | ts1985_na2o_table2_X0p40_T1100C | activity_coefficient | Na2O | temperature_K=1373.15 | 2.49754e-08 | 3.26531e-07 | 1.11641 | typed-refusal:missing_capability:documented_melt_activity_coefficient:Na2O | assumed-input (excluded) |
| ts1985 | ts1985_na2o_table2_X0p40_T1200C | activity_coefficient | Na2O | temperature_K=1473.15 | 1.31603e-07 | 3.26531e-07 | 0.394659 | typed-refusal:missing_capability:documented_melt_activity_coefficient:Na2O | assumed-input (excluded) |
| ts1985 | ts1985_na2o_table2_X0p40_T1300C | activity_coefficient | Na2O | temperature_K=1573.15 | 5.61381e-07 | 3.26531e-07 | 0.235334 | typed-refusal:missing_capability:documented_melt_activity_coefficient:Na2O | assumed-input (excluded) |
| ts1985 | ts1985_na2o_table2_X0p45_T1100C | activity_coefficient | Na2O | temperature_K=1373.15 | 1.57261e-07 | 3.85256e-07 | 0.389129 | typed-refusal:missing_capability:documented_melt_activity_coefficient:Na2O | assumed-input (excluded) |
| ts1985 | ts1985_na2o_table2_X0p45_T1200C | activity_coefficient | Na2O | temperature_K=1473.15 | 6.02495e-07 | 3.85256e-07 | 0.194205 | typed-refusal:missing_capability:documented_melt_activity_coefficient:Na2O | assumed-input (excluded) |
| ts1985 | ts1985_na2o_table2_X0p45_T1300C | activity_coefficient | Na2O | temperature_K=1573.15 | 1.94592e-06 | 3.85256e-07 | 0.703377 | typed-refusal:missing_capability:documented_melt_activity_coefficient:Na2O | assumed-input (excluded) |
| ts1985 | ts1985_na2o_table2_X0p50_T1100C | activity_coefficient | Na2O | temperature_K=1373.15 | 7.81853e-07 | 4.44444e-07 | 0.245307 | typed-refusal:missing_capability:documented_melt_activity_coefficient:Na2O | assumed-input (excluded) |
| ts1985 | ts1985_na2o_table2_X0p50_T1200C | activity_coefficient | Na2O | temperature_K=1473.15 | 2.38555e-06 | 4.44444e-07 | 0.729771 | typed-refusal:missing_capability:documented_melt_activity_coefficient:Na2O | assumed-input (excluded) |
| ts1985 | ts1985_na2o_table2_X0p50_T1300C | activity_coefficient | Na2O | temperature_K=1573.15 | 6.31627e-06 | 4.44444e-07 | 1.15264 | typed-refusal:missing_capability:documented_melt_activity_coefficient:Na2O | assumed-input (excluded) |
| ts1985 | ts1985_na2o_table2_X0p55_T1100C | activity_coefficient | Na2O | temperature_K=1373.15 | 4.68739e-06 | 5.03642e-07 | 0.968809 | typed-refusal:missing_capability:documented_melt_activity_coefficient:Na2O | assumed-input (excluded) |
| ts1985 | ts1985_na2o_table2_X0p55_T1200C | activity_coefficient | Na2O | temperature_K=1473.15 | 1.08831e-05 | 5.03642e-07 | 1.33463 | typed-refusal:missing_capability:documented_melt_activity_coefficient:Na2O | assumed-input (excluded) |
| ts1985 | ts1985_na2o_table2_X0p55_T1300C | activity_coefficient | Na2O | temperature_K=1573.15 | 2.27019e-05 | 5.03642e-07 | 1.65394 | typed-refusal:missing_capability:documented_melt_activity_coefficient:Na2O | assumed-input (excluded) |
| kems-041-sossi-fegley-2018 | sossi_fegley_2018_table2_gamma_P__PO_2_5_ | activity_coefficient | P | temperature_K=1873 | 1e-08 | 1e-06 | 2 | typed-refusal:self_agreement_excluded | self-agreement-excluded (excluded) |
| kems-041-sossi-fegley-2018 | sossi_fegley_2018_table2_gamma_SiO2__SiO_2_ | activity_coefficient | SiO2 | temperature_K=1673 | 0.994987 | 1 | 0.0021824 | typed-refusal:self_agreement_excluded | self-agreement-excluded (excluded) |
| kems-041-sossi-fegley-2018 | sossi_fegley_2018_table2_gamma_TiO2__TiO_2_ | activity_coefficient | TiO2 | temperature_K=1673 | 1.59687 | 1.6 | 0.000849892 | typed-refusal:self_agreement_excluded | self-agreement-excluded (excluded) |

<!-- END t-512 extract-store reproduction rollup -->
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
inherits that omission. t-622 adopts the independently reviewed HT-C8 IVTAN MnO(g)/CoO(g) candidate
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
system-class-matched kinetic evidence exists. Full historical bound and grid:
`docs-private/research/2026-08-09-upstream-mission/HT-C8-bound/omitted-channel-bound.md`.

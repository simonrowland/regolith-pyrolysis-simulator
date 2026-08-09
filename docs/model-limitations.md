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
- **The evaporation-α surface has tiered coverage.** Tier 1 species Na, K, Fe, Mg, SiO, and Cr carry measured α with citation. Cr is `0.9 ± 0.1` over `1318–1563 K`, from Pound's selected McCabe–Hudson–Paxton solid-Cr Langmuir/Knudsen measurement. It is a clean pure-solid-Cr coefficient, not a silicate-melt coefficient: Cr melt activity remains in `P_sat`, and oxygen-contaminated surfaces can lower α. Tier 2 Ca, Ti, Al, and Mn carry proxy classifications. Mn is the owner-ratified monoatomic-metal class proxy `α=1.0`, envelope `[0.5,1.0]`, over the liquid process band `1519–2334.526 K`; it is not a Mn-specific measurement. Two independent sweeps found no direct Mn HKL coefficient, including a full-text read of Safarian & Engh 2013 with zero Mn value. CrO₂ alone remains tier 3 with no numeric α and fail-loud behavior unless the explicit upper-bound fallback is enabled. Fallback engagement records `unmeasured_alpha_fallback_species`. Melt activity remains in `P_sat`, not α. See [`docs/output-interpretation.md`](output-interpretation.md).
- **SiO evaporation and cold-wall sticking α are split by interface regime.** HOT source evaporation uses the Wetzel & Gail 2013 Arrhenius compilation `alpha_s_SiO(T)=0.52*exp(-3685/T)` at source T, with microscopic reversibility applying at that source interface; its grounded range is about 1000-1800 K with an uncertainty envelope of 0.003-0.067. COLD-WALL condensation below the valid-range floor uses the grounded Pound 1972 high-supersaturation unity condensation coefficient (`alpha_c=1.0`), carrying the `[0.016, 1.0]` band and the no-direct-cold-wall-SiO-measurement gap. The runtime intentionally does not extrapolate the evaporation Arrhenius onto cold walls; `alpha_e != alpha_c` off-equilibrium at high supersaturation.
- **Robinot-class O2 accuracy is an error budget, not a tuned score.** The lab-validation diagnostic reports a WARN-only `robinot_o2_error_budget` sidecar under `lab_oxygen_atom_partition`; it does not gate, reroute oxygen, debit reagents, or alter any ledger outcome. The two headline miss factors use different published normalizations against Robinot exp. 1 analyzer-visible O2 (`35 mg`): raw faithful source-side O2 potential is `0.881913 g`, or `25.20x`; the literature-alpha/top-area forward prediction is `0.656204 g`, or `18.75x` central (`18.25x-19.04x` area band). The diagnostic decomposes the remaining daylight into sourced terms: plume oxidation (`unquantified`, could lower simulated free O2), deposit gettering (`unquantified`, could lower simulated free O2), melt-redox retention (`runtime_accounted` when the terminal partition exposes it, but Robinot allocation remains unmeasured), post-run air oxidation (`unquantified`, can raise recovered-deposit oxygen but cannot close in-run analyzer O2), and analyzer/flow/baseline integration (`quantified_anchor`: exp. 1 `35 mg`, exp. 2 `39.229 mg`, about `11%` reproducibility floor). The central residual is explicit: `0.621204 g` O2-equivalent remains unallocated among sink channels. Negative result accepted: without position-resolved gas sampling and air-isolated deposit oxidation-state data, the residual is unexplained rather than fit away.
- Feedstock values include literature-derived ranges and estimates.

## Honest residuals after the multiphase re-ground

These are disagreements and regressions exposed by replacing flattering linear thermochemistry with the phase-aware/JANAF surface. They are not calibration targets. The confidence tiers below distinguish a reproducible internal counterfactual from an external experimental reproduction; neither tier certifies product yield.

| Validated system | Current residual and direction | Decomposed error budget | Classification and confidence |
|---|---|---|---|
| KEMS/time-series-lake Mg-versus-SiO endpoint ranking | The original comparison disagreed on `2/34` comparable endpoint pairs. The JANAF-4th multiphase re-ground increased that to `3/34`; the later phase-basis/two-rail/Kress surface increased it again to `4/34` (`0.117647`). The observed literature rows did not change: the model ordering moved in the worse direction. The tracked regression pin is `tests/test_timeseries_validation_lake.py::test_endpoint_rank_metric_is_not_labeled_as_kinetic_ordering`. | The first added inversion is attributed to the multiphase Mg/SiO endpoint ordering; the second to the combined phase-basis/two-rail/Kress value surface. The available tracked artifacts do **not** separate that second inversion among those three engine changes, so assigning percentages would be invented. | **Known model disagreement; moderate confidence.** The `4/34` pin is reproducible, but this is a scale-free endpoint-rank residual, not a partial-pressure error, a dex fit, or sequential kinetic-order validation. |
| No-additives full track, final cleaned-melt Na2O | Final Na2O increases from `0.085182 kg` with the legacy Na segment to `0.305797 kg` with current multiphase Na thermochemistry: `+0.220615 kg` retained, so clean Na removal is about `0.22 kg` per track harder than the linear model claimed. | At track entry the current surface retains `+0.321319 kg` more Na2O; a `-0.166274 kg` smaller spent-reductant return partly offsets it; the higher electrochemical floor adds `+0.065570 kg`, closing to `+0.220615 kg`. Separately, liquid-fraction gating improves the current result from `0.366145` to `0.305797 kg`, a `-0.060348 kg` credit. That credit is a within-current-surface comparison and must not be subtracted from the already closed `+0.220615 kg` legacy-to-current delta. | **Known model consequence; high confidence for this executable counterfactual, low confidence as an experimental yield claim.** The harness changed only the Na thermochemistry and rejected a late-cooldown mutation; no matching end-to-end experiment establishes that either residual is physically correct. The supporting investigation artifact is not distributed in the tracked repository, so this historical counterfactual is not independently reproducible from a clean checkout. |

The builtin vapor tier also carries only the monatomic `Na(g)` source term. VapoRock-family equilibrium enumerates both `Na(g)` and `NaO(g)`, and the AlphaMELTS adapter can retain both species, while the builtin fit target is specifically VapoRock `Na(g)`. This is a **known analytic-tier model limitation**, not a curation artifact; its yield impact is unquantified, so no numeric correction is applied. Tracked implementation sources: `data/vapor_pressures.yaml`, `simulator/melt_backend/vaporock.py`, and `simulator/melt_backend/alphamelts.py`.

No quantitative Richter, gas-mixing, FeO-SiO2, or Sesko reproduction residual is claimed here. The reviewed campaign material supports literature ranges and qualitative species-order/deposit checks, but not matched-observable residuals with a defensible engine/input/extrapolation split. Those remain thin or ungrounded validation gaps, not zero-error results.

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

### Extract-store single-species reproduction battery (t-512)

Generated from production priority-winner observations plus every KEMS
extract observation of type `psat_series` / `rate_series` /
`activity_coefficient` / `alpha`. Residuals
are the deliverable (doctrine: *Headline accuracy is the product*).
Engine refusals surface as typed skips; mismatches are FINDINGs —
tolerances are **not** widened to pass. Geometry: tools/motzfeldt.py available; geometry inversion is used only with complete numeric inputs, otherwise a typed capability/data gap is reported.

Observations: **172 total / 38 comparable / 134 skipped**. Comparable residual points: **81**; explicit gap records: **208**. Extrapolated-alpha FINDINGs: **13**.

- In-scope observations evaluated: **172**
- Comparable observations: **38**
- Skipped observations with typed reasons: **134**
- Species with FINDING (mismatch outside stated/default budget): **5**

| Species | Types | N pts | Match | Mismatch | Skip/gap | Max residual (dex) | Mean residual (dex) | Classification |
|---|---|---:|---:|---:|---:|---:|---:|---|
| Al | activity_coefficient,rate_series | 3 | 0 | 0 | 3 | — | — | engine-or-payload-skip |
| Al2O | psat_series | 14 | 0 | 0 | 14 | — | — | engine-or-payload-skip |
| Al2O3 | alpha | 1 | 0 | 0 | 1 | — | — | engine-or-payload-skip |
| AlO | psat_series | 6 | 0 | 0 | 6 | — | — | engine-or-payload-skip |
| As | activity_coefficient | 1 | 0 | 0 | 1 | — | — | engine-or-payload-skip |
| As4O6 | activity_coefficient,psat_series | 7 | 0 | 0 | 7 | — | — | engine-or-payload-skip |
| BaO | activity_coefficient | 1 | 0 | 0 | 1 | — | — | engine-or-payload-skip |
| Bi | activity_coefficient | 1 | 0 | 0 | 1 | — | — | engine-or-payload-skip |
| Ca | activity_coefficient,rate_series | 2 | 0 | 0 | 2 | — | — | engine-or-payload-skip |
| CaO | rate_series | 1 | 0 | 0 | 1 | — | — | engine-or-payload-skip |
| Co | activity_coefficient | 1 | 0 | 0 | 1 | — | — | engine-or-payload-skip |
| Cr | activity_coefficient,alpha,psat_series,rate_series | 8 | 0 | 0 | 8 | — | — | engine-or-payload-skip |
| Cs2O | activity_coefficient | 1 | 0 | 0 | 1 | — | — | engine-or-payload-skip |
| Cu | activity_coefficient,alpha,rate_series | 6 | 0 | 0 | 6 | — | — | engine-or-payload-skip |
| Eu_metal_and_EuO | activity_coefficient | 2 | 0 | 0 | 2 | — | — | engine-or-payload-skip |
| Fe | activity_coefficient,alpha,rate_series | 35 | 5 | 15 | 15 | 1.1 | 0.73 | FINDING-mismatch |
| Ga2O | activity_coefficient | 1 | 0 | 0 | 1 | — | — | engine-or-payload-skip |
| Ge | activity_coefficient | 1 | 0 | 0 | 1 | — | — | engine-or-payload-skip |
| GeO2 | activity_coefficient | 1 | 0 | 0 | 1 | — | — | engine-or-payload-skip |
| In | activity_coefficient | 1 | 0 | 0 | 1 | — | — | engine-or-payload-skip |
| K | activity_coefficient,alpha,rate_series | 7 | 4 | 1 | 2 | 0.886 | 0.354 | FINDING-mismatch |
| Li2O | activity_coefficient | 1 | 0 | 0 | 1 | — | — | engine-or-payload-skip |
| Mg | activity_coefficient,alpha,psat_series,rate_series | 36 | 8 | 11 | 17 | 0.699 | 0.224 | FINDING-mismatch |
| MgO | alpha,psat_series,rate_series | 3 | 0 | 0 | 3 | — | — | engine-or-payload-skip |
| Mn | activity_coefficient,alpha,psat_series,rate_series | 10 | 0 | 0 | 10 | — | — | engine-or-payload-skip |
| MoO2 | activity_coefficient | 1 | 0 | 0 | 1 | — | — | engine-or-payload-skip |
| MoO3 | activity_coefficient | 1 | 0 | 0 | 1 | — | — | engine-or-payload-skip |
| Na | activity_coefficient,alpha,psat_series,rate_series | 27 | 4 | 3 | 20 | 0.585 | 0.251 | FINDING-mismatch |
| NaF | psat_series | 1 | 0 | 0 | 1 | — | — | engine-or-payload-skip |
| Ni | activity_coefficient | 1 | 0 | 0 | 1 | — | — | engine-or-payload-skip |
| O | psat_series | 13 | 0 | 0 | 13 | — | — | engine-or-payload-skip |
| O2 | psat_series | 7 | 0 | 0 | 7 | — | — | engine-or-payload-skip |
| P | activity_coefficient | 1 | 0 | 0 | 1 | — | — | engine-or-payload-skip |
| P4O10 | activity_coefficient | 1 | 0 | 0 | 1 | — | — | engine-or-payload-skip |
| Pb | activity_coefficient | 1 | 0 | 0 | 1 | — | — | engine-or-payload-skip |
| Rb2O | activity_coefficient | 1 | 0 | 0 | 1 | — | — | engine-or-payload-skip |
| S | alpha,rate_series | 4 | 0 | 0 | 4 | — | — | engine-or-payload-skip |
| S2 | psat_series | 1 | 0 | 0 | 1 | — | — | engine-or-payload-skip |
| SO3 | psat_series | 1 | 0 | 0 | 1 | — | — | engine-or-payload-skip |
| Sb4O6 | activity_coefficient | 1 | 0 | 0 | 1 | — | — | engine-or-payload-skip |
| Se_n_ladder | psat_series | 3 | 0 | 0 | 3 | — | — | engine-or-payload-skip |
| Si | alpha,rate_series | 3 | 0 | 0 | 3 | — | — | engine-or-payload-skip |
| SiO | alpha,rate_series | 45 | 10 | 20 | 15 | 1.3 | 0.281 | FINDING-mismatch |
| SiO2 | activity_coefficient,alpha | 3 | 0 | 0 | 3 | — | — | engine-or-payload-skip |
| Sn | activity_coefficient,alpha,rate_series | 5 | 0 | 0 | 5 | — | — | engine-or-payload-skip |
| SrO | activity_coefficient | 1 | 0 | 0 | 1 | — | — | engine-or-payload-skip |
| Ti | psat_series | 1 | 0 | 0 | 1 | — | — | engine-or-payload-skip |
| TiO | rate_series | 1 | 0 | 0 | 1 | — | — | engine-or-payload-skip |
| TiO2 | activity_coefficient,rate_series | 2 | 0 | 0 | 2 | — | — | engine-or-payload-skip |
| V | activity_coefficient | 1 | 0 | 0 | 1 | — | — | engine-or-payload-skip |
| VO_VO2 | activity_coefficient,psat_series | 3 | 0 | 0 | 3 | — | — | engine-or-payload-skip |
| WO2 | activity_coefficient | 1 | 0 | 0 | 1 | — | — | engine-or-payload-skip |
| WO3 | activity_coefficient | 1 | 0 | 0 | 1 | — | — | engine-or-payload-skip |
| Yb_metal_and_YbO | activity_coefficient,psat_series | 5 | 0 | 0 | 5 | — | — | engine-or-payload-skip |
| Zn | activity_coefficient | 1 | 0 | 0 | 1 | — | — | engine-or-payload-skip |

**Typed observation skips (roadmap, one primary reason per skipped observation):**

- `typed-refusal:alpha_form_not_evaluable`: **1**
- `typed-refusal:alpha_unsupported_species:'Al2O3'`: **1**
- `typed-refusal:alpha_unsupported_species:'MgO'`: **1**
- `typed-refusal:alpha_unsupported_species:'SiO2'`: **1**
- `typed-refusal:missing_capability:documented_melt_activity_coefficient:FeO`: **2**
- `typed-refusal:missing_condition:melt_composition`: **1**
- `typed-refusal:missing_condition:pO2_boundary`: **9**
- `typed-refusal:missing_condition:standard_state_boundary`: **5**
- `typed-refusal:missing_numeric_activity`: **36**
- `typed-refusal:missing_numeric_species_rate`: **24**
- `typed-refusal:missing_numeric_species_rate:qualitative_bound`: **13**
- `typed-refusal:no_usable_rate_series_payload`: **3**
- `typed-refusal:not_comparable_system_class:molten_metal`: **11**
- `typed-refusal:not_comparable_system_class:pure_element_condensed`: **6**
- `typed-refusal:not_comparable_system_class:solid_film_growth`: **1**
- `typed-refusal:pointer_or_anchor_without_numeric_points`: **5**
- `typed-refusal:unsupported_observable:clausing_factor_not_species_rate`: **3**
- `typed-refusal:unsupported_observable:qualitative_activity_ordering`: **11**

**Coverage by observation type:**

| Type | Observations | Comparable | Skipped | Comparable points | Gap points | Typed skip reasons |
|---|---:|---:|---:|---:|---:|---|
| activity_coefficient | 49 | 0 | 49 | 0 | 49 | `typed-refusal:missing_capability:documented_melt_activity_coefficient:FeO` ×2; `typed-refusal:missing_numeric_activity` ×36; `typed-refusal:unsupported_observable:qualitative_activity_ordering` ×11 |
| alpha | 60 | 35 | 25 | 69 | 27 | `typed-refusal:alpha_form_not_evaluable` ×1; `typed-refusal:alpha_unsupported_species:'Al2O3'` ×1; `typed-refusal:alpha_unsupported_species:'MgO'` ×1; `typed-refusal:alpha_unsupported_species:'SiO2'` ×1; `typed-refusal:no_usable_rate_series_payload` ×3; `typed-refusal:not_comparable_system_class:molten_metal` ×11; `typed-refusal:not_comparable_system_class:pure_element_condensed` ×6; `typed-refusal:not_comparable_system_class:solid_film_growth` ×1 |
| psat_series | 19 | 0 | 19 | 0 | 88 | `typed-refusal:missing_condition:pO2_boundary` ×9; `typed-refusal:missing_condition:standard_state_boundary` ×5; `typed-refusal:pointer_or_anchor_without_numeric_points` ×5 |
| rate_series | 44 | 3 | 41 | 12 | 44 | `typed-refusal:missing_condition:melt_composition` ×1; `typed-refusal:missing_numeric_species_rate` ×24; `typed-refusal:missing_numeric_species_rate:qualitative_bound` ×13; `typed-refusal:unsupported_observable:clausing_factor_not_species_rate` ×3 |

**Coverage by comparison family:**

| Comparison family | Observations | Comparable | Skipped | Comparable points | Gap points | Typed skip reasons |
|---|---:|---:|---:|---:|---:|---|
| activity_coefficient | 49 | 0 | 49 | 0 | 49 | `typed-refusal:missing_capability:documented_melt_activity_coefficient:FeO` ×2; `typed-refusal:missing_numeric_activity` ×36; `typed-refusal:unsupported_observable:qualitative_activity_ordering` ×11 |
| alpha | 60 | 35 | 25 | 69 | 27 | `typed-refusal:alpha_form_not_evaluable` ×1; `typed-refusal:alpha_unsupported_species:'Al2O3'` ×1; `typed-refusal:alpha_unsupported_species:'MgO'` ×1; `typed-refusal:alpha_unsupported_species:'SiO2'` ×1; `typed-refusal:no_usable_rate_series_payload` ×3; `typed-refusal:not_comparable_system_class:molten_metal` ×11; `typed-refusal:not_comparable_system_class:pure_element_condensed` ×6; `typed-refusal:not_comparable_system_class:solid_film_growth` ×1 |
| alpha_in_legacy_rate_series | 3 | 3 | 0 | 12 | 0 | — |
| psat_series | 19 | 0 | 19 | 0 | 88 | `typed-refusal:missing_condition:pO2_boundary` ×9; `typed-refusal:missing_condition:standard_state_boundary` ×5; `typed-refusal:pointer_or_anchor_without_numeric_points` ×5 |
| rate_hkl | 41 | 0 | 41 | 0 | 44 | `typed-refusal:missing_condition:melt_composition` ×1; `typed-refusal:missing_numeric_species_rate` ×24; `typed-refusal:missing_numeric_species_rate:qualitative_bound` ×13; `typed-refusal:unsupported_observable:clausing_factor_not_species_rate` ×3 |

**Coverage by species:**

| Species | Observations | Comparable | Skipped | Comparable points | Gap points | Typed skip reasons |
|---|---:|---:|---:|---:|---:|---|
| Al | 3 | 0 | 3 | 0 | 3 | `typed-refusal:missing_numeric_activity` ×1; `typed-refusal:missing_numeric_species_rate:qualitative_bound` ×2 |
| Al2O | 1 | 0 | 1 | 0 | 14 | `typed-refusal:missing_condition:pO2_boundary` ×1 |
| Al2O3 | 1 | 0 | 1 | 0 | 1 | `typed-refusal:alpha_unsupported_species:'Al2O3'` ×1 |
| AlO | 1 | 0 | 1 | 0 | 6 | `typed-refusal:missing_condition:pO2_boundary` ×1 |
| As | 1 | 0 | 1 | 0 | 1 | `typed-refusal:missing_numeric_activity` ×1 |
| As4O6 | 3 | 0 | 3 | 0 | 7 | `typed-refusal:missing_condition:standard_state_boundary` ×2; `typed-refusal:unsupported_observable:qualitative_activity_ordering` ×1 |
| BaO | 1 | 0 | 1 | 0 | 1 | `typed-refusal:unsupported_observable:qualitative_activity_ordering` ×1 |
| Bi | 1 | 0 | 1 | 0 | 1 | `typed-refusal:missing_numeric_activity` ×1 |
| Ca | 2 | 0 | 2 | 0 | 2 | `typed-refusal:missing_numeric_activity` ×1; `typed-refusal:missing_numeric_species_rate:qualitative_bound` ×1 |
| CaO | 1 | 0 | 1 | 0 | 1 | `typed-refusal:missing_numeric_species_rate` ×1 |
| Co | 1 | 0 | 1 | 0 | 1 | `typed-refusal:missing_numeric_activity` ×1 |
| Cr | 7 | 0 | 7 | 0 | 8 | `typed-refusal:missing_condition:pO2_boundary` ×1; `typed-refusal:missing_numeric_activity` ×2; `typed-refusal:missing_numeric_species_rate` ×1; `typed-refusal:not_comparable_system_class:molten_metal` ×1; `typed-refusal:not_comparable_system_class:pure_element_condensed` ×2 |
| Cs2O | 1 | 0 | 1 | 0 | 1 | `typed-refusal:unsupported_observable:qualitative_activity_ordering` ×1 |
| Cu | 6 | 0 | 6 | 0 | 6 | `typed-refusal:missing_numeric_activity` ×1; `typed-refusal:missing_numeric_species_rate` ×2; `typed-refusal:not_comparable_system_class:molten_metal` ×3 |
| Eu_metal_and_EuO | 2 | 0 | 2 | 0 | 2 | `typed-refusal:missing_numeric_activity` ×1; `typed-refusal:unsupported_observable:qualitative_activity_ordering` ×1 |
| Fe | 22 | 7 | 15 | 20 | 15 | `typed-refusal:missing_capability:documented_melt_activity_coefficient:FeO` ×2; `typed-refusal:missing_numeric_activity` ×3; `typed-refusal:missing_numeric_species_rate` ×3; `typed-refusal:missing_numeric_species_rate:qualitative_bound` ×1; `typed-refusal:not_comparable_system_class:pure_element_condensed` ×3; `typed-refusal:unsupported_observable:clausing_factor_not_species_rate` ×3 |
| Ga2O | 1 | 0 | 1 | 0 | 1 | `typed-refusal:unsupported_observable:qualitative_activity_ordering` ×1 |
| Ge | 1 | 0 | 1 | 0 | 1 | `typed-refusal:missing_numeric_activity` ×1 |
| GeO2 | 1 | 0 | 1 | 0 | 1 | `typed-refusal:missing_numeric_activity` ×1 |
| In | 1 | 0 | 1 | 0 | 1 | `typed-refusal:missing_numeric_activity` ×1 |
| K | 7 | 5 | 2 | 5 | 2 | `typed-refusal:missing_numeric_activity` ×1; `typed-refusal:missing_numeric_species_rate:qualitative_bound` ×1 |
| Li2O | 1 | 0 | 1 | 0 | 1 | `typed-refusal:unsupported_observable:qualitative_activity_ordering` ×1 |
| Mg | 16 | 9 | 7 | 19 | 17 | `typed-refusal:missing_condition:melt_composition` ×1; `typed-refusal:missing_condition:pO2_boundary` ×1; `typed-refusal:missing_numeric_activity` ×2; `typed-refusal:missing_numeric_species_rate` ×3 |
| MgO | 3 | 0 | 3 | 0 | 3 | `typed-refusal:alpha_unsupported_species:'MgO'` ×1; `typed-refusal:missing_numeric_species_rate` ×1; `typed-refusal:pointer_or_anchor_without_numeric_points` ×1 |
| Mn | 8 | 0 | 8 | 0 | 10 | `typed-refusal:missing_condition:pO2_boundary` ×1; `typed-refusal:missing_numeric_activity` ×1; `typed-refusal:missing_numeric_species_rate` ×3; `typed-refusal:not_comparable_system_class:molten_metal` ×3 |
| MoO2 | 1 | 0 | 1 | 0 | 1 | `typed-refusal:missing_numeric_activity` ×1 |
| MoO3 | 1 | 0 | 1 | 0 | 1 | `typed-refusal:missing_numeric_activity` ×1 |
| Na | 13 | 7 | 6 | 7 | 20 | `typed-refusal:missing_condition:pO2_boundary` ×2; `typed-refusal:missing_numeric_activity` ×2; `typed-refusal:missing_numeric_species_rate` ×1; `typed-refusal:missing_numeric_species_rate:qualitative_bound` ×1 |
| NaF | 1 | 0 | 1 | 0 | 1 | `typed-refusal:pointer_or_anchor_without_numeric_points` ×1 |
| Ni | 1 | 0 | 1 | 0 | 1 | `typed-refusal:missing_numeric_activity` ×1 |
| O | 1 | 0 | 1 | 0 | 13 | `typed-refusal:missing_condition:pO2_boundary` ×1 |
| O2 | 1 | 0 | 1 | 0 | 7 | `typed-refusal:missing_condition:pO2_boundary` ×1 |
| P | 1 | 0 | 1 | 0 | 1 | `typed-refusal:missing_numeric_activity` ×1 |
| P4O10 | 1 | 0 | 1 | 0 | 1 | `typed-refusal:missing_numeric_activity` ×1 |
| Pb | 1 | 0 | 1 | 0 | 1 | `typed-refusal:missing_numeric_activity` ×1 |
| Rb2O | 1 | 0 | 1 | 0 | 1 | `typed-refusal:unsupported_observable:qualitative_activity_ordering` ×1 |
| S | 4 | 0 | 4 | 0 | 4 | `typed-refusal:missing_numeric_species_rate` ×2; `typed-refusal:not_comparable_system_class:molten_metal` ×2 |
| S2 | 1 | 0 | 1 | 0 | 1 | `typed-refusal:pointer_or_anchor_without_numeric_points` ×1 |
| SO3 | 1 | 0 | 1 | 0 | 1 | `typed-refusal:pointer_or_anchor_without_numeric_points` ×1 |
| Sb4O6 | 1 | 0 | 1 | 0 | 1 | `typed-refusal:unsupported_observable:qualitative_activity_ordering` ×1 |
| Se_n_ladder | 1 | 0 | 1 | 0 | 3 | `typed-refusal:missing_condition:standard_state_boundary` ×1 |
| Si | 3 | 0 | 3 | 0 | 3 | `typed-refusal:missing_numeric_species_rate` ×1; `typed-refusal:missing_numeric_species_rate:qualitative_bound` ×1; `typed-refusal:not_comparable_system_class:pure_element_condensed` ×1 |
| SiO | 23 | 10 | 13 | 30 | 15 | `typed-refusal:alpha_form_not_evaluable` ×1; `typed-refusal:missing_numeric_species_rate` ×4; `typed-refusal:missing_numeric_species_rate:qualitative_bound` ×4; `typed-refusal:no_usable_rate_series_payload` ×3; `typed-refusal:not_comparable_system_class:solid_film_growth` ×1 |
| SiO2 | 3 | 0 | 3 | 0 | 3 | `typed-refusal:alpha_unsupported_species:'SiO2'` ×1; `typed-refusal:missing_numeric_activity` ×2 |
| Sn | 5 | 0 | 5 | 0 | 5 | `typed-refusal:missing_numeric_activity` ×1; `typed-refusal:missing_numeric_species_rate` ×2; `typed-refusal:not_comparable_system_class:molten_metal` ×2 |
| SrO | 1 | 0 | 1 | 0 | 1 | `typed-refusal:unsupported_observable:qualitative_activity_ordering` ×1 |
| Ti | 1 | 0 | 1 | 0 | 1 | `typed-refusal:pointer_or_anchor_without_numeric_points` ×1 |
| TiO | 1 | 0 | 1 | 0 | 1 | `typed-refusal:missing_numeric_species_rate:qualitative_bound` ×1 |
| TiO2 | 2 | 0 | 2 | 0 | 2 | `typed-refusal:missing_numeric_activity` ×1; `typed-refusal:missing_numeric_species_rate:qualitative_bound` ×1 |
| V | 1 | 0 | 1 | 0 | 1 | `typed-refusal:missing_numeric_activity` ×1 |
| VO_VO2 | 2 | 0 | 2 | 0 | 3 | `typed-refusal:missing_condition:standard_state_boundary` ×1; `typed-refusal:unsupported_observable:qualitative_activity_ordering` ×1 |
| WO2 | 1 | 0 | 1 | 0 | 1 | `typed-refusal:missing_numeric_activity` ×1 |
| WO3 | 1 | 0 | 1 | 0 | 1 | `typed-refusal:missing_numeric_activity` ×1 |
| Yb_metal_and_YbO | 3 | 0 | 3 | 0 | 5 | `typed-refusal:missing_condition:standard_state_boundary` ×1; `typed-refusal:missing_numeric_activity` ×1; `typed-refusal:unsupported_observable:qualitative_activity_ordering` ×1 |
| Zn | 1 | 0 | 1 | 0 | 1 | `typed-refusal:missing_numeric_activity` ×1 |

**Coverage by source:**

| Source | Observations | Comparable | Skipped | Comparable points | Gap points | Typed skip reasons |
|---|---:|---:|---:|---:|---:|---|
| behrens-rosenblatt-1972 | 1 | 0 | 1 | 0 | 3 | `typed-refusal:missing_condition:standard_state_boundary` ×1 |
| berkowitz-chupka-inghram-1957 | 1 | 0 | 1 | 0 | 2 | `typed-refusal:missing_condition:standard_state_boundary` ×1 |
| costa-jacobson-2015 | 2 | 2 | 0 | 2 | 0 | — |
| fedkin-grossman-ghiorso-2006 | 8 | 8 | 0 | 26 | 0 | — |
| habermann-daane-1964 | 1 | 0 | 1 | 0 | 3 | `typed-refusal:missing_condition:standard_state_boundary` ×1 |
| janaf-4th | 4 | 0 | 4 | 0 | 4 | `typed-refusal:pointer_or_anchor_without_numeric_points` ×4 |
| kems-001-homma-1966 | 10 | 0 | 10 | 0 | 10 | `typed-refusal:missing_numeric_species_rate` ×4; `typed-refusal:not_comparable_system_class:molten_metal` ×6 |
| kems-002-ohno-1967 | 11 | 0 | 11 | 0 | 11 | `typed-refusal:missing_numeric_species_rate` ×6; `typed-refusal:not_comparable_system_class:molten_metal` ×5 |
| kems-003-pound-1972 | 6 | 0 | 6 | 0 | 6 | `typed-refusal:not_comparable_system_class:pure_element_condensed` ×5; `typed-refusal:unsupported_observable:clausing_factor_not_species_rate` ×1 |
| kems-005-fedkin-2006 | 10 | 9 | 1 | 15 | 1 | `typed-refusal:no_usable_rate_series_payload` ×1 |
| kems-007-costa-2015 | 4 | 4 | 0 | 12 | 0 | — |
| kems-008-schaefer-fegley-2004 | 5 | 2 | 3 | 3 | 3 | `typed-refusal:alpha_unsupported_species:'Al2O3'` ×1; `typed-refusal:alpha_unsupported_species:'MgO'` ×1; `typed-refusal:alpha_unsupported_species:'SiO2'` ×1 |
| kems-009-safarian-2013 | 2 | 0 | 2 | 0 | 2 | `typed-refusal:missing_numeric_species_rate` ×1; `typed-refusal:not_comparable_system_class:pure_element_condensed` ×1 |
| kems-010-richter-2007 | 4 | 2 | 2 | 6 | 5 | `typed-refusal:missing_condition:melt_composition` ×1; `typed-refusal:missing_numeric_species_rate` ×1 |
| kems-011-wetzel-gail-2013 | 3 | 0 | 3 | 0 | 5 | `typed-refusal:alpha_form_not_evaluable` ×1; `typed-refusal:missing_numeric_species_rate` ×1; `typed-refusal:not_comparable_system_class:solid_film_growth` ×1 |
| kems-012-sossi-2019 | 6 | 5 | 1 | 5 | 1 | `typed-refusal:missing_numeric_activity` ×1 |
| kems-015-hashimoto-1983 | 6 | 0 | 6 | 0 | 6 | `typed-refusal:missing_numeric_species_rate` ×6 |
| kems-016-stolyarova-1992 | 4 | 0 | 4 | 0 | 4 | `typed-refusal:missing_numeric_activity` ×3; `typed-refusal:missing_numeric_species_rate:qualitative_bound` ×1 |
| kems-017-stolyarova-2013 | 1 | 0 | 1 | 0 | 1 | `typed-refusal:missing_numeric_species_rate:qualitative_bound` ×1 |
| kems-018-stolyarova-2012 | 1 | 0 | 1 | 0 | 1 | `typed-refusal:missing_numeric_species_rate:qualitative_bound` ×1 |
| kems-022-demaria-1971 | 21 | 0 | 21 | 0 | 81 | `typed-refusal:missing_capability:documented_melt_activity_coefficient:FeO` ×2; `typed-refusal:missing_condition:pO2_boundary` ×9; `typed-refusal:missing_numeric_activity` ×1; `typed-refusal:missing_numeric_species_rate:qualitative_bound` ×9 |
| kems-027-plante-hastie-1983 | 1 | 0 | 1 | 0 | 1 | `typed-refusal:missing_numeric_species_rate` ×1 |
| kems-031-halwax-2024 | 3 | 0 | 3 | 0 | 3 | `typed-refusal:missing_numeric_species_rate` ×2; `typed-refusal:pointer_or_anchor_without_numeric_points` ×1 |
| kems-032-copland-jacobson-2010 | 4 | 0 | 4 | 0 | 4 | `typed-refusal:missing_numeric_species_rate` ×2; `typed-refusal:unsupported_observable:clausing_factor_not_species_rate` ×2 |
| kems-037-richter-2002 | 4 | 2 | 2 | 2 | 2 | `typed-refusal:no_usable_rate_series_payload` ×2 |
| kems-041-sossi-fegley-2018 | 29 | 0 | 29 | 0 | 29 | `typed-refusal:missing_numeric_activity` ×28; `typed-refusal:missing_numeric_species_rate:qualitative_bound` ×1 |
| nist-webbook | 2 | 0 | 2 | 0 | 6 | `typed-refusal:missing_condition:standard_state_boundary` ×2 |
| richter-et-al-2007 | 2 | 2 | 0 | 6 | 0 | — |
| sossi-et-al-2019 | 1 | 1 | 0 | 1 | 0 | — |
| sossi-fegley-2018 | 14 | 0 | 14 | 0 | 14 | `typed-refusal:missing_numeric_activity` ×3; `typed-refusal:unsupported_observable:qualitative_activity_ordering` ×11 |
| wetzel-gail-2013-sio-arrhenius | 1 | 1 | 0 | 3 | 0 | — |

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

**FINDINGS (mismatches outside budget — not tuned away):**

- FINDING mismatch Fe α T=1973K expected=0.23 actual=0.02 budget={'kind': 'absolute', 'value': 0.02, 'defaulted': False, 'source': 'point.sigma', 'components': []}
- FINDING mismatch Fe α T=2073K expected=0.25 actual=0.02 budget={'kind': 'absolute', 'value': 0.02, 'defaulted': False, 'source': 'point.sigma', 'components': []}
- FINDING mismatch Fe α T=2173K expected=0.24 actual=0.02 budget={'kind': 'absolute', 'value': 0.02, 'defaulted': False, 'source': 'point.sigma', 'components': []}
- FINDING mismatch Fe α T=2273K expected=0.23 actual=0.02 budget={'kind': 'absolute', 'value': 0.02, 'defaulted': False, 'source': 'point.sigma', 'components': []}
- FINDING mismatch Fe α T=1973K expected=0.23 actual=0.02 budget={'kind': 'absolute', 'value': 0.02, 'defaulted': False, 'source': 'point.sigma', 'components': []}
- FINDING mismatch Fe α T=2073K expected=0.25 actual=0.02 budget={'kind': 'absolute', 'value': 0.02, 'defaulted': False, 'source': 'point.sigma', 'components': []}
- FINDING mismatch Fe α T=2173K expected=0.24 actual=0.02 budget={'kind': 'absolute', 'value': 0.02, 'defaulted': False, 'source': 'point.sigma', 'components': []}
- FINDING mismatch Fe α T=2273K expected=0.23 actual=0.02 budget={'kind': 'absolute', 'value': 0.02, 'defaulted': False, 'source': 'point.sigma', 'components': []}
- FINDING mismatch Fe α T=2123K expected=0.24 actual=0.02 budget={'kind': 'absolute', 'value': 0.05, 'defaulted': True, 'rationale': 'extract observation has no usable numeric uncertainty; default absolute α envelope ±0.05 (t-512)'}
- FINDING mismatch Fe α T=1973K expected=0.23 actual=0.02 budget={'kind': 'absolute', 'value': 0.02, 'defaulted': False, 'source': 'point.sigma', 'components': []}
- FINDING mismatch Fe α T=2073K expected=0.25 actual=0.02 budget={'kind': 'absolute', 'value': 0.02, 'defaulted': False, 'source': 'point.sigma', 'components': []}
- FINDING mismatch Fe α T=2173K expected=0.24 actual=0.02 budget={'kind': 'absolute', 'value': 0.02, 'defaulted': False, 'source': 'point.sigma', 'components': []}
- FINDING mismatch Fe α T=2273K expected=0.23 actual=0.02 budget={'kind': 'absolute', 'value': 0.02, 'defaulted': False, 'source': 'point.sigma', 'components': []}
- FINDING mismatch Fe α T=1795K expected=0.0115 actual=0.02 budget={'kind': 'absolute', 'value': 0.0045000000000000005, 'defaulted': False, 'source': 'observation.uncertainty.alpha_range', 'components': ['published alpha range half-width']}
- FINDING mismatch Fe α T=1800K expected=0.013 actual=0.02 budget={'kind': 'absolute', 'value': 0.0045000000000000005, 'defaulted': False, 'source': 'observation.uncertainty.alpha_range', 'components': ['published alpha range half-width']}
- FINDING mismatch K α T=1698.15K expected=1 actual=0.13 budget={'kind': 'absolute', 'value': 0.05, 'defaulted': True, 'rationale': 'extract observation has no usable numeric uncertainty; default absolute α envelope ±0.05 (t-512)'}
- FINDING mismatch Mg α T=1973K expected=0.24 actual=0.2 budget={'kind': 'absolute', 'value': 0.01, 'defaulted': False, 'source': 'point.sigma', 'components': []}
- FINDING mismatch Mg α T=2073K expected=0.28 actual=0.2 budget={'kind': 'absolute', 'value': 0.01, 'defaulted': False, 'source': 'point.sigma', 'components': []}
- FINDING mismatch Mg α T=2173K expected=0.28 actual=0.2 budget={'kind': 'absolute', 'value': 0.01, 'defaulted': False, 'source': 'point.sigma', 'components': []}
- FINDING mismatch Mg α T=2273K expected=0.27 actual=0.2 budget={'kind': 'absolute', 'value': 0.01, 'defaulted': False, 'source': 'point.sigma', 'components': []}
- FINDING mismatch Mg α T=1973K expected=0.24 actual=0.2 budget={'kind': 'absolute', 'value': 0.01, 'defaulted': False, 'source': 'point.sigma', 'components': []}
- FINDING mismatch Mg α T=2073K expected=0.28 actual=0.2 budget={'kind': 'absolute', 'value': 0.01, 'defaulted': False, 'source': 'point.sigma', 'components': []}
- FINDING mismatch Mg α T=2173K expected=0.28 actual=0.2 budget={'kind': 'absolute', 'value': 0.01, 'defaulted': False, 'source': 'point.sigma', 'components': []}
- FINDING mismatch Mg α T=2273K expected=0.27 actual=0.2 budget={'kind': 'absolute', 'value': 0.01, 'defaulted': False, 'source': 'point.sigma', 'components': []}
- FINDING mismatch Mg α T=2123K expected=0.24 actual=0.2 budget={'kind': 'absolute', 'value': 0.01, 'defaulted': False, 'source': 'observation.values.sigma', 'components': ['sigma']}
- FINDING mismatch Mg α T=1723K expected=0.04 actual=0.2 budget={'kind': 'absolute', 'value': 0.05, 'defaulted': True, 'rationale': 'extract observation has no usable numeric uncertainty; default absolute α envelope ±0.05 (t-512)'}
- FINDING mismatch Mg α T=1923.15K expected=0.04 actual=0.2 budget={'kind': 'absolute', 'value': 0.05, 'defaulted': True, 'rationale': 'extract observation has no usable numeric uncertainty; default absolute α envelope ±0.05 (t-512)'}
- FINDING mismatch Na α T=1723.15K expected=0.26 actual=1.0 budget={'kind': 'absolute', 'value': 0.05, 'defaulted': False, 'source': 'observation.uncertainty.alpha_range', 'components': ['published alpha range half-width']}
- FINDING mismatch Na α T=1723.15K expected=0.26 actual=1.0 budget={'kind': 'absolute', 'value': 0.05, 'defaulted': False, 'source': 'observation.uncertainty.alpha_range', 'components': ['published alpha range half-width']}
- FINDING mismatch Na α T=1723.15K expected=0.26 actual=1.0 budget={'kind': 'absolute', 'value': 0.05, 'defaulted': False, 'source': 'observation.uncertainty.alpha_range', 'components': ['published alpha range half-width']}
- FINDING mismatch SiO α T=1750K expected=0.0195 actual=0.06331450981454725 budget={'kind': 'absolute', 'value': 0.016499999999999997, 'defaulted': False, 'source': 'point.sigma', 'components': []}
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
- FINDING mismatch SiO α T=1750K expected=0.0195 actual=0.06331450981454725 budget={'kind': 'absolute', 'value': 0.016499999999999997, 'defaulted': False, 'source': 'point.sigma', 'components': []}
- FINDING mismatch SiO α T=1700K expected=0.003 actual=0.05951222494762625 budget={'kind': 'absolute', 'value': 0.016499999999999997, 'defaulted': False, 'source': 'observation.uncertainty.alpha_range', 'components': ['published alpha range half-width']}
- FINDING mismatch SiO α T=1725K expected=0.022 actual=0.06141148854585059 budget={'kind': 'absolute', 'value': 0.016499999999999997, 'defaulted': False, 'source': 'observation.uncertainty.alpha_range', 'components': ['published alpha range half-width']}
- FINDING mismatch SiO α T=1750K expected=0.036 actual=0.06331450981454725 budget={'kind': 'absolute', 'value': 0.016499999999999997, 'defaulted': False, 'source': 'observation.uncertainty.alpha_range', 'components': ['published alpha range half-width']}
- FINDING mismatch SiO α T=1795K expected=0.026 actual=0.06674664410854737 budget={'kind': 'absolute', 'value': 0.016499999999999997, 'defaulted': False, 'source': 'observation.uncertainty.alpha_range', 'components': ['published alpha range half-width']}
- FINDING mismatch SiO α T=1800K expected=0.029 actual=0.0671283587857187 budget={'kind': 'absolute', 'value': 0.016499999999999997, 'defaulted': False, 'source': 'observation.uncertainty.alpha_range', 'components': ['published alpha range half-width']}
- FINDING mismatch SiO α T=2273K expected=0.2 actual=0.10278334807870564 budget={'kind': 'absolute', 'value': 0.08, 'defaulted': False, 'source': 'observation.values.alpha_range', 'components': ['published alpha range half-width']} extrapolated: true

Comparable per-observation residuals and uncertainty ledger:

| Source | Observation | Type | Species | Coordinate | Literature | Literature uncertainty | Engine uncertainty | Combined propagated uncertainty | Engine | Residual | Residual dex | Residual / literature budget | Status |
|---|---|---|---|---|---:|---|---|---|---:|---:|---:|---:|---|
| costa-jacobson-2015 | costa_jacobson_2015_fe_olivine_kems | alpha | Fe | temperature_K=1750 | 0.02 | absolute=0.0045000000000000005 (observation.uncertainty.alpha_range); published alpha range half-width | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 0.02 | 0 | 0 | 0 | match |
| fedkin-grossman-ghiorso-2006 | fedkin_2006_table3_fe_hashimoto_langmuir | alpha | Fe | temperature_K=1973 | 0.23 | absolute=0.02 (point.sigma) | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 0.02 | -0.21 | 1.0607 | 10.5 | mismatch |
| fedkin-grossman-ghiorso-2006 | fedkin_2006_table3_fe_hashimoto_langmuir | alpha | Fe | temperature_K=2073 | 0.25 | absolute=0.02 (point.sigma) | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 0.02 | -0.23 | 1.09691 | 11.5 | mismatch |
| fedkin-grossman-ghiorso-2006 | fedkin_2006_table3_fe_hashimoto_langmuir | alpha | Fe | temperature_K=2173 | 0.24 | absolute=0.02 (point.sigma) | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 0.02 | -0.22 | 1.07918 | 11 | mismatch |
| fedkin-grossman-ghiorso-2006 | fedkin_2006_table3_fe_hashimoto_langmuir | alpha | Fe | temperature_K=2273 | 0.23 | absolute=0.02 (point.sigma) | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 0.02 | -0.21 | 1.0607 | 10.5 | mismatch |
| fedkin-grossman-ghiorso-2006 | fedkin_2006_table3_fe_hashimoto_langmuir_per_T_alpha_series | rate_series | Fe | temperature_K=1973 | 0.23 | absolute=0.02 (point.sigma) | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 0.02 | -0.21 | 1.0607 | 10.5 | mismatch |
| fedkin-grossman-ghiorso-2006 | fedkin_2006_table3_fe_hashimoto_langmuir_per_T_alpha_series | rate_series | Fe | temperature_K=2073 | 0.25 | absolute=0.02 (point.sigma) | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 0.02 | -0.23 | 1.09691 | 11.5 | mismatch |
| fedkin-grossman-ghiorso-2006 | fedkin_2006_table3_fe_hashimoto_langmuir_per_T_alpha_series | rate_series | Fe | temperature_K=2173 | 0.24 | absolute=0.02 (point.sigma) | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 0.02 | -0.22 | 1.07918 | 11 | mismatch |
| fedkin-grossman-ghiorso-2006 | fedkin_2006_table3_fe_hashimoto_langmuir_per_T_alpha_series | rate_series | Fe | temperature_K=2273 | 0.23 | absolute=0.02 (point.sigma) | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 0.02 | -0.21 | 1.0607 | 10.5 | mismatch |
| kems-005-fedkin-2006 | fedkin_2006_fe_class_b1 | alpha | Fe | temperature_K=2123 | 0.24 | absolute=0.05 (documented default) | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 0.02 | -0.22 | 1.07918 | 4.4 | mismatch |
| kems-005-fedkin-2006 | fedkin_2006_fe_hashimoto_langmuir_table3 | alpha | Fe | temperature_K=1973 | 0.23 | absolute=0.02 (point.sigma) | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 0.02 | -0.21 | 1.0607 | 10.5 | mismatch |
| kems-005-fedkin-2006 | fedkin_2006_fe_hashimoto_langmuir_table3 | alpha | Fe | temperature_K=2073 | 0.25 | absolute=0.02 (point.sigma) | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 0.02 | -0.23 | 1.09691 | 11.5 | mismatch |
| kems-005-fedkin-2006 | fedkin_2006_fe_hashimoto_langmuir_table3 | alpha | Fe | temperature_K=2173 | 0.24 | absolute=0.02 (point.sigma) | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 0.02 | -0.22 | 1.07918 | 11 | mismatch |
| kems-005-fedkin-2006 | fedkin_2006_fe_hashimoto_langmuir_table3 | alpha | Fe | temperature_K=2273 | 0.23 | absolute=0.02 (point.sigma) | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 0.02 | -0.21 | 1.0607 | 10.5 | mismatch |
| kems-007-costa-2015 | costa_2015_fe_olivine_class_and_motzfeldt_absence_b1 | alpha | Fe | temperature_K=1750 | 0.02 | absolute=0.0045000000000000005 (observation.uncertainty.alpha_range); published alpha range half-width | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 0.02 | 0 | 0 | 0 | match |
| kems-007-costa-2015 | costa_2015_fe_olivine_kems_alpha_multicell | alpha | Fe | temperature_K=1700 | 0.0165 | absolute=0.0045000000000000005 (observation.uncertainty.alpha_range); published alpha range half-width | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 0.02 | 0.0035 | 0.0835461 | 0.777778 | match |
| kems-007-costa-2015 | costa_2015_fe_olivine_kems_alpha_multicell | alpha | Fe | temperature_K=1720 | 0.0155 | absolute=0.0045000000000000005 (observation.uncertainty.alpha_range); published alpha range half-width | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 0.02 | 0.0045 | 0.110698 | 1 | match |
| kems-007-costa-2015 | costa_2015_fe_olivine_kems_alpha_multicell | alpha | Fe | temperature_K=1750 | 0.0197 | absolute=0.0045000000000000005 (observation.uncertainty.alpha_range); published alpha range half-width | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 0.02 | 0.0003 | 0.00656377 | 0.0666667 | match |
| kems-007-costa-2015 | costa_2015_fe_olivine_kems_alpha_multicell | alpha | Fe | temperature_K=1795 | 0.0115 | absolute=0.0045000000000000005 (observation.uncertainty.alpha_range); published alpha range half-width | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 0.02 | 0.0085 | 0.240332 | 1.88889 | mismatch |
| kems-007-costa-2015 | costa_2015_fe_olivine_kems_alpha_multicell | alpha | Fe | temperature_K=1800 | 0.013 | absolute=0.0045000000000000005 (observation.uncertainty.alpha_range); published alpha range half-width | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 0.02 | 0.007 | 0.187087 | 1.55556 | mismatch |
| fedkin-grossman-ghiorso-2006 | fedkin_2006_yu_k_vacuum_langmuir | alpha | K | temperature_K=1723.15 | 0.13 | absolute=0.019999999999999997 (observation.uncertainty.alpha_range); published alpha range half-width | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 0.13 | 0 | 0 | 0 | match |
| kems-005-fedkin-2006 | fedkin_2006_k_class_b1 | alpha | K | temperature_K=1723.15 | 0.13 | absolute=0.019999999999999997 (observation.uncertainty.alpha_range); published alpha range half-width | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 0.13 | 0 | 0 | 0 | match |
| kems-005-fedkin-2006 | fedkin_2006_k_yu_langmuir | alpha | K | temperature_K=1723.15 | 0.13 | absolute=0.019999999999999997 (observation.uncertainty.alpha_range); published alpha range half-width | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 0.13 | 0 | 0 | 0 | match |
| kems-012-sossi-2019 | sossi_2019_k_class_b1 | alpha | K | temperature_K=1698.15 | 1 | absolute=1.55 (observation.values.alpha_range); published alpha range half-width | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 0.13 | -0.87 | 0.886057 | 0.56129 | match |
| kems-012-sossi-2019 | sossi_2019_k_open_furnace_alpha_e_context | alpha | K | temperature_K=1698.15 | 1 | absolute=0.05 (documented default) | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 0.13 | -0.87 | 0.886057 | 17.4 | mismatch |
| fedkin-grossman-ghiorso-2006 | fedkin_2006_table3_mg_hashimoto_langmuir | alpha | Mg | temperature_K=1973 | 0.24 | absolute=0.01 (point.sigma) | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 0.2 | -0.04 | 0.0791812 | 4 | mismatch |
| fedkin-grossman-ghiorso-2006 | fedkin_2006_table3_mg_hashimoto_langmuir | alpha | Mg | temperature_K=2073 | 0.28 | absolute=0.01 (point.sigma) | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 0.2 | -0.08 | 0.146128 | 8 | mismatch |
| fedkin-grossman-ghiorso-2006 | fedkin_2006_table3_mg_hashimoto_langmuir | alpha | Mg | temperature_K=2173 | 0.28 | absolute=0.01 (point.sigma) | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 0.2 | -0.08 | 0.146128 | 8 | mismatch |
| fedkin-grossman-ghiorso-2006 | fedkin_2006_table3_mg_hashimoto_langmuir | alpha | Mg | temperature_K=2273 | 0.27 | absolute=0.01 (point.sigma) | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 0.2 | -0.07 | 0.130334 | 7 | mismatch |
| fedkin-grossman-ghiorso-2006 | fedkin_2006_table3_mg_hashimoto_langmuir_per_T_alpha_series | rate_series | Mg | temperature_K=1973 | 0.24 | absolute=0.01 (point.sigma) | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 0.2 | -0.04 | 0.0791812 | 4 | mismatch |
| fedkin-grossman-ghiorso-2006 | fedkin_2006_table3_mg_hashimoto_langmuir_per_T_alpha_series | rate_series | Mg | temperature_K=2073 | 0.28 | absolute=0.01 (point.sigma) | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 0.2 | -0.08 | 0.146128 | 8 | mismatch |
| fedkin-grossman-ghiorso-2006 | fedkin_2006_table3_mg_hashimoto_langmuir_per_T_alpha_series | rate_series | Mg | temperature_K=2173 | 0.28 | absolute=0.01 (point.sigma) | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 0.2 | -0.08 | 0.146128 | 8 | mismatch |
| fedkin-grossman-ghiorso-2006 | fedkin_2006_table3_mg_hashimoto_langmuir_per_T_alpha_series | rate_series | Mg | temperature_K=2273 | 0.27 | absolute=0.01 (point.sigma) | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 0.2 | -0.07 | 0.130334 | 7 | mismatch |
| kems-005-fedkin-2006 | fedkin_2006_mg_class_b1 | alpha | Mg | temperature_K=2123 | 0.24 | absolute=0.05 (documented default) | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 0.2 | -0.04 | 0.0791812 | 0.8 | match |
| kems-005-fedkin-2006 | fedkin_2006_mg_hashimoto_langmuir_table3 | alpha | Mg | temperature_K=2123 | 0.24 | absolute=0.01 (observation.values.sigma); sigma | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 0.2 | -0.04 | 0.0791812 | 4 | mismatch |
| kems-008-schaefer-fegley-2004 | schaefer_fegley_2004_mg_forsterite_alpha_s_survey | alpha | Mg | temperature_K=2243 | 0.2 | absolute=0.0049999999999999906 (observation.values.alpha_range); published alpha range half-width | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 0.2 | 0 | 0 | 0 | match |
| kems-010-richter-2007 | richter_2007_mg_cai_langmuir_alpha_arrhenius | alpha | Mg | temperature_K=1873 | 0.0603586 | absolute=0.2054316518789593 (point.sigma); Arrhenius E uncertainty propagated as sigma_alpha/alpha=sigma_E/(R*T) | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 0.2 | 0.139641 | 0.520291 | 0.679746 | match |
| kems-010-richter-2007 | richter_2007_mg_cai_langmuir_alpha_arrhenius | alpha | Mg | temperature_K=2023 | 0.107388 | absolute=0.3383970051344181 (point.sigma); Arrhenius E uncertainty propagated as sigma_alpha/alpha=sigma_E/(R*T) | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 0.2 | 0.0926118 | 0.270073 | 0.273678 | match |
| kems-010-richter-2007 | richter_2007_mg_cai_langmuir_alpha_arrhenius | alpha | Mg | temperature_K=2173 | 0.176453 | absolute=0.5176490661392505 (point.sigma); Arrhenius E uncertainty propagated as sigma_alpha/alpha=sigma_E/(R*T) | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 0.2 | 0.0235469 | 0.0544007 | 0.0454881 | match |
| kems-037-richter-2002 | richter_2002_mg_cai_langmuir_gamma | alpha | Mg | temperature_K=1723 | 0.04 | absolute=0.05 (documented default) | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 0.2 | 0.16 | 0.69897 | 3.2 | mismatch |
| kems-037-richter-2002 | richter_2002_mg_table1_per_run_areas_b1 | alpha | Mg | temperature_K=1923.15 | 0.04 | absolute=0.05 (documented default) | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 0.2 | 0.16 | 0.69897 | 3.2 | mismatch |
| richter-et-al-2007 | richter_2007_mg_cai_arrhenius_langmuir | alpha | Mg | temperature_K=1873 | 0.0603586 | absolute=0.2054316518789593 (point.sigma); Arrhenius E uncertainty propagated as sigma_alpha/alpha=sigma_E/(R*T) | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 0.2 | 0.139641 | 0.520291 | 0.679746 | match |
| richter-et-al-2007 | richter_2007_mg_cai_arrhenius_langmuir | alpha | Mg | temperature_K=2023 | 0.107388 | absolute=0.3383970051344181 (point.sigma); Arrhenius E uncertainty propagated as sigma_alpha/alpha=sigma_E/(R*T) | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 0.2 | 0.0926118 | 0.270073 | 0.273678 | match |
| richter-et-al-2007 | richter_2007_mg_cai_arrhenius_langmuir | alpha | Mg | temperature_K=2173 | 0.176453 | absolute=0.5176490661392505 (point.sigma); Arrhenius E uncertainty propagated as sigma_alpha/alpha=sigma_E/(R*T) | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 0.2 | 0.0235469 | 0.0544007 | 0.0454881 | match |
| fedkin-grossman-ghiorso-2006 | fedkin_2006_yu_na_vacuum_langmuir | alpha | Na | temperature_K=1723.15 | 0.26 | absolute=0.05 (observation.uncertainty.alpha_range); published alpha range half-width | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 1 | 0.74 | 0.585027 | 14.8 | mismatch |
| kems-005-fedkin-2006 | fedkin_2006_na_class_b1 | alpha | Na | temperature_K=1723.15 | 0.26 | absolute=0.05 (observation.uncertainty.alpha_range); published alpha range half-width | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 1 | 0.74 | 0.585027 | 14.8 | mismatch |
| kems-005-fedkin-2006 | fedkin_2006_na_yu_langmuir | alpha | Na | temperature_K=1723.15 | 0.26 | absolute=0.05 (observation.uncertainty.alpha_range); published alpha range half-width | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 1 | 0.74 | 0.585027 | 14.8 | mismatch |
| kems-012-sossi-2019 | sossi_2019_na_alpha_e_authors_adopted_unity | alpha | Na | temperature_K=1698.15 | 1 | absolute=0.05 (documented default) | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 1 | 0 | 0 | 0 | match |
| kems-012-sossi-2019 | sossi_2019_na_alpha_e_gamma_derived_range | alpha | Na | temperature_K=1673.15 | 1 | absolute=0.7 (point.sigma) | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 1 | 0 | 0 | 0 | match |
| kems-012-sossi-2019 | sossi_2019_na_class_and_transport_b1 | alpha | Na | temperature_K=1698.15 | 1 | absolute=0.7 (observation.values.alpha_range); published alpha range half-width | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 1 | 0 | 0 | 0 | match |
| sossi-et-al-2019 | sossi_2019_na_open_furnace_apparent | alpha | Na | temperature_K=1698.15 | 1 | absolute=0.04999999999999999 (observation.uncertainty.alpha_range); published alpha range half-width | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 1 | 0 | 0 | 0 | match |
| costa-jacobson-2015 | costa_jacobson_2015_sio_olivine_kems | alpha | SiO | temperature_K=1750 | 0.0195 | absolute=0.016499999999999997 (point.sigma) | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 0.0633145 | 0.0438145 | 0.511469 | 2.65542 | mismatch |
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
| kems-007-costa-2015 | costa_2015_sio_olivine_class_and_motzfeldt_absence_b1 | alpha | SiO | temperature_K=1750 | 0.0195 | absolute=0.016499999999999997 (point.sigma) | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 0.0633145 | 0.0438145 | 0.511469 | 2.65542 | mismatch |
| kems-007-costa-2015 | costa_2015_sio_olivine_kems_alpha_multicell | alpha | SiO | temperature_K=1700 | 0.003 | absolute=0.016499999999999997 (observation.uncertainty.alpha_range); published alpha range half-width | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 0.0595122 | 0.0565122 | 1.29748 | 3.42498 | mismatch |
| kems-007-costa-2015 | costa_2015_sio_olivine_kems_alpha_multicell | alpha | SiO | temperature_K=1725 | 0.022 | absolute=0.016499999999999997 (observation.uncertainty.alpha_range); published alpha range half-width | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 0.0614115 | 0.0394115 | 0.445827 | 2.38858 | mismatch |
| kems-007-costa-2015 | costa_2015_sio_olivine_kems_alpha_multicell | alpha | SiO | temperature_K=1750 | 0.036 | absolute=0.016499999999999997 (observation.uncertainty.alpha_range); published alpha range half-width | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 0.0633145 | 0.0273145 | 0.245201 | 1.65542 | mismatch |
| kems-007-costa-2015 | costa_2015_sio_olivine_kems_alpha_multicell | alpha | SiO | temperature_K=1795 | 0.026 | absolute=0.016499999999999997 (observation.uncertainty.alpha_range); published alpha range half-width | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 0.0667466 | 0.0407466 | 0.409456 | 2.46949 | mismatch |
| kems-007-costa-2015 | costa_2015_sio_olivine_kems_alpha_multicell | alpha | SiO | temperature_K=1800 | 0.029 | absolute=0.016499999999999997 (observation.uncertainty.alpha_range); published alpha range half-width | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 0.0671284 | 0.0381284 | 0.364508 | 2.31081 | mismatch |
| kems-008-schaefer-fegley-2004 | schaefer_fegley_2004_sio_alpha_s_survey | alpha | SiO | temperature_K=2273 | 0.04 | absolute=0.08 (observation.values.alpha_range); published alpha range half-width | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 0.102783 | 0.0627833 | 0.409863 | 0.784792 | match |
| kems-008-schaefer-fegley-2004 | schaefer_fegley_2004_sio_alpha_s_survey | alpha | SiO | temperature_K=2273 | 0.2 | absolute=0.08 (observation.values.alpha_range); published alpha range half-width | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 0.102783 | -0.0972167 | 0.289107 | 1.21521 | mismatch |
| kems-010-richter-2007 | richter_2007_sio_cai_langmuir_alpha_arrhenius | alpha | SiO | temperature_K=1873 | 0.0687787 | absolute=0.1634119997736591 (point.sigma); Arrhenius E uncertainty propagated as sigma_alpha/alpha=sigma_E/(R*T) | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 0.072704 | 0.0039253 | 0.0241043 | 0.0240209 | match |
| kems-010-richter-2007 | richter_2007_sio_cai_langmuir_alpha_arrhenius | alpha | SiO | temperature_K=2023 | 0.106584 | absolute=0.2344569366817506 (point.sigma); Arrhenius E uncertainty propagated as sigma_alpha/alpha=sigma_E/(R*T) | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 0.0841227 | -0.0224611 | 0.102778 | 0.0958007 | match |
| kems-010-richter-2007 | richter_2007_sio_cai_langmuir_alpha_arrhenius | alpha | SiO | temperature_K=2173 | 0.155477 | absolute=0.3183997506581433 (point.sigma); Arrhenius E uncertainty propagated as sigma_alpha/alpha=sigma_E/(R*T) | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 0.0953941 | -0.0600825 | 0.212143 | 0.188701 | match |
| richter-et-al-2007 | richter_2007_si_cai_arrhenius_langmuir | alpha | SiO | temperature_K=1873 | 0.0687787 | absolute=0.1634119997736591 (point.sigma); Arrhenius E uncertainty propagated as sigma_alpha/alpha=sigma_E/(R*T) | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 0.072704 | 0.0039253 | 0.0241043 | 0.0240209 | match |
| richter-et-al-2007 | richter_2007_si_cai_arrhenius_langmuir | alpha | SiO | temperature_K=2023 | 0.106584 | absolute=0.2344569366817506 (point.sigma); Arrhenius E uncertainty propagated as sigma_alpha/alpha=sigma_E/(R*T) | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 0.0841227 | -0.0224611 | 0.102778 | 0.0958007 | match |
| richter-et-al-2007 | richter_2007_si_cai_arrhenius_langmuir | alpha | SiO | temperature_K=2173 | 0.155477 | absolute=0.3183997506581433 (point.sigma); Arrhenius E uncertainty propagated as sigma_alpha/alpha=sigma_E/(R*T) | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 0.0953941 | -0.0600825 | 0.212143 | 0.188701 | match |
| wetzel-gail-2013-sio-arrhenius | wetzel_gail_2013_sio_arrhenius | alpha | SiO | temperature_K=1000 | 0.0130505 | absolute=0.05 (documented default) | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 0.0130505 | 0 | 0 | 0 | match |
| wetzel-gail-2013-sio-arrhenius | wetzel_gail_2013_sio_arrhenius | alpha | SiO | temperature_K=1400 | 0.0374006 | absolute=0.05 (documented default) | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 0.0374006 | 0 | 0 | 0 | match |
| wetzel-gail-2013-sio-arrhenius | wetzel_gail_2013_sio_arrhenius | alpha | SiO | temperature_K=1800 | 0.0671284 | absolute=0.05 (documented default) | unavailable (engine path exposes no quantitative model uncertainty) | not computable (engine uncertainty unavailable) | 0.0671284 | 0 | 0 | 0 | match |

Assumption-only engine diagnostics (visible negative results, but excluded from comparable coverage, headlines, and residual pins):

| Source | Observation | Type | Species | Coordinate | Literature | Assumption-only engine value | Raw residual dex | Typed gaps | Status |
|---|---|---|---|---|---:|---:|---:|---|---|
| kems-022-demaria-1971 | demaria_1971_fe_activity_multi_rotating_cell | activity_coefficient | Fe | window=temperature-not-stated | 1 | 0.135318 | 0.868646 | typed-refusal:missing_capability:documented_melt_activity_coefficient:FeO; typed-refusal:missing_condition:source_sample_composition; typed-refusal:missing_capability:reference_state_conversion:pure_Fe_to_FeO; typed-refusal:unsupported_observable:qualitative_activity_not_point | assumed-input (excluded) |
| kems-022-demaria-1971 | demaria_1971_fe_lunar_basalt_kems_main_cell | activity_coefficient | Fe | temperature_K=1550 | 1 | 0.135318 | 0.868646 | typed-refusal:missing_capability:documented_melt_activity_coefficient:FeO; typed-refusal:missing_condition:source_sample_composition; typed-refusal:missing_capability:reference_state_conversion:pure_Fe_to_FeO; typed-refusal:unsupported_observable:qualitative_activity_not_point | assumed-input (excluded) |
| kems-010-richter-2007 | richter_2007_mg_rate_series_geometry | rate_series | Mg | temperature_K=2173.15 | 0.00251189 | 0.536913 | 2.3299 | typed-refusal:missing_condition:melt_composition; typed-refusal:missing_condition:pO2_boundary | assumed-input (excluded) |
| kems-010-richter-2007 | richter_2007_mg_rate_series_geometry | rate_series | Mg | temperature_K=2073.15 | 0.000630957 | 0.0799963 | 2.10307 | typed-refusal:missing_condition:melt_composition; typed-refusal:missing_condition:pO2_boundary | assumed-input (excluded) |
| kems-010-richter-2007 | richter_2007_mg_rate_series_geometry | rate_series | Mg | temperature_K=1973.15 | 0.000125893 | 0.0097594 | 1.88942 | typed-refusal:missing_condition:melt_composition; typed-refusal:missing_condition:pO2_boundary | assumed-input (excluded) |
| kems-010-richter-2007 | richter_2007_mg_rate_series_geometry | rate_series | Mg | temperature_K=1873.15 | 1.58489e-05 | 0.000926307 | 1.76675 | typed-refusal:missing_condition:melt_composition; typed-refusal:missing_condition:pO2_boundary | assumed-input (excluded) |

<!-- END t-512 extract-store reproduction rollup -->

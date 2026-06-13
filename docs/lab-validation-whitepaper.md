# Validation of a First-Principles Vacuum-Pyrolysis Process Model Against Gram-Scale Solar-Furnace Experiments

**Draft v0 — skeleton (2026-06-12). JBIS voice. Table slots are marked
`<!-- SLOT:name source=<campaign deposit> status=<draft|refined|final> -->`
and carry placeholder column structures; replace the table body, never the
slot marker, so provenance survives refinement.**

> **Data provenance.** Every data table in this document was compiled against
> regolith-pyrolysis-simulator **v0.5.6**, git commit
> **`f3f2c6a`** (`f3f2c6ae98c9febe81d6fc5b9d0718202c6f3b9c`),
> commit date **2026-06-12**, compiled **2026-06-12 (UTC)**. All numeric
> values, parameter-provenance classes, and `file:line` source references
> reflect that committed repository state; the working tree was clean for
> `data/`, `simulator/`, and `engines/` at compile time. This stamp pins the
> commit the table *values* were drawn from — a later stamp-only edit does not
> change it. When a table is refined against a newer build, bump this stamp and
> record the per-row code version in Appendix C.

---

## Abstract

<!-- SLOT:abstract source=design-robinot-reconciliation rev 4 status=refined -->

A first-principles analytical model of solar-thermal vacuum pyrolysis of
regolith is compared without parameter fitting against the gram-scale
solar-furnace experiments of Robinot et al. (2026), with secondary anchors
from Šeško et al. (2024) and Sauerborn (2005). The converged result is a
split accuracy envelope, not a tuned match: one literature-alpha/top-area
Robinot variant is order-unity on total non-O2 vapor/capture (`1.1-2.0x`
after oxidation-state and mass-account bands), while analyzer-visible free
O2 is still `18.75x` high before any downstream sink is credited. The O2
miss is therefore treated as a four-channel sink-allocation problem
(plume oxidation, deposit gettering, melt-redox retention, and post-run air
oxidation), not as a scalar alpha or area correction. The exp. 1 / exp. 2
O2 reproducibility floor is about `11%`; daylight below that is not a
meaningful validation target for the present corpus.

## 1. Introduction

### 1.1 Context: regolith pyrolysis as a multi-product ISRU route
<!-- Prose: the pressure/temperature-lever bet (Mandate §1), where validation fits. Cite MRE/hydrogen/carbothermal baselines. -->

### 1.2 The validation posture
<!-- Prose: no curve-fitting policy; negative results as results; the experiment as a hypothesis subject to the same adversarial standard; "headline accuracy is the product". One paragraph on why a published accuracy envelope serves users better than a tuned match. -->

### 1.3 The empirical corpus

<!-- SLOT:corpus-table source=hypothesis-registry.md + sauerborn-anchors.md status=refined -->

| Source | Apparatus | Scale | Regime | Observables | Role in this work |
|---|---|---|---|---|---|
| Robinot et al. 2026 (exp. 1) | PROMES 1.5 kW solar furnace, flow-through Ar at 13 mbar | 3.38 g pellet (EAC-1A) | viscous/transitional | O2 time series, per-surface deposit masses (Fig. 4c), 60 min | primary anchor |
| Robinot et al. 2026 (exp. 2, supplement) | same rig, lower heating rate | 3.35 g pellet | viscous/transitional | full 1 s instrument record (O2, power, energy), 95 min | reproducibility floor; secondary anchor |
| Šeško et al. 2024 | purpose-built high-vacuum solar furnace | 10 g EAC-1A simulant sample placed in an alumina ceramic crucible. | reported pressure 1e-7..1e-2 mbar; surface temperature 1750 K to 1900 K; transport_model: molecular_transitional_regime_p0b_blocked | Deposits were inspected after the run by SEM/EDX and polished cross-section microscopy. | regime discriminator (pending P0b+ transport wiring) |
| Sauerborn 2005 (3 selected of 8) | DLR Cologne high-flux solar furnace, modified 34 L polished stainless vacuum chamber, 295 mm diameter x 300 mm high, glass dome top, approximate inner area 0.74 m2. | 0.991 g powder; 7.856 g powder/granulate; 0.6072 g powder | target before each pyrolysis run was below 1e-4 mbar; JSC-1 plotted chamber about 1e-3 mbar and QMS about 1e-5 mbar | mass loss, O2 onset/rise, RFA/EDX volatile depletion, cold-trap/cone/glass deposits | independent cross-validation (pending presets) |

## 2. The Analytical Model

### 2.1 Derivation chain from melt to outlet
<!-- Prose: HKL evaporation flux, activity/Ellingham chain, P_sat sources, condensation routing, transport regime. Cite the kernel docs. -->

### 2.2 Parameter provenance — the no-fitting ledger

<!-- SLOT:parameter-provenance source=derivation-audit-findings.md status=refined -->

Class counts: 11 first-principles / 14 literature / 35 assumed / 8 fitted.

**FITTED entries (8; debt to eliminate under the no-fitting policy):**

| # | Subsystem | Parameter / value | Where set | Robinot-path sensitivity |
|---:|---|---|---|---|
| 17 | P_sat runtime | Na runtime Antoine `(8.477035,11265.231371,0)`, `pseudo_psat_backsolved_from_vaporock` | `data/vapor_pressures.yaml::metals.Na.antoine`; convention in `engines/builtin/vapor_pressure.py:59-71` | Medium if builtin fallback used. |
| 18 | P_sat runtime | K runtime Antoine `(3.869571,4961,0)`, VapoRock-backsolved | `data/vapor_pressures.yaml::metals.K.antoine` | Medium/low if fallback used. |
| 19 | P_sat runtime | Mg runtime Antoine `(10.628931,6788.644019,0)`, VapoRock-backsolved | `data/vapor_pressures.yaml::metals.Mg.antoine` | Medium/high if fallback used. |
| 20 | P_sat runtime | Fe runtime Antoine `(12.404333,19156.973681,0)`, VapoRock-backsolved | `data/vapor_pressures.yaml::metals.Fe.antoine` | High if fallback used. |
| 21 | P_sat runtime | SiO runtime Antoine `(22.117682,40638.351545,0)`, VapoRock-backsolved | `data/vapor_pressures.yaml::oxide_vapors.SiO.antoine` | Very high if fallback used. |
| 22 | P_sat runtime | CrO2 runtime Antoine `(12.9245114,23732.9593,0)`, JANAF0 fallback fit | `data/vapor_pressures.yaml::oxide_vapors.CrO2.antoine/reaction` | Low/medium. |
| 39 | Condensation routing | Condensation temps Fe/Mg/Na/K/Ca/Mn/Cr/Al/Ti, operator-tuned against P_sat curves | `simulator/condensation.py:390-432`, `data/setpoints.yaml:820-832` | Medium/high; controls stage assignment. |
| 66 | Lab overlay | Required closure factor `alpha * area = 0.03969` | `campaign analysis 2026-06`, `:112-126` | Very high; paper-derived diagnostic, not allowed as hidden runtime scalar. |

| Rank | Soft spot | Deposit finding |
|---:|---|---|
| 1 | HKL source area / exposed-area basis | row 4 and row 68. Current default `0.2 m2` is a tonne-scale pool area; Robinot is a gram-scale pellet/powder bed. Sensitivity is linear and the design doc identifies `alpha * area = 0.03969` as the closure scale. |
| 2 | Lab alpha-area closure | rows 8, 12-15, 66, 67. Existing alpha table has real pins for Fe/Mg/SiO/Na but K/proxy species remain assumption debt; the paper-derived `0.03969` factor is FITTED and must stay diagnostic/lab-gated if ever used. |
| 3 | SiO vapor-pressure path | rows 21, 33, 35, 36. SiO dominates non-O2 vapor; runtime pseudo-Antoine is a VapoRock-backsolved fit, activity uses ideal oxide fraction, and the hard-vacuum pO2 floor directly controls suppression. |
| 4 | Sticking / wall capture coefficients | rows 43-45. Current `alpha_s` values are migrated constants, not literature-pinned values. New wall-material table currently references those defaults; it does not supply replacement measured sticking coefficients. |
| 5 | Condensation temperatures and stage bands | rows 39-42. Several values are explicitly operator-tuned or engineering midpoints. They control apparent surface/stage split and can move mass between condenser/filter/wall buckets. |
| 6 | Transport geometry and wall temperature | rows 57-58. `d=0.12 m`, `L=1 m`, `T_wall=1500 C` are industrial/lab-generic assumptions, not Robinot apparatus-specific geometry. Deposit and conductance sensitivity is high. |
| 7 | Activity coefficient model for metals | rows 31-34. Ellingham data are literature-pinned, but `a_oxide = wt_pct/100` is an idealized activity proxy. That is not a first-principles melt activity model. |
| 8 | Knudsen/viscous blending | rows 55-56. Regime thresholds are standard, but `Kn/(Kn+0.01)` is an assumed smooth bridge. Important near transition; less important if the faithful run is deep in one regime. |
| 9 | Diffusion/viscosity estimates | rows 50-52 and row 60. Fallback diffusion, transition-metal/SiO LJ estimates, and N2-like viscosity are order-of-magnitude transport assumptions. |
| 10 | Legacy Antoine rows for Ca/Al/Cr/Mn/Ti/Si | rows 23-30. Several source strings explicitly say `LEGACY_DERIVATION_VALUE` and TODO replacement. Ca/Al matter most among these in the faithful vapor mass ledger. |
| 11 | pO2 control setpoints and precision | row 63. These are recipe/control assumptions, not thermodynamic constants; sensitivity is high for SiO/O2, but the faithful Robinot design doc already falsifies pO2-floor-only closure. |
| 12 | Turbine energy simplification | row 64. Soft but low for Robinot mass reconciliation. |

### 2.3 Evaporation coefficients: principled treatment

<!-- SLOT:alpha-table source=alpha-principles.md status=refined -->

| Species | alpha value or band | Sources + conditions | Notes / lineage / uncertainty | Status (pinned/banded/assumption) |
|---|---|---|---|---|
| Na | 1.0 (tier 2); recommended band 0.9-1.0 | Sossi et al. 2019 GCA 260:204 (ferrobasalt FCMAS, open furnace mass-loss, 1300-1550 C, near-ideal); Sossi & Fegley 2018 liquids ~unity | Open-sweep regime matches simulator; Fedkin 2006 KEMS 0.13 (sealed, back-flux high) rejected for our conditions. Envelope already good. | pinned (Yes (pinnable)) |
| K | 1.0 (tier 2, Na-analogy); recommended band 0.8-1.0 | Analogy to Na (Sossi 2019 + Sossi & Fegley 2018 "liquids commonly near unity") | No direct high-T basaltic melt pin found for K; alkali behavior similar. Assumption-with-sensitivity justified but not pure invention. | banded (Banded (analogy)) |
| Mg | 0.20 (tier 2); recommended band 0.05-0.25 | Richter et al. 2002/2007 (Type B CAI-like basaltic liquids, vacuum, ~1800 C, alpha~0.1-0.2 from mass+isotope); Hashimoto 1990 forsterite; SF2004 Table 10; Mendybaev/Knight forsterite-rich melts ~0.05-0.2 | Closest to regolith: Richter CAI liquids (basalt-like). Composition dep (forsterite vs multicomponent). | banded (Banded) |
| Fe | 0.02 (tier 2); recommended band 0.01-0.2 (melt); ~1 (pure metal) | Costa & Jacobson 2015 KEMS Fo93Fa7 olivine Fe+ 0.011-0.020 (1700-1800 K); Ebel 2005 proxy ~0.2 noted high; Safarian & Engh 2013 pure Fe/metal ~1 (weak evap) | From FeO in melt: reduction + oxide kinetics lower vs pure metal. pO2/fO2 sensitive. | banded (Banded) |
| SiO (from SiO2 melt) | 0.04 (tier 2); recommended band 0.01-0.1 (0.03-0.06 central for high-T silicate) | SF2004 Table 10 SiO2(liq) + Hashimoto 1990 alpha_s=0.038-0.048; Costa & Jacobson 2015 olivine SiO+ 0.003-0.036; Richter et al. 2002 CAI melts ~0.1-0.2 | Lineage of "0.038+/-0.005@1235K": Hashimoto 1990 forsterite evap (Nature 347:53, low-P H2/solid or low-T run at 1235 K / 962 C cited in Tachibana refs); SF2004 reinterp/analog for liquid SiO2 at higher T. Not direct molten basalt 1800 C. Richter CAI (basalt-like) higher side. Broad but literature-constrained. | banded (Banded (pinnable within factor ~3)) |
| Pure Si (metal) | 1.0 (tier 2, pure_elemental_only); recommended band 0.84-1.0 (per Safarian) | Safarian & Engh 2013 Metall. Mater. Trans. A 44:747-753 (pure Si vacuum evap branch) | Simulator does not use; SiO from oxide dominates. Confirms alpha~1 for atomic metal evap. | pinned (Yes (for pure metal)) |
| Ca, Al, Ti | 0.3-0.9 (tier 2, proxies); recommended band 0.1-1.0 (wide) | Zhang et al. 2014 CaTiO3 melt 2005 C (Ca/Ti activity proxies); Schaefer & Fegley 2004 + Shahar & Young 2007 CAI modeling (Al) | Not direct HKL alpha for target species from regolith basalt; activity proxies. Broad uncertainty. | assumption (Assumption / broad proxy) |
| Cr, Mn, others | tier 3 (no value); recommended band N/A (fail-loud unless fallback) | No direct HKL alpha in searched sources (speciation/redox papers exist: Sossi 2018 CrO2/CrO3; Klemme 2022) | Tier 3 policy correct; remain assumption-with-sensitivity or disable. | assumption (Assumption-only) |

### 2.4 Geometry: evaporation area as a derived quantity
<!-- Prose + slot when area-geometry lands: area from melt volume + vessel geometry; the shallow-pot aspect-ratio result if it survives. -->
<!-- SLOT:area-model source=area-geometry-findings.md status=refined -->

| Area audit item | Site / value | Finding |
|---|---|---|
| Rigid HKL site | `simulator/state.py:506-508` | `MeltState` carries `total_mass_kg` as a derived quantity but initializes `melt_surface_area_m2: float = 0.2`. This is the live default consumed by HKL. |
| Rigid HKL site | `data/setpoints.yaml:1117-1119` | Furnace/crucible setpoints declare `diameter_m: 0.5` and `melt_surface_area_m2: 0.2`. Repo search found no runtime assignment from this setpoint into `MeltState`; it is a duplicate/declarative assumption, not the live mass-derived source. |
| Current mass feedback | `total_mass_kg` can fall | if a run vaporizes 37% of the melt, `total_mass_kg` can fall, but HKL area remains the same `MeltState.melt_surface_area_m2` scalar unless some external caller mutates it. |
| Lab seam | P2 lab path | the P2 lab path already derives/configures wall capture geometry. It does not yet derive HKL exposed melt area. Robinot has explicit missing fields for exposed melt/crucible opening area. Industrial runs still use the rigid `MeltState` scalar. |

Derived-area model summary: `melt_surface_area_m2` should be an hourly derived input to HKL, not a fixed scalar. Proposed data model: `area_basis` one of `legacy_fixed`, `lab_exposed_area`, `industrial_pot_geometry`; lab basis uses `lab_geometry.sample.exposed_melt_area_m2`; industrial basis requires declared pot geometry (`radius_m` or `diameter_m`, `usable_depth_m`, `freeboard_fraction`) or `shape: cylindrical` plus `initial_aspect_h_over_r`; density basis starts with `EquipmentDesigner.MELT_DENSITY_KG_M3 = 2700.0` or a future composition-weighted density model.

Runtime derivation: read melt inventory from the mol-native ledger and project to kg only at the boundary; compute `V_melt(t) = m_melt(t) / rho_melt(t)`; for declared radius compute `fill_depth(t) = V_melt(t) / (pi R^2)`; normal fill uses `A_exposed(t) = pi R^2`; aspect input derives `R = (V0 / (pi a))^(1/3)`, then freezes pot geometry and updates only fill level over time.

Refusal shape: `missing_melt_geometry_for_derived_area`; `invalid_pot_geometry`; `pot_overfilled`; `unsupported_dynamic_area_basis`; lab missing `exposed_melt_area_m2` stays diagnostic-only/fail-closed and does not fall back to industrial `0.2 m2`.

Shallow-pot verdict: shallow pots are a credible throughput lever, but "maximize area per kg" is not the same as "best furnace." The first implementable design change is geometry-derived HKL area with fail-closed pot specs and digesting; the next physics gap is a frozen-skull wall/bottom heat-flux model.

## 3. Reproduction Method

### 3.1 Faithful-run protocol
<!-- Prose: preset schema (conditions captured verbatim with units; provenance classes; assumption-with-sensitivity marking), runner --preset/--leg, digest-scoped artifacts, mass balance closing exactly. The Šeško idealized-schedule catch as an example of the fidelity standard. -->

### 3.2 What "faithful" cannot capture
<!-- Prose: the four assumption-marked geometry surfaces (paper states no numeric geometry); sensitivity treatment. -->

## 4. Results: Model–Experiment Daylight

### 4.1 Oxygen yield

<!-- SLOT:o2-comparison source=o2-probes-findings.md + attack-alpha-findings.md + design-robinot-reconciliation rev 4 status=refined -->

| Quantity | Experiment (exp. 1 / exp. 2) | Model (faithful) | Model (literature-α forward prediction, band) | Daylight |
|---|---|---|---|---|
| O2 yield (mg) | `35` / `39.229` | about `882` (`25.20x` exp. 1) | `656.204` central (`18.25x-19.04x` area band; `18.75x` central) | Faithful raw is `25.20x` high; literature-alpha/top-area is still `18.75x` high. Missing free-O2 equivalent: `0.621204 g`. |
| O2 yield (% of `3.38 g` sample) | `1.05%` / `1.16%` | about `26.1%` | about `19.4%` central | Above the `~11%` exp. 1 / exp. 2 reproducibility floor by order-of-magnitude; not a run-to-run scatter issue. |
| Free-O2 / captured-vapor ratio | `0.035 g / 1.26 g = 0.028` | high, not sink-corrected | model source chemistry implies a reduced-source floor `>=0.2` before downstream sinks | The measured ratio is below the stoichiometric floor, so free O2 must be consumed or retained after source emission. |

*Experimental reproducibility floor: |1.17 − 1.05| / 1.11 ≈ 11 % — the
benchmark against which model daylight is judged.*

### 4.2 Per-surface deposition

<!-- SLOT:wall-comparison source=wall-probes-findings.md status=refined -->

| Surface | Experiment (g, Fig. 4c) | Model (faithful) | Model (conductance-routed) | Daylight |
|---|---:|---:|---:|---|
| Holder | 0.20 | 0.000011986 | | pending-synthesis |
| Window | 0.35 | 0.000296237 | | pending-synthesis |
| Condenser | 0.20 | 0.000155767 | | pending-synthesis |
| Filter | 0.51 | 0.000000000 | 0.000321760 | pending-synthesis |

Conductance-routed source variant: `filter_reachable_paper_share_area` has Wall total g `0.000785623`, Filter g `0.000321760`, Filter share `40.96%`, Filter species `K, Mg, Na, SiO`.

### 4.3 Time-resolved evolution
<!-- SLOT:timeseries-comparison source=supplement-exp2 CSVs + staged-T rerun artifacts status=refined -->
Exp. 2 has a 1-second O2 record available; the staged-temperature model comparison is still pending, so no model-vs-experiment time-series plot is claimed here.

### 4.4 Hypothesis disposition

<!-- SLOT:hypothesis-disposition source=hypothesis-registry.md + all attack-wave deposits + design-robinot-reconciliation rev 4 status=final -->

Final count: `2` killed, `4` weakened, `10` survive.

| # | Hypothesis | Disposition | Evidence | What it now means |
|---|---|---|---|---|
| M1 | Source-side alpha-area kinetics | killed | Literature alpha plus honest top area predicts O2 `18.75x` and vapor `1.365x`; O2-matching post-hoc area gives vapor `0.093x`. | Alpha remains parameter debt, but alpha-area as the main O2 closure mechanism is killed. |
| M2 | Pellet/effective area basis | weakened | Honest top-area variants still leave O2 `18.25x-19.04x` high. | Area is a diagnostic and cache seam, not a behavior fix by itself. |
| M3 | Oxygen sink decomposition | survives | Stoichiometric O2 deficit; SiO is the mature oxygen-bearing source vapor; deposit mass/chemistry, upstream filter, and residual melt redox remain open. | Split into plume oxidation, deposit gettering, melt-redox retention, and post-run air oxidation. Post-run air oxidation can explain oxidized recovered deposits only, not low in-run analyzer O2. |
| M4 | Activity / vapor-pressure chain | weakened | SiO Antoine terms, activity proxy, and pO2 floor remain high-sensitivity, while total vapor is already order-unity. | Standing uncertainty band; do not retune coefficients to hide the oxygen-sink gap. |
| M5 | Open-system transport survival | weakened | Outlet-only survival did not close O2; instrument recommendations keep QMS/pO2 open. | Transport matters only when tied to an atom-conserved sink channel. |
| M6 | Surface routing / sticking / filter | weakened | Direct named wall deposits are `0.000368x` paper total; reachable-filter variant fixes routing but not absolute mass. | Survives for per-surface split and gettering, not as standalone mass/O2 closure. |
| M7 | Thermal-field model | survives | Surface `1850-2400+ deg C` and bulk/effective `1400-1750 deg C` remain plausible. | The `1800 deg C` reading is not a reliable bulk setpoint. |
| E1 | Pyrometer/emissivity T error | survives | Emissivity/front-face bias and partial-melt gradients can be hundreds of K. | True temperature must be measured; sign alone does not close O2. |
| E2 | Wall/condenser actual T | survives | Glass walls `100-400+ deg C`; condenser faces `50-200+ deg C`; opacification can warm surfaces. | Needed for per-surface deposition, gettering, re-evaporation, and air/post-run separation. |
| E3 | O2 ppm x flow integration | killed | Independent rough reconstruction plus image digitization land on the `35 mg` anchor. | The paper O2 integral stands; cross-sensitivity remains a method caveat. |
| E4 | Mass-accounting soft spots | survives | A `0.265 g` discrepancy could raise apparatus deposits from `1.260 g` to `1.525 g` (`+21.0%`). | Use mass bands and oxidation-state caveats; do not force one exact ledger. |
| E5 | Deposit chemistry/location writeup | survives | EDS/XRD/Raman characterize products, but oxygen fraction and post-vent reoxidation are unresolved. | Air-isolated oxidation-state data is mandatory. |
| C1 | Thermal-history equivalence false | survives | Staged history reduces O2 but still leaves `19.52x` high. | Flat one-hour hold is invalid; T(t) is secondary unless coupled to area and sink channels. |
| C2 | Analyzer species specificity | survives | Total vapor and analyzer-visible O2 diverge. | Compare analyzer output only to free O2/O reaching the analyzer, not total O atoms in SiO, deposits, melt, or post-run oxides. |
| C3 | Sample emplacement differences | survives | Robinot used a `13 mm x 10 mm`, `3.38 g` pellet on a cooled holder, partially molten. | Geometry, active fraction, and heat penetration must be explicit. |
| C4 | Cross-paper discriminator scope | survives | Sauerborn and Šeško do not yet carry matching O2/deposit sidecars. | Cross-papers can falsify mechanisms qualitatively; they cannot certify quantitative Robinot closure. |

## 5. Error Budget and Accuracy Envelope

The budget is presented as an additive loss cascade in the manner of a
PVsyst installation loss diagram: for each observable, the raw model
prediction is carried term-by-term down to the measured value, each named
term contributing a sized factor (multiplicative factors are stated in log
space alongside percent-of-gap-explained). The cascade terminates in an
explicit **unexplained residual** row; no residual is ever absorbed into a
named term. Terms are ordered model-parameter → model-structural →
experiment-side, so the reader can see at a glance how much of the daylight
is ours, how much is the bench's, and how much is honestly unattributed.

<!-- SLOT:error-budget source=design-robinot-reconciliation rev 4 status=refined -->

PVsyst-style cascade, kept per observable. Each cascade ends with an explicit
unexplained-residual row; no residual is absorbed into a named term.

| Observable | Cascade row | Class | Current value / factor | Budget meaning |
|---|---|---|---|---|
| Analyzer-visible O2 | Raw faithful model | baseline | about `25.20x` Robinot exp. 1 (`~882 mg` vs `35 mg`) | Faithful raw value before literature-alpha/top-area attack. |
| Analyzer-visible O2 | Literature-alpha/top-area forward prediction | model parameter / geometry | `0.656204 g`, `18.75x` central; `18.25x-19.04x` area band; P4 pressure-floor variant `18.59x` | Alpha and exposed area shrink the miss but do not close it. |
| Analyzer-visible O2 | Experiment reproducibility and integral | experiment | exp. 1 `35 mg`; exp. 2 `39.229 mg`; about `11%` reproducibility floor | The O2 integral stands; cross-sensitivity is a caveat, not a falsifier. |
| Analyzer-visible O2 | Stoichiometric sink requirement | structural | free-O2/vapor `0.028` measured vs `>=0.2` congruent reduced-source floor; missing free-O2 equivalent `0.621204 g` | Requires downstream consumption or retention after source emission. |
| Analyzer-visible O2 | Four-channel allocation | structural | plume oxidation / deposit gettering / melt-redox retention / post-run air oxidation | Channel split is not yet measured; post-run air oxidation cannot reduce in-run analyzer O2. |
| Analyzer-visible O2 | **Unexplained residual** | residual | allocation of the missing `0.621204 g` among the four channels | Remains open until position-resolved gas sampling and air-isolated oxidation-state data exist. |
| Total non-O2 vapor/capture | Literature-alpha/top-area forward prediction | model parameter / geometry | `1.72035 g` vs `1.26 g`, or `1.365x` | Order-unity total vapor with honest area and alpha. |
| Total non-O2 vapor/capture | Mass-account upper band | experiment | `1.525 g` target if the `0.265 g` discrepancy is unrecovered deposit; model ratio about `1.13x` | Target identity can move the comparison by `+21.0%`. |
| Total non-O2 vapor/capture | Reduced-vs-oxidized transported mass | experiment / structural | reduced transported mass could be `0.84-0.97 g`; honest vapor band about `1.1-2.0x` | Oxidation state and account identity dominate the remaining target band. |
| Total non-O2 vapor/capture | Sauerborn active-mass miss | structural | `3.76x-4.78x` overprediction unless active melt fraction / heat penetration is modeled; MS5 active fraction improves to `1.87x` only if `43 wt%` is used as an input | Sauerborn remains a separate active-fraction problem, not a Robinot O2 closure. |
| Total non-O2 vapor/capture | **Unexplained residual** | residual | active melt fraction, heat penetration, and oxidation-state target identity | Must be pre-registered before claiming better than the `1.1-2.0x` band. |
| Per-surface deposition / oxidation state | Direct named wall deposits | baseline | `0.000368x` paper total; holder `0.0000599x`, window `0.000846x`, condenser `0.000779x`, filter `0` | The current named wall accounts do not reproduce paper-scale surface deposits. |
| Per-surface deposition / oxidation state | Unmapped material accounts | structural | `process.condensation_train` `1.842495 g`; `terminal.offgas` `0.283171 g` | Paper-scale material exists, but not in Robinot holder/window/condenser/filter semantics. |
| Per-surface deposition / oxidation state | Surface and sticking bands | model / experiment | Fe on cold Cu/SS near `1`; Na `0.5-1.0`; SiO/Mg broad `0.04-1.0`; wall `100-400+ deg C`, condenser `50-200+ deg C` | Surface temperature, view factors, sticking, and re-evaporation remain unresolved. |
| Per-surface deposition / oxidation state | Four-channel allocation | structural | deposit gettering vs post-run air oxidation must be separated with in-vac witnesses | Recovered EDS/XRD oxygen cannot be treated as in-run analyzer closure. |
| Per-surface deposition / oxidation state | **Unexplained residual** | residual | surface routing plus sink-channel attribution | Open until holder/window/condenser/filter mapping and in-vac oxidation states are measured. |

**Headline accuracy statement** *(the product of this work)*:
<!-- SLOT:headline source=design-robinot-reconciliation rev 4 status=final -->
> Current Robinot-class accuracy: one literature-alpha/top-area Robinot variant is order-unity on total non-O2 vapor/capture (`1.1-2.0x` oxidation-state band), but analyzer-visible O2 is overpredicted by `18.75x`; closure now requires a four-channel oxygen-sink ledger, position-resolved gas sampling, and air-isolated deposit oxidation-state data.

## 6. Discussion

### 6.1 What the daylight means for the refinery concept
<!-- Prose: which design conclusions are robust to the stated envelope (sequencing, selectivity levers) vs which are sensitive (absolute rates, cycle times). -->

### 6.2 Recommendations for future lab benches

<!-- SLOT:instrument-recommendations source=instrument-recommendations.md status=refined -->

RESULT: instrument recs - 4 recommended / 2 impractical-or-partial; top pick calibrated QMS (with strong secondary for true-T pyrometry upgrade + OES).

| Instrument | Discriminates | Feasibility at ~1.5 kW solar rig | Priority |
|---|---|---|---|
| Two-color / multi-wavelength ratio pyrometers (1500-2100+ °C molten silicates) | True melt surface T (ε unknown; reduces gray-body assumption error vs single-band) | Commercial fiber-optic models (Optris CTratio 2M 250-3000 °C, IMPAC ISR 6/320 series, AMETEK Land SPOT+ ratio); mid cost (few k€); vacuum-compatible sensors/ports; fiber routing bypasses some window issues but melt-view still needs clear line to surface before heavy opacification; solar furnace precedent for refractory melts (multi-wave near true mp ±2%). Residual error vs single-band: improved for varying ε but non-gray spectral ε (molten basalt T/wavenumber/Fe dep per Biren) still yields 10s–100s K bias; 3+ wave or spectral better. | Recommended |
| FTIR on low-pressure outlet gas (SiO/CO/H2O) | Vapor speciation (SiO, CO, H2O — IR-active bands); NOT O2 (IR-inactive homonuclear); limited boundary pO2 | Research vacuum/low-P gas cells exist (Bruker, Thermo, Netzsch couplings); high cost for dedicated long-path vacuum cell + heated lines (to prevent cond); mbar total P degrades sensitivity (path-length limited; typical ppm at atm paths, worse at low partial P); SiO has known IR features but reactive (needs care); commercial avail yes but not turnkey for mbar metal-vapor outlet. Optical access: outlet duct/port after reactor (avoids main window opacif); vacuum 1-100 mbar compatible with proper cell. O2 blind spot is critical gap vs hypotheses. Precedent in pyrolysis gas analysis (TGA-FTIR) but low-P solar reactor sparse. | Partially practical (supplement for CO/H2O/SiO speciation; limited mbar sensitivity + O2 blind) |
| Calibrated QMS (quadrupole mass spec) for metal vapors + O2 at mbar pressures | Vapor speciation (SiO/metal/suboxide atomic+molecular ions + O2 directly); quantitative rates via calibrated partial P; boundary-layer pO2 via inlet sampling near melt; indirect deposition timing | Commercial (Hiden HPR/HMT series, Pfeiffer, SRS) with differential pumping or metering valve for mbar-to-UHV; mid-high cost; fully vacuum/mbar compatible (precedent up to 5e-3 mbar direct, higher with inlet); no optical access needed (gas sampling line/port); commercial calibrated modes for quantitative (addresses "prior RGA qualitative-only" note). Fouling: ion source can contaminate in heavy vapor but design mitigations (orifice, protection). Direct solar pyrolysis precedents: TUM solar-vapor pyrolysis thesis (RGA + metering valve on regolith solar furnace, O2 rise detected); PSA 60 kW Oresol solar regolith (QMS integrated, O2 partial P increase at ~1425 °C during solar exposure). 1.5 kW class directly scalable. | Top pick; Recommended (strongest single tool for speciation + pO2 + calibrated vs qualitative) |
| QCM / witness-plate deposition monitoring at 273-600 K surfaces | Per-surface deposition RATES (real-time mass gain vs end-of-run integrals only); helps wall/condenser T (rate on instrumented surfaces); fouling dynamics | Standard in PVD/MBE thin-film (INFICON, Leybold quartz oscillators/controllers); low-mid cost; vacuum compatible at 1-100 mbar (Langmuir regime); crystals operate at 273-600 K (cooled holders common). Optical access not required (surface mount or view port). Major issue in deposition-heavy env (Robinot glass opacified rapidly): QCM crystals saturate/damp quickly under thick metal/suboxide loads (oscillation stops after mg/cm² range); poor "fouling tolerance" without shutters, multiple crystals, or high-mass-load designs. Witness plates (removable, post-weigh gravimetric) are robust for integrals and already used in Robinot-style work; give relative per-surface rates but no real-time. Hybrid (QCM on designated clean condenser + witness array) feasible. | Practical as rate monitor on protected/ designated surfaces or hybrid with gravimetric witnesses; limited standalone robustness in heavy-fouling reactor |
| In-situ optical emission / UV-Vis of the plume (Na/K D-lines, SiO bands) | Vapor speciation (strong Na/K D-lines visible; atomic metals; SiO electronic bands UV/vis; some suboxides); plume release timing/speciation vs T ramp; indirect T via line ratios | Low cost (fiber spectrometers e.g. Ocean Insight / Avantes / Avaspec + collection optics/fiber ports); commercial widely available. Vacuum compatible; plume view through side port or outlet duct (better than direct melt view through opacifying glass). Precedent: high-T furnace / plasma smelting reduction (OES for species dynamics); PVD/evaporation monitoring (OES for metal vapors); some solar-adjacent. Intense solar scatter is challenge for direct melt/plume view in solar furnace (filtering, timing, or downstream duct view mitigates). Na/K D-lines easy/bright; SiO bands documented in evap lit. Complements QMS (optical, non-sampling). | Strong secondary; Recommended (low-cost, real-time speciation/timing supplement; easy Na/K, viable SiO) |
| Concentrated-solar community true-T methods (multi-wave + reflectometry / spectral emissivity corrected pyrometry; flash-assisted notes) | True melt surface T (ε unknown + gradients + non-blackbody); best bound on surface vs single/ratio | Solar furnace / CST labs already standardize spectral emissivity + pyrometry combos (in-situ reflectance or FTIR emissometer + radiance for true T solving); multi-wave common (as #1). Flash methods (laser flash) more for diffusivity/conductivity than real-time T. Reflectometry-corrected pyrometry directly addresses ε(T,λ) for molten oxides/silicates. Precedent: PROMES/solar receiver material tests, high-T oxide emissivity rigs, Giulietti survey (many entries with solar furnace + two-color/FTIR). For 1.5 kW rig: requires additional optical access (fiber/spectrometer port to melt surface) — viable if view maintained before heavy deposit or via dedicated window; cost mid-high for integrated or spectrometer + pyrometer pair. Commercial or lab-built (fiber-optic spectrometers + ratio pyros). Directly tackles the ε=1 assumption + Biren-style T-dep ε of molten basalt at 5 µm. | Strong secondary; Strongly recommended (CST-native, highest fidelity for true surface T hypothesis) |

### 6.3 Limitations and future work
<!-- Prose: Šeško molecular-regime run pending P0b+; Sauerborn presets; Fig. 4c digitization status; what a third dataset adds. -->

### 6.4 Accuracy improvement backlog

Standing list of the data, systematic lab results, and work items that would
shrink specific budget terms. Terse by design; one row each; pointers into
the analysis corpus. Rows retire when a budget term's band tightens.

<!-- SLOT:accuracy-backlog source=design-robinot-reconciliation rev 4 status=refined -->

| Improvement | Channel / budget term | Acceptance condition |
|---|---|---|
| Position-resolved gas sampling with calibrated QMS near melt, pre-filter, and outlet; OES/UV-Vis as timing support | Plume oxidation; analyzer-visible O2 unexplained residual | Shows whether O2/O is consumed between melt and analyzer and whether plume/silica products grow with the missing O2. |
| Air-isolated deposit oxidation-state falsifier on holder/window/condenser/filter witnesses, paired with air-exposed controls | Deposit gettering; post-run air oxidation; per-surface oxidation-state residual | Separates in-vac bound oxygen from post-run reoxidation; post-vent EDS alone is not closure. |
| Pre-registered oxide-vapor ceilings for Na/K/Fe/Mg and related source species | Source-species ceiling; prevents plume oxidation from becoming a tuning knob | Any explanation requiring source oxide vapors above the ceiling is killed rather than tuned. |
| Active-melt-fraction thermal model for Sauerborn MS5 and Robinot-style pellets | Total vapor/capture band; active-mass / heat-penetration residual | Predicts active mass fraction before comparison; the reported Sauerborn `43 wt%` melted fraction is validation data, not an input knob. |
| O-atom ledger split across analyzer free O2/O, SiO/plume products, deposits, and residual melt | Four-channel oxygen-sink ledger; O2 residual | Reports atom-conserved diagnostics for plume oxidation, deposit gettering, melt-redox retention, and post-run air oxidation separately. |
| Holder/window/condenser/filter account mapping | Deposit gettering; per-surface split residual | Maps paper surfaces to wall-deposit segments, condensation train, filter semantics, and offgas before claiming per-surface agreement. |

## 7. Conclusions
<!-- Prose: restate the envelope, the no-fitting posture, and the standing invitation to falsify. -->

## Acknowledgements / Data availability
<!-- Note: all comparison artifacts digest-scoped and reproducible from presets; cite the provenance machinery. -->

## References
<!-- Robinot et al. 2026 ASR; Šeško et al. 2024 Acta Astronautica; Sauerborn 2005 (Bonn); Engelschiøn et al. 2020 (EAC-1A); Safarian & Engh 2013; alpha-principles primary sources (Hashimoto, Richter, Mendybaev et al.); JANAF; NIST. -->

---

### Appendix A — Hypothesis registry (full)
<!-- SLOT:appendix-registry source=hypothesis-registry.md status=refined -->

#### Model-Side Hypotheses
| ID | Hypothesis | Current evidence for / against | First-principles prediction required to survive | Kill condition | Attack assignment |
|---|---|---|---|---|---|
| M1 | Source-side alpha-area kinetics: the 25x O2 gap is mainly a missing HKL evaporation coefficient and/or species-alpha surface, not ledger bookkeeping. | For: faithful O2 is 0.881913 g vs 0.035 g, 25.20x high; P3 requires alpha * area = 0.03969, matching the forsterite alpha_c scale (`campaign analysis 2026-06`). O2 probes name HKL alpha*area plus open-system transport as leading (`campaign analysis 2026-06`). Against: sticking pins constrain collector-side condensation, not melt-source evaporation; global alpha scalar is rejected (`campaign analysis 2026-06`). | Independent alpha values must reduce vapor source terms species-by-species in the HKL equation, preserve volatile ordering, and keep the aggregate alpha*area factor near the derived 0.03969 without changing global vapor-pressure data. | Required species alphas fall outside literature/physics envelopes, or O2 only closes by using a hidden global multiplier that breaks Fe/SiO/Na/Mg trends. | W2, W6 |
| M2 | Melt-pool vs pellet area basis: runtime area is pool-scale, while Robinot was a gram pellet/droplet with much smaller or time-varying exposed area. | For: Candidate B says the current HKL runtime uses `MeltState.melt_surface_area_m2`, not lab pellet area (`campaign analysis 2026-06`). Required area at unchanged alpha is 0.00793729 m2; strict 20 mm top area would oversuppress O2 to 1.385 mg (`campaign analysis 2026-06`). Robinot pellet was 13 mm x 10 mm on a water-cooled holder (`campaign analysis 2026-06`). Against: geometry alone can overshoot and is not a blind fix. | Vapor and O2 must scale with exposed effective area over the observed pellet-to-droplet evolution; projected area, roughness, cracks, molten fraction, and porosity must bound the inferred effective area. | Measured or defensible effective area cannot produce the required alpha*area factor, or one area value closes O2 while destroying deposit/species order. | W2, W3 |
| M3 | Oxide/suboxide condensation and oxygen-bearing deposits: some oxygen currently credited to O2 gas should instead be visibly conserved in SiO2/MgO/etc. deposits under lab conditions. | For: Robinot Fig. 4c has 1.26 g apparatus deposits and only 0.035 g O2 (`campaign analysis 2026-06`). The sim emits 0.881913 g O2 and 2.126107 g non-O2 vapor; oxygen partition is explicitly unresolved (`campaign analysis 2026-06`, `campaign analysis 2026-06`). Against: current evidence does not pin deposit oxygen fraction; Candidate C is diagnostic until chemistry is constrained. | Lower analyzer O2 must reappear as atom-conserved oxygen bound in named deposits or condensation accounts, with deposit oxygen fractions compatible with Robinot characterization and without double-crediting the same oxygen. | O2 closes only by disappearing, deposit oxygen fraction required is incompatible with EDS/XRD/Raman, or per-surface masses overproduce Robinot deposits. | W4, W5 |
| M4 | Activity-coefficient / vapor-pressure chain: nonideal melt activities and oxygen fugacity, not just pure-component VP, shift vapor source terms and O/O2 partition. | For: Robinot warns thermodynamic equilibrium has fixed composition and excludes kinetic limitations (`campaign analysis 2026-06`). O/O2 behavior is chemically uncertain in the paper model (`campaign analysis 2026-06`). Against: pO2-floor remediation is falsified as leading; even pO2 = total pressure remains 18.59x high (`campaign analysis 2026-06`, `campaign analysis 2026-06`). | A nonideal activity model must predict species-specific vapor-pressure changes from composition, T, and oxygen potential, and must preserve the volatile sequence seen in Sesko/Sauerborn rather than apply a uniform closure factor. | Independent activity/fugacity calculation yields a small correction, wrong species order, or requires retuning coefficients to the Robinot result. | W2, W6 |
| M5 | Open-system transport survival: Robinot measured O2 that survived flow, recombination, quench, and analyzer transport, not total stoichiometric oxygen generated at the melt. | For: Robinot used an argon carrier and outlet analyzer (`campaign analysis 2026-06`, `campaign analysis 2026-06`). Divergence investigation says paper O2 is outlet-integrated via ppm x flow and identifies survival/quench as a gap (`campaign analysis 2026-06`, `campaign analysis 2026-06`). Against: outlet metric alone was already rejected as leading because analyzer-observable O2 stayed far above 35 mg (`campaign analysis 2026-06`). | O2 survival fraction must follow residence time, flow, pressure, surface recombination, and quench; changing argon flow or pump conductance must produce directional changes in ppm and deposit oxygen. | Analyzer-observable postprocess remains near 8.6e-4 kg under physical transport, or a 25x loss is required with no corresponding wall/deposit oxygen sink. | W1, W4, W5 |
| M6 | Surface-role, sticking, and filter routing: the model has enough total condensible mass, but it is not physically or semantically routed to holder/window/condenser/filter. | For: P5 shows direct wall surfaces are only 0.000464 g while condensation_train is 1.843 g and outbound residual is 2.126 g (`campaign analysis 2026-06`). P6 shows filter reachability creates SiO/Na/Mg/K filter deposits but remains far below 0.51 g (`campaign analysis 2026-06`). Sticking pins find no exact SiO/Fe/Na/Mg pair data for Robinot surfaces, with many bounded assumptions (`campaign analysis 2026-06`, `campaign analysis 2026-06`). | Given surface temperatures, view factors, flow path, and sticking bounds, condensible flux must partition into the paper's holder/window/condenser/filter rows while conserving total vapor and species. | Measured/defensible geometry and sticking cannot move enough mass to paper surfaces, or matching filter mass requires impossible area/capture or wrong species. | W4, W5 |
| M7 | Thermal-field model: a punctual 1800 deg C pyrometer reading is not a one-hour homogeneous melt bulk temperature. | For: Robinot's power/O2 history was staged, with O2 pulses and 33 min decline, not a flat hold (`campaign analysis 2026-06`). The sample touched a cold surface limiting maximum temperature, and the paper calls accurate melt/condensing-surface temperature control critical (`campaign analysis 2026-06`, `campaign analysis 2026-06`). Against: staged time-temperature alone reduced O2 only to 19.52x high (`campaign analysis 2026-06`). | A heat-transfer reconstruction must predict integrated vapor flux from local T(x,t), not a bulk setpoint; lower bulk than hotspot should reduce O2, while true hotter-than-reported T should increase source terms. | Physically plausible thermal fields still produce near-faithful O2/deposit source terms, or require ignoring the observed power/O2 trace. | W3 |

STATUS: MODEL-SIDE - 7 hypotheses registered; 7 assigned; no parked negatives.

#### Experiment-Side Hypotheses
| ID | Hypothesis | Current evidence for / against | First-principles prediction required to survive | Kill condition | Attack assignment |
|---|---|---|---|---|---|
| E1 | Pyrometer/emissivity temperature error: Robinot's reported 1800 deg C may not be the true reacting bulk temperature. | For: pyrometer assumed emissivity 1 (`campaign analysis 2026-06`); Robinot says the temperature may be underestimated because the melt is not a blackbody (`campaign analysis 2026-06`); emissivity may vary during pyrolysis (`campaign analysis 2026-06`). Against: the paper's own flagged sign is underestimation, which worsens O2 overproduction if the model is rerun at true higher T. | Direction must be explicit. If true T > reported, source vapor/O2 should increase: O2 gap worsens, wall-source mass may improve. If true reacting bulk T < reported hotspot, O2 gap can shrink but wall-deposit underprediction worsens and must explain why the paper's non-blackbody warning sign was misleading. | Independent emissivity/thermal reconstruction gives a small correction, the wrong sign, or a sign that cannot improve both O2 and deposit comparisons without another mechanism. | W3, W5 |
| E2 | Actual wall/condenser temperatures are wrong in the comparison, especially after glass opacification increases radiative load. | For: Robinot had a refrigerated copper condenser, porous filter, water-cooled holder/condenser, and visible glass opacification (`campaign analysis 2026-06`, `campaign analysis 2026-06`, `campaign analysis 2026-06`). Robinot says condensing-surface temperature control needs better understanding (`campaign analysis 2026-06`). Sauerborn reports deposition on relatively cool chamber/glass surfaces and cold-trap overheating risk (`campaign analysis 2026-06`, `campaign analysis 2026-06`). | Direction must be explicit. Colder actual surfaces raise capture/sticking and can lower analyzer-visible oxygen-bearing vapor. Hotter opacified glass lowers net sticking/re-evaporation thresholds and should reduce deposits unless source flux rises. | Surface-temperature envelope cannot move capture by orders of magnitude, or the inferred temperatures contradict refrigerated/water-cooled hardware and observed deposits. | W4, W5 |
| E3 | O2 quantification error in ppm x flow integration: the 35 mg O2 number may be biased by analyzer/flow/baseline assumptions. | For: Robinot used a Systech O2 analyzer with 0.1 ppm to 1% range and +/-2% reading precision (`campaign analysis 2026-06`), with argon at 0.3 NL/min and about 13 mbar (`campaign analysis 2026-06`). Divergence investigation describes the ppm x outlet-flow integration method (`campaign analysis 2026-06`). Against: a normal instrument error is far smaller than 25x unless there is an unmodeled method failure. | Direction must be explicit. Overestimated ppm/flow makes paper O2 lower and worsens the sim gap. Underestimated ppm/flow, missed peaks, baseline subtraction, or leakage can raise paper O2 and help close the gap, but must quantify a factor near 25 if used alone. | Raw trace/flow/baseline error budget is <<25x, or the required correction has the wrong sign. | W1, W5 |
| E4 | Mass accounting soft spots: Fig. 4c, text, and the 0.265 g discrepancy may not define one closed material ledger. | For: design records Fig. 4c rows: 1.82 g glass lump, 0.20 g holder, 0.35 g window, 0.20 g condenser, 0.51 g filter, 0.035 g O2, 0.265 g discrepancy, 3.380 g initial (`campaign analysis 2026-06`). Robinot text says 1.1 g was vaporized/captured on cold surfaces while the figure rows sum to 1.26 g apparatus deposits (`campaign analysis 2026-06`). Faithful report shows simulator recovery is 100% vs paper 92% (`campaign analysis 2026-06`). | Discrepancy allocation must obey mass and element balance. If all 0.265 g were missed O2, O2 anchor rises to 0.300 g and the gap shrinks but does not vanish. If it is missed deposits, paper apparatus capture rises and direct wall gap worsens. If cleanup/residual, comparison anchors stay unchanged. | Raw mass worksheet or supplementary data allocates discrepancy to non-O2 cleanup/loss, or any allocation needed for closure violates element/oxygen balance. | W5 |
| E5 | Deposit chemistry/location writeup may overstate what is known: apparatus masses are real, but oxygen fraction, source purity, and location mapping are not pinned enough for closure. | For: oxygen partition is unresolved and deposit O/species are not pinned (`campaign analysis 2026-06`, `campaign analysis 2026-06`). Robinot reports deposits on cold surfaces/holder/window/condenser/filter (`campaign analysis 2026-06`). Sticking/source review finds no exact coefficient pins for the exact surface/species pairs (`campaign analysis 2026-06`). | Deposit chemistry must predict how much oxygen is bound per recovered gram and must separate sample-derived condensate from holder/filter/reactor contamination. | Deposit oxygen fraction is too low to affect O2, too high for characterization, or location/source attribution collapses under microscopy/EDS/XRD review. | W4, W5, W6 |

STATUS: EXPERIMENT-SIDE - 5 hypotheses registered; 5 assigned; no parked negatives.

#### Comparison-Side Hypotheses
| ID | Hypothesis | Current evidence for / against | First-principles prediction required to survive | Kill condition | Attack assignment |
|---|---|---|---|---|---|
| C1 | Thermal-history equivalence is false: comparing a one-hour 1800 deg C sim hold to Robinot's staged power/temperature history is invalid. | For: Robinot staged power from melting through O2 pulses to full shutter operation (`campaign analysis 2026-06`). O2 probes say staged time-temperature materially reduces O2 but does not close the gap alone (`campaign analysis 2026-06`). | Integrating vapor flux over Robinot's staged T(t), molten area(t), and pressure/flow must predict lower O2 than a flat hold, with the sign and magnitude fixed before seeing the 35 mg target. | Even a faithful staged thermal history stays far above Robinot, or the thermal history must be chosen from the desired answer rather than from paper observables. | W3 |
| C2 | Analyzer species specificity: Robinot measured O2 in argon, while the simulator may be reporting total generated oxygen or oxygen-bearing vapor/deposit species. | For: setup uses an oxygen trace analyzer (`campaign analysis 2026-06`); paper modeling discusses O and O2 as separate uncertain species (`campaign analysis 2026-06`); divergence says paper metric is outlet-observable O2 (`campaign analysis 2026-06`). Against: P1 outlet metric alone was falsified; analyzer-observable O2 remained high (`campaign analysis 2026-06`). | O2, O, SiO, SiO2, MgO, and bound deposit oxygen must be reported as separate observables, with only sensor-visible O2 compared to Robinot's 35 mg. | Species-specific postprocessing still leaves O2 far above paper, or closure depends on comparing total oxygen atoms to an O2-only analyzer. | W1, W4 |
| C3 | Sample emplacement differences: pellet on water-cooled holder, slumping droplet, and powder/crucible analogs are not interchangeable geometry. | For: Robinot pellet was pressed 13 mm x 10 mm and placed on a water-cooled holder (`campaign analysis 2026-06`); melt formed a lower droplet and reactor position was readjusted (`campaign analysis 2026-06`); sample contact with cold surface limited maximum T (`campaign analysis 2026-06`). | Exposed area, heat sink, droplet geometry, cracks, and porosity must predict both HKL source area and temperature field; pellet and powder-bed data must not be swapped without a geometry transform. | Measured emplacement geometry cannot bound the required area/thermal correction, or a powder-bed analogy contradicts Robinot pellet observations. | W2, W3 |
| C4 | Cross-paper discriminator scope: Sesko and Sauerborn can falsify mechanisms qualitatively, but cannot certify quantitative Robinot closure without matching observables. | For: design limits Sesko to species-order, pressure/temperature, molecular-transport, wall-deposit, and approximate-thickness checks unless new sidecar data lands (`campaign analysis 2026-06`). Divergence says Sesko does not provide the same outlet-integrated O2-yield test but supports volatile sequence qualitatively (`campaign analysis 2026-06`). Sauerborn supports cold-surface deposition qualitatively (`campaign analysis 2026-06`). | A mechanism that closes Robinot must preserve Sesko species order/deposit trends and Sauerborn cold-surface behavior under their own pressure/T geometry, while not claiming quantitative O2/deposit validation they do not measure. | Robinot closure fails Sesko/Sauerborn qualitative trends under pinned transport, or a proposed validation relies on observables those papers do not contain. | W6 |

STATUS: COMPARISON-SIDE - 4 hypotheses registered; 4 assigned; no parked negatives.



Coverage check: 16/16 rows attacked by at least one wave; no orphans.

### Appendix B — Parameter provenance table (full)
<!-- SLOT:appendix-provenance source=derivation-audit-findings.md status=refined -->

Full 68-row derivation-chain audit. FITTED rows remain flagged as FITTED because they are calibration debt, not acceptable final provenance.

| # | Subsystem | Parameter / value | Source class | Where set | Robinot-path sensitivity |
|---:|---|---|---|---|---|
| 1 | Evaporation | HKL flux form: `J = alpha * (P_sat - P_ambient) * sqrt(M / (2*pi*R*T))` | first-principles | `engines/builtin/evaporation_flux.py:230-288` | High; source of O2/vapor gap. |
| 2 | Evaporation | `GAS_CONSTANT`, `pi`, seconds/hour conversion | first-principles | `engines/builtin/evaporation_flux.py:236-253`, `simulator/state.py:109-113` | Linear/unit only; not soft. |
| 3 | Evaporation | Molar masses from `MOLAR_MASS` / YAML payload | literature-pinned | `engines/builtin/evaporation_flux.py:216-218`, `tests/test_physics_ground_truth.py:94-122` | Low/medium; sqrt dependence. |
| 4 | Evaporation | Default melt surface area `0.2 m2` | ASSUMED | `data/setpoints.yaml:1117-1120`, consumed as `melt_surface_area_m2` | Very high; lab pellet area mismatch is a top Robinot gap. |
| 5 | Evaporation | Axial `stir_factor`; recipe window `4-8x`, clamp max `10.0` | ASSUMED | `data/setpoints.yaml:1059-1063`, `simulator/state.py:130-167`, `engines/builtin/evaporation_flux.py:180-192` | High; HKL rate linear. |
| 6 | Evaporation | `_DEFAULT_EVAPORATION_ALPHA = 1.0` for unmeasured fallback | ASSUMED | `engines/builtin/evaporation_flux.py:67`, `:239-268` | High only if `allow_unmeasured_alpha_fallback` is enabled. |
| 7 | Alpha | Na `alpha=1.0`, envelope `[0.9,1.0]` | literature-pinned | `data/vapor_pressures.yaml::metals.Na.evaporation_alpha`; pinned by `tests/test_evaporation_alpha_provenance.py` | Medium; Na is minor vapor mass. |
| 8 | Alpha | K `alpha=1.0`, envelope `[0.9,1.0]`, Na analogy | ASSUMED | `data/vapor_pressures.yaml::metals.K.evaporation_alpha` | Medium/low; explicit analogy debt. |
| 9 | Alpha | Mg `alpha=0.2`, envelope `[0.1,0.21]` | literature-pinned | `data/vapor_pressures.yaml::metals.Mg.evaporation_alpha` | Medium/high; Mg vapor is material in faithful run. |
| 10 | Alpha | Fe `alpha=0.02`, envelope `[0.011,0.02]` | literature-pinned | `data/vapor_pressures.yaml::metals.Fe.evaporation_alpha` | High; Fe vapor is material and alpha is small. |
| 11 | Alpha | SiO `alpha=0.04`, envelope `[0.003,0.048]` | literature-pinned | `data/vapor_pressures.yaml::oxide_vapors.SiO.evaporation_alpha` | Very high; SiO dominates non-O2 vapor. |
| 12 | Alpha | Ca `alpha=0.9`, proxy tag | ASSUMED | `data/vapor_pressures.yaml::metals.Ca.evaporation_alpha` | Medium/high; Ca vapor appears in faithful run. |
| 13 | Alpha | Al `alpha=0.3`, broad proxy | ASSUMED | `data/vapor_pressures.yaml::metals.Al.evaporation_alpha` | Medium; Al vapor present, broad envelope. |
| 14 | Alpha | Si `alpha=1.0`, pure elemental only | ASSUMED | `data/vapor_pressures.yaml::metals.Si.evaporation_alpha` | Low in current faithful run; inactive pure-Si branch. |
| 15 | Alpha | Ti `alpha=0.8`, proxy tag | ASSUMED | `data/vapor_pressures.yaml::metals.Ti.evaporation_alpha` | Low; Ti vapor tiny. |
| 16 | Alpha | Cr/Mn `fail_loud_missing_alpha` policy | ASSUMED | `data/vapor_pressures.yaml::metals.Cr/Mn.evaporation_alpha_policy` | Low unless fallback enabled; honest no-value state. |
| 17 | P_sat runtime | Na runtime Antoine `(8.477035,11265.231371,0)`, `pseudo_psat_backsolved_from_vaporock` | FITTED | `data/vapor_pressures.yaml::metals.Na.antoine`; convention in `engines/builtin/vapor_pressure.py:59-71` | Medium if builtin fallback used. |
| 18 | P_sat runtime | K runtime Antoine `(3.869571,4961,0)`, VapoRock-backsolved | FITTED | `data/vapor_pressures.yaml::metals.K.antoine` | Medium/low if fallback used. |
| 19 | P_sat runtime | Mg runtime Antoine `(10.628931,6788.644019,0)`, VapoRock-backsolved | FITTED | `data/vapor_pressures.yaml::metals.Mg.antoine` | Medium/high if fallback used. |
| 20 | P_sat runtime | Fe runtime Antoine `(12.404333,19156.973681,0)`, VapoRock-backsolved | FITTED | `data/vapor_pressures.yaml::metals.Fe.antoine` | High if fallback used. |
| 21 | P_sat runtime | SiO runtime Antoine `(22.117682,40638.351545,0)`, VapoRock-backsolved | FITTED | `data/vapor_pressures.yaml::oxide_vapors.SiO.antoine` | Very high if fallback used. |
| 22 | P_sat runtime | CrO2 runtime Antoine `(12.9245114,23732.9593,0)`, JANAF0 fallback fit | FITTED | `data/vapor_pressures.yaml::oxide_vapors.CrO2.antoine/reaction` | Low/medium. |
| 23 | P_sat runtime | Ca runtime Antoine `(11.238,9520,0)`, legacy rough dH_vap approximation | ASSUMED | `data/vapor_pressures.yaml::metals.Ca` | Medium/high; Ca vapor material and source says TODO replace. |
| 24 | P_sat runtime | Al runtime Antoine `(11.553,17340,0)`, legacy CRC-style regression/dH | ASSUMED | `data/vapor_pressures.yaml::metals.Al` | Medium. |
| 25 | P_sat runtime | Si runtime Antoine `(11.619,21590,0)`, inactive legacy pure-Si approximation | ASSUMED | `data/vapor_pressures.yaml::metals.Si` | Low current load. |
| 26 | P_sat runtime | Ti runtime Antoine `(11.65,23200,0)`, legacy table/regression | ASSUMED | `data/vapor_pressures.yaml::metals.Ti` | Low. |
| 27 | P_sat runtime | Cr runtime Antoine `(11.42,20730,0)`, legacy rough dH_vap approximation | ASSUMED | `data/vapor_pressures.yaml::metals.Cr` | Low/medium. |
| 28 | P_sat runtime | Mn runtime Antoine `(11.183,14740,0)`, legacy rough dH_vap approximation | ASSUMED | `data/vapor_pressures.yaml::metals.Mn` | Low/medium. |
| 29 | P_sat sidecar | Pure-component Antoine Na/K/Mg/Fe anchored to normal boiling point and dH_vap | literature-pinned | `data/vapor_pressures.yaml::* .pure_component_antoine`, tested in `tests/test_physics_ground_truth.py:42-85` | Diagnostic/ground-truth anchor, not the pseudo runtime fit. |
| 30 | P_sat sidecar | Pure-component Ca/Al/Si/Ti marked `LEGACY_DERIVATION_VALUE` despite boiling-point tests | ASSUMED | `data/vapor_pressures.yaml::* .pure_component_antoine` | Medium for Ca/Al; source strings explicitly say TODO replace. |
| 31 | Activity / Ellingham | `_ELLINGHAM_THERMO` dH/dS/n table, JANAF high-T refit | literature-pinned | `engines/builtin/vapor_pressure.py:73-92` | High if builtin fallback used; sets `a_M`. |
| 32 | Activity / Ellingham | Ellingham fit range `1100-1700 K` | literature-pinned | `engines/builtin/vapor_pressure.py:75`, `:274-285` | Medium; extrapolation warning, not correction. |
| 33 | Activity | Oxide activity proxy `a_oxide = comp_wt[parent_oxide] / 100` | ASSUMED | `engines/builtin/vapor_pressure.py:270-287`, `:343-351` | High; idealized wt-fraction activity, no activity coefficients. |
| 34 | Activity | Metal activity equation from Ellingham; clamp `a_M <= 1` | first-principles | `engines/builtin/vapor_pressure.py:289-320` | High; algebraic, but inherits table/activity assumptions. |
| 35 | pO2 | Vacuum floor / fallback `pO2_bar >= 1e-9` | ASSUMED | `engines/builtin/vapor_pressure.py:401-418` | Very high for SiO suppression and O2 gap. |
| 36 | pO2 / SiO | SiO suppression `sqrt(1e-9 / pO2_bar)` when no explicit exponent | first-principles | `engines/builtin/vapor_pressure.py:367-375`, `data/setpoints.yaml:1357-1363` | Very high; controls SiO vapor. |
| 37 | Oxide vapor | CrO2 exponents `oxide_activity=0.5`, `pO2=0.25` from stoichiometry | first-principles | `data/vapor_pressures.yaml::oxide_vapors.CrO2`, `engines/builtin/vapor_pressure.py:343-365` | Low/medium. |
| 38 | Overhead speciation | `gamma=1` oxide activity proxy: oxide mole fraction | ASSUMED | `engines/builtin/overhead_gas_equilibrium.py:300-339` | Medium; affects diagnostic partials/speciation. |
| 39 | Condensation routing | Condensation temps Fe/Mg/Na/K/Ca/Mn/Cr/Al/Ti, operator-tuned against P_sat curves | FITTED | `simulator/condensation.py:390-432`, `data/setpoints.yaml:820-832` | Medium/high; controls stage assignment. |
| 40 | Condensation routing | SiO `1050 C`, engineering midpoint of 900-1200 C zone | ASSUMED | `simulator/condensation.py:408-422`, `data/setpoints.yaml:903-911` | High for apparatus deposit mapping. |
| 41 | Condensation routing | CrO2 `1250 C`, JANAF/VapoRock-informed Cr stage setpoint | literature-pinned | `data/setpoints.yaml:834-840`, `simulator/condensation.py:423` | Low/medium. |
| 42 | Condensation routing | Stage temperature bands, e.g. SiO `900-1200 C`, alkali/Mg `350-700 C` | ASSUMED | `data/setpoints.yaml:842-930`, `data/materials.yaml:22-82` | High for per-surface deposit split. |
| 43 | Sticking | `STICKING_COEFF`: Fe .9, SiO .7, CrO2 .9, Mg .8, Na/K .95, Ca/Mn .85, Cr .9 | ASSUMED | `simulator/condensation.py:434-445`, migrated to `data/materials.yaml:1-20` | Very high for named wall/deposit capture. |
| 44 | Sticking | Unknown species fallback `alpha_s=0.8` | ASSUMED | `simulator/condensation.py:1567`, `:1600`, `:2246` | Medium if unlisted species appears. |
| 45 | Wall materials | New `data/wall_materials.yaml` stickiness rows reference old defaults, not new numeric pins | ASSUMED | `data/wall_materials.yaml:1-45`; rows point to `data/materials.yaml::default_alpha_s_by_species` | High; literature table does not yet replace runtime coefficients. |
| 46 | Surface deposition | HKL impingement/deposition equation with Boltzmann constant and molecule mass | first-principles | `simulator/condensation.py:1775-1810` | Medium/high; wall sink equation. |
| 47 | Surface deposition | Series resistance form `1/k_total = 1/(alpha_s*k_HKL) + (1-f)/k_MT` | first-principles | `simulator/condensation.py:1827-2001` | High in viscous boundary-layer regime. |
| 48 | Mass transfer | Laminar Sherwood `Sh=3.66` | literature-pinned | `simulator/condensation.py:131-149`, `:201-299` | Medium/high for viscous capture. |
| 49 | Mass transfer | Stirring enhancement `Sh = 3.66 * sqrt(radial_stir_factor)` | ASSUMED | `simulator/condensation.py:201-299`, `:1951-1954` | Medium; plausible but not geometry-pinned. |
| 50 | Diffusion | Default binary diffusion `1.0e-2 m2/s` historical anchor | ASSUMED | `simulator/condensation.py:143-154`, fallback at `:1963-1966` | Medium if LJ calculation missing/invalid. |
| 51 | Diffusion | LJ params for N2/Ar/CO2/O2/Na/K/Ca from BSL/Svehla-style sources | literature-pinned | `simulator/condensation.py:157-180` | Medium. |
| 52 | Diffusion | LJ params for Fe/Mg/Mn/Cr/Al/Ti/SiO estimated from rules of thumb | ASSUMED | `simulator/condensation.py:181-188` | Medium/high for metal/SiO wall capture. |
| 53 | Knudsen | Mean-free-path formula using Boltzmann constant | first-principles | `simulator/condensation.py:2004-2021` | Medium; regime classification. |
| 54 | Knudsen | N2 collision diameter `3.7e-10 m` | literature-pinned | `simulator/condensation.py:106`, `:2004-2008` | Medium. |
| 55 | Knudsen | Regime thresholds: viscous `<0.01`, free molecular `>=10` | literature-pinned | `simulator/condensation.py:107-110`, `:2043-2050` | High if near transition; standard convention. |
| 56 | Knudsen | Smooth regime factor `Kn/(Kn+0.01)` | ASSUMED | `simulator/condensation.py:2034-2040`, `:2206-2209` | Medium/high; arbitrary transition blend. |
| 57 | Transport | Pipe geometry `d=0.12 m`, `L=1.0 m` | ASSUMED | `simulator/overhead.py:151-155`, `data/setpoints.yaml:1121-1124` | Very high for lab reproduction and deposits. |
| 58 | Transport | Default pipe/wall temperature `1500 C` | ASSUMED | `simulator/overhead.py:51`, `:147-155`; `simulator/condensation.py:104` | High for wall P_sat/deposition. |
| 59 | Transport | Poiseuille conductance formula | first-principles | `simulator/overhead.py:19-28`, `:742-811` | Medium; pressure/backpressure. |
| 60 | Transport | Gas viscosity `eta = 1.8e-5*(T/300)^0.7`, N2-like | ASSUMED | `simulator/overhead.py:799-801` | Medium; conductance scales inverse. |
| 61 | Transport | Fallback mean molar mass `0.040 kg/mol` | ASSUMED | `simulator/overhead.py:53-63`, `:806-811` | Low in live runs using species mixture; soft fallback. |
| 62 | Gas boundary | Ideal-gas partial pressures `p = nRT/V` | first-principles | `simulator/overhead.py:689-699`, `engines/builtin/overhead_gas_equilibrium.py:137-150` | Medium; headspace/backpressure. |
| 63 | Gas boundary | pO2 method setpoints: C2A `<1e-8 bar`, C3 `0.5-1.5 mbar`, C4 `0.08-0.35 mbar`, C5 `0.01-0.1 bar`; precision `0.1-0.3 mbar` | ASSUMED | `data/setpoints.yaml:1068-1101` | High; pO2 controls SiO and O2 survival. |
| 64 | Gas boundary | Turbine shaft power `0.02 kWh/kg O2` simplified; capacity venting algebra | ASSUMED | `simulator/overhead.py:510-545` | Low for Robinot mass comparison. |
| 65 | Gas boundary | Partial-pressure total tolerance `max(1e-12 mbar, 1e-12*scale)` | first-principles | `simulator/state.py:571-585` | Low; validation only. |
| 66 | Lab overlay | Required closure factor `alpha * area = 0.03969` | FITTED | `campaign analysis 2026-06`, `:112-126` | Very high; paper-derived diagnostic, not allowed as hidden runtime scalar. |
| 67 | Lab overlay | Forsterite proxy `alpha_c = 0.038 +/- 0.005` | literature-pinned | `campaign analysis 2026-06`, `:112-116`; expanded in `alpha-principles.md` | Very high if lab overlay uses it. |
| 68 | Lab overlay | Lab exposed-area seam absent; current `lab_geometry` wall area does not reach HKL source area | ASSUMED | `campaign analysis 2026-06`, `:287-289` | Very high; current faithful path likely overuses pool-scale area. |

### Appendix C — Faithful-run artifacts
<!-- Run digests, preset SHAs, code version per comparison row. -->

# Output Interpretation

This doc covers how to *read* a run output document. The full JSON shape is pinned in [`docs/runner-output-schema.md`](runner-output-schema.md); the prose here explains what the numbers mean and where they come from.

## Vapor pressure provenance

`vapor_pressure_source_report` tells you which source label produced the vapor pressure for each species in the final equilibrium.

Labels are **colon-separated**: `<authority>:<derivation-term>[:<qualifier>…]`. Only the first segment names the authority, so match on it deliberately rather than comparing whole strings.

Authority segment (the head token):

- `builtin_authoritative` — builtin Antoine + Ellingham `VAPOR_PRESSURE` provider; this is the current authoritative surface consumed by evaporation.
- `builtin_extrapolation_limited` — same builtin provider, but the underlying fit was evaluated outside its validated range.
- `builtin_authority_limited` — same builtin provider on a reconstructed (not directly fitted) Ellingham term.
- `thermoengine` — live MELTS `μ → a` conversion via ThermoEngine transport.
- `alphamelts_python_api` / `alphamelts_text` — PetThermoTools fallback transports for the AlphaMELTS path.
- `builtin_fallback` — explicit fallback label, not the default path.
- `vaporock` — legacy/sentinel label on older or fallback artifacts. Current VapoRock output is diagnostic-only and, when present, lives under `vaporock_full_speciation_Pa`.
- `kernel_diagnostic` — kernel-recorded sentinel; the species value did not come from any thermochemical authority and should not be treated as a measurement.

Derivation term (which equilibrium produced the number): `standard_reaction_term`, `liquid_oxide_standard_reaction`, `gas_standard_fugacity`, `pure_component_derived_from_evaluation`.

**Qualifiers are load-bearing, not cosmetic.** A label such as
`builtin_authoritative:standard_reaction_term:extrapolated_beyond_valid_range_K` has an
authoritative head token but is still refused for certification: the tokens
`extrapolated_beyond_valid_range_K` and `pure_component_extrapolated` mark a number computed
outside the fit's validated range, and `simulator/grind_preflight.py` rejects those regardless
of head token so process-envelope extrapolation cannot silently certify ledger yields.
`out_of_gamma_domain` likewise marks an activity-model evaluation outside its domain. Treat a
qualified label as compute-and-mark output: usable as a diagnostic, not as evidence.

The `summary` map gives per-source counts and species-count percentages for the latest vapor surface used by the evaporation path, keyed by the **full** label — so one species family can appear under several keys that differ only in qualifier.

## Stage purity report

`stage_purity_report` splits each condenser stage's collected mass three ways — `designated_species_kg`, `coproduct_species_kg`, `impurity_species_kg` — sourced from `simulator.condensation.stage_purity_report()` against the canonical registry in `simulator/condensation_routing.py`. The verdict is set from `purity_fraction`:

- **PURE** — designated species strictly **greater than** 95 % of stage total kg. Expected for Stage 3 SiO under default Path A. Exactly 95.0 % reads as MIXED, not PURE.
- **MIXED** — 80 % ≤ purity ≤ 95 %. Mild routing drift; usually a sign that a cold spot upstream of the designated condenser caught some flux. Cross-reference `wall_deposit_kg` and the segment cold-spot ledger.
- **CONTAMINATED** — <80 %. Real failure mode. The recipe's selectivity claim does not hold for this stage; check the F1 routing registry and the per-segment wall T.

The registry's accepted species per stage are: Stage 1 Fe condenser `Fe`; Stage 2 Cr oxide harvester `Cr`/`Cr2O3`/`CrO2`/`Mn`; Stage 3 SiO zone `Si`/`SiO`/`SiO2`; Stage 4 alkali/Mg cyclone `Na`/`K`/`Mg`. Stages 0, 5, 6 and 7 (hot duct, vortex dust filter, turbine-compressor, turbine outlet monitor) designate nothing, so anything landing there is by definition off-route.

## Shuttle refusal history

`shuttle_refusal_history` is an append-only list of C3 shuttle dispatches the S1b T-acceptance gate refused. Empty list means every step was thermodynamically accepted at its dispatch T. Each entry carries the engine's structured diagnostic: `reaction_family` (`C3_K` or `C3_NA`), `reagent`, `hour`, `campaign_hour`, `temperature_C`, plus `diagnostic.k_reduction_margin_kJ_per_mol_O2` and (for Na) `diagnostic.thermo_deltaG_kJ_per_mol_O2`.

Per-step refusals leave the run `status` at `ok` or `partial`. Only whole-run halts (e.g. `KnudsenRegimeRefusal` for a viscous-flow violation) escalate to `status="refused"`.

Under JANAF-4th multiphase Ellingham (2026-07-09 re-ground):

- K → FeO has non-positive margin at any practical melt T (crossover ~836 °C). The legacy K-shuttle path is therefore refused at every dispatch; this is the surviving design, not a failure.
- Na → FeO has positive margin only below 1181.5 °C. The default C3_NA recipe injects at 1150 °C for a positive (~31 °C) margin window.

## Knudsen regime diagnostic

`run_metadata.knudsen_regime_diagnostic` reports the transport-regime check per pipe segment:

- `status: "ok"`, `regime: "viscous"` — Kn < 0.01, sweep gas drives directional transport. Default operating condition for the 5–15 mbar pN₂ band.
- `status: "warning"`, `regime: "transitional"` — Kn between 0.01 and 10; F3 still computes attenuated HKL flux (`regime_factor = Kn / (Kn + 0.01)`) but you are losing transport-directed condensation and the simulator's stage-yield numbers should be read as a lower bound (see [`docs/model-limitations.md`](model-limitations.md) on viscous-regime mass transfer).
- `status: "refused"`, `regime: "free_molecular"` — Kn ≥ 10 on at least one segment; `KnudsenRegimeRefusal` escalates the run to `status="refused"`. Recipe is unrunnable at this pN₂; increase sweep gas pressure.

## Evaporation alpha

Hertz-Knudsen-Langmuir fluxes are scaled by per-species `evaporation_alpha` metadata in `data/vapor_pressures.yaml`. Each numeric alpha block carries a source citation, temperature context, uncertainty envelope, and confidence tier.

- **Tier 1** (Na, K, Fe, Mg, SiO, Cr) carries measured α with citation. Cr is α = 0.9 ± 0.1 over 1318–1563 K, selected by Pound (1972) from McCabe, Hudson & Paxton's direct solid-Cr Langmuir/Knudsen comparison. This is clean, pure-solid Cr evaporating as Cr(g), not Cr dissolved in silicate melt; melt activity belongs in `P_sat`, and oxygen-contaminated surfaces can lower α.
- **Tier 2** (Ca, Ti, Al, Mn) contains proxy or conditional-proxy values. Ca and Ti use Zhang 2014 CaTiO₃ melt coefficients; Al uses a broad conflicting-proxy envelope. Mn uses the owner-ratified monoatomic-metal class proxy α = 1.0 with envelope `[0.5,1.0]` over `1519–2334.526 K`. This is not a Mn-specific or temperature-calibrated measurement: two independent literature sweeps found no direct Mn HKL coefficient, and Safarian & Engh 2013 full text contains zero Mn value. The proxy is medium-high confidence for clean liquid Mn; clean solid α-Mn remains unmeasured. Elemental Si is valid only for the inactive pure-element Si branch; the SiO silicate-vapor path keeps its separate SiO alpha.
- **Tier 3** (CrO₂ only) intentionally has no numeric α. When fallback is disabled, the engine records `missing_alpha` plus a typed `species_refusals` entry and retains Cr as condensed Cr₂O₃; other species and the run continue. Released setpoints default `chemistry_kernel.allow_unmeasured_alpha_fallback` to `false`; explicit fallback may use the α = 1.0 upper bound for CrO₂ and records `unmeasured_alpha_fallback_species`. Cr and Mn use their grounded rows regardless of this flag.

The evaporation diagnostic includes `flux_uncertainty_pct`, a per-species map derived from the alpha envelope. It is alpha-only uncertainty, not a total model uncertainty: vapor-pressure fits, melt activities, temperature dependence, and composition dependence remain separate limitations.

## Mass balance

`per_hour_summary[i].mass_balance_pct` is `|mass_in − mass_out| / mass_in × 100`, computed against the atom ledger. The invariant the goldens pin is below `5×10⁻¹² %` at every tick under the full default-on stack (`tests/test_mass_balance.py`). The 0.5.0 closure under default-on `freeze_gate` + V1c-JANAF + V1e-impl + S1b + F1–F6 + E3 is `2.19×10⁻¹⁴ %`. Drifts above `5×10⁻¹² %` should be treated as regressions.

## O₂ metric semantics

`per_hour_summary[i].O2_yield_kg_cumulative` is a legacy serialized key. Read it as source-side O₂ potential: emitted O₂ counted by the simulator's O₂ bins before downstream sinks, capture efficiency, analyzer visibility, or recovery losses. `O2_source_side_potential_kg_cumulative` is the honest alias with the same numeric value, and `O2_metric_label` carries the display label.

Do not use either field as recovered/captured O₂ yield. The downstream sink/recovery model is Phase B. Current lab validation against Robinot et al. shows the source-side/free-O₂ calculation remains about `18.75×` above analyzer-visible O₂ after the honest area/alpha correction; see [`docs/lab-validation-whitepaper.md`](lab-validation-whitepaper.md) §4.1 and §4.5.

## Per-tick HourSnapshot diagnostics (0.5.4+)

The 0.5.4 release added two earlier-warning diagnostic surfaces to every `HourSnapshot`:

- **`metal_projection_drift_kg`** (W8 / M2 closure) — per-species `ledger_kg - projection_kg` across two like-for-like domains that share `train.stages[*].collected_kg`: the condensation-train/chromium ledger versus the UI remainder after source-tracked metal is removed, and stage-routed metal-phase ledger products versus their source-tracked UI share. Metal-phase products with no stage destination, including C6 Si, are intentionally outside the audit. A residual above `1×10⁻⁹ kg` in either domain is reported; if both domains drift for one species, the larger absolute residual is retained so opposite signs cannot cancel. Empty dict means every audited stage-collection domain is aligned. Negative values surface projection-only stale state. Under the runner's strict result contract, any nonempty drift fails the run with `reason="metal_projection_drift"`; interactive consumers preserve it as a diagnostic only. The historical field name remains for compatibility. The global `mass_balance_error_pct` ≤5×10⁻¹² % gate remains hard in every mode.

- **`knudsen_regime_summary`** (0.5.4.1 E3) — per-tick Knudsen-regime visibility from the latest condensation pass. Carries `status` (`ok` / `warning` / `refused`), `knudsen_number` (float), `knudsen_regime` (`viscous` / `transition` / `free_molecular`), `regime_factor` (F3 viscous-flow attenuation), and `warnings` (operator-facing strings when Kn approaches the boundary). Complements the F3 hard refusal at `Kn ≥ 10` with earlier-warning visibility so operators can lower ramp rates or adjust pN₂ sweep proactively. Empty dict on ticks that didn't trigger a condensation route.

Stage 0 foulant reports should be read as diagnostics. They group contaminant disposition into trapped gases, refractory carbon, and other mineral contaminants, with warning flags for contaminant impact; they do not claim additional cleaned-melt yield or alter the mol-native ledger handed to later melt chemistry.

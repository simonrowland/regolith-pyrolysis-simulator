# S5b — unit / scale conversion slips (`engines/` only)

**Repo tip (code base audited):** `a49cef012` (`origin/review/janaf-batch-2026-09-22`). READ-ONLY; no product edits.  
**Worktree:** `/workspace/repos/wt/slot-05` absent → audited via `git show` / checkout of tip `engines/` on `/workspace/repos/regolith-pyrolysis-simulator`.  
**Scope:** `engines/` only (41 `*.py` files: `alphamelts/`, `builtin/`, `magemin/`, `vaporock/`, `domain_reason.py`, `engine_commissioning.py`).  
**Predicate:** conversion between Pa/bar/kbar/GPa/atm/mbar, K/C, J/kJ, wt%/mol%/fraction, log10/ln, or formula-unit basis where the factor is **wrong, duplicated, inverted**, or an **already-converted** value is converted again. Prefer local arithmetic over shared converters.  
**Seed (out of scope here):** P4a-1 / R5 — MAGEMin `pure_phase_properties` bar→kbar `*1e-4` lives in `simulator/melt_backend/magemin.py` on `origin/review/janaf-p4a1-pure-phase`, **not** under `engines/`. Whole-repo S5 owns that site (`ferry/sweeps/S5-unit-scale-conversion.md`); this split does **not** overwrite it.

| site (file:line) | predicate match | trigger input | live? | severity |
|---|---|---|---|---|
| *(none)* | — | — | — | — |

## Counts

- Sites: 0  
- Live: 0  
- P0: 0 · P1: 0 · P2: 0 · P3: 0  

## No-hit areas (in scope, audited)

### Pressure (Pa / bar / mbar / MPa)

- `engines/builtin/evaporation_flux.py:983-985` — default `overhead_pressure_pa = request.pressure_bar * 1e5` (bar→Pa once).  
- `engines/builtin/ca_aluminothermic_step.py:363-364` — default `p_total_mbar = request.pressure_bar * 1000` (bar→mbar); `:447-448` mbar→Pa `*100` (1 mbar = 100 Pa). Not double-applied on one path.  
- `engines/builtin/foulant_disposition.py:19,250` — local `PA_PER_BAR = 100_000.0`; `p_overhead_bar * PA_PER_BAR` → Pa once.  
- `engines/builtin/overhead_gas_equilibrium.py:149` — ideal-gas `P_bar = n R T / (V * 1e5)` with `GAS_CONSTANT` J/(mol·K); matches tests.  
- `engines/alphamelts/thermoengine.py:1224-1225` — bar→MPa `/10` then echo `*10` (1 bar = 0.1 MPa); no kbar/GPa local arithmetic in `engines/magemin/` (passes `pressure_bar` through to adapter).  
- `engines/builtin/cco_redox_buffer.py` — CCO/QFM formulas are `log10(fO2/bar)` with `P_bar` in the Jakobsson term; diagnostic twin in `melt_effect_adjustment.py:836-840` evaluates at stamped `pressure_bar=1.0` (pressure term zero) — consistent, not inverted.

### Temperature (K / °C)

- All live C↔K sites use `+ 273.15` or `CELSIUS_TO_KELVIN_OFFSET` (273.15): `vaporock/provider.py:160`, `vapor_pressure.py:1369`, `fe_redox_respeciation.py:111`, `foulant_disposition.py`, `evaporation_flux.py:817`, `electrolysis_step.py`, `ca_aluminothermic_step.py`, `metallothermic_step.py`, `magemin/provider.py` / `parity.py`, `alphamelts/parser.py` / `result.py`, `thermoengine.py`. **No** `+ 273.0` and no double add/subtract on one value.  
- `engines/builtin/melt_effect_adjustment.py:984-987` `_liquidus_perturbation_pct` divides by `T_in_C` under explicit metric `delta_T_frac_of_T_in_C` / basis `celsius_relative_percent_with_floor` — declared Celsius-relative notice scale, not a silent K/C conversion slip.

### Energy (J / kJ) and log10 / ln

- `foulant_disposition.py:334` — dG slope kJ→J `*1000` then width with `R * T * math.log` (ln); onset pressure shift also `math.log` — matched pair.  
- `ca_aluminothermic_step.py:550-551` — `R/1000` → kJ with `math.log(p/p_ref)` (ln) for vapor ΔG term.  
- `electrolysis_step.py` — `E * n * F / 1000` → kJ; Nernst uses `(RT/nF) * ln(Q)` via shared `mre_parent_oxide_log_quotient` (ln).  
- fO2 paths use `math.log10` / `10**` consistently (`vapor_pressure.physical_melt_dissociation_pO2_bar`, `_common.resolve_transport_pO2_bar`, `vaporock` / electrolysis anode log echo). No ln/log10 swap found.

### Composition (wt% / mol% / formula basis)

- kg↔wt% projections use `*100` / `/100` once (`_common.composition_wt_pct_from_account_view`, magemin/alphamelts phase modes from masses).  
- `overhead_gas_equilibrium._oxide_activity_proxy_gamma_1` — wt% / M → mole fractions (not wt-as-mole).  
- `vapor_pressure.py:2020` Fe degraded `comp_wt/100` is an explicit wt-fraction activity proxy (typed warning path), not mol%.  
- Metallothermic / electrolysis mol↔kg via `M_gmol/1000` round-trips; `electrons_per_formula` / O2-per-formula comments match Faraday stoichiometry.  
- AlphaMELTS compound-endmember→parent-oxide path in `engines/alphamelts/domain.py` **refuses** fabricated formula-basis conversion (no silent Mg2SiO4÷2-style divisor under `engines/`).

### ThermoEngine volume / density (local arithmetic)

- `thermoengine.py:1314-1320` — Volume/DvDp/DvDt `*1e-5` (J/bar → m³); Density `*1000` (g/cm³ → kg/m³). Tests pin `Volume: 20 → volume_m3 2e-4`, `Density: 3.5 → 3500`. Applied once per property. Pure-phase μ: J/g × g/mol → J/mol only when TE returns specific G for a single-component phase.

### Packages with no conversion arithmetic of this class

- `engines/builtin/{condensation_route,oxygen_bubbler,oxygen_reservoir_exchange,native_fe_*,evaporation_transition,backend_equilibrium}.py` — ledger / mol passthrough.  
- `engines/{domain_reason,engine_commissioning}.py` — commissioning bands / reasons (wt% labels only).  
- `engines/vaporock/provider.py` — passes through adapter `*_Pa` / bar controls; unit scale of VapoRock library output is owned by `simulator/melt_backend/vaporock.py` (out of this split).

SWEEP: S5b | sites=0 | live=0 | P0=0 P1=0 P2=0 P3=0 | no-hit areas: bar↔Pa/mbar/MPa locals (evaporation_flux, ca_aluminothermic, foulant PA_PER_BAR, overhead_gas 1e5, thermoengine /10); C↔K 273.15/CELSIUS_TO_KELVIN; J/kJ+ln (foulant width, C7 vapor term, electrolysis Faraday/Nernst); log10 fO2 rails; wt%/mol%/formula (activity proxy, Fe wt/100 typed, metallothermic g↔kg, MELTS endmember refuse); TE Volume*1e-5 Density*1000 once; vaporock/provider passthrough; condensation/oxygen/native_fe/evap_transition/backend_equilibrium/domain_reason/commissioning

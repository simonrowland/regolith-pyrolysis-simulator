# S6 — absence becomes a number

**Repo tip (write branch):** `4404a70b3` (`empirical/reviews-2026-09-22`).  
**Code base audited:** `origin/review/janaf-batch-2026-09-22` (`a49cef012`). READ-ONLY; no product edits.  
**Scope:** whole repo (`*.py` + doctrine docs that encode the anti-pattern).  
**Predicate:** a missing / unknown / refused input silently becomes `0`, `0.0`, NaN, `{}`, or another default that downstream code treats as a **measured** value (`.get(k, 0.0)`, `or 0`, `float(x or 0)`, `fillna(0)`, sum over missing keys, …).  
**Project doctrine:** absence is NEVER a measured zero (`simulator/battery/migrate.py` header; `SCHEMA.md`; `tests/test_failclosed_silent_zero_b135.py`).  
**Seed:** empty / SiO2-key-absent melt read as `0` wt% SiO2 via `comp.get('SiO2', 0.0)` (`tests/test_vaporock_backend.py` documents the readout; live call site `simulator/melt_backend/vaporock.py:1832`).

**Corpus scale (not site count):** ~1700 `*.py` lines matching `.get(..., 0|0.0)` and ~558 matching `or 0|0.0` on the base tip. Vast majority are sparse-dict **accumulators** (`d[k] = d.get(k, 0.0) + x`) or counter tallies — not scored as sites below. Sites are curated by **consequence** (physics / score / ledger / display).

**Systemic enabler:** melt and feedstock maps are sparse (only positive mass keys). Reading `comp.get(oxide, 0.0)` therefore equates **key absence** with **measured zero wt%/kg** everywhere that pattern is used as an input to physics, gating, scoring, or equipment sizing.

| site (file:line) | predicate match | constructed trigger | live? | severity |
|---|---|---|---|---|
| `simulator/melt_backend/alphamelts.py:2723` (`_to_petthermotools_liq_comp`) | Missing oxide keys → `SiO2_Liq` / `TiO2_Liq` / … = `0.0` (also `FeOt` from FeO/Fe2O3 gets; `fe3fet = self._fe3fet_ratio or 0.0`). Sent into PetThermoTools liquid composition. | Equilibrate with sparse `comp_wt` that omits `SiO2` (or Fe oxides) but still has other MELTS oxides so the empty-melt refusal does not fire. | live | P0 |
| `engines/builtin/vapor_pressure.py:2020` | Fe degraded path: `a_oxide = comp_wt.get(parent_oxide, 0.0) / 100.0` — absent FeO becomes activity 0 (typed warning exists, but numeric absence still becomes a measured activity input). | Public vapor-pressure call with intrinsic fO2 absent and FeO key missing from `comp_wt`. | live | P0 |
| `engines/alphamelts/domain.py:340-355` | `sio2_pct = canonical_wt.get('SiO2', 0.0)` then `missing_silica = sio2_pct <= 0.0` → `SILICATE_WINDOW` / silicate-network failure. Absence and true zero share one branch. | Domain-gate a melt whose oxide map simply omits SiO2 (vs explicit `SiO2: 0`). | live | P0 |
| `simulator/melt_backend/alphamelts.py:2232-2238` | Adapter mirror of SiO2=0 basis gate: `canonical_wt.get('SiO2', 0.0) <= 0.0` → ordinary SiO2=0 refusal string. | Same as above on AlphaMELTS adapter path. | live | P0 |
| `engines/magemin/domain.py:172-180` | `sio2 = normalized.get('SiO2', 0.0)` then out-of `[30,80]` → `SILICATE_WINDOW`. Missing key → 0 → fail band. | MAGEMin domain validate with SiO2 key absent. | live | P0 |
| `simulator/chemistry/structural_activity.py:642-650` (+ docstring `:636-638`) | `estimate_liquidus_flag`: documented “Missing oxide keys contribute 0 mole fraction”; builds liquidus K from SiO2/Al2O3/alkali/basic gets. | Call liquidus flag with formula-unit map missing SiO2 (or modifiers). | live | P0 |
| `simulator/electrolysis.py:645` (+ `engines/builtin/electrolysis_step.py:419`) | `feo_fraction = max(0.0, comp.get('FeO', 0.0)) / 100.0` (kg path uses `composition_kg.get("FeO", 0.0)`). Drives MRE reduction allocation. | Electrolyse a melt whose wt%/kg map omits FeO (depleted key dropped) while other oxides remain. | live | P0 |
| `engines/builtin/metallothermic_step.py:1152-1156` | `MgO_pct = composition_wt_pct.get("MgO", 0.0)` → `rate_factor = 0.20 * exp(-0.05 * MgO_pct)` clamped. **Missing MgO → 0 → near-max rate (0.20).** | C6 Mg-thermite hour with MgO key absent from composition_wt_pct. | live | P0 |
| `engines/builtin/metallothermic_step.py:441` / `:615` | `K2O_current_pct` / `Na2O_current_pct` via `.get(..., 0.0)` for solubility headroom / shuttle skip. Missing alkali → treated as 0 wt% present. | C3 K/Na shuttle with alkali key omitted. | live | P0 |
| `simulator/core.py:6633-6636` | `_compute_intrinsic_melt_fO2`: FeO/Fe2O3/Na2O/K2O via `.get(..., 0.0)` into IW-heuristic fO2. | `load_batch` / intrinsic path when `_melt_oxide_wt_pct()` omits Fe/alkali keys. | live | P1 |
| `simulator/melt_backend/vaporock.py:1832` (**seed**) | `assess_engine_commissioning(..., sio2_wt_pct=comp_wt.get('SiO2', 0.0))`. Empty melt is refused earlier (`:1804-1827`); **non-empty SiO2-key-absent** melt still reads 0 wt% → out-of-band extrapolated notice while engine runs. | Projected `comp_wt` with oxides but no `SiO2` key (post empty-check). | live | P1 |
| `simulator/equipment.py:555-556` (`size_volatiles_train`) | **Worse than zero:** `comp.get('Na2O', mass_kg * 0.4 / 100.0)` and `K2O` default `mass_kg * 0.1 / 100.0` after `normalized_feedstock_component_masses_kg` (which only returns present species). Missing alkali → **fabricated mare-like mass**, sizes C0 train. | Size equipment with feedstock whose `composition_wt_pct` omits Na2O/K2O (or empty composition → only defaults). | live | P1 |
| `simulator/campaigns.py:2790` | C6 endpoint: `refractory_pct = sum(comp.get(str(name), 0.0) for name in species)` vs threshold — missing refractory species count as 0 wt% → early endpoint. | Hold C6 with `composition_wt_pct()` sparse vs configured `composition_endpoint.species`. | live | P1 |
| `simulator/optimize/objective.py:2376` | Operator instruction: `composition.get("O2", 0.0)` → `pO2_mbar` when overhead composition lacks O2. | Tap snapshot whose `overhead.composition` omits O2. | live | P1 |
| `simulator/backends.py:644-652` (`_oxide_wt_pct`) + `:608-617` | Missing / non-numeric / non-finite oxides → `0.0`; drives `is_spinel_rich_stage0_subprocess_feedstock` route class. | Feedstock composition missing TiO2/Al2O3/MgO keys (or garbled strings). | live | P1 |
| `simulator/ceramic_classifier.py:376-451` | Point/window/constraint matchers use `composition.get(oxide, 0.0)` and `_oxide_sum(... get 0)` — absent oxide is measured 0 for ceramic identity. | Classify a partial major-oxide vector. | live | P1 |
| `simulator/extraction.py:1496` | `float(feo_voltage or 0.75)` — refused/None FeO ladder voltage becomes 0.75 V drive. | MRE_BASELINE with empty voltage sequence and ladder returning falsy. | live | P1 |
| `simulator/runner/__init__.py:3196` / `:3878`; `scripts/sso_r_validation_map.py:671` | Stage SiO2 capture / yield: `collected_kg.get("SiO2", 0.0)` (and missing stage → 0.0). Ledger/report treats never-collected as measured 0 kg. | Report stage yields when stage or SiO2 key absent. | live | P2 |
| `simulator/core.py:10823-10829` (`_melt_from_raw_inventory`) + `:10849` (`_melt_from_anhydrous_silicate`) | Load-batch builders fill every `OXIDE_SPECIES` via `.get(oxide, 0.0)` — omitted feedstock oxides become 0 kg inventory entries. | `load_batch` with sparse oxide declaration. | live | P2 |
| `simulator/core.py:10811-10818` (`_melt_from_composition`) | Same OXIDE_SPECIES fill via `comp.get(oxide, 0.0)`. **No product callers** on this tip (dead helper); still encodes the anti-pattern. | Future caller / test revival of `_melt_from_composition`. | latent | P2 |
| `simulator/core.py:6818-6826` | Kress91 wt reconstruction: `x.get('SiO2', 0.0) * MW + …` — missing mole-fraction keys contribute 0 to weighted_total / Fe wt%. | Redox split with sparse `x` mole fractions. | live | P2 |
| `simulator/core.py:10803` | Na2CO3 foulant SiO2 gate: `melt.get('SiO2', 0.0)` → missing silica → gate 0 → no decomp extent. | Stage-0 foulant path with SiO2 absent from melt inventory. | live | P2 |
| `simulator/diagnostic_helpers/alphamelts_volatility.py:318` / `:441` | Diagnostic `wt_activity = … comp_wt.get(parent_oxide, 0.0) … / 100.0`. | Volatility diagnostic with parent oxide key missing. | live | P2 |
| `scripts/vaporock_pseudo_antoine_refit.py:183` / `:192` | Fit fallback activity from `composition_wt_pct.get(SiO2|parent, 0.0)/100`; early `None` only after activity≤0 — still collapses absence and zero. | Refit row with oxide omitted from composition. | live | P2 |
| `engines/alphamelts/thermoengine.py:1972-1973` | Liquid FeO/Fe2O3 `get(..., 0.0) or 0.0` into redox / liquid diagnostics. | ThermoEngine liquid_comp sparse in Fe. | live | P2 |
| `simulator/optimize/objective.py:988-990` | Profile `raw.get("extraction", 0.0)` / `composition` weights — missing branch weight becomes measured 0.0 (then sum-to-one check may refuse). | Score profile omitting one weight key. | latent | P3 |
| `docs/runner-output-schema.md:398` | Schema text: consumers should “treat missing keys as 0.0” for dropped zero mol entries — **encodes** absence→number for ledger consumers. | Any consumer following the schema literally. | live | P3 |

## Counts

- Sites: 26  
- Live: 24  
- P0: 9 · P1: 7 · P2: 8 · P3: 2  

## No-hit areas (audited; doctrine already fail-closed or N/A)

- `simulator/battery/migrate.py` — printed null / missing admission → typed “absence is not a measured zero” refusals (not silent 0).  
- `simulator/battery/validate.py` — `Species.charge` required; absence refused.  
- `simulator/battery/score.py` — absence/unavailable is refusal (see `tests/battery/test_score.py`); `.get(..., 0)` hits are counter tallies only.  
- `simulator/accounting/completeness.py` — core mol fields Optional; unknown stays `None`.  
- `simulator/optimize/physics.py` — missing product mol → completeness `None` / fail-closed (not 0.0 product).  
- `simulator/wall_deposition.py:_wall_geometry_conductance_weight` — missing/invalid area raises `AccountingError` (“not proof of zero deposition weight”).  
- Empty-melt early refusals: `simulator/melt_backend/vaporock.py:1804-1827`, `magemin.py` empty-composition warning path, `imcc_sf04/backend.py` empty melt — refuse **before** SiO2 commissioning readout (seed residual is non-empty + missing-SiO2-key).  
- `tests/test_failclosed_silent_zero_b135.py` — L1–L3 / A1+ regressions locking several former silent zeros.  
- `fillna(0)` / `nan_to_num` — **0 hits** in `*.py` on this base.  
- Sparse-dict **output accumulators** (`result[k] = result.get(k, 0.0) + x`) across `engines/builtin/*`, `simulator/optimize/objective.py` bookkeeping — not counted (building a sum, not claiming a measured input).

SWEEP: S6 | sites=26 | live=24 | P0=9 P1=7 P2=8 P3=2 | no-hit areas: battery migrate/validate/score refusals; accounting completeness Optional mols; optimize/physics missing-mol None; wall_deposition area refusal; vaporock/magemin/imcc empty-melt early refuse; failclosed_silent_zero_b135; fillna(0) absent; output accumulators

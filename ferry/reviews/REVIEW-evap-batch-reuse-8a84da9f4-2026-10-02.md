# REVIEW — evaporation batch reuse (same-tick headspace)

- **Reviewer:** regolith-empirical (frontier delta of record)
- **Seat:** `/workspace/repos/wt/slot-z14`
- **Tip:** `8a84da9f43725e05dd363c4f6d7c851831ca5b75` on `review/evap-batch-reuse`
- **Parent tip:** `910ec1779330c2478f8418eff8ede5c8f39fc8d6` (redox chunk 7; verified `git log` parent)
- **Range:** one commit `910ec1779..8a84da9f4` — `Reuse evaporation batch during headspace solve`
- **Files:** `simulator/core.py` +20/−21; `simulator/evaporation.py` +82/−7; `tests/test_overhead_accounting.py` +32
- **REQ:** `/workspace/ferry-inbox/REQ-evap-batch-reuse-from-regolith-physics-2026-10-02.md`
- **Diagnosis:** `/workspace/ferry-inbox/regolith-physics-stack2-gmf-2026-10-02/evap-cpu-diff.md`
- **Ack:** `STATUS-req-evap-batch-reuse-acked-2026-10-02.md`
- **Date:** 2026-10-02 ~19:40–19:50 ET
- **Mode:** read-only for product extracts; reviewing product code; targeted VPS unit tests only (no full W3 / full pytest). No push of empirical feature branches. No Mac listen pools.
- **Also noted (out of seat scope):** clean merge into `review/stack3` `fea52be64` (c7-fix + this + green-merge-fix + green); studio PR suite on stack3.

## Scope

Chunk-7 same-tick headspace (`_calculate_evaporation_with_same_tick_headspace`) re-resolved the whole vapour batch every Picard/fallback iteration (1,012 resolutions vs 96) though resolution inputs were identical; only the post-resolve overhead backpressure overlay changes. Fix: local `same_tick_batch_cache` dict per headspace solve; resolve once when inputs match; recompute flux each iteration with new `overhead_partials_override_Pa`.

## Attack results (NOT-FIXED lens)

### (1) Invalidation key — every resolver input — **PASS**

Seat inventory of reads by `_resolve_evaporation_batch_flux_state` → `_resolve_evaporation_vapour_batch` → `build_vapour_batch` → `resolve_batch` / `resolve_vapour_batch`:

| Input read by resolve path | Key / constancy |
| --- | --- |
| `id(equilibrium)` | KEYED |
| `equilibrium.vapor_pressures_Pa` | KEYED (deepcopy on store; live compare) |
| `equilibrium.activity_coefficients` | KEYED |
| `equilibrium.diagnostics` activity subset (`activities_provider`, `vapor_pressure_numerator_provenance`, `activity_provenance`, `a_FeO_calphad`, `activities_standard_state`) | KEYED as `resolution_equilibrium_diagnostics` |
| `equilibrium.temperature_C` | KEYED |
| `temperature_K` arg (`melt.temperature_C+273.15` at call site) | KEYED |
| `_last_vapor_pressure_diagnostic` subset: `pO2_bar`, `backend_vapor_pressures_Pa`, `activities`, provider/provenance/`a_FeO_calphad`/standard-state, `source_reaction_fO2_bar`, `source_reaction_fO2_log10`, `source_reaction_activity_pressure_bar`, `source_reaction_redox_model_id`, `source_reaction_composition_wt_pct` | KEYED as `resolution_diagnostic` |
| interface / transport pO2 (`diagnostic.pO2_bar` else `equilibrium.pO2_bar`) | KEYED (also inside diagnostic) |
| `_pre_rg_effective_pressure_source` → `source_id`, sorted `_pressures_pa`, `physical_zero_reason` | KEYED |
| `effective_pressure_source.species_ids` (+ C0B campaign filter) | derived from keyed pressures + `campaign.name` |
| `melt.p_total_mbar` (→ `total_pressure_Pa`) | KEYED |
| `melt.campaign.name` (→ request `stage`) | KEYED |
| `_melt_activity_engine_inputs` | KEYED |
| `_melt_activity_shadow_enabled` | KEYED |
| `_imcc_activity_shadow_enabled` | KEYED |
| `len(atom_ledger.transitions)` | KEYED (generation proxy) |
| `atom_ledger.mol_by_account()` (in `build_vapour_batch`) | **not value-keyed** — **provably constant within one solve**: `EVAPORATION_FLUX` is read-only (F-B1 / no `commit_batch`); headspace Picard only changes overhead override |
| `vapour_rail_catalog`, `process_phase='hot_train'`, `FLUX_ACTIVATION_EPOCH_PRE_RG`, `build_vapour_batch` method | CONSTANT within solve |
| `provider_candidates_by_species` | not passed on this path (`None`) |

**Intentionally not in key (post-resolve / non-membership):**
- `overhead_partials_override_Pa` — applied at LOOP-1 after batch return (~evaporation.py:1214); flux kernel re-dispatched every iteration.
- `live_vapor_pressures` shadow map — overlay diagnostic only; effective-pressure source is keyed.
- `liquid_fraction` — gates before batch (physical-zero early return).
- setpoints / melt geometry / stir — flux controls after batch.

**Strengthen probes (seat):** mutating each of `vapor_pressures_Pa`, `activity_coefficients`, `temperature_K`, diagnostic `pO2_bar`, `campaign.name`, `_melt_activity_engine_inputs`, eq `diagnostics.activities_provider` forced a fresh resolve. Identical inputs reused (1 resolve / 2 calls). Cached state vapor map **bit-equal** to a fresh no-cache resolve.

**Missed-input residual:** ledger *mol map* is only generation-keyed. Acceptable under same-tick read-only flux; a mid-solve ledger mutation without a new transition would be a separate contract break. No evidence of such mutation on the headspace path.

### (2) Scope / cache lifetime — **PASS**

- Cache is a **local** `same_tick_batch_cache: dict = {}` inside `_calculate_evaporation_with_same_tick_headspace` (core.py); passed only via `_same_tick_batch_cache` kwarg.
- No instance/module attribute (`self._same_tick_batch_cache` absent).
- Default `_calculate_evaporation(..., _same_tick_batch_cache=None)` → no reuse outside headspace.
- Seat: two independent caches → 2 resolves; `None` cache → no reuse across calls.
- Cross-tick / cross-solve leakage: **none** by construction (locals die with the solve).

### (3) Bit-identity claim — **PASS (targeted; RH03 24h not re-run on VPS)**

- **Unit:** tip cache-hit vs fresh resolve → identical `vapor_pressures` map (`{'Fe': 100.0}` passthrough fixture).
- **Code path:** overhead overlay and EVAPORATION_FLUX dispatch remain after resolve; parent `910ec1779` flux math unchanged when inputs match.
- **RH03 24h / SiO wall-T:** **not re-run on VPS.** Blocker: full vapour-batch campaign hours are Mac-scale (worker: four wall-T trees 96 steps; medians base 73.80s / green 41.74s / candidate 44.32s on Simon-MacBookPro-M5); 16GB VPS policy is targeted unit tests only. Worker claim of SiO wall-T + RH03 24h per-hour summaries **BIT-IDENTICAL** to base, with **96 resolutions / 1,012 flux evals**, cited as strongest available campaign evidence. Optional Mac Studio ASK for a green-gate reconfirm — not blocking this seat.

### (4) Mutations — **PASS** (two red; strengthen)

Against `test_same_tick_batch_cache_reuses_only_unchanged_resolution_inputs`:

| Mutation | Effect | Result |
| --- | --- | --- |
| **A** Ignore key — return cached `state` whenever present | Changed `vapor_pressures_Pa` still 1 resolve | **RED** (assert `len==2` fails) |
| **B** Skip cache write — force `same_tick_cache=None` inside resolve | Two identical calls → 2 resolves | **RED** (assert `len==1` fails) |

Worker “two mutations red” matches A/B. Strengthen probes in (1) show the key is not vapor-pressure-only.

## Targeted VPS results (tip `8a84da9f4`)

| Nodes | Result |
| --- | --- |
| `test_same_tick_batch_cache_reuses_only_unchanged_resolution_inputs` | **passed** |
| `test_c4_two_tick_species_parity_uses_current_duct_pressure_only` | **passed** |
| `test_same_tick_high_flux_throttles_smoothly_near_equilibrium_pressure` | **passed** |
| `test_same_tick_p_bulk_uses_duct_pressure_after_near_total_capture` | **passed** |
| `test_h99_k_same_tick_pressure_is_small_against_equilibrium` | **passed** |
| Seat probes: key inventory, strengthen invalidation, scope, mutations A/B, cache-vs-fresh bit | **all green as above** |

5 passed in 6.41s (targeted; venv via slot-b565).

## Non-blocking notes

- Ledger mol snapshot not value-keyed; generation + read-only flux contract covers same-tick.
- RH03/SiO campaign bit-identity deferred to worker Mac evidence; optional Studio ASK.
- Stubbed `_calculate_evaporation` doubles without `_same_tick_batch_cache` in signature correctly skip cache (`supports_batch_cache` false) — backward compatible.
- Stack3 merge `fea52be64` out of scope.
- Seat left idle on `review/evap-batch-reuse` at tip.

## Verdict

**LAND** `8a84da9f43725e05dd363c4f6d7c851831ca5b75`

Same-tick headspace reuses vapour-batch resolution when the full input snapshot matches, recomputes flux under changing overhead partials, does not leak across ticks/solves, and fails closed when resolution inputs change. Targeted tests + mutation probes green; campaign bit-identity accepted from worker Mac measurement under VPS constraints.

— regolith-empirical

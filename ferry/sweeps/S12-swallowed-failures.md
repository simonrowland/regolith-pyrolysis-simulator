# S12 — swallowed failures

**Repo tip (code base audited):** `origin/work-v064-green` @ `fbe3491b2f515212e61f65ac8a2e4f424b868b5d` (detached in `/workspace/repos/wt/slot-05`). READ-ONLY; no product edits; no push.  
**Scope:** whole product tree (`simulator/`, `engines/`, `app.py`) for swallowed failures: `except Exception: pass`, broad `except Exception`/`BaseException` returning a default/`None`/`0`, and log-and-continue where doctrine requires a typed refusal or notice.  
**Predicate:** an exception that should surface as a typed refusal, status-bearing notice, or hard fail is caught broadly and replaced with silence, a numeric default, or continuation so a consumer sees an **unflagged** success-looking value.  
**Project doctrine:** predict-and-flag / fail-closed; absence is never a measured zero (`tests/test_failclosed_silent_zero_b135.py`); engine/shadow boundaries must not invent availability or chemistry after a library fault.  
**Rank rule:** P0 iff the swallow changes a live physics / ledger / deposition / redox / inventory result; P1 live contract break with flag missing or weaker; P2 latent / config / renormalize-adjacent; P3 cleanup / diagnostic-only best-effort.

**Corpus (not site count):** on this tip, AST over `simulator/`+`engines/` (non-test): **17** `except Exception|BaseException|bare → pass`, **15** → `continue`, **47** → return `None`/`0`/`0.0`/`False`/`{}`/`[]`/empty. Vast majority are process cleanup, `_safe_*` helpers, version probes returning `'unavailable'`, or already status-bearing (`status='not_converged'`, IMCC `imcc_gas_unavailable` notice, vapour-batch typed error dict). Sites below are curated by **consequence**.

| site (file:line) | predicate match | constructed trigger | live? | severity |
|---|---|---|---|---|
| `simulator/wall_deposition.py:557-576` (`_segment_wall_regime_factor`) **seed** | Broad `except Exception` replaces Knudsen `regime_factor` with `getattr(model, "regime_factor", 1.0) or 1.0`. Caller `wall_deposition.py:105` multiplies wall deposit candidates by that factor. Confirmed: `overhead_pressure_mbar=None` or unknown `carrier_gas` → swallow returns `1.0` (model default); honest high-P N₂ case returns ~`0.0038` (~**263×** over-deposit if swallowed). | Wall channel with viscous overhead (e.g. `overhead_pressure_mbar=100`) while Knudsen inputs are briefly non-numeric / unknown carrier; compare segment deposit rates vs honest Knudsen factor. | live | P0 |
| `engines/alphamelts/thermoengine.py:1992-2001` (`ThermoEngineTransport._oxide_mol`) | `resolve_species_formula(...).molar_mass_kg_per_mol()` under `except Exception: return 0.0`. Feeds `_fe_redox_split` FeO/Fe2O3 moles → `Fe3Fet_Liq`. Confirmed: unknown oxide wt% → `0.0` mol with no refusal. | ThermoEngine liquid_comp carrying an oxide whose formula resolve raises (registry hole / library fault) while Fe wt% > 0; redox split treats Fe as absent. | live | P0 |
| `engines/alphamelts/thermoengine.py:1946-1967` (`_endmember_moles_from_wt`) | Same formula resolve under `except Exception: mol.append(0.0); continue`. Endmember vector silently zeros that component; later thermo sees a leaner melt. | Endmember name present in `comp_wt` with wt>0 whose formula resolve raises. | live | P0 |
| `simulator/melt_backend/alphamelts.py:5540-5558` (`_MELTSBackendSupport._oxide_mole_fractions`) | `except Exception: continue` drops oxide from mole map then **renormalizes** remaining oxides to sum 1. Confirmed: `BogusXYZ` 30 wt% disappears; SiO2/Al2O3 fractions inflate. | Any path calling `_oxide_mole_fractions` with a positive-wt oxide that fails `resolve_species_formula`. | live | P0 |
| `simulator/accounting/stage0_inventory.py:706-717` (`_species_mol`) | Formula present but `molar_mass_kg_per_mol()` raises → `except Exception: return 0.0`. Inventory mol / element rows undercount as measured zero. Confirmed via patched formula raising `RuntimeError`. | Stage-0 / target-inventory diagnostic for a registry species whose molar-mass call raises (or unknown species already returns 0 via `_formula` None). | live | P0 |
| `simulator/accounting/stage0_inventory.py:500-507` (`_species_kg_by_accounts`) | `project_account_kg` then `kg_by_account` both under `except Exception: continue` — **entire account omitted** from remaining/released maps with no notice. | Ledger projection that raises on one `REMAINING_ACCOUNTS` / destination account during `remaining_c0_inventory_kg` / depletion records. | live | P0 |
| `engines/magemin/provider.py:445-451` (`MAGEMinShadowProvider._ensure_backend`) | Pre-bound backend: `initialize(...)` failure → `pass`, then **`_backend_initialised = True`**. Confirmed: `FailInit` still returned and marked initialised. Later dispatch can treat a failed init as ready. | Construct `MAGEMinShadowProvider(backend=uninitialized)` (constructor allows injected backend); first `_ensure_backend` after init raises. | live | P0 |
| `engines/vaporock/provider.py:329-336` (`VapoRockProvider._ensure_backend`) | Identical init-`pass` + mark-initialised pattern. Confirmed same. Default `backend=None` cold path still nulls on construct failure; **injected** backend path is live (tests + explicit `backend=`). | `VapoRockProvider(backend=uninitialized_adapter)` then first dispatch/`_ensure_backend`. | live | P0 |
| `engines/alphamelts/provider.py:428-438` (`_composition_wt_pct`) | Unresolvable species → `continue`; remaining oxides renormalized to 100 wt%. Comment hopes domain gate catches low major-oxide sum; **partial** drop of one major oxide while others remain does **not** empty the melt — composition silently changes. Confirmed: `{SiO2:1, BogusXYZ:0.5}` → `{SiO2: 100.0}`. | Shadow/provider composition build with one unresolvable major oxide alongside valid SiO2. | live | P1 |
| `engines/magemin/provider.py:419-422` (`_composition_wt_pct`) | Same `except Exception: continue` drop + renormalize as AlphaMELTS provider. | MAGEMin shadow composition with one unresolvable species. | live | P1 |
| `simulator/optimize/evaluate.py:5258-5261` (`_composition_wt_pct_to_mol`) | Unregistered species → `continue`; rump-terminal / proof mol maps omit mass without typed refusal on the row. | Optimizer rump-terminal proof with a wt% key that fails `resolve_species_formula`. | live | P1 |
| `simulator/melt_backend/sulfsat.py:620-638` (`_resolve_fe3fet`) | Kress91 fit `except Exception` returns **`0.0` Fe3+/ΣFe** plus warning string and `usable=False`. Number is still a measured-looking zero ratio; doctrine prefers refuse/out_of_range without a fabricated 0.0 when no operator Fe3Fet supplied (warning exists but value remains). | Sulfur-sat path with FeOt>0 and Kress91 raising (bad mol fractions / fO2). | live | P1 |
| `simulator/optimize/evaluate.py:1910-1922` (`_knudsen_pipe_diameter_m`) | Config load / parse `except Exception: pass` then `DEFAULT_PIPE_DIAMETER_M`. Knudsen summaries can quietly use the transport default when setpoints are broken — no notice on the summary. | Optimizer Knudsen fallback with unreadable `data/setpoints.yaml` furnace.hot_wall_pipe. | live | P2 |
| `simulator/furnace_materials.py:38-43` (`_catalog_max_service_T_C`) | Catalog load `except Exception: return default` (2000). Confirmed: load failure → `2000.0` while healthy catalog → `2200.0` (clips envelope downward). Safer than inventing hotter service, but still a silent wrong bound. | Break `load_furnace_materials` during furnace T envelope build. | live | P2 |
| `simulator/core.py:12942-12951` (`step` poisoned-hour summary) | `PoisonedHourState` construction `except BaseException: pass` then **`raise`** original — swallows only the summary string builder; hour failure still propagates. | Force poisoned-hour path where building `aborting_exception_summary` itself raises. | live | P3 |
| `simulator/cost_ledger.py:665-676` + `simulator/core.py:3018-3049` | Best-effort cost observation: exceptions recorded via warning list / `_LOGGER.warning`, physics continues. Nested `except Exception: pass` if warning append fails. Cost is declared diagnostic-only — not a physics result change. | Cost ledger `observe_transition` raising mid-hour. | live | P3 |
| `simulator/engine_pool.py:259-274` | Worker bootstrap: `connection.send` / `close` failures → `pass` after primary error already classified. Cleanup-only. | Native worker crash where IPC send also fails. | live | P3 |
| `simulator/backends.py:928-941` / `:1129-1138` / `:1161-1168` | Advisory marker / resolution-status attribute attach `except Exception: return/pass`. Selection still status-bearing elsewhere; markers may be missing. | Backend object rejecting attribute sets. | live | P3 |
| `simulator/melt_backend/vaporock.py:1048,1132,1144,1150,1165,1432` | Session/warm-pool/close cleanup `except Exception: pass`. No chemistry invent. | Warm-pool teardown faults. | live | P3 |
| `simulator/diagnostic_helpers/binary_pot_battery.py:2241-2244` | Timeout path: `closer()` `except Exception: pass` then marks handle unavailable — typed refusal already returned. | Battery cell timeout whose `close()` raises. | live | P3 |

## Counts

- Sites: 20  
- Live: 20  
- P0: 8 · P1: 4 · P2: 2 · P3: 6  

## No-hit areas (audited; not counted as swallows)

- **Typed library boundaries that keep status:** `magemin`/`vaporock` adapter `equilibrate` → `EquilibriumResult(status=...)`; MAGEMin provider `_run_backend` → `not_converged` + warning; VapoRock `_run_backend` → `None` projected as `backend_status='unavailable'`; evaporation `_resolve_evaporation_vapour_batch` → typed `_last_vapour_batch_resolve_error`; extract_reproduction `*_exception:` reason strings; IMCC gas failure → `imcc_gas_unavailable` notice.  
- **Narrow `(TypeError, ValueError)` parse clamps** (float coercion) — out of class unless paired with `Exception` (see S6 for absence→number).  
- **Shadow planner** `chemistry/kernel/planner.py:187` / `:243` — records `shadow_error` / `parity_error` then continues; authoritative path still runs (by design; not an unflagged success).  
- **Vapour-rail shadow activity** `request.py` — `shadow_unavailable_no_behavior_change` status maps.  
- **`except Exception: pass` cleanup** for process groups / pipes already covered under P3.  
- **Version probes** returning `'unavailable'` (`_engine_version`, sulfsat package version) — diagnostic identity only.  
- **Ledger `set_account_policy`** `ledger.py:1251` — catches then **re-raises** after rollback (not a swallow).  
- **S6 / S7 classes** — `.get(..., 0.0)` absence→number and notice-drop-in-transit are separate sweeps; only except-driven defaults listed here.

## Method

- Mode: **ran-tests** (focused runtime probes via `uv run python` on tip) + static AST corpus.  
- Probes: wall regime None/bad-carrier vs high-P Knudsen; ThermoEngine `_oxide_mol` / endmember zeroing; stage0 `_species_mol` bad molar mass; MAGEMin/VapoRock init-pass mark; AlphaMELTS composition renormalize; furnace catalog default.

SWEEP: S12 | sites=20 | live=20 | P0=8 P1=4 P2=2 P3=6 | tip=fbe3491b2f515212e61f65ac8a2e4f424b868b5d | path=/workspace/ferry-inbox/sweeps/S12-swallowed-failures.md

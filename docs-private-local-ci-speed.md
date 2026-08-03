# CI-speed audit + safe fixes (train10 / a9a46cf)

Date: 2026-08-02  
Baseline: `ci-train10-a9a46cf.xml` (7 233 tests, **10 221.9 test-seconds**)  
Worktree tip for fixes: detached `a9a46cf` (train10 / VR-5 landed)  
Prior campaign: `docs-private/research/2026-07-27-ci-speed/report.md`  
Warm-pool layer: VR-5 `simulator/melt_backend/vaporock.py` + shared `simulator/engine_pool.py` (MAGEMin warm default-on; VapoRock warm opt-in)

**No commit** — paths staged only.

---

## 1. Verdict

The owner’s COLD-ENGINE suspicion is **mostly wrong for the top of the wall-clock histogram**.

| Driver (top-40 test-seconds) | Share of top-40 | Notes |
|---|---:|---|
| **CAMPAIGN-LENGTH** | ~92% | Full C0→C6 / runner / web full-path / magemin_fullrun_* |
| **FIXTURE-SCOPE** | ~2–3% | Timeseries lake re-ran `validate_lake()` thrice; Mg bisection per-param |
| **COLD-ENGINE** | ~1% of suite | VapoRock+alphaMELTS+thermoengine family ≈ 150 s total |
| **RECOMPILE** | residual | VR-3/VR-7 catalog/legacy-view already cached in production paths; test YAML reloads minor |

The magemin_fullrun chains still dominate suite test-seconds (`A≈2904s`, `B≈2320s`, `C≈610s`, `D≈296s` after the 2026-07-27 folds). Prior folds (yield-root-cause module fixture, electrolysis module fixture, condensation package fixture) already removed the largest *redundant* full runs. Remaining full runs are **distinct physics inputs** (or retained consumers of those fixtures) — not cold alphaMELTS/MAGEMin boots.

Safe wins landed here are therefore **fixture-scope + session warm-boot cache**, not campaign cuts. Campaign cuts are listed as **nightly-marker candidates** (owner call).

---

## 2. Top-40 slowest tests (from `ci-train10-a9a46cf.xml`)

| # | s | Class | Test | Cost driver | Warm-pool / session-fixture candidacy |
|---:|---:|---|---|---|---|
| 1 | 1455.1 | yield_root_cause | `test_pyrolysis_track_c5…@magemin_fullrun_b` | **CAMPAIGN-LENGTH** (retained module fixture run) | No — physics track |
| 2 | 1235.2 | electrolysis_step | `test_full_run_mass_balance…[lunar]@magemin_fullrun_a` | **CAMPAIGN-LENGTH** (retained module fixture) | No |
| 3 | 1131.5 | electrolysis_step | `test_full_run_mass_balance…[mars]@magemin_fullrun_a` | **CAMPAIGN-LENGTH** | No |
| 4 | 390.5 | web_events_decision_pause | `test_pause_resume_around_every_gate…@serial` | **CAMPAIGN-LENGTH** (serial web path) | No — Socket.IO state |
| 5 | 327.0 | web_functional_qa | `test_alternate_path_b…@serial` | **CAMPAIGN-LENGTH** | No |
| 6 | 254.7 | staged_bakeout | `test_c2a_staged_k_shuttle…@magemin_fullrun_b` | **CAMPAIGN-LENGTH** | No |
| 7 | 250.7 | runner_smoke | `test_runner_records_operator_decision…@magemin_fullrun_a` | **CAMPAIGN-LENGTH** | No |
| 8 | 247.3 | run_executor | `test_run_executor_partial_path…@magemin_fullrun_a` | **CAMPAIGN-LENGTH** | No |
| 9 | 202.7 | condensation_route | `test_full_run_mass_balance…[lunar]@magemin_fullrun_c` | **CAMPAIGN-LENGTH** (package fixture first consumer) | Already package-scoped |
| 10 | 186.5 | web_functional_qa | `test_headless_full_run…@serial` | **CAMPAIGN-LENGTH** | No |
| 11 | 178.4 | condensation_route | `…[s_type_asteroid]@magemin_fullrun_c` | **CAMPAIGN-LENGTH** | Package fixture |
| 12 | 168.3 | metallothermic | `test_full_run_mass_balance…[lunar]@magemin_fullrun_b` | **CAMPAIGN-LENGTH** | Cannot share package fixture (different additives) |
| 13 | 155.7 | condensation_route | `…[mars]@magemin_fullrun_c` | **CAMPAIGN-LENGTH** | Package fixture |
| 14 | 153.6 | metallothermic | `…[mars]@magemin_fullrun_b` | **CAMPAIGN-LENGTH** | Same |
| 15 | 140.9 | mass_balance | `test_cumulative_transition_mass_closure…@magemin_fullrun_b` | **CAMPAIGN-LENGTH** | No |
| 16 | 133.6 | metallothermic | `test_c6_ci_empty_window…@magemin_fullrun_b` | **CAMPAIGN-LENGTH** | No |
| 17 | 89.9 | make_recipe_db_profile | `test_target_menu_generated…[pc-extract-k]@magemin_fullrun_d` | **CAMPAIGN-LENGTH** (profile eval) | No |
| 18 | 72.0 | make_recipe_db_profile | `…[pc-extract-na]@magemin_fullrun_d` | **CAMPAIGN-LENGTH** | No |
| 19 | 70.6 | make_recipe_db_profile | `…[pc-extract-o2]@magemin_fullrun_d` | **CAMPAIGN-LENGTH** | No |
| 20 | 69.8 | optimizer_staged | `test_one_topology_vs_all_topologies_study` | **CAMPAIGN-LENGTH** (optimizer study) | No |
| 21 | 43.4 | timeseries_validation_lake | `test_validation_lake_reports…` | **FIXTURE-SCOPE** | **Fixed** — module-scope lake |
| 22 | 40.8 | timeseries_validation_lake | `test_markdown_report…` | **FIXTURE-SCOPE** | **Fixed** |
| 23 | 40.6 | timeseries_validation_lake | `test_endpoint_rank_metric…` | **FIXTURE-SCOPE** | **Fixed** |
| 24 | 35.7 | yield_root_cause | `test_c5_targeted_feo_full_track…@magemin_fullrun_d` | **CAMPAIGN-LENGTH** (distinct inputs) | No |
| 25 | 31.9 | physics_ground_truth | `test_mg_phase_correct…[moon…]` | **FIXTURE-SCOPE** (pure bisection ~19 s) | **Fixed** — module-scope roots |
| 26 | 28.2 | physics_ground_truth | `test_mg_phase_correct…[asteroid…]` | **FIXTURE-SCOPE** | **Fixed** |
| 27 | 27.9 | optimizer_study | `test_cli_help_unknowns…` | CAMPAIGN-ish / CLI | Leave |
| 28 | 26.7 | make_recipe_db_profile | `test_target_menu_all_emits…` | CAMPAIGN-ish | Leave |
| 29 | 26.5 | sio_tsweep_smoke | `…[mars_basalt]@magemin_fullrun_c` | **CAMPAIGN-LENGTH** (live MAGEMin) | Warm already default-on for MAGEMin |
| 30 | 25.7 | sio_chain_coherence | `test_sio_evolved_is_invariant…` | Physics compute | Leave |
| 31 | 23.5 | sio_tsweep_smoke | `…[lunar]@magemin_fullrun_c` | **CAMPAIGN-LENGTH** | MAGEMin warm |
| 32 | 23.3 | sio_tsweep_smoke | `test_sio_wall_sweep_cli_smoke@magemin_fullrun_c` | **CAMPAIGN-LENGTH** | MAGEMin warm |
| 33–37 | 20–22 | optimizer_* | two-phase / staged / cache | Optimizer study | Leave |
| 38 | 20.0 | sio_step_condensation | `test_wall_band_capture…` | Physics compute | Already lru_cached helpers |
| 39–40 | 18.7–19.0 | mass_balance | `test_c2a_staged_freeze_gate…@serial` | **CAMPAIGN-LENGTH** | Leave |

### Top files by sum time (context)

| Sum s | n | File |
|---:|---:|---|
| 2382 | 37 | chemistry/test_builtin_electrolysis_step_provider |
| 1498 | 15 | test_yield_root_cause |
| 560 | 50 | chemistry/test_builtin_condensation_route_provider |
| 515 | 7 | test_web_functional_qa |
| 490 | 64 | chemistry/test_builtin_metallothermic_step_provider |
| 488 | 112 | test_make_recipe_db_profile |
| 402 | 3 | test_web_events_decision_pause |
| 376 | 116 | test_optimizer_study |
| 351 | 71 | test_runner_smoke |
| 125 | 4 | test_timeseries_validation_lake ← **fixed** |
| 77 | 79 | test_physics_ground_truth ← **partial fix** |
| 65 | 218 | *vaporock* family (all tests) |
| 46 | 316 | *alphamelts* family |

---

## 3. Cold-engine cross-check (VR-5 / engine pool)

| Site | Pattern | Can adopt VR-5 warm / session fixture without physics change? |
|---|---|---|
| `VapoRockProvider._ensure_backend` | Cold `VapoRockBackend().initialize({})` per lazy construct | **Yes — landed**: session warm pool when `REGOLITH_VAPOROCK_SESSION_WARM=1` |
| `vaporock_runtime_available` | Probe with `warm_worker=False` | Correctly stays cold (spawn waste + monkeypatch safety) |
| Live `tests/test_vaporock_backend.py` | Explicit `warm_worker=False` unit path; opt-in warm live smoke | Keep explicit; unit tests must not inherit warm |
| `tests/chemistry/test_corpus_anchored_parity.py` | Module fixtures; probe `warm_worker=False` | Probe stays cold; provider path benefits from session warm |
| MAGEMin full-run tests | `MAGEMinBackend` / liquidus via InternalAnalytical + live MAGEMin slots | Warm **already default-on** (`warm_worker=True`); cost is physics not boot |
| alphaMELTS backend tests | Mostly fakes; petthermotools warm opt-in | Already covered; suite family ~46 s |
| Metallothermic full_run | Re-runs campaign with **non-package** additives | Not a cold-engine site; fold would need additive-aligned package params (physics review) |

**RECOMPILE:** VR-3 catalog compile + VR-7 legacy-view already have process caches (`u0_manifest`, `vapor_pressure_legacy_view` consumers). Residual per-test YAML reloads in ground-truth helpers were `lru_cache`d (read-only; mutating tests already `deepcopy`).

---

## 4. Staged safe wins (implemented)

### W1 — Timeseries validation lake module fixtures (`FIXTURE-SCOPE`)

**Files:** `tests/test_timeseries_validation_lake.py`  
**Change:** `validation_lake_reports` / `validation_lake_catalog` / `validation_lake_by_dataset` at `scope="module"`. Three consumers share one `validate_lake()` pass. Pure read-only over static datasets — golden-neutral.

| | Before (junit) | After (`-n0`) |
|---|---:|---:|
| File wall | 124.8 s (4 tests) | **35.5 s** (4 passed) |
| Savings | | **≈ 89 s** |

### W2 — Mg 0.01 bar root module fixture + YAML lru_cache (`FIXTURE-SCOPE`)

**Files:** `tests/test_physics_ground_truth.py`  
**Change:** Module-scoped `mg_phase_0p01_bar_roots` for both body params; `@lru_cache` on `_setpoints_data` / `_vapor_pressure_data` (mutators already deepcopy).

| | Before (junit) | After (`-n0`) |
|---|---:|---:|
| Two Mg params | 31.9 + 28.2 = 60.1 s | **~39.6 s** for both + session-cache unit tests bundled |
| Mg-only local | | **~38–40 s** for both roots (sequential bisections still required) |
| Savings | | **≈ 20 s** vs junit attribution (import/xdist noise + shared setup) |

Note: both roots still compute (different fO2); savings is shared setup/attribution, not deleting a bisection. Full file: 78 passed; 1 **pre-existing** fail on a9a46cf (`test_mn_source_spread…` expects legacy raw YAML token `chosen` but `data/vapor_pressures.yaml` is schema_version 2 catalog — unrelated to this change).

### W3 — Process-scoped VapoRock warm-boot cache + provider routing (`COLD-ENGINE`)

**Files:**  
- `simulator/melt_backend/vaporock.py` — `get_or_create_session_backend`, `clear_session_backend_cache`, `SESSION_WARM_ENV`  
- `engines/vaporock/provider.py` — lazy path uses session warm when enabled  
- `conftest.py` — default `REGOLITH_VAPOROCK_SESSION_WARM=1` for pytest processes; clear cache before `close_all_engine_pools`  
- `tests/test_vaporock_backend.py` — unit test for reuse / opt-out  

**Semantics:** Caches **boot/worker only** (VR-5 warm pool). No result/calibration cache (owner ruling / VR-10). Opt-out: `REGOLITH_VAPOROCK_SESSION_WARM=0`. Explicit backends and `warm_worker=False` probes unchanged.

| | Measurement |
|---|---|
| `tests/test_vaporock_backend.py` full | **38 passed in 15.6 s** |
| Session-cache unit test | pass |
| Authority + corpus subset | **66 passed, 51 skipped** in 28.2 s |
| A/B warm 0 vs 1 on small live set | 15.4 s vs 16.7 s (noise; this set mostly cold-explicit inits) |

**Projected suite savings from W3:** modest. VapoRock-named tests total **~72 s** in junit; boot-share helps multi-provider sessions more than single-backend suites. Conservative staged credit: **≈ 15–30 s** suite-wide until a full train remeasure. Golden-neutral by construction (same equilibrate path).

### Intentionally not done

- No campaign-length test edits, no nightly markers applied.  
- No metallothermic→package-fixture fold (additives differ from `full_builtin_provider_run`).  
- No further A/B/C group splits (prior campaign: co-tenancy risk).

---

## 5. Nightly-marker candidates (owner call — not gated here)

Estimated savings = junit test-seconds removed from the PR gate if marked `@pytest.mark.nightly` (or equivalent). **Wall-clock** savings under xdist is ≤ critical-chain reduction, not the sum.

| Candidate | junit s | Why nightly-safe to consider |
|---|---:|---|
| Yield retained pyrolysis track (fullrun_b) | 1455 | Already has assertion-only siblings from prior fold |
| Electrolysis retained full runs ×2 (fullrun_a) | 2367 | Same |
| Condensation package consumers ×3 (fullrun_c) | 537 | Package fixture; assertions only on consumers |
| Metallothermic full runs ×2 + C6 CI empty | 456 | Long InternalAnalytical campaigns |
| Web serial full paths ×3 | 904 | Socket.IO / gate pause coverage |
| Runner + executor magemin_fullrun_a | 498 | Operator/decision path |
| Staged bakeout C2A | 255 | Campaign |
| Cumulative mass balance + freeze-gate pair | 179 | Campaign / serial |
| make_recipe_db_profile D rows (extract-*) | 232 | Analytical eval; roster timeout history |
| SiO tsweep / wall sweep (fullrun_c) | 73 | Live MAGEMin smoke |
| Targeted FeO full track (fullrun_d) | 36 | Distinct inputs from folded track |
| **Sum of listed candidates** | **≈ 6991 s** | ≈ **68% of suite test-seconds** |

**Recommended owner tiers (optional):**

1. **Tier N1 (largest, already folded consumers):** yield + electrolysis retained runs (~3.8 ks test-s; also shrinks critical chain A/B).  
2. **Tier N2:** web serial full paths (~0.9 ks).  
3. **Tier N3:** remaining magemin_fullrun_* smoke except a thin PR-gate sentinel per family.

Do **not** gate these without an explicit nightly job that still runs them.

---

## 6. Receipts (`-n0`)

| Command | Result |
|---|---|
| `pytest tests/test_timeseries_validation_lake.py -n0` | 4 passed in 35.52 s |
| `pytest tests/test_physics_ground_truth.py::test_mg_phase_correct_0p01_bar_threshold` + session-cache units | 4 passed in 39.57 s |
| `pytest tests/test_vaporock_backend.py -n0` | 38 passed in 15.60 s |
| `pytest tests/chemistry/test_vaporock_authority_promotion.py` + corpus `-k 'grid_25 or vaporock or loader'` | 66 passed, 51 skipped in 28.17 s |
| `pytest tests/test_physics_ground_truth.py -n0` | 78 passed, **1 pre-existing fail** (`test_mn_source_spread…` / schema v2) |

---

## 7. Totals (FINAL)

| Metric | Value |
|---|---:|
| **Current total** (junit test-s) | **10221.9 s** (2.84 h) |
| **Staged savings** (measured + conservative W3) | **≈ 110–140 s** (~1.1–1.4% suite) |
| **Projected total** after staged | **≈ 10080–10110 s** |
| **Nightly-candidate savings** (if owner gates listed) | **≈ 6991 s** (~68% suite test-s; wall ≤ critical-chain) |

Critical-chain note (from prior campaign, still valid): gate wall is bounded by `max(S/N_workers, longest_xdist_group)`, not by S. Staged fixture wins barely move the 32-worker wall; nightly markers on A/B retained full runs would.

---

## 8. Staged paths (no commit)

```
conftest.py
engines/vaporock/provider.py
simulator/melt_backend/vaporock.py
tests/test_physics_ground_truth.py
tests/test_timeseries_validation_lake.py
tests/test_vaporock_backend.py
docs-private-local-ci-speed.md
```

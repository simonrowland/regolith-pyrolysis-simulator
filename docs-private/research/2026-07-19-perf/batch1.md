# Performance campaign batch 1 — profile and determinism-safe patches

Date: 2026-07-19

Baseline: `57f28975eef27ff35e6ec2c2a086f15a8473d39e`

Environment: canonical `.venv`, Python 3.12.13, macOS arm64, tests forced `-n0`

## Executive result

Batch 1 contains two measured reuse patches plus one cold-path timeout cleanup:

1. Exact-byte YAML parse reuse removes repeated parsing while preserving a fresh mutable mapping per caller. A representative optimizer evaluation fell from a 0.661 s median to 0.073 s (9.0x); a full internal runner cProfile fell from 2.388 s to 1.852 s with an identical output SHA-256.
2. Liquidus sampling now reuses the raw fraction for an exactly repeated temperature while preserving the historical sample list and iteration count. The measured step-curve case fell from 22 to 15 engine calls and 1.192 s to 0.803 s (32.7% wall reclaimed).
3. The normal MAGEMin liquidus path was already migrated to the shared warm pool at baseline. Its aggregate budget remains 15 s. The vestigial explicit-cold diagnostic budget is reduced from 900 s to 300 s.

No optimizer parallelism, cache identity, `EvalSpec`, results-store schema, golden file, or engine/optimizer cache-key code was changed.

## Profile method and limitations

Raw artifacts are under `docs-private/research/2026-07-19-perf/`:

- `profile-eval-internal/{cprofile.txt,timings.json}` — before patch.
- `profile-eval-after/{cprofile.txt,timings.json}` — after patch.
- `raw/{runner-internal-before.prof,runner-internal-after.prof}`.
- `raw/{magemin-warm-before.json,magemin-warm-after.json}`.
- `raw/grid-prepare.prof`.
- `benchmark_liquidus_reuse.py` — deterministic 50 ms/sample duplicate benchmark.

The requested `scripts/profile_magemin_runtime.py` is absent at HEAD. It exists at non-ancestor commit `3067c37`; that exact version was temporarily restored to run the measurements, then removed from the worktree. It was not included in the patch.

The installed AlphaMELTS subprocess transport is unavailable in this sandbox, so the optimizer and full-runner cProfiles use the real internal-analytical evaluation/runner control flow. MAGEMin itself was exercised through the compiled binary. Wrapping grid drain directly in `cProfile` changes multiprocessing `__main__` identity and fails pickling `_bootstrap_grid_worker`; grid preparation was cProfiled, and the drain was timed normally.

## Workload profiles

### 1. Representative optimizer evaluation

Workload: `scripts/profile_eval_hotpath.py`, lunar-mare profile, real `evaluate()`, internal-analytical 1 h, five timed runs plus one cProfile run.

Before patch:

| Hot function | Calls | Cumulative time |
|---|---:|---:|
| `simulator.optimize.evaluate:evaluate` | 1 | 2.333 s |
| `yaml.safe_load` | 35 | 1.957 s |
| `simulator.config:load_config_bundle` | 4 | 1.680 s |
| `_build_eval_inputs` | 1 | 0.866 s |
| runner `_session_config` | 1 | 0.409 s |
| wall-fouling/coating report path | 1 | 0.403–0.425 s |

The four bundle loads represent 24 YAML parses over only six config files. Within the eval, 18/24 are exact repeats; earlier setup in the same process can make all 24 repeated. Total `safe_load` calls were 35 because several older direct YAML readers bypass `load_config_bundle`.

Unprofiled medians:

- single eval: 0.660821 s
- sequential same-PID eval: 0.648993 s/eval

After patch:

- single eval: 0.073344 s (0.587477 s saved, 88.9%, 9.01x)
- sequential same-PID eval: 0.075475 s/eval (0.573518 s saved, 88.4%, 8.60x)
- cProfile: 0.616 s total; 11 `safe_load` calls / 0.254 s remained outside or before the central loader reuse opportunity.

### 2. Full runner scenario

Workload: `python -m simulator.runner`, lunar mare/C0/1 h/internal-analytical, pinned start time and kernel SHA.

Before patch cProfile:

| Hot function | Calls | Cumulative time |
|---|---:|---:|
| runner `main` / `run` | 1 | 1.719 / 1.714 s |
| `RunExecutor.execute` | 1 | 1.320 s |
| `yaml.safe_load` | 23 | 1.047 s |
| `load_config_bundle` | 2 | 0.781 s |
| PySulfSat import/initialize | 1 | 0.884 s |

After patch: 1.852 s total versus 2.388 s before, saving 0.536 s (22.4%). `safe_load` fell from 23 to 17 calls; its cumulative time fell from 1.047 s to 0.624 s. The remaining runner startup is dominated by imports and PySulfSat initialization.

The before/after runner output files are byte-identical:

`69c1c0b28b897acd6f54d66abe26b3bf91ab31a6ff5128acc2cef631d912df44`

### 3. MAGEMin points plus full liquidus

Workload: 21 representative lunar/Mars/asteroid equilibrium points followed by a full lunar liquidus/solidus search, one persistent warm worker.

Before patch:

- total: 7.3249 s
- point p50: 0.09173 s
- liquidus: 47 calls, 5.1507 s, call p50 0.10722 s
- liquidus/solidus: 1370.3125 C / 918.75 C

After patch:

- total: 6.0736 s
- point p50: 0.08511 s
- liquidus: 47 calls, 4.0663 s
- liquidus/solidus unchanged: 1370.3125 C / 918.75 C

After removing timing-only fields, the before/after semantic JSON bytes are identical. This lunar curve has no repeated bisection temperature, so the new liquidus memo removes zero calls here. The wall difference is ordinary engine variance, not claimed patch speedup.

### 4. Grid/pregrind slice

Preparation profile: 660 candidate compositions, one temperature, one fO2, three materialized keys.

| Hot function | Calls | Cumulative time |
|---|---:|---:|
| module + `main` | 1 | 0.831 / 0.605 s |
| `yaml.safe_load` | 7 | 0.474 s |
| `build_grid_points` | 1 | 0.215 s |
| `alphamelts_queue_domain_reason` | 660 | 0.212 s |
| imports | 260/11 | 0.225 s |

Normal one-worker drain of three ThermoEngine points took 14.62 s wall. All three points refused/failed, so it is not a representative successful-solve throughput number. It still exposes a cold-start issue: dependency import, VapoRock delegate setup, ThermoEngine health/init, and noisy native diagnostics dominate tiny refused point timings. This is ranked for batch 2 rather than patched speculatively.

## Redundancy inventory

| Rank | Repeated work | Measured redundancy | Batch action |
|---:|---|---:|---|
| 1 | Central config YAML parse | 18 duplicate parses within one optimizer eval; 24 eliminated when the same-process setup already warmed the six exact files | Patched |
| 2 | Step-like liquidus solidus/liquidus bisections | 22 requests, 15 unique temperatures, 7 duplicates (31.8%) | Patched |
| 3 | Builtin vapor-pressure activity derivation | 10 oxide activity calls derive the same single-cation fraction map; 100 mapping visits versus 10 necessary | Batch 2 |
| 4 | MAGEMin input projection inside a liquidus search | cleaned-melt split + oxide projection + bulk projection repeated for each of 47 samples although composition is invariant | Batch 2 |
| 5 | Older direct YAML readers | 11 `safe_load` calls / 0.254 s remain in the optimizer cProfile after central reuse | Batch 2 |

## Staged patch batch

### Patch A — exact-byte config parse reuse

`simulator/config.py` caches parsed YAML and its digest by `(exact raw bytes, functional-digest mode, path)`, with a bounded 32-entry LRU. Every public load still reads the file bytes, so an in-process file rewrite invalidates the lookup immediately. Every return deep-copies the parsed mapping, so mutable state never leaks between callers or runs.

Proof:

- focused tests count six parses over two complete bundle loads, mutate the first bundle, and prove the second is isolated;
- a same-path byte rewrite produces new content and digest;
- optimizer and runner timings above;
- runner output SHA-256 identical before/after;
- runner determinism scenarios pass.

### Patch B — exact-temperature reuse inside one liquidus find

`find_liquidus_solidus_by_fraction` memoizes only the raw fraction returned by the engine for an exact float temperature during that finder invocation. Duplicate requests still pass through clamping, monotonicity checks, sample append/sort, and iteration accounting. Therefore the serialized sample list and algorithm shape remain unchanged.

For output compatibility, budget warnings/diagnostics continue to count logical sample attempts (`len(samples)`), not physical engine invocations. A budgeted duplicate regression uses a deterministic fake clock to prove 15 decreasing residual-budget engine calls, unchanged 22 logical samples / 14 iterations, and no new diagnostics.

Measured benchmark (50 ms deterministic engine sample):

| | Engine calls | Unique calls | Result samples | Iterations | Wall |
|---|---:|---:|---:|---:|---:|
| Before | 22 | 15 | 22 | 14 | 1.192 s |
| After | 15 | 15 | 22 | 14 | 0.803 s |

Saved: 7 calls and 0.389 s (32.7%). At the observed warm MAGEMin 0.107 s median, the same overlap would reclaim about 0.75 s. The profiled lunar curve had no overlap and correctly retained 47 calls.

### Patch C — explicit-cold liquidus hang budget cleanup

Production MAGEMin initialization already creates `EngineWorkerPool(size=1)` by default and selects `MAGEMIN_WARM_LIQUIDUS_BUDGET_S = 15`. No second pool or routing patch was added.

The cited mass-balance test does not execute MAGEMin: it uses `InternalAnalyticalBackend`, installs a constant liquidus stub, and disables gate dispatch. Its two parameters passed in 17.67 s (18.81 s process wall), not 163 s. The stale `~163s MAGEMin` comment was corrected while retaining the serial marker pending xdist/co-load proof.

`DEFAULT_LIQUIDUS_FINDER_BUDGET_S` applies only when `warm_worker=False`; it is reduced from 900 s to 300 s. The derivation retains 1.84x headroom over the historical 163 s cold measurement. A literal unit assertion pins the intended cold ceiling.

Live proof after the change:

`MAGEMIN_RUNTIME_POOL_BYTE_IDENTITY planetary=3/3 liquidus=1/1` — one warm/cold canonical-byte comparison test passed in 9.09 s.

## Determinism, golden, and balance gates

| Gate | Result |
|---|---|
| Config + liquidus + MAGEMin focused tests | 86 passed, 9 skipped |
| Native MAGEMin warm/cold planetary + liquidus byte gate | 1 passed in 9.09 s; planetary 3/3, liquidus 1/1 |
| Mass balance (`tests/test_mass_balance.py`) | 15 passed |
| Runner deterministic scenarios (lunar/Mars/asteroid) | 3 passed |
| Direct full-run before/after output hash | byte-identical SHA-256 above |
| Golden files changed | none |

The committed runner golden comparison currently fails for lunar and Mars and passes for asteroid. This is pre-existing at untouched HEAD `57f2897`: the same clean detached worktree produced the same 2 failures / 1 pass. No golden was regenerated or modified. Combined with the identical before/after runner hash, this batch is golden-neutral even though baseline HEAD is not golden-green in this environment.

## Timeout follow-up proposal

No global pytest timeout was reduced in batch 1. The repo-root `AGENTS.md` named by the stale comment is absent from this worktree, so there is no tracked file to edit here.

Recommended after a quiesced full-suite `--durations=50` run:

1. Give the intentional cold-vs-warm native parity test an explicit 360 s timeout (above the 300 s cold aggregate plus startup/teardown).
2. If no other legitimate default test exceeds the limit, reduce the global `pyproject.toml` timeout from 300 s to 120 s.
3. Update the external/private AGENTS invariant to state: normal MAGEMin liquidus is retained-worker warm with a 15 s aggregate budget; 300 s is reserved for explicit `warm_worker=False` diagnostics/parity; the stubbed C2A mass-balance test does not set the global floor.
4. Re-prove the C2A test under xdist/co-load before removing its serial marker.

## Ranked remaining opportunities for batch 2

1. **C2A freeze-gate test per-step overhead.** Current cProfile: 39 VapoRock shadow dispatches consume 4.77 s; 39 refusal snapshots consume 3.46 s; thermal-train YAML loads occur 136 times and consume 2.05 s. Reuse invariant config and avoid repeated diagnostic reconstruction. Estimated test win: several seconds, subject to byte-order preservation.
2. **Finish centralizing exact-byte YAML loads.** Eleven direct `safe_load` calls / 0.254 s remain in the optimizer cProfile; `optimize/recipe.py` independently parses `setpoints.yaml`. Route them through the same isolated loader without changing fallback/error contracts. Estimated eval win: 0.1–0.25 s.
3. **Cold worker import/startup.** Profiles observed multi-second matplotlib font discovery, VapoRock import/delegate setup, and ThermoEngine init/health work. Keep plotting imports out of headless workers and lazily initialize VapoRock only when selected. Estimated cold/grid/CI win: several seconds per worker; warm throughput unchanged.
4. **Hoist invariant liquidus input projection.** MAGEMin repeats account split and oxide/bulk projection for every sample (47 times in the lunar profile). Prebuild an immutable request template and vary only temperature/remaining timeout. Likely sub-second, lower than native solve time but multiplied across grids.
5. **Bulk melt activity calculation.** Compute the single-cation fraction map once, then derive all requested oxide activities while preserving warning/result order. Removes 9/10 repeated fraction-map derivations per full dispatch.
6. **Grid domain filtering.** `alphamelts_queue_domain_reason` took 0.212 s across 660 points. Hoist invariant policy/config and batch vectorizable checks; estimated preparation win ~0.1 s at this slice, larger on full materializations.
7. **Serialization/I/O measurement.** JSON/SQLite routines did not enter the top profile rows collected here. Add child-side pool profiling and SQLite/JSON byte counters before changing serialization; no speculative patch yet.

Optimizer parallelism is intentionally omitted from this list: owner evidence says it already sustains about 80% of CPU count, and this batch did not touch it.

## Cache-reissue lane

No cache-key miss was fixed here. Engine/optimizer cache identity, `EvalSpec`, and results-store schema are untouched. Any cross-run should-hit/miss evidence belongs to the parallel cache-identity reissue; none of the staged changes should be folded into that lane.

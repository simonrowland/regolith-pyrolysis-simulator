# Optimizer e2e performance profile and deterministic wins

Date: 2026-07-19

Baseline: `7f7fe8d` (`perf(t-363): fix O(hours^2) refusal-state deepcopy hang`)

Environment: canonical `.venv`, macOS arm64, Python 3.12.13

Scope: optimizer study/evaluate/pool/result-store path; no cache-key, EvalSpec identity, result-store schema, or parallel scheduling changes.

## Executive result

Two study-depth costs were patched without changing result bytes. Sampler `tell()` no longer rebuilds the full historical candidate-ID set every batch: at parallel width four this removes approximately `N^2 / 8` historical visits. A 30,000-result benchmark fell from 1.452 s to 0.00470 s (309x). Complete-objective Pareto selection now maintains the current frontier instead of comparing every candidate to the whole study; a deterministic 5,000-point, three-objective study fell from 0.302 s to 0.0104 s (29.2x). Nullable objective vectors retain the prior all-pairs algorithm because partial-vector dominance is not transitive.

The four-evaluation analytical e2e profile was engine-bound, so its 12.76 s to 12.78 s delta is noise. The same fixed-seed before/after run produced byte-identical `pareto.json`, `leaderboard.csv`, `study.profile.yaml`, and `search_provenance.json`; `pareto.json` SHA-256 was `5bfad8e67fab90df59be74f237b1d8243240270577a23a195fce88e39443586d` on both revisions.

## Method

Representative parent/study profile:

```shell
MPLCONFIGDIR=/private/tmp/gf-perf2-profile/mpl \
XDG_CACHE_HOME=/private/tmp/gf-perf2-profile/xdg \
.venv/bin/python -m cProfile -o profile.pstats -m simulator.optimize \
  --feedstock lunar_mare_low_ti \
  --profile data/optimize_profiles/lunar_mare_low_ti.yaml \
  --strategy random --fidelity internal-analytical \
  --parallel 4 --budget 4 --seed 19 \
  --pin campaigns.C0.temp_range_C --out RUN_DIR
```

The pin avoids a pre-existing sampler/profile incompatibility in which `campaigns.C0.temp_range_C` can be sampled as a scalar. It does not affect cache identity code. Four actual `evaluate()` calls ran through `study.run`, the warm engine worker pool, parent persistence, and final artifact generation. A second eight-evaluation/two-batch run used `profile_study_memory.py` with `tracemalloc` and GC callbacks. Scaling microbenchmarks used fixed seed `190719` and exact before/after implementations.

## Baseline wall-time breakdown

Parent `cProfile`: 31,125,479 calls, 12.657 s cumulative. Worker CPU is not included in the parent's function bodies; parent wait/call time captures its wall contribution.

| Area | Baseline evidence | Interpretation |
|---|---:|---|
| CLI + import | 0.454 s | One-time process setup. |
| Profile resolve/validation | 0.120 s load; 0.466 s validation path | Per-study setup, not per-eval dominant. |
| Sampler construction | 0.555 s | Mostly first SciPy/Sobol availability import. |
| Full `study.run` | 11.879 s | Excludes CLI/import. |
| Candidate evaluation phase | 10.691 s | Four real analytical evaluations and parent preflight. |
| Cache lookup preflight | 3.579 s / 4 calls | `_lookup_cached` rebuilds full eval inputs before a miss; punted to cache reissue. |
| Warm worker call | 6.079 s / 4 calls | Actual worker request wall time. |
| Pool start | 0.364 s / 4 workers | One bootstrap per worker in the batch. |
| Pool close/consume | 6.734 s | Includes waiting for worker completion and teardown. |
| Task construction | 0.377 s / 4 tasks | Almost entirely first `_default_feedstocks()` config load; recursive profile normalization itself was 0.00078 s. |
| Result-store writes | 0.0111 s / 4 | Parent-only SQLite writes; no measured lock contention. |
| Provenance payloads | 0.0150 s / 4 | Result serialization. |
| Tell journal writes | 0.00895 s / 4 | Objective/margin/result serialization and append. |
| Strategy snapshots | 0.00071 s / 2 | Small at N=4, but grows quadratically with study depth. |
| Final/non-eval study work | about 1.19 s | `study.run - _evaluate_candidates`; artifact generation and final scans included. |

Inter-eval behavior: the normal batch size equals `parallel`. `study.run` calls `evaluate_batch()` once per batch, and `evaluate_batch()` constructs and closes a fresh `EngineWorkerPool`. For budget `N` and parallelism `P`, this creates `ceil(N/P)` pools and normally gives each worker one evaluation. Cross-batch warm-backend reuse is therefore 0%; the eight-eval run created two four-worker pools. This is the largest remaining fixed-overhead opportunity, but it was not changed because it needs a dedicated state-isolation and parallelism review.

Parallel utilization: baseline process CPU was `(user + sys) / wall = (27.46 + 7.09) / 12.76 = 271%`, or 67.7% of four logical workers under cProfile. The eight-eval `tracemalloc` run recorded 30.465 s wall and 20.966 s parent CPU (68.8% of one parent core); child CPU is outside `process_time()`. No lock wait appeared in the hot functions: ResultStore and line-writer locks are taken by the parent after the evaluation barrier, so workers are idle during persistence rather than contending on the lock.

## Allocation and growth profile

The eight-evaluation/two-batch run reached 81,272,541 traced parent bytes peak and ended at 43,630,029 bytes. GC completed 1,068 generation-0, 97 generation-1, and 9 generation-2 collections under `tracemalloc`. Artifact growth from four to eight evaluations was:

| Artifact | N=4 | N=8 | Growth |
|---|---:|---:|---:|
| `study.events.jsonl` | 140,821 B / 8 rows | 281,678 B / 16 rows | Linear |
| `provenance.jsonl` | 99,560 B / 4 rows | 199,137 B / 8 rows | Linear |
| `strategy_state.jsonl` | 1,345 B / 2 rows | 4,185 B / 4 rows | 3.11x bytes for 2x evaluations |

`strategy_state.jsonl` is append-only and writes the entire accumulated strategy result list before and after every batch. The cumulative result rows visited and emitted are `Theta(N^2/P)`. Its output bytes are part of resume/replay compatibility, so changing it would violate this task's byte-identity requirement. It remains ranked work rather than a patch here.

## Patched wins

### 1. O(N^2/P) historical-ID rebuilds in every sampler

Affected `RandomStrategy`, Morris screen, Optuna TPE, Optuna NSGA-II, and staged strategy. Each `tell()` rebuilt a set from all prior results even though four strategies already maintain an authoritative result dictionary. Random now maintains an append-only `_recorded_ids` set; the others query their existing dictionaries. Validation order and error text are unchanged.

Fixed-width-four benchmark:

| Results | Prior historical visits | Before | After | Speedup |
|---:|---:|---:|---:|---:|
| 10,000 | 12,495,000 | 0.1096 s | 0.00151 s | 72.8x |
| 30,000 | 112,485,000 | 1.4518 s | 0.00470 s | 308.8x |

Determinism proof: the existing duplicate-batch and already-recorded errors passed across all five strategies; the 143-test strategy sweep passed. Fixed-seed e2e study outputs remained byte-identical.

### 2. Frontier-sized Pareto comparisons for complete objectives

The prior algorithm tested every candidate against the full study. The new exact incremental frontier preserves source order, duplicate points, mixed objective direction (scores are still pre-normalized), and strict dominance. It applies only when every score is numeric. Any `None` invokes the old algorithm unchanged because missing-objective dominance can be non-transitive.

Fixed-seed, uniform three-objective benchmark:

| Points | Prior comparisons | Front | Before | After | Speedup | Exact equality |
|---:|---:|---:|---:|---:|---:|---|
| 1,000 | 82,054 | 23 | 0.0404 s | 0.00337 s | 12.0x | yes |
| 5,000 | 622,401 | 22 | 0.3021 s | 0.0104 s | 29.2x | yes |

Worst case remains quadratic when nearly every point is non-dominated, but typical dominated studies scale with frontier size instead of total study size. All 124 objective tests, including nullable identity and stable ordering, passed.

### 3. Duplicate heavy-result strip removed

The main study loop created `light_scored`, then `_to_record()` stripped the already-light result again. An explicit private `already_light` flag now reuses the same object in that path while preserving defensive stripping for replay, certification, cache restoration, and direct tests. The representative profile reduced `_strip_heavy_result` calls from 8 to 4. Direct time saved at N=4 was 0.218 ms cumulative, so this is a small fixed-cost cleanup, not a headline wall-time claim.

## Determinism and verification

Before/after fixed-study deterministic artifacts were byte-identical:

- `pareto.json`: identical, SHA-256 `5bfad8e67fab90df59be74f237b1d8243240270577a23a195fce88e39443586d`.
- `leaderboard.csv`, `study.profile.yaml`, `search_provenance.json`: `cmp` identical.
- `study.manifest.json` differs only in `created_at` and the derived `study_id`; those fields are intentionally wall-time-specific and are excluded by the repository's deterministic study gate.

Tests, canonical `.venv`, all with `-n0`:

- Strategy sweep before the independent review: 143 passed.
- Objective sweep before the independent review: 124 passed.
- Post-review empty-score Pareto and cross-stage duplicate regressions: 4 passed locally and 4 passed independently.
- Focused e2e/determinism/pool/warm-runtime/mass-balance gate: 7 passed in 138.09 s.
- The focused gate includes exact same-seed `pareto.json` and `winner.recipe.yaml` comparison, canonical EvalSpec/result identity, serial-vs-pool deterministic view, fresh simulator/ledger per warm call, repeat evaluation identity, and cumulative transition mass closure.

No golden files changed. No cache-key computation, EvalSpec identity, result-store schema, worker count, batch width, or scheduling logic changed.

## Real-engine profiling status

The internal-analytical e2e study completed. Live real-engine profiling was attempted through the existing `evaluate_alphamelts_1h` profiler and a standalone ThermoEngine `evaluate()` harness:

1. AlphaMELTS subprocess: timed out at its normal 20 s hard limit.
2. AlphaMELTS profiling-only retry: timed out again after raising the local profiling limit to 120 s; no production constant was changed.
3. ThermoEngine on the low-Ti feedstock: correctly refused because that Stage-0 route requires subprocess isolation.
4. ThermoEngine on `lunar_highland`: backend started, but its equilibrium job exceeded the normal 3 s hard timeout and a profiling-only 120 s retry also timed out. No production timeout was changed.

Therefore no successful live real-engine evaluation profile is claimed. This is an environment/backend execution block, not evidence about the optimizer patches.

## Ranked remaining opportunities

1. **Study-scoped warm worker pool.** Reuse one pool across batches. Current cross-batch reuse is 0%, causing `ceil(N/P)` bootstraps/teardowns and parent barriers. Preserve worker count and ordering; prove fresh simulator/ledger state, byte identity, timeout recovery, and unchanged CPU utilization before landing.
2. **Quadratic strategy-state journal.** Full growing state is serialized twice per batch; staged mode also serializes topology zero twice (`active_strategy` plus the full staged tuple). A compatible checkpoint+delta format could remove `Theta(N^2/P)` bytes, but requires explicit resume-format/version approval and cannot be byte-neutral.
3. **Batch parent persistence.** ResultStore opens/transactions/closes once per result, and line writers open/flush/close per row. One parent transaction and open handles per batch should remove fixed overhead while retaining row order and schema.
4. **Memoize within-eval backend status/reason scans.** Run history is recursively scanned several times while creating canonical run references and authority fields. Measure on long real-engine traces once a backend completes.
5. **Cache objective/journal serialization payloads.** Tell events encode objectives and margins at the top level and inside `scored_result`. Reuse Python payload objects while emitting identical JSON.
6. **Signature reflection.** Pool and study evaluator dispatch call `inspect.signature()` repeatedly. Per-callable caching is low risk but was below the measured threshold in this profile.
7. **Worst-case Pareto frontier.** Complete-objective incremental pruning is still quadratic when most points are non-dominated. A dimension-specialized skyline algorithm could give stronger bounds, but needs exact stable duplicate/order tests.

## Punted to the cache reissue

`_lookup_cached()` spent 3.579 s across four candidates rebuilding the complete eval inputs/spec before each miss. A miss then rebuilds those inputs in the worker; staged prefix paths can add another derivation. This is the largest measured parent-side duplicate computation, but sharing it crosses EvalSpec/cache identity and the explicit cache-key lane guardrail. No code in that path was touched.

## Files changed

- `simulator/optimize/strategy/{random_strategy,screen,bayesian,genetic,staged}.py`
- `simulator/optimize/objective.py`
- `simulator/optimize/study.py`
- `tests/test_optimizer_objective.py`
- `tests/test_optimizer_staged.py`
- `docs-private/research/2026-07-19-perf-optimizer/profile_study_memory.py`
- `docs-private/research/2026-07-19-perf-optimizer/profile_real_engine.py`
- This report.

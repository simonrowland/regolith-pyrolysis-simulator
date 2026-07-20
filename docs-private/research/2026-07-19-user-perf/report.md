# Live web-run performance profile

Date: 2026-07-19

Scope: operator-visible `start_simulation` through `simulation_complete`, populated tick/summary panels, and durable run artifact

Status: staged candidate; no commit

## TL;DR

- Full C0→C6 lunar web run exercised 39 hours, 106 outgoing packets, 2.269 MB of Socket.IO payload, and an 815 KB artifact.
- Wall-advisor panel YAML was parsed three times per active tick; reuse cut its 39-tick unprofiled median from 5.475 s to 0.228 s (24.0x, 5.247 s reclaimed).
- Under cProfile, `_tick_payload()` fell from 14.498 s to 0.409 s (97.2%); exact panel bytes did not change.
- Per-hour wall-deposit cumulative summaries were O(hours²); the append-only fast path is now O(hours), 1.660 s → 0.0088 s at 4,000 synthetic hours (188.6x).
- Exact persisted artifact bytes and production-manager Socket.IO packet bytes are identical before/after.
- Runner goldens, web/run/artifact tests, determinism tests, and focused mass-balance tests pass; cache keys, EvalSpec, and result-store schema are untouched.

## What was profiled

The harness at `profile_live_web.py` drives the real Flask-Socket.IO handler and background loop, not the optimizer or a direct core shortcut:

1. `web/events.py::handle_start` with `lunar_mare_low_ti`, 1,000 kg, `internal-analytical`, `pyrolysis`, and speed 0.
2. The accelerated, canonical cross-surface campaign overrides from `tests/test_cross_surface_parity.py`, retaining the full observed chain `C0 → C0B → C2A_STAGED → C3_NA → C4 → C6`.
3. Each operator decision is submitted through `make_decision` using the surfaced recommendation.
4. The real per-hour path runs `drive_session(..., OPERATOR) → SimSession.advance() → core.step() → build_per_hour_summary() → _tick_payload() → Socket.IO emit`.
5. Completion runs `_completion_payload() → _persist_terminal() → _full_runner_payload() → build_run_artifact() → RunArtifactStore.save() → simulation_complete`.

The run completed 39 simulation hours and emitted:

| Surface | Count | Exact encoded size |
|---|---:|---:|
| `simulation_tick` | 39 | included below |
| `per_hour_summary` | 39 | included below |
| `campaign_complete_summary` | 6 | included below |
| `decision_required` | 3 | included below |
| status/completion | 19 | included below |
| All production-manager Socket.IO packets | 106 | 2,268,896 bytes |
| Persisted run artifact | 1 | 815,313 bytes |

Final paired profiles are `before-final.prof` / `before-final.json` and `after-final.prof` / `after-final.json`. The compact JSON summaries and reproducible harness are staged; the 2.3 MB raw `.prof` files remain local evidence. The before mode selects the exact pre-patch implementations inside the same harness/process setup; an initial baseline was also captured before editing.

## Wall-time breakdown

Inclusive timings below are the paired cProfile runs. cProfile substantially magnifies pure-Python costs, so attribution and call counts come from this table; the user-facing wall-advisor reclaim is separately reported from five unprofiled A/B repetitions.

| Boundary | Calls | Before | After | Notes |
|---|---:|---:|---:|---|
| Full start-to-complete profiled wall | 1 | 97.189 s | 76.481 s | 20.709 s observed reduction; core/provider variance prevents attributing all of it to these patches |
| `core.step()` | 39 | 79.084 s | 72.799 s | Unchanged code; 6.285 s sample variance |
| Melt redox/liquidus gate | 39 | 59.399 s | 57.568 s | Dominant cross-process/provider work; listed, not changed |
| Known refusal-state deepcopy | 39 | 10.353 s | 5.334 s | Unchanged t-363-class hotspot; large sample variance and separate landing |
| `_tick_payload()` | 39 | 14.498 s | 0.409 s | Wall-advisor parse removal; 14.089 s profiled reclaim, 97.2% |
| `build_per_hour_summary()` | 39 | 0.101 s | 0.055 s | Includes cumulative wall-deposit projection |
| Wall-deposit cumulative helper | 39 | 2.888 ms | 1.365 ms | Small at 39 h; quadratic growth removed |
| Recipe capture deepcopy | 40 | 0.139 s | 0.148 s | Unchanged |
| Socket emit, including manager work | 106 | 0.202 s | 0.113 s | Unchanged; sample variance |
| Actual Socket.IO packet encoding | 106 | 22.576 ms | 12.806 ms | Explicit production-manager encoder timer; unchanged bytes |
| Completion payload | 1 | 0.621 s | 0.157 s | Unchanged; sample variance |
| Full runner payload | 1 | 0.154 s | 0.030 s | Unchanged; sample variance |
| Terminal persistence | 1 | 0.574 s | 0.127 s | Includes artifact build/save |
| Artifact `save()` | 1 | 0.323 s | 0.075 s | Unchanged fsync/replace path; sample variance |

The profiled total moved 97.189 s → 76.481 s, but the unchanged physics core also moved by 6.285 s. I therefore do not claim the full 20.709 s as patch-caused. The deterministic attributable measurement is the repeated panel microbenchmark below.

## Patch 1: reuse the bundled wall-material revision

### Removed pattern

`_tick_payload()` builds the wall-risk panel every hour. For active vapor species, `wall_advisory_payload()` evaluates three wall zones, and each `advise_wall_materials()` call reparsed the same bundled `data/wall_materials.yaml`. The 39-hour run therefore performed 117 identical YAML parses in the panel path.

`simulator/wall_advisor.py` now caches only the bundled default revision after its first completed parse. The public/custom-path `load_wall_materials()` remains fresh on every call. The bundled cache key includes resolved path, inode, mtime-ns, ctime-ns, and size; a same-size/same-mtime custom-file rewrite regression proves the public loader retains old semantics.

### Measured result

Five unprofiled A/B repetitions, 39 identical active-species panel builds each:

| Metric | Before | After |
|---|---:|---:|
| Median total | 5.475124 s | 0.227823 s |
| Observed range | 5.206100–9.315227 s | 0.190421–0.333891 s |
| Median per tick | 140.39 ms | 5.84 ms |
| Median reclaimed | 5.247301 s/run | 134.55 ms/tick |
| Speedup | 1.0x | 24.03x |

The targeted panel payload fingerprint was identical: `9442c1effb2ab43b39de446628cc3cd7fa56bd8e629882770c16f3d8b810811f`.

## Patch 2: linear cumulative wall-deposit summaries

### Removed pattern

Every `build_per_hour_summary()` called `_wall_deposit_cumulative_kg_at_snapshot()`, which converted the entire growing snapshot list to a tuple and rescanned hours 1…N. Across a run, delta visits were `1 + 2 + … + N = O(N²)`.

The helper now keeps a weak, per-simulator presentation cache for the finalized append-only snapshot stream. The common live path adds only the new snapshot delta. Repeated or out-of-order requests retain the old semantics through cache hits or a full fallback rescan. The cache is outside scientific simulator state and cannot affect trajectory, rollback, artifact schema, or cache identity.

The live invariant is explicit: core appends a finalized snapshot before `SimSession` builds its summary; prior snapshot deltas are not mutated. Tests cover varied deltas and an out-of-order older-row request.

### Measured result and ratchet

At the representative 39-hour run this helper was only 2.888 ms profiled, so this is a long-run latency ratchet rather than the headline present-day win.

At 4,000 synthetic hourly summaries with two segment/species deltas:

| Metric | Before | After |
|---|---:|---:|
| Wall time | 1.660103 s | 0.008801 s |
| Speedup | 1.0x | 188.63x |
| Output SHA-256 | `754f9fb6ea8c850f6b49ae3a551f97f6f84144a6a3c122b919a0af93b04d3cd5` | identical |

`test_wall_deposit_cumulative_summary_cost_stays_linear_as_hours_grow` is the non-timing complexity ratchet: 64 hours must visit 64 delta mappings, and 128 hours may visit no more than `2 × small + 2`. The pre-patch rescan performs 2,080 and 8,256 visits respectively and fails the ratchet.

## Byte identity and golden neutrality

The harness pins run ID and start time, captures the actual persisted artifact file, and intercepts the production Socket.IO manager's real encoded packets. Flask-Socket.IO test-client validation re-encodes are explicitly excluded. The packet stream hash uses length framing only to preserve packet boundaries; each hashed payload is the exact encoded packet bytes.

| Exact surface | Before | After | Result |
|---|---|---|---|
| Persisted artifact file, 815,313 bytes | `a14502a4d700aadfddc8aa0bcce4c515c335516ba0dbe7abd8cb5cc98a405f0c` | same | byte-identical |
| 106 Socket.IO packets, 2,268,896 bytes | `c07ccf9c0942eda1deba7bda0d5b0afa42483d8e1b532af2c57dd029ffa278ae` | same | byte-identical |
| Decoded ordered event objects | `8256bb93093c5b14f02b26b2933bd69cfb76e2dcfeeac212290e5af9aded165c` | same | semantic cross-check |
| Canonical loaded artifact | `a784e249c1e8cae16c6648c9cdff9a668c5ddb44826d2a89c1cb97435fcf8169` | same | semantic cross-check |

No golden files changed. All three runner golden fixtures passed exactly.

## Verification

- `.venv/bin/python -m pytest -n0 ...` focused run/web/artifact/determinism/mass-balance suite: **82 passed**, 38 existing warnings, 603.21 s.
- Post-review focused cache/ratchet/wall-advisor suite: **28 passed**, 2.07 s.
- `.venv/bin/python -m pytest -n0 tests/test_runner_smoke.py::test_runner_golden_fixture_matches`: **3 passed**, 9 existing warnings, 39.65 s.
- Two post-review 39-hour full live-web profile runs completed and produced identical exact wire/artifact bytes.
- Independent review: initial findings folded; re-review **READY**, no remaining determinism, thread-safety, semantic-compatibility, or harness blockers.
- `git diff --check`: run at staging gate.

## Ranked remaining opportunities

1. **Melt-redox/liquidus provider work (largest, cross-run/cache boundary; list only).** `_establish_melt_redox_gate_authority_for_current_hour()` consumed 57.6–59.4 profiled seconds and waited on the provider/liquidus subprocess. This is the dominant live-run cost, but changing cross-run cache behavior or keys is explicitly out of scope during cache reissue.
2. **Known refusal rollback deepcopy (separate t-363 landing).** `_snapshot_terminal_refusal_hour_state()` consumed 5.3–10.4 profiled seconds and still contains the already-found cumulative-history deepcopy on this detached base. Do not duplicate the separate `7f7fe8d` patch here; that landing should add its missing complexity ratchet.
3. **C6 endpoint history recount.** `CampaignManager.check_endpoint()` recounts prior C6 at-target snapshots per C6 hour. It is another O(hours²) shape, but this 39-hour scenario spends too few C6 hours for a measured user win. A future fix needs hold/preheat semantic proof and a structural ratchet.
4. **Per-hour ledger double copy.** `atom_ledger.mol_by_account()` already returns detached nested balances; `web/events.py` deepcopies that result again before storing `per_hour_ledger`. Profile contribution is small here, but payload/account cardinality can make it relevant.
5. **Terminal zero-hour executor work.** `_full_runner_payload()` calls `RunExecutor.execute_session(hours=0)` to construct an envelope, which still snapshots ledger/melt and rebuilds cost-rollup state before `PyrolysisRun._build_output()` rebuilds terminal projections. Measured terminal cost is subsecond; simplify only with a direct-envelope contract test.
6. **Recipe-capture deepcopy.** The latest tick and per-hour summary are deepcopied on every hour even though only the latest capture survives. Current total is ~0.14 s profiled; ownership must be proven before removing copies.
7. **Serialization/persistence.** Exact encoding was 13–23 ms for 106 packets; artifact fsync/replace was 75–323 ms in these samples. Both are visible at completion but much smaller than physics/provider and panel construction.

## Scope guard receipt

- Cache key: untouched.
- EvalSpec: untouched.
- Results-store schema and artifact schema: untouched.
- Physics, mass-balance tolerances, and scientific state: untouched.
- Generated outputs/goldens: byte-identical.
- Requested state: staged only, no commit.

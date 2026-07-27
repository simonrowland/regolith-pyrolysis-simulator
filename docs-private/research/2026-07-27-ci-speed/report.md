# CI wall-clock compression

Date: 2026-07-27

Branch: `cispeed-w`

Evidence: 17 Studio-1 JUnit XML files in the main checkout at
`docs-private/research/2026-07-27-ci-speed/junits/`

## Verdict

The serial-tail premise is real, but late scheduling is not its primary cause
on the current 32-worker gate.

- `ci-ydisp-final.xml` contains 13,967.628 test-seconds.
- `magemin_fullrun_c` is the critical chain at 5,446.795 s (90.78 min).
- The work-conservation lower bound at 32 workers is
  `max(13,967.628 / 32, 5,446.795) = 5,446.795 s`.
- pytest-xdist 3.8's current `loadgroup` scheduler sorts scopes by item count
  by default. The current first scopes are `serial` (190 items), C (12), A
  (11), B/D (10), so every large chain already enters the first wave on a
  32-worker Studio gate.
- Starting C at time zero therefore still predicts 90.78 min. Only 1.322x
  co-tenancy inflation is needed for that chain alone to exceed 7,200 s.

The implemented duration ordering replaces the incidental item-count policy
with an explicit, reproducible duration policy. It protects low-worker and
future-roster gates, but the next 32-worker Studio gate should not be expected
to materially beat the present lower bound. The high-leverage follow-up is the
review-only fold design below: it removes an estimated 4,924.088 redundant
test-seconds while retaining every assertion.

## Measurement method

`scripts/generate_xdist_loadgroup_hints.py` parses every `<testcase>`, recognizes
an xdist group only when the final `@group` occurs after the final `]` (matching
xdist's parameter-ID guard), sums each group within each suite, then takes the
median per-suite chain total across input XML files.

This avoids treating parameter IDs such as `[Fe@1700K]` as loadgroups. The
checked table was regenerated from all 17 XML files and is byte-identical to
`tests/xdist_loadgroup_durations.json`.

### Cross-run critical-chain reconstruction

Every captured suite has C as its critical chain:

| JUnit | Total test-s | Critical group | Critical s |
|---|---:|---|---:|
| ci-b092-gate.xml | 12,799.8 | C | 4,727.3 |
| ci-b093-gate.xml | 11,396.6 | C | 4,403.9 |
| ci-baseline-227f667.xml | 12,564.5 | C | 4,702.5 |
| ci-perfhunt-g2.xml | 11,126.5 | C | 4,364.8 |
| ci-perfhunt-gate.xml | 13,431.8 | C | 4,627.3 |
| ci-s2-t231-g2.xml | 11,119.0 | C | 4,367.2 |
| ci-s2-t231-gate.xml | 12,203.5 | C | 4,516.2 |
| ci-s2-yielddisp-gate.xml | 12,207.3 | C | 4,663.4 |
| ci-t380p1-gate.xml | 11,241.5 | C | 4,430.3 |
| ci-t412-gate.xml | 11,200.5 | C | 4,381.2 |
| ci-t420-g2.xml | 11,128.8 | C | 4,350.8 |
| ci-t420-gate.xml | 12,719.1 | C | 4,669.6 |
| ci-t424a-g2.xml | 11,121.3 | C | 4,364.3 |
| ci-t424a-gate.xml | 13,938.8 | C | 4,641.1 |
| ci-t428-g2.xml | 11,139.5 | C | 4,371.2 |
| ci-volatiles-c1-gate.xml | 11,592.2 | C | 4,442.1 |
| ci-ydisp-final.xml | 13,967.6 | C | 5,446.8 |

### Representative chain table

| Group | Items | Representative s | 17-run median s | Min–max s |
|---|---:|---:|---:|---:|
| `magemin_fullrun_c` | 12 | 5,446.795 | 4,442.077 | 4,350.827–5,446.795 |
| `magemin_fullrun_a` | 11 | 3,199.041 | 2,760.002 | 2,717.845–3,199.041 |
| `magemin_fullrun_b` | 10 | 2,418.301 | 2,010.815 | 1,977.501–2,418.301 |
| `serial` | 190 | 936.976 | 452.359 | 418.028–936.976 |
| `magemin_fullrun_d` | 10 | 201.162 | 133.230 | 124.634–228.750 |
| `corpus_grid25` | 2 | 6.841 | 6.121 | 5.723–13.111 |
| `corpus_grid25_sio` | 2 | 4.433 | 4.082 | 3.637–8.547 |

C is dominated by five runs:

| Test family | Representative s |
|---|---:|
| Yield-root-cause Fe product | 1,391.656 |
| Yield-root-cause Al infeasibility | 1,362.761 |
| Electrolysis O2 bins, Mars | 1,089.073 |
| Electrolysis O2 bins, lunar | 1,080.598 |
| All other C members | 522.707 |

## Expected wall model

For representative total work `S = 13,967.628`, critical chain
`C = 5,446.795`, and 32 workers:

| Model | Bound / estimate |
|---|---:|
| Perfectly divisible work, ignoring chains | `S / 32 = 436.488 s` |
| Non-C work on remaining 31 workers | `(S - C) / 31 = 274.865 s` |
| Hard lower bound | `max(C, S / 32) = 5,446.795 s` |
| Current xdist 3.8 size-first first wave | approximately 5,446.795 s |
| New checked-duration first wave | approximately 5,446.795 s |

Before/after is equal for the present 32-worker roster because both policies
put every large chain in the initial wave. The new policy differs under low
worker counts or roster drift: it orders C, A, B, `serial`, D by measured
duration rather than `serial`, C, A, B/D by item count.

## Ordering implementation

- `_pytest_loadgroup_order.py` loads only the checked JSON hints, reconstructs
  xdist's sorted union group name from markers, and performs a stable descending
  duration sort. Unknown groups and ungrouped tests retain their mutual order.
- `conftest.py` exposes the hook suite-wide.
- `pyproject.toml` adds `--no-loadscope-reorder`; without it, xdist 3.8
  `LoadScopeScheduling.schedule()` replaces collection order with its
  group-item-count sort.
- `tests/xdist_loadgroup_durations.json` is static input. No live timing,
  cache, or previous local run influences collection.
- `scripts/generate_xdist_loadgroup_hints.py` deterministically regenerates the
  table from JUnit files or directories.

No test, assertion, timeout, fixture, simulation input, or golden changed.

Focused evidence:

- `python -m pytest tests/test_xdist_loadgroup_order.py -n 0 -q`:
  4 passed.
- `python -m pytest tests/test_xdist_loadgroup_order.py -n 4 --dist loadgroup -q`:
  4 passed.
- The subprocess harness defines unknown tests before known groups and proves
  deterministic collected order C → A → B → `serial` → unknown under `-n0`.
- Its four-worker barrier fails if any unknown group starts before all four
  flagged chains; it passes under `-n4 --dist loadgroup`.
- A real-tree collect-only probe passed with all ten D rows moved ahead of an
  earlier ungrouped CLI argument, proving the imported root hook is active.

Full-suite wall effect remains intentionally deferred to the controller's next
Studio gate.

## Group-membership audit

### A/B/C: historical serialization debt, now bounded concurrency

Commit `79bb941` created one global `magemin_fullrun` group because the old
machine-wide MAGEMin lock admitted only one call. Commit `a158691` proved that
each CLI call uses a private temporary directory and replaced the exclusive
lock with a bounded K-slot semaphore. Current code documents no shared mutable
state, defaults to `min(3, cores // 6)`, and allows an explicit slot count
(`simulator/melt_backend/magemin.py:171-191`). Slot acquisition itself is
machine-wide and fail-bounded (`simulator/melt_backend/magemin.py:195-236`);
the K-plus-one blocking contract is pinned at
`tests/test_magemin_backend.py:1849-1896`.

Commit `84c0a98` then greedily split the old chain into A/B/C to match K=3.
These groups are load balancing, not shared-fixture scopes:

- A: electrolysis mass-balance (2 params), evaporation-transition (3),
  evaporation-flux shadow parity (3), runner/executor (2), C6 static hold (1).
- B: yield root cause (1), condensation split parity (3), metallothermy (3),
  cumulative mass balance, cross-surface parity, staged bakeout.
- C: yield root cause (2), electrolysis O2-bin checks (2), condensation (3),
  SiO sweep smoke (5).

Recommendation: correctness no longer requires tests within A/B/C to share a
worker. If folds are deferred, trial more logical groups than semaphore slots;
the semaphore, not pytest grouping, is the concurrency authority. Do not land
that split without a co-tenancy gate because additional groups can consume
their per-test ceilings while waiting for a slot. The folds below are safer and
larger.

### D: timeout roster, not shared state

D is ten parameter rows of one internal-analytical profile test. Every row gets
a unique `tmp_path`, uses `monkeypatch`, and calls `evaluate(..., "stub", ...)`
(`tests/test_make_recipe_db_profile.py:601-643`). Commit `32a3a5c` created D
only to avoid bloating A after rows exceeded the default ceiling.

Recommendation: low priority. Parameter-level groups are safe by construction,
but D is only 133 s median and not near the wall-clock frontier.

### `serial`: multiple unrelated hazards fused into one chain

The 190-item group is a catch-all, not one shared fixture:

- SQLite busy-lock contention:
  `tests/test_grind_scratch_reduction.py:26-30`.
- heavy real-backend resource contention:
  `tests/test_north_star_baseline.py:32-35`.
- stateful redox/validation fixtures:
  `tests/test_sso_r_r20_state.py:37-38` and
  `tests/test_sso_r_validation_map.py:15-16`.
- Socket.IO/global web state:
  `tests/test_web_events_decision_pause.py:62-64` and
  `tests/test_web_functional_qa.py:30`.

Recommendation: retain serialization within each hazard class, but validate
four independent groups (`serial_sqlite`, `serial_native`, `serial_redox`,
`serial_web`) with a focused co-scheduling flake loop. The source establishes
different resources, not proof that cross-class concurrency is clean, so this
split should not land on static inspection alone. It is also non-critical:
`serial` is 936.976 s versus C at 5,446.795 s.

## Fold designs — review only, not applied

### Yield-root-cause trio

`_run_pyrolysis_track()` is a single canonical setup
(`tests/test_yield_root_cause.py:56-68`). Three tests call it with identical
defaults at lines 152, 488, and 504. Their representative costs are
1,420.616 + 1,391.656 + 1,362.761 = 4,175.033 s.

Design:

1. Add a module-scoped `full_pyrolysis_track_result` fixture that calls
   `_run_pyrolysis_track()` once.
2. Keep all three tests and their existing bodies; replace only each local
   `result = _run_pyrolysis_track()` with the injected fixture result.
3. Place all three assertions in B, where the retained run already lives.
4. Keep the targeted-FeO run at line 346 separate because its input differs.

The three consumers only read simulator/ledger state
(`tests/test_yield_root_cause.py:151-174,487-508`); none mutates the shared
result. Estimated redundant work removed: 2,754.417 s. Assertions remain
textually identical.

### Electrolysis full-run pair

Both parametrized tests build the same simulator, force the same liquidus
floor, enable the same C5 MRE target/cap, use the same decisions, and run the
same 5,000-step loop:

- mass-balance setup: `tests/chemistry/test_builtin_electrolysis_step_provider.py:1948-1974`;
- O2-bin setup: `tests/chemistry/test_builtin_electrolysis_step_provider.py:2068-2094`.

Their YAML dependencies are already module-scoped
(`tests/chemistry/conftest.py:28-40`).

Design:

1. Add a module-scoped fixture parametrized by the existing lunar/Mars tuples.
2. Move only common construction and stepping into that fixture; return
   `(feedstock_key, additives_kg, sim)`.
3. Keep both test functions. Replace their duplicated setup with fixture
   unpacking and retain every assertion exactly.
4. Put both consumers in A so one fixture instance exists per feedstock on one
   worker; leaving them in A and C would execute the module fixture twice.

Estimated redundant work removed: 1,080.598 + 1,089.073 = 2,169.671 s.

### Combined expected effect

Using representative durations and retaining the existing B/A runs:

| Quantity | Before | Fold design |
|---|---:|---:|
| Total test-seconds | 13,967.628 | about 9,043.540 |
| C chain | 5,446.795 | about 522.707 |
| Critical chain | C = 5,446.795 | A = 3,199.041 |
| Ideal 32-worker wall lower bound | 90.78 min | 53.32 min |

This is a model, not a measured claim. Apply only after review, with assertion
diff verification and focused fixture-isolation gates.

## Fold implementation and proof

The reviewed designs above are now applied.

### Implemented folds

- `tests/test_yield_root_cause.py` now creates the identical-default
  `_run_pyrolysis_track()` result once in a module-scoped fixture. The three
  consumers remain separate tests in `magemin_fullrun_b`; the targeted-FeO
  call remains separate and unchanged.
- `tests/chemistry/test_builtin_electrolysis_step_provider.py` now creates one
  full electrolysis run per existing lunar/Mars parameter tuple in a
  module-scoped fixture. Both consumers remain separate tests in
  `magemin_fullrun_a`, and their pre-existing parameter IDs are preserved.
- Each shared run gets a post-run fingerprint containing ledger closure,
  total mol by account, total mol by species, melt composition, and product
  totals. An autouse teardown guard rechecks the fingerprint after every
  consumer, so a future mutating consumer fails before it can silently
  contaminate its sibling.

### Predicted critical chain from checked hints

The checked hint table stores 17-suite median group totals. Median contributions
of the four run-bearing C items removed by the folds are:

| Removed C item | Median s |
|---|---:|
| Yield Fe-product consumer | 952.489 |
| Yield Al-infeasibility consumer | 949.531 |
| Electrolysis O2-bin lunar consumer | 1,037.199 |
| Electrolysis O2-bin Mars consumer | 1,015.309 |
| **Total removed from C** | **3,954.528** |

The resulting chain prediction is:

| Group | Checked hint s | Fold adjustment | Predicted s |
|---|---:|---:|---:|
| `magemin_fullrun_a` | 2,760.002 | retained electrolysis runs; assertion-only consumers move here | 2,760.002 |
| `magemin_fullrun_b` | 2,010.815 | retained yield run; assertion-only consumers move here | 2,010.815 |
| `magemin_fullrun_c` | 4,442.077 | -3,954.528 | 487.549 |
| `serial` | 452.359 | unchanged | 452.359 |
| `magemin_fullrun_d` | 133.230 | unchanged | 133.230 |

Predicted critical chain: `magemin_fullrun_a = 2,760.002 s = 46.00 min`.
The assertion-only consumers are negligible in this model; the executed JUnit
receipt below measured them at 0.029–0.036 s each.

### Executed receipts

- Collection: both edited modules, `-n 0`: 51 tests collected; the
  electrolysis consumers collect consecutively per feedstock, with their old
  IDs intact.
- Assertion diff audit: Python AST source-segment comparison against `HEAD`
  found all 22 existing assertions across the five consumers textually
  identical (same counts and source text).
- Filtered preflight:
  `-k 'not full_run and not pyrolysis_track and not targeted_feo_full_track'`:
  44 passed, 7 deselected in 2,103.77 s.
- Complete yield module, `-n 0`: 14 passed in 2,218.60 s (36:58), process
  exit 0. Fold timing: retained consumer 2,051.298 s; sibling consumers
  0.033 s and 0.033 s.
- Complete electrolysis module, `-n 0`: 37 passed in 1,641.74 s (27:21),
  process exit 0. Fold timing: lunar retained consumer 838.634 s and sibling
  0.036 s; Mars retained consumer 802.028 s and sibling 0.029 s.

The two complete module commands ran concurrently, but each module itself ran
serially under `-n 0`. No timeout, assertion, fingerprint, or golden failure
occurred.

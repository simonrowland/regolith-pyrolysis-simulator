# t-155 optimizer-pool findings

## TL;DR

The pool break was a test-fixture vocabulary failure, not process-pool serialization. Since t-155, `RecipePatch.recipe_id()` resolves and validates conditional patches; the pool tests' synthetic `test.value` patch is outside allowlist-v12 and correctly raises `RecipeValidationError`. The fixtures now carry their integer test signal on the real `furnace_max_T_C` knob, keeping production validation and every identity surface unchanged. The one steer-authorized timing flake widens the child-reap per-eval budget from 0.75 s to 2.0 s, still below the 5 s hang path.

## Root cause and fix

- First-bad mechanism: `RecipePatch.recipe_id()` -> `resolve_conditional_patch()` -> `validated()` -> `spec_for()` refuses the dummy `test.value` path.
- Fix scope: `tests/test_optimizer_pool.py` only. `_patch()` emits a schema-valid allowlist-v12 patch; `_patch_signal()` decodes the synthetic integer used by fake evaluators.
- Validation remains strict. No carve-out, fallback identity, `__reduce__`, pool thaw, production schema, or serializer change was made.
- Existing pool thaw logic already recursively converts immutable mapping carriers; no serialization defect reproduced after valid fixtures were used.
- Timing scope follows steer seq 1 from the prior worker: one of 19 nodes was the child-reap 0.755 s versus 0.750 s flake. The unrelated abort wall-budget widening left by that worker was reverted.

## Verification

- Representative real serial/pool parity: `1 passed in 2.27s`.
- Full pool file plus the red-ledger study node: `35 passed, 4 skipped in 26.84s` (`34` pool tests passed plus `1` study test passed). The four skips require subprocess spawning disallowed by this managed sandbox (`PermissionError: [Errno 1] Operation not permitted`).
- Focused t-155 recipe/EvalSpec/staged identity checks: `20 passed in 1.49s`.
- Full `tests/test_optimizer_{recipe,evalspec,staged}.py` sweep: `312 passed, 1 failed in 304.97s`. The sole failure is `test_no_pin_schema_is_golden_neutral_for_search_and_evalspec_hash`, the pre-existing `b-042` executable EvalSpec hash rebaseline assigned to the bugfix controller in red-ledger section 2.3. This patch touches no production or identity code; `recipe_id` stayed at its asserted t-155 value in that failing test.
- `git diff --check`: clean.

## Staged paths

- `tests/test_optimizer_pool.py`
- `docs-private/research/2026-07-16-t155-pool/findings.md`

BLOCKED: staged for controller commit

READY: docs-private/research/2026-07-16-t155-pool/findings.md

## Round 2: deterministic normal-child assertion

### TL;DR

The deterministic `normal-child-survived.txt` failure is a stale test-timing contract, not a pool-reaping defect. The fixture child writes after 1.0 s, but round 1 raised this node's worker timeout from 0.75 s to 2.0 s. The child therefore completes its side effect a full second before timeout teardown begins; no reaper can undo that write. The fixture child now sleeps 3.0 s, keeping it alive when the 2.0 s timeout reaper snapshots and kills descendants. No production pool code changed.

### Root cause derivation

- Original contract (`26ab7c01`): child writes at 1.0 s; worker timeout was 0.2 s. Later `24b9ad0` widened the timeout to 0.75 s. In both versions the child was alive when timeout teardown began, so file absence tested reaping.
- Round-1 fixture commit `e7975e6` widened only `per_eval_timeout_seconds` to 2.0 s for cold-spawn headroom. It left the child delay at 1.0 s. Timeline: child writes at about T+1.0 s; pool declares timeout and begins reaping at T+2.0 s; assertion observes the already-written file.
- Current teardown still reads the worker's tracked child-PID log, stops the worker/process group, snapshots descendants, and SIGKILLs tracked descendants. The unchanged direct termination regression `test_pool_process_termination_kills_child_process_group` passes 3/3 here.
- Correct test semantics require `child_delay > worker_timeout`, plus enough post-result observation time for an unreaped child to write. The new 3.0 s child delay exceeds the 2.0 s timeout; the existing 1.3 s post-result wait (after the replacement worker completes the next evaluation) exposes a missed reap.

### Round-2 verification

- Focused t-155 recipe/EvalSpec/staged identity selection: `35 passed, 278 deselected in 3.91s`.
- Full `tests/test_optimizer_pool.py -n0`: `34 passed, 4 skipped in 21.70s`; all executable nodes green. The same four process-pool nodes remain sandbox-skipped because this worker's actual runtime still raises `PermissionError: [Errno 1] Operation not permitted` while creating a `ProcessPoolExecutor`.
- Requested normal-child node, three solo attempts: all three reached the module fixture and sandbox-skipped for that same `PermissionError`; this runtime could not execute the provisioned path described by the controller.
- Direct child-reaper regression, three solo attempts: `1 passed` each (`2.09 s`, `2.06 s`, `2.02 s`).
- `git diff --check`: clean before findings append.

## Round-2 staged paths

- `tests/test_optimizer_pool.py`
- `docs-private/research/2026-07-16-t155-pool/findings.md`

BLOCKED: staged for controller commit

READY: docs-private/research/2026-07-16-t155-pool/findings.md

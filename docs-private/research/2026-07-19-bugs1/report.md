# Tier-1 bug batch: b-069 + b-072

Date: 2026-07-19

Baseline: `44a99b3`

Scope: reproduce first; change only confirmed reliability behavior.

## Verdicts

### b-069 — NOT-REPRODUCED

The claimed dependency-manifest divergence is absent on current code.

- `requirements.txt:16` and `pyproject.toml:32` both specify
  `petthermotools>=0.4.5,<0.5`.
- All six other runtime dependencies have identical normalized names and
  specifiers in `requirements.txt:1-6` and `pyproject.toml:21-31`.
- The three requirements-only test packages at `requirements.txt:31-33`
  exactly match `[project.optional-dependencies].dev` at
  `pyproject.toml:86-90`; this is an intentional combined installer surface,
  not a dependency mismatch.
- `requirements.txt:7-12` explicitly identifies
  `pyproject.toml [project.dependencies]` as the canonical runtime list and
  requires the requirements installer mirror to remain in lockstep.

No manifest edit was made. The null hypothesis was retained.

### b-072 — REAL+FIXED

The warm pool from `e463f28` did not supersede the reported process-churn
path. It persists a Python `WarmEngineWorker`, but every request handled at
`simulator/melt_backend/magemin.py:158-165` still reaches
`_call_magemin_subprocess`, whose `subprocess.run` launches the actual MAGEMin
executable once per solve. Liquidus/solidus searches call equilibrium
repeatedly, and independent simulator/provider workers had no machine-user
wide concurrency bound.

Fix:

- Added a per-user, cross-process `flock` gate around the actual MAGEMin
  executable launch. At most one MAGEMin child can now run concurrently for
  this user, including calls from separate warm workers/providers.
- Lock wait and executable runtime share the existing per-call deadline; the
  subprocess receives only the residual budget.
- Unlock and descriptor close are exception-safe. The existing
  `subprocess.run`, temporary working directory, worker timeout, process-group
  cleanup, and pipe teardown remain intact.
- Added a regression with two concurrent callers proving the second launch
  cannot enter `subprocess.run` until the first releases the slot. Tightened
  the timeout assertion to account for lock-wait budget consumption.

The change bounds the reliability concern only; MAGEMin inputs, parsing,
results, and bridge selection are unchanged. No compiled MAGEMin binary is
available in this checkout, so the macOS IOSurface exhaustion symptom itself
could not be stress-reproduced; the unbounded live launch topology was
reproduced from the current call graph.

## Verification

Focused post-fix gate:

```text
.venv/bin/python3 -m pytest tests/test_magemin_backend.py \
  -k "launches_are_serialized or subprocess_runs_in_fresh_temp_cwd or subprocess_timeout_clamped or reinitialize_closes" \
  -n0 -q
4 passed, 51 deselected
```

Syntax and diff hygiene:

```text
.venv/bin/python3 -m py_compile simulator/melt_backend/magemin.py tests/test_magemin_backend.py
git diff --check
PASS
```

Canonical filtered suite was run before and after the fix:

```text
.venv/bin/python3 -m pytest tests/ -k "magemin or backend or deps or requirements" -n0 -q
baseline: 17 failed, 539 passed, 16 skipped, 5685 deselected
post-fix: 17 failed, 540 passed, 16 skipped, 5685 deselected
```

The same 17 failures remain. They are baseline failures rather than
regressions from this patch: one AlphaMELTS mocked runner now receives a
separate `--version` probe; one MAGEMin warm-spawn test relies on a parent-only
monkeypatch; and 15 optimizer backend tests require an unavailable local
AlphaMELTS subprocess transport. Full logs are retained at
`/tmp/gf-bugs1-baseline-pytest.log` and `/tmp/gf-bugs1-post-pytest.log`.

## Review

Independent review found no P0/P1 issue. Its P2 lock-descriptor cleanup finding
and P3 multi-user temp-path concern were fixed before staging. The remaining
P3 observation is that the regression uses separate thread callers/file
descriptions rather than a spawned-process fixture; the production primitive
is nevertheless an OS advisory file lock specifically shared across
processes.

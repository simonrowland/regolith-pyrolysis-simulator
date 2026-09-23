# W3b independent CI signal — regolith-empirical

**Copy-ready target:** `ferry/ci/W3b-green-suite.md`

**Raw pytest log:** `/workspace/ferry-inbox/ci/W3b-green-pytest.log`

**Status:** INCOMPLETE — the xdist run reached 66%, then `gw0` died (“node down: Not properly terminated”). The controller terminated with an internal error while attempting to replace the worker because `/workspace/repos/wt/slot-08/.venv/bin/python` was missing.

- **Worktree:** `/workspace/repos/wt/slot-08` (detached; restored clean to requested tip after the run)
- **Command:** `.venv/bin/python -m pytest tests/ -q -n auto -p no:cacheprovider`
- **Tip SHA:** `2e9e17c3d138fdfba9269c493f82974a71153fa5`
- **Python:** 3.12.14
- **Started:** 2026-09-22 21:16:20 EDT
- **Ended:** 2026-09-22 22:36:25 EDT
- **Wall clock:** 4805 s (1:20:05)
- **Suite completed:** No (xdist worker loss/internal error)
- **Worktree changes:** clean; no product code fixed

## Counts

- **Passed:** 10,153
- **Failed:** 426
- **Skipped:** 1,964
- **Xfailed:** 2
- **Errors:** 30
- **Warnings:** 421

Classification legend: `ENV-CORPUS` = unavailable external corpus/provenance; `ENV-ENGINE` = expected missing optional engine/native artifact; `ENV-OPTIONAL` = undeclared-by-default optional dependency; `ENV-FIXTURE` = missing private fixture; `INFRA` = timeout/run infrastructure; `REAL` = observed product/data/test assertion mismatch.

**Classification totals (failed + error):** individual test classification is unavailable: `-q` xdist emitted only progress characters and the worker-loss internal error prevented pytest from printing failed/error test IDs. Run-level `INFRA` event: 1 (worker loss; not included in the 456 pytest outcome records). `ENV-CORPUS`, `ENV-ENGINE`, `ENV-OPTIONAL`, `ENV-FIXTURE`, and `REAL`: not determinable from this incomplete log.

## Failed tests (426)

Individual failed-test IDs were not emitted before the xdist worker loss; see the raw log for the progress stream and termination traceback.

## Error tests (30)

Individual error-test IDs were not emitted before the xdist worker loss; see the raw log for the termination traceback.

## Installation / infrastructure note

The run also recorded SciPy Sobol direction-number initialization warnings for a missing `_sobol_direction_numbers.npz`. The decisive termination was xdist `gw0` loss followed by failure to spawn a replacement worker because `.venv/bin/python` was absent. The worktree was restored to a clean detached checkout at the requested SHA after pytest ended.

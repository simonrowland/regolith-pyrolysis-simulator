# b-080 landing merge report

## Merge

- Candidate parent: `fef43d5e3f89cce0f802a3d4274edd5ca352dff1`
- Green parent: `be76d28ad8b73d0563ee34fddf452ec77b3d6de5`
- `git fetch origin work-v064-green` could not resolve `github.com`. The exact
  green commit was already present locally as `refs/remotes/origin/work-v064-green`
  and was merged as the second parent, without rebasing or rewriting history.

## Conflict resolutions

- `simulator/optimize/pool.py`: kept green's shared fidelity backend resolver,
  including canonical backend handling and proprietary-engine refusal. Pool
  tasks use that helper with the normalized request profile, preserving the
  candidate's task backend selection behavior.
- `simulator/optimize/strategy/bayesian.py`: kept green's shared
  `_tell_optuna_strategy` validation and result bookkeeping. Its tell path now
  calls the candidate's `_tell_optuna_trial` with executed-spec definitions,
  preserving b-080's withheld-objective pruning and score alignment for TPE.
- `simulator/optimize/strategy/genetic.py`: kept green's shared tell helper and
  the candidate's ask-time objective definitions and executed-spec scoring.
  NSGA-II therefore shares the validation path while retaining b-080's
  objective behavior.
- `simulator/optimize/study.py`: kept green's shared Pareto selection and
  ask-order publication. The selector now accepts the resolved profile and
  uses the candidate's executed-objective Pareto and rank helpers at explore,
  certify, replay, two-phase certification, and aborted-cache call sites.
  Green's ask-ordinal handling remains intact.
- `tests/suite_shape_guard.py`: retained both the b-080 campaign-heavy roster
  entries and green's battery-heavy roster entries.
- `tests/test_optimizer_evaluate.py`: combined the candidate's fidelity and
  campaign imports with green's engine refusal imports; both sides' tests
  remain present.
- `tests/test_optimizer_study.py`: retained the 25-second CPU budget and
  reconciled the wall timeout at 150 seconds for the timeout process-pool
  behavior. Kept green's measured lifespan provenance assertions and the
  candidate's threshold-status assertion.

## Static checks

Ruff is unavailable on the host and on rpp. As directed, Ruff F821 was replaced
with these checks:

- `py_compile` passed for all 28 Python files touched by the b-080 fix series,
  using `PYTHONPYCACHEPREFIX` under `$TMPDIR`.
- One import sweep passed for all 15 touched `simulator/` and `engines/`
  modules.
- The merged-diff name sweep confirmed the replaced helper/import names were
  removed on their respective parent sides and left no stale references.

## Remote gate

- Request ID: `8193b3914155482da4e3dd51dfbac46c`
- Requested these 12 test files in one `regolith-focused-tests` gate request:
  `tests/test_feedstock_normalization_consumers.py`,
  `tests/test_make_recipe_db_profile.py`, `tests/test_optimizer_evalspec.py`,
  `tests/test_optimizer_evaluate.py`,
  `tests/test_optimizer_mixed_objective_rank.py`,
  `tests/test_optimizer_objective.py`,
  `tests/test_optimizer_profile_smoke_labels.py`,
  `tests/test_optimizer_profiles.py`, `tests/test_optimizer_reoptimize.py`,
  `tests/test_optimizer_staged.py`, `tests/test_optimizer_strategy_tpe.py`,
  and `tests/test_optimizer_study.py`.
- The worker gate did not return test results. The controller reported the
  rpp peer-pin mismatch infrastructure issue and directed this worker to stop
  waiting; the controller will gate the merge directly.
- Gate counts: unavailable; no pass, failure, or failing node IDs were
  reported, so there were no IDs to compare against green. No local pytest
  fallback was run.

# Recipe Optimizer User Guide

The recipe optimizer searches recipe settings for one feedstock/profile pair against the profile objectives. Use it when you want a ranked set of candidate recipes instead of one manually tuned run.

It is an operator tool for recipe search. It is not a chemistry-authority switch. Backend authority still follows the simulator backend rules, and current checked-in optimizer profiles keep every fidelity choice on the `internal-analytical` backend (the builtin analytical model; legacy name `stub`).

Engineers who need runtime internals should read [Eval runtime architecture](architecture-eval-runtime-2026-06.md). This guide stays operator-facing.

## When To Use It

Use the optimizer when you need to:

- Compare recipes for a known feedstock profile.
- Search for better objective tradeoffs, such as more stored oxygen, more metal product, lower energy, or shorter duration.
- Produce auditable artifacts: `leaderboard.csv`, `pareto.json`, `search_provenance.json`, `winner.recipe.yaml`, `provenance.jsonl`, and the optimizer cache database.

Do not treat an `internal-analytical`-backed (legacy `stub`) result as a real process prediction. Internal-analytical-backed studies are useful for UI, cache, profile, and workflow checks.

## Run From The Web

Open the app, then go to `/optimizer`.

The page has two surfaces:

- The **Feedstock/Profile Winners** table reads stored optimizer results from the configured runs directory.
- The **Launch Optimizer Job** form submits a disk-backed CLI job and polls job status.

Launch fields:

- **Feedstock**: feedstock id from `data/feedstocks.yaml`.
- **Profile**: optimizer profile id from `data/optimize_profiles/*.yaml`; the web form rejects a profile that does not belong to the selected feedstock.
- **Strategy**: one of `random`, `screen`, `bayes`, `nsga2`, `staged`.
- **Fidelity**: one of `stub`, `fast`, `high`, `auto`.
- **Budget**: positive integer evaluation count.
- **Parallel**: positive integer worker count; web submission is capped by `OPTIMIZER_JOB_PARALLEL_CAP` or the default cap of `4`.
- **Seed**: non-negative integer strategy seed.

The web job runner launches the same CLI used below. Job detail pages show status, feedstock, profile, strategy, fidelity, budget, parallel count, seed, PID, timestamps, queue position, log tail, and result links when available.

## Run From The CLI

Canonical form:

```bash
python -m simulator.optimize \
  --feedstock lunar_mare_low_ti \
  --profile data/optimize_profiles/lunar_mare_low_ti.yaml \
  --strategy staged \
  --fidelity high \
  --budget 24 \
  --parallel 1 \
  --out runs/optimizer-lunar-mare-low-ti \
  --seed 0
```

Actual flags:

```text
--feedstock FEEDSTOCK
--profile PROFILE
--strategy {bayes,nsga2,random,screen,staged}
--fidelity {stub,fast,high,auto}
--parallel PARALLEL
--budget BUDGET
--out OUT
--seed SEED
--warm-start-from PRIOR_RUN_OR_ARTIFACT
```

`--feedstock`, `--strategy`, `--fidelity`, and `--budget` are required. `--profile` accepts the built-in profile name, a feedstock profile id, or a YAML profile path. `--warm-start-from` accepts a prior run directory, `cache.sqlite`, or `pareto.json`; omitted means no store warm-start. If `--out` is omitted, the study writes under `runs/<timestamp>`.

On success, the CLI prints:

```text
out_dir: <path>
winner: <candidate_id>
strategy: <input_strategy>-><strategy_class>
```

## Choose Inputs

### Feedstock And Profile

Pick a feedstock first, then a profile for that feedstock. Public feedstock background lives in [Feedstocks](feedstocks.md).

Optimizer profiles are deny-by-default. A valid profile declares:

- `profile_id`
- `profile_schema_version`
- `feedstock`
- `objectives`
- `constraints`
- `run`
- `fidelities`
- `seed_recipes`

The web form lists the available feedstock/profile pairs from `data/optimize_profiles/*.yaml`.

### Strategy

Accepted strategy names:

| Strategy | Code path |
| --- | --- |
| `random` | `RandomStrategy` |
| `screen` | `MorrisScreenStrategy` |
| `bayes` | `OptunaTPEStrategy` |
| `nsga2` | `OptunaNSGA2Strategy` |
| `staged` | `StagedStrategy` |

The web form defaults to `staged`. Use a small budget for smoke checks. Increase budget when you need a broader search.

### Budget And Parallelism

`budget` is the number of candidates to evaluate. Larger budgets cost more time and usually produce a better search.

`parallel` controls concurrent worker evaluations. The CLI accepts any positive integer. The web launcher applies a cap, default `4`.

### Fidelity

Current CLI and web fidelity flags are `stub`, `fast`, `high`, and `auto`.

Operator meaning:

| Flag | Honest interpretation |
| --- | --- |
| `stub` | Legacy input alias for fast smoke-path evaluation on the `internal-analytical` model; serialized backend identity is `internal-analytical`. Useful for checking profiles, job wiring, artifacts, and UI. Not a real chemistry result. |
| `fast` | Fast tier label. The study still checks the EvalSpec cache before running a fresh evaluation. In the checked-in profiles, this tier is also `internal-analytical`-backed (legacy `stub`). |
| `high` | High tier label. Intended for real-backend work when a profile/backend config points there. In the checked-in profiles, this tier is also `internal-analytical`-backed (legacy `stub`). |
| `auto` | Valid fidelity label. In the checked-in profiles, this tier is also `internal-analytical`-backed (legacy `stub`). |

There is no literal CLI flag named `cached-real` or `real-alphamelts` in the current code. Cached reuse is controlled by the EvalSpec cache, and every fidelity can hit that cache. A cached result is only as honest as the backend that originally produced it.

Real AlphaMELTS-backed work, when configured, is slow and backend-dependent. If the backend cannot resolve, treat the run as failed or diagnostic, not as a lower-fidelity success.

## Read Results

### Leaderboard

The web leaderboard is the **Feedstock/Profile Winners** table. It shows:

- rank
- feedstock/profile
- objectives
- feasible yes/no
- study date
- fidelity
- backend badge
- version badge
- provenance
- completeness
- coating
- products
- detail link

The CLI writes `leaderboard.csv`, `pareto.json`, `study.events.jsonl`, and optional
`search_provenance.json` in the output directory. If there is a feasible Pareto
winner, it also writes `winner.recipe.yaml`.

`study.events.jsonl` is the primary replay record: replay re-runs strategy
`ask()` calls and feeds the recorded `tell()` results back into the strategy.
Seed reruns are only a determinism check or a way to extend a study, not the
save/replay mechanism.

Winner selection is deterministic: choose the feasible Pareto point with the best primary profile objective, then compare remaining objectives in declared order, then `cache_key`, then `candidate_id`.

### EvalSpec

Stored results are keyed by `EvalSpec`. The important fields are:

- `recipe_id`
- `feedstock_recipe_digest`
- `feedstock_id`
- `profile_id`
- `fidelity`
- `code_version`
- `data_digests`
- `campaign`
- `backend_name`
- `mass_kg`
- `hours`
- `stage_ids`
- `stage_patch`
- `c5_enabled`
- `mre_max_voltage_V`
- `mre_target_species`

The cache key is a SHA-256 digest of canonical EvalSpec JSON. Same EvalSpec, same code `VERSION`, and same data digests mean the result can be reused.

### Backend Badge

The backend badge displays active backend and backend status, for example:

```text
InternalAnalyticalBackend / unavailable
```

Internal-analytical results — active backend `InternalAnalyticalBackend`, or backend status `diagnostic_stub` — are not authoritative. A real-backend result should show a non-analytical active backend and a backend status that is not `unavailable`.

### Stale Version Badge

The version badge compares the stored result's code version with the current `VERSION` file.

- `current`: stored result matches current code version.
- `stale`: stored result came from an older code version.
- `unknown`: stored result did not record a version.

Use stale results as historical data, not as fresh optimizer evidence.

## Fail-Loud Behavior

The optimizer rejects invalid inputs instead of guessing:

- Unknown `feedstock_id`.
- Unknown `profile_id`.
- Profile not valid for the selected feedstock.
- Unknown strategy.
- Unknown fidelity.
- Non-positive budget or parallel count.
- Negative seed.
- Invalid profile schema.
- Unknown feedstock in the simulator data bundle.

The CLI exits with an error for validation, profile, filesystem, and study errors.

If no feasible Pareto winner exists, the study writes empty artifacts and raises:

```text
no feasible candidates; winner.recipe.yaml not written
```

If a requested real backend is unavailable, do not reinterpret that as a successful `internal-analytical` (`stub`) run. Fix backend configuration or choose an explicitly `internal-analytical`-backed (legacy `stub`) study.

## Related Docs

- [Feedstocks](feedstocks.md)
- [Recipe Playbook](recipe-playbook.md)
- [Output Interpretation](output-interpretation.md)
- [Running simulations from the shell](running-from-shell.md)
- [Eval runtime architecture](architecture-eval-runtime-2026-06.md)

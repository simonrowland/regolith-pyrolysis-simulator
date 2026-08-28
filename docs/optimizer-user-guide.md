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

The page has four surfaces:

- **Optimizer Results** — the **Feedstock/Profile Winners** table, which reads stored optimizer results from the configured runs directory.
- **Launch Optimizer Job** — submits a disk-backed CLI job and polls job status.
- **Import Study Bundle** — ingests a study bundle produced elsewhere (for example on a Studio box).
- **Imported Studies** — lists ingested bundles with their status and verification state.

Launch fields:

- **Feedstock**: feedstock id from `data/feedstocks.yaml`.
- **Profile**: optimizer profile id from `data/optimize_profiles/*.yaml`; the web form rejects a profile that does not belong to the selected feedstock.
- **Strategy**: one of `random`, `screen`, `bayes`, `nsga2`, `staged`. The form preselects `staged`.
- **Fidelity**: one of `internal-analytical`, `fast`, `high`, `auto`. The form preselects `high`.
- **Budget**: positive integer evaluation count; the form starts at `24`.
- **Parallel**: positive integer worker count; web submission is capped by `OPTIMIZER_JOB_PARALLEL_CAP` or the default cap of `4`.
- **Seed**: non-negative integer strategy seed.
- **MRE catalog**: an MRE preset id from the setpoints preset catalog, submitted as `mre_preset_id`. This is the surface for the "do we need MRE at all?" question — it selects which MRE species set the study is allowed to use.

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

`python -m simulator.optimize.cli` is the same entry point and takes the same flags.

Actual flags (run `--help` for the authoritative list):

```text
--feedstock FEEDSTOCK
--profile PROFILE
--strategy {bayes,nsga2,random,screen,staged}
--fidelity {internal-analytical,fast,high,auto}
--parallel PARALLEL
--budget BUDGET
--out OUT
--seed SEED
--warm-start-from PRIOR_RUN_OR_ARTIFACT
--per-eval-timeout-seconds SECONDS
--pin DOTTED.PATH
--constrained-max
--furnace-temp-cap-C DEGC
--cycle-time-cap-h HOURS
--two-phase-certify
--certify-top-k K
--certify
--source-store SOURCE_STORE
--cache-key CACHE_KEY
```

`--feedstock`, `--fidelity`, and `--budget` are always required; `--strategy` is required
unless `--certify` is set. `--profile` accepts the built-in profile name, a feedstock
profile id, or a YAML profile path. `--warm-start-from` accepts a prior run directory,
`cache.sqlite`, or `pareto.json`; omitted means no store warm-start. If `--out` is
omitted, the study writes under `runs/<timestamp>`.

### Searching under a hardware ceiling

`--constrained-max` switches the study to yield-under-ceilings mode: wall coating becomes
a furnace-lifespan **cost** rather than a hard gate. Pair it with the ceilings you actually
have — `--furnace-temp-cap-C` activates the `furnace_temperature` gate at that maximum, and
`--cycle-time-cap-h` activates the `cycle_time` gate at that maximum run hour. `--pin
DOTTED.PATH` (repeatable) freezes one optimizer knob at its loaded default so the search
cannot move it.

`--per-eval-timeout-seconds` sets the per-candidate wall-clock timeout (default 2700 s;
also settable via `REGOLITH_OPTIMIZER_EVAL_TIMEOUT_SECONDS`).

`--two-phase-certify` runs a coarse explore pass and then exact-certifies the top-K
(`--certify-top-k`). `--certify` re-certifies a single stored result with an exact
live fill, addressed by `--source-store` (a `cache.sqlite`) plus `--cache-key`.

On success, the CLI prints:

```text
out_dir: <path>
winner: <candidate_id>
strategy: <input_strategy>-><strategy_class>
```

### Advanced Furnace-Lifetime Cost

The recipe `cost_parameters` block exposes `furnace_lifetime_cost_multiplier`
and `min_fouling_penalty`. Their defaults are `500.0` and `1.0`: the multiplier
may be relaxed to zero for edge-case exploration, but the penalty floor must
remain strictly positive. The optimizer values the furnace at roughly 500
typical batch process costs and amortizes that value over the qualified coating
model's worst-segment `campaigns_to_resinter` lifetime. A clean/infinite-lifetime recipe
with exactly zero deposition pays zero, while any positive qualified fouling
pays at least one typical batch cost. The multiplier is an edge-case exploration
lever: lower it only to surface qualified fouling recipes when an extraction goal
is otherwise intractable. The one-batch floor remains, so zero fouling keeps its
bright-line priority even under a relaxed multiplier. Advanced overrides participate
in the evaluation cache identity. This is an economic trade-off, not a hard gate.

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

Current CLI and web fidelity values are `internal-analytical`, `fast`, `high`, and `auto`.
`stub` is still accepted as **input** on the CLI — argparse canonicalizes it through
`canonical_backend_name` before validating — but `internal-analytical` is the canonical
spelling, the one `--help` prints, the only one the web form offers, and the one that
lands in stored results.

Operator meaning:

| Flag | Honest interpretation |
| --- | --- |
| `internal-analytical` | Fast smoke-path evaluation on the built-in analytical model. Legacy input alias: `stub`. Useful for checking profiles, job wiring, artifacts, and UI. Not a real chemistry result. |
| `fast` | Fast tier label. The study still checks the EvalSpec cache before running a fresh evaluation. In the checked-in profiles, this tier is also `internal-analytical`-backed. |
| `high` | High tier label. Intended for real-backend work when a profile/backend config points there. In the checked-in profiles, this tier is also `internal-analytical`-backed. |
| `auto` | Valid fidelity label. In the checked-in profiles, this tier is also `internal-analytical`-backed. |

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
- cache tier
- backend badge
- version badge
- corpus
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

Stored results are keyed by `EvalSpec`. It carries far more fields than are listed here
(`simulator.optimize.evaluate.EvalSpec` is the authority); the ones an operator normally
reasons about are:

- `recipe_id`
- `feedstock_id`
- `profile_id`
- `fidelity`
- `campaign`
- `backend_name`
- `mass_kg`
- `hours`
- `track`
- `additives_kg`
- `c5_enabled`
- `mre_max_voltage_V`
- `mre_target_species`
- `runtime_campaign_overrides`
- `chemistry_kernel`
- `cost_parameters`

The cache key is a SHA-256 digest of canonical EvalSpec JSON
(`simulator.optimize.evalspec.canonical_evalspec_json`). Two things about that payload
matter, because they are the opposite of the intuitive answer:

- **`corpus_version` is the sole data-corpus version lever.** Bump it to make the
  optimizer rerun.
- **`code_version` and the data/provider fingerprints are NOT key material.** They are
  carried as provenance only and deliberately excluded, so that editing a comment in a
  data YAML — or shipping a new code version — does not miss cache. Do not expect a
  release bump to invalidate stored optimizer results; it will not.

`physics_constraints` (feasibility thresholds and active gates) *is* first-class key
material, because a threshold change changes verdicts and must not be served from cache.
Owner-ratified default `cost_parameters` serialize away, so runs at the defaults share one
cache identity.

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

The badge is advisory, not enforcement: code version is provenance and not cache-key
material (see EvalSpec above), so a `stale` result will still be served from cache. Judging
whether an old result is still trustworthy is the operator's call, which is exactly why the
badge is shown.

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

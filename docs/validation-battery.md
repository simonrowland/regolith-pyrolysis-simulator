# Validation Battery

The validation battery scores every thermodynamic engine in the simulator against published
experimental measurements, rail by rail. The engines are scored *against* the literature, never
tuned *to* it. A disagreement is reported as a residual and investigated, and a band is never
widened to make a row pass. The residuals and their error bars are the simulator's headline
accuracy.

Every engine is asked about the same observations under the same conditions, so the battery is
also the cross-engine comparison. The error budget built on it is in
[Model Limitations](model-limitations.md).

## Pipeline

```text
data/literature/extracts/*.yaml   experimental measurements, one file per source
data/literature/compilations/     assessed reference tables (JANAF and similar)
        |  scripts/battery_migrate.py   (simulator/battery/migrate.py)
        v
observation store, schema v2.1    works, experiments, observations
        |  scripts/battery_score.py     (simulator/battery/score.py)
        v
data/battery/residuals.jsonl      one residual per observation point and engine
        |  scripts/battery_hero_plot.py
        v
score report, headline summary, hero charts and the battery leaderboard
```

| Path | Holds |
|---|---|
| `data/literature/works/` | One record per published work with its experiments, plus the alias registry `ALIASES.yaml` |
| `data/literature/extracts-v2/` | Observations lifted from each extract (the extract itself is never rewritten) |
| `data/literature/observations-v2/` | Observations from compilations and measurement sidecars |
| `data/battery/migration-report.md` | Store counts; its header gives the current size of the store |
| `data/battery/migration-queue.yaml` | Values still to be checked against the source page, grouped by typed reason |
| `data/battery/pins.yaml` | Pinned bands: an independent baseline the scorer never re-centres |
| `data/battery/decision-band-ratchet.yaml` | Uncertainty bands derived from source scatter |
| `data/battery/score-report.md`, `score-summary.json` | Scorer outputs |

Each scoring run writes `data/battery/residuals.jsonl` afresh; it is generated, not committed.
The migrator never invents a value: an unprinted pressure, phase or method stays unknown, and
absence is never stored as a measured zero.

## Running it

Apply and check the engine patches first, so the scores describe the engines the simulator loads:
`patches/scripts/enginepatch.sh apply`, then `patches/scripts/enginepatch.sh verify`.

```bash
.venv/bin/python scripts/battery_migrate.py                              # 1. lift sources into the store
.venv/bin/python data/literature/build_index.py --write-store-summary    # 2. refresh the store summary
.venv/bin/python scripts/battery_validate_store.py                       # 3. validate the store
.venv/bin/python scripts/battery_score.py                                # 4. score every engine
```

- **Migrate** takes `--dry-run` (no writes) and `--root` (another checkout). It prints its counts
  and `hard_issues`, and exits 1 whenever `hard_issues` is non-zero, so compare the printed count
  with the committed migration report.
- **Validate** writes `data/battery/store-validation.json`. It exits 0 when the record has no
  forbidden hard-issue shapes, 1 when it has some, and 2 when the store cannot be loaded.
- **Score** runs the full engine set by default and accepts:

| Flag | Effect |
|---|---|
| `--engines a,b` | Score only these engine tokens |
| `--rail RAIL` | Score one rail |
| `--work ID` | Score one work or source id |
| `--limit N` | Stop after N observations |
| `--measured-headline` | Score measured-evidence candidates only and write the per-engine headline |
| `--report-only` | Rebuild `score-report.md` from an existing `residuals.jsonl` |
| `--update-band-ratchet` | Accept the current source-derived band minima into the ratchet file |
| `--write-pins` | Regenerate `pins.yaml` from the pin sources, never from residuals |

For quick diagnostics, `scripts/battery_residuals_report.py funnel` counts a residuals file by
status, rail, engine and refusal reason, and `rowdiff BASE TIP` lists the rows that moved.

## Rails

The rails are the `Rail` enum in `simulator/battery/enums.py`; `rail_for_quantity` assigns each row.

| Rail | What it compares |
|---|---|
| `vapour` | Saturation, partial and reference vapour pressures of every species except SiO and SiO₂ |
| `melt_activity` | Melt activities, activity coefficients and interaction parameters; equilibrium phase compositions; sulfide and chloride capacities; distribution ratios of elements other than Fe, Cr, Mn, Ti and P |
| `thermochemistry` | Formation enthalpy and Gibbs energy, log K, Cp, S, H − H₂₉₈, fusion and vaporisation enthalpies, transition temperatures |
| `SiO_evolution` | SiO and SiO₂ vapour pressures, and SiO evaporation coefficients, rates and condensate compositions |
| `pyrolysis_yield` | Yield fractions, O₂ yield, mass-loss fractions (single and versus temperature), evolved-gas yields |
| `wall_deposition` | Wall-deposit mass |
| `redox` | Fe³⁺/Fe²⁺ and other valence ratios; distribution ratios of Fe, Cr, Mn, Ti and P |
| `alkali_shuttle` | Na and K evaporation coefficients, rates and condensate compositions |
| `residue_composition` | Composition of the evaporation residue |

A quantity outside every rail, such as an ion-intensity ratio, is counted under a typed
no-headline-rail reason rather than borrowed onto a neighbouring rail.

## Engines

The scored set is `SCORE_ENGINE_SET` in `simulator/battery/score.py`:

| Token | Engine | Composition it takes |
|---|---|---|
| `internal-analytical` | The simulator's built-in Ellingham/Antoine model | One homogeneous liquid |
| `openimcc` | [OpenIMCC](https://github.com/simonrowland/openimcc) associate melt-activity and gas model | One homogeneous liquid |
| `vaporock` | VapoRock vapour–melt equilibrium | One homogeneous liquid |
| `alphamelts` | alphaMELTS | Resolves the liquid from a bulk composition |
| `thermoengine` | MELTS through ThermoEngine | Resolves the liquid from a bulk composition |
| `magemin` | MAGEMin Gibbs-energy minimiser | Resolves the liquid from a bulk composition |

Every engine is asked about every candidate row; a row it does not model returns a typed
refusal, which the coverage chart counts. Fe³⁺/Fe²⁺, for example, comes from internal-analytical,
OpenIMCC, alphaMELTS and ThermoEngine, and VapoRock and MAGEMin decline it with
`engine_does_not_report_fe_redox`. [Melt Backends](melt-backends.md) describes each adapter.

### How a comparison is shared

- **Same rows.** Each observation is scored once per engine, under one residual key: reference,
  temperature, quantity, rail and engine.
- **Same oxygen.** `simulator/battery/fo2_ladder.py` resolves one fO₂ per row and gives it to
  every engine. In general it takes the first of: the printed fO₂; the printed Fe³⁺/ΣFe through
  the Kress and Carmichael (1991) inverse; the experiment's controlled atmosphere or buffer; and
  the melt's intrinsic fO₂, flagged as borrowed. A row that resolves none is refused for every
  engine alike. Rows where an engine computes its own redox state are labelled `engine_intrinsic`
  and kept apart from `shared_ladder` rows.
- **Same population.** A pairwise comparison uses only the shared rows: observations on which
  both engines returned exactly one numeric residual. A refusal on either side drops the row
  from that pair, so no engine is credited or penalised for rows the other did not answer.

### How each engine runs

The scorer opens each engine once per run and reuses its live handle, replacing a handle that
has died. Native engines run in persistent worker processes from `simulator/engine_pool.py`
(`WarmEngineWorker`, `EngineWorkerPool`), with a start-up deadline and a per-call deadline.
A call that times out or crashes becomes an `attempted_unavailable` refusal on that row, with
`engine_timeout` or `engine_crash` in its detail; the worker is replaced and scoring continues.
Internal-analytical rows run in a child process with a CPU budget. Engine results are computed
during the run, not read from a stored result cache, so every residual reflects the engines as
installed.

## Reading a residual

Residuals are typed records (`simulator/battery/records.py`, serialized as described in
[Battery residual serialization](battery/residual-schema.md)). With C the engine prediction and
R the measurement, `compute_metric` uses one of three metrics:

| Metric | Value | Used for |
|---|---|---|
| dex | log₁₀(C / R), both positive | Vapour pressures, activities, activity coefficients, Fe³⁺/Fe²⁺, distribution ratios, viscosity, electrical conductivity |
| relative | (C − R) / \|R\|, R ≠ 0 | Yield fractions, O₂ yield, evaporation and mass-loss rates, wall-deposit mass, density |
| absolute | C − R, in the quantity's unit | Gibbs energies and enthalpies, Cp, S, log K, transition temperatures, evaporation coefficients, mass-loss fractions, wt% compositions |

A positive dex residual means the engine over-predicts. A pair outside the metric's domain, such
as a non-positive value under dex, is a `metric_domain` refusal, never a zero.

**Band.** Each residual is judged against a decision band, chosen in this order:

1. **Printed by the source.** The row's printed uncertainty is converted into the residual's
   metric. A one-sigma value is multiplied by 1.96 to give a two-sided 95 % band; a two-sigma
   value or 95 % interval is used as printed; a range contributes its half-width. An uncertainty
   with no stated confidence level is used as printed and labelled `printed_unstated`.
2. **Derived from source scatter.** Where two or more sources measure the same aligned
   condition, σ = 1.4826 × the median absolute deviation of their values about the median, and
   the band is 1.96 σ. `decision-band-ratchet.yaml` keeps the tightest accepted band, so these
   bands only tighten.
3. **Pooled KEMS band.** Knudsen-effusion partial pressures with neither band above take the
   pooled measured KEMS band.

The status is then `match` (|residual| ≤ band), `mismatch`, or `no_band` when no band applies.
A `no_band` row keeps its number and counts in n and RMS; the match rate uses banded rows only.

**Refusal.** A `refused` residual carries a typed `RefusalReason` and a detail payload, never a
number. Missing and invalid inputs refuse. A refusal is a diagnostic: the report's refusal census
counts every reason, marking where data or certification is still needed.

**Notice flags.** Out-of-domain physics is predicted and flagged rather than refused. An engine
answer outside its certified band, or outside its activity-coefficient domain, is priced with an
`out_of_certified_band` or `out_of_gamma_domain` notice that names the reason and the band.
Flagged rows form named strata. They appear in the all-numeric tier and in the hero charts and
stay out of the measured headline. The report's notice backlog counts open flags by kind; each
kind is a certification project that new measurements close.

**Measured headline.** A residual is `score_eligible` only when every conjunct in
`SCORE_ELIGIBLE_CONJUNCTS` holds: a finite numeric comparison in a valid metric domain, measured
reference evidence, an admitted source whose review permits use, passing validity gates, the
same identity on both sides, independent lineage with complete ancestry, an allowed prediction
authority, and no blocking qualification or flagged stratum. The `exclusions` field names every
conjunct a row fails. An engine is never validated against a table it consumes: comparisons with
assessed compilations sit in their own compilation tier and are never added to the measured tier.

## The score report

`data/battery/score-report.md` carries two co-equal headlines per rail and engine: the
**all-numeric tier**, over every priced residual, certified and flagged, and the **measured tier**,
with n, n inside band, RMS dex, signed and absolute median dex, band width and RMS/band. It also
reports the candidate census, flagged strata, condition hulls, the compilation tier, the refusal
census, admission, the notice backlog and pin failures. Dex statistics use dex residuals only.
`score-summary.json` holds the headline in machine-readable form.

## Hero charts

`scripts/battery_hero_plot.py` renders the charts from a residuals file (`.jsonl`, `.jsonl.gz`
or a battery `.tgz` archive):

```bash
.venv/bin/python scripts/battery_hero_plot.py \
  --input data/battery/residuals.jsonl \
  --output-dir out/hero \
  --history-path out/hero/hero-history.jsonl \
  --simulator-commit "$(git rev-parse --short HEAD)" \
  --openimcc-commit <OpenIMCC revision used by the score run>
```

The charts cover the vapour, melt activity, Fe³⁺/Fe²⁺ (`redox`), SiO evolution and residue
composition rails, using dex residuals; flagged rows are included and counted. Error is
log₁₀(predicted / measured), closer to zero is better, and the green band marks ±0.5 dex.

| Chart | Question | How to read it |
|---|---|---|
| A · Scorecard | Which engine is closest to measurement? | One panel per rail, one row per pairing of OpenIMCC with VapoRock, ThermoEngine, alphaMELTS or MAGEMin. Circle and square are each engine's RMS on their shared rows; the solid pair is T ≥ 1700 K and the faded pair T < 1700 K. |
| B · Temperature | How does the error change with temperature? | One panel per engine: signed error against temperature, binned median with its interquartile band, and triangles for points beyond ±3 dex. The title gives scored rows, RMS and extrapolated count. |
| C · Components | Which engine is closest for each species or oxide? | Vapour by species and melt activity by oxide: OpenIMCC against each other engine on shared rows. |
| D · Fingerprint | Where does the rail meet the target? | Per engine, 100 K × 0.05 SiO₂ cells coloured by median absolute error: good ≤ 0.5 dex, okay ≤ 1.0 dex, needs work above. Cells with fewer than three rows stay blank. |
| E · Coverage | Can each engine predict the benchmark rows? | Rows per engine split into scored, input missing, outside domain, not an output of this engine, and other refusals. |
| F · Progress | Is shared-row RMS improving? | Shared-row RMS per engine at each landed store in the history file. A render not yet in the history appears as a candidate (X); diamonds give held-out RMS where n ≥ 5. |
| G · Exposure | Are the scored rows exposed or held out? | In-sample rows that informed diagnosis against held-out rows, added after the exposure cutoff and never used in diagnosis. Needs `--exposure-census`, `--exposed-sources` and `--pre-cutoff-observation-ids`. |

The run also writes `battery-hero.html`, with every chart and table and an explorer whose window
RMS over a chosen temperature and SiO₂ range is exact. Each chart's numbers are in `tables/*.csv`,
and `methods.md` records the scorer revision, the OpenIMCC pin and these conventions. A new
history path starts empty; `--append-history --landing-sha <sha>` adds the current run to it.

## Leaderboard

`simulator.battery.leaderboard` ranks individual numeric predictions or groups of predictions from
the scorer's residual ledger. It reads `.tgz` score archives and residual JSONL directly; it never
runs the scorer or creates a second store. Flagged numeric predictions remain in the ranking and
carry their notice kinds. Refused rows never rank and are counted in the output header.

By default, the leaderboard selects species that can be formed from the nonzero oxide components
in the `lunar_mare_low_ti` feedstock. The `all` feedstock setting skips that species filter. Without
a run report, temperature is not filtered unless you provide `--tmin-K`, `--tmax-K`, or both. The
output header says `Temperature: not filtered.` when no temperature bounds apply. With `--run`, the
report's traversed temperature range and pO₂ range are used; temperature includes the report's
±100 K margin, and the evaporated-moles column comes from the run report. Explicit temperature
bounds further restrict that run range. The report also limits species and engines by rail using its
recorded activity providers and backends.

Rows are ranked by absolute residual: `worst` puts the largest first and `best` the smallest first.
Groups are ranked by RMS in the selected order. Rankings are independent within each unit block;
`dex` comes first by default, followed by other units alphabetically. Each markdown block names its
unit. Use `--unit` when paging a specific unit.

```bash
# Worst 10 lunar predictions, then the next 10
python -m simulator.battery.leaderboard --archive scores.tgz \
  --feedstock lunar_mare_low_ti --level row --order worst --limit 10 --format md
python -m simulator.battery.leaderboard --archive scores.tgz \
  --feedstock lunar_mare_low_ti --level row --order worst --limit 10 --offset 10 --format md

# Best 10 OpenIMCC predictions
python -m simulator.battery.leaderboard --archive scores.tgz \
  --feedstock lunar_mare_low_ti --engine openimcc --level row --order best --limit 10

# Worst groups across all feedstocks
python -m simulator.battery.leaderboard --archive scores.tgz \
  --feedstock all --level group --order worst --limit 10

# Hero-recipe priorities from the run report; markdown output also writes sibling JSON
python -m simulator.battery.leaderboard --archive scores.tgz --run hero-run.json \
  --feedstock lunar_mare_low_ti --level group --limit 10 --out out/hero-priorities.md
```

Use `--format md`, `json`, or `csv`; `--out` writes the selected format to a file. Markdown file
output also writes the same page as sibling JSON. `--limit` defaults to 10 and `--offset` defaults
to 0. The output's `total` counts all ranked rows or groups before paging. Filters include
`--engine` (repeat it to select multiple engines), `--rail`, `--species`, and `--unit`. CSV begins
with a metadata comment containing `total`, `limit`, `offset`, and `refused_count` before its header.

OpenIMCC can call the CLI on a score archive, or import the library function when it already has
residual rows in memory:

```python
from pathlib import Path

from simulator.battery.leaderboard import leaderboard
from simulator.battery.score import load_residuals_jsonl

rows = load_residuals_jsonl(Path("data/battery/residuals.jsonl"))
page = leaderboard(rows, engine="openimcc", level="row", order="best", limit=10)
```

### JSON schema: `battery-leaderboard.v1`

JSON output is a stable, versioned interface. The top-level object has exactly these keys:

| Key | Meaning |
|---|---|
| `schema` | Schema identifier, `battery-leaderboard.v1`. The refusal-count meaning below is part of v1; bump the version if keys or meanings change. |
| `filters` | Applied filters and the ranking policy. |
| `total` | Number of ranked rows/groups before `offset` and `limit`. Refusals are excluded. |
| `limit` | Maximum number of returned rows. |
| `offset` | Number of ranked rows skipped before returning this page. |
| `rows` | Page of row-level predictions or group summaries. |

The `filters` object has these keys:

| Key | Meaning |
|---|---|
| `feedstock` | Feedstock id, or `all`. |
| `engine` | Selected engine ids as a list, or `null` for all engines. |
| `level` | `row` or `group`. |
| `order` | `worst` or `best`. |
| `rail` | Selected rail, or `null`. |
| `species` | Selected formula, or `null`. |
| `unit` | Selected unit block, or `null` when unit blocks are ranked separately. |
| `temperature_K` | `{min, max, source}` temperature bounds, or `null` if none are declared. |
| `run_envelope` | Report-derived hero context, or `null` without `--run`; its keys are listed below. |
| `refused_count` | Total refused rows in the input, counted before feedstock, engine, rail, species, unit, temperature, or run-envelope filters. All refusals are excluded from ranking; this is not a filter-specific count. Refusals have no unit and remain counted with `--unit`. |
| `rank_policy` | Text describing the within-unit ranking and unit-block order. |

Markdown output lists input-wide refusal totals by engine and rail, using `unknown` for a
blank rail. These buckets are printed regardless of active filters; they include known
matches and non-matches and do not show counts matching the selected filters. For example,
with five refusals and `--engine openimcc --rail vapour --species Fe`, the summary can show
`magemin/unknown=1; openimcc/unknown=1; openimcc/vapour=2; other/activity=1`. The
`openimcc/vapour=2` bucket includes refusals that do not match the selected species, and
`other/activity=1` matches neither the selected engine nor rail.

When `--rail` or `--species` is active, a separate Markdown line reports refusals missing
the rail value needed by `--rail` and/or the species value needed by `--species`. It is a
missing-value attribution summary, not a filter-match count. Refusals with known values
that do not match a filter remain in the input-wide `refused_count` but are not
unattributed.

When `run_envelope` is present, it contains `feedstock_composition_class`,
`feedstock_class_source`, `backend`, `engines_by_role`, `temperature_K` (`min`, `max`, and
`margin_K`), `pO2_bar` (`min` and `max`), `species_by_rail`, `evaporated_mol_by_species`, and
`evaporated_mol_method`. `engines_by_role` maps each role to the providers named by the report;
`species_by_rail` maps rail names to formulas; and `evaporated_mol_by_species` maps formulas to
estimated moles. `feedstock_class_source` identifies the report field or catalog entry used for
the class. Markdown output also prints the feedstock class, pO₂ range, engine roles, and species by
rail from this metadata.

Each `rows` item has keys depending on `filters.level`.

| Row-level key | Meaning |
|---|---|
| `rank` | Rank within its unit block, starting at 1. |
| `observation_id` | Reference observation id used by the scorer. |
| `source` | Source id for the reference observation. |
| `rail` | Battery rail. |
| `species` | Chemical formula. |
| `engine` | Candidate engine id. |
| `T_K` | Observation temperature in K, or `null` if unavailable. |
| `observed` | Reconstructed reference value, or `null` if not recoverable from the residual. |
| `predicted` | Candidate prediction value, or `null` if unavailable. |
| `residual` | Signed scored residual. |
| `unit` | Residual unit; `dex` for dex metrics and `<unit> (<operation>)` otherwise. |
| `flags` | Notice kinds attached to the numeric prediction. |

| Group-level key | Meaning |
|---|---|
| `rank` | Rank within its unit block, starting at 1. |
| `rail`, `species`, `engine`, `unit` | Group identity; groups never combine different units or operations. |
| `n` | Number of numeric predictions, including flagged predictions. |
| `flagged_n` | Number of flagged predictions included in `n` and RMS. |
| `rms` | RMS of all `n` signed residuals in the group's unit. |
| `rms_unflagged_only` | RMS over unflagged predictions, or `null` if none exist. |
| `median` | Median signed residual. |
| `bias` | Mean signed residual. |
| `hero_evaporated_mol` | Moles of the species evaporated by `--run`, or `null` without a run report. |
| `worst_observation_ids` | Up to three unique observation ids ordered by absolute residual, largest first. |

Changing the schema keys requires a version bump and a corresponding key-pinning test.

## Adding a source

1. Write `data/literature/extracts/<source-id>.yaml` to the extract schema in
   [`data/literature/extracts/SCHEMA.md`](../data/literature/extracts/SCHEMA.md): one file per
   source, with the citation, the printed values and conditions, and fidelity samples.
2. Check it with `.venv/bin/python tools/validate_literature_extracts.py
   data/literature/extracts/<source-id>.yaml`.
3. Put assessed or smoothed tables in `data/literature/compilations/` instead; its
   [README](../data/literature/compilations/README.md) gives the rule. Measurements that a
   compilation reprints belong in an extract credited to the original source.
4. Rebuild the literature index with `.venv/bin/python data/literature/build_index.py`, then run
   the four steps under [Running it](#running-it).

See [CONTRIBUTING](../CONTRIBUTING.md) for the pull-request process and the
[Citation Policy](citation-policy.md) for how values are sourced.

## Current scope and upcoming work

- Liquidus-temperature prediction is an upcoming feature. Printed liquidus rows are stored now
  and refused as `quantity_not_predicted` until it ships.
- Evaporation coefficients and rates have rails for SiO and the alkalis. For other species, such
  as Mg and Zn, they are counted under the reason `non_alkali_kinetic` rather than a rail.
- The hero collection charts five rails in dex. The remaining rails, and every absolute or
  relative metric, are reported in `score-report.md`.

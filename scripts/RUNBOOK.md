# Scripts runbook

## AlphaMELTS expedited grid grinder

The daemon uses worker processes only for AlphaMELTS calls. One parent SQLite
writer commits batches under WAL mode; harvest readers do not pause the daemon,
and a crash can lose at most the current uncommitted batch.

### Local smoke

From the repository root:

```bash
.venv/bin/python scripts/grid_pregrind.py \
  --prepare-only \
  --limit 12 \
  --db /tmp/grind-smoke.db

.venv/bin/python scripts/grid_pregrind.py \
  --drain-only \
  --workers 2 \
  --limit 6 \
  --db /tmp/grind-smoke.db
```

The first command is the only path that generates the grid or imports Kress91
partitioning. The second opens that prepared queue without generating points,
upserting a batch, or materializing keys. Run the drain command again; it must
report the selected rows as `resume_skipped` with `pending=0`. Drain-only reads
the stored batch params and Kress91 provenance from `batches.params_json` and
records host, worker, and engine-probe facts in `last_drain_run` metadata.
Inspect counts directly with:

```bash
sqlite3 /tmp/grind-smoke.db \
  'select status, count(*) from alphamelts_outputs group by status order by status;'
```

`--limit` is a smoke-only queue/rank cap: prepare still computes and prints the
full budget, but materializes the first 12 one-time-shuffled ranks; drain then
selects ranks below 6. Production omits it. The fixed backbone uses 10%-equivalent
eight-major simplex points, three Cr2O3 levels, fixed MnO/NiO/CoO/P2O5, eight
intended fO2 partition levels, one actual 1.0 bar pressure point, and the 50/25
C temperature grid. Batch labels must retain the `v2-kress-composition`
revision so these rows cannot be confused with the frozen-adapter smoke batch.
Iron-free backbone compositions use one provenance level because Kress
partitioning cannot change their engine input; retaining eight copies would
misstate non-identity provenance as eight canonical states. The budget prints
both the gross Cartesian count and the smaller deduplicated queue count before
assigning the latter's ordinals modulo 3.

Current default budget (`seed=20260710`, 2 s/point estimate):

| Budget term | Count |
|---|---:|
| Major simplex points | 149 |
| Cr2O3-expanded compositions | 447 |
| Iron-bearing / iron-free compositions | 330 / 117 |
| Temperature points | 27 |
| Intended Kress fO2 levels | 8 |
| Actual pressure points | 1 |
| Gross Cartesian points | 96,552 |
| Iron-free nonidentity duplicates removed | 22,113 |
| Canonical queue points | 74,439 |
| Shards 0 / 1 / 2 (`ordinal % 3`) | 24,813 / 24,813 / 24,813 |

The printed budget is authoritative if defaults change.

The v2 grid realizes fO2 in composition space. For every composition,
temperature, and intended log10(fO2/bar), the grinder calls the simulator's
public `melt_mol_fractions_for_kress91()` and `kress91_split()` functions, then
re-partitions total iron into explicit Fe2O3 and FeO moles. AlphaMELTS receives
that full-precision explicit couple and no fO2 constraint. `composition_mol`
and `composition_mol_by_account` are the exact values sent to the frozen
adapter and are canonical-key inputs; the raw payload additionally preserves
the exact generated `.melts` file and engine streams. `intended_fO2_log` is a
provenance-only `grid_keys` column, excluded from `canonical_vector` and
`expedited_key`. The Kress implementation/version, coefficient identifiers,
target grid, and fixed pressure are recorded in each v2 batch's `params_json`.
The adapter-required finite `fO2_log` field is an invariant bookkeeping
placeholder and is not serialized into `.melts`; do not interpret it as the
intended fO2 or an engine constraint.

Pressure is intentionally a single honest input at 1.0 bar. Condensed-phase
equilibria are insensitive to the sub-bar pressures of interest at this grid's
resolution; the vapor side belongs to VapoRock, and 1.0 bar matches the
simulator's own AlphaMELTS liquidus-search usage. The hub records
`pressure_bar=1.0` as the actual canonical input. Do not reintroduce sub-bar
AlphaMELTS points through this grinder.

Existing refusal rows from the pre-v2 smoke batch, including IDs below
1,000,000,000, are valid negative-cache evidence and remain untouched. The
compatible schema migration only adds nullable provenance columns; it never
rewrites or deletes those rows.

## Battery residual diagnostics

`scripts/battery_residuals_report.py` reads a `residuals.jsonl` ledger one
line at a time (the ledger is written by the battery scorer and is not
tracked in git). It does not score the store. A temperature band is half-open
in kelvin, `t-min <= T < t-max`, using the identity temperature stored on the
residual key. Rows with no identity temperature are outside every band.

```bash
python scripts/battery_residuals_report.py funnel data/battery/residuals.jsonl \
  --engine openimcc --t-min 1000 --t-max 1500

python scripts/battery_residuals_report.py rowdiff \
  data/battery/residuals-base.jsonl data/battery/residuals-tip.jsonl

python scripts/battery_residuals_report.py band data/battery/residuals.jsonl \
  --t-min 1000 --t-max 1500
```

`funnel` counts rows by status, rail, engine, refusal reason, and the stored
refusal-detail reason. `rowdiff` reports status transitions, numeric value
changes, keys that moved between refused and scored, and keys added or
removed. It indexes accepted base rows as key to a status code and float
value, streams the tip ledger once, and writes each change as that row is
read. A filter does not hide a repeated key: every keyed line is checked,
and the engine and temperature filters apply only to counted and compared
rows. A tip key absent from the base index is kept in a set of those keys,
including a key the filter does not report. Accepted keys left only in the
base ledger are written after that stream. A repeated key names the key and
the line. A repeated base key is found while the base index is built, so no
report is written. A repeated tip key leaves any earlier detail lines,
writes an invalid trailer, and the command exits nonzero.
`band` stores numeric values in a float64 array and reports per-engine
status counts and the median and root-mean-square inside the band.
The median and root-mean-square are the score report's statistics.

## JUnit outcome compare

`scripts/junit_compare.py` reports new failures, new passes, and other
outcome changes between two junit XML files. Failure text is compared after
redacting volatile paths, worker ids, addresses, and timings. The command
exits 0 whether or not the tip has new failures.

```bash
python scripts/junit_compare.py base-junit.xml tip-junit.xml
```

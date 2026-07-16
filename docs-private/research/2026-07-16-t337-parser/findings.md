# t-337 — alphaMELTS species-tokenization fix and epoch-2 retry pass

## TL;DR

- Root cause: the subprocess activity extractor treated every line containing
  `activit` as the start of a possible two-line activity table. In repeated
  alphaMELTS solve output, an inline `Activity of H2O = ...` line can precede
  the next solve block. The five-line look-ahead then accepted
  `<> Stable liquid solid assemblage achieved.` as the table header and the
  following liquid-composition row as its numeric values. That produced the
  bogus species labels `<>`, `Stable`, `liquid`, `solid`, `assemblage`, and
  `achieved.`.
- Fix: table look-ahead now runs only after an explicit colon-terminated
  activity-table heading such as `Liquid activities:`. Inline activity
  assignments retain their dedicated parser. Status lines are never treated
  as species/table headers.
- Dictionary: added the native subprocess phase labels `ortho-oxide` and
  `alkali-feldspar`. These remain distinct from ThermoEngine's canonical
  `orthooxide` label.
- Recovery population: 4,569 epoch-2 `cache_v2_unknown_species` rows; live SQL
  must determine the exact distinct-row count for `cache_v2_unknown_phase`
  (the supplied 314 + 50 figures are label incidences); 15,689
  `subprocess_died` rows. Retry into epoch 3 on the same database, preserving
  epoch 2.

## Shape characterization and regression

The defect is in
`simulator/melt_backend/alphamelts.py::_extract_subprocess_activity_mapping`,
not `engines/alphamelts/parser.py`. The failing shape is multi-step/repeated
stdout where the end of one solve and the next stable-assemblage block are
adjacent:

```text
Activity of H2O = 0  Melt fraction = 0.921889
<> Stable liquid solid assemblage achieved.
Initial alphaMELTS calculation at: P 1.000000 (bars), T 1200.000000 (C)
liquid:    SiO2 TiO2 Al2O3 Fe2O3 Cr2O3 FeO
90.3451 g 46.49 2.21 16.60 0.00 0.00 11.71
```

Before the fix, `_activity_table_after()` accepted the status sentence as six
column labels and paired it with the numeric liquid row. The regression passes
that exact offending sequence through `_parse_single_point_stdout()` and
requires `activity_coefficients == {"H2O": 0.0}`. The legitimate explicit
table form remains covered independently:

```text
Liquid activities:
SiO2_Liq Na K Fe
0.42 0.08 0.03 0.25
```

The multi-block shape is consistent with the locally retained alphaMELTS
capture at
`docs-private/research/2026-07-12-epoch2-sigabrt-rootcause/repro/phase-mass-1000000502.json`,
whose stdout contains repeated stable-assemblage blocks, inline H2O activity,
and native phase rows.

## Phase-label provenance

Both additions are alphaMELTS subprocess vocabulary, not aliases invented by
the cache writer:

- `ortho-oxide`: the retained alphaMELTS 2.3.1 / MELTSv1.0.2 capture above
  contains `Adding the solid phase ortho-oxide`, an `ortho-oxide:` assemblage
  row, and an `ortho-oxide1` row in `Phase_main_tbl.txt`.
- `alkali-feldspar`: the bundled alphaMELTS 2.3.1 executable vocabulary contains
  the literal phase label `alkali-feldspar`; the epoch-2 corpus emitted it in
  the phase-bearing writer fields (50 supplied label incidences).

The cache-v2 manifest now states this source explicitly: alphaMELTS 2.3.1
MELTSv1.0.2 `Phase_main_tbl.txt`/assemblage labels from captured subprocess
output and bundled executable vocabulary, union the existing ThermoEngine
`MELTSmodel.get_phase_names()` vocabulary.

## Studio3 retry pass — specify only; do not launch in t-337

Run from the synchronized Studio checkout. These commands target the existing
v2 database and preserve the epoch-2 rows by writing retries to epoch 3.

```bash
cd "$HOME/repos/regolith-pyrolysis-simulator"
DB="$HOME/grind-runs/grind-alphamelts-fullcapture-v2.db"

.venv/bin/python scripts/grid_pregrind.py \
  --backend subprocess \
  --db "$DB" \
  --model MELTSv1.0.2 \
  --timeout-s 90 \
  --engine-epoch 3 \
  --retry-source-epoch 2 \
  --retry-failed cache_v2_unknown_species \
  --retry-limit 1000000 \
  --workers 8

.venv/bin/python scripts/grid_pregrind.py \
  --backend subprocess \
  --db "$DB" \
  --model MELTSv1.0.2 \
  --timeout-s 90 \
  --engine-epoch 3 \
  --retry-source-epoch 2 \
  --retry-failed cache_v2_unknown_phase \
  --retry-limit 1000000 \
  --workers 8

.venv/bin/python scripts/grid_pregrind.py \
  --backend subprocess \
  --db "$DB" \
  --model MELTSv1.0.2 \
  --timeout-s 90 \
  --engine-epoch 3 \
  --retry-source-epoch 2 \
  --retry-failed subprocess_died \
  --retry-limit 1000000 \
  --workers 8
```

Semantics confirmed from the CLI/writer contracts:

- `--retry-failed` selects `alphamelts_outputs.refusal_reason` at the exact
  `--retry-source-epoch`, ordered by shuffle rank/id.
- `--engine-epoch` must differ from the source epoch; equal epochs are refused
  so cache rows cannot be overwritten. Here source 2 -> target 3 is mandatory.
- The default retry limit is 12, so each production command needs the explicit
  high cap above.
- Existing target-epoch rows are counted as done and skipped. Commands are
  resumable on interruption.
- Each command prints `retry_selection` and a target-epoch `retry_histogram` as
  its receipt.
- Eight workers is deliberate for all passes and especially the 15,689
  `subprocess_died` points, which correlate with the original 20-worker load.

Expected recovery: all 4,569 species failures and the genuine-label phase
failures should produce epoch-3 rows without repeating their dictionary reason.
The reduced-concurrency death pass should recover most or all 15,689 points;
any residual `subprocess_died` remains an explicit typed failure for another
retry or root-cause triage.

## Final-corpus acceptance check

Run this query against the same v2 database after all three commands:

```bash
sqlite3 "$DB" <<'SQL'
.headers on
.mode column
WITH wanted(reason) AS (
  VALUES
    ('cache_v2_unknown_species'),
    ('cache_v2_unknown_phase'),
    ('subprocess_died')
),
source AS (
  SELECT o.expedited_key, o.refusal_reason
  FROM alphamelts_outputs AS o
  JOIN wanted AS w ON w.reason = o.refusal_reason
  WHERE o.engine_epoch = 2
),
recovery AS (
  SELECT
    source.refusal_reason AS source_reason,
    target.id,
    target.status_kind,
    target.refusal_reason AS target_reason
  FROM source
  LEFT JOIN alphamelts_outputs AS target
    ON target.expedited_key = source.expedited_key
   AND target.engine_epoch = 3
)
SELECT
  source_reason,
  count(*) AS selected,
  sum(id IS NULL) AS missing_target,
  sum(target_reason = source_reason) AS repeated_reason,
  sum(status_kind = 'success') AS successes
FROM recovery
GROUP BY source_reason
ORDER BY source_reason;

WITH latest AS (
  SELECT
    o.*,
    row_number() OVER (
      PARTITION BY o.expedited_key
      ORDER BY o.engine_epoch DESC
    ) AS recency
  FROM alphamelts_outputs AS o
)
SELECT
  status_kind,
  coalesce(refusal_reason, '<none>') AS refusal_reason,
  count(*) AS rows
FROM latest
WHERE recency = 1
GROUP BY status_kind, refusal_reason
ORDER BY status_kind, rows DESC;
SQL
```

Acceptance:

1. `missing_target = 0` for all three source classes.
2. `repeated_reason = 0` for both dictionary classes.
3. `subprocess_died` has `repeated_reason = 0` for full recovery; otherwise the
   residual count is reported and retried/triaged, never silently accepted.
4. The latest-row census is the final effective corpus census; epoch-2 source
   rows remain present and unchanged for provenance.

## Verification

All commands used the main-repository venv and quoted serial mode `-n0`:

- focused parser regression + qualified/unqualified activity-table controls +
  phase dictionary test: **4 passed**;
- grid-writer suite, `tests/test_grid_pregrind.py`: **72 passed**;
- wave10 suites, `tests/test_wave10_tooling_resilience.py` and
  `tests/chemistry/test_wave10_metallothermic_failclosed.py`: **2 passed**;
- parser/backend suite, `tests/test_alphamelts_backend.py`: **153 passed, 2
  skipped, 2 failed**. Both failures are outside this diff: the deterministic
  `test_endmember_activity_labels_do_not_reach_evaporation_flux_as_oxide_keys`
  uses a stale `SimpleNamespace` test double lacking the current
  `_evaporation_flux_control_inputs` helper, and the installed-engine cold C0
  integration test timed out after its existing 20-second subprocess limit
  (the timeout reproduced on an isolated rerun).
- direct scratch smoke of the formerly failing multi-block text returned only
  `{'H2O': 0.0}`.

No goldens/data-setpoints or cache identity fields changed. The dictionary
manifest content changes descriptively, as required for its enumerated values;
cache lookup identity remains untouched.

READY: docs-private/research/2026-07-16-t337-parser/findings.md

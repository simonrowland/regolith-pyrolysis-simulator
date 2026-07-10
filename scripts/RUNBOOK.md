# AlphaMELTS expedited grid grinder

The daemon uses worker processes only for AlphaMELTS calls. One parent SQLite
writer commits batches under WAL mode; harvest readers do not pause the daemon,
and a crash can lose at most the current uncommitted batch.

## Local smoke

From the repository root:

```bash
.venv/bin/python scripts/grid_pregrind.py \
  --prepare-only \
  --limit 12 \
  --db docs-private/recipe-db/grind-alphamelts-expedited.db

.venv/bin/python scripts/grid_pregrind.py \
  --drain-only \
  --workers 2 \
  --limit 6 \
  --db docs-private/recipe-db/grind-alphamelts-expedited.db
```

The first command is the only path that generates the grid or imports Kress91
partitioning. The second opens that prepared queue without generating points,
upserting a batch, or materializing keys. Run the drain command again; it must
report the selected rows as `resume_skipped` with `pending=0`. Drain-only reads
the stored batch params and Kress91 provenance from `batches.params_json` and
records host, worker, and engine-probe facts in `last_drain_run` metadata.
Inspect counts directly with:

```bash
sqlite3 docs-private/recipe-db/grind-alphamelts-expedited.db \
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

## Studio-1 deploy

Target: `mac-studio-256-1`. The grinder is niced below MinerU, LiteLLM, and
OMLX. Do not deploy to a different fleet member from an older host map.

1. Inspect the studio checkout before updating, then fast-forward from the
   public `3a0e64c` baseline:

   ```bash
   git status --short
   git pull --ff-only origin main
   git merge-base --is-ancestor 3a0e64c HEAD
   ```

2. Run the engine-stack check mode and the grinder's direct AlphaMELTS probe:

   ```bash
   python3 install-engines.py --check
   .venv/bin/python scripts/grid_pregrind.py --probe-only
   ```

   The frozen `3a0e64c` installer does not yet accept `--check`. If the pulled
   release still lacks that mode, use the direct probe as the fail-closed check;
   do not run the installer's mutating default mode merely to verify the stack.

3. Materialize Studio-1's quasi-uniform shard on the laptop. This seeds
   `grid_keys` and output AUTOINCREMENT IDs at the Studio-1 block starting at
   `1000000000`; ranks are assigned once and shard by `shuffle_rank % 3`:

   ```bash
   .venv/bin/python scripts/grid_pregrind.py \
     --prepare-only \
     --shard 0 \
     --db docs-private/recipe-db/grind-alphamelts-studio1.db
   ```

   Shards 1 and 2 use the identical command with their shard number and a
   separate DB. Their ID blocks start at 2000000000 and 3000000000. Do not
   launch them until those studios are cleared.

   `id_block_registry` records the source namespace. IDs below 1000000000 are
   laptop/dev/smoke; leading block 1 is Studio-1, 2 is Studio-2, and 3 is
   Studio-3. Blocks 4000000000, 5000000000, and later are reserved for future
   named run types or sources and must be added to the registry when allocated.
   The leading block therefore identifies provenance even during raw-ID merge.

4. Create studio-local storage outside Dropbox, copy the prepared shard DB,
   and render the plist template:

   ```bash
   mkdir -p "$HOME/Library/Application Support/regolith-grid" \
     "$HOME/Library/Logs/regolith-grid"
   cp scripts/com.rpp.grid-pregrind.plist.template \
     "$HOME/Library/LaunchAgents/com.rpp.grid-pregrind.plist"
   ```

   Replace `__HOME__`, `__REPO_ROOT__`, `__LOCAL_DB_PATH__`, `__STATUS_PATH__`,
   and `__LOG_DIR__`. Recommended local paths:

   - DB: `$HOME/Library/Application Support/regolith-grid/grind-alphamelts-expedited.db`
   - status: `$HOME/Library/Application Support/regolith-grid/status.json`
   - logs: `$HOME/Library/Logs/regolith-grid`

   Copy `grind-alphamelts-studio1.db` to the configured studio-local DB path
   before loading the agent. The template pins `--drain-only --shard 0`; it
   cannot regenerate or re-partition the laptop-prepared queue. As a preflight,
   run the rendered command once with `--workers 2 --limit 6` and confirm it
   produces output rows without importing `simulator.fe_redox`.

   Never omit `--drain-only` on a studio. A normal run against a DB that already
   contains stored batch seed/params refuses and directs the operator back to
   `--drain-only`, preventing cross-version key rematerialization.

5. Validate and install the LaunchAgent:

   ```bash
   plutil -lint "$HOME/Library/LaunchAgents/com.rpp.grid-pregrind.plist"
   launchctl bootstrap "gui/$(id -u)" \
     "$HOME/Library/LaunchAgents/com.rpp.grid-pregrind.plist"
   launchctl print "gui/$(id -u)/com.rpp.grid-pregrind"
   ```

SIGTERM stops new submissions, waits for active calls, commits the final batch,
and closes SQLite. The plist sets `Nice=10`, `KeepAlive=true`, and 22 workers
(80% of the studio's 28 cores).

## Incremental harvest

Run manually or from cron on the laptop. The remote backup is a WAL-consistent
SQLite snapshot; the local accumulator advances its last-seen output ID per
host/table and ignores canonical-key replays:

```bash
.venv/bin/python scripts/grind_harvest.py \
  --host mac-studio-256-1 \
  --remote-db "$HOME/Library/Application Support/regolith-grid/grind-alphamelts-expedited.db" \
  --accumulator docs-private/recipe-db/grind-alphamelts-accumulator.db
```

The command prints pulled, inserted, conflict-skipped, source-total, and
accumulator-total counts. Harvest can run at any cadence without stopping the
daemon.

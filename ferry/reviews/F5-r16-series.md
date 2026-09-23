# F5 — R16 P1 series store regen (printed log fO2 + migrate hardenings)

**Branch:** `empirical/f5-r16-series-2026-09-22`
**Base:** `origin/review/r34-hardening` (`07ad01dae`)
**Tip:** `b616a5739aa950da14216917cf3b9d47f1653c0d`
**Worktree:** `/workspace/repos/wt/slot-09`
**Status:** implemented; pushed
**Scope:** confirmed R16 P1 only (P2 interval-keep × `interval_needs_point` left untouched)

## Intent

R16 found the r34-hardening series regenerated the derived store at
`2e9e17c3d`, then landed printed per-run log fO2 (`d4f91337f`) and migrate
hardenings (`22cf80906`, `07ad01dae`) with **no** follow-up regen. Store
consumers (`load_migrated_store`, readiness, score) read committed
`extracts-v2`, so the tip advertised landings readiness never saw.
`scripts/check_store_freshness.py --head HEAD` reported STALE (3 later
migrate-input commits). Live migrate + tip fo2 tests were already green;
the hole was the landed store.

## Change

One tip regen after `07ad01dae`:

1. `.venv/bin/python scripts/battery_migrate.py`
   → `rows_in=46370 observations=91807 works=238 experiments=6875 queue=130479 hard_issues=1468`
2. `.venv/bin/python data/literature/build_index.py --write-store-summary`
   (summary bytes unchanged vs prior tip)

Committed store deltas (explicit pathspecs):

| Path | Role |
| --- | --- |
| `data/literature/extracts-v2/holzheid-1997-feo-nio-coo-activity-metal-saturated.yaml` | land printed `fO2_log` |
| `data/literature/extracts-v2/kems-140-heck-2025.yaml` | land printed `fO2_log` |
| `data/literature/extracts-v2/sossi-2020-cu-zn-isotope-evap-formalism.yaml` | land printed `fO2_log` |
| `data/literature/extracts-v2/thomas-2022-chlorine-bonding-silicate-melts.yaml` | land printed `fO2_log` |
| `data/literature/works/10.1016_j.chemgeo.2022.121269.yaml` | Thomas work refreshed |
| `data/battery/migration-queue.yaml` / `migration-report.md` | regen artefacts |
| `tests/battery/test_r16_store_regen.py` | committed-store pins + mutation proofs |

## Before / after (committed `extracts-v2` `fO2_log:` keys)

| Source stem | Before (`07ad01dae`) | After (`b616a5739`) |
| --- | ---: | ---: |
| `holzheid-1997-feo-nio-coo-activity-metal-saturated` | **0** | **33** |
| `sossi-2020-cu-zn-isotope-evap-formalism` | **0** | **70** |
| `kems-140-heck-2025` | **0** | **81** |
| `thomas-2022-chlorine-bonding-silicate-melts` | **0** | **43** |

Freshness STALE half:

| Check | Before | After |
| --- | --- | --- |
| `check_store_freshness.py` STALE (3 post-`2e9e17c3d` input commits) | **STALE** | **cleared** (last store touch = this regen) |
| UNTRACED (`bencze` / `charnoz` / `lebrun` / `vanbuchem`) | 4 | 4 (pre-existing; out of R16 P1) |

## Tests

```text
.venv/bin/pytest tests/battery/test_r16_store_regen.py \
  tests/battery/test_printed_fo2.py -o addopts= -q
# 17 passed
```

Mutation proofs (`test_r16_store_regen.py`):

1. `test_mutation_pre_regen_tip_extracts_v2_have_zero_fo2_log` — blobs at
   `07ad01dae` still have **0** `fO2_log` keys (census would fail on the
   stale tip).
2. `test_mutation_stripping_fo2_log_fails_census_bound` — delete landed
   keys from a tmp copy → bound assertion fails.
3. `test_mutation_stale_tip_is_flagged_by_freshness_stale_half` — on
   `07ad01dae` the STALE half still names `d4f91337f` et al.

## Out of scope

- R16 P2 (interval-keep × `interval_needs_point` × `oxygen_condition` route
  order) — latent; not the confirmed P1.
- UNTRACED four extracts — residual note in R16; not created by this series'
  fo2 hole.

## Push

Pushed: yes (`origin/empirical/f5-r16-series-2026-09-22`).
One commit: `b616a5739` — `battery: regenerate the derived store for printed log fO2 and migrate hardenings`

READY: /workspace/ferry-inbox/reviews/F5-r16-series.md

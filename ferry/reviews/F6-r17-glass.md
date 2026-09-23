# F6 — R17 P1 JANAF glass-region store regen

**Branch:** `empirical/f6-r17-glass-2026-09-22`
**Base:** `origin/review/s9-glass` (`b5b9dacc8`)
**Tip:** `2ed3e40931da68aa0871aff8f9108cfa8d9358a7`
**Worktree:** `/workspace/repos/wt/slot-07`
**Status:** implemented; pushed
**Scope:** confirmed R17 P1 only (P3 pure-glass / multi-marker gates left untouched)

## Intent

R17 found `review/s9-glass` taught `generate_table` to leave mixed
glass+liquid liquid-table series as phase unknown with the printed marker
quoted, but did not regenerate `observations-v2`. Store consumers
(`load_migrated_store`) still saw `phase.value=l` on all 128
`transition_temperature:glass-liquid` tables. `scripts/check_store_freshness.py
--head HEAD` reported STALE (4 later migrate-input commits after
`2e9e17c3d`, including `b5b9dacc8`). Tip generator tests were already green;
the hole was the landed store.

## Change

One tip regen after `b5b9dacc8`:

1. `.venv/bin/python scripts/battery_migrate.py`
   → `rows_in=46370 observations=91807 works=238 experiments=6875 queue=131247 hard_issues=1468`
2. `.venv/bin/python data/literature/build_index.py --write-store-summary`

Committed store deltas (explicit pathspecs):

| Path | Role |
| --- | --- |
| `data/literature/observations-v2/compilations-janaf/janaf-*.yaml` (34 shards with glass-liquid tables) | relabel glass-region series `l` → unknown |
| `data/literature/extracts-v2/holzheid-1997-…` / `kems-140-…` / `sossi-2020-…` / `thomas-2022-…` | collateral: land printed `fO2_log` (same tip also carried R16's post-regen extracts) |
| `data/literature/works/10.1016_j.chemgeo.2022.121269.yaml` | Thomas work refreshed |
| `data/battery/migration-queue.yaml` / `migration-report.md` | regen artefacts |
| `data/literature/observation_store_summary.yaml` | store summary |
| `tests/battery/test_r17_glass_store_regen.py` | committed-store pins + mutation proofs |

No generator code change (R17: not required for this finding).

## Before / after (committed `observations-v2` glass-region)

| Check | Before (`b5b9dacc8`) | After (`2ed3e4093`) |
| --- | ---: | ---: |
| glass-liquid tables with `cp:segment-0` phase `l` | **128 / 128** | **0 / 128** |
| glass-liquid tables with `cp:segment-0` phase `unknown` | **0 / 128** | **128 / 128** |
| `nist-janaf-4th:Mg-013:cp:segment-0` | `value=l` | `unknown` + `printed "GLASS <--> LIQUID" at 900.000 K` |
| `nist-janaf-4th:W-003:cp:segment-0` | `value=l` | `unknown` + `printed "GLASS <--> LIQ"` |

Collateral `extracts-v2` `fO2_log:` keys (same tip also stale for R16's three post-`2e9e17c3d` extract/migrate commits):

| Source stem | Before | After |
| --- | ---: | ---: |
| `holzheid-1997-feo-nio-coo-activity-metal-saturated` | **0** | **33** |
| `sossi-2020-cu-zn-isotope-evap-formalism` | **0** | **70** |
| `kems-140-heck-2025` | **0** | **81** |
| `thomas-2022-chlorine-bonding-silicate-melts` | **0** | **43** |

Freshness STALE half:

| Check | Before | After |
| --- | --- | --- |
| `check_store_freshness.py` STALE (4 post-`2e9e17c3d` input commits) | **STALE** | **cleared** (last store touch = this regen) |
| UNTRACED (`bencze` / `charnoz` / `lebrun` / `vanbuchem`) | 4 | 4 (pre-existing; out of R17 P1) |

## Tests

```text
.venv/bin/python -m pytest tests/battery/test_r17_glass_store_regen.py \
  tests/battery/test_janaf_generator.py::test_liquid_glass_region_is_not_stamped_liquid \
  -o addopts= -q
# 7 passed
```

Mutation proofs (`test_r17_glass_store_regen.py`):

1. `test_mutation_pre_regen_tip_stamps_mg013_liquid` — blob at `b5b9dacc8`
   still has Mg-013 `cp:segment-0` phase `l` (census would fail on the stale tip).
2. `test_mutation_restamping_mg013_liquid_fails_unknown_pin` — rewrite Mg-013
   to `l` in a tmp copy → unknown pin fails.
3. `test_mutation_stale_tip_is_flagged_by_freshness_stale_half` — on
   `b5b9dacc8` the STALE half still names that glass generator commit.

## Out of scope

- R17 P3 (pure-glass `Phase.GLASS` branch and multi-marker silent skip) —
  latent / defense-in-depth; not the confirmed P1.
- UNTRACED four extracts — residual note in R17 freshness output; not created
  by this glass-region hole.
- Generator logic — already correct on `b5b9dacc8`; this lane is store regen only.

## Push

Pushed: yes (`origin/empirical/f6-r17-glass-2026-09-22`).
One commit: `2ed3e4093` — `battery: regenerate the derived store for the JANAF glass-region relabel`

READY: /workspace/ferry-inbox/reviews/F6-r17-glass.md

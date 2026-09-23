# F4 — identity special-cases + identity instability (V4)

**Branch:** `empirical/f4-identity-2026-09-22`
**Base:** `origin/review/r6-r8-fix` (`1f8df6cbe`)
**Tip:** `f5e4a6aca7a4a87173e54d2b001c8140055b2ae3`
**Worktree:** `/workspace/repos/wt/slot-04`
**Input:** `/workspace/ferry-inbox/verify/V4-s10-s15-identity.md` (CONFIRMED only)
**Status:** implemented + pushed

## Scope (confirmed only)

| Source | Verifier sev | Shared root | Shipped? |
|---|---|---|---|
| S10-P0a (compilation origin hole) | P1 | **R-comp** | yes |
| S10-P0c (SiO/CrO2 name-gated backstops) | P1 | **R-wall** | yes |
| S10-P0b (SiO bare-zero arm) | P2 latent | R-wall | **no** (per V4 / steering) |
| S15-1,3,4,5,6,8 (ordinal ids) | P1 | **R-ord** | yes |
| S15-2,7 (label ids) | P1 | **R-label** | yes |
| S15 P2 amplifiers | P2 | — | no |

One commit per root cause (4 commits).

## Commits

1. `c57351664` — **R-comp** `battery: classify compilation rows by store path, not source_id prefixes`
2. `40bf4e3b0` — **R-wall** `condensation: authorize wall backstops by declared product class`
3. `96593a15d` — **R-ord** `battery: mint durable ids from content, not encounter ordinals`
4. `f5e4a6aca` — **R-label** `battery: keep printed labels out of durable identity keys`

## Changes

### R-comp
- `load_score_context` walks `iter_observation_store_paths` (same recursion as `load_migrated_store`).
- Origin keys are relative paths (`compilations-<family>/<shard>.yaml`), so nested USGS shards satisfy `compilations-*`.
- `is_compilation_source` gates on path-under-compilations first; source_id prefix markers are legacy fallback only when origin is absent.

### R-wall
- Sticking YAML gains `wall_product_class_by_species` (`CrO2: stable_condensation_product`).
- Reactive backstop authorizes via `reactivity_class == reactive` (no `species == "SiO"`).
- Stable backstop authorizes via declared wall product class (no `species == "CrO2"`).
- Call sites in `condensation.py` and `wall_deposition.py` updated.

### R-ord
- New `simulator/battery/stable_ids.py` helpers.
- JANAF series: `phase-window:T=lo..hi` (or `whole`) instead of `segment-N`.
- USGS cells: `T=` + `col=` + printed `name=` — no `row=`.
- Migrate series explode: `::T=` (or printed fallback) instead of `::point:N`.
- Oxygen fan-out: `::field:<name>` instead of encounter index.
- Author `::point:N` rekeyed to `::T=` when temperature is present (S15-6).
- CaO raw pCa: `cao_raw_pCa:T=…`.
- Live observations-v2 still carries pre-remint ids until a store rematerialize landing.

### R-label
- JANAF transition ids: `transition_temperature:T=<printed T>`; subtype remains on identity for display.
- `_experiment_id` no longer mints from locator table/figure/record; declared id or stable fallback only.

## Tests + mutation proofs

```text
.venv/bin/pytest \
  tests/battery/test_f4_compilation_origins.py \
  tests/test_f4_wall_product_class.py \
  tests/battery/test_f4_stable_ids.py \
  tests/battery/test_f4_label_ids.py \
  -o addopts=
# 23 passed
```

Mutation proofs:
- R-comp: monkeypatch `_origin_under_compilations` → False restores nested USGS miss.
- R-wall: monkeypatch reactivity / wall_product class helpers flips authorization.
- R-ord: monkeypatch `temperature_token` changes series point ids.
- R-label: retitling locator.table does not change minted experiment_id.

Related generator / migrate spot checks also updated for the new id schemes.

## Push

`git push -u origin HEAD` → `origin/empirical/f4-identity-2026-09-22`

READY: /workspace/ferry-inbox/reviews/F4-identity.md

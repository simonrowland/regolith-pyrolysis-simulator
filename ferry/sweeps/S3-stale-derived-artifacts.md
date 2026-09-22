# S3 — stale derived artifact outlives its source

**Code:** `origin/review/janaf-batch-2026-09-22` @ green tip `fbe3491b2`.
**Scope:** `simulator/battery/`, `scripts/` (bench/battery), `tools/validate_literature_extracts.py`, `data/literature/build_index.py`.
**Predicate:** a generated/derived file or cache entry is written per-source but never removed when the source is removed, renamed or produces nothing, and is later READ back as if current (store dirs, works/, INDEX, caches, readiness inputs, scoreboard inputs).
**Seed:** R4 P1 — extracts-v2 orphans after delete/rename.

Read-only sweep (no push). Tip tree used for LIVE checks: `extracts=`240, `extracts-v2=`236, stem-orphans `v2−extracts=`**0** today.

---

## Findings

| site (file:line) | predicate match | constructed trigger | live? | sev |
|---|---|---|---|---|
| `simulator/battery/migrate.py:8937-8958` (`write_outputs`) — empty-rewrite only when **source extract still exists**; **no** `unlink` for stems absent from `discover_extracts`. Contrast `observations-v2` wipe-all at `:8916-8926` and `works/` unlink at `:8856-8860`. | Per-extract sibling written under `extracts-v2/<stem>.yaml`; deleted/renamed stem’s sibling survives remigrate with prior observations. | (1) Migrate extract `gone.yaml` with ≥1 observation → `extracts-v2/gone.yaml` exists. (2) Delete or rename `extracts/gone.yaml`. (3) Remigrate any other extract. Sibling remains. | **latent** (0 stem-orphans at tip) | **P1** |
| `simulator/battery/migrate.py:1483-1495` `load_migrated_store` via `iter_observation_store_paths` on **every** `extracts-v2/*.yaml` | Orphan sibling READ into the observation dict (last-wins on `observation_id`); feeds bench readiness, generate, score. | After trigger above: `load_migrated_store(root)` still returns `gone-source::…` observation ids; `scripts/bench_readiness.py` / `bench_generate.py` / `score_store` see them as live store rows. | latent | **P1** |
| `simulator/battery/score.py:1522-1531` `load_score_context` — `extracts-v2.glob("*.yaml")` for origins | Orphan file READ into `origins[observation_id]=path.name` and observations come from `load_migrated_store`. | Same orphan tree; score context attributes residuals to the dead sibling name. | latent | **P1** |
| `data/literature/build_index.py:1025-1037` `load_v21_store` — walks all `extracts-v2/*.yaml` by stem; `build_source_status:1286-1290` unions `set(store)` into `source_ids` | Ghost `source_id` enters `SOURCE_STATUS.yaml` as ingested/unknown from store artefacts alone (no extract required). | Orphan `extracts-v2/gone.yaml` with observations → `python data/literature/build_index.py` emits a `gone` status row with `artefacts: [data/literature/extracts-v2/gone.yaml]`. | latent | **P1** |
| `scripts/battery_score.py:4,176-228,254-283` + committed `data/battery/score-report.md` | Scoreboard derived from store+residuals; `residuals.jsonl` is gitignored; report is committed and **not** regenerated when the store tip moves. Tip report still claims store `554775645` (45737 rows in / 84322 obs / **166 works**); tip `migration-report.md` is 46369 / 91588 / **238 works** (`fbe3491b2` store regen). | Land store regen without re-score (already true at tip). Anyone reading `data/battery/score-report.md` as current scoreboard gets pre-remap headlines. `--report-only` also rewrites the report from a stale/missing residuals ledger (stamp mismatch is only a `warnings.warn`). | **LIVE** at tip | **P1** |
| `data/literature/build_index.py:1068-1073` (`load_v21_store` shard cache) + `:1466-1467` summary regen **opt-in** (`--write-store-summary`) | Per-shard `{size, observation_id_count}` cache reused when `cached["size"]==size`; content can change at equal byte length and the old count is READ into `SOURCE_STATUS` totals. Deleted-shard keys linger in the YAML until opt-in regen (unused once the file is gone — weaker). | Rewrite a compilation shard to a different observation_id count but identical `st_size` without `--write-store-summary`; next `build_source_status` keeps the cached `total_rows`. | latent (tip summary matches all 969 live shards) | **P3** |
| `scripts/bench_generate.py:37-72` | Writes numbered `NNN.json` / `.evidence.json` under `--output` without clearing the directory; a shorter re-run leaves higher-numbered files on disk. | Generate once (N records); remove experiments; re-generate into same dir → files `N…` remain. **No in-scope reader** globs them (only `summary.json` lists current); debris only. | latent | **P3** |

### Notes tied to the seed (not extra sites)

- Zero-obs **still-present** extracts empty-rewrite (`:8940-8958`) is the intentional d-032 fix; it does **not** cover delete/rename. R4-fix.patch’s extracts-v2 `unlink` loop is the mirror of works cleanup — not applied at tip.
- `scripts/check_store_freshness.py` only flags extracts **missing** a sibling (`_untraced_extracts`); it never flags extracts-v2 stems with no extract. Orphans pass the tripwire.

---

## No-hit areas (explicit clean)

- `works/` — non-live work YAML unlinked (`migrate.py:8856-8860`); benches/context ride the work file.
- `observations-v2/` — wipe-all then rewrite (`:8916-8926`), including compilation shards; empty shard dirs rmdir’d.
- `works/ALIASES.yaml` — rewritten with aliases filtered to `live_work_ids` (`:8816-8824`).
- `migration-queue.yaml` / `migration-report.md` — full rewrite each migrate.
- `INDEX.yaml` / `INDEX.md` / `SOURCE_STATUS.yaml` — full rewrite from a directory walk (ghost rows only via stale extracts-v2, counted above).
- JANAF / USGS `write_staging` — refuse non-empty `--out` (no incremental leftover shards in-place).
- `tools/validate_literature_extracts.py` — validate-only; no derived store writers.
- `scripts/bench_readiness.py` — stdout JSON only; no on-disk cache.
- `simulator/battery/pins.py` — tombstones are intentional hand baseline, not auto per-source derivatives.
- `residuals.jsonl` writer — wholesale replace when a full score runs (divergence is the committed report / gitignore pair above).

---

SWEEP: S3 | sites=7 | live=1 | P0=0 P1=5 P2=0 P3=2 | no-hit areas: works/ unlink, observations-v2 wipe-all, ALIASES live-filter, migration-queue/report full rewrite, INDEX/SOURCE_STATUS full rewrite (except ghost via extracts-v2), JANAF/USGS empty-out staging, validate_literature_extracts (no writers), bench_readiness (no cache), pins tombstones (intentional), residuals.jsonl wholesale replace on full score

# S1 — cross-scope reference by id

**Repo tip:** `fbe3491b2` (work-v064-green / live paths); code also checked on `origin/review/janaf-batch-2026-09-22` (same sites; migrate blob differs elsewhere).  
**Scope:** `simulator/battery/`, `scripts/` (bench/battery), `tools/validate_literature_extracts.py`, `data/literature/build_index.py`.  
**Predicate:** code resolves an id (experiment, bench, context, work, observation, fidelity pin, apparatus ref) by a **global** lookup, accepting a target owned by a different work/extract than the referrer, with **no typed refusal**.  
**Seed:** R4 P0 — equipment FK via `::` id (`ferry/reviews/R4-migrate-stale-and-equipment-fk.md`).  
**Live corpus check (2026-09-22):** `rg` on `data/literature/extracts/*.yaml` for FQ `bench_id:` / `experiment:` with `::` → **0 hits**. Works-tree audit of `experiment.bench_id` / `equipment_context_id` vs owning `work_id` → **0 cross-work FKs**. All sites below are **latent**.

Shared enabler: `Migrator._registry_id` (`simulator/battery/migrate.py:6316-6318`) returns any token containing `::` unchanged (no work-scope check). SCHEMA says registry IDs are local to the extract and the migrator qualifies as `<work_id>::bench|experiment::<id>`.

| site (file:line) | predicate match | trigger input | live? | severity |
|---|---|---|---|---|
| `simulator/battery/migrate.py:6502-6534` (`_link_equipment_context` → `self.result.experiments.get`) | Global experiment lookup; writes `equipment_context_id` onto a **foreign** experiment when FQ `experiment:` resolves; refuses only missing / second-equipment — **not** `experiment.work_id != work.work_id`. Seed R4 P0. | Extract `zzz-attacker` context row: `experiment: <victim_work>::experiment::<series>` + `equipment: {cell_material: poison}` (migrate after victim so victim experiment already exists). | latent | P0 |
| `simulator/battery/migrate.py:6377-6383` (+ `_registry_id` `:6316-6318`) | Experiment registry `bench_id` / `bench` accepted via FQ passthrough; no check that bench exists under **this** work or `bench.work_id == experiment.work_id`. Verifier-suspected `experiment.bench_id` cross-work case. | Attacker extract `experiments:` entry with `bench_id: <victim_work>::bench::<id>` where that bench was already lifted for the victim. | latent | P0 |
| `simulator/battery/validate.py:718-728` | `experiment.bench_id not in benches` only (dangling). Foreign **live** bench_id passes with zero issues. | Corpus where `experiment.work_id != benches[experiment.bench_id].work_id`. | latent | P0 |
| `simulator/battery/validate.py:730-741` | `equipment_context_id not in context_rows` only; no work_id / source match between experiment and context row (R4). | `equipment_context_id` naming another work's `context_id` that exists in the global context map. | latent | P0 |
| `simulator/battery/migrate.py:1540-1559` (`resolve_equipment_context`) | Global `context.get(ref)`; returns foreign row; SCHEMA requires resolve against **the work record's** `context:` list. Typed refusal only when dangling. | Any `Experiment` whose `equipment_context_id` is a live row with different `work_id` / source. | latent | P0 |
| `scripts/bench_generate.py:45` | `benches.get(experiment.bench_id)` — global; no `bench.work_id == experiment.work_id` refusal (only missing bench). | Store experiment carrying foreign `bench_id` (from migrate site above). | latent | P1 |
| `scripts/bench_readiness.py:300` | Same global `benches.get` accept of foreign bench identity for readiness / consumer paths. | Same as above. | latent | P1 |
| `simulator/battery/migrate.py:6583-6587` + `:6925` | Observation `experiment:` → `_registry_id` FQ passthrough; `experiment_id` stored without `experiment.work_id == work.work_id` check. `validate_observation` may later issue `source_id ∉ work.source_ids` (side-channel), but migrate attach itself has no typed cross-work refusal; finalize still writes. | Observation with `experiment: <victim_work>::experiment::<id>` (victim experiment already present). | latent | P1 |
| `simulator/battery/migrate.py:3007-3010` (`lineage_parents_from_source`) + `validate.py:932-940` | Qualified `derived_from` with `::` kept as written; validate only checks global observation membership — no same-extract / same-work refusal. | `derived_from: <other_source_id>::<observation_id>` where that observation exists in the store. | latent | P2 |

## Counts

- Sites: 9  
- Live: 0  
- P0: 5 · P1: 3 · P2: 1 · P3: 0  

## No-hit areas (in scope, audited)

- `tools/validate_literature_extracts.py` — fidelity sample / `_find_observation` / `resolve_field_path` are **single-extract doc-local**; no global cross-extract id resolve.
- `data/literature/build_index.py` — path / `source_id` index assembly; no bench/experiment/context/equipment FK global resolve of this class.
- `simulator/battery/pins.py`, `scripts/battery_score.py`, `scripts/battery_migrate.py` — pin_band / CLI wrappers; no cross-work registry FK lookup matching the predicate.
- `simulator/battery/generators/`, `identity.py`, `waypoints.py`, `polymorph_dictionary.py`, `consumer_inputs.py` (identity packaging only), `enums.py`, `records.py` (field defs only).
- `simulated_experiment_id` dangling check (`validate.py:710-716`) — global experiment map, but synthetic cross-ref is out of literature work-scope doctrine for this sweep (not counted).
- Live `data/literature/extracts/` and `data/literature/works/` — no constructed cross-work FQ FKs present today.

SWEEP: S1 | sites=9 | live=0 | P0=5 P1=3 P2=1 P3=0 | no-hit areas: tools/validate_literature_extracts.py; data/literature/build_index.py; simulator/battery/pins.py; scripts/battery_score.py; scripts/battery_migrate.py; generators/; identity/waypoints/polymorph/consumer_inputs/enums/records; simulated_experiment_id; live extracts/works (no FQ cross-work FKs)

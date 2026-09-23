# b-555 — silent extracts must emit a typed queue record (ADDENDUM-B4)

**Repo:** regolith-pyrolysis-simulator  
**Branch:** `empirical/b555-silent-extracts-2026-09-22`  
**Base:** `origin/work-v064-green` @ `2e9e17c3d138fdfba9269c493f82974a71153fa5`  
**Tip:** `d626bd959b1d6f8b39a0d7cfbc19dfec96bcd651`  
**Seat:** `/workspace/repos/wt/w3b-green`  
**Date:** 2026-09-23 00:05 EDT (America/Toronto)

## Scope

ABSENCE of observations from four extracts is correct; SILENCE (no extracts-v2 sibling and no migration-queue entry) is not. Make migrate emit a typed refusal/queue record per extract; do **not** ingest observations. Make the class impossible: any extract with zero observations AND zero typed records must emit a typed record.

## Motivating extracts (4 → 0 silent)

| Extract | Why typed |
| --- | --- |
| `bencze-yazhenskikh-2016-table-s1-k` | `table-shaped extract not consumed` (`literature_extract_table.v1`) |
| `charnoz-2023-hydrogen-magma-ocean` | `model-derived cohort excluded by ruling` |
| `lebrun-2013-magma-ocean-atmosphere` | `model-derived cohort excluded by ruling` |
| `vanbuchem-2023-lavatmos` | `model-derived cohort excluded by ruling` |

Also typed by the class invariant (already had empty extracts-v2 siblings; now also queued): `vanbuchem-2025-lavatmos2` (model-derived), `lpi-lunar-sourcebook-chapter08` (catch-all `extract produced no observations`).

## One-line fix

After each extract lift, if the source still has zero observations and zero queue rows, `add_queue` with `silent_extract_refusal_why(doc)`.

## Changes

| File | Change |
| --- | --- |
| `simulator/battery/migrate.py` | `silent_extract_refusal_why` + `Migrator._record_silent_extract_if_needed` at end of `_migrate_extract`. |
| `tests/battery/test_migrate.py` | Classifier, table/model/catch-all migrate fixtures, mutation proof, live-file classify, committed-store pin. |
| `data/battery/migration-queue.yaml` | +6 typed silent-extract entries. |
| `data/battery/migration-report.md` | per-source queued 0→1 for those six; queue size 130257→130263. |

## Before / after

| Check | Before | After |
| --- | --- | --- |
| `scripts/check_store_freshness.py` untraced | **4** (the motivating set) | **0** |
| Observations ingested from the four | 0 | 0 (unchanged; not ingested) |
| Queue `why` for table sidecar | *(absent)* | `table-shaped extract not consumed` |
| Queue `why` for three model extracts | *(absent)* | `model-derived cohort excluded by ruling` |

## Mutation proof

`test_b555_silent_extract_mutation_proof`: monkeypatch `Migrator._record_silent_extract_if_needed` → no-op restores silence (0 queue rows for the table fixture); undo recovers the typed refusal.

## Tests run

```text
pytest -n 0 -q tests/battery/test_migrate.py -k b555
  test_b555_silent_extract_refusal_why_classifier
  test_b555_table_shaped_extract_emits_typed_refusal_not_observations
  test_b555_model_derived_extract_emits_typed_refusal_not_observations
  test_b555_zero_obs_zero_queue_is_impossible
  test_b555_silent_extract_mutation_proof
  test_b555_live_extract_files_classify_to_named_reasons
  test_b555_committed_store_has_zero_silent_extracts
→ 7 passed

check_store_freshness.py → OK (every extract has a store trace; untraced 4→0)
```

## Regen note

Extract-only lift + merge of the six silent queue rows into the committed queue/report (no observation payloads change; absence preserved). Working-tree / tip untraced count: 4→0.

## Push

`origin/empirical/b555-silent-extracts-2026-09-22` @ `d626bd959b1d6f8b39a0d7cfbc19dfec96bcd651`

READY: /workspace/ferry-inbox/reviews/b555-silent-extracts.md

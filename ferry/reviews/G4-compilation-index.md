# G4 — Compilation record JSON paths as Work INDEX assets

**Repo:** regolith-pyrolysis-simulator    
**Branch:** `empirical/g4-compilation-index-assets-2026-09-22`  
**Base:** `origin/work-v064-green` @ `2e9e17c3d138fdfba9269c493f82974a71153fa5`  
**Tip:** `b47c10582d93b862f9440e78b96e97286a2a2f75`  
**Seat:** `/workspace/repos/wt/slot-10`  
**Plan:** `/workspace/ferry-inbox/analysis/A5-g-lanes.md`  
**A2 ranks:** 7 (~1,855 queue entries)  
**Date:** 2026-09-22 21:55 EDT (America/Toronto)

## Scope

ENGINEERING only. When `locator.source_path` names `data/literature/compilations/robie-hemingway-fisher-1978-usgs-b1452/…/*.json`-style record paths under `compilations/.../records/`, register that path as a Work INDEX asset and resolve it in `choose_read_from` before falling back to `unknown`. Stated compilation-record locators never fall through to PDF.

## One-line fix

Add `COMPILATION_RECORD` (`compilation_record`), register record JSON paths on the Work during compilation migrate, and prefer those assets in `choose_read_from`.

## Changes

| File | Change |
| --- | --- |
| `simulator/battery/enums.py` | `AssetRole.COMPILATION_RECORD`. |
| `simulator/battery/migrate.py` | `is_compilation_record_path` / `compilation_record_asset_id` / `compilation_record_source_file`; register+merge extras across work ensure/rebuild; resolve in `choose_read_from` before unknown/PDF. |
| `tests/battery/test_migrate.py` | Predicate + migrate integration + mutation proof + no-PDF-fallthrough unit. |

## Before / after (fixtures)

| Record | Before | After |
| --- | --- | --- |
| `robie-hemingway-fisher-1978-usgs-b1452-0003` | `read_from=unknown:…`; queue `locator source_path 'data/literature/compilations/robie-hemingway-fisher-1978-usgs-b1452/records/robie-hemingway-fisher-1978-usgs-b1452-0003.json' has no matching INDEX asset` | `read_from=compilation_record:data/literature/compilations/robie-hemingway-fisher-1978-usgs-b1452/records/robie-hemingway-fisher-1978-usgs-b1452-0003.json`; no INDEX-asset queue |
| OCR locator without MinerU asset | unknown (never PDF) | unchanged (guard) |
| Extract with empty INDEX corpus | unknown read_from | unchanged (guard) |

## Mutation proof

`test_g4_compilation_record_asset_mutation_proof`: set `is_compilation_record_path` → always False → migrate re-queues `has no matching INDEX asset` and `read_from` starts with `unknown`; restoring the predicate recovers the `compilation_record:data/literature/compilations/robie-hemingway-fisher-1978-usgs-b1452/records/robie-hemingway-fisher-1978-usgs-b1452-0003.json` asset id.

## Tests run

```text
pytest -n 0 -q
  test_g4_compilation_record_path_predicate
  test_g4_compilation_record_registers_index_asset
  test_g4_compilation_record_asset_mutation_proof
  test_g4_choose_read_from_compilation_does_not_fall_through_to_pdf
  test_h04_ocr_locator_does_not_fall_through_to_pdf
  test_g11_read_from_is_unknown_without_index_asset
→ 6 passed
```

## Push

`origin/empirical/g4-compilation-index-assets-2026-09-22`

READY: /workspace/ferry-inbox/reviews/G4-compilation-index.md

# V3 — verify S16 host-coupled tests (fail instead of skip)

**Verifier:** adversarial re-check of `/workspace/ferry-inbox/sweeps/S16-host-coupled-tests.md`  
**Code base:** `origin/review/r6-r8-fix` @ `1f8df6cbe` (worktree `/workspace/repos/wt/slot-04`)  
**Sweep tip cited:** `origin/work-v064-green` @ `fbe3491b2` — ancestor of this base; all 9 sites still present (janaf line nums drifted: claimed `:924/:964/:999` → tip `:1047/:1087/:1122`).  
**Env:** `REGOLITH_CORPUS_ROOT` unset; no `/Users/simonrowland/Repos/regolith-corpus`; no `docs-private/`.  
**Calibration (task):** a test failing without a private resource is a **CI defect P2/P3, not P0**, unless it hides a real regression. Null hypothesis = false-positive as P0.  
**Method:** static path/`skipif` audit + focused `pytest -o addopts=` (9 nodes). No product commits / no push.

## Reproduction (2026-09-22 ~20:57 EDT)

```
pytest -o addopts= --tb=line -q \
  …::test_every_numeric_token_round_trips_to_page_layout \
  …::test_source_round_trip_rejects_consistent_token_value_mutation \
  …::test_phase_records_have_strict_safe_grids_and_no_embedded_headers \
  …::test_phase_as_published_represented_per_record \
  …::test_off_grid_temperature_with_intact_layout_is_refused \
  …::test_grid_membership_is_distinct_from_field_count \
  …::test_non_data_marker_lines_are_recorded \
  …::test_t2_vaporock_residual_pins_are_two_way \
  …::test_c5_debit_route_alpha_and_source_metadata_are_executable[Na]
→ 4 failed, 5 errors (0 skipped)
```

Fail modes match sweep: Robie `AssertionError: read-only corpus PDF is required`; JANAF `FileNotFoundError` on `B-133.txt` / `Ba-001.txt`; rail `FileNotFoundError` on gitignored `docs-private/.../engine_crosscheck_report.json`.

## Does any hide a real regression?

**No.** Absence → ERROR/FAILED (loud CI red), not a green pass with wrong physics/data/score. Sibling compilations (B1544, extract-mirror, B689, Kelley) correctly `skipif` / `pytest.skip`. These are ungated private-resource tests, not silent product defects.

## Verdict table (9 claimed P0)

| finding | exists? | live (fresh CI)? | your sev | one-line skip condition | shared-root |
|---|---|---|---|---|---|
| Robie `source_layout` → `test_every_numeric_token_round_trips_to_page_layout` (:296) | **yes** — fixture `:84–99` `assert SOURCE_PDF.is_file()` | **yes** — ERROR at setup | **P2** (not P0) | `skipif(not SOURCE_PDF.is_file() or not shutil.which("pdftotext"))` | **R1** |
| same → `test_source_round_trip_rejects_consistent_token_value_mutation` (:302) | **yes** | **yes** — ERROR at setup | **P2** | same as above | **R1** |
| same → `test_phase_records_have_strict_safe_grids_and_no_embedded_headers` (:450) | **yes** (+ MinerU under hardcoded `MINERU_ROOT` when extraction=`mineru`) | **yes** — ERROR at setup (PDF assert first) | **P2** | same + `skipif(not MINERU_ROOT.is_dir())` when mineru path exercised | **R1** |
| same → `test_phase_as_published_represented_per_record` (:526) | **yes** | **yes** — ERROR at setup | **P2** | same as R1 PDF/`pdftotext` gate | **R1** |
| `test_non_data_marker_lines_are_recorded` (`test_janaf_generator.py:1122`; sweep `:999`) via `_raw_table_path` | **yes** — 21 private tables (`Ba/Ca/Cr/Cu/S-*.txt`) not in `tests/fixtures/janaf/` | **yes** — FAILED `FileNotFoundError` `Ba-001.txt` | **P2** | `pytest.skip` when `_raw_table_path(id)` is not a file (or when `_JANAF_SOURCE_DIR` absent) | **R2** |
| `test_off_grid_temperature_with_intact_layout_is_refused` (:1047; sweep `:924`) | **yes** — needs private `B-133.txt` | **yes** — FAILED `FileNotFoundError` | **P2** | skip unless `_raw_table_path("B-133").is_file()` (or vend fixture) | **R2** |
| `test_grid_membership_is_distinct_from_field_count` (:1087; sweep `:964`) | **yes** — same `B-133.txt` (+ fixture `Al-001` for second half) | **yes** — FAILED on `B-133` first | **P2** | same B-133 file gate | **R2** |
| `engine_residuals` → `test_t2_vaporock_residual_pins_are_two_way` (:1405–1437) | **yes** — `ENGINE_REPORT_PATH` `:72–78` gitignored `docs-private/.../engine_crosscheck_report.json` | **yes** — ERROR at fixture setup | **P2** | `skipif(not ENGINE_REPORT_PATH.is_file())` (or track a pin under `validation-data/`) | **R3** |
| `test_c5_debit_route_alpha_and_source_metadata_are_executable` (:1125–1230) `[Na]` (+ other cleaned_melt LIVE_SPECIES) | **yes** — `:1230` `ENGINE_REPORT_PATH.read_text` on executable branch | **yes** — FAILED `FileNotFoundError` | **P2** | skip when report absent before `:1230` (debit/alpha asserts above can stay) | **R3** |

## Shared roots

1. **R1 — Robie B1452 hardcoded host PDF** (`SOURCE_PDF` under `/Users/simonrowland/Repos/regolith-corpus/...`; fixture asserts instead of `skipif`). Prefer `REGOLITH_CORPUS_ROOT` like B1544/Hemingway. One gate fixes 4 tests.  
2. **R2 — JANAF `_raw_table_path` private fallthrough** (`:918–920`): missing fixture → private corpus path with no existence/`skip` gate. One helper-level skip (or vend `B-133` + 21 marker fixtures) fixes 3 tests.  
3. **R3 — ungated `ENGINE_REPORT_PATH`** (`docs-private/` gitignored; file comment at `:105` already notes machine-local). One `is_file()` skip (fixture + C5 branch) fixes 2 sites.

## Counts

| class | S16 claimed | verifier |
|---|---|---|
| **P0 held** | 9 | **0** |
| **P0 → demoted (real CI defect)** | — | **9 → P2** |
| **False-positive as described (no fail)** | — | **0** |
| **P1** | 0 | 0 |
| **P2 confirmed** | 0 claimed | **9** |
| **P3** | 0 | 0 |

No product-physics regression hidden. Severity overrate only: sweep Batch-Y “CI absence = P0” bar rejected by this task’s calibration.

## TL;DR

- **CONFIRMED P0: 0.** All 9 sites **exist and fail live** on fresh clone, but they are **CI skip-gate defects → P2**, not silent wrong physics/data/score.  
- **CONFIRMED P2: 9** (4 Robie R1 + 3 JANAF R2 + 2 rail engine-report R3). **False-positive descriptions: 0.**  
- Path: `/workspace/ferry-inbox/verify/V3-s16-host-coupled.md`

READY: /workspace/ferry-inbox/verify/V3-s16-host-coupled.md

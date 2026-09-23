# S16 — host-coupled tests that fail instead of skip

**Repo tip:** `fbe3491b2` (`origin/work-v064-green`).  
**Worktree:** `/workspace/repos/wt/slot-06` (detached @ `fbe3491b2`). READ-ONLY on product code; writes only to `/workspace/ferry-inbox/sweeps/`. Do not push.  
**Scope:** host-coupled tests that need a private corpus, engine binary, network, specific path or env var, and **FAIL** (instead of skip) when absent.  
**Predicate:** under default CI / fresh clone (no `REGOLITH_CORPUS_ROOT`, no `docs-private/`, no private engine install), a test or its fixture raises `AssertionError` / `FileNotFoundError` / ERROR at setup rather than `pytest.skip` / `@pytest.mark.skipif`.  
**Seed:** JANAF private-corpus hash / raw-txt coupling — `tests/test_janaf_compilation.py` now **skips** when `JANAF_SOURCE_DIR` is absent (dir gate at `:128–133`); sibling battery raw-path fallback still fails open (below). Closest literal “hash-check private file → CI red” pattern on this tip: Robie B1452 `source_layout` SHA256 of a hardcoded `/Users/simonrowland/Repos/regolith-corpus/...` PDF.  
**Method:** static path/`skipif` audit + focused `pytest -o addopts=` runs with corpus/`docs-private` absent; mode **ran-tests**.  
**P0 calibration (Batch Y, tests):** P0 when host/CI absence fails a gate that should skip.

| site (file:line / test) | host resource | fail mode (absence) | live? | P0? | severity | fix direction |
|---|---|---|---|---|---|---|
| `tests/test_robie-hemingway-fisher-1978-usgs-b1452_compilation.py:84–99` fixture `source_layout` → `test_every_numeric_token_round_trips_to_page_layout` (:296) | Hardcoded private PDF `/Users/simonrowland/Repos/regolith-corpus/raw/robie-hemingway-fisher-1978-usgs-b1452/...pdf` + `pdftotext` on PATH; also SHA256 vs `SOURCE_SHA256` | `AssertionError: read-only corpus PDF is required` (ERROR at setup). Confirmed. | live | yes | P0 | `skipif(not SOURCE_PDF.is_file())` (+ skip if `pdftotext` missing); prefer `REGOLITH_CORPUS_ROOT` like B1544/Hemingway |
| same fixture → `test_source_round_trip_rejects_consistent_token_value_mutation` (:302) | same | same ERROR at setup | live | yes | P0 | same |
| same fixture → `test_phase_records_have_strict_safe_grids_and_no_embedded_headers` (:450) | same (+ MinerU under hardcoded `MINERU_ROOT` when extraction=`mineru`) | same ERROR at setup | live | yes | P0 | same; gate MinerU with skip |
| same fixture → `test_phase_as_published_represented_per_record` (:526) | same | same ERROR at setup | live | yes | P0 | same |
| `tests/battery/test_janaf_generator.py:999` `test_non_data_marker_lines_are_recorded` via `_raw_table_path` (:795–797) | Private `…/raw/janaf-nist-txt/{Ba,Ca,Cr,Cu,S}-*.txt` (21 tables not in `tests/fixtures/janaf/`) | `FileNotFoundError` on first missing `Ba-001.txt`. Confirmed. | live | yes | P0 | skip when `_JANAF_SOURCE_DIR` absent / file missing; or vend the 21 marker fixtures |
| `tests/battery/test_janaf_generator.py:924` `test_off_grid_temperature_with_intact_layout_is_refused` | Private `B-133.txt` (not in fixtures) via `_raw_table_path` | `FileNotFoundError` `…/janaf-nist-txt/B-133.txt`. Confirmed. | live | yes | P0 | skip or add `B-133.txt` fixture |
| `tests/battery/test_janaf_generator.py:964` `test_grid_membership_is_distinct_from_field_count` | same `B-133.txt` | same `FileNotFoundError`. Confirmed. | live | yes | P0 | same |
| `tests/test_rail_conformance.py:1405–1407` fixture `engine_residuals` → `test_t2_vaporock_residual_pins_are_two_way` (:1437) | Gitignored `docs-private/research/2026-08-03-vapour-rail-engine-crosscheck/engine_crosscheck_report.json` (`ENGINE_REPORT_PATH` :72–78) | `FileNotFoundError` at fixture setup (ERROR). Confirmed. | live | yes | P0 | `skipif(not ENGINE_REPORT_PATH.is_file())`; or check in a tracked pin under `validation-data/` |
| `tests/test_rail_conformance.py:1125–1230` `test_c5_debit_route_alpha_and_source_metadata_are_executable` (parametrized over `LIVE_SPECIES`) | same `ENGINE_REPORT_PATH` read at `:1230` on the executable branch | `FileNotFoundError` per live species (39 FAILED observed). Confirmed `[Na]`. | live | yes | P0 | skip when report absent; or stub composition for C5 without private report |

## Counts

- Sites: 9  
- Live: 9  
- P0: 9 · P1: 0 · P2: 0 · P3: 0  

## No-hit areas (audited; correctly skip or in-repo)

- **Seed partially gated:** `tests/test_janaf_compilation.py:128–133` `janaf_source_dir` → `pytest.skip` when `JANAF_SOURCE_DIR` is not a directory; `test_source_txt_round_trip` / `test_mutated_real_source_token_fails` skipped on this tip (confirmed `Al-006` skip). Residual footgun (not scored): if `REGOLITH_CORPUS_ROOT` points at an **existing but incomplete** tree, dir gate passes and `:142`/`hashlib.sha256` asserts fail — should treat missing per-table files as skip too.  
- `tests/test_hemingway-haas-robinson-1982-usgs-b1544_compilation.py` — `@pytest.mark.skipif(not PDF.is_file(), …)`.  
- `tests/test_corpus_extract_mirror.py` — `skipif(not CORPUS_ROOT.is_dir(), …)`.  
- `tests/test_pankratz_1987_usbm_b689_compilation.py:424–428` corpus fixture → `pytest.skip`.  
- `tests/test_kelley_1960_usbm_b584_compilation.py:168–171` MinerU live decode → skip if corpus unmounted.  
- `tests/test_cache_convert.py` — `skipif(not LEGACY_DB.exists(), …)` for `docs-private/recipe-db/reduced-real.db`.  
- `tests/test_c6_grind_manifests.py` — runtime skip / `skipif` on missing grind manifests.  
- `tests/test_vp_cea_ingest.py`, `tests/test_vr7_transcription.py` — optional `docs-private` paths gated with `is_file()` continue/skip.  
- `tests/test_imcc_sp_extension.py:_ext4` — skip when gitignored EXT4 absent.  
- `tests/test_u0_vapour_manifest.py` — fixture-only CI mode + skipif for private inventory.  
- Live engine gates: MAGEMin/AlphaMELTS/VapoRock/ThermoEngine live smokes use `skipif` / runtime `pytest.skip` / `REGOLITH_RUN_*` env gates (`test_magemin_backend`, `test_alphamelts_backend`, `test_engine_worker_live_determinism`, `test_reduced_real_pt0_determinism`).  
- In-repo compilation source snapshots (Burcat / NASA Glenn / SGTE unary / ATCT / Robie-Waldbaum layout.txt) — not private-host coupled.  
- E2E server: `tests/e2e/conftest.py` skips unless `REGOLITH_E2E_REQUIRE_SERVER=1`.  
- Network: no default-CI `urlopen`/`requests.get` test hit outside gated e2e.

SWEEP: S16 | sites=9 | live=9 | P0=9 P1=0 P2=0 P3=0 | no-hit areas: janaf_compilation dir-skip; B1544/extract-mirror/B689/Kelley/cache_convert/c6/vp_cea/vr7/imcc_ext4/u0 skips; live engine REGOLITH_RUN_/skipif; in-repo compilation sources; e2e server gate; no ungated network

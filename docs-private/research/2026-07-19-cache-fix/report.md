# Cache reissue dual-review fixes

## TL;DR

- Closed the GRID engine-key collision by keying the effective vapor-pressure provider.
- Replaced provider-text authority with a typed backend-family contract plus row-carried, row-validated evidence metadata.
- Migrated strict PT-1 admission and MAGEMin accounting to the new namespace/authority schema.
- Replaced the b-043 toy migration fixture with captured legacy bytes, production DDL, `_insert_materialized()`, and production collision policy.
- Canonical focused acceptance: **776 passed, 14 skipped** (`-n0`); affected integration rerun: **300 passed, 10 skipped**.
- Golden changed; `corpus_version` did not.

## 1. GRID vapor-provider determinant

The effective runtime choice is exposed by `effective_vapor_pressure_provider_selection()` (`simulator/melt_backend/vaporock.py:203`), captured in point inputs (`scripts/grid_pregrind.py:714`), checked again after worker initialization (`scripts/grid_pregrind.py:1260`), and included in the v2 quantized determinant contract (`scripts/grid_pregrind_writer.py:282`, `scripts/grid_pregrind_writer.py:913`). This closes the case where identical thermodynamic inputs run through `vaporock` versus `activity-antoine` and produce different vapor outputs.

Teeth: `test_grid_vapor_provider_selection_splits_engine_keys` (`tests/test_grid_pregrind.py:2655`) asserts both expedited and cache-v2 hashes differ for the two effective selections. Worker-side mismatch refusal also prevents a queued key from silently executing under a changed runtime provider.

## 2. Typed F0 authority and reachable row validation

Real-engine adapters now use the closed `RealBackendFamily` enum and `RealBackendAuthority` marker (`simulator/melt_backend/base.py:34`, `simulator/melt_backend/base.py:42`). Genuine AlphaMELTS, ThermoEngine, MAGEMin, and MAGEMin-shadow adapters explicitly implement that marker; MRO/class-name authorization was removed. Cached-real configuration preserves the same typed routing identity (`simulator/backends.py:143`, `simulator/backends.py:194`) without making it sufficient for admission.

Every equilibrium payload carries `reduced-real-authority-v1` evidence (`simulator/reduced_real_determinism.py:2541`). The validator now consumes the payload, requires `melts` or `magemin` evidence, cross-checks typed backend family against namespace, and applies provider/backend checks to the row authority (`simulator/reduced_real_determinism.py:2423`). Key routing uses `_typed_backend_family()` rather than provider label text (`simulator/reduced_real_determinism.py:3424`). All store/read call sites pass the payload, making the checks reachable.

Teeth: `test_internal_backend_cannot_be_relabelled_real_by_provider_text` probes internal backends with diagnostic/unknown provider text, then tampers the namespace; admission rejects it as internal evidence. `test_same_name_stub_has_no_real_engine_authority` covers all four former class-name grants, and `test_same_name_alphamelts_stub_cannot_pass_row_authority` provides the end-to-end same-name C3 probe (`tests/test_reduced_real_pt0_determinism.py`). Cached-real tests additionally cover typed same-family replay and cross-family partitioning.

## 3. New-schema readers

Strict PT-1 admission reads `key.vapor_pressure_provider_selection` and `payload.authority.vapor_pressure`, validates the authority object, and rejects selection/authority mismatch (`simulator/grind_preflight.py:281`). MAGEMin population accounting classifies the new `magemin:` namespace and reads provider/role from row authority (`scripts/populate_reduced_real_cache.py:786`, `scripts/populate_reduced_real_cache.py:823`). Seed validation and physics-bucket backfill also pass the decoded row payload into the fail-closed authority validator (`scripts/seed_reduced_real_cache.py:277`, `scripts/backfill_physics_bucket.py:351`).

Teeth: `test_magemin_accounting_reads_new_namespace_schema` and `test_new_schema_pt1_gate_rejects_fallback_authority` (`tests/test_populate_reduced_real_cache_config.py:395`, `tests/test_populate_reduced_real_cache_config.py:406`), migrated put/merge strict-gate cases in `tests/test_populate_reduced_real_cache_driver.py`, and direct new-schema equilibrium-row regressions for seed/backfill (`tests/test_dose_cache_prep_scripts.py`, `tests/test_backfill_physics_bucket.py`).

## 4. Real b-043 conversion golden

`execute_cache_identity_migration_fixture()` now loads a captured legacy reduced-real row, materializes it through the production converter, executes production destination DDL, and calls the production insertion/collision path (`scripts/cache_convert.py:151`). The determinant identity excludes the legacy key digest (`scripts/cache_convert.py:1612`), while `_insert_materialized_with_collision_policy()` coalesces identical rows, quarantines conflicting materializations, and tombstones collided state (`scripts/cache_convert.py:2973`). The real quarantine table is created at `scripts/cache_convert.py:398`.

Fixture bytes: `docs-private/research/2026-07-19-cache-reissue/cache-convert-legacy-row.fixture.json`. Teeth: `test_cache_identity_migration_fixture_uses_real_converter_path` monkeypatches `_insert_materialized()` and asserts it is reached, then checks real-table collision outcomes (`tests/test_cache_convert.py:106`). A converter-path, DDL, uniqueness, or collision-policy regression now changes/fails b-043.

## Verification receipts

- Canonical requested suites plus seed-reader regression coverage: `.venv/bin/python -m pytest -n0 -q ...` → **776 passed, 14 skipped** in 305.62 s.
- Affected integration rerun (cached-real, reduced-real, population, GRID, converter, resilience): **276 passed, 10 skipped** in 57.98 s.
- Seed/backfill re-review regression run: **19 passed**.
- Typed-authority/replay/population/seed/backfill rerun after hostile re-review: **187 passed, 1 skipped**.
- Full affected integration rerun after removing class-name authority: **300 passed, 10 skipped**.
- Golden executable check: `.venv/bin/python scripts/regenerate_cache_identity_goldens.py --check` → exit 0.
- Golden byte identity: 64,675 bytes, SHA-256 `ecffaa10635b45a95428a0cad09b8d6d091fccfc811ba004234ff5b1f1e97636`.
- `git diff` and `git diff --cached` for `simulator/corpus_version.py` and `data/corpus_version.txt` are empty.
- Independent hostile re-review: `docs-private/reviews/2026-07-19-063-land/cache-impl-fix/independent-review.md` → **VERDICT: LAND**.

## Preserved PASS findings

- Corpus side-door removal: untouched by this fix; corpus-version inputs remain absent from engine identity.
- Optimizer `recipe_id`: optimizer implementation was not modified during this fix; canonical optimizer suites pass.
- Amount namespace: extensive-only correction was not changed.
- Engine minimality: only the missing effective vapor selection and typed backend-family routing were added; provider display text and row evidence remain outside determinant bytes.
- Determinism: golden generator check is clean and repeated reads are byte-identical.

## Golden impact

`docs-private/research/2026-07-19-cache-reissue/b-043-cache-contract.golden.json` changed to record the GRID vapor determinant, real converter/quarantine DDL, and real coalesce/conflict/missing-determinant outcomes. This is intentionally golden-affecting. No `corpus_version` bump was made.

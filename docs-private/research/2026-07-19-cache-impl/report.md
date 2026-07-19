# Cache-identity reissue implementation report

Date: 2026-07-19

## Outcome

Implemented the converged cache-identity architecture with the amount-invariance correction. Engine result identity is now routed by result namespace and built from effective input values. Engine/native/code/corpus versions, epochs, static digests, and fingerprints no longer invalidate engine results. Optimizer identity retains `corpus_version` as its sole version lever and uses value-only resolved recipe IDs.

This work is staged only. It does not commit, push, or change `data/corpus_version.yaml`. The controller must perform the single coordinated corpus regeneration after all golden-affecting 0.6.3 work lands.

## Implemented changes

### Engine result cache

- Rebuilt reduced-real canonical identity around routed namespaces, backend-boundary composition, temperature, pressure, fO2, commanded pO2, effective vapor-transport pO2, effective model/mode, MAGEMin database, vapor-provider selection, redox policy, sulfur input and inventory values, SulfSat mode/selection, and freeze/gate reference semantics.
- Removed engine version, code version, corpus version, data/source/module digests, provider fingerprints, and request-schema identity from exact engine lookup bytes and compatibility expansion.
- Kept engine version as queryable indexed PT1 metadata. Added regression coverage showing a version-only change produces the same key while independently written rows report their actual version.
- Removed corpus/data/request-schema comparisons from shard population and seeding paths. Canonical key/payload bytes and hashes remain validated; provenance metadata is copied without becoming an invalidation gate.
- Retained store-schema validation as database-format compatibility, not chemistry identity. Request schema is reporting/migration metadata.
- Removed epoch from successful grid-result uniqueness and selection. Epoch remains claim/operational provenance.

### Grid/cache-v2 completeness

- Unified `canonical_input_vector`, `expedited_key`, and binary `cache_v2_key_hash` on one quantized value map.
- Materialized normalized composition plus total submitted amount where extensive, T/P/fO2, commanded pO2, effective vapor-transport pO2, redox fields, model/run mode, and all finder controls.
- Excluded timeout, health, worker, epoch, and version fields from chemistry identity.
- Persisted `engine_result_namespace` in database metadata at creation/first materialization. Reopening even a prepared database under another engine namespace now fails before keys can be reused.
- Removed `corpus_version` and `cache_lever` from the cache-v2 manifest, immutable metadata, validator comparison, and legacy-manifest identity projection.

### Optimizer cache

- Added `RecipePatch.optimizer_recipe_id()`: resolve conditional values, serialize only canonical effective path/value entries, then hash. Recipe schema, allowlist, bounds, and prefix provenance cannot leak into optimizer identity.
- Routed evaluate and all identity-bearing staged-prefix call sites through the value-only recipe ID.
- Filtered canonical EvalSpec bytes to real run/scoring inputs, effective profile and physics-constraint content, and explicit `corpus_version`.
- Removed code version, resolved engine version, feedstock/static digests, dependency/source/provider fingerprints, and schema/bounds identities from optimizer key bytes.
- Preserved resolved engine version on EvalSpec/result metadata. ResultStore regression proves same-key overwrite/lookup remains version-neutral while the updated metadata is visible.

### DDL and migration evidence

- Updated grid `SCHEMA_SQL` and converter `DDL` so result uniqueness excludes engine epoch/version/config provenance and engine version has a normal metadata index.
- Added `scripts/regenerate_cache_identity_goldens.py`. Its default mode overwrites the golden; `--check` compares without writing.
- Generator executes both SQL strings in SQLite, records complete normalized `sqlite_schema`, serializes the executable manifest, checks logical parity, and invokes the converter-owned collision/rebuild fixture.
- Converter fixture proves version-only identical payloads coalesce, conflicting payloads quarantine, and missing determinants quarantine for rebuild.
- Legacy lossy converter rows remain explicitly typed `legacy-cleaned-fraction-only`; they are compatibility evidence, not promotable complete input identities. Promotion policy quarantines them when required amount/transport determinants are absent.

## Amount handling by namespace

| Namespace/path | Amount in identity | Reason |
|---|---:|---|
| alphaMELTS/PetThermoTools equilibrium composite | Yes | Output includes physically rescaled extensive phase/species quantities. |
| alphaMELTS combined grid/finder row | Yes | Extensive equilibrium fields coexist with finder output. |
| ThermoEngine equilibrium/grid | No | Intensive equilibrium output is invariant to total submitted amount. |
| MAGEMin equilibrium/freeze-gate | No | Native database projection is normalized and output is intensive. |
| Reduced-real freeze/gate curve | No | Curve identity uses boundary composition and reference transforms; amount is not consumed. |

Regressions independently scale otherwise identical grid compositions: alphaMELTS keys split; ThermoEngine keys remain identical. Reduced-real tests assert amount is present for the extensive alphaMELTS composite and absent from MAGEMin gate keys. MAGEMin `ig` versus `igad` still splits through the effective database determinant.

## Determinant and neutrality proof

Engine regressions cover:

- composition and backend-boundary projection;
- total amount for extensive namespaces;
- temperature, pressure, fO2, commanded pO2, and transport pO2;
- redox/ferric split, model, run mode, and finder controls;
- sulfur input and canonical inventory values, SulfSat mode/selection;
- MAGEMin database and vapor-provider selection;
- namespace routing and prepared-database cross-engine refusal.

Neutrality regressions cover engine/native version, code/corpus/data/source identity, timeout/health settings, epoch, and equilibrium provider implementation IDs after namespace routing. Corpus changes leave engine rows reusable but change optimizer EvalSpec keys and ResultStore eligibility. Recipe schema/allowlist/bounds/conditional-schema changes are neutral when resolved values are equal; concrete recipe or prefix values still split.

## Determinism gate evidence

The dedicated live gate was run with:

```text
REGOLITH_RUN_ENGINE_DETERMINISM=1 .venv/bin/pytest -n0 -q tests/test_engine_worker_live_determinism.py
```

Result: **3 passed, 1 skipped in 137.60 s**. Available ThermoEngine checks proved 24-point warm-versus-cold byte identity, two-slot pool-versus-cold byte identity, and pool-versus-cold identity under synthetic load/prior-call interleaving. The MAGEMin executable was unavailable, so its live 24-point gate skipped. Generic worker coverage separately exercises prior-call isolation and repeated-point byte equality; unavailable real engines remain an environment-gated acceptance item.

## Verification

- Optimizer EvalSpec/evaluate/ResultStore/pool/fidelity plus staged prefix identity: 556 passed, 4 skipped.
- Reduced-real, cached-real, grid triage/converter/rekey, engine-local, backfill, populate/seed focused groups passed in their final targeted reruns.
- Grid plus converter after commanded/transport pO2 completion: 87 passed, 9 skipped.
- Reduced-real/cached-real/rekey/config group: 155 passed, 10 skipped.
- Populate configuration/driver after metadata-neutral merge updates: 54 passed.
- Executable golden `--check`, Python compilation, and `git diff --check`: pass.
- Final combined focused gate: `830 passed, 14 skipped, 51 warnings` in 185.49 s.

## Golden/DDL/manifest files moved

- `tests/test_optimizer_evalspec.py`: b-042 executable canonical EvalSpec JSON/hash pin and sensitivity/neutrality assertions.
- `tests/test_optimizer_recipe.py`: executable optimizer recipe/cache pins after value-only recipe identity.
- `scripts/grid_pregrind_writer.py`: executable cache-v2 manifest and normalized grid DDL.
- `scripts/cache_convert.py`: executable normalized converter DDL and converter-owned migration fixture.
- `docs-private/research/2026-07-19-cache-reissue/b-043-cache-contract.golden.json`: regenerated complete normalized SQLite schemas, manifest, parity checks, and collision/rebuild outcomes.

The b-043 file is under the repository's ignored `docs-private/` tree and is force-staged intentionally because the architecture names it as the committed generated golden. The implementation report is force-staged for the same reason.

## Remaining risks

- MAGEMin and native alphaMELTS live determinism acceptance could not run without their available executables. ThermoEngine live acceptance passed; unavailable-engine gates should run on the controller/engine host before the coordinated corpus regeneration.
- Legacy pre-reissue rows lacking amount or transport pO2 cannot be safely promoted. They remain compatibility-only and the executable migration policy classifies them for rebuild.
- No corpus version bump was made. Mixing these staged constructors with an old coordinated optimizer corpus before controller regeneration is unsupported for release publication, though it no longer invalidates engine rows.

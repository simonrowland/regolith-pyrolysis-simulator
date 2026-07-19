# t-155 Tier-2 knob-vocabulary identity epoch — implementation report

Date: 2026-07-15
Branch: `t155-identity-epoch`
Base observed in worktree: `ca0428a`
Contract: ratified `SPEC.md` rev 2 plus `FOLD-reviews.md`

## Outcome

Implemented allowlist-v12 as one identity epoch. Empty-patch, sparse materialization,
and fully resolved lunar-mare default bytes stayed unchanged. Bounds, recipe, study,
conditional-subspace, EvalSpec, and manifest identities moved as required. No physics,
chemistry, ledger, mass-balance, objective, or resolved-default golden was regenerated.

## Per-section diff map

### §1 — demotions and removals

- `simulator/optimize/recipe.py`: applied the ratified 14 inert demotions, diagnostic
  demotion, six t-008 sub-mbar demotions, C2B absolute search demotion, and C4 canonical
  replay-path rename. Searchable rows moved from 84 to 70.
- `simulator/optimize/strategy/screen.py`, `simulator/optimize/knob_saturation.py`, and
  associated tests: structural consumers now use canonical v12 paths.
- No Part-II future-family allowlist rows were added.

### §2 — ranked A2 rows and delta materialization

- `simulator/optimize/recipe.py`: added the eight ratified A2 rows, including C2B/C5
  `target_delta_below_ceiling_C`; C6 remains absolute with no delta row.
- `simulator/campaigns.py`: materializes the C2B/C5 target once at phase entry from
  `min(furnace_max_T_C, process_high_C) - delta`; consumers read the stored value.
  No-delta defaults remain C2B 1480 C and C5 1575 C.
- Dual C2B absolute/delta authority refuses.

### §3 — conditional subspaces and propagation

- `simulator/optimize/recipe.py`: immutable guard/context records, stable subspace
  digests, explicit/manual guard resolution, inactive-child omission, and effective pins.
- `simulator/optimize/doe.py`: structured sampled candidates; fixed 64-dimensional
  C5-off and 70-dimensional C5-on streams; deterministic interleave; Sobol batch/index
  equality; stage-scoped sampling; continued indexed-LHC refusal.
- `simulator/optimize/evalspec.py`, `simulator/optimize/evaluate.py`: optional
  `conditional_subspace_digest`, fail-closed context validation, profile-aware sparse
  guard resolution, and C5-off execution pins applied before recipe-derived run options.
- `random_strategy.py`, `staged.py`, `fidelity.py`, and `study.py`: mask/digest/pins
  propagate through candidate metadata, cache lookup, pool requests, staged replay,
  search provenance, certification, save/import, and warm-start rebuilds.
- Patch bytes and recipe IDs omit inactive C5 coordinates; conditional identity remains
  in metadata/EvalSpec.

### §4 — scale metadata and mapping

- `simulator/optimize/recipe.py`: `linear`, `log`, `log10`, `ordinal`, and
  `zero-inflated` metadata.
- `simulator/optimize/doe.py`: live DOE/Random/fidelity/staged mapping honors the scale;
  anchored C5-on intervals are intersected with the positive decomposition-voltage tail.
- TPE/NSGA-II legacy linear/unconditional behavior is explicitly regression-pinned as
  the ratified deferral.

### §5 — aliases and generated manifest

- `simulator/optimize/recipe.py`: all 13 aliases normalize before validation; collisions
  refuse; minute input converts to canonical hours and runtime emission converts hours
  back to minutes.
- `simulator/campaigns.py`, `strategy/screen.py`, `knob_saturation.py`, and
  `web/routes.py`: canonical structural/runtime readers updated with required legacy
  fallbacks.
- Added `scripts/generate_optimizer_recipe_vocabulary.py` and generated
  `data/optimizer_recipe_vocabulary.json`; no forbidden future prefix is present.

## Identity acceptance matrix

| Surface | Result | Evidence |
|---|---|---|
| Empty patch | PASS, unchanged | `b"[]"` before and after |
| Sparse materialization | PASS, unchanged | `b"{}"` before and after |
| Fully resolved defaults | PASS, unchanged | 57,035 bytes; SHA-256 `69ed44ac69342dcbe542b66a6509903c242877c43fff9acc96e4f64bcb9f0f01` |
| Bounds digest | PASS, changed | exact old/new below |
| Empty recipe ID | PASS, changed | exact old/new below |
| Study search identity | PASS, changed | exact old/new below; count 70; dimensions 64/70 |
| EvalSpec bytes/cache key | PASS, changed | exact old/new below |
| Default C2B/C5/C6 targets | PASS, unchanged | focused endpoint tests |
| Non-identity runtime goldens | PASS, not rebaselined | clean-HEAD comparison reproduced the no-recipe golden backlog failure |

## Regenerated identity pins

| Pin | Old | New |
|---|---|---|
| Searched row count | `84` | `70` |
| Search-path SHA-256 | `6b1388b7909a135b18153bdda8503dc36911ed23520914cdbd2246e1c6827249` | `20fbfe6d34c8499b006fc44f10288f08ed9c723a2520a5eeffe500de39ed2fdc` |
| Allowlist version | `allowlist-v11` | `allowlist-v12` |
| O2 neutral allowlist version | `allowlist-v11` | `allowlist-v12` |
| Bounds digest | `32e9d2e945bd870a2af90d5fc46259dd7b724404d9066c4505d98921b8fd4252` | `2308bef69d19aa7679dda3f5d9838c91f7efd22eaaa16fc64cf5aa8b2cf63eb5` |
| Empty recipe ID | `defd94f2daff77987fe73577ffa5b87df51072d418794d41530accd88caf5907` | `fcb620b79a966a6412204c76bf87dc220e1c4cb38cda36c44f57d80cd4fa84b4` |
| Non-empty pO2 recipe ID | `7236cc9dee164395c000645da3846140bff1e17aa6772a057d26b0cfe3ae8801` | `6b828131b4a825d1f3d761c7b851863fc2ec17dccf8e07665d11ccaa8ae47b89` |
| Pinned EvalSpec bytes SHA-256 (1,477 bytes) | `f0fe52290d995a48a96cd5861686acfe6fd9b177d91b34c6495262206b544870` | `1fa41efd6ff891b1c71caf9ebd41de36e6b5f5896e62d6cc6c397a40abc93143` |
| Empty EvalSpec cache key | `be16be9b30f3b68f4889933efad83da6eb65b40cd80f7c5d16349a5f891e464b` | `872f467f22434f07b6ffa6e34f8d8d29d89620d4f38ae49afeb03a15d719af1d` |
| Study identity SHA-256 | `a8ffba282e43fecbd31cd1816c92fb843c40504666580a2ff81ee05a1c02855d` | `824a6522b1e802b3fa1fb2f0dd93e1b449930e4990756b0159b9436959187ef2` |
| C5-off subspace digest | not present | `7dfe16aff3f3ce553483953257b9fd3a18280b57b3f4af306342663a78e3a075` |
| C5-on subspace digest | not present | `ba10851113c96b4bcc0e52feecb66a80e640ddda38d4a6a21fd5f6fdbfc8bdc3` |
| Conditional Sobol stream SHA-256 | not present | `6f8e525bef21ad89bfe4d69dc5504aa6e247dc10d6033cba87513cdf24c36160` |
| Conditional LHC stream SHA-256 | not present | `da078800efa7a5e4d4a677b05fb522aaac3e5fa86e71ba24f8a7036a730ec5e8` |
| Manifest byte SHA-256 | not present | `7d4bd1a5ce7a9f1121646375cad3ba331f2b2ed0586525196abed84a2fcec8a6` |
| Manifest payload digest | not present | `d0462d34fcb19823f1f3dce7cf5064d18e12d45b7a8cf9ee55848f3bc3c4545c` |

Legacy/custom DOE fixtures whose semantics did not change retained their numeric streams;
synthetic test-only `hold_time_min` paths were renamed to neutral `hold_time_ticks` so the
new production alias normalizer does not rewrite custom schemas.

## Verification

- Review-fix regressions: `6 passed`.
- Final new/adjacent targeted batch: `5 passed`; canonical saturation/Morris batch:
  `8 passed`.
- Focused SPEC gate: `607 passed`, `2 failed`, `74 warnings` in 229.91 s. Both failures
  are pre-existing/environmental: the process-pool timeout test falls back to serial under
  the managed sandbox, and the no-recipe golden mismatch reproduces on untouched HEAD.
- Definitive full gate: `pytest tests/ -v -n auto` — `5,347 passed`, `132 skipped`,
  `2 xfailed`, `45 failed`, `20 errors`, `976 warnings` in 1,633.74 s.
- Reference comparison: versus the brief's approximate `~42 failed + 14 @serial errors`
  at `20cb18c`, this checkout reports `+3 failed` and `+6 errors`. Every additional item
  is in the existing documented backlog, managed-sandbox process-pool limitation, or the
  cascading serial validation-map fixture error set. No failure names a t-155 identity,
  alias, manifest, DOE, EvalSpec, conditional-context, saturation, or Morris assertion.
  Delta attributable to this chunk: **zero new failures**.
- Full log: `docs-private/research/2026-07-15-t155-epoch/FULL-SUITE-FINAL.log`.
- `git diff --check`: clean.

## Independent review

One independent diff review found four issues, all folded before the final gate:

1. Conditional metadata was not retained in study search provenance/warm starts.
2. Sparse manual guards ignored active validated profile defaults.
3. Anchored C5-on intervals could cross below the positive-tail floor.
4. The manifest described the hours-to-minutes runtime transform backwards.

Regression coverage was added for each affected contract. The review made no edits.

## Scope audit

- Data edits: only generated `data/optimizer_recipe_vocabulary.json`.
- No Part-II future-family entries.
- No `setpoints.yaml`, physics, chemistry, ledger, objective, or runtime golden edits.
- Controller commit intentionally not created by this worker.

## FIX-ROUND — conditional identity refusal and propagation

Date: 2026-07-16

### Outcome

Closed all three P0 conditional-identity holes, both P1 findings, and the cheap
P2.1/P2.2/P3.1 findings without changing any ratified identity pin, generated
vocabulary row, data file, or runtime golden.

### Finding → fix → test

| Finding | Fix | Acceptance coverage |
|---|---|---|
| P0.1 guarded child identity bypass | `RecipePatch.recipe_id()` now resolves through `RecipeSchema.resolve_conditional_patch()` before hashing. Standalone C5 or bubbler children without a parent/default/effective context refuse instead of acquiring an identity. Recipe-file normalization uses the same resolution boundary. | Direct C5 and bubbler child-only `recipe_id()` refusals; recipe-I/O child-only refusal; inactive parent+child resolution warning tests. |
| P0.2 prefix exemption entered full cache identity | Conditional scope is runtime-enumerated. Prefix contexts require unique, non-C5 `prefix_stage_ids`; the prefix builder verifies them exactly against the executed stage set and returns `PrefixEvalSpec` directly. The ordinary full EvalSpec/evaluate path refuses prefix context. Staged full evaluations never reuse prefix-only metadata, while prefix proof now survives search provenance/journal/save/warm-start metadata. | Arbitrary scope, missing proof, C5-containing proof, proof/executed-set mismatch, and full-EvalSpec reuse refusals; positive direct-`PrefixEvalSpec` construction; staged prefix cache/replay and provenance propagation tests. |
| P0.3 bubbler metadata contradicted patch | Supplied masks and effective pins are checked against guard state derived from the validated/resolved patch plus effective parent values before EvalSpec construction. The resolved patch becomes the evaluated/hashed patch. | Active-patch/inactive-mask and inactive-patch/active-mask refusals for C2B, C3, C4, and C6. |
| P1.1 zero-dimensional batch duplication | Conditional batch sampling now refuses requests above the one canonical sample capacity of a zero-dimensional subspace, matching indexed sampling. | Two-sample off/on capacity succeeds; the third sample raises `ConditionalSubspaceExhausted`. |
| P1.2 propagation matrix | Added mask/digest/pin/scope/prefix-proof assertions across staged candidates, prefix cache round-trips, provenance/journal/save metadata, fidelity fast/high task pairs, imported cache EvalSpecs, and recipe-I/O refusal. | New tests in `test_optimizer_staged.py`, `test_optimizer_fidelity.py`, `test_optimizer_import_bundle.py`, and `test_recipe_io.py`; the focused EvalSpec/DOE suites cover construction and sampling. |
| P2.1 obsolete structural aliases | Tap-truncation structural consumers now use canonical `duration_hr` paths. | Canonical-path set assertion. |
| P2.2 silent inactive-child discard | Inactive legacy/manual guarded children emit exactly one `InactiveConditionalChildWarning` before removal. | C5 and bubbler warning-count/resolved-patch tests. |
| P3.1 unreachable expressions | Removed the three bare names after `_optional_float()`'s terminal return. | Module compilation plus focused DOE suite. |

### Verification receipts

- Final contract gate (`-n0`): **421 passed, 16 warnings** in 299.75 s. This
  includes all five mandated suites plus the added fidelity, import-cache, and
  recipe-I/O propagation tests.
- Explicit byte-stability recheck: **2 passed**. Empty patch remains `b"[]"`,
  sparse empty remains `b"{}"`, fully resolved defaults remain 57,035 bytes at
  SHA-256 `69ed44ac69342dcbe542b66a6509903c242877c43fff9acc96e4f64bcb9f0f01`,
  and the pinned EvalSpec bytes/cache key remain unchanged.
- Broad eight-suite diagnostic: **507 passed, 1 failed, 38 warnings** in 357.20 s.
  The sole failure is the previously documented unrelated
  `test_no_recipe_run_matches_committed_golden_text` saturation-interval mismatch;
  this fix round did not modify or regenerate that golden.
- `git diff --check` and `git diff --cached --check`: clean.
- No new allowlist rows, no new `data/` edits, and no golden edits were made in
  this fix round.

### Scope note

The ratified `SPEC.md` named by the dispatch was absent from this worktree and
the main checkout. Implementation followed the dispatch text and the exact
required fixes in `implreview-codex.md`, using fail-closed mismatch semantics.

BLOCKED: staged for controller commit

READY: docs-private/research/2026-07-15-t155-epoch/IMPLEMENTATION-REPORT.md

## FIX-ROUND-2 — closing-review residuals

### Outcome

Closed both residuals from `closing-codex.md` without moving a pinned identity,
adding an allowlist row, or changing data/golden artifacts.

### Residual → closure → test

| Residual | Closure | Acceptance coverage |
|---|---|---|
| Invented-stage prefix proof hole | The study prefix-replay boundary now rebuilds the selected staged strategy table from the authoritative topology and profile allowlist. It requires `prefix_depth == stage_index` and an exact ordered match between candidate prefix IDs and the rebuilt stage-table prefix before prefix patch, `PrefixEvalSpec`, cache-key, or store lookup construction. | `test_staged_prefix_replay_refuses_invented_stage_before_identity` submits `("NOT_A_STAGE",)` with self-consistent conditional metadata and asserts refusal before the prefix builder or store lookup is reached. |
| Missing explicit two-stream end-to-end matrix | Added one two-row Sobol matrix for the no-MRE pinned-zero and MRE-on streams. Each row flows through sampler → public `evaluate()` → `EvalSpec` → `StudyRecord`, with patch, mask, digest, effective pins, C5 runtime state, cache key, recipe ID, and provenance round-trip assertions. | `test_t155_two_stream_sampler_evaluate_evalspec_study_identity_matrix`. |

### Verification receipts

- New residual tests: **2 passed** in 2.31 s.
- Required `-n0` optimizer recipe/EvalSpec/DOE/staged suites plus the new study
  matrix: **377 passed, 16 warnings** in 276.95 s.
- Closing-review auxiliary t-155 fidelity/import/study/recipe-I/O selectors:
  **6 passed, 192 deselected** in 2.00 s.
- Generated vocabulary manifest: **1 passed** in 0.33 s.
- Explicit byte-stability recheck: **2 passed** in 1.16 s. Empty patch remains
  `b"[]"`; sparse empty remains `b"{}"`; resolved defaults remain SHA-256
  `69ed44ac69342dcbe542b66a6509903c242877c43fff9acc96e4f64bcb9f0f01`;
  empty recipe ID remains
  `fcb620b79a966a6412204c76bf87dc220e1c4cb38cda36c44f57d80cd4fa84b4`;
  pinned EvalSpec bytes/cache key remain unchanged.
- Ruff was unavailable in the supplied venv (`No module named ruff`); all
  changed Python surfaces imported and executed under the passing suites.
- `git diff --check` and `git diff --cached --check` are clean. The only staged
  `data/` path remains `data/optimizer_recipe_vocabulary.json`; no golden,
  snapshot, or fixture path is staged.

BLOCKED: staged for controller commit

READY: docs-private/research/2026-07-15-t155-epoch/IMPLEMENTATION-REPORT.md

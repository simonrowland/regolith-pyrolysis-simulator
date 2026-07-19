# Cache F0 row-admission closure

## TL;DR

- Closed the remaining C3 stub-as-real bypass at row admission.
- Authority now resolves from a closed exact backend identity into `RealBackendFamily`; row evidence/family strings cannot grant authority.
- Provider IDs and roles require exact family tuples; builtin-wrapped real execution remains valid only after backend validation.
- Hostile arbitrary, same-name, internal-analytical, builtin, and unknown-provider rows are rejected.
- Focused verification: 216 passed/10 skipped; golden check clean.

## Exact bypass closed

`validate_reduced_real_equilibrium_record_key()` previously accepted row-carried
`evidence_class=melts` and `backend_family=alphamelts` strings as the authority
source. Its builtin-provider branch also returned success whenever a row's
backend spelling was not recognized as internal analytical. The validator now
resolves authority first through `_row_validated_backend_family()`'s closed,
exact adapter identity tuples and rejects absent, arbitrary, same-name-only, and
internal identities before considering descriptive strings
(`simulator/reduced_real_determinism.py:2451-2471`, `:2508-2523`).

After typed family resolution, evidence class, backend family, and namespace
must all equal values derived from the resolved `RealBackendFamily`
(`simulator/reduced_real_determinism.py:2472-2485`). Provider metadata no
longer supplies an early-success path: resolved, authoritative, fallback, and
role fields must form an exact family-specific tuple
(`simulator/reduced_real_determinism.py:2486-2497`, `:2526-2569`). This keeps the legitimate
`builtin-backend-equilibrium` wrapper usable for a row-validated AlphaMELTS or
ThermoEngine backend while a same-name non-internal label cannot reach success.

## Typed authority production

`_equilibrium_record_authority()` still derives the family from the live typed
backend, but now serializes backend identity through
`_typed_backend_identity_for_authority()` rather than name/MRO helpers
(`simulator/reduced_real_determinism.py:2582-2604`, `:3477-3499`). A non-typed
backend therefore serializes as internal analytical even if its class or
provider label resembles a real engine.

## Hostile probe teeth

`test_row_authority_strings_cannot_promote_stub_to_real_engine` hand-builds
hostile rows with arbitrary real labels, a fake same-name AlphaMELTS class,
builtin provider plus that fake class, explicit internal analytical identity,
unknown provider fields, a cross-family provider, and the wrong provider role.
Every row is rejected; the same probe protects the legitimate typed-backend plus
builtin-wrapper path (`tests/test_reduced_real_pt0_determinism.py:1916-2000`).

`test_typed_real_backend_produces_row_validated_authority` proves the positive
case: a `RealBackendAuthority` carrying `RealBackendFamily.ALPHAMELTS` produces
the canonical row identity and passes admission
(`tests/test_reduced_real_pt0_determinism.py:2003-2034`). Existing provider-text
and same-name-stub regressions remain active immediately above these tests.

## Verification

- `.venv/bin/python -m pytest -n0 -q tests/test_reduced_real_pt0_determinism.py tests/test_reduced_real_cache_interpolation.py tests/test_cached_real_backend.py tests/test_cache_convert.py tests/test_populate_reduced_real_cache_config.py tests/test_populate_reduced_real_cache_driver.py tests/test_dose_cache_prep_scripts.py tests/test_backfill_physics_bucket.py` — **216 passed, 10 skipped**.
- `.venv/bin/python scripts/regenerate_cache_identity_goldens.py --check` — pass; the b-043 golden remained unchanged.
- These runs cover reduced-real authority/replay/interpolation, cached-real live fill, cache conversion/collision, population, seed/backfill readers, and the new hostile probe.

## Preserved closures

No production files outside reduced-real authority validation/serialization were
changed in this F0 closure. Existing collision/quarantine, corpus side-door,
value-only `recipe_id`, amount namespace, deterministic replay, and b-043
conversion teeth remain green in the focused suites and golden check above.

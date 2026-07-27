# Staged yield-disposition fix 5 — final fail-closed residuals

## TL;DR

- Stage 0 carbon-cleanup debit records carry typed origin; the recorder validates and consumes the stamp without account-name inference.
- Nonzero condensation snapshots require closing typed-origin and attribution evidence; cleanup/main splits also close against physical species.
- The roundoff regression now crosses the real unresolved-origin boundary: `0.99x` closes and `1.01x` raises.
- Focused, canonical Mars/CI/lunar, pool, Stage 0, runner-envelope, and mol-native gates pass; staged only, no commit.

## Review closures

### Stage 0 producer origin

All three carbon-cleanup producers now emit three-field debit records:
`(account, species_kg, material_origin)`. Sulfate and cation-sulfate feeds are
`feedstock`; carbon inventory and Boudouard process gas are `reagent`.

`_record_stage0_carbon_cleanup_transitions` rejects missing or invalid debit
origin before `load_external`, passes the explicit stamp through unchanged,
and strips the stamp only when constructing the provider's existing two-field
debit payload. The regression changes the process-gas debit stamp to
`feedstock` and proves that stamp, not the account name, reaches
`load_external`; a missing stamp raises `MaterialOriginError` before a load.

Runtime AST sweep:

- 9 `load_external` / `load_external_mol` calls.
- 0 unstamped runtime calls.
- 0 `material_origin` expressions derived from account or name.
- Sulfate, cation-sulfate, and Boudouard sibling debit records all have literal
  typed origin.

### Condensation snapshots

Each nonzero physical condensation snapshot now requires:

- a typed-origin account/element mapping containing only `feedstock` and
  `reagent`;
- finite, nonnegative origin atom balances that close to the physical snapshot;
- a valid `tracked` or `pool_ratio` attribution method per nonzero element.

After campaign-delta extraction, both cleanup and main typed atom portions must
close against their physical species portions. Missing typed-origin evidence,
missing attribution evidence, malformed origin labels/balances, and temporal
snapshot inconsistencies raise `OriginUnresolvedError`. An empty attribution
method set no longer defaults to `tracked`.

### Roundoff boundary

The staged test now combines an unresolved initial Na remainder with a typed
external feedstock complement. The absolute basis keeps the seed above the
ledger's kg dust floor while exercising the normalized `5e-14` boundary:

- `0.99 * limit`: accepted.
- `1.01 * limit`: `OriginUnresolvedError`.

## Verification

- `tests/test_yield_disposition.py` plus the new Stage 0 source-origin
  regression: **19 passed in 22.80s**.
- Mars/CI typed-origin closures, lunar schema closure, construction and
  serialization failure envelopes, final-state mol-native guard, artifact
  kg-mutation guard, and ledger API: **15 passed in 54.21s**.
- Full builtin Stage 0 provider suite: **24 passed in 46.05s**.
- `git diff --check`: passed.
- No Grok `APPROVE-WITH-FIXES` tail was persisted in the research directory.
- No golden regenerated; no commit or push performed.

READY: docs-private/research/2026-07-25-s2-yielddisp/fix5-report.md

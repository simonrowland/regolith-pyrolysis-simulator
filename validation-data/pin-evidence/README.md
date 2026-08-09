# Validation-pin evidence artifacts

Small, **publishable**, **git-tracked** records that back residual pins in
`data/vapour_rail_validation_pins.yaml`.

## Why this root

`validation-data/` already holds the time-series validation lake (CSV +
catalog). Pin evidence is the same class of object: checkable external
comparison data that a fresh clone must resolve. Putting it under
`docs/references/` would mix literature bibliographies with measured residual
records; `docs-private/` is machine-local and **must not** be the primary
evidence path (see P1-3).

## What belongs here

One YAML record per residual that needed a distilled public artifact. Each
record carries the facts a reader must be able to check without the private
narrative report:

- species and validation family (`vaporock` / `mass_spec` / …)
- pinned residual value and prior residual (when a regrind)
- comparison basis, method, T / pO2 context, date, source id

Private research narratives may still exist under `docs-private/research/` and
may be listed on the pin as `internal_notes_refs` — the promotion-evidence
guard does **not** require those.

## Guard

`tests/test_rail_conformance.py::test_validation_tiers_and_promotions_have_external_evidence`
requires every primary `evidence_refs` path to:

1. exist on disk, and
2. be **tracked by git** (`git ls-files`).

Untracked on-disk paths (the old `docs-private/` accident) fail.

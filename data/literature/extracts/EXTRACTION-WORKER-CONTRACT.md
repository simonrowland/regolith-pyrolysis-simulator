# Extraction worker contract (t-509 OCR campaign)

Binding companion to `SCHEMA.md` / `literature_extract.v1`. OCR extraction
workers that emit extracts into `data/literature/extracts/` must satisfy every
rule below. The t-508 validator is the machine gate; tranche review is the
human gate.

## Emit shape

1. **One YAML file per literature source** at
   `data/literature/extracts/<source-id>.yaml` (`source_id` = filename stem).
2. **Verbatim values only** — no Antoine refits, no cross-source averages, no
   model fill-in. Unit conversions must show the conversion.
3. **Typed observations** from the closed set in `SCHEMA.md`
   (`psat_series`, `gibbs_table`, `activity_coefficient`, `alpha`,
   `rate_series`, `transition_point`), including partial shapes per the t-507
   scope ruling.
4. **Locator on every observation** and **on every equipment-metadata field**
   (page / table / figure / paragraph / …). Inferred geometry must set
   `inferred: true` and state the derivation.
5. **Equipment evidence is first-class** (owner 2026-08-01 PROVENANCE-CHAIN):
   experimental sections + apparatus figures/schematics are OCR targets
   alongside result tables. Chain:
   archived PDF → retained OCR artifact
   (`docs-private/research/ocr-artifacts/<source-id>/`) → extract field with
   locator → catalog row citing `(source_id, observation_id)`.

## Fidelity samples (t-510 — required from day one)

Every OCR-produced extract that carries ≥1 observation **must** include a
non-empty `fidelity_samples` list. These are **not optional** for t-509
outputs; the pilot DRAFT corpus is grandfathered via
`_fidelity_pre_policy_allowlist.yaml` (`ENFORCED_FOR_NEW`), but **new extracts
are never added to that allowlist**.

### What a sample is

A reviewer-verified line item the tranche review checked against the source
page / OCR artifact:

```yaml
fidelity_samples:
  - species: SiO
    observable: alpha
    observation_id: wetzel_gail_2013_sio_arrhenius
    field: alpha_form          # optional key inside values
    # For series points, pin with either:
    #   T_K: 1800.0
    #   index: 3
    value:
      type: arrhenius
      A: 0.52
      B_K: 3685.0
    locator:
      page: 7
      figure: "4a"
      note: "α(T) Arrhenius fit coefficients as printed"
```

Minimum content:

| Field | Rule |
|-------|------|
| `species` | Canonical id under `species:` |
| `observable` and/or `observation_id` | Locates the observation |
| `value` | Exact stored value (scalar or structure) the reviewer read |
| `locator` | Page/table/figure/paragraph (or OCR artifact pointer) — **required** |
| `T_K` / `index` | When pinning a series point |
| `field` | When pinning one key inside `values` |

Path-based samples (`path` + `value` + locator/note) remain valid for
mechanical pins, but **OCR workers should emit the structured form** so
tranche review can re-check against the page without reverse-engineering
paths.

### How many

At least **one** sample per extract with observations. Prefer **N ≥ 1 per
major observation family** present (e.g. one α pin + one P_sat pin when both
exist). More samples catch more drift classes; one is the hard floor.

### Tranche review obligation

Before adoption (`review_status: reviewed`):

1. Reviewer opens the source page / OCR artifact at each sample's locator.
2. Confirms the sample `value` matches the publication (within stated
   transcription rules).
3. Confirms the same value is what the extract stores (the checked-in match
   test also asserts this automatically).

An extract without samples fails the validator. A sample that no longer
matches extract content fails
`tests/test_literature_extracts.py::test_fidelity_sample_matches_extract`.

## Do not

* Invent uncertainty, equipment dimensions, or numeric values not in the
  source.
* Park equipment fields at the observation top level (must live under
  `equipment:` with per-field locator).
* Use absolute/machine-local `provenance_path` values.
* Add a new extract's `source_id` to `_fidelity_pre_policy_allowlist.yaml`
  (shrink-only; closed set is hash-pinned).
* Average competing observations or drop losers — retain and let
  `extract_merge` surface `disagreement_dex`.

## Validation entry points

```bash
python tools/validate_literature_extracts.py
python tools/validate_literature_extracts.py --check-fidelity-match
pytest -n0 tests/test_literature_extracts.py -q
```

## Provenance chain (recap)

```
reference library PDF
    → docs-private/research/ocr-artifacts/<source-id>/  (page images + MinerU)
    → data/literature/extracts/<source-id>.yaml
         · observation.locator
         · equipment.<field>.locator
         · fidelity_samples[*].locator   ← reviewer pin
    → catalog / validation anchors cite (source_id, observation_id)
```

Nothing enters an equipment or fidelity field without a locator.

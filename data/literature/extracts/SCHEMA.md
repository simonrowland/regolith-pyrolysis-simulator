# Literature extract store — schema (`literature_extract.v1`)

Evidence layer for vapour-rail acquisition (t-508). **One YAML file per literature
source** at `data/literature/extracts/<source-id>.yaml`. Per-species views,
species×source coverage tables, and cross-source consistency reports are
**derived** (`tools/extract_merge.py`); they are not edited by hand.

**Runtime never reads this tree.** Catalog fits and validation anchors may
*cite* `(source_id, observation_id)` pairs; the extract store remains the
verbatim evidence layer.

Binding policy: `docs-private/design/2026-07-30-vapour-rail-unification/VALUE-PRECEDENCE.md`.

## File layout

| Path | Role |
|------|------|
| `data/literature/extracts/<source-id>.yaml` | One extract per literature source |
| `data/literature/extracts/_source_priority.yaml` | Per-family `source_priority` lists (DATA, not code) |
| `data/literature/extracts/SCHEMA.md` | This document |
| `tools/validate_literature_extracts.py` | Fail-loud validator CLI |
| `tools/extract_merge.py` | Derived by-species view + coverage + consistency |

Files whose basenames start with `_` are store policy / derived scaffolding,
not source extracts.

## Root object

```yaml
schema_version: literature_extract.v1
source_id: costa-jacobson-2015          # must match filename stem
source:
  citation: "..."                        # required
  doi: "10.…"                            # optional but preferred
  url: "https://…"                       # optional
  version: null                          # database / edition version when applicable
  year: 2015                             # optional
extraction:
  method: manual_from_draft              # required free text
  date: "2026-08-01"                     # ISO YYYY-MM-DD required
  worker: t508-store-gk                  # required
  provenance_path: docs-private/...      # optional; repository-relative only
review_status: draft                     # draft | reviewed | rejected
# Optional store-local overrides (normally use _source_priority.yaml)
source_priority: {}
# Required when any observation is present (t-510 / owner fidelity-gate)
fidelity_samples:
  - path: species.Fe.observations[costa_2015_fe_alpha_kems].values.alpha
    value: 0.02
    note: "DRAFT alpha-kinetics Costa Fe KEMS pin"
    locator: { record: costa_jacobson_2015_fe_olivine_kems }
species: {}                              # map: canonical U0 / manifest id → species block
```

### `review_status`

- `draft` — pilot / tranche-pending (default for migrations).
- `reviewed` — tranche accepted; append-only thereafter (no silent rewrite of values).
- `rejected` — retained for audit; merge tool may exclude from operative views.

### `fidelity_samples` (required when observations present)

Each sample is a located, source-backed check that a stored field matches the
DRAFT / publication:

| Field | Required | Meaning |
|-------|----------|---------|
| `path` / `field_path` | yes | Dot path into the extract |
| `value` / `draft_value` | yes | Expected / stored value |
| `locator` / `source_locator` or `note` | yes | Where in the source this was checked |

Validator refuses extracts that carry observations but lack a non-empty
`fidelity_samples` list.

## Species block

Keys are **canonical species ids** from `data/vapour_rail_u0_manifest.yaml`
(and oxide / gas ids used by the rail inventory). Unknown ids are allowed in
draft extracts (**validator warns**; does not refuse) so partial acquisition is
not blocked.

```yaml
species:
  Fe:
    observations:
      - observation_id: costa_2015_fe_alpha_kems   # unique within the file
        type: alpha                                 # see observation types
        locator: { figure: "3", page: 12 }          # required on every observation
        T_range_K: [1700.0, 1800.0]                 # optional [min, max]
        phase: solid_solution_olivine               # optional free text
        standard_state: "…"                         # optional free text
        regime: kems_effusion                       # optional; regime is NOT a conflict
        units: dimensionless                        # required when values present
        uncertainty:                                # FIRST-CLASS (owner 2026-08-02)
          note: "high-side of measured D(Fe+) band"
          alpha_range: [0.011, 0.02]                # retain stated uncertainty verbatim
        values: { alpha: 0.02 }                     # VERBATIM; no fitted coefficients
        equipment: {}                               # optional; see equipment metadata
```

## Observation types

Exactly one of:

| `type` | Payload intent |
|--------|----------------|
| `psat_series` | Temperature–pressure (or log-p) points as published |
| `gibbs_table` | Formation / free-energy / heat-capacity table or CEA segments |
| `activity_coefficient` | γ or a as published (KEMS ion ratios, etc.) |
| `alpha` | Evaporation / condensation coefficient (HKL α), including `alpha_form` Arrhenius |
| `rate_series` | Weight-loss, ion-current, or flux time series (for HKL inversion) |
| `transition_point` | Melting / boiling / triple / congruent-transition fixed points |

**Verbatim only.** Extracts store published numbers (and arithmetic that merely
converts units with the conversion shown). No Antoine/refit coefficients invented
by the migrator, no averaging across sources, no model-derived fill-in.

Partial shapes are in scope (t-507 scope ruling): single-species P_sat series,
derived γ without raw ion time series, single-species rate series, relative/
uncalibrated series with bound-not-point semantics.

### Values payload

- `values` is **required** on every observation.
- Empty `{}` / null-only payloads are **refused**.
- Pointer / bound-not-point / competing-observation rows must carry explicit
  structured content (e.g. `semantics: bound_not_point_ordering`,
  `semantics: competing_observation_do_not_average`).

### Uncertainty (first-class)

Each observation **retains stated uncertainty verbatim** under `uncertainty`
(string or mapping). Do not invent uncertainty; do not drop DRAFT-stated
envelopes / ranges / ± terms. `tools/extract_merge.py` **propagates** uncertainty
into the by-species view (`observation.uncertainty`, group
`winner_uncertainty`, group `uncertainties[]`) so catalog fits inherit an error
budget input.

## Locators (per observation AND per equipment field)

A locator is a mapping with at least one non-empty location key among:

- `page`, `published_page`, `pdf_page_index`
- `table`, `figure`, `paragraph`, `section`, `equation`
- `line_range` (e.g. thermo.inp line range)
- `note` (prose locator when the above do not apply — allowed, but prefer structured keys)
- `source_path`, `record`

Every **observation** requires a `locator`.  
Every **equipment-metadata field** that is present requires its **own** `locator`
(often experimental-section prose or apparatus figures, not result tables).  
Derived geometry must set `inferred: true` (or any truthy inferred flag) and
state the derivation in `inference` or `note`.

## Equipment metadata

Optional block on an observation. Field names (owner-ratified 2026-08-01):

| Field | Meaning |
|-------|---------|
| `orifice_area` | Effusion orifice area (or diameter series for multi-orifice Motzfeldt) |
| `clausing_factor` | Orifice Clausing factor |
| `sample_surface_area` | Sample / melt free surface area \(A_s\) |
| `cell_material` | Cell wall material (fixes effective pO₂ boundary) |
| `chamber_pressure` | Ambient / chamber pressure during the run |
| `multi_orifice_series` | Boolean or series descriptor for multi-orifice Motzfeldt studies |

Each present field is an object with **value + locator** (both required):

```yaml
orifice_area:
  value: 3.14e-7
  units: m2
  locator: { page: 4, figure: "1b", paragraph: "experimental" }
  inferred: false
# or inferred:
sample_surface_area:
  value: 1.0e-4
  units: m2
  locator: { page: 4, paragraph: "crucible ID stated as 10 mm" }
  inferred: true
  inference: "area = π (d/2)^2 from stated crucible inner diameter 10 mm"
```

**Validator refuses**:

- equipment field lacking a locator
- equipment field lacking a non-null `value`
- bare scalar equipment fields
- recognized equipment field names parked at the observation top level

## Value precedence (store policy)

`_source_priority.yaml` holds per-observable-family ordered source-id lists
(evaluated compilations → primary measurement → secondary → estimate). First
available wins; **never average**. Losers stay as competing observations.

**Fail-closed:** missing / empty / incomplete family lists are validation
errors. Unlisted sources in a competing group cannot be crowned winner by
lexical filename order — merge emits no winner for that group.

### Typed-observable identity (not just type+regime)

Phase, standard-state, property/quantity subtype, and units are part of the
observable key. Melting vs boiling, gas vs solid Gibbs, KEMS vs Langmuir α are
**different observables**, not conflicts.

`tools/extract_merge.py` surfaces:

- `disagreement_dex` — max log10 spread among **comparable positive scalars**,
  one scalar per source, only for multi-source groups with overlapping
  `T_range_K` (open/missing ranges treated as overlapping). Singletons →
  `null`. No linear-span invent; no within-row multi-field bag dex.
- `winner_uncertainty` / `uncertainties[]` — error-budget inputs for catalog fits.
- **Cross-source consistency report** (`--consistency`) — auto-computed
  disagreement table alongside coverage (no hand curation).

Coverage `found` is **payload-aware**: empty/null observations do not count.

## Append-only after review

After `review_status: reviewed`, value-bearing fields must not be silently
rewritten. Corrections append a new observation (or a new extract revision with
explicit `source.version`) and leave the prior row.

## Validation entry point

```bash
python tools/validate_literature_extracts.py
python tools/validate_literature_extracts.py data/literature/extracts/some-source.yaml
python tools/validate_literature_extracts.py --show-warnings   # unknown species ids
```

Exit non-zero on any refusal rule.

## Merge / derived views

```bash
python tools/extract_merge.py --by-species -o build/literature_by_species.yaml
python tools/extract_merge.py --coverage -o build/literature_coverage.yaml
python tools/extract_merge.py --consistency -o build/literature_consistency.yaml
python tools/extract_merge.py --by-species --coverage --consistency --outdir build/literature
```

Coverage is species × source → `found` / `absent` relative to the U0 manifest
(when the manifest is available) or the union of extract species keys.
Consistency is the auto-computed multi-source disagreement report.

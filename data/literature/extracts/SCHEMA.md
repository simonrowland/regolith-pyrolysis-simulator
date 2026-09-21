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
| `data/literature/extracts/_fidelity_pre_policy_allowlist.yaml` | ENFORCED_FOR_NEW fidelity-sample allowlist (shrink-only) |
| `data/literature/extracts/SCHEMA.md` | This document |
| `data/literature/extracts/EXTRACTION-WORKER-CONTRACT.md` | t-509 OCR extraction worker contract (fidelity samples day-one) |
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
# Fidelity samples (t-510 gate) — required for NEW extracts; see policy below
fidelity_samples:
  # Preferred structured form (OCR / reviewer-verified):
  - species: Fe
    observable: alpha
    observation_id: costa_jacobson_2015_fe_olivine_kems
    # Optional series pin: T_K: 1750.0  OR  index: 0
    field: alpha                         # optional key inside values
    value: 0.02
    locator: { page: 12, figure: "3" }   # required on structured samples
  # Path-based form (pilot DRAFT migrations; still accepted):
  # - path: species.Fe.observations[costa_jacobson_2015_fe_olivine_kems].values.alpha
  #   value: 0.02
  #   note: "DRAFT alpha-kinetics Costa Fe KEMS pin"
  #   locator: { record: costa_jacobson_2015_fe_olivine_kems }
species: {}                              # map: canonical U0 / manifest id → species block
```

### `review_status`

- `draft` — pilot / tranche-pending (default for migrations).
- `reviewed` — tranche accepted; append-only thereafter (no silent rewrite of values).
- `rejected` — retained for audit; merge tool may exclude from operative views.

### `fidelity_samples` (t-510 gate)

Each sample is a **reviewer-verified** line item checked against the source /
OCR artifact: the stored value still matches what the reviewer read at the
locator. Samples are the extraction-drift fuse — a bad regeneration or silent
rewrite of a pinned field turns the checked-in match test RED.

#### Sample shapes

**Structured (preferred for OCR / new extracts):**

| Field | Required | Meaning |
|-------|----------|---------|
| `species` | yes | Canonical species id under `species:` |
| `observable` and/or `observation_id` | yes (≥1) | Observation type and/or id |
| `value` / `draft_value` | yes | Expected value (scalar or structure) |
| `locator` / `source_locator` | yes (exactly 1) | Where in the source/OCR this was checked |
| `T_K` / `T` or `index` | no | Pin a series point by temperature or list index |
| `field` / `value_key` | no | Key inside `values` (or the series point) |
| `rel_tol` | no | Finite numeric magnitude tolerance, `0 <= rel_tol < 1`; never changes type matching |

**Path-based (accepted; pilot DRAFT migrations):**

| Field | Required | Meaning |
|-------|----------|---------|
| `path` / `field_path` | yes | Dot path into the extract (supports `[observation_id]`) |
| `value` / `draft_value` | yes | Expected / stored value |
| `locator` / `source_locator` or `note` | yes | Where in the source this was checked; locator aliases are mutually exclusive and any supplied locator must be valid |

A sample uses **exactly one** addressing mode. Path keys cannot be combined
with structured selector keys as decoration; the resolver rejects such mixed
samples as ambiguous. On new (non-allowlisted) extracts, every present path
must identify observation evidence under
`species.*.observations[id].(values|equipment)...`, regardless of any other
keys in the sample.

Aliases within a mode are also exclusive: use one of `field`/`value_key`, one
of `T_K`/`T`, one of `value`/`draft_value`, one of
`locator`/`source_locator`, and either `index` or a temperature selector. When
both `observation_id` and `observable` are supplied, the selected observation's
type must equal `observable`; no null or contradictory alias may be decorative.

A parameterized checked-in test
(`tests/test_literature_extracts.py::test_fidelity_sample_matches_extract`)
resolves every sample against the extract and asserts equality. Mutating a
pinned extract value in memory must go RED.

**YAML alias note:** sample `value:` must be an **independent literal**, never a
YAML alias (`*id`) into the observation body at the root or any nested depth.
The validator recursively refuses shared mutable mapping/sequence identity — a
body edit must not silently co-update any part of the pin. Both the stored pin
and resolved body target must contain a non-null, non-whitespace payload leaf;
empty scalar strings and whitespace-only strings are refused. Match uses
the same rule for YAML binary/set values. Mutable set aliases are refused along
with mapping/list aliases. Match uses type-strict equality at every scalar leaf
and mapping key, including `int` versus `float`; sequence container types also
must agree.
Explicit `rel_tol` relaxes numeric magnitude only after concrete numeric types
It must be a finite, non-boolean number in `0 <= rel_tol < 1`; null, negative,
infinite, and nonnumeric tolerances are refused rather than ignored.

#### Policy: `ENFORCED_FOR_NEW` (effective 2026-08-03)

The validator **refuses** an extract that carries observations but lacks a
non-empty `fidelity_samples` list — **except** source_ids on the pre-policy
allowlist in `_fidelity_pre_policy_allowlist.yaml`.

| Rule | Detail |
|------|--------|
| Why grandfather | The pilot 68 extracts were migrated from DRAFT acquisition blocks, not OCR/source-page review. Instantly requiring reviewer-verified samples would invalidate a green corpus without improving evidence. |
| Who is exempt | `active_pre_policy_source_ids` only |
| Who is enforced | Every other extract (all t-509 OCR-produced extracts from day one; live corpus may grow past the frozen 68) |
| Shrink-only | `active ∪ graduated = closed`, `active ∩ graduated = ∅`. Graduate by moving an id from active → `graduated_pre_policy_source_ids` and appending it to `_fidelity_graduation_ledger.yaml`. The validator unions committed ledger versions from Git ancestry as external prior state, so deleting a tombstone from both current files still fails. The current ledger/closed set are also hash-pinned in tests. |

Worker contract for OCR extraction: `EXTRACTION-WORKER-CONTRACT.md` (and
`docs-private/research/2026-08-03-ocr-campaign/EXTRACTION-WORKER-CONTRACT.md`).

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

## Bench and experiment registries (worked example)

A **Bench is the instrument**. Anything that changed between runs belongs on
the **Experiment**, including orifice diameter and channel length under
`apparatus.geometry`. An unattributable per-run value is a typed absence:
never select an alternative, pair two independent sets, invent a midpoint, or
fabricate an “unknown-configuration” bench. An unknown orifice does not erase
a known `bench_id`. Observations must never inherit a configuration the source
did not assign.

Registry IDs are local to the extract. The migrator supplies `work_id` and
qualifies IDs as `<work_id>::bench::<id>` and
`<work_id>::experiment::<experiment_id>`. An observation's `experiment:`
is a foreign key, not a nested experiment object. References must resolve.
Registries can be lists (below) or mappings keyed by local ID.

This copyable registry fragment follows Furukawa (1975). It describes the
reported Fe-V series, **not one physical charge**: per-charge masses,
compositions and configurations are unresolved. Retain published row
compositions in observation values; do not use starting-metal assays as a
charge. The complete source-specific example, including the Fe-V-Cr series,
is [kems-119-furukawa-1975.yaml](kems-119-furukawa-1975.yaml).
It also retains the explicitly described N_V=0.796/0.900 solid-scan protocol
as a separate series record with approximate 14400 s total duration. This
does not assert another physical charge or reassign mixed Table 2 observations.
The instrument's temperature-stability magnitude is a bound of <=0.5 C.
These real records exercise RANGE, NOMINAL and BOUND through migration.

```yaml
benches:
- id: rm6k
  identity: {basis: described_in_this_work}
  apparatus_family:
    state: {tag: value, value: Knudsen-cell mass spectrometer}
    locator: {page: 13, published_page: 3051, section: '3.1'}
  cell_material_and_liner:
    state: {tag: value, value: High-purity alumina SSA-S}
    locator: {page: 13, published_page: 3051, section: '3.1'}
  geometry:
    cell_internal_dimensions:
      diameter_m:
        state: {tag: value, value: {kind: point, point: '0.009'}}
        locator: {published_page: 3051, section: '3.1'}
      height_m:
        state: {tag: unknown, reason: not_published}
        locator: {published_page: 3051, section: '3.1', note: Body height 12 mm is not a separately stated internal depth}
  other_facts:
  - name: offered_orifice_diameters_not_run_assignments
    unit: mm
    value:
      state: {tag: value, value: {kind: categorical, categorical: '0.5 or 0.3'}}
      locator: {published_page: 3051, section: '3.1', note: Discrete alternatives; not a range or pairing with lid thickness}
experiments:
- experiment_id: fe-v-series
  bench_id: rm6k
  method: knudsen_effusion
  locator: {published_page: 3053, section: '3.2', note: Series of measurements; individual configurations unassigned}
  sample:
    mass_kg:
      state: {tag: value, value: {kind: interval, interval_low: '0.001', interval_high: '0.003'}}
      locator: {published_page: 3053, section: '3.2', note: Printed 1-3 g}
    printed_composition:
      state: {tag: unknown, reason: not_published}
      locator: {published_page: 3054, table: '2', note: No single composition for this series; compositions remain in observation rows}
  apparatus:
    geometry:
      orifice_diameter_m:
        state: {tag: unknown, reason: not_published}
        locator: {published_page: 3051, section: '3.1'}
      orifice_channel_length_m:
        state: {tag: unknown, reason: not_published}
        locator: {published_page: 3051, section: '3.1'}
  conditions:
    temperature_K:
      state: {tag: unknown, reason: not_published}
      locator: {published_page: 3054, table: '2', note: Tabulated 1600 C intercept is not a universal run setpoint}
  thermal_schedule:
    total_duration_s:
      state: {tag: unknown, reason: not_published}
      locator: {published_page: '3053-3054', note: About 1 h limit only at >=1600 C; specified solid scans about 4 h overall}
```

Add `experiment: fe-v-series` beside `observation_id:` on the corresponding
existing observation, retaining its `type`, `locator`, `values`, uncertainty
and other required observation fields. Quoted results from other authors do
**not** acquire this instrument. Supporting method/starting-material records
are not measurements of a charge; retain their original evidence without
pretending they are runs. Derived fits from this work can reference their
underlying measurement series; their `model_derived` evidence class stays intact.

The migrator defaults missing pressure evidence to unknown. When supplied,
`pressure_environment` requires `total_pressure_Pa` and `sweep_gas` as
Located values, and `regime.regime_class` as a State. Do not confuse cell-side
chamber pressure with sample vapour pressure. There is no dedicated base-pressure
field in the current record: the complete example preserves its typed absence
at `pressure_environment.pumping.base_pressure_Pa`; it is not run pressure.

`pressure_environment.sweep_gas.state.value` uses the battery `SweepGas`
field list below. The migrator and per-file validator share its parser and
store validation rules. Existing single-species records serialize unchanged.

| SweepGas field | Meaning |
|---|---|
| `species` | Single gas species, or `none` for carrier-free conditions; omit for mixtures/alternatives |
| `flow_sccm` | Required numeric State; total sweep flow |
| `partial_pressure_Pa` | Required numeric State; pressure attributable to the whole sweep gas |
| `components` | Optional list of at least two `SweepGasComponent` records constituting one mixture |
| `alternatives` | Optional list of at least two `SweepGas` records printed as alternatives, never combined |

Exactly one of `species`, `components`, `alternatives` must be populated.
Each component has exactly `species`, `mole_fraction`, `flow_sccm`, and
`partial_pressure_Pa`. The last three are required numeric States (decimal
strings in `tag: value`, or an explicit typed absence); mole fractions are
dimensionless. Values must be finite and nonnegative; fractions lie in [0, 1].
Do not infer fractions, flows or partial pressures from each other. A printed
mixture without fractions retains its components with
`mole_fraction: {tag: unknown, reason: not_published}` on each component.
Unknown fractions remain unknown; recording a mixture does not establish fO2.
For “CO or CO-Ar mixture”, record alternatives: a single-species CO record
and a mixture with CO and Ar components. Preserve the printed wording and
locator on the enclosing Located record; do not select an alternative or
pair independent sets of values. Carrier-free `species: none` cannot carry
numeric flow or partial pressure. Omit unused optional fields.

### Located values and print forms

Every fact is `{state: ..., locator: ...}`. Numeric fields use a tagged
`Value` inside `state.value`; strings such as the detector name are direct
`state.value` strings. Quote numeric payloads to preserve decimal spelling.
Suffixes define SI units (`_m`, `_kg`, `_Pa`, `_K`, `_s`); retain original
units and printed wording in the locator note. Unit conversion preserves
interval endpoints, inequality and approximation. Never calculate physical
geometry, rates or partial pressures into evidence.

| Printed form | Value payload |
|---|---|
| POINT | `{kind: point, point: '9'}` |
| RANGE | `{kind: interval, interval_low: '1', interval_high: '3'}` |
| NOMINAL (“about 4”) | `{kind: point, point: '4', approximate: true}` |
| BOUND (“at most about 1”) | `{kind: bound, bound_operator: '<=', bound_value: '1', approximate: true}` |
| Discrete alternatives | `{kind: categorical, categorical: '0.5 or 0.3'}` |

These payload examples illustrate syntax; their units and applicability come
from the enclosing field and source. NOMINAL is encoded as a point **with
`approximate: true`**, never as an exact point. BOUND operators are `<`, `<=`,
`>`, `>=`. A range is not its midpoint. A ± stability envelope is not
calibration uncertainty. A conditional duration must not become an unconditional
`total_duration_s`: retain the qualification in evidence prose and mark the
aggregate field unknown when no single value applies.

Typed absence:
```yaml
orifice_diameter_m:
  state: {tag: unknown, reason: not_published}
  locator: {published_page: 3051, section: '3.1', note: Alternatives printed, run assignment absent}
```
Closed reasons: `not_published`, `not_numeric`, `in_cited_source`,
`illegible`, `would_invent`, `not_applicable`. Use
`tag: not_applicable, reason: not_applicable` for an inapplicable field;
absence is not zero. Explanations go in `locator.note`, not new reason tokens.

### Identity, field homes and refusal

`identity.basis: described_in_this_work` means the instrument is described
here; locate the description on its facts (the identity object has no locator
field). A genuinely author-cited instrument uses:
```yaml
identity:
  basis: cited_by_author
  ref:
    work_id: null
    cited_as: 'Smith (1980)'
    for_parameters: [orifice_diameter_m]
    locator: {page: 3, section: apparatus}
```
This is a syntax example, not Furukawa evidence. Populate the cited work ID only
when resolved. `inferred_from_embedded_evidence` is migrator-only; never author
it in an extract.

Stable instrument fields include `geometry`, `heating_method`, `detector`,
`temperature_measurement`, `temperature_calibration`, `pumping_type`,
`pumping_speed_m3_s`, `gauges`, `ionization`, and numeric/categorical
`other_facts: [{name, unit, value: <Located Value>}]`.
Experiment fields include `sample` (also accepted as `charge`),
`apparatus.geometry` for per-run configuration, `pressure_environment`,
`fO2_control`, `conditions`, and `thermal_schedule`.
Sample fields include `mass_kg`, `printed_composition`, `initial_composition`,
`form`, `characterization`, `surface_area_m2`, and `pretreatment`.
Thermal schedules hold Located `method`, `total_duration_s`,
`cooling_or_quench`, plus `ramps` (`rate_K_s`, start/end temperature),
`setpoints_and_holds` (`temperature_K`, `hold_duration_s`) or `points`
(`time_s`, `temperature_K`). Do not record alloy preparation as a measurement hold.

For Furukawa there is ONE real instrument; diameter alternatives {0.5, 0.3} mm
and lid thickness alternatives {1, 0.5} mm are **unpaired evidence sets**.
Both diameters were compared at N_V=0.796, but no individual aggregate row has
a diameter/thickness assignment. Preserve the sets on Bench as categorical
evidence, per-run absences on Experiment. Effective escape area remains
**BLOCKED**. Even an assigned diameter alone yields only GEOMETRIC_ONLY, not
a transport-effective area. The current waypoint implementation reads bench
geometry; this canary supplies no run-specific geometry to that route and
does not widen or change waypoint code.

## Equipment / bench metadata

Optional block on an observation during the compatibility window. The complete allowed
printed-name set and each name's typed destination are defined only in
`data/literature/lab_parameter_vocabulary.yaml`. The validator loads that file; do not
maintain a second allowlist here or in Python. New extracts should prefer the top-level
`benches:` / `experiments:` registries described above, while legacy
`equipment:` continues to validate against the same vocabulary.

The vocabulary covers apparatus identity and method, cell/chamber/orifice geometry,
pumping, gauges, detection/ionization, temperature measurement/calibration, heating,
charge characterization, thermal schedules, and long-tail bench facts. It also assigns
typed homes to the formerly dropped `furnace`, `heating_schedule`, `residue_analysis`,
`oxygen_partial_pressure`, and `integrated_exposure` fields.

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

# t-246 ceramics taxonomy unification

## Deterministic field crosswalk

The merge keeps `data/ceramics_taxonomy.yaml` as the only data authority.
Its 23 normative `nodes` retain their order, IDs, signatures, tolerances, and
match flags. The 35-entry hierarchy moves into the same document at
`ceramic_hierarchy.entries` in its existing order.

| Legacy field | Canonical field | Rule |
|---|---|---|
| `schema_version` | `ceramic_hierarchy.schema_version` | Preserve integer exactly. |
| `policy_id` | `ceramic_hierarchy.policy_id` | Preserve token exactly. |
| `ignored_identity_oxides` | `ceramic_hierarchy.ignored_identity_oxides` | Preserve order and values. |
| `analytical_tolerance_wt_pct` | `ceramic_hierarchy.analytical_tolerance_wt_pct` | Preserve numeric value. |
| `source_documents.<id>` | `sources.ceramic_<id>.path` | Preserve path exactly; add only a descriptive title. |
| `ceramics.<id>` | `ceramic_hierarchy.entries.<id>` | Preserve entry and hierarchy order; add `canonical_node_id`. |
| `parent`, `level`, `label`, `composition` | Same fields on the hierarchy entry | Preserve without coercion. |
| `service_temp` | Same field on the hierarchy entry | Preserve values, kind, citations, and notes. |
| `liner_suitability` | Same field on the hierarchy entry | Preserve verdict, citations, and notes; no suitability inference. |
| `datasheet` except `mechanical_properties` | Same field on the hierarchy entry | Preserve text exactly. |
| `datasheet.mechanical_properties` | `strength.text` | Move text verbatim; set `strength.status: sourced_qualitative_text` and `source_ids: [ceramic_datasheets]`. No numeric extraction. |
| Missing canonical-node hierarchy match | Derived node property state | Emit `status: not_classified`, `text/value/verdict: null`, and empty source IDs. |

`data/ceramic_types.yaml` is removed after migration. The compatibility loader
in `simulator/ceramic_classifier.py` delegates to the canonical loader and
projects the embedded hierarchy; it is not a second file or authority.

## Entry crosswalk

| Hierarchy entry | Canonical node |
|---|---|
| `calcium_aluminate_refractory` | null (family parent) |
| `monocalcium_aluminate_CA` | `calcium_aluminate_ca` |
| `calcium_dialuminate_CA2` | `calcium_aluminate_ca2_grossite` |
| `calcium_hexaluminate_CA6` | `hibonite_ca6` |
| `tricalcium_aluminate_C3A` | `tricalcium_aluminate_c3a` |
| `mayenite_C12A7` | `mayenite_c12a7` |
| `aluminosilicate_ceramic` | null (family parent) |
| `mullite` | `mullite` |
| `anorthite` | `anorthite_plagioclase` |
| `cordierite_mullite` | null (distinct composite) |
| `sillimanite_group` | null (no normative node) |
| `cordierite_pure` | `cordierite` |
| `basic_mgo_refractory` | null (family parent) |
| `doloma` | `dolime_cao_mgo` |
| `magnesium_aluminate_spinel` | `spinel_mgal2o4` |
| `forsterite` | `forsterite_olivine` |
| `periclase_mgo` | `periclase_mgo` |
| `enstatite` | null (no normative node) |
| `ca_mg_silicate` | null (family parent) |
| `wollastonite` | `wollastonite` |
| `diopside` | `diopside_pyroxene` |
| `akermanite_melilite` | `akermanite_melilite_mg` |
| `merwinite` | `merwinite` |
| `monticellite` | `monticellite` |
| `alkaline_earth_silicate_cement` | null (family parent) |
| `dicalcium_silicate_C2S` | null (no normative node) |
| `tricalcium_silicate_C3S` | null (no normative node) |
| `ree_ti_cr_rump_phase` | null (family parent) |
| `perovskite_catito3` | `perovskite_catito3` |
| `ree_aluminate_silicate_family` | null (no normative node) |
| `cr_spinel_chromite` | null (no normative node) |
| `ree_titanate_pyrochlore_family` | `ree_titanate_pyrochlore_family` |
| `cas_cmas_glass_ceramic` | null (family parent) |
| `cmas_glass_ceramic` | null (no normative node) |
| `gehlenite_anorthite_path` | null (range/path is not the pure gehlenite node) |

## Producer call path

There is one backend producer:

```text
LedgerAPI.terminal_product_taxonomy()
  -> build_terminal_product_taxonomy_entity()
     -> classify_terminal_product()
```

The live view calls `LedgerAPI.terminal_product_taxonomy()` directly:

```text
LedgerAPI.view("terminal_ceramic")
  -> LedgerAPI.terminal_product_taxonomy()
```

The completed-run path uses the same producer and does not reclassify:

```text
PyrolysisRun._build_output_detail()
  -> _terminal_product_taxonomy_report()
     -> LedgerAPI.terminal_product_taxonomy()
  -> runner_payload.terminal_product_taxonomy
  -> build_run_artifact() pass-through
  -> artifact.terminal.terminal_product_taxonomy
```

`classify_terminal_product()` has exactly one runtime call site, inside the
entity builder. No `web/**` module imports or calls it. The legacy
`classify_ceramic_rump()` API remains a compatibility projection, but its
loader delegates to the embedded canonical hierarchy; it owns no separate
data file.

## Entity schema

Before the flip, `terminal_ceramic.data` was:

```text
{
  species_kg,
  class_kg,
  classifier: "terminal_rump"
}
```

The transitional `classifier` label is removed. The view data and the
runner/artifact field now use the same `terminal_product_taxonomy` entity:

```text
{
  product_class,
  match_status,
  user_label_term,
  display_name,
  assemblage?,
  grade,
  matched_nodes?: [{
    id,
    label,
    normative_fraction_wt_pct,
    product_class,
    evidence_tier,
    properties: {
      density_g_cm3?,
      melting_c?,
      use_class?,
      notes?,
      catalog_entry_id,
      hierarchy,
      service_temperature,
      liner_suitability,
      strength,
      datasheet
    }
  }],
  evidence_tiers,
  residual?,
  properties_panel,
  provenance,
  physical_composition: {
    mass_kg,
    species_kg,
    species_mol,
    class_kg,
    oxide_wt_pct,
    basis: {
      species_kg: "kg_projected_from_mol_ledger",
      species_mol: "mol_atom_ledger",
      class_kg: "kg_reporting_projection",
      oxide_wt_pct: "oxide_wt_pct_normalized_volatiles_free"
    }
  }
}
```

The old 23-node normative selection algorithm and node data are parse-equal
to the pre-migration YAML. The added properties decorate selected nodes
after the fit; they do not participate in matching.

## Provenance and null rules

- `strength.text` is byte-for-byte the former
  `datasheet.mechanical_properties` text. No number, range, temperature,
  grade, or verdict is extracted from it.
- Every grounded strength uses
  `status: sourced_qualitative_text` and
  `source_ids: [ceramic_datasheets]`; that source points to the pre-existing
  datasheet research document.
- Existing `service_temp` and `liner_suitability` cells are copied without
  coercion, including their citations and `uncharacterized`/limited states.
- A canonical normative node with no exact hierarchy crosswalk emits
  `catalog_entry_id: null`, empty hierarchy, typed `not_classified` service,
  liner, and strength records, null values/verdicts, and no source IDs.
- A no-match result emits a typed `properties_panel.status:
  not_classified` with null strength text. Empty physical rump composition is
  a no-match entity rather than an exception.
- Furnace ceiling, temperature-profile ID, and run ID remain null when the
  producer does not possess those facts. The runner campaign token is not
  relabelled as a temperature-profile ID.
- Runner-side producer failure emits
  `terminal_product_taxonomy: null` without changing the primary run status;
  the artifact preserves explicit null versus absent legacy payloads.
- Classification is read-only. The existing byte-identical ledger tests
  exercise the new view alongside every other named view.

## Focused verification

Environment: the workspace `.venv` was used because system Python 3.14 lacks
the pytest-xdist/timeout plugins required by `pyproject.toml`.

- Pre-change baseline: 73 passed in 21.93 s.
- Post-migration focused gate: 127 passed in 47.36 s, with seven existing
  NumPy deprecation warnings from `nptyping`.
- Post-self-review rerun: 127 passed in 44.84 s with the same seven warnings.
- Included:
  `tests/test_terminal_product_taxonomy.py`,
  `tests/test_ceramic_classifier.py`,
  `tests/accounting/test_ledger_api.py`,
  `tests/test_wall_materials_data.py`,
  `tests/test_run_artifact_contract.py`, and the three affected real-run
  contract nodes in `tests/test_runner_smoke.py`.
- Structural checks: `py_compile`, `git diff --check`, exact old/new
  YAML parse comparison, no stale `ceramic_types.yaml` references, one
  runtime classifier caller, and no web caller.

## Fix round 2 — converged-review dispositions

### P1/HIGH: live ceramic advisory strength loss — fixed

`load_ceramic_types()` now projects the canonical `strength.text` back to the
legacy in-memory `datasheet.mechanical_properties` field consumed by
`CeramicMatch` and the live advisory surfaces. The YAML remains the single
authority: all 35 canonical entries still omit that datasheet key, and the
projection test proves every projected entry differs only by the exact
canonical strength string. It adds no source, status, citation, numeric
strength, or second data file.

`ceramic_rump_payload()` now carries the exact canonical forsterite strength
string through `match.datasheet.mechanical_properties`; the canonical entry
remains unchanged.

### P1: report-viewer taxonomy tri-state and consumption — fixed

`web/report_viewer/report-viewer.js` now uses own-property presence rather
than truthiness:

- a present object renders its classification verdict, emitted matched-node
  properties (including exact strength status/text), and the complete
  `physical_composition` floor;
- explicit null renders a typed `attempted but unavailable` state;
- an absent legacy field retains the existing W-D7 pending state.

The renderer iterates only emitted property keys. Missing node properties are
not synthesized, physical numeric cells require real finite numbers, and an
omitted physical map is labelled `not emitted` rather than `captured, empty`.
Contract tests cover all three taxonomy states and assert that absent optional
matched-node fields do not appear.

### P3: unused hierarchy helper — fixed

`hierarchy_entries_by_id()` was removed from
`simulator/terminal_product_taxonomy.py`; repository search reports no
remaining reference.

### Fix-round receipts

- New regression slice: 6 passed in 1.09 s.
- Focused fix-round gate: 102 passed in 30.84 s.
- Final post-self-review gate: 102 passed in 31.05 s.
- Gate command used `-n 0` and included the advisory and report-viewer
  contracts, all ceramic-classifier tests, all terminal-product-taxonomy
  tests, and the three affected real-run runner contract nodes.
- `node --check web/report_viewer/report-viewer.js`, `git diff --check`, and
  the dead-helper search passed.
- Null-hypothesis review: no fix-round edit touches the canonical YAML, schema
  versions, ledger-view flip, or runner goldens; compatibility projection
  introduces no new strength authority, and viewer output does not invent
  absent entity fields.

## Deferred controller gate

- `RUNNER_SCHEMA_VERSION` is bumped `1.7.0 -> 1.8.0`; the exact top-level
  contract and public runner schema doc are updated.
- `LEDGER_SCHEMA_VERSION` is bumped `2.0.0 -> 3.0.0` because
  `terminal_ceramic.data` changes shape and removes the transitional label.
- `ARTIFACT_SCHEMA_VERSION` remains `0.2.0`: the locked artifact design
  already reserved optional `terminal_product_taxonomy`; the builder now
  preserves present, explicit-null, and absent states. Controller should
  confirm this no-bump interpretation at the T2 gate.
- Do not hand-edit runner goldens. Studio regeneration/review is required for:
  `tests/fixtures/runner/lunar_mare_low_ti_C0_24h.json`,
  `tests/fixtures/runner/mars_basalt_C2A_12h.json`, and
  `tests/fixtures/runner/ci_carbonaceous_chondrite_C2B_12h.json`.
- The controller should run `scripts/regenerate_runner_goldens.py` from the
  executable simulator only after proving current-main baseline state, then
  run the full T2/Studio gate and independent review before commit.

## Fix round 3 — real-artifact viewer fixture

### P2: viewer tri-state tests bypassed the artifact path — fixed

`tests/test_report_viewer_mol_render.py` now builds the present Forsterite
entity with `build_terminal_product_taxonomy_entity()`, carries present,
producer-failure explicit-null, and legacy-absent payloads through
`build_run_artifact()`, and serializes the resulting terminal sections into
the Node harness. The present fixture removes the same three optional
properties as the prior consumer-omission case, so every existing negative
field assertion remains pinned; the exact strength assertion now uses the
canonical producer text.

`tests/fixtures/web_render/render_report_ledger_values.mjs` contains no
hand-authored taxonomy entity or terminal envelope. It renders only the real
artifact terminal JSON supplied by the Python driver.

### Fix-round-3 receipts

- `.venv/bin/python -m pytest -n 0 -q tests/test_report_viewer_mol_render.py tests/test_run_artifact_contract.py`
  — 40 passed in 1.06 s.
- `node --check tests/fixtures/web_render/render_report_ledger_values.mjs`
  — passed.
- `git diff --check` — passed.

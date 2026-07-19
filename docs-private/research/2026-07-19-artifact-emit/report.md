# Web §2(b) producer artifact emission report

## TL;DR

- Run artifacts now carry the canonical thermal-train report and the complete product-classification block under `terminal`.
- Per-hour rows now carry all seven shuttle fields plus direct MRE voltage and current, copied from `HourSnapshot` without web rounding.
- The artifact builder only attaches producer payload objects; legacy payloads still omit the new optional terminal blocks.
- Exactly three executable runner goldens moved; removing the new fields and schema version reproduces every legacy field exactly.
- Canonical `.venv`, `-n0` verification passed: 370 tests, including artifact, runner/per-hour, web, and mass-balance coverage.

## Scope and premise correction

The product-classification block was already materialized in the runner payload.
The thermal-train calculation existed as the authoritative named-view query, but
this checkout did **not** materialize it in the runner payload before artifact
construction. The producer now invokes that canonical query once while projecting
the completed run, stores the result as `thermal_train_report`, and the artifact
builder attaches that exact payload object. No thermal physics or alternate
calculation was added to `build_run_artifact()`.

Frontend files were not changed. `corpus_version` and
`ARTIFACT_SCHEMA_VERSION` (`0.2.0`) were not changed. The additive runner shape
is documented and versioned as `RUNNER_SCHEMA_VERSION = 1.7.0`.

## Field provenance and attachment

| Emitted field | Existing run computation/source | Producer packaging | Artifact attachment |
|---|---|---|---|
| `thermal_train_report` | `AccountingQueries.thermal_train_report()` in `simulator/accounting/queries.py:162` derives the canonical named-view data from recorded snapshots. | `simulator/runner.py:2763` calls that authority once and recursively orders only mapping keys for deterministic JSON; success and failure envelopes emit it at `simulator/runner.py:1309` and `simulator/runner.py:4356`. | `simulator/accounting/run_artifact.py:427` attaches the existing payload block as `terminal.thermal_train_report`; it does not call the query. |
| `product_classification` | `_product_classification_report()` calls `classify_products(..., early_tap_mode=False)` at `simulator/runner.py:2752` and builds classification plus markdown from the same classification object. | The runner already emits the full block at `simulator/runner.py:1299` (and the guarded failure envelope at `simulator/runner.py:4344`). | `simulator/accounting/run_artifact.py:428` attaches the existing full block as `terminal.product_classification`; no classifier call occurs in the artifact builder. |
| `shuttle_phase` | Snapshot copies live `_shuttle_phase` at `simulator/core.py:12578`. | `build_per_hour_summary()` copies it at `simulator/runner.py:2125`. | The unchanged per-hour row is stored as `timesteps[].summary`. |
| `shuttle_injected_kg_hr` | Shuttle kernels accumulate `_shuttle_injected_this_hr` at `simulator/extraction.py:2402` and `simulator/extraction.py:2513`; snapshot copies it at `simulator/core.py:12579`. | Copied directly beside the other shuttle fields in `build_per_hour_summary()`. | Same per-hour summary object. |
| `shuttle_reduced_kg_hr` | Shuttle kernels accumulate `_shuttle_reduced_this_hr` at `simulator/extraction.py:2404` and `simulator/extraction.py:2515`; snapshot copies it at `simulator/core.py:12580`. | Copied directly in `build_per_hour_summary()`. | Same per-hour summary object. |
| `shuttle_metal_produced_kg_hr` | Shuttle kernels accumulate `_shuttle_metal_this_hr` at `simulator/extraction.py:2406` and `simulator/extraction.py:2517`; snapshot copies it at `simulator/core.py:12581`. | Copied directly in `build_per_hour_summary()`. | Same per-hour summary object. |
| `shuttle_K_inventory_kg` | Snapshot copies the live simulator inventory at `simulator/core.py:12582`. | Copied directly in `build_per_hour_summary()`. | Same per-hour summary object. |
| `shuttle_Na_inventory_kg` | Snapshot copies the live simulator inventory at `simulator/core.py:12583`. | Copied directly in `build_per_hour_summary()`. | Same per-hour summary object. |
| `shuttle_cycle` | Snapshot selects the live cycle at `simulator/core.py:12607`. | Copied directly in `build_per_hour_summary()`. | Same per-hour summary object. |
| `mre_voltage_V` | MRE dispatch assigns `_mre_voltage_V` at `simulator/extraction.py:1777`; snapshot copies it at `simulator/core.py:12631`. | Copied directly at `simulator/runner.py:2134`. | Same per-hour summary object. |
| `mre_current_A` | MRE dispatch assigns `_mre_current_A` at `simulator/extraction.py:1778`; snapshot copies it at `simulator/core.py:12632`. | Copied directly at `simulator/runner.py:2135`. | Same per-hour summary object. |

The per-hour path uses native snapshot precision. It deliberately does not use
the live web tick's rounded display projection.

## Golden and schema impact

Regenerated from the executable with:

```text
.venv/bin/python scripts/regenerate_runner_goldens.py
```

Exactly these goldens moved:

1. `tests/fixtures/runner/lunar_mare_low_ti_C0_24h.json`
2. `tests/fixtures/runner/mars_basalt_C2A_12h.json`
3. `tests/fixtures/runner/ci_carbonaceous_chondrite_C2B_12h.json`

Each gained `thermal_train_report`, the nine requested fields on every per-hour
row, and runner schema `1.7.0`. No run-artifact golden exists. The web-trace
fixture and `data/fixtures/thermal_train/default-v2.json` did not move.

A programmatic semantic comparison loaded each fixture from `HEAD`, removed
only the new thermal/per-hour fields from the regenerated copy, normalized the
two runner-schema pins, and compared the remaining JSON. All three reported
`legacy_fields_unchanged=true` and the aggregate result was `overall=true`.

## Backward compatibility and null-hypothesis proof

- `build_run_artifact()` conditionally attaches the two producer blocks only
  when their payload keys exist. `test_legacy_payload_omits_new_terminal_blocks`
  proves an older payload retains its previous shape.
- `test_terminal_preserves_precomputed_producer_blocks_without_reprojection`
  uses object identity to prove the artifact terminal receives the producer's
  thermal and classification objects rather than rebuilt substitutes.
- `test_completed_run_artifact_preserves_computed_views_and_hourly_controls`
  executes a representative run, checks the thermal report against the
  canonical query, checks full classification identity, and checks all nine
  per-hour values against the committed `HourSnapshot`.
- `test_per_hour_summary_preserves_nonzero_shuttle_and_mre_snapshot_values`
  propagates nine distinct nonzero/nonnull sentinels, preventing hard-coded
  empty or zero defaults from satisfying the provenance contract.
- The semantic golden comparison proves no existing emitted value changed.
- Thermal mapping-key canonicalization was added only because cross-process
  byte-parity exposed unordered nested configuration sets; values are unchanged.

## Verification

Focused attachment and subprocess determinism gate:

```text
5 passed in 26.94s
```

After independent review added the distinct nonzero provenance sentinels, the
complete runner-smoke plus artifact-contract files passed:

```text
97 passed, 25 warnings in 239.30s
```

Canonical broad gate (`.venv/bin/pytest -n0`) over runner/per-hour, session CLI,
artifact contract/store/confidence/ledger, web events/thermal/advisory, and mass
balance suites:

```text
370 passed, 39 warnings in 613.04s
```

Warnings were existing NumPy deprecations and the declared SiO
`VaporPressureFallbackWarning`; there were no failures. `git diff --check`
passed.

Independent review found the initial all-zero C0 provenance-test weakness; the
nonzero sentinel test above resolves it. The reviewer reported all other areas
clean.

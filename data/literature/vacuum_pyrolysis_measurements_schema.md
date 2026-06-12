# Vacuum Pyrolysis Measurements Sidecar Schema

`vacuum_pyrolysis_measurements.yaml` stores experiment-grade paper anchors
outside runtime preset inputs. Presets may reference `measurement_id`; they may
not copy measured values.

Required structure:

```yaml
schema_version: vacuum_pyrolysis_measurements.v1
measurements:
  <measurement_id>:
    evidence_class: experiment-grade
    paper_citation: ...
    doi: ...
    extraction: ...
    conditions_reported: ...
    material_context: ...
    quantitative_measurements: []
    observed_locations: []
    conflicting_reported_values: []
    named_missing_fields: []
    measured_time_series: []
```

Every quantitative value, including `conditions_reported` scalar rows, must
preserve the reported unit and include a normalized SI projection when useful.
Each value row must carry:

- `source.citation_id`
- `source.source_location`
- `source.digest`

Per-location deposit rows live under `observed_locations`. Rows may be
quantitative, qualitative-only, or missing-mass, but the absence of a mass is
named with `extraction_status`, not converted to zero. Each quantitative deposit
row includes `measured_hot_vs_cooldown` with one of:

- `hot`
- `cooldown`
- `post_run_cooldown`
- `unknown`

Conflicting values are represented explicitly under
`conflicting_reported_values` with every reported value preserved. The Robinot
2026 skeleton records the 1.1 g vs 1.3 g conflict as
`resolution: unresolved_report_both`.

Named validation failures tested for this sidecar:

- `sidecar_value_missing_citation`
- `missing_schedule_interpolation`
- `schedule_interpolation_missing_source_class`
- `schedule_interpolation_missing_assumption_note`
- `missing_carrier_gas`
- `missing_gas_boundary_imposed_flow`
- `missing_gas_boundary_pressure_control`
- `gas_boundary_not_reported_missing_source_class`
- `gas_boundary_not_reported_missing_citation`
- `gas_boundary_not_reported_missing_reason`
- `gas_boundary_not_reported_missing_digest`
- `gas_boundary_missing_source_class`
- `gas_boundary_missing_source_ref`
- `gas_boundary_missing_citation_id`
- `gas_boundary_missing_digest`
- `po2_setpoint_exceeds_total_pressure_without_effective_po2`
- `po2_achieved_exceeds_total_pressure`
- `po2_clipping_requires_total_pressure_flag`
- `po2_clipping_status_inconsistent`
- `sticking_coefficient_missing_source_class`
- `sticking_coefficient_missing_source_detail`
- `sticking_coefficient_missing_digest`
- `qualitative_composition_missing`
- `qualitative_composition_missing_measurement_type`
- `qualitative_composition_missing_citation`

The gas-boundary, pO2, sticking, and qualitative-composition entries are
cross-schema fail-loud rules tested in
`tests/test_vacuum_pyrolysis_schema.py` because the design does not place a
runtime loader module for VPR-P5.

Gas-boundary paper omissions use an explicit not-reported disposition, not
silent absence. A not-reported gas-boundary row sets
`reported_status: not_reported`, `source_class: not_reported`, a paper
`citation_id`, an `extraction_note` or `reason`, and a digest. This preserves
sparse-paper honesty for fields such as carrier gas, imposed flow, and
pressure-control mode.

`lab_schedule.interpolation` remains the consumer field for the schedule shape.
When the paper reports only time/temperature anchors, the preset carries
`interpolation_source_class: assumption_with_sensitivity_marker` plus a citation
and extraction note so the schedule cannot masquerade as a paper-reported T(t)
profile.

# Vacuum Pyrolysis Preset Schema

Preset files in this directory are tracked runtime inputs for faithful
literature reproduction and remediation-twin studies. Paper measurements stay
out of preset files and live in
`data/literature/vacuum_pyrolysis_measurements.yaml`.

Required top-level fields:

- `schema_version`
- `paper_id`
- `paper_citation_id`
- `measurement_id`
- `preset_kind`: `faithful_reproduction` or `faithful_with_remediation_twin`
- `extraction_status`: `skeleton_not_full_paper_data`, `partial`, or `complete`
- `lab_schedule`
- `lab_geometry`
- `pair`
- `comparison_contract`
- `digests`

`lab_schedule.gas_boundary` is mandatory when a paper reports carrier gas,
composition, flow, pump throughput, or pressure-control mode. The gas boundary
must include source class, source reference, citation id, and digest. Digest
keys stay split: feedstock, schedule, gas boundary, geometry, transport formula,
sticking provenance, and measurement sidecar.

Sparse papers must name gas-boundary omissions explicitly. A required gas
boundary row may use `reported_status: not_reported` only with
`source_class: not_reported`, a paper `citation_id`, an `extraction_note` or
`reason`, and a digest; silent absence still fails validation.

`lab_schedule.interpolation` is the consumer-facing schedule-shape field. If
the paper reports only duration/peak anchors rather than a T(t) profile, the
schedule also carries `interpolation_source_class:
assumption_with_sensitivity_marker` plus citation and extraction note.

`lab_geometry.surfaces` records named lab surfaces rather than industrial pipe
segments. Each surface carries `id`, `role`, `temperature_profile`,
`source_class`, and `extraction_note`. Robinot-style skeletons include holder,
window, condenser, and filter roles even when dimensions remain unresolved.

`pair.*.mitigation.pO2_cover` must report achieved-vs-setpoint behavior. If
`setpoint_mbar > p_total_mbar`, `effective_pO2_achieved_mbar`,
`limited_by_total_pressure`, and `status` are mandatory; the preset must not
imply a physically impossible pO2 setpoint was achieved.

`kinetics_caveat` values:

- `none`
- `furnace_scale_bulk_mixing_assumption`
- `blocked_missing_gram_scale_kinetics_model`

Trust tags follow contract C3. Reproduction outputs may use
`fidelity_tier: real` or `cached-real`; `internal_analytical_used` must remain
false for any experiment-grade claim, and cached analytical output may not be
presented as real-engine evidence.

Validation placement is intentionally test-local for this chunk. The design
names the schema paths but does not place a runtime loader/validator module, and
layout freeze R11.7 bars speculative simulator modules before review.

Named validation failures tested for this schema:

- `missing_schedule_interpolation`
- `schedule_interpolation_missing_source_class`
- `schedule_interpolation_missing_assumption_note`
- `missing_carrier_gas`
- `po2_setpoint_exceeds_total_pressure_without_effective_po2`
- `sidecar_value_missing_citation`
- `sticking_coefficient_missing_source_class`

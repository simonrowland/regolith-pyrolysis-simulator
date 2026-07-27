# KEMS reproduction presets

These YAML files describe runtime inputs only. Published observations belong in
`data/literature/kems_measurements.yaml`; pressure, intensity, and effusion
observations must never be copied into a preset.

## Schema version 1

Unknown or missing fields are rejected by
`simulator.diagnostic_helpers.kems.load_kems_case`.

- `case_id`, `source_id`: stable recipe and literature identities.
- `oxide`: formula, material identity, fractional purity, and purity locator.
- `samples`: one row per run with initial and post-experiment mass in mg.
- `cell`: material, orifice diameter and area in SI, and transmission factor.
- `temperature_program`: polythermal range and step in K, per-step hold in s,
  repeat count, systematic temperature uncertainty, and the cited isothermal
  equilibrium check.
- `exterior_chamber_pressure`: an operator (`less_than` or `equal`) and Pa.
- `provider_inputs`: the explicit oxygen-pressure driver passed to the builtin
  vapor-pressure provider, with `reported`, `derived`, or `assumed` status.
- `calibration`: standard, method, paper-defined sensitivity factor, and locator.
- `measurement_selectors`: exact observable/species pairs. Species are never
  summed, element-remapped, or gas-suffix-remapped.
- `citations`, `assumptions`: source identity and declared model inputs.

## Geometry and flux

The orifice area must satisfy
`A_orifice = pi * (orifice_diameter_m / 2)^2`. The adapter calculates ideal
per-area flux with `knudsen_effusion_molar_flux`, then apparatus effusion as
`J_i * A_orifice * W_i`, where `W_i` is the reported or explicitly derived
Clausing/transmission factor. `melt_surface_area_m2` is not a KEMS field.

`transmission_factor.status` is `reported`, `derived`, or `not_reported`.
Reported values require a source locator; derived values require both a
derivation and source locator. `not_reported` requires a null value. In that
case apparatus effusion is unavailable; the adapter does not assume `W_i = 1`.
`evaporation_alpha` is neither a case field nor a KEMS multiplier.

## Measurements and evidence

Supported selectors are `partial_pressure_pa`, `ion_intensity`,
`effusion_rate_mol_s`, and `total_pressure_pa`. A total-pressure comparison is
permitted only when its independent observation point sets
`allow_total_pressure_fallback: true`. Runtime presets cannot authorize the
fallback. The serialized record uses `species: null` and the fixed
`total-pressure-fallback` evidence scope, so it cannot certify any individual
species. Numeric observation points require a cited comparator uncertainty
with `kind` and `value`; assumed inputs do not waive that requirement.

Halwax et al. (2024) publish the CaO and MgO pressure series as Figures 7-13,
not as numeric point tables, and do not report a Clausing factor or pointwise
pressure uncertainty. The sidecar therefore preserves the exact species and
source locators while marking unextractable point values absent.

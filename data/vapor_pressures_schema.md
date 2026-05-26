# Vapor Pressure YAML Schema

`data/vapor_pressures.yaml` stores fallback Antoine coefficients used by the
builtin `VAPOR_PRESSURE` path. Every `metals` and `oxide_vapors` row must
declare `fit_target` so consumers can tell whether the raw Antoine term is a
pure-component vapor pressure, a back-solved pseudo-standard term, or an
explicit reaction term.

## `fit_target` Values

`pure_component_psat`

: `antoine` evaluates a pure-species saturation pressure. The builtin metal
  loop multiplies the result by the Ellingham liquid-metal activity `a_M`; this
  is single-counted when the coefficients are genuinely pure-component or
  legacy approximations to that basis. Rows with this target must include a
  `source` field. If an entry is present but not emitted by the active consumer,
  add `consumer_status: inactive`.

`pseudo_psat_backsolved_from_vaporock`

: `antoine` evaluates a pseudo-standard term fitted so the final consumer
  chain matches VapoRock partial pressures after the existing activity and
  pO2 factors are applied. Rows with this target must include a `backsolve`
  block containing:

  - `feedstock_grid`: calibration composition set.
  - `fO2_convention`: oxygen-fugacity convention used by the fit.
  - `activity_formula`: consumer-side activity/pO2 expression included in the
    final fitted chain.
  - `target`: VapoRock partial-pressure target.
  - `residual_dex`: maximum reported residual in log10 pressure units.

`standard_reaction_term`

: `antoine` evaluates a standard-reaction ΔG-equivalent term. The consumer
  applies explicit oxide-activity and pO2 exponents from the YAML reaction
  metadata. Rows with this target must include a `reaction` block containing:

  - `formula`: reaction represented by the standard term.
  - `exponent_oxide`: oxide activity exponent used by the consumer.
  - `exponent_pO2`: pO2 exponent used by the consumer.
  - `basis`: source/provenance of the standard-reaction fit.

## Convention Guardrail

No consumer may infer vapor-pressure convention from comments or species names.
Schema validation requires the metadata above, and consumer code must preserve
the existing math: pure-component rows use `a_M * P_sat`; pseudo-backsolved rows
use the same expression by construction against their calibration grid; standard
reaction rows apply the declared oxide and pO2 exponents exactly once.

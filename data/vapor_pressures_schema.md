# Vapour-Pressure Catalog Schema v2

`data/vapor_pressures.yaml` is the single hot-vapour authority. Its root is:

```yaml
schema_version: 2
families:
  <co_evolving_family_id>:
    physical_properties: {...}
    fiat_routing: {...}
    vaporisation_coefficients: {...}
    code_metadata: {...}
```

Every family contains exactly those four strata. The typed compiler is
`simulator.vapour_rail.catalog.compile_vapour_rail_catalog`; runtime code must
not infer capability from field presence. During the U1--U5 shadow period,
`vapor_pressure_legacy_view` generates the old `metals`, `oxide_vapors`, and
`foulant_vapor` maps. Those maps are not duplicated in YAML.

## Physical properties

`physical_properties.species` maps canonical gas IDs to rows. Every row has:

- an atom-explicit `formula`;
- `validation.status` (`pending_validation` or `validated`) and `anchor_refs`;
- zero or more balanced `source_reactions`;
- one typed `pressure_models` entry with `pressure_kind`, `species_basis`,
  `valid_domain.temperature_K`, provenance, and evaluator family.

VR-3 supports `antoine`, `standard_reaction_term`, and
`tabulated_equilibrium`. NASA CEA evaluators belong to VR-4. An unavailable
Stage-0 identity row declares `availability: unavailable_pending_acquisition`;
it compiles metadata but no executable pressure evaluator.
`availability` is an optional scalar enum: omission means `available`, and the
only explicit non-executable value is `unavailable_pending_acquisition`.
Mappings and unknown strings fail catalog compilation.

`standard_reaction_term` requires `source_reaction_id`, an atom-balanced
reaction, and a typed `reference_pressure_model`. The reference may be Antoine
or tabulated; an `antoine` sibling is not required. Activity and pO2 exponents
are applied exactly once by the compiled evaluator.

## Fiat routing

This stratum contains engineering choices only: plant bin, capture policy,
products/coproducts, and process or terminal destination. It contains no
physical coefficient.

## Vaporisation coefficients

This stratum owns HKL alpha and uncertainty plus the anti-cliff contract:

```yaml
extrapolation_policy: conservative_slope_continuation
out_of_range_status: out_of_range_conservative_continuation
acquisition_flag: <stable row-specific identifier>
```

Outside a finite domain, the evaluator continues the endpoint slope in the
same direction while keeping pressure below straight extrapolation. It never
flatlines or returns zero solely because temperature crossed the endpoint.

## Code metadata

This stratum owns formula/catalog IDs, source ledger account, request rule,
solve-group identity, canonical aliases, applicability, and the temporary
compatibility projection name. Physical values are forbidden here.

## Catalog closure

Collision-only gas names use `_gas`; their catalog row stores the unsuffixed
chemical formula and atom map. Aggregate/generic rows marked `carrier_only`
retain `formula: null`, have no pressure models or direct vapour flux, and can
produce gas only through a balanced decomposition edge.


## VR-8 acquisition dormancy

Group-A/B oxide and trace acquisition rows live primarily in
`data/vapour_rail_trace_acquisition.yaml` (loader:
`simulator.vapour_rail.trace_acquisition`). They are dormant to flux until the
matching R-family epoch. Monatomic `O(g)` is also declared as the
`monatomic_oxygen_family` in this file with:

- formula `O` and atom-balanced `0.5 O2 -> O` source reaction;
- `pO2_exponent: +0.5` (from half-O2 recombination: p_O ~ fO2^(+1/2));
- `availability: unavailable_pending_acquisition` and
  `hot_train_applicability: not_applicable` so legacy metals/oxide/foulant
  maps are unchanged.

`list_pending_validation()` returns the complete remaining pending set across
acquisition, vapor_pressures, and species_catalog surfaces.

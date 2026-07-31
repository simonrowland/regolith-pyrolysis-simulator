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

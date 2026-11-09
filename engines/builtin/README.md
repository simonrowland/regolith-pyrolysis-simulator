# `engines/builtin/` — builtin chemistry providers

Every builtin chemistry intent is implemented in this directory as a
`ChemistryProvider`: vapour pressure, evaporation flux and transition,
condensation routing, electrolysis, metallothermic and aluminothermic steps,
Stage 0 pretreatment, overhead-gas equilibrium and bleed, the O₂ bubbler,
oxygen-reservoir exchange, Fe redox respeciation, native-Fe saturation and
tapping, metal-phase stratification and backend equilibrium. The
provider-to-intent table is in
[`docs/developer-map.md`](../../docs/developer-map.md) ("Engine Source
Trees"), and kernel routing is described in
[`docs/architecture.md`](../../docs/architecture.md).

## Adding a builtin provider

1. Implement the provider class in a new module here.
2. Declare the accounts it may touch in its `CapabilityProfile.declared_accounts`.
3. Add one row to `simulator/core.py::_BUILTIN_PROVIDER_REGISTRATIONS`. No
   change to `_build_chemistry_kernel` is needed.
4. Return ledger changes as proposals. `ChemistryKernel.commit_batch` is the
   only writer to the ledger.

`_common.py` holds shared helpers. `cco_redox_buffer.py`,
`foulant_disposition.py` and `melt_effect_adjustment.py` hold dataclasses and
typed results and define no provider class.

## Vapour pressure

`VAPOR_PRESSURE` is authoritative in the builtin (Antoine + Ellingham). It is
registered separately from the table above, through
`simulator/core.py::_register_vapor_pressure_pair`, because it needs the
simulator-owned `data/vapor_pressures.yaml` payload. Its result is diagnostic
(`transition=None`): vapour pressures are an input to `EVAPORATION_FLUX`,
which is the intent that emits the ledger transition. VapoRock is a
diagnostic-only shadow.

# Battery residual serialization

Battery residuals are serialized from the typed records in
`simulator/battery/records.py` by `residual_to_plain()` and `to_plain()`.
Enum values are written as their tokens, and optional `None` fields are omitted.

`Residual.fo2_mode` and `Notice.fo2_mode` are optional typed fields:

| Token | Meaning |
|---|---|
| `engine_intrinsic` | The engine calculated its redox state without a supplied fO2. |
| `shared_ladder` | The row's resolved fO2 condition came from the shared ladder. |

The notice that establishes a mode carries the same `fo2_mode` token.
Mode-labeled residual keys include that token so records from distinct
conditions remain separate. Residuals without a resolved or requested fO2
mode omit the field.

The `composition_binary_complement` notice records a binary composition
derived as the complement of a printed caption mole percent. Such residuals
remain visible in the `binary-complement-composition` flagged stratum.

The `composition_oxide_from_element_mass_percent` notice records oxide mole
fractions computed by stoichiometry from printed element mass percent, when
those oxides account for the whole sample. The reason carries the printed
oxide sum in wt%, read from the derivation inputs. Such residuals remain
visible in the `oxide-from-element-mass-percent-composition` flagged stratum.

## Composition and liquidus storage rules

**Initial-charge-only compositions.** A composition with
`proxy_flag = "initial_charge_only"` is a printed starting charge, while the
datum it accompanies describes a later, depleted state. It is kept as sample
context and is never treated as the composition at the measured point. When
the source publishes no per-run composition, the point composition is stored
as unknown (missing input), and the residual carries a source-disagreement
notice that names the `initial_charge_only` role. Consumers may still predict
with the starting charge, but the residual stays flagged.

**Liquidus saturation rows.** A printed liquidus row (a liquid composition
saturated with a named solid at a printed temperature) is stored as a
`transition_temperature` quantity with subtype `liquidus`. The printed
temperature is the point value. The row's own printed liquid composition,
including any printed zero components, goes in `identity.composition`. The
saturating solid is kept in the point conditions as a cross-check. When the
source reports no starting charge, the experiment's initial composition stays
unknown and never inherits a row's liquid composition. Liquidus-temperature
prediction is an upcoming feature; until it ships, these rows are refused as
`quantity_not_predicted` before any engine is opened.

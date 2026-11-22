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

Activity `reference_state.convention` may be `temkin` for the ideal-ionic
solution interpretation of FeO activity: the Fe²⁺ fraction on the cation
sublattice multiplied by the O²⁻ fraction on the anion sublattice, including
silicate anions. For pure FeO both fractions are one, so `a_FeO = 1`.

For Distin, keep the measured reference distinct from that model
interpretation. The measured activity is the oxygen ratio against the
nonstoichiometric oxide saturated with liquid iron; the source then converts
it to a stoichiometric standard. The scorer's iron-saturation reference owner
uses the Darken & Gurry factor for that conversion. Its N₀ payload describes
the saturation composition and comparison band and is not an additional
activity divisor.

The `composition_binary_complement` notice records a binary composition
derived as the complement of a printed caption mole percent. Such residuals
remain visible in the `binary-complement-composition` flagged stratum.

The `composition_oxide_from_element_mass_percent` notice records oxide mole
fractions computed by stoichiometry from printed element mass percent, when
those oxides account for the whole sample. The reason carries the printed
oxide sum in wt%, read from the derivation inputs. Such residuals remain
visible in the `oxide-from-element-mass-percent-composition` flagged stratum.

**Range-only NASA-9 fit points.** An accepted fit with no source-printed grid is
evaluated at `Tmin`, `Tmax`, and every interior multiple of 100 K. If that gives
more than 11 points, it is evaluated at 11 evenly spaced points including both
endpoints. The fit evaluator does not cross its declared range:
`evaluate_sidecar_fit` returns `outside_validity`, and no comparison row is
written. Each comparison carries the `evaluated_from_fit_on_declared_grid` notice; its
reason names the fit record, validity range, and grid rule. Points from one
fit quantity share a series id, so the grid is one flagged series rather than
independent measurements. NASA-9 comparison JSONL rows serialize these notices
in `notices`.

Policy (d-460): predict outside-range fit temperatures and attach
`out_of_certified_band` naming the range. Until scorer-gates task t-1922 lands,
out-of-range fit temperatures are not scored. Engine-polynomial extrapolation
with `out_of_certified_band` is handled by
`compilation_tier._nasa_cea_9_state`, a different path.

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
prediction is an upcoming feature. Until a predictor exists, these rows are
refused as `quantity_not_predicted` before any engine is opened. Policy (d-460):
report their observation counts in an engine × quantity capability matrix
instead of refusing them. Until task t-1923 lands, that matrix is not emitted.

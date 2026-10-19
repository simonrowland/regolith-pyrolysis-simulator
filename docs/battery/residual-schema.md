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

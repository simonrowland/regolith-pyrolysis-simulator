# Beerkens (2003) duplicate-extract reconciliation

Green baseline: `f69c0da13` (extract and pins introduced in `4ccfc2608`).
Wave version: `48c2f9135`.
Wave review: `docs-private/reviews/2026-10-27-code/ror-nabeerkens03-na2o.md`.
The green commit has no separate tracked audit sidecar; its extract and test
are the green-side record used here.

PDF renders inspected: physical pages 1, 4, 5, and 6. The article's printed
folios are 1893, 1896, 1897, and 1898. The page 5 render places both activity
fits in §VI. The page 6 render places the 1673 K value, pressure inputs, and
error estimates in the final paragraph of §VI, before §VII. Page 1 states that
activity reference states are pure phases at the same temperature, with
sodium oxide in its most stable phase; it does not name the specific Na2O
phase.

| Topic | Green baseline | Wave version and review | Resolution |
| --- | --- | --- | --- |
| Sulfur tables and figure records | Contains Table I, Table II, and figure records 1–6. | These records are omitted from the wave extract. | Retain all green sulfur context rows unchanged; omission is not a conflict or a reason to remove reviewed data. |
| Activity fits | One `model_parameters` context row contains the model fit `exp(-43552/T + 8.33)` and float fit `exp(-46000/T + 9.50)`. | The wave splits the same fits into two context rows; `48c2f9135` locates both on PDF p. 5 / published p. 1897. The wave review records that an earlier trial incorrectly put them on p. 6. | Keep green's context id and combined row because both formulas and assignments are already present. The p. 5 render confirms the current locator; do not duplicate the fit facts. |
| Activity value | A context value records the printed text `about 1.7 × 10^-8` at 1673 K. | The wave stores `1.7e-8` with `approximate: true` and the same printed text. | These representations agree. Preserve the exact printed text in context and add the numeric activity with its approximate flag to the observation. |
| Quantity | The context uses the descriptive label `sodium_oxide_activity`. | The observation uses `activity`, which maps to the existing `ACTIVITY` quantity. | Use the exact `activity` token for the observation and context quantity field. Do not introduce a new token. |
| Evidence class and lineage | Green labels the context `measured_reduced` and leaves the value pending a corpus `TABLE_CSV` lineage. | The reviewed wave labels it `measured_tabulated`; the review says this is the authors' own reduction of measurements, not an externally attributed value. The controller ruling and Kim measured-tabulated precedent settle the class. | Use `measured_tabulated` for the observation and context. The printed pressures, sulfate activity, and float-glass composition are located point conditions and derivation context, not separate observations or `derived_from` parents; remove the pending TABLE_CSV requirement and self-attribution. |
| Temperature coordinate | Green's context records `temperature_K: 1673`; it has no observation coordinate. | The wave observation used `T_K: '1673'`. | Store the new row coordinate as `T_C: '1399.85'`, which the migrator converts to 1673 K. Keep the printed 1673 K in the context and derivation text. |
| Conditions | The value and conditions are prose in the context; no located point-condition states are present. | The wave adds p(O2) = 0.33 bar, p(SO2) = 0.666 bar, and the float-glass composition. | Add `point_conditions` with `state` and source `locator` for each value. Convert the pressures to 33000 Pa and 66600 Pa; retain all six printed composition values as strings. |
| Standard state | Green inferred pure liquid Na2O from JANAF and included a 1 bar gas standard in the Figure 5 note. | The wave review says the source's pure-phase convention is printed but the specific Na2O phase is not named. | Follow PDF p. 1 / published p. 1893: record the Raoultian pure-endmember convention with unknown phase and polymorph. Do not infer a phase or attach a reference pressure to this activity. Update the Figure 5 note to match. |
| Error estimates | Green includes only the estimated maximum error of 30%–40%. | The wave includes the two component estimates printed on p. 6: about 15%–20% for Kpc and 15% for maximum sulfate solubility. | Add both component strings and the exact maximum-error range to the context and observation; the p. 6 render confirms the assignments and values. |
| Fit and figure quantity gap | Green correctly keeps the fit equations as context and leaves Figure 5 undigitized. | The wave review confirms no activity-function quantity token exists and excludes Figure 5 point digitisation. | Keep both fits as context with no invented function token, and keep Figure 5 undigitized. The observation uses the existing scalar `activity` token. |

## Isolated migration

Green has 0 migrated observations for this extract. The merged extract adds
exactly one observation, for a delta of **+1**. The added row is
`beerkens_2003_float_glass_activity_at_1673K`: `ACTIVITY`, liquid Na2O-bearing
glass, 1673 K, approximate activity 1.7e-8, float-glass composition, p(O2)
33000 Pa, and p(SO2) 66600 Pa. Its reference convention is Raoultian pure
endmember with the specific endmember phase unknown. Its locators are PDF
page 6 / published page 1898 for the activity and pressures, and PDF page 4 /
published page 1896 for the composition. The existing green context ids remain
unchanged; the fit context is not duplicated.

The isolated migration assertions are in
`tests/test_beerkens_2003_sulfate_decomposition_na2o_activity.py`. Result:
zero registry issues and zero hard issues; one migrated observation with the
activity value, temperature, standard state, pressures, and located float-glass
composition asserted.

The focused local fallback, including the Beerkens source pins, the YAML comma
guard, and `tests/battery/test_migrate_composition.py`, completed with **128
passed in 196.65s**. Its mutation check changes the model-glass fit from
`exp(-43552/T + 8.33)` to `exp(-43553/T + 8.33)` and confirms the source pin
rejects the mutation. The remote gate request produced no output for more than
ten minutes and was stopped for the authorized local fallback.

A scan of tracked extracts found this is the only observation combining a
direct `point_conditions.composition` with a migrator-readable composition map
under `values`. The owner-level precedence fix therefore affects this added
row only; no existing migrated observation is expected to change.

A broader local attempt also included `tests/battery/test_migrate.py`; the
session watchdog reported a timeout and the 900-second foreground cap ended
that attempt with exit 124 before a final summary. This incomplete broad-suite
run is not counted as passing evidence.

No battery-store regen or extracts-v2 write is included. Expected store delta:
one added activity observation; no extra fit-context rows, since the fit facts
already exist in green's combined context row.

# t-1129 r3 — liquidus saturation rows

Ruling d-090 replaces r2's `LIQUIDUS_COMPOSITION` vector proposal. A printed
saturation row is a liquidus-temperature point at its printed liquid
composition:

- Store it as `Quantity.TRANSITION_TEMPERATURE`, subtype `liquidus`.
- Keep the printed temperature as the scalar point value.
- Put that row's printed liquid map in `identity.composition`, converted with
  `wt_pct_to_mole_fraction`; preserve explicitly printed zero components.
- Put the saturating solid(s) in `point_conditions` as a cross-check. The ruling
  does not name a key, so this note does not invent one.
- Görnerup reports no starting charge. Keep the experiment Sample's initial
  composition unknown; it must not inherit any row's liquid map.

The existing per-engine prediction allowlist must refuse these rows as
`quantity_not_predicted` before opening an engine. The liquidus-temperature
prediction branch belongs to t-1113. No ValueKind, quantity, profile, or store
schema change is needed.

Görnerup & Wijk 1996 Table 1 is encoded as a 39-row extract. Migration stores
each row's own liquid map (including printed zeros), printed temperature,
unknown Sample composition, and saturating solid in `point_conditions`. The
The derived observation store is not committed with offers. Under train-only
store generation (d-255), offers carry the extract and code, and the train
regenerates once per batch at the gate. Offer authors can run
`scripts/check_store_freshness.py --pre-gate` to label stale migrate inputs as
awaiting that gate regen. If the gate regen is a fixed point and inputs
changed, the train records the store-neutral acknowledgement in
`scripts/store_neutral_acks/<sha>.txt`, citing the gate regen. Migrating this
extract is expected to add 39 liquidus observation rows.

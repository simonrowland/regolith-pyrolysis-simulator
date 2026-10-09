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

The r2 note identifies Görnerup & Wijk 1996 Table 1 as a 39-row corpus extract.
That extract is absent from this simulator checkout, so its data rows are not
encoded here. Once the corpus source is available, verify each row's own liquid
map, printed zeros and temperature, unknown Sample composition, and solid in
`point_conditions` before regenerating the derived store.

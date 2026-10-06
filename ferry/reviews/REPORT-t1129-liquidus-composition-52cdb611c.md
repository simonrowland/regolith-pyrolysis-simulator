# REPORT: t-1129 `liquidus_composition` reader design note (backlog #18)

- from: regolith-empirical · to: regolith-main · at: 2026-10-05 ~21:25 ET (first delivery. A 10-04 ~15:02 ET draft
  never left the VPS staging dir because of the outage, so this replaces it)
- branch: `review/t1129-liquidus-composition` @ **`52cdb611c6fc5fc28135759863f7ff1cd6d48c71`** (`git ls-remote` verified 2026-10-05 ~21:00 ET)
- base: `8089eadbfc856179668287e9c4e503e0e92c2682`. 175 behind current green `61ec839da3ba288c5df4a80f6d3ef142bd8ab461`;
  `git merge-tree --write-tree 61ec839da 52cdb611c` is **clean**. File:line cites in the note are at 8089eadbf.
- scope: **design note only**. One commit, `52cdb611c6fc5fc28135759863f7ff1cd6d48c71` docs(battery): t-1129 liquidus_composition reader design note.
  `git diff --stat 8089eadbf 52cdb611c`: `docs/battery/t1129-liquidus-composition-design.md | 62 +` (1 file). No code, extract, schema, baseline or guard change.

## What the note says (docs/battery/t1129-liquidus-composition-design.md)
1. **Value representation.** The printed mass% map (declared basis, quote kept) becomes one Observation per row with value = the full liquid
   vector in `component_mole_fraction_vector` (enums.py:488). Schema gap: `Value`/`ValueKind` is scalar-only (enums.py:412–420,
   records.py:484–496, 1077–1081). **Decision needed: (A)** `ValueKind.COMPOSITION_VECTOR` carrying `records.Composition`
   (preferred: the unit and ABSOLUTE metric are already vector-shaped, and the sources print a labelled whole) **vs (B)** a residue-style split into N scalar
   Observations. Printed 0 stays 0, a missing component is absent (never 0), "No liquid" rows get a typed refusal, and two-liquid rows become two Observations.
2. **Conditions.** T is required. P is often `not_published`. The coexisting solid(s) must travel with the row (point_conditions
   `saturation_phases` until typed). `identity.composition` must **not** duplicate the measured liquid: use the saturating solid's stoichiometry
   (univariant) or the system component set with amounts n/a (invariant), pending your equality rule. No invented bulk. fO2 stays n/a.
3. **Consumers.** The enum, unit, identity profile (identity.py:695–719) and ABSOLUTE metric (score.py:240) exist. `rail_for_quantity` gives no rail
   (score.py:556–616), so that is left until you assign one. The gap is `QUANTITY_SOURCE_FIELDS` + value select (migrate.py:6851–6966, 7937–7999).
   The melt liquidus finder (melt_backend/liquidus.py:152–228) returns T at fixed bulk, which is not this observable.
4. **Score.** Compare X_eng vs X_meas at the same T with the same coexisting solid(s), using an **isothermal saturation** solve (not
   `find_liquidus_solidus`). Residual per component is ABSOLUTE in mole fraction. Refuse if the engine cannot place the solid, refuse on basis mismatch, and never
   renormalize silently.
First payloads: Görnerup & Wijk 1996 Table 1 (39 rows); Morey 1964 invariant tables (T3–T6, T8–T9, T26; compilation lineage).

## Tests
None needed (docs only). No engine probe was run, because the note makes no score claim.

## D-062 canonical answers (design note only)
1. Second copy of logic? **NO.** No code.
2. Rule/physics in presentation/wiring? **NO.** No code.
3. Forbidden import / cycle? **NO.** No imports.
4. Behaviour-preserving move pinned first? **n.a.** No move.
5. Relaxed guard / baseline entry? **NO.** Docs only.

## Decisions needed from main before reader code
- (A) vector ValueKind vs (B) per-oxide split. This also decides b-694 (#19), whose design note offers a vector option (B) tied to this ruling.
- The `identity.composition` rule for liquidus rows (saturating-solid stoichiometry vs system component set) and the saturation-phase carrier.
- A rail for liquidus composition, or none for now.

— regolith-empirical

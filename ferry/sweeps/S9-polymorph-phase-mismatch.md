# S9 — polymorph / phase identity mismatch

**Repo tip (write branch):** `1174258a2` (`empirical/reviews-2026-09-22`).  
**Code base audited:** `origin/review/janaf-batch-2026-09-22` (`a49cef012`) via worktree `/workspace/repos/wt/slot-09`. READ-ONLY on product code; writes only to `ferry/sweeps/` (+ ferry-inbox copy). Do not push.  
**Scope:** `simulator/reference_data/`, `simulator/melt_backend/`, `simulator/vapour_rail/`, `simulator/battery/generators/`.  
**Predicate:** code pairs values for "the same substance" that are actually different phases / polymorphs / standard states (crystal vs liquid vs glass, polymorphs, gas vs condensed, reference-state switches inside a JANAF table) **without refusing or labelling**.  
**Seed:** MAGEMin `en` is orthoenstatite vs JANAF MgSiO3 clinoenstatite; SiO2 quartz vs cristobalite.

**Seed disposition on this base:**  
- **MgSiO3 ortho vs clino:** no MAGEMin↔JANAF pure-phase pairing code on `a49cef012` (`magemin.py` has no `pure_phase_properties` / `orthoenstatite` registry — that surface lives on the separate R5 / `janaf-p4a1-pure-phase` line). JANAF Mg-012 emits crystal segments with roman `i`/`ii`/`iii` (not mineral `clinoenstatite`/`orthoenstatite`); `polymorph_dictionary` has **no** MgSiO3 mineral row. Identity compare requires a resolved crystal polymorph, so a future ortho-labelled engine value would not silently equal these rows — but nothing in scope today **pairs** the two. Counted under no-hit melt_backend for this seed.  
- **SiO2 quartz vs cristobalite:** JANAF generator + dictionary correctly keep O-035/O-036/O-037 as distinct polymorph tokens. The seed still fires in `reference_data/nasa_glenn.py` via normalized `phase=condensed` for `a-qz` / `b-qz` / `b-crt` (below).

**Related prior review (not re-scored as fixed):** R7 notes glass/`state: l` boundary non-split as pre-existing / out of R7 intent — it is in-class for S9 and is site 1.

| site (file:line) | predicate match | trigger input | live? | severity |
|---|---|---|---|---|
| `simulator/battery/generators/janaf.py:1425-1435` + `:707-720` (`generate_table` boundary collection + `_segments` single-phase early return) | Index `state: l` tables that print `GLASS <--> LIQUID` never become segment boundaries (gate only admits `_COMBINED_STATES` or `cr`+named crystal↔crystal). `_segments` then stamps **one** `Phase.L` segment for the whole T series. Glass-region thermo points share liquid species identity with no glass label / refusal. Transition rows themselves stay phase-unknown (R7); the **series** observations do not. | Any of 95 live JANAF `state: l` tables with a `GLASS <--> LIQUID` short row (e.g. Mg-013 MgSiO3, Na-024, W-003). Probe Mg-013 `cp:segment-0`: series spans 298.15–900 K (glass Cp climb) and 900–3000 K (liquid plateau) under `species.phase=l`, `phase_basis=phase declared by JANAF index state 'l'`. | live | P0 |
| `simulator/reference_data/nasa_glenn.py:403-460` (`resolve_phase`) + `:1136-1142` (`phase_ordinal` backfill) | Parenthetical mineral suffixes `a-qz` / `b-qz` / `b-crt` (and peers outside the closed allow-list) normalize to **`phase=condensed`**. Quartz and cristobalite therefore share the same normalized phase token for formula `SiO2`. Records are not merged and `phase_as_published` / name / ambiguity are retained; `phase_ordinal=phase_flag` only when multiple `(formula, phase)` mates exist — but any consumer keying `(formula, phase)` alone treats them as one condensed identity. Burcat reuses this resolver. | Live NASA-Glenn records NG-1862 `SiO2(a-qz)`, NG-1863 `SiO2(b-qz)`, NG-1864 `SiO2(b-crt)` all store `phase: condensed` (seed quartz vs cristobalite). | live | P1 |
| `simulator/vapour_rail/catalog.py:3403-3438` (pure-psat condensed record select) | Condensed thermo pick order is `condensed_thermo_record` → `thermo_by_key["condensed"]` → **`condensed_solid`** → `condensed_liquid`. No check that the chosen record’s `standard_state` matches `pure_condensed_phase_identity.phase` (`condensed_solid` \| `condensed_liquid`). No T-dependent solid↔liquid switch; solid can silently serve a liquid-declared identity (or generic `condensed` default at `:3427`). | Catalog species whose `phase_properties` carry both solid and liquid (or identity liquid + only solid thermo). **No** dual solid+liquid `phase_properties` block found in live `data/vapor_pressures.yaml` today. | latent | P1 |
| `simulator/vapour_rail/nasa_cea.py:559-591` (`NasaCeaPolynomial.pure_psat_over_Pstd`) | Construction-time guards are only `self.standard_state == "gas"` and `condensed.standard_state != "gas"`. Docstring states formula/name are **not** compared; `condensed_solid`, `condensed_liquid`, and bare `condensed` all qualify. Wrong-phase or unrelated condensed Gibbs is exponentiated as vaporization. Catalog compile uses this path after the preference order above. | Compile/evaluate pure-psat with a condensed_solid polynomial while identity/domain is liquid (or mismatched formula), once a catalog row supplies that pair. | latent | P1 |
| `simulator/melt_backend/vaporock.py:2471-2505` (`_strip_gas_suffix`) | `(g)`-marked oxide-colliding gases become `SiO2_gas` etc.; **bare** `SiO2` / `FeO` / … are returned unchanged and collide with melt `OXIDE_SPECIES` keys (gas vs condensed under one string). Documented in-module; live System path emits `(g)` markers so the collision is latent for production VapoRock results. | Mock / legacy / non-System pressure dict using bare `SiO2` as a gas key merged into melt-keyed maps. | latent | P2 |

## Counts

- Sites: 5  
- Live: 2  
- P0: 1 · P1: 3 · P2: 1 · P3: 0  

## No-hit areas (in scope, audited)

- **`simulator/melt_backend/magemin.py` (seed ortho/clino):** no pure-phase / enstatite polymorph registry or JANAF crosswalk on this base; assemblage payload keeps engine phase name strings without formula-only collapse to JANAF Mg-012.  
- **`simulator/battery/generators/janaf.py` SiO2 polymorph tables:** O-035/O-036/O-037 resolve via `JANAF_CRYSTAL_DICTIONARY` to `cristobalite_high` / `cristobalite_low` / `quartz` (+ O-037/O-039 I/II → alpha/beta aliases); not collapsed.  
- **`simulator/battery/generators/janaf.py` formation reference-state axis:** `delta_fH` / `delta_fG` / `log10_Kf` stamp `reaction` + `formation_elements` as **unknown** with `FORMATION_BASIS_REASON` (temperature-specific elemental refs) — labelled, not a silent concrete wrong reference_state.  
- **`simulator/battery/generators/janaf.py` transition rows with unequal phases:** `_transition_species_phase` refuses one-sided assign (glass→l, cr→l, etc. stay unknown) — R7 surface; not an S9 hit.  
- **`simulator/battery/generators/usgs_b1259.py` / `usgs_b1452.py` / `usgs_b1544.py`:** crystal paths go through `resolve_printed_name_polymorph` / `PHASE_SPLITS`; oxide Si reference explicitly `_crystal("SiO2", "quartz")` (labelled). Al2SiO5 polymorphs identity-mismatch on polymorph (tested).  
- **`simulator/battery/generators/bench.py`:** waypoint/authority packaging only; no thermo phase pairing.  
- **`simulator/vapour_rail/activity.py`:** exact `StandardStateIdentity` + reviewed phase/endmember map; `STANDARD_STATE_MISMATCH` / unmapped endmember refusals.  
- **`simulator/vapour_rail/catalog.py` `pure_condensed_phase_identity`:** requires explicit `condensed_solid` \| `condensed_liquid` and phase-tagged reactants when reaction-anchored; `_strip_phase_suffix` used for formula concordance with separate phase field — not an unlabelled collapse.  
- **`simulator/melt_backend/imcc_sf04/gas.py`:** SiO2(l)/SiO2(g) window misses are **refused** with reasons (not silently substituted).  
- **`simulator/melt_backend/sulfsat.py` Frost QFM:** alpha/beta quartz branch is an internal buffer formula, not a substance identity stamp.  
- **`simulator/reference_data/janaf.py` `formula_normalised`:** strips phase suffix for index keys; each table retains its own `state` / separate file (no cross-polymorph merge).  
- **`simulator/reference_data/burcat.py` `crosscheck_xml`:** matches on `(name, phase, Tmin, Tmax)`; mismatches recorded, not fixed.  
- **`simulator/vapour_rail/shomate.py`:** requires an explicit `standard_state` convention; no formula-blind pure-psat helper analogous to CEA’s.

SWEEP: S9 | sites=5 | live=2 | P0=1 P1=3 P2=1 P3=0 | no-hit areas: melt_backend/magemin (no pure-phase en/clino pair on base); generators JANAF SiO2 dictionary + formation unknown-ref + transition span refuse; USGS generators polymorph/PHASE_SPLITS; bench.py; vapour_rail activity exact SS; catalog pure_condensed_phase_identity tagged path; imcc SiO2 window refuse; sulfsat QFM internal; janaf formula_normalised; burcat crosscheck; shomate SS require

# t-1129 — Design note: reader path for `Quantity.LIQUIDUS_COMPOSITION` (r2)

Author: regolith-empirical. r1 2026-10-04 (cites at `8089eadbf`); r2 2026-10-05, revised against main's review of record (FIX-FIRST, design).
**Every file:line in this note is on green `61ec839da` (`work-v064-green`).** Design only: this commit changes no reader, schema, guard,
extract or derived store. The probes quoted below were run in-process on that tree with `open_battery_engine` patched.

## 0. Scope: which rows this quantity admits

A row is a saturation liquidus composition only if it prints all three of: (1) a numeric map of the liquid's components, (2) a temperature,
(3) the coexisting solid. A row that lacks any of the three stays a **typed absence**. Nothing else is admitted under this token.

- **On-tree instance.** The only `quantity: liquidus_composition` payload in this checkout is
  `jaggi-2021-mercury-atmosphere::jaggi_2021_table2_magma_ocean_composition_input`
  (`data/literature/extracts/jaggi-2021-mercury-atmosphere.yaml:48`, quantity at `:60`; derived record at
  `data/literature/extracts-v2/jaggi-2021-mercury-atmosphere.yaml:917`). It is a model magma-ocean composition-input set: species formula
  `Mercury_model`, phase `l`, temperature unknown, composition unknown, evidence `compilation_assessed`, value `unavailable`
  ("no mapped liquidus_composition field selected; source fields: quantity, method_class, evidence_class, source_compositions"). It prints no
  liquid map at a temperature against a solid, so it **stays a typed absence** under this rule. Whether it should carry this token at all is a
  corpus-side question (§6 Q8).
- **The target payloads are not in this checkout.** Görnerup & Wijk 1996 and Morey 1964 were extracted in the corpus repo (Görnerup extraction
  report, corpus commit `ac22bd89c4ad`). Neither is a file under `data/literature/` here, so this note makes no claim that they are extracted
  in this tree. As the corpus report transcribes it:
  - Görnerup Table 1: 39 rows (15 at 1600 °C, 1 at 1650, 21 at 1700, 2 at 1750). Each row prints **one** saturating solid as printed: CaO
    (12 rows), 3CaO·SiO2 (11), 2CaO·SiO2 (16). No polymorph is printed. The liquid is printed as **Al2O3 / CaO / SiO2 wt%**, "normalized
    with respect to the three components after analysis"; MgO and MoO3 were analysed and are not in the table. Four CaO–SiO2 binary rows
    (18.6, 18.10, 18.9, 18.11) print Al2O3 = 0, and four CaO–Al2O3 rows (19.6, 19.3, 19.4, 19.9) print SiO2 = 0.
  - Morey 1964 invariant tables print a reaction per row, often with two or more solids, plus "No liquid" rows. The r1 row counts and the
    two-liquid examples were not checked against rows here. Multi-solid rows are out of scope for the first reader (§2).

## 1. Problem (on green)

- `Quantity.LIQUIDUS_COMPOSITION` is `simulator/battery/enums.py:90` (line 89 is the neighbour `CONDENSATE_COMPOSITION`). Unit
  `component_mole_fraction_vector` is `enums.py:490`. Metric `MetricOperation.ABSOLUTE` is `simulator/battery/score.py:241`.
- Identity profile: `simulator/battery/identity.py:712-736` puts liquidus in one branch with viscosity, density, electrical conductivity and
  Fe3/Fe2. It requires `temperature_K`, `total_pressure_Pa`, `composition` (`:719`) and marks `fO2_Pa` (`:723`), `reservoir` (`:730`) and
  `subtype` (`:735`) not applicable.
- `QUANTITY_SOURCE_FIELDS` (`simulator/battery/migrate.py:6966-7081`) has no entry for this quantity, so `select_declared_source`
  (`migrate.py:7778`) ends at the unavailable return `migrate.py:8140-8144`. `empty_value_from_payload` is `migrate.py:8147`.
- `ValueKind` (`enums.py:413-422`) is POINT … EXPRESSION, then UNAVAILABLE. No member carries a composition. The payload map is
  `simulator/battery/records.py:483-497`; `Observation.value` is a `Value` (`records.py:1081`).
- No `Rail` fits (`enums.py:39-50`). `rail_for_quantity` (`score.py:557-602`) returns `None`; `no_headline_rail_reason` (`score.py:605-617`)
  gives `no_headline_rail:liquidus_composition`. The quantity is not in the method-scoped apparatus set (`simulator/battery/validity.py:326-345`).

## 2. Identity: what fixes the point, and what is compared

`identity_equal` compares `species` (`identity.py:1345`) and then only the axes in `_AXIS_NAMES` (`identity.py:319-335`, loop `:1353-1397`).
`Observation.point_conditions` (`records.py:1089`) is **not** an equality key, and `saturation_phases` occurs nowhere under `simulator/` or
`tests/`. A solid stored only in `point_conditions` cannot make two rows comparable. Before two rows can be required to share solid S, the solid
has to sit on a compared axis (or the scorer has to grow a reader of that key). r2 proposes the first.

| Axis | r2 rule | Evidence on green |
|---|---|---|
| `subtype` | **Not used.** No solid, assemblage or label goes on subtype (r1's subtype carrier is withdrawn). | Profile marks it n/a (`identity.py:735`). Probe: `fill_identity(..., subtype=State.of("3CaO.SiO2"))` stores n/a (rewrite at `migrate.py:6912-6915`). A raw subtype value fails `validate_quantity_profile` as `invalid_identity` on `subtype` (`identity.py:1257-1262`). |
| `point_conditions` | Provenance only. Never the carrier that makes two rows comparable. | Not in `_AXIS_NAMES`; no reader of `saturation_phases`. |
| coexisting solid | **Proposal (profile change, §6 Q2):** carry the single saturating solid on the existing compared axis `reservoir` (`State[Species]`, `identity.py:384`): phase `cr`, formula = printed stoichiometry (`CaO`, `Ca2SiO4`, `Ca3SiO5`), polymorph `State.unknown("not printed")` where the source names none. | `reservoir` is already the compared "condensed phase in equilibrium" axis for `p_sat` (`identity.py:543-544`; `_reservoir_rule` `:1281-1313`). A Species value compares through `_species_equal` (`identity.py:1136-1137` → `:901-930`). Probe: same solid, unknown polymorph → `identity_unknown` on `species.polymorph`; `Ca3SiO5` vs `Ca2SiO4` → `identity_mismatch` on `species.formula`. A crystal must carry a polymorph State that is a value or unknown (`simulator/battery/validate.py:510-528`). Today the profile strips it: probe `fill_identity(..., reservoir=...)` stored n/a. |
| multi-solid invariant (Morey) | **Typed absence** in the first reader. One `Species` cannot hold an assemblage, and `point_conditions` is not used in its place. | `reservoir: State[Species] \| None` (`identity.py:384`). |
| `identity.composition` | **Rule that is storable today:** `State.unknown`, never a value. It is not the measured liquid, not the solid stoichiometry, not an invented bulk (Görnerup prints no starting recipe). `fill_identity` already writes `State.unknown("no composition mapped from source")` when nothing is mapped (`migrate.py:6897-6904`), and every pair then compares `identity_unknown` on `composition`. **Proposal (§6 Q2):** give liquidus its own profile branch with `composition` permitted n/a, by the same reasoning as `TRANSITION_TEMPERATURE`, which marks its own observable axis n/a ("no duplicate T coordinate", `identity.py:672-675`). | r1's "amounts unknown / not applicable" was not storable. Probe: `fill_identity(..., composition=State.not_applicable(...))` rewrote it to unknown (`migrate.py:6897-6904`). A forced n/a fails `validate_quantity_profile` as `invalid_identity` on `composition` (`identity.py:1263-1268`). `Composition` rejects a non-finite amount (`records.py:366-370`): `CaO = NaN` raised. r1's univariant "solid stoichiometry on `identity.composition`" is **withdrawn**: a filled composition opens the engine (§4). |
| `species` | Liquid, phase `l`. **`Species.formula` token:** under (A), a system label naming the printed liquid components, e.g. `CaO-Al2O3-SiO2` (spelling and ordering for main, §6 Q3). Under (B), the oxide (`CaO`, `Al2O3`, `SiO2`). | `Species.formula` is required (`records.py:268-269`) and always compared (`identity.py:904`). Probe: `Species("CaO-Al2O3-SiO2", Phase.L)` keeps the label with charge 0. |
| `temperature_K` | Required; the printed T. | `identity.py:719`. |
| `total_pressure_Pa` | Required; `State.unknown("not_published")` unless printed (Görnerup: argon atmosphere, no pressure printed). | `identity.py:719`. |
| `fO2_Pa` | Not applicable. | `identity.py:723`. |

## 3. Value: the scored liquid and the conversion

- **Printed liquid components.** The scored liquid is exactly the components the source prints for the liquid. For Görnerup that is
  **Al2O3, CaO, SiO2**: a three-component CaO–Al2O3–SiO2 liquid as normalized by the authors, not a CaO–SiO2 join. Printed zeros (Al2O3 on the
  four 18.x rows, SiO2 on the four 19.x rows) stay 0. MgO and MoO3 go into provenance as omitted analysed components; they are never
  re-inserted and the vector is never renormalized around them. `Composition.basis` names the printed normalization.
- **Representation (§6 Q1).** (A) a new `ValueKind.COMPOSITION_VECTOR` carrying a `records.Composition` (`records.py:349-371`,
  `amount_basis=MOLE_FRACTION`), which needs a `_VALUE_PAYLOAD` entry (`records.py:483-497`), a `Value` field (`records.py:509-538`) and the
  codec (`_value_from_plain`, `migrate.py:1389`). (B) N scalar POINT observations, one oxide per species. r2 still prefers (A): with
  `composition` unknown/n/a, (A) keys the liquid's system through `Species.formula`, whereas under (B) `CaO` in a CaO–SiO2 liquid and `CaO`
  in a CaO–Al2O3–SiO2 liquid at the same T and solid would share one identity.
- **Converter owner.** `wt_pct_to_mole_fraction` (`migrate.py:2891-2912`) owns mass% → mole fraction. This path **cannot call it unchanged**:
  it `continue`s past any key outside `_OXIDE_COMPONENT_KEYS` (`migrate.py:2825-2842`; skip at `:2898-2899`) and then normalizes, so a
  dropped oxide is a silent basis change. Probe: CaO 70 / SiO2 30 / MgO 0 / MoO3 0.4 wt% → CaO 0.71429, SiO2 0.28571, MgO 0; MoO3 is gone
  with no notice. (Al2O3, CaO, SiO2 are all in the allowlist, so Görnerup would not lose a printed component, but the reader must not depend
  on that.) **Plan:** extend the owner with a keyword-only strict mode that raises on any key outside the allowlist; the default stays the
  current behaviour, pinned before the change. No second converter. Omitted keys are recorded in provenance by the reader; the function
  does not do that.
- **Source key.** `composition_mass_percent` is already a charge-composition key (`_PRINTED_COMPOSITION_MAP_KEYS`, `migrate.py:2843-2850`,
  special-cased at `:2969`). `_initial_oxide_map_from_values` (`migrate.py:2998`) feeds it into the Sample's initial composition
  (`migrate.py:9306-9319`). The Görnerup corpus extract stores each Table 1 liquid under `composition_mass_percent`. The liquid map must
  therefore move to a dedicated key outside that tuple, registered in `QUANTITY_SOURCE_FIELDS` for this quantity only (name for main, §6 Q6);
  otherwise the measured liquid would be read a second time as the charge. r1's "add `composition_mass_percent` later" is withdrawn.
- **Zeros on the way back.** `composition_wt_pct` (`score.py:1459-1486`) drops zero-mass components (`:1486`). Probe: CaO 0.75 / SiO2 0.25 /
  Al2O3 0 → `{CaO: 73.68, SiO2: 26.32}`, Al2O3 gone. The measured vector is never routed through it.

## 4. Engine predict: refuse before any composition is stored

`predict_with_engine` (`score.py:2966`) has no liquidus branch. Current outcomes for a `liquidus_composition` observation (alphaMELTS,
1873.15 K, 101325 Pa):

1. Formula `CaO`, phase `l`, composition unknown → `unsupported` / `quantity_has_no_engine_pot`; engine not opened (`score.py:3263-3273`).
2. Same, composition CaO 0.75 / SiO2 0.25 mole fraction (C3S stoichiometry) → **engine opened**. `score.py:3222-3225` treats any filled
   composition as the melt pot (`composition_wt_pct` gives CaO 73.68 / SiO2 26.32 wt%). After a successful cell, `score.py:3715-3716` reads
   activities and pressures for any quantity that is not melt-activity or vapour, and `score.py:3786-3791` can return that scalar with
   `unit = QUANTITY_UNITS[quantity]` (`score.py:3622`), i.e. `component_mole_fraction_vector`.
3. Formula that does not parse (`liquid`, and also the system label `CaO-Al2O3-SiO2`) → `not_probed` / `species_formula_unparsed`
   (`score.py:3055-3065`).

So r1's "falls through / unsupported" holds only for case 1, and case 3 refuses only by accident of the formula. **Required before any reader
stores a value on this quantity:** an explicit early `unsupported` / `quantity_not_predicted` return with the same shape as the residue return
at `score.py:3042-3053`, placed before the formula parse at `score.py:3055`, so it runs before the T check (`:3098`) and the pot selection
(`:3222`) whatever the formula, T or composition. It is one set membership feeding one return, not a copy of the residue block, and it gets
its own pin and mutation proof in the reader commit. Scoring stays refused until an isothermal-saturation engine path and a vector residual
exist: `compute_metric` (`score.py:666`, ABSOLUTE at `:686-687`) and `point_magnitude` (`score.py:769-772`) take only a scalar
`Value.point`, and the `QUANTITY_METRIC` entry (`score.py:241`) computes no component-wise residual. `find_liquidus_solidus_by_fraction`
(`simulator/melt_backend/liquidus.py:299`) returns T at fixed bulk, the inverse observable; `LiquidFractionPathPoint.liquid_composition_wt_pct`
(`liquidus.py:212-215`) is an engine-side output shape only.

What a future score compares, once the above exists: X_meas (printed liquid, mole fraction, printed components only) against the engine's
equilibrium liquid at the same T (and P when known) in contact with the same solid, per component, ABSOLUTE in mole fraction. Refuse if the
engine cannot place the solid at T, and refuse on any component-set mismatch. No summary statistic is proposed.

## 5. Consumers (on green)

| Consumer | File:line | Today | After the plan |
|---|---|---|---|
| Enum + unit | `enums.py:90`, `:490` | present | unchanged |
| Identity profile | `identity.py:712-736` | T, P, composition required; reservoir, subtype n/a | own branch: T, P, reservoir required; composition n/a (if ruled), else unknown |
| Selector | `migrate.py:6966-7081`, `:7778`, `:8140` | unavailable (Jäggi row) | dedicated liquid key selects; rows missing map, T or solid stay typed absence |
| Engine predict | `score.py:2966`; `:3055`, `:3222`, `:3263` | three outcomes in §4 | `quantity_not_predicted` for every engine |
| Metric | `score.py:241`, `:666`, `:769` | scalar only | unchanged; no residual |
| Rail | `score.py:557-617` | `None` | `None` until main assigns one |
| Validity apparatus | `validity.py:326-345` | quantity not in the set | unchanged |
| Melt finder | `liquidus.py:152`, `:212-215`, `:299` | T at fixed bulk | unchanged |

## 6. Decisions needed from main before code

1. Value: (A) `COMPOSITION_VECTOR` or (B) per-oxide split. r2 recommends (A).
2. Profile: a liquidus-only branch with `reservoir` required (single solid) and `composition` permitted n/a. Fallback that needs no profile
   change for composition: it stays required and is always unknown; the solid still needs a compared axis.
3. The `Species.formula` system label under (A), and its component ordering.
4. Same-solid rows with an unprinted polymorph compare `identity_unknown` on `species.polymorph`. Accept, or rule otherwise?
5. Multi-solid invariant rows (Morey): typed absence until an assemblage axis is typed?
6. The dedicated liquid-map source key (outside `_PRINTED_COMPOSITION_MAP_KEYS`), and the corpus-side rename of the Görnerup map.
7. Strict mode on `wt_pct_to_mole_fraction` (the owner) rather than a reader-side precheck.
8. Jäggi Table 2 parent: keep it as a typed absence under this token, or retype it corpus-side?
9. `CONDENSATE_COMPOSITION` has the same unit and metric and no reader. Put it in the same not-predicted set now, or leave it?

## Out of scope (this commit)

No reader, ValueKind, profile, converter, guard, extract, baseline or store change. The guard this note relies on today is the final
unavailable return in `select_declared_source` (`migrate.py:8140`). Implementation waits on §6.

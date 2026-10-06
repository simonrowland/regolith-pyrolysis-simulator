# b-716 — Design note: composition ROLE for initial vs point melt

Author: regolith-empirical (seat worker). 2026-10-04 ~15:20 ET.
Tip when written: `8089eadbf` (`work-v064-green` / branch base). **Design only — no reader/schema code until main reviews.**
Motivating payloads: Hastie 1981 NBSIR Table 2 originals (K1, Western, Eastern, K2) and Fig. 5 KMS(3). Scout: ferry `hastie-fig11-scout-report.md`. Unlocked after b-718 (`review/b718-sample-inheritance`).

## Problem

Table 2 footnote a prints **initial** oxide wt% for K1 / Western / Eastern / K2. Those same rows’ pressure fits (and Fig. 11 K₁ γ points; Fig. 5 KMS(3) Na₂O 15.6→13.3 vs initial 17) describe a **depleted** state. After b-714 the five Table 2 maps bind as `composition_wt_pct` and the migrator dual-attaches them via `_located_printed_and_initial` (`migrate.py:3167–3179`) onto both `point_conditions.printed_composition` and `point_conditions.composition` (`migrate.py:12725–12735`). Consumers then treat that map as the **melt at the measurement point**.

Needed: a composition **ROLE** every consumer honours:

| Role | Meaning | Engine / score use |
|---|---|---|
| `printed_analysis` | Printed analysis that *is* the point (or run) composition | Allowed as melt / identity composition |
| `calculated_from_print` | Authors’ composition calculated from print + stated derivation | Allowed as melt **only** with `Located.inference` / `Derivation` retained; never invent derivation |
| `initial_charge_only` | Starting charge / Table 2 “initial compositions” while the datum is depleted | Charge / inventory / residue-start only — **not** point melt |
| `two_phase_bulk` | Bulk in a two-phase region, not the liquid composition | Already refused for single-liquid engines |

## Existing field that can carry role (no new Composition attribute)

**Prefer `Composition.proxy_flag` (`records.py:353`).**

- Already an optional closed-ish string on every typed `Composition`.
- Already branched by migrator notices and scorer (`proxy_flag == "composition_from_sample_catalog"` at `migrate.py:3008`, `12224–12235`, `13062–13077`; `score.py:774–796`).
- Does **not** overload `Composition.basis` (required engine-basis token: `printed_oxides` / `printed_mole_fraction` / `sample_catalog_proxy` — `records.py:350`, `migrate.py:2902,3059,3007`).
- Does **not** overload `analysis_selection_rule` (catalogue selection prose only).

Proposed vocabulary (additive; absent/`None` = today’s default = **printed_analysis**):

| `proxy_flag` | Role |
|---|---|
| *(absent)* | `printed_analysis` |
| `composition_from_sample_catalog` | (existing) catalogue proxy |
| `initial_charge_only` | **new token** — starting analysis must not feed point melt |
| *(two-phase)* | keep existing notice/phase path (below); optional mirror flag later |

**`calculated_from_print`:** do **not** invent a second flag. Carry it with the existing `Located.inference` / `Derivation` on the composition (`records.py:407+`; waypoints already surface relation notices at `waypoints.py:833–879`). A calculated map without inference is a typed hard issue, not a silent point melt.

**`two_phase_bulk`:** already carried without a new field — `notice.band == two_phase_bulk_composition_not_liquid_composition` / phase reason marker (`migrate.py:3765–3766,13083–13093`; `score.py:147–148,4148–4164,4298–4305`). Leave that path.

**Mapping-form `printed_composition`:** when the extract only has a wt% Mapping (not yet a `Composition`), either (a) stamp `proxy_flag` on the typed residual that `_located_printed_and_initial` builds for `point_conditions.composition`, or (b) omit attaching initial-only maps to melt channels and keep them on `sample.initial_composition` alone — still stamp `proxy_flag=initial_charge_only` on that `Composition` so `normalized_composition`’s initial route cannot promote it.

Reject as carriers: `Composition.basis` (engine basis), `Sample.composition_class` (material class string), free-form locator notes (consumers do not parse notes).

## Smallest schema + reader change

1. **Schema:** zero new fields. Document the `proxy_flag` tokens above (and that absent means printed analysis).
2. **Reader (later commit, not this note):**
   - When extract declares initial-only (Table 2 footnote a / `starting_glass_wt_pct` / explicit `composition_role: initial_charge_only` / Hastie K1·Western·Eastern·K2 + Fig. 5 KMS(3) starting glass), set `Composition.proxy_flag = "initial_charge_only"` on the typed composition and **do not** place that map on melt-facing `point_conditions.composition` for depleted-point rows (keep on `sample.initial_composition` / charge helpers).
   - Preserve `Located.inference` for author-calculated maps (`calculated_from_print`).
   - Leave two-phase notice emission unchanged.
3. **Extracts (later):** Hastie Table 2 K1/Western/Eastern/K2 and Fig. 5 glass initial maps declare the role; no invented depleted point compositions (scout forbids assumption vectors as published points).

## Consumers that must honour the role (tip `8089eadbf` file:line)

| Consumer | File:line | Honour rule |
|---|---|---|
| `_printed_point_composition` | `score.py:2595–2610` | Return None when `proxy_flag == "initial_charge_only"` (today accepts any inference-free Composition). |
| `_comparison_identity` | `score.py:2630–2674` (uses point at `:2655`) | Must not lift initial-charge maps onto comparison identity.composition. |
| Engine predict fallback | `score.py:3122–3127` | Same — vapour-equilibrium path must not use initial-charge as melt. |
| `_catalogue_composition_notice` | `score.py:774–796` | Already honours catalog `proxy_flag`; extend pattern for `initial_charge_only` notice if useful. |
| `_is_bulk_not_liquid_composition` + refuse | `score.py:4148–4164`, `:4298–4305` | Already honours `two_phase_bulk` via notice/phase — keep. |
| `normalized_composition` | `waypoints.py:716–922` (routes `:732–733`, printed prefer `:911`) | Skip / refuse melt route when selected Composition (or residual) has `proxy_flag == "initial_charge_only"`; charge helpers (`:362–440`) may still read `sample.initial_composition`. |
| `_melt_composition` / engine_point readiness | `generators/bench.py:315–323`, `:76–91`, `:383+` | Inherits waypoint selection — must not get initial-charge as melt. |
| `_observation_composition` (species coverage) | `validity.py:902–917` | May read initial for **coverage** census; must not treat it as liquid X for single-liquid activity when flagged initial-charge / two-phase. |
| Migrator attach | `migrate.py:3167–3179`, `:12725–12735` | Stop dual-promoting initial-only maps onto point melt channels; stamp `proxy_flag`. |
| Migrator catalog / two-phase | `migrate.py:2971–3016`, `:13083–13093` | Existing role precedents — mirror for initial-charge notices if desired. |
| Identity profiles | `identity.py` composition reqs (`:328+`, activity/γ profiles) | Incomplete identity when only initial-charge is available for a depleted point — prefer unknown composition over false melt. |

`comparison_candidates` (`score.py:4779+`) needs no special role branch once `_printed_point_composition` / waypoints refuse initial-charge as melt; admission/evidence gates stay separate (scout: Hastie-31 still superseded / figure_only).

## Hastie binding (why this unlocks after b-718)

- Table 2 K1/Western/Eastern/K2: printed maps are **initial**; Western/Eastern even print run-range K₂O (18.9–17.6 / 23.3–22.1); K1 fit note “[K₂O] ~14” vs initial 19.5.
- Fig. 5 KMS(3): sample `printed_composition` 17/12/71 is starting glass; point Na₂O range 15.6–13.3 — initial only.
- Fig. 11: only x(K₂O) remaining is printed; full oxides **not** calculable without assumptions (scout) — do not store assumption vectors as `printed_analysis`.
- b-718 fixed foreign sample inheritance; this note is the role semantics those (and future) bindings must declare so consumers stop treating initial as point melt.

## Out of scope (this commit)

No `migrate.py` / `score.py` / `waypoints.py` edits, no extract edits, no baseline/guard changes, no store regen. Implementation waits on main’s OK of `proxy_flag` vocabulary (vs asking for a new `Composition.role` field — **not recommended**).

## D-062 (design-note-only)

See REPORT after `git diff --stat`: docs only; no behaviour change.

## Ruling #10 outcome (regolith-main 2026-10-04 19:45 ET) — implemented

- `proxy_flag = "initial_charge_only"`, option (a): the stamp rides on the typed composition `_located_printed_and_initial` builds for `point_conditions.composition`; the map is **not** dropped from the melt channels (option (b) rejected: it would turn rows into missing input).
- Trigger: an explicit, page-located `composition_role: initial_charge_only` on the series row or its parent `values` (unknown token → queue entry, never a guess).
- score: predict with the proxy composition; `_initial_charge_composition_notice` adds a `source_internally_inconsistent:` notice so the residual joins the existing `source-internally-inconsistent` flagged stratum. No refusal, no second stratum system.
- Other consumers carry the flag unchanged. The "Honour rule" column above is superseded where it says return None / refuse.

## ROR fix (review of record 2026-10-06) — scope narrowed to rows with a printed depleted K2O

- P1: the two parent `values.composition_role` blocks on Hastie Table 2 are removed. Footnote a says the System column is the initial composition, but only K1 ("Equation corresponds to [K2O] ~ 14" vs 19.5), synthetic Western ("[K2O] 18.9-17.6" vs 22.7), synthetic Eastern ("23.3-22.1 [K2O]" vs 23.6) and K2 ("6[K2O]" vs 8.7) print a run K2O that differs from the map. Each of those four original rows now carries its own row-level `composition_role` with that quote. Illite (K2O 7.4, no different run K2O; kept by `df0dea51b` as the printed analysis) is unstamped on both the superseded original and the live quoted row.
- `series_row_extra` skips `composition_role`, so a row-level declaration does not rename the row: the four point ids stay `h=e3f647c078ee` / `h=c3c57e8f6757` / `h=5990a63e7c0b` / `h=65d014284ac8`.
- Disclosed, unchanged (no code change asked): the score notice does not fire on the Hastie rows (quantity identity unknown; originals superseded) — the scored instance is Markova 1983 (25 points). Markova `identity.composition` still comes from the series' first point map for 18/25 rows, so the diagnostic prediction uses another sample's starting charge (follow-up: prefer the point's own map when filling identity). Fig. 5 KMS(3) starting glass lives on `experiment.sample.printed_composition` and the class stays opt-in (closed token, undeclared sources such as Markova 1984 Table 2 are not discovered). `normalized_composition` (waypoints) ignores the token, as ruling #10 option (a) prescribes.

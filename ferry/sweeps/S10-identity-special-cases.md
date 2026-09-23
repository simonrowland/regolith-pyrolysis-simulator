# S10 — hard-coded identity special-cases

**Repo tip (audited):** `fbe3491b2` (`origin/work-v064-green`) via worktree `/workspace/repos/wt/slot-09`. READ-ONLY on product code; writes only to ferry-inbox. Do not push. No product commits.  
**Also noted (not re-based):** `origin/review/r34-hardening` adds one pending ledger remap site (`migrate.py` `source_id == "janaf"`); prefer green for this whole-repo baseline.  
**Scope:** whole repo — code branching on a literal `source_id` / work id / filename / species name instead of a declared property (capability, `compilation_role`, reactivity class, density verdict, published phase, etc.).  
**Predicate seed:** `if source_id == "janaf"` (and peers). Doctrine peer: W1 d-039 — readiness/scoring predicates must be data-driven, not source-id substrings.  
**Mode:** static-only + corpus `rg` (store source_id census; nested vs flat origins).

| site (file:line) | predicate match | trigger input | live? | severity |
|---|---|---|---|---|
| `simulator/battery/score.py:516-524` (`is_compilation_source`) + call sites `:1400`, `:1476`, `:1572`, `:1634`, `:2140` | Treats a source as compilation via **literal prefix markers** on `source_id` (`janaf`, `usgs`, `usbm`, …) or `origin.startswith("compilations-")`. Does **not** read `compilation_role` / `scoring_eligible` / `engine_reference_input` already stamped on rows. | Nested USGS store rows (`source_id: robie-waldbaum-1968-usgs-b1259`, etc.): `load_score_context` only `glob("*.yaml")` for origins (`:1525`), so nested paths get **no** origin; markers miss (`robie-…` / `hemingway-…` / `pankratz-…` do not `startswith` `usgs`/`usbm`). **~15k** B1259 rows alone fail both gates → omitted from `diagnostic_references` while nested `nist-janaf-4th` still matches via `"janaf"`/`"nist-janaf"`. Flat `compilations-*.yaml` and any `source_id` starting `janaf`/`burcat`/… take the identity branch and flip `reference_measured_evidence` / notices. | live | **P0** |
| `simulator/condensation.py:6443-6448` (`wall` Psat unavailable) | When `refusal_reason == "saturation_pressure_unavailable"` and **`species == "SiO"`** (and backstop off), returns **`0.0`** instead of `WallSaturationPressureRefusal`. Declared property already exists: `reactivity_class_by_species` in `data/literature/vacuum_pyrolysis_sticking.yaml` (`SiO: reactive`) via `_sticking_reactivity_class`. | Call `…` with `species="SiO"`, missing/non-finite Psat, `reactive_product_backstop=False`. Peer species refuse; SiO silently zeros. | live | **P0** |
| `simulator/condensation.py:6400-6418` + `:6386-6398` | Reactive / stable condensation-product backstops authorized by **literal species** (`species != "SiO"` / `species != "CrO2"`) after (or instead of) using declared reactivity / product class. Only SiO is `reactive` today; CrO2 is `physisorbing` yet name-gated for the stable-product path. | Default `reactive_product_backstop=True` with SiO + missing Psat → full local P as driving pressure. Any other species stamped `reactive` later raises `ValueError` rather than following the class. CrO2 + `stable_condensation_product_backstop=True` → max(0, local_P); other species rejected by name. | live | **P0** |
| `simulator/battery/migrate.py:5868-5922` (`_is_nist_janaf_table`, `_is_usgs_b1544/b1452/b1259_record`) + dispatch `:8255-8267` | Chooses lift generator by **`source_id == "nist-janaf-4th"` / USGS source-id constants** or **path segment** under `compilations/<family>/`, not a declared `generator:` / schema discriminator on the doc (tables already carry `compilation_role` + `schema_version`). | Live JANAF/USGS compilation migrate: wrong/missing `source_id` with matching folder (or the reverse) routes to generator vs generic compilation lift. Corpus today is consistently labeled → wrong-route is rare but the branch is identity-keyed. | live (dispatch) / latent (mislabel) | **P1** |
| `simulator/battery/score.py:531-538` (`is_sf04_workbook`) + `:955-967`, `:1478`, `:1575` | IMCC circularity / eligibility refuses when **`observation.source_id == "sf04-magma-companion-workbook"`** (also accepts evidence `original_method_class` / `model == magma_model_companion_workbook`). Source-id arm is an identity special-case beside the declared evidence regime. | Score IMCC engines against workbook observations (live SF04 companion extract / store rows with that `source_id`). | live | **P1** |
| `simulator/battery/score.py:113-133` (`INTERNAL_CONSISTENCY_STEMS`, `COMPILATION_SOURCE_MARKERS`) | Filename stem set + source_id prefix frozensets are hard-coded identity registries driving scoring policy. | Any observation whose store file is `species_rail_differential_ledger.yaml` / `gibbs_battery_residual_ledger.yaml`, or whose `source_id` matches a marker prefix. | live | **P1** |
| `simulator/battery/migrate.py:8883-8897` (`write` grouping) | Observation-v2 destination layout branches on **literal compilation folder family** (`family == "janaf"` → per-element shards; closed USGS id set → per-record dirs; else flat `compilations-{family}.yaml`). | Remigrate of `data/literature/compilations/janaf/…` or the three USGS trees. | live | **P1** |
| `simulator/metal_stratification.py:8-67` (`target_pool`) | Pool routing by **species name sets** (`BOTTOM_POOL_SPECIES`, `Al` → always `float_layer`). Only **Si** consults `si_destination_verdict` (density). Al ignores the density property path used for Si. | Stratification transfer of Al (always float) vs Si (sink/float by verdict). | live | **P1** |
| `simulator/battery/generators/usgs_b1452.py:1384-1386` (`_oxide_species`) | **`formula == "H2O"` → `Phase.L`**, else `Phase.CR`, instead of the record’s published phase / state field. | B1452 oxide-path generation for H2O (liquid forced). | live | **P1** |
| `simulator/vapour_rail/request.py:1421-1424` | When building live evaluator inputs, **`rule.species_id == "Fe"`** pulls `source_reaction_activity_results["FeO"]` as shadow — Fe identity, not a declared oxide-parent / activity-channel property on the rule. | U0/request resolve for Fe source-reaction rules with FeO activity present. | live | **P1** |
| `simulator/equilibrium.py:1281-1297` | SiO Psat pO2 suppression runs when **`name == "SiO"`** and `pO2_exponent` is absent — hard-coded species law instead of requiring a declared exponent/reference on the coefficient row (other species already carry `pO2_exponent` in `data/vapor_pressures.yaml`). | Builtin Antoine path for SiO above `pO2_reference_bar` without a stamped exponent. | live | **P1** |
| `scripts/calibration_battery.py:219-228`, `:422` | Evidence **kind** / row filters branch on literal KEMS **`source_id`** sets (`kems-005-fedkin-2006`, `kems-008-…`, `kems-041-…`, `kems-022-demaria-1971`+`species_id=="Na"`) instead of evidence/method fields alone. | Calibration envelope build over those extracts. | live | **P1** |
| `simulator/evaporation.py:1206-1226` | Melt-derived Fe flux capped via **`species == "Fe"`** + Fe-named residual capacity attribute — identity gate for a shared transport scheduler. | Evaporation hour with `_native_fe_vapor_residual_capacity_mol_this_hr` set and Fe in `flux_kg_hr`. | live | **P2** |
| `tools/migrate_pilot_extracts.py:906-918`, `:1259`, `:1358` | Pilot rewrite: **`doc["source_id"] == "janaf-4th"`** clean overwrite; **`source_id == "fedkin-grossman-ghiorso-2006"`** adds per-T series; **`"janaf" in source_hint`** routes phase blocks to the JANAF extract. | Re-running pilot migrate tools on those drafts. | latent (tooling) | **P2** |
| `scripts/vapour_rail_sf04_high_t.py:149` | Refuses load unless **`document.get("source_id") == "sf04-magma-companion-workbook"`** — identity pin instead of schema/role. | Script invoked on a differently labeled but structurally valid workbook extract. | latent | **P2** |
| `simulator/diagnostic_helpers/species_rail_differential.py:978-982` (`ellingham_provenance`) | Marks engine-own-input when `compilation_id == COMPILATION_JANAF` **and** (`"janaf" in anchor` or `"chase" in anchor`) — substring on free-text anchor, not a stamped provenance property. | Ellingham channel differential vs JANAF compilation id. | live | **P2** |
| `simulator/vapour_rail/u0_manifest.py:633-656`, `:868-876` | Manifest flags/notes for **`O` / `P2O5_gas` / `NaF` / `TiOH4`** by literal `species_id` (alongside set membership). Inventory authoring keyed by identity. | Manifest build/refresh for those ids. | live | **P2** |
| `origin/review/r34-hardening` `simulator/battery/migrate.py:8080-8085` (not on green) | Ledger remap only when **`source_id == "janaf"`** and **`path.name == "species_rail_differential_ledger.yaml"`** — leaves `pankratz-1987-usbm-b689` ledger tokens merged into real USBM work (R8 P1). | Remigrate ledger on r34 tip. | pending-tip | **P1** (tip) |

## Counts

- Sites: **18** (17 on green + 1 pending-tip noted)
- Live: **14**
- Latent / tooling / pending-tip: **4**
- **P0: 3** · P1: 10 · P2: 5 · P3: 0  
  (P0 count on green baseline = 3; r34 pending not included in P0)

## Fix direction (summary)

- Prefer declared fields already on corpus: `compilation_role.*`, evidence `model` / `original_method_class`, `reactivity_class_by_species`, density verdict, published phase, `pO2_exponent`, explicit `generator:` / lift discriminator.
- `is_compilation_source`: walk origins with the same recursive store iterator as `load_migrated_store`; classify by role stamps (or origin path under `compilations-`), not source_id prefixes.
- Condensation backstops: authorize by reactivity / product class from sticking data, not `species == "SiO"|"CrO2"`.
- Migrate lift: one declared generator key (or schema) per compilation family; path/source_id only as fallback diagnostics.
- Metal pool: route Al through the same density verdict path as Si (or a declared `target_pool` property).

## No-hit areas (audited, out of class or safe)

- **W1 pure-substance readiness predicate** — not present on `fbe3491b2` (`is_pure_substance_engine_reference` absent); green still has no JANAF source-id readiness special-case of that form.
- **`evaluator_family` / `correlation_family` / `reaction_family` switches** — branch on declared row/backend properties (`volatile_properties`, vapour_rail catalog, stage0), not bare source_id.
- **Backend name dispatch** (`alphamelts` / `thermoengine` / `magemin` / IMCC) — declared backend identity tokens for provider selection (`backends.py`, `reduced_real_determinism.py`).
- **Closed thermo model tables keyed by species** (`thermal_train.vapor_cp_j_per_mol_k` O2/SiO/monatomic; Fe2O3×2 stoichiometry) — molecule-inherent model choice, not a substitute for a missing role/property stamp.
- **`SOURCE_ID = "…"` constants** in loaders/generators used to **stamp** records, not to branch peer behavior.
- **Filename skips** for `ALIASES.yaml` / `manifest.yaml` / README — inventory hygiene, not result identity.
- **Extract `source_id` must match stem** (`tools/validate_literature_extracts.py`) — consistency check, not behavioral special-case.
- **R8 ledger `source_id == "janaf"` remap** — absent on green; only on r34-hardening (listed above as pending-tip).

SWEEP: S10 | sites=18 | live=14 | P0=3 P1=10 P2=5 P3=0 | base=fbe3491b2 work-v064-green | path=/workspace/ferry-inbox/sweeps/S10-identity-special-cases.md

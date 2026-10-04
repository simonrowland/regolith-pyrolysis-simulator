# REVIEW — residue-r5: Sossi Mn/Ti predictions + whole-store cache/shadow fixes

- **Reviewer:** regolith-empirical (frontier of record)
- **Seat:** `/workspace/repos/wt/slot-y17`
- **Tip:** `c35db99ffa4633a9e65786912da6ac8ede36b299` on `review/residue-r5`
- **Green base:** `bac60dc99ee19d29c2855defbe2226b3ca4ada9d` (`origin/work-v064-green`)
- **Merge of product tip with green:** `856c06dce98147c37141ad6fdc2d560ed861cb62`
- **Range on green `bac60dc99`:** 9 commits + merge, then pin + digest consolidation
  - `46543834c` feat: Sossi buffered Mn and Ti residue predictions
  - `8b6a7f402` test: mixed-source residue scoring cohorts (pin BEFORE fix)
  - `19bd65f4c` fix: key residue prediction caches by `(source_id, engine)`
  - `1ecdfcbfd` test: residue vapour shadow suppression (pin)
  - `b547cddb2` test: one catalog build for residue adapter reuse (pin)
  - `3ca518746` perf: reuse registered builtin provider + VapoRock shadow; residue callers skip diagnostic shadows
  - `5675739e1` test: correct residue oxygen anchor units (comment Pa wording)
  - `856c06dce` merge green
  - `22bf459c5` test: pin residue pack digest provenance (BEFORE move)
  - `c35db99ff` refactor: one owner for residue pack digests (`_gas_pack_byte_digests`)
- **REQ:** `/workspace/ferry-inbox/REQ-residue-r5-from-regolith-physics-2026-10-03.md`
- **Bundle:** `/workspace/ferry-inbox/regolith-physics-residue-r5-2026-10-03/` (`r5-fix-report.md`, `r5-digest-report.md`, fingerprint + two mutation scripts, pre-offer receipt)
- **Date:** 2026-10-03 ~20:29–20:45 ET
- **Mode:** read-only for product code; extracts not hand-edited; no force-push; no Mac listen pools; no push of empirical product branches. Targeted VPS unit tests only (no full W3 / full-store score_store / full pytest).

## Scope

Sossi buffered-fO2 Mn and Ti residue predictions on the shared inventory integrator, plus two whole-store fixes: (1) residue prediction cache keyed by `(source_id, engine)` so Sossi cannot wipe Hashimoto residuals; (2) provider/VapoRock reuse and residue pressure-model callers skipping diagnostic shadows. Digest consolidation after green merge is behaviour-preserving.

Files (`git diff --stat` `bac60dc99..c35db99ff`):

| Path | Δ |
| --- | --- |
| `simulator/battery/residue.py` | +643 (Sossi cohort + digest helper; Hashimoto IA shadow skip) |
| `simulator/battery/score.py` | +264 (Sossi/Hashimoto scorer paths; source-aware cache) |
| `simulator/chemistry/kernel/planner.py` | +50 (`_dispatch_authoritative_without_shadows`) |
| `simulator/core.py` | +54/−17 (provider reuse; `include_diagnostic_shadows`) |
| `simulator/diagnostic_helpers/binary_pot_battery.py` | +9/−… (adapter opt-out) |
| `tests/battery/test_sossi_residue_predictions.py` | +275 |
| `tests/battery/test_internal_analytical_battery_engine.py` | +58 |
| `tests/battery/test_hashimoto_residue_predictions.py` | +10 |
| `tests/battery/test_residue_oxygen_balance.py` | +29/−… |
| `tests/battery/test_score.py` | +47/−… |

**Store effect:** none. `git diff bac60dc99 c35db99ff -- data/` is empty (0 bytes). Confirmed on seat.

## Attack results

### (1) Sossi physics: buffered-fO2 + Mn/Ti vs kems-012 table 2 — **PASS**

Seat verification against extract-v2 + residue/score paths:

- Admitted live residue: **Mn 43 + Ti 43** (plus 6×43 unsupported rare-earth/transition channels = 344 Sossi live; 120 Hashimoto; **464** total admitted residue). Spot-load `load_score_context(sources=(sossi,hashimoto))` reproduces 464 / experiments map still 3,518.
- Observations: `formula: Mn|Ti`, `subtype: element_ppm_by_mass`, Table 2 LA-ICP-MS residue ppm in quenched glass (admission locators cite §4.0 / Table 2). Values are absolute residual ppm points (e.g. Mn ~721–783), **not** remaining-fraction or oxide wt%.
- Predictions: `_sossi_project_element_ppm` → `primary_element_ppm`; scorer metric via `metric_operation_for_identity` selects **DEX** for `element_ppm_by_mass` (Hashimoto `oxide_wt_percent` stays ABSOLUTE). Like-with-like: **element ppm vs element ppm**.
- Buffered boundary: integrator takes `buffered_fO2_log` per experiment from `point_conditions.fO2_log`; provenance records `buffered_boundary: printed log10 fO2; reservoir oxygen exchange included`. Parents `Mn→MnO`, `Ti→TiO2`. Open furnace Langmuir-limit and Pt-loop bead geometry are named OUT_OF_CERTIFIED_BAND notices (`sossi-residue-r5`), not silent assumptions.
- OpenIMCC refuses Mn with `channel_missing` (no Mn gas channel) while producing Ti (~1045 ppm on seat probe); IA produces both — matches worker report, not a like-with-like mismatch.

### (2) Cache key `(source_id, engine)` — **PASS**

- Outer key is `(_HASHIMOTO_SOURCE_ID|_SOSSI_SOURCE_ID, engine)`; value is the **full cohort map** (Hashimoto: `experiment_id::alpha_arm`; Sossi: experiment short_id). Two experiments of one source do **not** collide at the outer key — they are separate map entries, each built with that run’s `buffered_fO2` / starting ppm / schedule.
- Cache is allocated fresh inside each `score_store` call (`residue_prediction_cache: dict[tuple[str, Engine], …] = {}`).
- Class hunt in `score.py`: the only other `cache_key` is `_row_metadata_cache` keyed by observation reference/parent for payload metadata — **not** a per-engine prediction cache and not the under-keyed engine-only shape that caused the Hashimoto wipe.
- Mutation `reintroduce_shared_residue_cache.py` (VPS-adapted): restoring engine-only keys → mixed-source test fails on `hashimoto_ids <= numeric_references`. **Mutation RED / detected** (exit 0). Product tree restored; HEAD still tip.

### (3) Shadow suppression in `3ca518746` — **PASS**

- Residue IA pressure-model callers pass `include_diagnostic_shadows=False` (Hashimoto ~L1243, Sossi ~L1826 in `residue.py`). Default on `core._dispatch_only` / adapter remains `True` — ordinary vapour adapter still dispatches one shadow per call (worker vapour fingerprint identical; seat mutation proves skip path is live).
- Provider reuse: `_register_vapor_pressure_pair` reuses registered `BuiltinVaporPressureProvider` / `VapoRockProvider` when already present; `register_idempotent` **raises** on conflicting registration (no silent catalog swap).
- Stale-provider lens: catalog payload is bound at provider construction; score loops do not mutate `vapor_pressure_catalog_data` mid-process. A mid-process catalog change would require a new conflicting registration (hard fail) or an unsupported in-place mutate — not introduced here. Safe for the residue hot path.
- Mutation `reintroduce_vapor_shadow_per_call.py`: forcing `dispatch = kernel.dispatch` → shadow count 1→2; adapter pin fails. **Mutation RED / detected**. Tree clean after.

### (4) Refusal reasons after merge — **PASS** (detail preserved)

Green status-token bucketing maps legacy spellings onto `ResidualStatus`; residue cells still carry **specific** reasons in `refusal_detail["reason"]`:

| Cell | `refusal_reason` (bucket) | `refusal_detail["reason"]` (specific) |
| --- | --- | --- |
| Sossi Gd (OpenIMCC) | `outside_supported_species` | `channel_missing` |
| Sossi Mn (OpenIMCC) | `outside_supported_species` | `channel_missing` |
| Hashimoto FeO (IA) | `unsupported` | `hashimoto_internal_analytical_channels_missing` (+ detail species list) |

Seat probes confirm the pre-merge specific tokens survive in the detail field; the outer enum is the green bucket, not an information drop.

### (5) NOT-FIXED lens — **PASS** (no remaining wipe under current keys)

Constructed attacks:

- **Store ordering** (Sossi before Hashimoto): outer keys include `source_id` → Hashimoto cannot occupy / be served from Sossi’s slot. Mutation proves the old engine-only shape reintroduces the wipe.
- **Engine set** (OpenIMCC-only, IA-only, both): engines are separate tuple elements in the key.
- **Within-source different conditions:** cohort map is per `experiment_id` (Hashimoto also `::alpha_arm`); conditions are inputs to each experiment’s integration, not a second outer-key dimension that would collide.

Remaining theoretical classes (not present in this range): a future third residue source incorrectly sharing `_HASHIMOTO_SOURCE_ID` / `_SOSSI_SOURCE_ID`; two physical runs sharing one `experiment_id` (data bug). Neither is constructible from store ordering alone against this tip.

### (6) Counts reproduce (spot; no whole-store on VPS) — **PASS (spot)**

| Claim | Seat check |
| --- | --- |
| `data/` unchanged | `git diff bac60dc99 c35db99ff -- data/` → **0 bytes** |
| Admitted residue 464 | spot-load → **464** (344 Sossi + 120 Hashimoto) |
| +43 OpenIMCC Ti / +86 IA Mn+Ti | code paths + Ti OpenIMCC probe produces numeric; Mn OpenIMCC refused `channel_missing`; report key-set SHA-256 cited: OpenIMCC added `b34ed3a1a2a518c66025d64bd6983b51791d48647121cfa6a85d8dd687332037`, IA added `f1e7e47e6402167ae7665c1f13e6d1bb3037d77d43eafc55a11f1d4e0b880e01` |
| Post-merge digest fingerprints identical | `r5-digest-report.md`: OpenIMCC residual/provenance SHAs and IA SHAs match before/after consolidation |
| Whole-store **653,150** | **Not re-run** on 16GB VPS (policy). Worker measured on merge `856c06dce`. ASK Mac Studio if a green full-store gate is required beyond this REVIEW OF RECORD. |

## REFACTOR-AS-YOU-GO (d-062)

1. **Second copy of added/edited logic?** **No (named leftovers).** Gas-pack byte digests now have one owner `_gas_pack_byte_digests` in `residue.py` (both Hashimoto/Sossi call sites). Remaining `read_bytes().hexdigest` sites in `engine_local_config.py` and `compilation_tier.py` hash different artifacts; move commit names them. Sossi/Hashimoto cohort helpers are intentionally separate physics paths (different BC / channels), not duplicated logic. b-690 content-binding digest replacement is out of scope (named in REQ).
2. **Rule/threshold/physics in presentation or wiring?** **No.** Buffered fO2, channel selection, ppm projection, and integrator live in `residue.py`; scorer projects by experiment and maps refusals.
3. **Forbidden import / cycle?** **No.** `tests/test_import_boundary.py` → **14 passed**. Residue lazy-imports diagnostic adapter / openimcc bridge; planner gains an internal authoritative-only path without new cycles.
4. **Behaviour-preserving move backed by pin BEFORE the move?** **Yes.** `8b6a7f402` before `19bd65f4c`; `1ecdfcbfd`+`b547cddb2` before `3ca518746`; `22bf459c5` before `c35db99ff`.
5. **Relax guard / baseline / move into red-guard module?** **No.** Shadow skip is opt-in for residue callers only; ordinary adapter default remains shadowed. No baseline/guard file edits in range.

No FIX-FIRST items.

## Targeted tests (VPS)

| Suite | Result |
| --- | --- |
| `test_sossi_residue_predictions.py` (all) | **PASS** (in focused run) |
| `test_hashimoto_residue_predictions.py` (all) | **PASS** |
| `test_buffered_residue_keeps_printed_fo2…` + two-channel HKL anchors | **PASS** |
| `test_internal_analytical_residue_adapter_skips_diagnostic_shadow` | **PASS** |
| `test_residue_composition_has_its_own_rail_and_typed_engine_refusal` | **PASS** |
| **Focused total** | **14 passed** in ~127 s |
| Import boundary | **14 passed** |
| Mutation cache (engine-only key) | **RED / detected** |
| Mutation shadow (per-call dispatch) | **RED / detected** |

**Seat environment note (not a product REVISE):** `test_hashimoto_vacuum_oxygen_balance_is_engine_specific_and_alpha_weighted` expects OpenIMCC be41a6d Pa anchors (0.828…); this VPS openimcc `0.1.0.dev0` still yields afcb5d8-era 0.799…. Commit `5675739e1` only corrected comment units (bar→Pa). Tip was validated on Mac with matching pack; do not treat as R5 physics failure.

Not run (policy/cost): full W3, whole-store `score_store` (653,150), Mac Studio full pytest, worker’s 565-test serial merge suite (cited from report only).

## Verdict

**LAND `c35db99ffa4633a9e65786912da6ac8ede36b299`**

No P1 REVISE items. Optional P2 / gate: ask Mac Studio for full-store `score_store` reproduction of 653,150 / residue key-set SHAs if a green gate beyond this REVIEW OF RECORD is required; optionally align VPS openimcc datapack with be41a6d for the oxygen-anchor pin.

— regolith-empirical

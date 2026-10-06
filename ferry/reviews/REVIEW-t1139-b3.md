# REVIEW OF RECORD: t-1139 Build B3 (condensation routing for the trace vapour species)

Reviewer: regolith-empirical (VPS seat). Requested by: regolith-physics (REQ-review-of-record-t1139-b3-from-regolith-physics-2026-10-05.md). Date: 2026-10-05, ~21:15 ET.
Branch: `origin/review/t1139-build-b3` @ **9b5481979a25f88360a2a3e9d3cedeca56ae408d**. Base: **ad2af6d23** (Build B1). Green used for the pre-existing-failure check: **ebf8541d3**.
Commits reviewed: d7a05a13b (pins), ba463b7e5 (one saturation curve), b6d6aafe5 (wall candidates), f08a2ff78 (applicability), 9b5481979 (Rb/Cs flagged).
Authority: FOLD.md item 6 + grokbot P1 list; BUILD-B-PLAN.md (B3 only); brief-b3.md; refactor-as-you-go.md (d-062 checklist).
Method: I used detached worktrees at 9b5481979, ad2af6d23 and ebf8541d3 and read every hunk (`simulator/condensation.py` +730/−13, plus 5 new test files). I ran probes on the head and base code (scripts on the VPS at `/workspace/scratch/t1139-b3-probes/`) and did hand arithmetic from printed JANAF rows. Only targeted tests were run on this 16 GB box, never the full suite (ASK below).
Tags: **VERIFIED** = command output or file:line at 9b5481979. **INFERRED** = my reasoning. **FROM MEMORY** = a literature value I did not re-fetch.

## Verdict: **REVISE at 9b5481979**
Counts: **0×P0, 6×P1, 4×P2, 6×P3.**

The core curve is right. `trace_vapour_condensation_onset` is one function. It inverts the source-rail G(T) of the channel's own compilation at the flowing partial pressure, and it reproduces JANAF/CRC-class numbers to ≤0.4 K (check 2). Existing-species routing is unchanged, and the pins were committed first (check 5). Six things stop it landing:
- (a) 18 of the 20 "no receiving condensed phase" gaps are false. The receiving phase exists in the same compilation, and the false gaps hide real coating paths: GeO, Ga2O, In2O, VO2, CsO/RbO, Li2/Cs2.
- (b) `hot_train_applicability` now has two answers. 29 of 45 carriers have live catalog rows that still say `not_applicable`.
- (c) Two live-path crash regressions: a KeyError in `route()`, and a FileNotFoundError from `cold_spot_diagnostic` in sparse seats.
- (d) The 9b5481979 admission bypass is much wider than "Rb and Cs". For typed-gap and captured-stage carriers it vents mass, with no refusal record, under the label `impurity_capture` / `unavailable`.
- (e) No trace species ever reaches wall competition or the coating ledger. The test that claims it does is a tautology.

All are fixable inside `condensation.py` without touching the majors. A focused confirm is required (correctness REVISE).

---

## The six priority checks

### (1) ONE derivation: **YES for the dewpoint; NO for applicability**
- VERIFIED: there is one definition, `trace_vapour_condensation_onset` (`simulator/condensation.py:5481`), and one landing-stage rule, `_landing_stages_for_onset` (`:5723-5753`). Every new consumer goes through `_trace_onset_for_flow` (`:5692-5720`) → the onset:
  - wall candidates, `_mixed_temperature_wall_candidate_segments` (`:4293-4301`);
  - cold spot, `cold_spot_diagnostic` (`:8444-8461`);
  - route disposition (`:3215-3235`).
  `_promote_non_debiting_carrier_status` (`:5094-5100`) reads the carrier index only.
- VERIFIED rg: no other dewpoint or "first stage below" rule in `simulator/ engines/ web/ tools/ scripts/`. The hits are the existing `wall_deposition.py:379` / `condensation.py:4646` T_surface ≤ T_cond comparisons for designated species, `antoine_dew_temperature_diagnostic` (reused), the organics `dewpoint_C`, and the thermal-train display. All of these are named in the ba463b7e5 message.
- **But applicability is not one derivation** (P1-2): the catalog gate `_assert_condensation_applicable` (`:4998-5021`) still answers from the `code_metadata.hot_train_applicability` token. For 29 carriers that token is `not_applicable`, and the onset's `applicable` is ignored in `route()`.

### (2) Physics of the onset: **correct curve, wrong premise for suboxides/dimers; comment complete except the activity premise**
- VERIFIED: the source for each species is its RECEIVING condensed phase from the same compilation as the channel's gas record.
  - `_trace_vapour_carrier_sources` (`:5798-5816`) maps carrier → `channel.selected_sources[gas]`, and `_thermo_saturation_onset` tries that compilation first (`:5840-5870`).
  - Probe `onset_table.py`: for all 45 carriers the onset `source_id` equals the channel's selected gas source. No silent fallback occurs today; the fallback path is P3-4.
- VERIFIED: the pressure is the LOCAL flowing partial pressure, `wall_species_partial_pressures_pa[species]` (`:5715-5719`). It is train-level, not per-segment (P3-5).
- VERIFIED: the worker's table reproduces exactly at 100 Pa → 1 Pa:
  - Cs 261.2→143.5 °C; Rb 280.7→160.8; Pb 955.8→705.3 (stage 3→4); PbO 1074.7→845.3; Li 723.3→523.0 (4); Ga 1333.3→1029.9 (1→3); In 1193.6→910.1 (1→3); SnO 1119.4→888.6 (1→4); B2O3 1442.5→1190.0 (1); V 2252.0→1828.7.
  - Cs2O 659.6/495.6 and Rb2O 812.7/608.4 are both stage 4.
- **Spot-check 1, Pb at 1 bar vs the JANAF 2019 K boiling point (hand arithmetic):**
  - JANAF Pb-003 (l): ΔfG = 0 kJ/mol for 600–2000 K (liquid is the reference state).
  - Pb-005 (g): ΔfG = 10.486 at 1900 K and 1.674 at 2000 K.
  - Saturation at P° = 1 bar is where ΔfG(g) − ΔfG(l) = 0. The slope is (1.674 − 10.486)/100 = −0.08812 kJ mol⁻¹ K⁻¹, so T = 2000 + 1.674/0.08812 = **2019.0 K**.
  - Code: 2019.022 K ± 0.05 (`tests/test_t1139_b3_condensation_onset.py:66-80`, passes). ✓
- **Pb at 100 Pa and 1 Pa:**
  - Units: log10(p/P°) = log Kf(g) − log Kf(l) = log Kf(g) above Tm. At 100 Pa, p/P° = 100/10⁵ = 10⁻³, so log = −3.
  - 100 Pa: Pb-005 gives −3.187 at 1200 K and −2.576 at 1300 K. Interpolating in 1/T: f = 0.187/0.611 = 0.306, 1/T = 8.333e-4 − 0.306 × 0.641e-4 = 8.137e-4, so T = **1228.9 K = 955.8 °C**.
  - 1 Pa: −5.861 at 900 K and −4.787 at 1000 K, giving **978.4 K = 705.3 °C**.
  - Code: 955.8 / 705.3 °C. This is the same compilation, so it is an internal check. As an independent check, CRC Pb gives 1 Pa at 978 K and 100 Pa at 1229 K (FROM MEMORY). ✓
- **Spot-check 2, Cs and Rb at 1 Pa (cross-compilation: the code uses NASA Glenn, my arithmetic uses JANAF):**
  - Cs: JANAF Cs-005 (g) log Kf is −5.377 at 400 K and −4.318 at 450 K. Target −5 (1 Pa = 10⁻⁵ bar), so f = 0.377/1.059 = 0.356, 1/T = 2.5e-3 − 0.356 × 0.2778e-3 = 2.4011e-3, giving T = **416.5 K = 143.3 °C**. Code (NASA): 143.5 °C (Δ 0.2 K).
  - Cs at 100 Pa: JANAF gives 533.9 K = 260.8 °C; code 261.2.
  - Rb: JANAF Rb-005 log Kf is −5.793 at 400 K and −4.671 at 450 K, giving **434.1 K = 160.9 °C**. Code 160.8 °C. At 100 Pa: JANAF 281.3, code 280.7.
  - CRC (FROM MEMORY): Cs 1 Pa ≈ 418 K, Rb 1 Pa ≈ 434 K. ✓
- **Derivation comment** (`:5488-5535`):
  - Present: premise, algebra (K = P_sat/P° = exp(−ΔG/RT), bisection inversion, no clamping), unit check (G/RT dimensionless, Pa/Pa, K→°C), and a sanity case (Pb-003 1 bar 2019.022 K).
  - Missing: the **activity premise**. The receiving phase is taken as a pure condensate at unit activity, which makes the onset a lower bound on the true onset temperature (P2-1).
  - **The premise itself is wrong for 18 gases**: "a trace vapour condenses as itself … the condensed form of that vapour formula" (`:5492-5495`). Suboxides and dimers do not condense as themselves. See check 3 and P1-1.

### (3) Typed gaps: **NOT truthful for 18 of 20; a real receiving phase is missed, hiding coating paths**
The lookup is formula-identity only: `rail.records_for(species, state)` (`:5854`, `:5861`). The gap at `:5868-5880` is therefore true only for "no condensed record of the same formula". The status name `no_receiving_condensed_phase` claims something stronger.

VERIFIED (`rail_phases.py`) that the receiving phases are in the rail. Probe `missed_receiving.py` / `missed_receiving_bracket.py` then computed each onset with the head's own `reaction_equilibrium_constant` and `_stable_receiving_phase` on the same compilation, using Σν·Cond → n·Gas(g) and p_sat = P°·K^(1/n):

| gap gas | real receiving phase (same compilation) | onset 100 Pa / 1 Pa (stage) |
|---|---|---|
| **GeO** (Ge's carrier, grokbot P0-1) | ½Ge + ½GeO2 (disproportionation, NASA) | **779 / 630 °C (stage 4/4)** |
| **Ga2O** | 4/3 Ga(L) + ⅓Ga2O3 (NASA) | **886 / 706 °C (4/4)** |
| **In2O** | 4/3 In(L) + ⅓In2O3 (NASA) | **923 / 738 °C (3/4)** |
| **VO2** (V's carrier) | ½V2O4(l/cr) (JANAF; the formula unit is V2O4) | 1799 / 1453 °C (1/1) |
| V4O10 | 2 V2O5(L) (NASA) | — / 928 °C (3) |
| CsO / RbO | ½Cs2O2(L) / ½Rb2O2(L) (NASA) | 982/768 (3/4); 964/753 (3/4) |
| LiO | ½Li2O2(cr) (JANAF) | 1487 / 1217 °C (1/1) |
| Cs2 / Li2 | 2 Cs(L) / 2 Li(l) | 424/274 (4/uncaptured); 958/717 (3/4) |
| Cu2 / Sn2 / Ge2 | 2 M(cond) | 2109/1683; 2028/1598; 1932/1528 (1/1) |
| GaO / InO | ⅓M + ⅓M2O3 | 1967/1625; 1686/1399 (1/1) |
| BO / B2O2 / B2O | B(cr) + B2O3(l) | 1605/1317; 1363/1117; 1935/1602 (1/1) |
| **BO2** | ½B2O3(l) + ¼O2: a real receiver, but it **needs local pO2** | not computed; mistyped as "none" |
| PbO2 | PbO + ½O2 (PbO2(cr) decomposes; needs pO2) | typed `pressure_outside_saturation_domain`; acceptable but should name PbO |
| B2 | 2 B(cr): no root in 350–3000 K in my bracket | gap acceptable |

- INFERRED: GeO, Ga2O and In2O are the dominant gas of their element under the reducing furnace conditions. Typing them `unavailable` gives Ge, Ga and In no wall candidates through their main carrier. That is the "silent ceramic / hidden coating" failure that FOLD 9 and our channels review §2.79 warned about ("each binding must declare its condensed products").
- SnO is the one where condensing as itself happens to agree with disproportionation: SnO(L/cr) gives 1119/889 °C and Sn + SnO2 gives 1115/901 °C.

### (4) Uncaptured Rb/Cs: **visible flag for Rb/Cs ✓; but the same bypass vents other carriers with weak or wrong labels**
- VERIFIED (`route_probe.py`): Rb keeps `remaining=0.4` (= input), `retained_in_source=None`, and `condensed=0`.
  - Authority: `mass_disposition: flagged_uncaptured_condensable`, `hot_train_applicability: uncaptured_condensable`, `wall_landing_stage_number: 5`, `authoritative_for_terminal_offgas: False`.
  - It also carries a refusal record `{status: flagged, reason: flagged_uncaptured_condensable, authoritative_for_terminal_offgas: False}` (`:5110-5147`).
  - This is a visible flag, not silent venting. The record reaches the run artifact (`simulator/accounting/run_artifact.py:515`).
- Caveat (INFERRED): no consumer outside `condensation.py` reads `authoritative_for_terminal_offgas` (rg). The terminal-offgas ledger books the mass as offgas, and the non-authority shows only in the refusal and authority maps.
- At base the same Rb was `retained_in_source_pending_authority` (refused `antoine_data_unavailable`). At head the melt is debited (INFERRED from the `_promote_non_debiting_carrier_status` contract, `:5067-5074`), and the mass is reported as flagged offgas. Rb/Cs is what the brief asks for.
- The problem is scope (P1-5). The bypass hits every no-data carrier: 16 in production (`admission_catalog.py`) and all 45 with the legacy payload. Typed gaps (GeO-type: `mass_disposition: unavailable`) and captured-stage carriers (Ga-type: `mass_disposition: impurity_capture` with `condensed=0`) carry no refusal record of their own. Today they only show the generic `upstream_vapour_carrier_authority_missing`, and only because the carrier status is `missing`.

### (5) Existing-species routing and live pins identical: **YES (pins committed first)**
- VERIFIED order: d7a05a13b is tests-only (`tests/test_t1139_b3_routing_pins.py`, 196 lines, 18:54 ET), before ba463b7e5 (18:58), b6d6aafe5 (19:06), f08a2ff78 (19:07) and 9b5481979 (19:13). `git log ad2af6d23..9b5481979 -- tests/test_t1139_b3_routing_pins.py` → only d7a05a13b, so the pin file was never edited after the pin commit.
- VERIFIED: the d7a05a13b pin file passes at **base** ad2af6d23 (6 passed) and at head (inside the 19-pass B3 run). Contents: DESIGNATED_STAGE, CONDENSATION_TEMPS_C, wall-candidate segments, cold-spot for Fe/Ca/Al/Ti, the Fe Antoine refusal at 100 Pa, and the coating proxy.
- VERIFIED (`route_probe.py`, base vs head): Fe route outputs are bit-identical (`remaining 0.09209035236048009`, `wall 0.00790938774152487`, same pass-through refusal).
- VERIFIED: adjacent condensation suites give the same failing set at base and head on the VPS:
  - 3 failed: the 2 `test_sio_step_wall_deposit` tests plus `test_coating_rate.py::test_coating_diagnostic_default_output_is_byte_identical_to_golden`, whose golden sha also fails at base (platform).
  - 21 errors: a `vapor_pressure_data` fixture error in `tests/chemistry/test_rail_debit_chokepoint.py` under `-o addopts=""`.
  - Head: 313 passed. Base subset: 89 passed.
- VERIFIED: the two `tests/chemistry/test_sio_step_wall_deposit.py` failures (`test_wall_deposit_is_rebaselined_after_corrected_hkl_mass_flux`, `test_hot_wall_sio_reactive_deposit_uses_product_psat_floor`) are also red at **green ebf8541d3** (2 failed, 47 passed) and at base. They are pre-existing, as the worker said.
- Caveat: the pins are thin. They do not pin `route()` outputs for majors or the admission gate. My Fe probe covers that gap for this review, but a route() digest pin would make the proof self-contained (folded into P1-3's fix).
- Live-row carriers (Pb, Ga, …) still refuse at admission (`admission_catalog.py`: 29 `inapplicable_by_declared_predicate`), so the claim "Pb still refuses" is VERIFIED.

### (6) d-062 checklist: answered below (Q1 YES, Q2 NO, Q3 NO, Q4 YES, Q5 YES).

---

## d-062 five reviewer questions
1. **Second copy of added or edited logic anywhere? YES.**
   - (a) `hot_train_applicability` has two sources for the same species.
     - Catalog `code_metadata` tokens: `not_applicable` on 29 live rows (`t583_status_*`, `metal_trace_Pb_family`, `oxide_trace_GeO/SnO_family`). Build A's generator also emits the constant `not_applicable` (`simulator/vapour_rail/channel_generator.py:258`).
     - Versus `TraceVapourCondensationOnset.hot_train_applicability`.
     - The f08a2ff78 message names the copy, but justifies it with "Trace carriers have no row", which is **false for 29/45** (see P1-2).
   - (b) The "declared species" predicate `designated_stage_number(s) is None and s not in CONDENSATION_TEMPS_C` is written 5× (`:4293`, `:5096-5097`, `:5705-5708`, `:8444-8446`, and inside `_declared_routing_onset` `:5584-5585`) (P2-3).
   - (c) A second 80-step bisection in the same module (`:5956` vs the Antoine inverter `:5425`) that the commit does not name (P3-2).
   - No second dewpoint or first-stage rule exists (rg, check 1). The copies the commits do name (the Antoine inverter, catalog pure-psat, `NasaCeaPolynomial.pure_psat_over_Pstd`) are justified.
2. **Rule or physics in a presentation or wiring layer? NO.** Everything is in `simulator/condensation.py` (the owner). The diff touches no web/, tools/, scripts/ or report code.
3. **New import crosses a forbidden layer or creates a cycle? NO.**
   - The new lazy imports go from `simulator.condensation` (layer 2) to `simulator.vapour_rail.{channel_generator, source_rail, nasa_cea, tabulated_gibbs, stoich}` (layer 1, `tests/import_layers.toml:36`). That is downward.
   - `rg "simulator.condensation" simulator/vapour_rail/` is empty, so there is no cycle. `pytest tests/test_import_boundary.py` → **14 passed**.
   - It is not a layer violation, but it is a new **data** dependency of the live path on `data/literature/compilations/` (P1-4).
4. **Every behaviour-preserving move backed by a pin committed first? YES.** d7a05a13b precedes every code commit and is unchanged afterwards; it passes at base and at head; the failing sets match before and after (check 5). The four code commits are behaviour additions, each in its own commit.
5. **Relaxed a guard, added a baseline entry, or moved code into a module whose guards are already red? YES.**
   - 9b5481979 narrows the NO_DATA admission refusal ("Inapplicable / no-data is category-1 missing input: refuse the debit", `:5067-5074`). Any no-data trace carrier now returns before both that refusal and the `flux_dormant` check (`:5094-5106`). The commit names it, but scopes it as "elemental Rb and Cs", while it applies to 16 production carriers (P1-5).
   - `condensation.py`'s guard tests are already red: 2× `test_sio_step_wall_deposit` at green, base and head, pre-existing.
   - No baseline or allowlist file was touched (the diff is `condensation.py` plus 5 new test files).

A "yes" to 1 and 5 is FIX-FIRST unless named and justified. 1(a) is named with a false justification, and 5 is named with an understated scope.

---

## Findings (ranked)
**P0:** none. Channels are dormant and no live golden moves. Promote P1-3/P1-4 to P0 if any production or CI path runs `route()` with an undesignated flowing species and no `data/literature/compilations/`.

**P1**
1. **False typed gaps hide receiving phases and coating paths.**
   - Evidence: `simulator/condensation.py:5492-5495` (premise "condenses as itself"), `:5854`/`:5861` (formula-identity lookup), `:5868-5880` (gap). 18/20 listed gases have a receiving phase in the same compilation (table, check 3). GeO, Ga2O and In2O land in stage 3/4 and VO2 in stage 1, but at head they get **no** wall candidate (b6d6aafe5 even asserts `{"GeO","BO2","VO2"}` stay gaps, `tests/test_t1139_b3_wall_candidates.py:72`).
   - Fix: derive the condensation reaction with Build A's balancer (FOLD 1: extend the existing balanced-reaction path, no new evaluator). Candidates in order: same formula; M_n → n M(cond); formula-unit multiples (VO2 → ½V2O4, V4O10 → 2V2O5, MO → ½M2O2); O-conserving disproportionation (MO → M + MO2, M2O → M + M2O3, B suboxides → B + B2O3). Take the highest-T onset, or the lowest-G receiver.
   - O2-coupled receivers (BO2 → B2O3, PbO2 → PbO) become a typed `receiving_phase_requires_local_pO2` naming the phase, not "none". Keep `no_receiving_condensed_phase` only when no O-conserving receiver exists (B2 here).
   - State the reaction actually used in the onset object (`receiving_phase` is currently only `native_phase`, e.g. "L").
2. **`hot_train_applicability` has two answers; FOLD 6's "flip … from that same derivation" is not done.**
   - Evidence: `admission_catalog.py` gives 29/45 carriers refused `inapplicable_by_declared_predicate` from live catalog rows with `hot_train_applicability: not_applicable` (`catalog_rows.py`: Ga, Pb, GeO, SnO, Li, B2O3, Cs2O, VO …). The onset says `applicable` for Ga, Pb, Li, SnO, B2O3, Cs2O and VO.
   - The docstring "Trace vapours have no catalog row" (`:5010-5013`) and the f08a2ff78 justification are false. `channel_generator.py:258` hard-codes `not_applicable`. At B4 enable, every one of these refuses to `retained_in_source`: the failure our channels review §2.68 called P0-2.
   - Fix: one rule in `condensation.py`. For a t1139 trace carrier the gate defers to `trace_vapour_condensation_onset(...).hot_train_applicability`. Keep dormancy by `flux_dormant` / `request_rule`, not by the applicability token. Retire the constant in the generator (or set it to `derived_from_condensation_onset`). Pin that the 29 rows still refuse while dormant.
3. **Live-path crash 1: KeyError in `route()`.**
   - Evidence: `route_probe.py`. Rb with positive flux and no partial-pressure entry → `KeyError: 'Rb'` at `simulator/condensation.py:3236`. At base the same input is a typed refusal (`antoine_data_unavailable`, `retained_in_source 0.4`).
   - Cause: 9b5481979 lets no-data trace carriers past admission. `_trace_onset_for_flow` returns None for a missing pressure (`:5710-5714`), and the code then falls through to `stage_route_by_species[species]`.
   - Fix: when the onset is None for a trace carrier, keep the base refusal (`retained_in_source_pending_authority`, reason `wall_species_partial_pressure_missing`). Extend the `missing_partial_species` guard (`:3088-3102`) to trace carriers. Add a test.
4. **Live-path crash 2 and cost: `cold_spot_diagnostic` now loads the whole source rail and the Build A generator for any undesignated flowing species.**
   - Evidence: `live_reach.py` and `sparse_seat.py`. `cold_spot_diagnostic([seg], {"Fe":1,"AlO":1}, {"Fe":10,"AlO":1})` reaches `load_source_rail()` through `trace_vapour_condensation_onset` → `_trace_vapour_carrier_sources` (`:5551`, `:5798-5816`). `route()` calls cold-spot on every tick (`:2981-2984`).
   - With `data/literature/compilations/` absent, which is the sparse-seat layout from the owner decision of 2026-10-04, head raises `FileNotFoundError: …/janaf/manifest.yaml`. Base returns OK.
   - With the data present it costs **5.4 s cold start** on first use (`timing.py`; ~174 MB traced). Build A's guarantee "nothing in production imports source_rail/channel_generator" is gone.
   - Fix: decide carrier membership from a cheap static list, `data/vapour_rail_demand_manifest.yaml`, which Build A already reads, before touching the rail. Map rail-load errors to a typed onset status `source_rail_unavailable`. Add a test with the rail loader patched to raise.
5. **The 9b5481979 admission bypass is not scoped to Rb/Cs, and it vents gap and captured-stage carriers under misleading labels.**
   - Evidence: `:5094-5101` returns before the `flux_dormant` check (`:5102-5106`) for any no-data carrier. With the production payload that is 16 carriers: B, B2, B2O, B2O2, BO, BO2, Cs, Cs2, CsO, Li2O2, PbO2, Rb, RbO, V, V4O10, VO2.
   - `_record_trace_vapour_disposition` (`:5127-5136`) puts the mass in `remaining_by_species`. It writes `mass_disposition: impurity_capture` when `condensed_mass_kg_hr == 0` (`route_probe.py` Ga; asserted in `tests/test_t1139_b3_uncaptured.py:43-47`) and `mass_disposition: unavailable` for gaps. Neither case gets a refusal record unless the carrier status happens to be `missing`.
   - Fix: bypass only when the onset is `ok`. Gaps and unavailable onsets keep the base refusal (`retained_in_source_pending_authority`). Name the captured-stage case `pending_capture_model` (not `impurity_capture`) with its own status-bearing record until P1-6 lands. Keep the `flux_dormant` check ahead of the bypass.
6. **No trace species reaches wall competition or the coating ledger; the coating test is a tautology.**
   - Evidence: `route()` does `continue` for every trace species before the wall block (`:3215-3235`; `result.wall_deposit_by_species` has no Rb/Cs/Ga, as asserted in `tests/test_t1139_b3_uncaptured.py:30`).
   - The wall-side P_sat is still Antoine-only (`:7351`, and the cold-spot supersaturation check `:8521`), so even a candidate segment would refuse with `WallSaturationPressureRefusal`.
   - `test_every_onset_species_is_a_wall_candidate_the_coating_sums` (`tests/test_t1139_b3_wall_candidates.py:43-85`) only proves that `thickness_proxy_by_segment_m` sums a hand-built dict. It is the same assertion as the pin `test_coating_thickness_proxy_sums_the_deposit_map`.
   - FOLD 6 ("keep finite capture and wall competition … every new species gets wall-deposit candidates, so the coating model sees it") is unmet. B4 would have to add a second saturation evaluation for the wall.
   - Fix: expose `trace_vapour_saturation_pressure_pa(species, T_K)` from the same rail curve that `_thermo_saturation_onset` already evaluates. Use it as the wall P_sat for trace carriers in `_try_antoine_psat_pa`'s callers, and let trace carriers enter the existing finite, supply-capped wall competition. Test from `route()` output to `FoulingTerminalSnapshot`, not a hand dict.
   - If the owner rules wall deposition into B4, then type it explicitly (`wall_deposition_pending_b4`), delete the tautological test, and record the deferral.

**P2**
1. **The activity premise is missing from the derivation.** A unit-activity pure condensate gives a lower bound on the onset temperature. Co-condensation is not modelled: Rb/Cs with K into the stage-4 alkali condensate, and Ga/In/Ge/Sn/Cu into the stage-1 Fe condensate (FOLD's siderophile scope gap). "Uncaptured" for Rb/Cs is a pure-phase statement. Add this to the docstring (`:5488-5535`) and to the `flagged_uncaptured_condensable` record.
2. **Onsets above the hot duct are labelled `applicable`, stage 1.** At 100 Pa: B 2512, V 2252, VO 2310, SnO2 2221, CuO 1692, Ge 1709, GeO2 1732, Cu 1581, Sn 1574 and Li2O 1594 °C, all above the stage-0 band (1400–1600 °C). `_landing_stages_for_onset` skips stage 0 (`:5741`) and reports stage 1. Cold-spot still flags `stage_0_to_stage_1`, but the applicability should say `condenses_upstream_of_train`.
3. The declared-species predicate is repeated 5× (d-062 Q1b). Fold it into one helper (e.g. `has_declared_routing(species)`) next to `designated_stage_number`.
4. The onset is recomputed with no memo, at ~3.8 ms/call (`timing.py`). It is called from cold-spot, `_mixed_temperature_wall_candidate_segments` (≥3 call sites per tick: `:3248`, `:3972`, `:4270`, plus `wall_deposition.py:83`) and `route`. At B4 with 45 carriers that is roughly 0.5–1 s per tick. Cache by (species, rounded p, stage-edge tuple) per tick.

**P3**
1. `del stages` precedes the docstring in `_declared_routing_onset` (`:5580`), so the docstring is a dead expression.
2. The second 80-step bisection (`:5956` ff.) duplicates the Antoine inverter's loop (`:5425-5437`). Share one monotone inverter, or name it in the commit.
3. `_stable_receiving_phase` (`:6019-6037`) compares only the first record of each state. JANAF PbO has two `cr` tables (red and yellow, 100–1400/1500 K), and the first wins rather than the lower-G one.
4. Silent source fallback: if the channel's compilation lacks a condensed phase, both gas and condensed switch to the next `SOURCE_ORDER` source (`:5843-5870`). No fallback happens today (all 45 match), but flag `source_differs_from_channel` when it does.
5. The onset uses the train-level `wall_species_partial_pressures_pa`, while wall deposition prefers `wall_species_partial_pressures_pa_by_segment` (`simulator/wall_deposition.py:295-306`). That gives two notions of "local" pressure.
6. Tests: Rb and Cs have no independent numeric anchor. Add the JANAF printed-row values above (Cs 416.5 K, Rb 434.1 K at 1 Pa), which are cross-compilation because the code uses NASA. `test_onset_inverts_the_rail_equilibrium_constant` re-implements the liquid/solid picker (`tests/test_t1139_b3_condensation_onset.py:83-115`); prefer the production picker.

---

## Tests run (VPS, `/tmp/admit-review-venv`, `-o addopts=""`, no xdist)
| Command | Result |
|---|---|
| `pytest tests/test_t1139_b3_{routing_pins,condensation_onset,wall_candidates,applicability,uncaptured}.py` @9b5481979 | **19 passed** (9.7 s) |
| d7a05a13b pin file @ad2af6d23 (base code) | **6 passed** |
| `pytest tests/chemistry/test_sio_step_wall_deposit.py tests/test_b189_condensation_admission.py tests/test_condensation_input_refusals.py tests/test_coating_rate.py tests/test_condensation_extrapolation_honesty.py tests/test_condensation_temperature_overrides.py tests/chemistry/test_rail_debit_chokepoint.py tests/test_rail_battery_wall_basis.py tests/chemistry/test_evaporation_series_resistance_flux.py` @9b5481979 | 3 failed, 313 passed, 1 xfailed, 21 errors |
| same sio + coating + chokepoint files @ad2af6d23 | 3 failed (the same 3), 89 passed, 21 errors (same fixture errors) |
| `pytest tests/chemistry/test_sio_step_wall_deposit.py` @ebf8541d3 (green) | **2 failed, 47 passed** (the same 2: pre-existing) |
| `pytest tests/test_import_boundary.py` @9b5481979 | **14 passed** |
| probes `onset_table.py`, `rail_phases.py`, `missed_receiving*.py`, `admission*.py`, `catalog_rows.py`, `route_probe.py` (head + base), `live_reach.py`, `sparse_seat.py` (head + base), `timing.py` | as quoted above |

## ASK for regolith-main (Mac Studio)
1. Full `python -m pytest tests/` at the revised B3 tip, with a golden diff against ad2af6d23. On the VPS, `test_coating_rate.py::test_coating_diagnostic_default_output_is_byte_identical_to_golden` fails at both base and head (platform sha), and `tests/chemistry/test_rail_debit_chokepoint.py` errors on a fixture under `-o addopts=""` at both. Both need the Studio.
2. One short simulation in a **sparse seat** (no `data/literature/compilations/`) at the revised tip, to confirm the P1-4 fix: no rail load and no FileNotFoundError from `route()`/`cold_spot_diagnostic`.
3. Owner or controller ruling on P1-6: wall deposition for trace carriers in B3 (preferred: one curve) or explicitly deferred to B4.

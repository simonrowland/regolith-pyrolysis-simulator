# FOCUSED CONFIRM (review of record): t-1139 Build B2, the trace-parent activity path

Reviewer: regolith-empirical (VPS seat). Requested by: regolith-physics (REQ-confirm-t1139-b2-from-regolith-physics-2026-10-05.md). Date: 2026-10-05, ~22:00 ET.
Branch: `origin/review/t1139-build-b2` @ **07dc6017c03c236287e779d62fec6b7cbe5f3213** (fetched and verified). Base: **ad2af6d236bcf576192c662cb09f9a0a496d7ade** (Build B1, an ancestor of the tip). Green used for check (d): **61ec839da3ba288c5df4a80f6d3ef142bd8ab461**.
Fix commits read hunk by hunk: 216595a0f (P2), 018a8caca (P1-2), 075dffb67 (P1-1), 83f9b8ccb (P1-3), f4395ae14 (P1-4), 07dc6017c (P1-5).
Authority: review-sol-build-b2.md, FOLD.md (items 7 and 8, the grokbot P1 list, the Build B addition), BUILD-B-PLAN.md, brief-b2.md, brief-b2-r2.md, and the d-062 checklist.

Source checked: Fegley, Lodders & Jacobson 2023, arXiv:2305.13327v1. I fetched the PDF on the VPS (sha256 `093cbfa9…66c2ba`) and ran pdftotext; line numbers below refer to that text.

Method:
- Detached worktrees at the tip, base and green.
- Probes in `ferry/reviews/t1139-b2-confirm-probes/` on the mailbox branch.
- Mutation proofs on the tip; each file was restored afterwards and the tree is clean.
- Targeted tests only. This is a 16 GB VPS, so I did not run the full suite (ASK below).

Tags:
- **VERIFIED** = command output, or file:line at 07dc6017c.
- **SOURCE** = Fegley 2023 text.
- **INFERRED** = my reasoning.

## Verdict: **REVISE 07dc6017c03c236287e779d62fec6b7cbe5f3213**

Counts: **0×P0, 3×P1, 3×P2, 6×P3.**
Sol's six findings: **4 FIXED, 2 PARTIAL, 0 NOT-FIXED.**

The ladder is executable now, the scorer guard is back, and bounds are kept off the point residual. Both are mutation-proven. The γ conversion at the pure-liquid reference is algebraically right, and the worker's numbers reproduce. Three things stop it landing:
1. The mole fraction is not transformed with γ.
2. The unity "upper bound" for Cu is contradicted by every Cu2O row.
3. An unpinned change to a shared module moves the live major-oxide activities.

## Sol's six findings

| # | Finding | Fix | Status | Evidence |
|---|---|---|---|---|
| P1-1 | alias reuses γ unconverted | 075dffb67 | **PARTIAL** | VERIFIED: `pure_liquid_reference_coefficient` (`simulator/chemistry/melt_activity.py:430-512`) converts at X = 1. InO1.5 at 1923 K → γ = 0.020000462637877837. GaO1.5 → 7.858386e-3 (1500 K) and 1.297092e-2 (1673 K). These match the hand algebra in (b). But `_accept_row_basis` (`simulator/vapour_rail/activity.py:2297-2302`) multiplies the pure-reference γ by whatever X the caller passes, and the caller always passes the single-cation X. So X is not transformed with γ (→ **new P1-A**). |
| P1-2 | ladder ends with γ = None | 018a8caca | **FIXED** (executability) | VERIFIED probe: all 14 parent spellings return a numeric γ at 1500, 1673 and 1923 K. The band rule is at `activity.py:1814-1843`: a covering band, else the unique nearest band. Unbanded rows are never chosen, and a tie falls through. Li2O and PbO take their FactSage 1800–2200 K row, extrapolated. Two caveats: the homologue rung is dead in production for Li/Ge/Ga (**P2-1**), and the unity bound is false for Cu (**P1-B**). |
| P1-3 | no trace mole fraction at admission | 83f9b8ccb | **PARTIAL** | VERIFIED: `request.py:1398-1419` now reads `single_cation_mole_fractions(...)[_coefficient_formula(id)]`. The end-to-end test `tests/test_vapour_batch_request.py:986` returns a finite activity and pressure. But that X is the **cation** fraction, so for a parent-spelled request it is not "on the selected row's basis" (brief-r2) (→ **P1-A**). |
| P1-4 | scorer basis guard relaxed | f4395ae14 | **FIXED** | VERIFIED: the `score.py:3658` condition `if isinstance(detail, Mapping):` is identical to the base condition (ad2af6d23 `score.py:3648`). Mutation proof: putting back `and "standard_state" in detail` fails `test_coefficient_detail_without_standard_state_is_a_mismatch` and `test_scorer_refuses_trace_parent_gamma_with_an_unstated_basis`; restoring it makes them pass. Side effect: no production trace coefficient can pass the guard (**P2-2**). |
| P1-5 | bounds and flags lost in scoring | 07dc6017c | **FIXED** (scorer path) | VERIFIED: `_ladder_keys`, `_melt_activity_ladder` and `_ladder_bound_operator` (`score.py:3829-3862`). `candidate_observation` turns an UpperBound into `ValueKind.BOUND "<="` (`:3881`). `compile_residual` refuses it with `activity_bound_not_a_point` (`:4696`). StatusBearing values stay points with `approximate=True`, and their provenance is kept. Mutation proof: disabling both bound branches fails `test_upper_bound_coefficient_is_not_scored_as_a_point`; restoring it passes. Residue: P3-5. |
| P2 | equality proxies classed as published | 216595a0f | **FIXED** (GeO2) | VERIFIED: the `_GAMMA_EQUALS_OTHER` regex (`activity.py:1622`, loop at `:1678`) now classes the GeO2 `g(GeO2) = g(SiO2) from FactSage` row as a proxy. Mutation proof: removing the loop fails `test_gamma_equality_to_another_oxide_is_a_proxy` and `test_production_geo2_equality_row_is_a_proxy`. Residue: MoO2 and Tl2O are still classed published (P3-4). |

The worker's after-table reproduces exactly (VERIFIED, `probe_ladder.out`):
- InO1.5 0.02000046.
- GaO1.5 7.858e-3 / 1.297e-2.
- Cs2O, Rb2O, In2O3 and InO1.5 are `standard_state_basis_unestablished` StatusBearing.
- Li2O and PbO are rung 2, extrapolated.
- Cu2O, CuO0.5 and GeO2 are UpperBound, rung 4, γ = 1.
- V2O3 = 4.524806e-13 at 1500 K (proxy).

## (a) Why GeO2 and Cu2O go straight to unity, and why their 4 rows get no band selection

VERIFIED (`probe_ladder.out`, homologue and envelope sections):

- **Neither has a stated band.** `_select_gamma_rows` (`activity.py:1777-1791`) keeps the published group when it has ≥ 2 rows:
  - Cu2O has 4 published rows.
  - GeO2 has 3 published rows; its 4th row, the FactSage equality, is now a proxy and is dropped from the group.
  - Every one of those rows has `validity_range_K = null`. That matches Table 2: "Regular solution, 1673 K point", "Altman (1978)", "g(GeO2) = 7.4, FactSage", "Sossi et al. (2019)".
  - `_select_banded_row` skips unbanded rows (`:1827-1829`) and returns None when there are none (`:1838`).
  - Li2O and PbO differ only because each has exactly one row printed with a band, "FactSage 1800−2200 K". That row is the unique nearest band (300 K away at 1500 K), so it is selected and flagged extrapolated.
  - The point temperatures in the notes (1573, 1673, 1923 K) are not parsed into bands. That is a defensible reading of "stated band", but it is a choice (P3-6).
- **Cu2O skips the homologue because it has none.** `_TRACE_HOMOLOGUE` (`activity.py:1626-1633`) has no Cu entry, and neither the brief nor FOLD names one for Cu.
- **GeO2 does try its homologue but gets nothing.** It walks to SiO2 (`:2336-2338`). SiO2 has 3 unbanded published rows (Ban-Ya, MAGMA, FactSage), so it resolves to rung 4 itself. `rung in {2,3}` fails (`:2342`), so GeO2 falls to unity. 018a8caca's message admits this.
- **The same dead end hits other homologues.**
  - Li2O→Na2O and Ga2O3→Al2O3 also resolve to rung 4 in production; Na2O has 4 unbanded rows and Al2O3 has 3.
  - Only K2O (through its pMELTS 2000–3000 K band) and Ga2O3 (a single proxy row) resolve.
  - The FOLD-8 "homologue mandatory before unity" guarantee holds for Li only because Li2O has its own banded row. The tests at `tests/test_t1139_b2_activity_ladder.py:303` and `:348` prove the mechanism with a synthetic single-row Na2O (**P2-1**).
- **The unity bound is wrong for Cu (P1-B).**
  - The four Cu2O rows give γ = 1.774 / 24.89 / 124.8 / 244.6 at 1500 K, 1.672–138.5 at 1673 K, and 1.564–72.97 at 1923 K. All are > 1.
  - SOURCE (lines 1636-1641): Fegley computes Cu with Altman (1978) as its curve and reports "good agreement between the four different activity coefficient models".
  - An UpperBound γ = 1 means a ≤ X. Every source row says a ≥ 1.56·X, so the bound is false in direction, not just loose.
  - GeO2's rows straddle 1 (0.026–7.4), so a ≤ X is not established there either.
  - Rung 4 exists for an *unmeasured* γ, which is how the flag is named (`henrian_gamma_unmeasured`). Here γ is measured four times.

## (b) The cation-count conversion: derivation, and whether x and γ transform together

**The derivation is right.**
- Premise: pure liquid M_cO_v is the same substance as c mol of MO_{v/c}. So μ°(parent) = c·μ°(single), and for the dissolved component μ(parent) = c·μ(single).
- Hence a_s = a_p^(1/c), exactly, at any composition.
- At the pure reference X_p = X_s = 1: γ_s = γ_p^(1/c), and the inverse is γ_p = γ_s^c.
- Units: everything is dimensionless.

**Numbers (hand arithmetic):**
- In2O3: log γ = −6534.2/1923 = −3.397920, so γ = 4.0002e-4 and √ = 0.0200005 ✓.
- Conversely 2·log(0.02)·1923 = −6534.24, so the printed −6534.2 *is* (γ_InO1.5)². That rounding is why the code gives 0.02000046 rather than 0.02.
- Ga2O3 at 1500 K: −6314/1500 = −4.209333, so 6.1754e-5 → 7.8584e-3 ✓.
- Ga2O3 at 1673 K: −3.774059, so 1.6824e-4 → 1.2971e-2 ✓.
- Independent check on another element: As2O3 B = −4791 = 2·log(0.03)·1573 (−4791.0). So "g(AsO1.5) = 0.03" is also stored squared.

**But x and γ do NOT transform together at the composition the request runs at (P1-A).**
- γ_s = a_s/X_s = (γ_p·X_p)^(1/c)/X_s. That is composition-dependent if γ_p is constant, and the reverse holds too. A constant γ on one basis is a *different Henrian model* on the other; the two agree only at X = 1.
- The owner's own docstring says so (`melt_activity.py:456-459`: a dilute inventory "does not keep the coefficient at 0.02"). Yet `_accept_row_basis` (`activity.py:2300`) uses `value = γ_converted × mole_fraction`.
- The request path (`request.py:1404-1419`) passes the **single-cation** fraction for **both** spellings.

Consequences (VERIFIED, `probe_ladder.out`, melt {In2O3 1e-6, SiO2 1} at 1923 K):
1. **An In2O3 request** gives γ(In2O3) = 4.0e-4 × X_cation = 8.0e-10. Fegley's basis is a = γ·X with X = N_oxide/ΣN_oxides. SOURCE: lines 269-297 (the MgO example and the ΣX = 1 constraint), 504-515 (γ = a/X, pure solid or liquid = 1), and 1300-1310 (P_Li ∝ (X_Li2O·γ_Li2O)^½, with X_Li2O ≈ 6×10⁻⁶ from Table 1 oxide mole fractions). On that basis a = 4.0e-10. **The code is a factor 2.000 off in this melt.** In a basalt-like melt the cation/molecular ratio is 1.771 for In2O3 and 0.886 for PbO (`probe_ladder.out`), so the factor is ≈1.77 for every two-cation parent (In2O3, Ga2O3, Cu2O, Li2O, Rb2O, Cs2O, B2O3, V2O3) and ≈0.89 for PbO/GeO2/SnO.
2. **The two spellings disagree with each other.** InO1.5 gives a_s = 0.02 × X_c = 4.0e-8, which implies a_p = a_s² = 1.6e-15. The In2O3 spelling gives 8.0e-10. **The ratio is 5.0×10⁵ (= 1/X_c).** Which spelling B4 binds therefore changes In/Ga/Cu partial pressures by orders of magnitude.
3. **The tests do not catch it.**
   - `test_activity_basis_converts_activity_and_mole_fraction` (`tests/test_t1139_b2_activity_ladder.py:504`) passes the same X = 1e-6 to both spellings, so the mole fraction is never transformed.
   - The end-to-end test (`tests/test_vapour_batch_request.py:986-1039`) pins the cation fraction against an In2O3-basis γ by comparing with the production resolver.

INFERRED physics note:
- On the cation basis (a_s ∝ X_s) is the usual dilute-solution behaviour, and it is how Wood & Wade's 0.02 was measured (FROM MEMORY; not re-fetched).
- Fegley's CONDOR uses molecular X with the squared γ.
- The fix needs a physics ruling to pick one model per row. Then compute X on that model's basis and derive the other spelling's activity by a_s = a_p^(1/c), so both spellings agree.

## (c) Is "basis unestablished" truthful for Cs2O/Rb2O/In2O3, or does Fegley 2023 state the basis?

**At row level it is truthful. At paper level it is not.**

- **What the paper states.**
  - Table 2 rows carry no standard state except CdO ("liquid standard state"). The extract records "not stated in Table 2 row" (VERIFIED).
  - But the methods text states the convention for all of Table 2 as used in CONDOR. SOURCE:
    - Raoultian activity, pure solid or liquid = 1, γ = a/X (lines 504-515).
    - X = moles of oxide / Σ moles of all oxides in the melt (lines 269-297; also the Cu2O/CuO case, lines 439-451).
    - The dissolved species are liquid oxides: "thermodynamic activity of liquid MgO dissolved in the oxide melt" (line 206), and "activity coefficients of the dissolved metal oxide liquids" (line 1567).
    - Figure labels: "Rb2O(liq) dissolved in BSE melt", "Cs2O(liq) dissolved", "In2O3(liq) dissolved" (lines 3798-3799, 3995).
  - So the convention (raoultian_pure_endmember), the phase (liquid) and the X basis (conventional-oxide molecular) are stated.
- **What I could not establish.** The component basis of each row relative to its *primary* source:
  - The In2O3 row is provably (γ_InO1.5)² (arithmetic above).
  - Table 2 is not uniform. As2O3 "g(AsO1.5) = 0.03" is stored squared (B = −4791), but "g(AsO1.5) = 0.56" is stored unsquared (B = −396.2 = log 0.56 × 1573). So a printed M2O3 row is not guaranteed to be on the M2O3 basis.
  - **Rb2O:** a "regular solution extrapolation of the 1673 K point of Sossi et al. (2019)" (line 1344).
  - **Cs2O:** W_Cs-Si = −232,500 J/mol gives log γ = −12,144/T (line 1378). My check: 232500/(8.314·ln10) = 12,145 ✓.
  - Neither line states whether Sossi 2019 or Bennour used Cs2O/Rb2O or CsO0.5/RbO0.5 components.
  - **Not checked:** Sossi et al. 2019, Bennour 1996/1999 and Wood & Wade 2013 primary texts. They are not in the repo, and I did not reach the Mac corpus.
- **The code's rule is broader than "unestablished".** `_standard_state_established` (`activity.py:2007-2031`) returns False whenever `standard_state_as_printed` is present, and 0 of 82 rows lack it (VERIFIED). So the published-Point branch (`:2057`) is unreachable in production. With the restored guard, **every** emitted trace coefficient is refused by the scorer, so the Build B addition (t-1144 Sossi & Fegley scorable) is still blocked (**P2-2**).
- **Related (P2-3).** For Cs2O, Fegley says the *adopted* nominal is the proxy "log g(Cs2O) = log g(Na2O) from FactSage" (lines 1398-1400). The ladder instead picks the Bennour row because "one published row wins over a proxy" (`activity.py:1783-1788`). That is a factor of ≈9.4 at 1673 K (5.51e-8 vs 5.85e-9).

## (d) The cold vapour-intent test at the 5 s battery wall

The test is `tests/battery/test_internal_analytical_battery_engine.py::test_internal_analytical_observation_uses_core_vapor_pressure_intent`. The wall is `_ENGINE_OUTER_TIMEOUT_S["internal-analytical"] = 5.0` (`simulator/diagnostic_helpers/binary_pot_battery.py:173`).

VERIFIED (`vapour_intent_timing.log`): each tree was run solo in a fresh worktree, cold the first time (no .pyc), twice, with `-o addopts=""`.

| tree | run 1 call | run 2 call | result |
|---|---|---|---|
| base ad2af6d23 | 1.19 s | 1.19 s (earlier pass: 1.27 / 1.16 s) | pass |
| tip 07dc6017c | 1.16 s | 1.25 s (earlier: 1.30 / 1.40 s) | pass |
| green 61ec839da | 1.33 s | 1.28 s (earlier: 1.30 / 1.16 s) | pass |

- **I could not reproduce red on the VPS** at any of the three trees, so I cannot confirm it is red on base on the worker's seat.
- B2's added work on that path is `trace_parent_activity_coefficient_emission`: 3.6 ms on the first call (`time_emission.out`). The module import (~1.6 s) is common to base and tip.
- Tip vs base differs by +0.0 to +0.2 s across runs, i.e. noise.
- INFERRED: the worker's 5.20 s solo is seat load against a 5 s wall-clock timeout, not B2. **Consistent with "pre-existing and unrelated", but not proven red at base.**
- ASK: the Studio runs this single test 3× at ad2af6d23 and at 07dc6017c.

## (e) d-062 checklist

1. **Second copy of added or edited logic? — YES (minor, named).**
   - rg found the 1/c power in the owner (`melt_activity.py:410`) and the pre-existing inline IMCC copy (`melt_activity_resolver.py:2655`), which 075dffb67 names as left in place.
   - Phase-token groups `activity.py:1636-1639` and `_closed_phase_token` `:1947` normalise phase beside the battery `Phase` enum (named in f4395ae14).
   - Only one Table-2 A + B/T evaluator exists (`activity.py:1731`). The single-cation projector is reused, not copied.
2. **Rule, threshold or physics computation in a presentation or wiring layer? — NO.**
   - The emission copier (`binary_pot_battery.py:950-990`) copies the ladder's verdict, rung, flag and basis and decides nothing.
   - `tools/build_fegley2023_gamma_table.py` copies `standard_state_as_printed` without interpreting it.
   - The verdict→bound mapping sits in the scorer (`score.py:3857`), which owns the comparison.
   - `request.py:1408` chooses the fraction key through the activity owner's (private) `_coefficient_formula` (P3-1).
3. **New import across a forbidden layer, or a cycle? — NO.**
   - `battery/score.py` (layer 3) adds function-local imports of `vapour_rail.activity` (layer 1) at `:3858` and `:3888`. That is downward, so allowed (P3-2 for style).
   - `vapour_rail.activity` → `chemistry.melt_activity` is the same layer, and that import already existed.
   - `tests/import_layers.toml` and `import_boundary_baseline.json` are untouched. `tests/test_import_boundary.py` passes.
4. **Every behaviour-preserving move backed by a pin committed first? — NO for one change (P1-C).**
   - The fix round has no pure moves.
   - But 075dffb67 and 83f9b8ccb add 11 trace parents to the **shared** `MELT_OXIDE_CATIONS_PER_FORMULA` (`melt_activity.py:235-247`).
   - The live majors read that table through `single_cation_mole_fractions`: `equilibrium.py:533`, which feeds the live vapour-pressure activities at `:718/808/875/998/1239`, and `evaporation.py:1633/1648`.
   - The commit text ("Majors still key that same projector") implies no change, and no pin covers it. The "live pin" test (`test_t1139_b2_activity_pins.py:505`) is a catalog-table digest, not a live golden.
5. **Relaxed a guard, added a baseline entry, or moved code into a module whose guards are already relaxed? — NO.**
   - The scorer guard is restored to the base condition (mutation-proven).
   - No baseline or allowlist changes, and no skip or xfail added.
   - The refusal→extrapolate test edits are round-1 (89cf6a245), FOLD-7 mandated, and already accepted by sol.

## Findings

### P1
- **P1-A — The mole fraction is not transformed with γ (residual of sol P1-1 and P1-3).**
  - Where: `simulator/vapour_rail/request.py:1404-1419` supplies the single-cation X for every trace spelling, and `simulator/vapour_rail/activity.py:2297-2302` multiplies the pure-reference γ by it.
  - Effect: a parent-spelled request mixes Fegley's molecular-basis γ with the cation-basis X (×2.000 in the test melt, ×1.77 in basalt). The parent and activity-basis spellings disagree by 1/X_c (5.0×10⁵ in the test melt).
  - Fix:
    - Physics rules the Henrian basis per row.
    - Compute X on that basis in the existing projector owner (add a molecular-fraction projection next to `single_cation_mole_fractions`; do not make a second table).
    - Derive the other spelling by a_s = a_p^(1/c).
    - Add a test asserting a_parent = a_single^c for one dilute melt.
- **P1-B — The rung-4 "UpperBound γ = 1" is contradicted by the selected evidence for Cu2O and CuO0.5.**
  - Where: `activity.py:2099-2141`, reached via `:2353`.
  - Effect: all four published Cu2O γ are 1.56–245 across 1500–1923 K, so the bound a ≤ X is false. GeO2's rows straddle 1.
  - Fix: when rows exist but no band rule applies, do not emit a Henrian bound against the data. Possible paths, which need a physics ruling:
    - a StatusBearingValue with the candidate envelope (min/max) and the source's stated nominal (Altman 1978 for Cu, SOURCE lines 1636-1641), flagged; or
    - a LowerBound at min γ when every candidate is > 1.
- **P1-C — An unpinned behaviour change in a shared owner moves the live major activities.**
  - Where: `simulator/chemistry/melt_activity.py:235-247`.
  - Effect: in the six trace-bearing feedstocks the majors' single-cation X shift by 1.2–4.6×10⁻⁴ relative (VERIFIED, `probe_major_shift.out`):
    - lunar_mare_low_ti 2.51e-4
    - lunar_mare_high_ti 2.83e-4
    - lunar_highland 1.18e-4
    - lunar_pkt_kreep_average 2.54e-4
    - lunar_spa_kreep_influenced 2.00e-4
    - targeted_super_kreep_ore 4.59e-4
  - These feed live vapour pressures (`equilibrium.py:533`). The brief says "Channels STAY DORMANT. Live pins must be identical."
  - Counting trace cations is arguably right physically (INFERRED). But it must be either kept off the majors' live projection until B4, or declared as a golden shift, with a pin first and a Studio golden diff in the report.

### P2
- **P2-1 — The homologue rung is non-executable in production for Li2O→Na2O, GeO2→SiO2 and Ga2O3→Al2O3.**
  - The targets have only unbanded multi-row sets, so they resolve to rung 4 (`activity.py:2336-2353`).
  - The Li guarantee rests on Li2O's own banded row, and the tests use synthetic single-row targets (`tests/test_t1139_b2_activity_ladder.py:303, 348`).
  - Needs a physics ruling: a documented selection for the major homologue targets. One candidate is Fegley's own nominal FactSage row; the brief's "lowest residual" ban still applies.
- **P2-2 — "Basis unestablished" covers all 82 rows (`activity.py:2007-2031`).**
  - No production row can be a Point, and every emitted trace coefficient fails the restored guard. So t-1144 (the FOLD "Build B addition") stays blocked.
  - Fix in the table tool and extract: carry the paper-level stated convention, phase and X basis with page cites (SOURCE lines 206, 269-297, 504-515). Keep a row-level "component basis derived" flag for converted rows (As2O3 shows Table 2 converts inconsistently).
- **P2-3 — The Cs2O selection contradicts the source's adopted nominal (factor ≈9.4 at 1673 K).**
  - Where: `activity.py:1783-1788` picks Bennour; SOURCE lines 1398-1400 adopt "set = g(Na2O) FactSage".
  - Needs a ruling: "one published wins over a proxy" vs "the author's stated nominal".

### P3
1. `request.py:52,1408` imports the private `_coefficient_formula` from `activity.py`. Make it public in the owner.
2. Function-local `vapour_rail.activity` imports in the scorer (`score.py:1211, 3858, 3888`). They are layer-legal; hoist them, or pass the verdict tokens in.
3. Ladder provenance rides on `refusal_detail` of a non-refused prediction (`score.py:3825`). This follows the existing residue precedent, but overloads a refusal field.
4. MoO2 ("Geometric mean of FeO and TiO2") and Tl2O ("Basicity modified Rb2O value") are still classed `published`. Neither is a trace parent today.
5. The emission copier still puts the rung-4 unity bound into the scalar `melt_activity_coefficients` map (`binary_pot_battery.py:990`; acknowledged). Today the only reader is `score.py:3633`, which takes the detail path; a future scalar-only reader would see 1.0 as a value. Also, `bench.py:178-183` registers internal-analytical as a raoultian/L reporter while every detail says the basis is unestablished.
6. Point-temperature rows ("1673 K point") are not treated as degenerate bands. This is a defensible choice, but it is why Cu2O and GeO2 get no band selection. State it in the docstring. The name `test_activity_basis_converts_activity_and_mole_fraction` (`:504`) overstates what it tests: X is the same for both spellings.

## Tests run (VPS, `/tmp/admit-review-venv`, `-o addopts=""`, serial)
- At the tip, **819 passed, 0 failed** in 47.6 s (`targeted_tip.log`). Files:
  - tests/test_t1139_b2_activity_ladder.py
  - tests/test_t1139_b2_activity_pins.py
  - tests/test_t1139_b2_reported_gamma.py
  - tests/chemistry/test_melt_activity.py
  - tests/battery/test_internal_analytical_battery_engine.py
  - tests/test_melt_activity_resolver.py
  - tests/test_t1139_channel_generator.py
  - tests/test_t1139_b1_reactant_vector.py
  - tests/test_import_boundary.py
  - tests/battery/test_melt_activity_requirements.py
  - tests/test_vapour_batch_request.py
- Mutations M1 (guard), M2 (bound branches) and M3 (proxy equality): each fails its tests, then passes again after restore. The tree was clean afterwards.
- The vapour-intent test passes at base, tip and green (table in d).
- Not run: the full suite or whole-store scoring.

## ASK (for main: Mac Studio)
1. Full pytest at 07dc6017c vs ad2af6d23.
2. A golden diff on the six trace-bearing feedstocks, to size P1-C.
3. The single vapour-intent test 3× at base and at tip.
4. The scorer regression on existing rows, run without the 5 s wall cutting it short (brief-r2 asked for it; not reproducible here).

**REVISE 07dc6017c03c236287e779d62fec6b7cbe5f3213**

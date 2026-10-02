# REVIEW — residue-r3: Hashimoto time-refinement (N/2N step doubling)

- **Reviewer:** regolith-empirical (frontier of record)
- **Seat:** `/workspace/repos/wt/slot-y17`
- **Tip:** `50da4a5e9c300b03b770945640076e4d6ab709f6` on `review/residue-r3`
- **Parent/base:** `b13a98ae04cc1bc8fb98ba3b5686f399ff66c325` (= merge of LANDed residue batch `d1179bbf9` and R2 `54bf0d7e4`) — confirmed ancestor (`git merge-base --is-ancestor`)
- **Range:** `b13a98ae04cc1bc8fb98ba3b5686f399ff66c325..50da4a5e9c300b03b770945640076e4d6ab709f6` (one commit)
  - `50da4a5e9c300b03b770945640076e4d6ab709f6` Add residue time refinement
- **REQ:** `/workspace/ferry-inbox/REQ-residue-r3-from-regolith-physics-2026-10-02.md`
- **Bundle:** `/workspace/ferry-inbox/regolith-physics-residue-2026-10-02/r3-report.md` + `residue-r3-mutations.py`
- **Design context:** DESIGN-residue-predictor-r2 §1.4 (time integration/refinement) + §8 row R3
- **Date:** 2026-10-02 ~18:33–19:15 ET
- **Mode:** read-only for product code; extracts not edited; no force-push; no Mac listen pools. Targeted VPS unit tests only (no full W3 / full-store / full pytest). Store-regen sibling commit (R0 identity / sossi+hashimoto extracts-v2) not blocking.

## Scope

Residue predictor chunk R3: replace R2's single fixed N with N/2N step-doubling on every geometry arm; accept when `max_i |Δw_i| < 0.05` wt%; cap N at 256 and publish the finest prediction with notice `residue_time_refinement_unconverged` (no refusal). Record steps tried, per-level diff, accepted N, status, CPU. Worker cohort: 144 integrations, 135 converged, 9 unconverged at cap (7 common-alpha sphere-constant; run-20b6 also primary + disk); 553 CPU s. Primary FeO moves down by up to ~0.65 wt% vs R2 (more evaporation from finer temporal resolution — refinement, not a model change).

Files (`git diff --stat` vs parent):

| Path | Δ |
| --- | --- |
| `simulator/battery/residue.py` | +111 / −29 |
| `tests/battery/test_hashimoto_residue_predictions.py` | +160 |

## Attack results

### (1) Convergence criterion and step-doubling vs design-r2 §1.4 — **PASS**

Design §1.4 algorithm: `N0 = min(N_cap, max(8, ceil(t_end/60)))` with `N_cap=256`; re-run with `2N`; accept when `max_i |w_i(N)−w_i(2N)| < ε` with `ε=0.05` wt%; at cap emit `residue_time_refinement_unconverged` and still publish finest.

Seat verification:

- Constants: `_HASHIMOTO_N_CAP = 256`, `_HASHIMOTO_REFINEMENT_TOLERANCE_WT_PCT = 0.05`.
- `initial_steps = min(N_cap, max(8, ceil(duration_s/60)))` — exact §1.4 formula.
- Loop: try N, compare to previous, accept fine when `max_difference < 0.05`, else `steps = min(N_cap, 2*steps)`; at `steps >= N_cap` set `unconverged_at_cap` + notice, break without refusal.
- **Non-power-of-two N (56/224/…):** expected. Duration-derived N0 is rarely a power of two (e.g. 17c5-1: 1668 s → N0=28 → seq `[28,56,112,224,256]`; 17c3-2: 1002 s → N0=17 → `[17,34,68,136,256]`; 17d2: 7740 s → N0=129 → `[129,256]`). Report values match these sequences.
- **Adjacent-level acceptance:** compares consecutive tried levels (N vs next). When the next step clamps to 256 (e.g. 224→256, ratio ≈1.14 rather than 2), the final check is slightly weaker than pure doubling but still a finer grid; unconverged cases are flagged. Publishes the fine/finest vector on accept or cap. Sound and matches §1.4 intent (predict-and-flag at cap).

### (2) common-alpha sphere-constant run-17d2 Δ=1.30 wt% at N=256 — **PASS (genuine stiff near-exhaustion, not a defect)**

Seat re-ran common-alpha sphere_constant for `hashimoto-1983-run-17d2` (7740 s, N0=129):

| Level | FeO exhaustion | time_reached_s | Al2O3 wt% | MgO wt% |
| --- | --- | ---: | ---: | ---: |
| N=129 | step 71 | 4200.0 | 38.503 | 6.526 |
| N=256 | step 139 | 4172.34 | 39.806 | 5.288 |

- FeO is already 0 at both accepted endpoints; max |Δ| = **1.30245 wt% on Al2O3** (also |ΔMgO|≈1.24, |ΔSiO2|≈1.11, |ΔCaO|≈1.04) — post-exhaustion refractory enrichment diverges because FeO hits the √eps·N0 floor ~28 s earlier on the fine grid.
- Primary (sphere_shrinking) and disk arms converge (Δ≈0.028 / 0.036); runtime-catalog arm never exhausts FeO (α_Fe=0.02) and converges with Δ≪0.05.
- Atom closure remains ~1e-19 mol. Status correctly `unconverged_at_cap` with notice; prediction published. Matches the REQ's near-exhaustion hypothesis — **not** an integrator/activity/oxygen defect.

### (3) Every substep re-evaluates activities AND oxygen root — **PASS** (mutations a/b)

Code path:

- `integrate_residue_inventory`: each area substep builds `engine_inventory` from **current** inventory, calls `pressure_at` → `_solve_vacuum_oxygen_balance` before flux/depletion.
- Hashimoto `pressure_model` cache key = full sorted oxide inventory; changed composition → new OpenIMCC `evaluate` + `evaluate_gas` at pO2=1, then × pO2^ν.

Mutations (seat re-run via patched runner on tip; `TASK_PYTHON=/workspace/repos/wt/slot-b565/.venv/bin/python`):

| Mutation | Baseline | Mutated |
| --- | --- | --- |
| a freeze activities after step 1 | GREEN | RED (exit 1) |
| b freeze pO₂ after step 1 | GREEN | RED (exit 1) |

Also: `test_hashimoto_refinement_rechecks_activities_for_changed_composition` (real OpenIMCC, multiple evolving compositions) PASS; `test_residue_finite_step_oxygen_tracks_actual_parent_depletion_and_re_solves` PASS.

### (4) Mutations c/d/e — **PASS**

| Mutation | Baseline | Mutated |
| --- | --- | --- |
| c skip 2N comparison, accept N0 | GREEN | RED (exit 1) |
| d allow N to exceed N_cap | GREEN | RED (exit 1) |
| e refuse at cap instead of flagging | GREEN | RED (exit 1) |

All five mutations GREEN→RED; product tree restored clean after runner (`git status` empty; HEAD still tip).

### (5) CPU 553 s — **PASS (acceptable; no obvious waste)**

- Report: 553.09 process CPU s / 766.79 s elapsed over 144 geometry integrations (each may try several N levels up to 256 OpenIMCC+oxygen solves).
- Cost is dominated by independent full re-integrations at each N (design §1.4 “re-run with 2N”), not redundant work within a level (activity cache hits on unchanged composition). Cap cases necessarily burn the full ladder — required by predict-and-flag.
- Acceptable for offline Hashimoto cohort / review-of-record; not claimed interactive. No waste that violates §1.4 or §8 R3.

## Targeted tests (VPS)

| Suite | Result |
| --- | --- |
| `tests/battery/test_hashimoto_residue_predictions.py` | **5 passed** (incl. activity-refresh + cap-flag nodes) |
| `tests/battery/test_residue_oxygen_balance.py` | **10 passed** |
| **Total tip-touched** | **15 passed** in ~118 s |
| Mutations a–e | **5/5 GREEN→RED** in ~136 s |

Not run (policy/cost): full W3, full-store score, Mac Studio full pytest, worker's aborted all-vector real-physics unit attempt.

## Raw-result awareness (not scored; not a REVISE)

- Primary FeO down vs R2 (up to ~0.65 wt% on several runs; larger post-exhaustion refractory shifts on long/common-α arms) is temporal refinement of the same physics, not a parameter change.
- 9 unconverged_at_cap arms (7 common-α sphere-constant near FeO exhaustion; 20b6 also primary+disk) are explicitly flagged; primary 20b6 remains `unconverged_at_cap` at Δ≈0.063. Scoring / higher N_cap / adaptive stepping are out of R3 landing scope.

## Verdict

**LAND `50da4a5e9c300b03b770945640076e4d6ab709f6`**

No P1 REVISE items. Optional P2 awareness only: (a) non-exact doubling on the final clamp-to-256 step is weaker than pure 2N but flagged when unconverged; (b) stiff near-exhaustion cases may need adaptive N or local refinement in a later chunk if scoring cares about those sensitivity arms; (c) store-regen sibling still in flight — do not block.

— regolith-empirical

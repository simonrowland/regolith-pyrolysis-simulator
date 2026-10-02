# REVIEW — redox chunk 6 (metal reaction and finite metal film)

- **Reviewer:** regolith-empirical (frontier of record)
- **Seat:** `/workspace/repos/wt/slot-b565`
- **Tip:** `1de22dd6e9a97d3470ecf7f0db824c8452e4be2b` on `review/stack2-redox-c6` (detached seat)
- **Range:** `c5674a2d248cd7f915fb272d40e242355dfc574d..1de22dd6e9a97d3470ecf7f0db824c8452e4be2b` (one commit)
  - `1de22dd6e9a97d3470ecf7f0db824c8452e4be2b` Implement finite metal oxygen exchange
- **Prior LAND:** base `c5674a2d2` (stack2 merge 5d / transport regression)
- **REQ:** `/workspace/ferry-inbox/REQ-redox-c6-from-regolith-physics-2026-10-01.md`
- **Worker report:** `/workspace/ferry-inbox/regolith-physics-redox-2026-09-30/c6-report.md`
- **Design:** r3 §6 chunk 6 + M2 finite-film derivation (confirm CONVERGED under `REVIEW-redox-design-r3-confirm-2026-09-30.md`)
- **Date:** 2026-10-01 ~20:00–20:20 ET
- **Mode:** read-only for product code; extracts not edited; no force-push of feature branches. Targeted VPS unit tests only (no full W3 / PN2 e2e).

## Scope

Chunk 6: on M2 (retained metal + FeO), replace ferric stoichiometry / Kress `N_eq` flux with metal reaction `Fe + ½O₂ ↔ FeO`, caps `n_Fe/2` (uptake) and `n_FeO/2` (release), finite surface-inventory film root, Kress diagnostic-only. New provider path in `engines/builtin/oxygen_reservoir_exchange.py`. Integrator unchanged (chunk 7).

## Mapping to design / REQ

| Claim | Tip evidence |
| --- | --- |
| Stoichiometry `Δn_Fe=+2d`, `Δn_FeO=−2d`, `Δn_Fe₂O₃=0` | Provider m2 path + shadow provisional updates; tests assert ledger + shadow to `1e−12` |
| Caps `n_FeO/2`, `n_Fe/2` | Shadow `step_upper`/`step_lower`; capacity helper returns metal/FeO halves when buffer active |
| Finite film `F(u)=α[P_g−P_b(u)]−B(u−N)`, `N=n_FeO/2`, `N_T=(n_FeO+n_Fe)/2` | `_oxygen_finite_interface_root` M2 early branch; comments pin design fixture |
| Design fixture `P_i=0.017068850876654978` Pa (not 0.01) | `test_m2_finite_metal_film_fixture_and_endpoint_complementarity` |
| Endpoint complementarity | Clamped `u∈{0,N_T}`; `P_i=P_g−J/α` (not forced to `P_b(u)`) |
| Kress diagnostic-only on M2 | Commit uses `m2_metal_reaction`; `q_kress_at_equality` post-commit only; Fe₂O₃ untouched |
| Integrator unchanged | BE amount bisection / substeps retained; chunk 7 owns replacement |

## Attack results

### (1) Stoichiometry, caps, Fe/O atom balance — **PASS**

Parametrized oxidizing (`P_g=0.02` Pa) and reducing (`P_g=0.001` Pa) exchange on the ideal M2 fixture:

- `Δn_Fe = +2d`, `Δn_FeO = −2d`, `Δn_Fe₂O₃ = 0` on both shadow and ledger
- Headspace `Δn_O₂ = d`
- Tracked Fe and O atoms across `cleaned_melt` / `metal_phase` / `overhead_gas` close to `1e−12`
- Caps: uptake `−n_Fe/2 ≤ d < 0`, release `0 < d ≤ n_FeO/2`
- Single `oxygen_reservoir_exchange` transition; `validate_conservation` holds
- Provider refuses over-inventory extents (`metal_reaction_exceeds_*`)

Independent of worker mutations: ferric mutation / gas-only `P_i=0.01` would fail the new pin tests (worker reported both rejected; seat left clean).

### (2) Film law vs design; root uniqueness; endpoint complementarity — **PASS**

- Ideal residual bracket on `[0,N_T]`: strictly decreasing with one sign change (test + independent algebra).
- Tip root at design transport coeffs pins **`P_i = 0.017068850876654978` Pa** (`rel=1e−9`). Confirm re-derive was `0.017068850876694148` Pa (~`4e−14` abs); tip correctly pins the design publication, not gas-only `0.01`.
- Interior: `P_i = P_g − J/α` coincides with `P_b(u)` at the root (`J = B(u−N) = α(P_g−P_b)`).
- Endpoint (`P_g=0.1` Pa): `interface_root_clamped`, `u=N_T`, `P_i > P_b(N_T)` (complementarity; not a bulk amount cap).
- Activity path reuses `_fe_saturation_bound_fO2_log` (design: no second activity model). Kress evaluator is not consulted on the M2 flux branch.

### (3) Unavailable activity → zero passive transfer; evaporative-flush exclusion — **PASS (correct, not hiding)**

**Unavailable activity:** when `_fe_saturation_bound_fO2_log` returns `None`, M2 root returns `limiting_regime=surface_activity_unavailable`, flux `0`, `interface_pO2_bar=P_g`. Seat probe: transfer `0`, FeO/metal/headspace unchanged, interface equals gas. Matches design E3 (`zero_commit` / activity flag) — no invented `P_b`, no silent ferric fallback.

**Evaporative-flush exclusion:** flush dispatches `OXYGEN_RESERVOIR_EXCHANGE` **without** `m2_metal_reaction`. Seat probe with metal+FeO present: buffer→`overhead_gas` only; FeO and metal inventories unchanged. Flag-gating is correct: evaporative credit is a separate buffer→gas move (design: not added to the passive solve). Auto-detecting M2 from inventory alone would mis-route flush into FeO↔Fe. Does **not** hide passive metal transfer.

### (4) Non-M2 regimes bit-identical vs base? — **PASS (behavioral)**

- Provider default fo2_buffer path retained; M2 is opt-in via `m2_metal_reaction` + inventory predicate. `DECLARED_ACCOUNTS` gains `cleaned_melt` / `metal_phase` only for the new transition.
- Non-metal finite root still uses Kress `N_eq` path (no `surface_inventory_*` keys).
- Seat M5-like probe (FeO+Fe₂O₃, no metal): ferric stoichiometry `Δn_FeO=+4d`, `Δn_Fe₂O₃=−2d` holds; no `m2_metal_reaction` / `q_kress_at_equality` on that commit path.
- Worker: 170 h lunar candidate vs base — neither entered M2; both passed (times 377.55 s vs 360.78 s). VPS did not re-run 170 h.

### (5) Cost / PN2 timeout — **not a chunk-6 defect → CHUNK-7**

Worker instrumented partial M2 hour before apply: **3.49e6** CALPHAD activity evals, **2391** interface-root calls, **30** amount-solver calls, **2435** amount-residual evals; staged PN2 native-Fe e2e hits 600 s timeout with **no committed M2 transfer**. Base PN2 13–16 s never entered M2 (`d=0`).

Seat count on the ideal film root: **~43** activity fixed-point invocations per root (`diagnostics=False`), within design ≤83 flux-eval bound. Each amount residual re-enters the film root with a **provisional** Fe/FeO inventory (necessary under BE), and each midpoint `u` needs its own `P_b(u)` (necessary for the film law). No evidence of a chunk-6 bug that re-runs an activity fixed point “without need” beyond that structure.

The cost explosion is the **unchanged BE amount integrator** multiplying necessary M2 film+activity work. Design assigns integrator replacement to **chunk 7**. PN2 timeout classified **CPU-BLOCKED-ON-CHUNK-7**, not REVISE for chunk 6.

### (6) `rg` + targeted tests — **PASS**

Changed symbols (`m2_metal_reaction`, `n_fe_metal_mol`, surface-inventory film, `_apply_oxygen_reservoir_exchange`, etc.) referenced primarily from `tests/test_redox_authority_floor.py`, headspace/O₂ suites, mass-balance redox cases, S4/MRE.

| Suite | Result |
| --- | --- |
| `tests/test_redox_authority_floor.py` | **55 passed** (~111 s) |
| `tests/test_headspace_transport.py` | **36 passed** (~104 s) |
| `tests/test_controlled_o2_flow_boundary.py` + `test_overhead_accounting.py` + `test_lab_oxygen_diagnostics.py` | **120 passed** (~10 s) |
| mass_balance redox subset (9 selected) | **9 passed** |
| `tests/test_s4_shuttle_broadening.py` + `test_mre_ladder.py` | **59 passed** (~72 s) |

Not run on VPS (by policy / cost): full W3, staged PN2 native-Fe e2e, 170 h / 225 h heroes, full mass_balance C2A staged (1800 s mark).

## Non-blocking notes

- Intra-root activity fixed-point has no warm-start across midpoints; optional later polish, not required by chunk 6 acceptance.
- Confirm’s re-derived `P_i` and design pin differ at ~`4e−14` Pa; tip correctly follows the design fixture constant.
- PN2 / multi-hour M2 CPU remains blocked until chunk 7; do not treat base’s 13–16 s PN2 as a same-workload comparison.

## Verdict

**LAND `1de22dd6e9a97d3470ecf7f0db824c8452e4be2b`**

Metal stoichiometry, caps, Fe/O closure, finite film + complementarity, unavailable-activity zero transfer, evaporative-flush exclusion, and non-M2 ferric behavior all match design r3 chunk 6. PN2 timeout is integrator cost under first M2 entry → chunk 7, not a chunk-6 REVISE.

— regolith-empirical

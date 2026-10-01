# REVIEW — headspace r12c (outlet boundary + sonic duct cap)

- **Reviewer:** regolith-empirical (frontier of record)
- **Seat:** `/workspace/repos/wt/slot-b565`
- **Branch tip under seat:** `5aebdc8bbe618f2338bc82edf8861d208d06b3b2` on `empirical/review-stack2-r12c` (tracks `origin/review/stack2-r12c`)
- **Range:** `0ba4fb42862e58cde05da3a688d493fa30f99582..5aebdc8bbe618f2338bc82edf8861d208d06b3b2` (four commits)
  - `f540a5b37` Use actual headspace pressure for bleed
  - `523c69d8b` Fix finite-headspace bleed pressure (quasi-steady)
  - `e9e853725` Fix finite headspace outlet boundary
  - `5aebdc8bb` Cap overhead duct flow at sonic limit
- **REQ:** `/workspace/ferry-inbox/REQ-headspace-r12c-from-regolith-physics-2026-10-01.md`
- **Worker report:** `/workspace/ferry-inbox/regolith-physics-headspace-2026-09-30/r12c-report.md` (+ `luna-k-diag.md`, `r12-report.md`, `grok-tail.md`)
- **Prior DIAG:** `/workspace/ferry-inbox/from-empirical/DIAG-headspace-regolith-physics-2026-09-30.md`
- **Date:** 2026-10-01 ~03:40–03:55 ET
- **Mode:** read-only for product code after mutations reverted; extracts not edited; no force-push; no empirical product-branch push. Targeted tests only (no full W3).

## Verdict

VERDICT: LAND 5aebdc8bbe618f2338bc82edf8861d208d06b3b2

## Scope

Round 12c implements the DIAG: quasi-steady `P_out` is the configured duct outlet (else 0 for vacuum/cistern), never the ETC inverted-P2 diagnostic; duct removal is `min(Poiseuille, sonic)` with required `P_ss = max(P_Pois, P_choke)`; choke flag `headspace_duct_choked` when the sonic branch sets above-command pressure.

## Mapping to DIAG / claimed results

| Claim | Tip evidence |
| --- | --- |
| `P_out` = configured / 0, not ETC P2 | `_dispatch_overhead_bleed` (`core.py` ~10686–10697): configured override or `_resolve_downstream_pressure`, else `0.0`. Does **not** call `_headspace_downstream_pressure_bar(ETC)`. |
| ETC P2 = 0 when equipment does not bind | `controlled_flow_capacity` (`overhead_bleed.py` ~127–145): `if not equipment_binds: downstream_pressure = 0.0`. |
| Sonic cap + max pressure combo | `_headspace_quasi_steady_pressure_Pa` (`core.py` ~4012–4034); `_duct_mass_flow_capacity_kg_s` = `min(Poiseuille, C_choke·P_up)`. |
| RH03 h1 ~4.09 bar choked | Live RH03[2]/[24] tests pass; formula with report S/k/C → `P_ss = 4.0876 bar`; `headspace_duct_choked`. |
| RH03 h2 back to 13 mbar | RH03[2]/[24] assert `P_total = max(P_cmd, P_ss)` to 1e-9; stale-bolus unit test drains when `S→0`. |
| C2B h3 1.5 mbar; hero h61 1 mbar | Worker + optimizer/condensation suites green; Poiseuille/sonic candidates below command. |
| Kn at actual 13 mbar = 0.0024 | RH03[24] recomputes Kn at actual pressure (test path ~697–724). |
| Mass balance ≤ 5e-12 % | RH03[24] asserts `max_mass_balance_pct <= 5.0e-12`. |

## Targeted tests (VPS, `-o addopts=`)

| Suite | Result |
| --- | --- |
| `tests/test_headspace_transport.py` + controlled_o2 + overhead_accounting + builtin_overhead_bleed + pipe_conductance_m_avg | **230 passed** (~119 s) |
| `tests/test_condensation_extrapolation_honesty.py` + `tests/test_optimizer_evalspec.py` | **246 passed** (~309 s) |
| RH03 `test_predict_flag_rh03_recipe_completes_with_public_flags[2]` + `[24]` | **2 passed** (~81 s) |
| `tests/test_scalar_boundary_bool_poison.py` | **collection skip** — `openimcc` missing on VPS (pre-existing) |

Known baseline-not-green (worker + REQ): three `test_split_path_end_state_matches_pre_flip_account_balances` cases and `test_mass_balance.py` freeze_gate_off timeout — both reproduce on `0ba4fb428`; not r12c regressions. Not re-run here (heavy / timeout).

## Mutations (temporary local edits, all reverted; tree clean)

1. **Remove `max(P_Pois, P_choke)`** in `_headspace_quasi_steady_pressure_Pa` (Poiseuille-only).  
   - **Goes red:** `test_quasi_steady_pressure_uses_poiseuille_and_sonic_branches[1.0-True]` and `[4.0-True]` (choke flag false; pressure under-predicted).
2. **Restore ETC latch:** always invert diagnostic P2 in `controlled_flow_capacity` **and** feed `ETC.downstream_pressure_bar` into `_dispatch_overhead_bleed` `p_downstream_bar`.  
   - **Goes red:** RH03[2] — hour 1 `actual_pressure_Pa ≈ 1.247e9` (~12472 bar) vs expected choked `≈ 4.78e5` Pa; `removed_mol ≪ expected_removed_mol` (bolus not drained).

Restored with `git checkout -- simulator/core.py engines/builtin/overhead_bleed.py` (clean after; only `.slot-busy` untracked during seat).

## Attack results

### 1. Outlet boundary — is `P_out` really configured / 0 everywhere?

**Debit / quasi-steady balance: yes.**

| Path | `P_out` source on tip |
| --- | --- |
| Quasi-steady (`_dispatch_overhead_bleed`, not force-drain, no explicit conductance override) | Configured `headspace.downstream_pressure_bar` or model `_downstream_pressure_override` via `_resolve_downstream_pressure`; else **0.0**. Never ETC. |
| Controlled O2 with no configured outlet | Configured is `None` → **0.0** (vacuum/cistern). ETC still built for capacity/swallow, but its P2 is forced to **0** when equipment does not bind. |
| Non-controlled / HARD_VACUUM | Same dispatch rule → **0.0**. |
| Force-drain | Preserves **raw** configured control when present (provider-guard honesty); else 0. Quasi-steady disabled on force-drain (`p_end` from diagnostic ledger pressure). |
| Configured outlet regression | `test_quasi_steady_bleed_uses_configured_outlet_pressure` (headspace + train override) **passed** — leftover pressure equals 0.2 bar outlet. |

**Residual ETC readers (not the debit balance):** `_headspace_downstream_pressure_bar(ETC)` still returns ETC P2 when passed; used by `_headspace_venting_throughput` (transport-pO2 / CO2 pump path) and `overhead_model.update(... p_downstream_bar=...)`. After the ETC fix, no-equipment P2 is 0, so the old kilobar latch cannot re-enter those readers via `P2≈P_up`. Equipment-binding P2 remains an intentional pump/backpressure diagnostic (`test_co2_duct_outlet_uses_effective_pump_downstream_pressure`).

**Can ETC diagnostic still reach the balance?** Not the quasi-steady debit path. Mutation 2 proves that re-wiring ETC into dispatch + restoring always-invert P2 recreates kilobar RH03. Tip blocks both legs.

### 2. Sonic cap — derivation, gamma, max combo, tests

**Derivation / units.** Isentropic sonic throat:
`ṁ* = A P_up √(γ M/(R T)) (2/(γ+1))^((γ+1)/(2(γ−1)))` [kg/s];
`C_choke = ṁ*/P_up` [kg/(s·Pa)] (`overhead.py` `_choked_flow_coefficient_kg_s_Pa`). Forward capacity `min(k(P²−P_out²), C_choke·P)`. Inverse: candidates `√(P_out²+S/k)` and `S/C_choke`; required `P_ss = max(...)` because the duct carries the **min** of the two mass-flow laws, so pressure must satisfy **both** inequalities. Algebra and units check out; crossover `S* = C²/k` tested at fractions 0.25 / 1.0 / 4.0.

**γ = 1.4.** Registry has no heat-capacity ratio; fixed 1.4 is documented. For a mostly SiO/O2/metal-vapour mix at 2200 °C, high-T diatomic γ~1.3 and polyatomic ~1.2 are more realistic than cold diatomic 1.4; monatomic metal vapour pulls toward 5/3. Relative to γ=1.4 at RH03-like M:
- γ=1.3 → `C_choke` **−2.6%** → `P_choke` **+2.6%**
- γ=1.2 → `C_choke` **−5.3%** → `P_choke` **+5.6%**
- γ=1.67 → opposite direction (~−6% on `P_choke`)

So fixed 1.4 is slightly **optimistic** (under-predicts choked pressure) for a polyatomic/hot mix — O(few %) on RH03’s ~4 bar, not an order-of-magnitude issue. Direction: true lower γ → higher `P_ss` on the choked branch. Non-blocking model fidelity note, not a LAND blocker.

**Rewritten Poiseuille-only tests.** Not a silent weakening of the dual-capacity law:
- Fe/Na capacity ratio, vapor-pressure closed form, and inverse recovery now assert the dual law.
- `test_vapor_pressure_scales_with_square_root_of_flux` lowered fluxes `3.6/7.2 → 0.0036/0.0072` kg/h so both points stay on the Poiseuille branch below crossover (`S*≈5.3` kg/h on that geometry). **Correct scoping** of a √S claim that is false on the sonic branch.

### 3. Quasi-steady validity — τ ≪ tick; flag?

Comment at `core.py` ~4008–4011 states validity when `τ = n(P_end)/Q(P_end) ≪` one-hour tick. RH03 instrumentation records `residence_time_s`; RH03[24] only asserts finiteness when `S>0`, not `τ≪tick`.

| Case | τ (estimate) | vs 3600 s |
| --- | ---: | --- |
| RH03 h1 (choked, Q≈S/M) | ~0.52 s | holds (`τ/tick ~1.4e-4`) |
| C2B h3 at command | ~0.56 s | holds |
| Hero h61 at command, tiny S | ~5.5 h | **does not** ≪ tick |

When command holds and S is tiny, source-balance is inactive (`P_end = P_cmd`); the τ≫tick remark is about inventory turnover at the command floor, not a false choked `P_ss`. **No runtime flag** is raised when τ is not ≪ tick. Non-blocking hygiene (P2-class): a `headspace_quasi_steady_invalid` (or similar) would make the hero/low-S case honest. Not a P1 against the DIAG fix.

### 4. Ledger — single credit; mass balance; stale bolus

- `test_large_o2_headspace_bolus_pumps_down_and_is_credited_once`: removed O2 appears once in offgas account; all new transitions `validate_conservation` — **passed**.
- RH03[24]: `max_mass_balance_pct <= 5e-12` — **passed**.
- `test_stale_headspace_bolus_drains_to_command_when_source_stops`: with S>0 holds `P_ss`; after `S→0` returns to command — **passed**. Matches claimed RH03 h2 stale-bolus drain.

### 5. Hero Ti retention +1.3e-3 with unchanged pressure trace

Worker: tip vs `0ba4fb428` — `P_total` max 0.01 bar at h1 and h61 summary 0.001 bar on both; first hourly difference h35 `O2_source_side_potential_kg_cumulative` (tip 0 vs baseline `3.14e-12`); Ca −1.9e-5, Al −1.3e-5, Ti **+1.3e-3**.

**Mechanism (code-level):** At hero early-campaign `P≈0.01 bar` with d=0.12 m, **sonic binds** (`sonic/Poiseuille ≈ 0.10` at tip helpers). At h61 `P≈0.001 bar`, Poiseuille binds again. So the dual-capacity law changes early-hour pipe capacity / transport saturation / same-tick evaporation coupling even while quasi-steady still pins published `P_total` to command (S small ⇒ `P_ss < P_cmd`). That perturbs micro-scale O₂ source-side potential (first cumulative fingerprint at h35) and redox/evaporation paths enough to move Ti retention by ~1.3e-3 over 225 h. Ca/Al moves are ~50× smaller.

**Verdict on defect vs consequence:** **Real consequence** of the sonic capacity law at ~10 mbar operating points, not a double-credit or P_out latch defect. Pressure-trace invariance is expected when command dominates `P_ss`. Non-blocking: hero τ at command ≫ tick with no flag (attack 3).

### 6. Symbol `rg` + referencing tests; known base failures

Changed-symbol hits under `tests/`:  
`test_headspace_transport.py`, `test_controlled_o2_flow_boundary.py`, `test_overhead_accounting.py`, `chemistry/test_builtin_overhead_bleed_provider.py`, `test_pipe_conductance_m_avg.py`, `test_condensation_extrapolation_honesty.py`, `test_optimizer_evalspec.py`, `test_scalar_boundary_bool_poison.py` (openimcc skip).

All runnable referencing files executed above (**476 passed** across named+extra suites, plus RH03 selectors). Known failures on base `0ba4fb428` (split-path wall-deposit ×3; freeze_gate_off timeout) accepted as pre-existing / not green-classified — not r12c regressions.

## Non-blocking notes

- No `τ ≪ tick` runtime flag (attack 3).
- γ=1.4 slightly under-predicts choked `P_ss` for hot polyatomic mixes (~few %).
- `_headspace_downstream_pressure_bar(ETC)` shortcut remains for venting/reporting; safe after ETC P2=0 when unbound, but could be narrowed later so only equipment-binding callers pass ETC.
- Condensable inclusion in `S` and lab `L=ΣA/(πd)` unchanged (DIAG follow-ons).
- Split-path + freeze_gate_off remain STUDIO/base debt.

## Verdict

VERDICT: LAND 5aebdc8bbe618f2338bc82edf8861d208d06b3b2

— regolith-empirical

# REVIEW — redox chunk 4 (mol predicates and mole-log inverse)

- **Reviewer:** regolith-empirical (frontier of record)
- **Seat:** `/workspace/repos/wt/slot-b565`
- **Branch tip under seat:** `6ce76a9693b85924633af517475b75277beb6556` on `empirical/review-stack2-redox-c4-c3b` (tracks `review/stack2-redox`)
- **Range:** `d43b027d1..04f9f7f93` (two commits)
  - `e00cb9c01` Apply mole-based redox predicates
  - `04f9f7f93` Align B-598 checks with redox domains
- **REQ:** `/workspace/ferry-inbox/REQ-redox-c4-c3b-from-regolith-physics-2026-10-01.md`; design r3 §6 chunk 4 “Mol predicates and mole-log inverse” (~L858); G1 (~L782); G3 (~L786)
- **Worker report:** `/workspace/ferry-inbox/regolith-physics-redox-2026-09-30/c4-report.md`
- **Prior LAND:** chunk 3 on tip `d43b027d106b96dc9c68f16072c8ba38f104e3ab`
- **Date:** 2026-10-01 ~00:13–00:28 ET
- **Mode:** read-only for product code after mutations reverted; extracts not edited; no force-push. Targeted tests only (no full W3).

## Scope

Chunk 4: regime membership by moles (`OXYGEN_RESERVOIR_NOOP_MOL = 1e-15`); M5 inverts `ln n_Fe2O3 − ln n_FeO`; fraction inverse refuses `q` outside `(0,1)`; raw diagnostic ratios replace epsilon clamps as selectors; delete the endpoint gas-copy block (`no_melt_redox_buffer` with `fO2 = log10 P_transport`). M3/M4 storage unchanged (chunk 5).

## Mapping to design / REQ

| Claim | Tip evidence |
| --- | --- |
| M0–M5 by moles, NOOP cutoff | `core.py` ~7110–7207: `fe2o3 ≤ NOOP ∧ FeO > NOOP ∧ ¬metal` → M4; `FeO ≤ NOOP ∧ fe2o3 > NOOP` → M3; else both oxides > NOOP → M5 mole-log. M2 precedes via `native_fe_coexists` (~7017–7104). |
| M5 mole-log inverse | `fe_redox.py` `_kress91_ferric_ln_ratio_from_moles` / `_kress91_log_fO2_from_fe_oxide_moles`; evaluator `.log_fO2_from_fe_oxide_moles`; call sites in ledger inverse (~7215) and exchange target (~10084). |
| Fraction inverse refuses non-interior q | `fe_redox.py` ~784–788: `if not 0 < q < 1: raise Kress91InvalidControls` (clamp removed). |
| Raw ratios / no ε membership | Regime gates use moles only. `endpoint_clamped` (~6986–6988) is telemetry provenance, not a branch that selects `no_melt` / gas. |
| Gas-copy block deleted | Base `d43b` had `if endpoint_clamped: … basis=no_melt_redox_buffer; fO2=log10(P_transport)` (~7458–7480). Tip: **no** `if endpoint_clamped:` gate in `_melt_fO2_from_ledger`. |
| M3/M4 storage unchanged | M4 still returns bound scalar with `out_of_domain`; M3 still returns `None` + `fO2_log_lower_bound` (chunk 5 territory). |

## Acceptance (design L858)

| Check | Observed |
| --- | --- |
| `5e-7` FeO / `0.5` Fe₂O₃ is M5 mole-log | `test_m5_near_ferric_inventory_uses_the_mole_log_inverse` **passed** |
| `1e-14` / `1000` finite equality (binary64 q=1) | `test_m5_finite_equality_survives_binary64_ferric_fraction_one` **passed** |
| FeO ≤ NOOP with ferric present stays M3 | `test_ferrous_inventory_at_or_below_noop_stays_m3` **passed** |
| Near-ferric cases keep M5 equality (not gas / M3) | near-ferric zero / below-capacity / exhausted / interface tests **passed** |
| No-ferric release / trace-FeO uptake keep M4 bound | rewritten authority-floor tests **passed** (not gas-owned) |
| Fraction inverse refuses q∉(0,1) | `test_fraction_inverse_refuses_values_outside_the_open_interval` **passed** |

## Assertion rewrites — design relation or weakening?

| Rewrite | Verdict |
| --- | --- |
| Near-ferric zero/partial/exhausted → M5 mole-log equality (was M3 bound / gas) | **Design.** Above-NOOP minority FeO is M5 (G1); not a weakening. |
| Fully ferric exhausted → M3 bound, absent scalar (was gas-owned `no_melt`) | **Design.** G3 / M3; deleting gas-copy. |
| No-ferric release / trace-FeO uptake → `fe_saturation_bound` (was gas) | **Design.** M4 ledger regime is independent of transfer size. |
| Trace directional inventory → mole-log inverse (not synthetic q) | **Design.** |
| B-598 → domain bases + raw q; skip composition reconstruct when oxides absent | **Design relation**, see B-598 attack below. |

## Targeted tests (VPS, `-o addopts=`)

| Suite | Result |
| --- | --- |
| `tests/test_redox_authority_floor.py` + controlled O2 + overhead | **128 passed** (~81 s) |
| `tests/test_headspace_transport.py` | **25 passed** (~55 s) |
| `tests/chemistry/test_evaporation_freeze_gate.py` (-k interface/redox) | **16 passed** |
| `tests/test_config_flags.py` | **passed** (with targeted mass_balance set) |
| Final re-verify authority+headspace on clean tree | **72 passed** |
| `tests/test_scalar_boundary_bool_poison.py` | **collection skip** — `openimcc` missing on VPS (pre-existing env) |
| B-598 170 h | **not re-run** on VPS (~259 s worker wall); code-reviewed + worker audit accepted (see below) |

Known STUDIO-REGEN (non-blocking): fixed-pO2 SiO chain magnitude; RH03[24] active hours — same notice as chunk 3.

## Mutations (temporary local edits, all reverted; tree clean)

1. **Restore epsilon clamp** in `_kress91_ferric_ln_ratio`.  
   - **Goes red:** `test_fraction_inverse_refuses_values_outside_the_open_interval` (0,1,−0.1,1.1) — DID NOT RAISE.
2. **Binary64 q inversion** — M5 path uses `kress91_log_fO2_from_fe3_over_sigma_fe(q)` instead of mole-log.  
   - **Goes red:** `test_m5_finite_equality_survives_binary64_ferric_fraction_one` — `Kress91InvalidControls` at q=1.0.
3. **Restore near-ferric ε membership gate** before M5.  
   - **Goes red:** near-ferric / M5 mole-log fixtures — basis `ferrous_free_lower_bound` instead of `kress91_inverse`.

Restored with `git checkout -- simulator/core.py simulator/fe_redox.py` (clean after; only `.slot-busy` untracked during seat).

## Attack results

### Regime membership: zero or two regimes?

Mole predicates with NOOP boundaries partition M1/M3/M4/M5 for metal-free ledgers. Metal + both oxides matches M2∧M5 predicates; **precedence** returns M2 first (`native_fe_coexists`) — design table order, not a dual classifier write. No ledger observed matching zero regimes on the tip path.

### Remaining `KRESS91_FERRIC_FRACTION_EPSILON` as selector?

| Use | Role |
| --- | --- |
| `endpoint_clamped` telemetry (~6986) | Provenance flag only; does **not** branch to gas-copy (block deleted). |
| `endpoint_epsilon=` fields on domain records | Telemetry. |
| R-e `target_ferric_fraction` gate (~9431–9435) | **Chunk 10** (R-e uses moles); out of chunk-4 scope. Not a melt-regime selector. |

No remaining ε membership gate that assigns M3/M4/`no_melt` the way G1 forbade.

### Deleted gas-copy former cases → mole rows?

| Former gas-copy case | Tip row |
| --- | --- |
| Ferric absent, FeO present, transfer any | M4 `fe_saturation_bound` |
| Near-ferric (q ≥ 1−ε) with FeO > NOOP | M5 mole-log |
| Fully ferric (FeO ≤ NOOP) | M3 lower bound, absent equality |
| Endpoint-clamped zero transfer | No longer hands melt to gas |

### B-598 skip — hides defect?

`04f9f7f93` skips composition-based ratio reconstruction when snapshot `FeO` or `Fe2O3` fraction is ≤ 0. **Still asserts** `implied_q ≈ ledger_q` before that continue. Intermediate “ledger q 0.914 vs composition q 0.0” was the stale composition reconstruct against an oxide-omitting snapshot — not a ledger/implied disagreement. Worker audit on tip: no snapshot lacked both oxides with an interior ledger ratio that disagreed with projected inventory. **Skip does not hide a defect**; it stops false fails when composition omits ledger-owned oxides. Snapshot composition fidelity remains a hygiene note, not a chunk-4 REVISE.

## Non-blocking notes

- `endpoint_clamped` / epsilon provenance strings linger after G1 — cosmetic until a later cleanup chunk.
- R-e ε gate at ~9431 is chunk 10.
- SiO magnitude pin and RH03[24] remain STUDIO-REGEN.
- Full B-598 170 h not re-executed on VPS; rely on worker pass + code audit of the skip.

## Verdict

VERDICT: LAND 04f9f7f9376215ccd676376bcfeb6629ee0252f6

— regolith-empirical

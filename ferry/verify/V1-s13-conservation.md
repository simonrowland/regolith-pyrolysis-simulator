# V1 — verify S13 conservation / accounting leaks

**Verifier:** independent of S13 sweep author.  
**Base:** `origin/review/r6-r8-fix` @ `1f8df6cbe4cfbca526192b92e8adb2c1e1780dfc` (worktree `/workspace/repos/wt/slot-04`).  
**Sweep tip (audited by S13):** `fbe3491b2` (`origin/work-v064-green`) — ancestor of this base; cited sites still present.  
**Calibration:** P0 only if a wrong number reaches a **result / score / ledger mass TODAY**. V1 note: a leak that changes **ledger mass** is a real P0. Null hypothesis = false positive.  
**Method:** static read of cited sites + reproduction with real `_apply_oxygen_reservoir_exchange` runs (`tests/test_sso_r_r20_state.py` helpers; pytest `-n 0` for locking tests). No product commits / no push.

## Claimed P0 — managed headspace→melt (HIGHEST PRIORITY)

| check | evidence |
|---|---|
| Exists as described? | **YES** — `simulator/core.py:6155-6225`: `dn_to_headspace` drives `melt_intrinsic_fO2_log` / `exchange_o2_mol`; ledger commit uses only `dn_ledger_to_headspace`. With empty `process.overhead_gas` O₂, direction is `managed_headspace_to_melt` and `|dn_to_headspace| > 0` while ledger ΔO₂ = 0. |
| Live TODAY? | **State path yes** — hour loop calls `_apply_oxygen_reservoir_exchange()` (`core.py:13025`). `CONTROLLED_O2` appears in `data/setpoints.yaml` campaigns. Locking test `test_managed_o2_floor_relaxes_reducing_melt_without_real_o2_inventory` **passes** on this tip. |
| Ledger mass invent/destroy? | **NO** — reproduction: `sum(total_kg_by_account)` δ = **0**; element O atoms δ = **0**; melt species unchanged. Across a grid of fO₂∈[-15,-6], pO₂∈{0.1,1.5,15} mbar: **all** `managed_headspace_to_melt` cases had `dkg=0`. Fe respeciation refuses (`managed_floor_unbacked`); no Fe₂O₃ minted. |
| Result / score impact? | fO₂ advances (max |Δlog₁₀ fO₂| ≈ **0.023 / hr** in grid; typical ~2.5e-4 at fO₂=-10, pO₂=1.5). Freeze-gate fO₂ quantum is **1.0** (`_FREEZE_GATE_FO2_LOG_QUANTUM`) → one-hour managed advance does **not** flip gate bins. Continuous vapour can see a tiny fO₂ shift; **atom-ledger / mass-balance gate numbers do not move**. |
| **Your severity** | **P1 — not P0.** Real unbacked thermodynamic state update (oxidizing potential without ledger O), but **not** a conservation mass leak and not a wrong ledger/score mass today. Matches calibration: claimed P0 does not hold. |

**Smallest fix:** Advance `melt_intrinsic_fO2_log` only with ledger-committed `dn_ledger_to_headspace` (hold fO₂ when managed floor is unbacked), **or** credit a typed external O source into `mass_in` before moving fO₂. Keep `managed_floor_unbacked` Fe refusal.

**Reproduction (2026-09-22 ET):**
```
pytest tests/test_sso_r_r20_state.py::test_managed_o2_floor_relaxes_reducing_melt_without_real_o2_inventory \
  tests/test_sso_r_r20_state.py::test_fe_redox_respeciation_refuses_managed_floor_without_phantom_o2 -n 0
→ 2 passed
managed_headspace_to_melt: exchange_o2_mol≈-8.2e-4, ΔfO₂≈+2.5e-4, Δledger_kg=0, Fe refused
```

## Verdict table (all S13 finding sites)

| finding (S13 site) | exists? | live TODAY? | your sev | smallest fix | shared-root |
|---|---|---|---|---|---|
| **Managed floor fO₂ without ledger O** `core.py:6155-6225` (claimed **P0**) | yes | yes (state / CONTROLLED_O2 hour path); **ledger mass no** | **P1** (downgrade) | Drive fO₂ only from `dn_ledger_to_headspace`; or book typed external O before advance | **R1** dn vs dn_ledger split |
| **Clamp partial unbacked fO₂** `core.py:6157-6163` (claimed P1) | yes | yes — constructed: tiny real O₂ (1e-6) + large floor → `headspace_to_melt` + `exchange_clamped`, `unbacked_mol≈8.2e-3`, `ΔfO₂≈6e-3`, **Δkg=0** | **P1** | Same: fO₂ from ledger Δn only; surface unbacked remainder as refusal/notice | **R1** |
| `origin_unattributed_shortfall` pads unresolved on full withdrawal `ledger.py:952-991` (P1) | yes | yes (origin layer on apply); species transition conservation can still pass | **P1** | Refuse / require explicit external-origin auth instead of minting unresolved to close attribution | **R2** origin invent |
| `_reconcile_origin_projection` floors + invents unresolved `ledger.py:2133-2166` (P1) | yes | yes when species vs origin drift | **P1** | Reconcile only within atom tolerance; refuse/quarantine large \|physical−tracked\|; do not floor signed physical away | **R2** |
| `MassBalance.check` omits wall/sulfate/inert; hardcodes stage0 Δ=0 `mass_balance.py:74-105` (P2) | yes | **latent** — `MassBalance` referenced only from tests / helper, **not** hour `_flow_mass_out_kg` gate | **P2** | Align with `FLOW_MASS_ACCOUNTS` or delete helper; propagate real stage0 Δ | — |
| Double-book volatiles in helper `mass_balance.py:65-68,118-124` (P2) | yes | **latent** — `volatiles_collected_kg` only default + helper/tests (no production writer under `simulator/` except `state.py` field) | **P2** | Keep volatiles disjoint from stage totals; invariant test | — |
| Stage-0 product rollup omits `cation_sulfate_feed` `core.py:11154-11164` (P2) | yes | latent for helper / staging until reacted (excluded staging fails flow gate loudly) | **P2** | Include residual sulfate/inert in non-ledger checks; fail closed if leftovers remain | — |
| `allocate_pool_withdrawal` silent clip in tolerance `lots.py:61-66` (P2) | yes | yes (float band) | **P2** | Clip only below documented eps + typed dust note; else refuse | — |
| `project_account_kg` display clamp of negative dust `ledger.py:1297-1300` (P3) | yes | display-only; canonical balances unchanged | **P3** | Optional `dust_clamped_kg` metadata | — |
| Condensation η clip `condensation.py:53` (P3) | yes | yes (supply-limited capture) | **P3** | Keep cap; emit `capture_supply_limited` notice | — |
| Wall segment scale-to-supply `wall_deposition.py:86-133` (no-hit) | yes (safe redistribute) | no invent | — / no-hit | Keep | — |
| Stage-0 balance residue refuses plugs `core.py:11008-11039` (no-hit) | yes (fail-closed) | N/A | — / no-hit | Keep | — |
| `validate_conservation` hard refuse (no-hit) | yes | enforced | — / no-hit | Keep | — |

## Shared roots

1. **R1 — oxygen exchange dn / dn_ledger split** (`_apply_oxygen_reservoir_exchange`): managed floor + clamp partial unbacked. One fix (fO₂ ← ledger Δn only, or typed external O credit) closes both.  
2. **R2 — origin-layer invent-to-close** (`origin_unattributed_shortfall` + `_reconcile_origin_projection`): refuse or quarantine instead of minting unresolved. Species mass gate separate.

## Counts (verifier)

| class | claimed (S13) | verifier |
|---|---|---|
| **P0 held** | 1 | **0** |
| **P0 → false-positive as P0** (real defect, downgraded) | — | **1** → P1 |
| **P1 confirmed** | 3 | **3** (+1 downgraded = **4** P1 live issues) |
| **P2 confirmed** (incl. latent) | 4 | **4** |
| **P3 confirmed** | 2 | **2** |
| **no-hit confirmed** | 2 | **2** |
| Ledger-mass conservation P0 | — | **none found** |

## TL;DR

- **CONFIRMED P0: 0.** Claimed managed-floor P0 is a **FALSE-POSITIVE as P0** — path exists and is live for melt fO₂ state, but **ledger mass/atoms do not change**; Fe path fails closed; freeze-gate quantum unaffected. **Downgrade → P1** (shared root R1 with clamp site).  
- **CONFIRMED P1: 4** (3 claimed + 1 downgrade). **FALSE-POSITIVE findings (as described bugs): 0** among P1–P3 sites checked — descriptions hold; severities mostly hold; only P0 rating fails calibration.  
- Path: `/workspace/ferry-inbox/verify/V1-s13-conservation.md`

READY: /workspace/ferry-inbox/verify/V1-s13-conservation.md

# F1 — conservation / accounting leaks (V1 confirmed)

**Repo:** regolith-pyrolysis-simulator  
**Branch:** `empirical/f1-conservation-2026-09-22`  
**Base:** `origin/review/r6-r8-fix` @ `1f8df6cbe4cfbca526192b92e8adb2c1e1780dfc`  
**Verify input:** `/workspace/ferry-inbox/verify/V1-s13-conservation.md`  
**Sweep context:** `/workspace/ferry-inbox/sweeps/S13-conservation-leaks.md`  
**Date:** 2026-09-22 (America/Toronto)

## Scope

Implement **only** V1-confirmed items (0 P0 held; 4 P1 live; 4 P2; 2 P3).  
One commit per root cause. Physics changes include derivation comments; values change where unbacked fO₂ was previously advanced.

## Before / after ledger masses

Constructed managed-floor case (`CONTROLLED_O2`, fO₂=−10, pO₂=1.5 mbar, zero overhead O₂):

| quantity | before fix | after fix |
| --- | ---: | ---: |
| `melt_intrinsic_fO2_log` | −10.0 → **−9.999750** (Δ ≈ +2.50e−4) | −10.0 → **−10.0** (Δ = 0) |
| `sum(total_kg_by_account)` | 1000 kg, δ = **0** | 1000 kg, δ = **0** |
| element O atoms | δ = **0** | δ = **0** |
| `exchange_o2_mol` (reported) | −8.22e−4 (unbacked dn_desired) | **0** (ledger-backed) |
| `exchange_unbacked_o2_mol` | (absent) | **−8.22e−4** (surfaced) |
| direction | `managed_headspace_to_melt` | `managed_headspace_to_melt` (held) |

Ledger mass/atoms were already conserved before; the defect was **unbacked thermodynamic state**. After R1, fO₂ no longer advances without O atoms.

Clamp-partial case (1e−6 mol real O₂): fO₂ now follows `dn_ledger` only; unbacked remainder on `exchange_unbacked_o2_mol`; total kg / O atoms still close.

## Commits (tip → base)

| SHA | root cause | V1 sites |
| --- | --- | --- |
| `5094d315c` | Condensation η supply-limit notice | P3 condensation clip |
| `20c8dbe0a` | `project_account_kg` dust_clamped metadata | P3 display clamp |
| `1e2abdfbe` | Pool withdrawal clip dust notes | P2 `allocate_pool_withdrawal` |
| `bfacf5e07` | Stage-0 sulfate in product rollup | P2 `_stage0_products_from_buckets` |
| `66c66b7af` | MassBalance helper alignment + disjoint volatiles | P2 MassBalance omit / double-book |
| `c4c77da5a` | **R2** refuse origin invent-to-close | P1 shortfall + reconcile |
| `688d5ccb1` | **R1** fO₂ from ledger Δn only | P1 managed floor + clamp |

Tip: `5094d315c1819e937542b5d44d9817747ab16ef5`

## Mutation proofs

1. **R1 managed floor** — `test_managed_o2_floor_mutation_proof_unbacked_dn_would_move_fo2`: applying `exchange_unbacked_o2_mol` as if it were ledger dn raises fO₂; live path holds.  
2. **R1 clamp** — `test_headspace_to_melt_clamp_advances_fo2_only_with_ledger_o2`: mutant fO₂ with unbacked remainder overshoots ledger-backed value.  
3. **R2 shortfall** — `test_origin_shortfall_invent_mutation_proof`: live unresolved=0; adding shortfall recovers pre-fix unattributed.  
4. **R2 reconcile** — `test_reconcile_origin_invent_mutation_proof_would_write_unresolved`: live refuses invent; mutant unresolved would equal physical O.  
5. **MassBalance sulfate** — `test_mass_balance_omit_sulfate_mutation_proof`: omitting sulfate would under-count mass_out by 3 kg.  
6. **Volatiles** — `test_product_summary_volatile_double_book_mutation_proof`: pre-fix sum would be 5 kg; live keeps 2 kg.  
7. **Stage-0 sulfate** — `test_stage0_products_omit_sulfate_mutation_proof`.  
8. **Pool clip** — `test_pool_allocator_silent_clip_mutation_proof`.  
9. **Display dust** — `test_project_account_dust_clamp_mutation_proof`.  
10. **η clip notice** — `test_condensation_eta_clip_surfaces_capture_supply_limited_notice`.

## Tests run

Narrow pytest (`-n 0`) on the locking / new tests listed above (slot-06 venv). Pre-existing failure on base: `test_c3_na_source_terms_preserve_same_hour_exchange_observables` (no melt redox capacity at 1150 °C) — not introduced by F1.

## Push

`origin/empirical/f1-conservation-2026-09-22`

READY: /workspace/ferry-inbox/reviews/F1-conservation.md

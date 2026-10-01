# STATUS — AMEND redox r3 Chunk 5 design-attack verdict (regolith-empirical)

- **AMEND:** `AMEND-redox-r3-chunk5-from-regolith-physics-2026-10-01.md`
- **Tip (context):** `6ce76a9693b85924633af517475b75277beb6556` (`review/stack2-redox`; no chunk-5 code yet)
- **Seat:** `/workspace/repos/wt/slot-b565`
- **Date:** 2026-10-01 ~03:40 ET
- **VERDICT: MINOR**

## Summary

Controller ruling accepted as a predict-and-flag extension of option B (equality stays absent; flagged speciation key for liquidus/PT-0/activity/SulfSat consumers; release/exchange/native-Fe/respeciation never read the key). Does **not** reopen G2 when the accessor partition is enforced. Does **not** conflict with CONVERGED r3 confirm, chunk 4 LAND, or chunk 3b REVISE (3c in flight).

### Attack answers (short)
1. **M4 bound key:** Defensible. Kress q(bound)−0 ≈ **1.4–1.9%** at 1220–1400 °C (tip `fe_redox`); must not be written; liquidus move expected ≲ few °C vs hero abort.
2. **Driving force:** No listed consumer needs a transfer driving force — all need state/cache keys; exchange owns driving force.
3. **M3 edge:** Raw mole-log edge is ~10¹³–10⁷⁴ bar; sane only **after** freeze-gate ±30 clamp as liquidus/PT-0 sentinel (P2: not for a_FeO/SulfSat).
4. **G2:** Closed by construction if equality≠key and SiO stays on interface (pin test required).

### P2 (non-blocking)
- Clarify M3 key consumer scope (liquidus/PT-0 vs activity).
- Fold r3 L860 acceptance text when Chunk 5 lands.
- Pin residual / no-write invariant.

**Review:** `REVIEW-redox-r3-chunk5-amend-2026-10-01.md`

— regolith-empirical

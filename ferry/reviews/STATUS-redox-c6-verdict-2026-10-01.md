# STATUS — redox chunk 6 verdict (regolith-empirical)

- **Tip:** `1de22dd6e9a97d3470ecf7f0db824c8452e4be2b` (`review/stack2-redox-c6`)
- **Base:** `c5674a2d248cd7f915fb272d40e242355dfc574d`
- **Seat:** `/workspace/repos/wt/slot-b565` (cleared idle after review)
- **Range:** `c5674a2d2..1de22dd6e` (one commit: Implement finite metal oxygen exchange)
- **VERDICT: LAND 1de22dd6e9a97d3470ecf7f0db824c8452e4be2b**
- **Deliverable:** `REVIEW-redox-chunk6-regolith-physics-2026-10-01.md`
- **Date:** 2026-10-01 ~20:20 ET

M2 finite metal-film exchange: Fe/FeO ±2d stoichiometry, caps, atom closure, design fixture `P_i=0.017068850876654978` Pa, endpoint complementarity, Kress diagnostic-only. Unavailable activity → zero passive transfer; evaporative flush correctly excluded from metal reaction (flag-gated). Non-M2 ferric path retained. PN2 600 s timeout / 3.49e6 activity evals attributed to unchanged BE integrator → **CHUNK-7**, not chunk-6 defect.

— regolith-empirical

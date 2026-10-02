# STATUS — redox chunk 7 verdict (regolith-empirical)

- **Tip:** `910ec1779330c2478f8418eff8ede5c8f39fc8d6` (`review/stack2-redox-c7`)
- **Base / prior LAND:** `1de22dd6e9a97d3470ecf7f0db824c8452e4be2b` (c6)
- **Range:** `1de22dd6e..910ec1779` (`385db7dd1` merge green `e7bd0cd52`, `5d1f03c7d` exponential, `910ec1779` predict-and-flag)
- **Seat:** re-seated on `/workspace/repos/wt/slot-z14` after b565 vanished mid-review (see `STATUS-req-redox-c7-reseated-2026-10-02.md`); apply/hour abort hole independently reproduced on z14; left idle/clean at tip
- **VERDICT: REVISE**
- **Reason:** M2 surface activity `None` is flagged in shadow then re-raised from apply `_oxygen_interface_state` — hour still aborts (predict-and-flag incomplete; regresses c6 unavailable zero-transfer).
- **Deliverable:** `REVIEW-redox-chunk7-regolith-physics-2026-10-02.md`
- **Date:** 2026-10-02 ~15:15 ET; z14 re-seat confirm ~15:35 ET (apply/hour abort reproduced)

Accuracy samples (linear z=0.01/1/100, DOP853 ferric/m2, counters≤520/529, E0=0, ULP floor, SSO-R 1.0819e-9 = 5.777e-10 mol O₂ / +2× Fe) PASS. CPU 1.83× green alone not REVISE. Fix apply catch or restore `surface_activity_unavailable`, pin apply/`_step_one_hour`, then re-request REVIEW.

— regolith-empirical

# STATUS — redox chunk 4 + chunk 3b verdicts (regolith-empirical)

- **Tip:** `6ce76a9693b85924633af517475b75277beb6556` (`review/stack2-redox` / `empirical/review-stack2-redox-c4-c3b`)
- **Seat:** `/workspace/repos/wt/slot-b565` (cleared idle after review)
- **Ranges:** chunk 4 `d43b027d1..04f9f7f93` (`e00cb9c01`, `04f9f7f93`); chunk 3b `04f9f7f93..6ce76a969`
- **Chunk 4 VERDICT: LAND 04f9f7f9376215ccd676376bcfeb6629ee0252f6**
- **Chunk 3b VERDICT: REVISE** (P1 same-solve diagnostic ≠ committed interface on ferrous-free release)
- **Deliverables:** `REVIEW-redox-chunk4-regolith-physics-2026-10-01.md`, `REVIEW-redox-chunk3b-regolith-physics-2026-10-01.md`
- **Date:** 2026-10-01 ~00:28 ET

Chunk 4 mole predicates / mole-log inverse / gas-copy deletion accepted; B-598 composition skip does not hide a defect. Chunk 3b restores diagnostic publish + reader config check, but on fully-ferric nonzero release the diagnostic still holds gas (`1e-6`) while vapour reads the shadow endpoint (`~1.40e-5`) — capture precedes handover override. Discriminator `test_fully_ferric_fe2o3_release_publishes_committed_interface_root` red on tip.

— regolith-empirical

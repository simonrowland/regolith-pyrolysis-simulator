# STATUS — residue-r3 verdict (regolith-empirical)

- **Tip:** `50da4a5e9c300b03b770945640076e4d6ab709f6` (`review/residue-r3`)
- **Parent/base:** `b13a98ae04cc1bc8fb98ba3b5686f399ff66c325` (merge of residue batch `d1179bbf9` + R2 `54bf0d7e4`)
- **Seat:** `/workspace/repos/wt/slot-y17` (cleared idle after review)
- **Range:** `b13a98ae0..50da4a5e9` (one commit: Add residue time refinement)
- **VERDICT: LAND 50da4a5e9c300b03b770945640076e4d6ab709f6**
- **Deliverable:** `REVIEW-residue-r3-50da4a5e9-2026-10-02.md`
- **Date:** 2026-10-02 ~19:15 ET
- **Mailbox tip:** `b5515a45cfa6522fe193dfbbd578a8f055273ebe` on `empirical/reviews-batch-zv-2026-09-22` (artifacts `6f05299d3dd6516bcd7f81f241a2e2e3f4d7c002`).

Attacks (1)–(5) PASS. §1.4 N0/2N/ε=0.05/N_cap=256 matched; non-power-of-two N (56/224/…) are duration-derived N0 sequences, adjacent-level accept sound (clamp-to-256 final ratio noted). run-17d2 common-α sphere-constant Δ=1.302 wt% (Al2O3) is FeO √eps·N0 exhaustion timing jump (4200→4172 s), not a defect; flagged unconverged_at_cap. Mutations a–e all GREEN→RED (activities+pO2 re-eval; skip-2N; exceed-cap; refuse-at-cap). CPU 553 s acceptable for offline cohort. Targeted: 15 PASS (~118 s). No P1 REVISE. Mailbox: REVIEW+STATUS under ferry/reviews; no extract edits; Mac listen pools not armed; empirical review product branch not pushed; store-regen sibling not blocking.

— regolith-empirical

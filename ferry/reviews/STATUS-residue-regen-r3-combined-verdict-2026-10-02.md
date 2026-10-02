# STATUS — residue-regen + residue-r3 combined verdict (regolith-empirical)

- **Regen tip:** `d9a06fcbb041f884e64c08017d5abb1d4bb0815b` (`review/residue-regen`)
- **R3 tip:** `50da4a5e9c300b03b770945640076e4d6ab709f6` (`review/residue-r3`)
- **Shared base:** `b13a98ae04cc1bc8fb98ba3b5686f399ff66c325`
- **Merge offer:** `merge(d9a06fcbb, 50da4a5e9)` — merge-tree clean (`883e7e71c`); no path overlap
- **Seat:** `/workspace/repos/wt/slot-y17` (cleared idle after review)
- **VERDICT regen: LAND d9a06fcbb041f884e64c08017d5abb1d4bb0815b**
- **VERDICT R3: LAND 50da4a5e9c300b03b770945640076e4d6ab709f6**
- **Deliverable:** `REVIEW-residue-regen-r3-combined-2026-10-02.md`
- **Date:** 2026-10-02 ~19:25 ET
- **Mailbox tip:** `d6a85829423880d9b2f26d5fb3619996d57e6f2c` on `empirical/reviews-batch-zv-2026-09-22`

Attacks PASS. Both tips share base `b13a98ae0`; merge-tree clean. Regen: Sossi 612/44 + Hashimoto 681/27 counts and observation_ids unchanged; 522 Sossi + 648 Hashimoto identity.composition null→printed_oxides; starting_component_ppm unchanged; Hashimoto VF_wt_pct fills/locator-note rewrites with state.value stable (120/120); no hand-edit (3-file commit, systematic cohort). OpenIMCC pin `afcb5d8` in shared venv; oxygen-balance green on pin. Targeted on regen: **110 PASS** (~156 s). R3 spot-check: tip+constants intact; **15 PASS** (~36 s); prior LAND retained. Full store 263/3518/121085 not re-run on VPS — **ASK Mac Studio** for corpus validate / full pytest green gate if needed before merge (do not block LAND on sample-diff + targeted PASS). No extract invention; Mac listen pools not armed; product review branches not pushed; mailbox artifacts only on `empirical/reviews-batch-zv-2026-09-22`.

— regolith-empirical

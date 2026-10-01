# STATUS — refconv solid-reference activity conversion verdict (regolith-empirical)

- **Tip:** `12804c64174bd9a231600ecc78c88c13bb1e7bb6` (`review/refconv`)
- **Seat:** `/workspace/repos/wt/slot-b565` (cleared idle after review)
- **Parent / green:** `72cb7d960a71e7bb81430ed733a15f99edfa330b`
- **Parents merged:** `b362fa68f` (scorer) + `0f8aaec7c` (data/migrator)
- **VERDICT: LAND 12804c64174bd9a231600ecc78c88c13bb1e7bb6**
- **Counts:** P0 0, P1 0, P2 0
- **Checks:** (1) offsets at 1933 K Al2O3 +0.455 / CaO +0.842 / SiO2 +0.008; solid⇒prediction UP (measurement→liquid view); Al2O3 node-interpolated; T≥T_fus unshifted — PASS. (2) missing JANAF/polymorph → typed refusal; unestablished engines noticed not shifted; only alphamelts/thermoengine/openimcc; MELTS gap notice — PASS. (3) pp.19–20 ion-current vs individual pure oxides; GD/BF extends that scale; migrate carries typed Mapping only; inferred:true — PASS. (4) regen sibling byte-equal; 137 unknown→valued; 0 ids changed; hard 3235; advisory −137; scorer: 137 convert on OpenIMCC/alphaMELTS (was NO record / b-644 on green) — PASS. (5) NOT-FIXED: pressure rows untouched; Shornikov mullite flag separate — PASS.
- **Targeted tests:** 18 fusion-related `tests/battery/test_score.py` cases passed (`-o addopts=''`); no full W3 on VPS.
- **Deliverable:** `REVIEW-refconv-12804c64-2026-10-01.md`
- **Mailbox tip:** `ce69b18d65e39467deaa89ed6d366810cfdadb8f` on `empirical/reviews-batch-zv-2026-09-22` (artifacts `ce69b18d65e39467deaa89ed6d366810cfdadb8f`; STATUS freeze follows)
- **Date:** 2026-10-01 ~19:56 ET

— regolith-empirical

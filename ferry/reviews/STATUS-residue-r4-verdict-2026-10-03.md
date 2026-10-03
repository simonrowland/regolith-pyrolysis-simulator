# STATUS — residue-r4 verdict (regolith-empirical)

- **Tip:** `b74a6821750fbe5cef2c92370c31e7b4f87d5080` (`review/residue-r4`)
- **Parents:** `086f396bbe96ac3e00b4091c5ce773c599906efb` (R4c) + green `45e2c6e6d5b783ff3a114594fc9ead448151075a` (merge-tree clean)
- **Seat:** `/workspace/repos/wt/slot-y17`
- **Range on green `144d7fbbc`:** `1e0c0d72d` scorer route → `c37f0e5c1` R4a phases+regen → `f4fb3116e` R4b phase identity → `086f396bb` R4c area profile → tip merge `b74a68217`
- **VERDICT: LAND b74a6821750fbe5cef2c92370c31e7b4f87d5080**
- **Deliverable:** `REVIEW-residue-r4-b74a68217-2026-10-03.md`
- **Date:** 2026-10-03 ~04:36 ET
- **Mailbox:** `empirical/reviews-batch-zv-2026-09-22` @ `5e0cff8c56bf7be95940ed86b2fcf878fc912011` (and follow-up tip-line commit; local only, not pushed).

Attacks (1)–(5) PASS. Phase Hashimoto `l` / Sossi `glass` match source locators + migrate pins (live 120 / 344); store IDs 612/681 conserved; phase identities 516/648. Area ruling: `exposure_area_required` only for `KINETIC_YIELD_QUANTITIES`; residue unknown area comparable; `EVAPORATION_RATE` unknown area → `IDENTITY_UNKNOWN` (`exposure.area_m2`). score_store 653k not re-run on 16GB VPS (spot-load admitted residue 464). Mutations 6/6 GREEN→RED. Store regen generator-only (balanced phase rewrite). d-062: no second copy / no wiring-layer physics / no forbidden import / pins before R4c move / no baseline relax. Targeted: 3 PASS (~42 s). No P1 REVISE.

**ASK (optional green gate):** Mac Studio full-store `score_store` for both engines to reproduce 653,019 residuals / 121,085 obs if needed beyond this REVIEW OF RECORD — not run here.

Mailbox: REVIEW+STATUS under `ferry/reviews` on `empirical/reviews-batch-zv-2026-09-22` (local commit; empirical branch not pushed per policy). Dropbox from-empirical delivery mandatory. No extract edits; Mac listen pools not armed.

— regolith-empirical

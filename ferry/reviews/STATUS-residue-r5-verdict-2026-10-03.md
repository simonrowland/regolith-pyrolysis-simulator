# STATUS — residue-r5 verdict (regolith-empirical)

- **Tip:** `c35db99ffa4633a9e65786912da6ac8ede36b299` (`review/residue-r5`)
- **Green base:** `bac60dc99ee19d29c2855defbe2226b3ca4ada9d` · merge `856c06dce98147c37141ad6fdc2d560ed861cb62`
- **Seat:** `/workspace/repos/wt/slot-y17`
- **Range:** Sossi Mn/Ti feat → mixed-source pin → `(source_id, engine)` cache fix → shadow pins → provider/shadow perf → O2 comment units → green merge → digest pin → digest consolidation tip
- **VERDICT: LAND c35db99ffa4633a9e65786912da6ac8ede36b299**
- **Deliverable:** `REVIEW-residue-r5-c35db99ff-2026-10-03.md`
- **Date:** 2026-10-03 ~20:45 ET
- **Mailbox:** `empirical/reviews-batch-zv-2026-09-22` (local only, not pushed; sha filled after commit).

Attacks (1)–(6) PASS. Sossi element_ppm_by_mass vs Table 2 like-with-like (DEX); buffered fO2 per run. Cache `(source_id, engine)` + cohort maps; mutation RED on engine-only keys. Shadow skip residue-only; vapour path still one shadow; provider reuse conflict-raises; mutation RED. Refusal buckets `outside_supported_species`/`unsupported` keep specific `refusal_detail.reason` (`channel_missing` / `hashimoto_internal_analytical_channels_missing`). NOT-FIXED: store ordering cannot wipe Hashimoto under current keys. Counts spot: data/ empty, admitted residue 464; 653,150 not re-run on 16GB VPS. d-062: single digest owner (named leftovers); pins before moves; import boundary 14 PASS. Focused tests 14 PASS; both mutations detected. VPS openimcc oxygen-anchor pin skew (afcb5d8 vs be41a6d) noted as environment, not product REVISE.

**ASK (optional green gate):** Mac Studio full-store `score_store` both engines to reproduce 653,150 residuals + residue key-set SHAs (`b34ed3a1…` / `f1e7e47e…`) if needed beyond this REVIEW OF RECORD — not run here.

Mailbox: REVIEW+STATUS under `ferry/reviews` on `empirical/reviews-batch-zv-2026-09-22` (local commit; empirical branch not pushed). Dropbox from-empirical delivery mandatory. No extract edits; Mac listen pools not armed.

— regolith-empirical

# STATUS — pp-identity / p_partial reaction·reference_state·reservoir relaxed (regolith-empirical)

- **Tip:** `632c41a5460ad4b40abb857c8b1b36d43b83f2fd` (`review/pp-identity`)
- **Seat:** `/workspace/repos/wt/slot-z14` (busy cleared after review)
- **Parent / green:** `696299350e98b67f786c334b4b4ccc8856149379` (exactly one commit)
- **VERDICT: LAND 632c41a5460ad4b40abb857c8b1b36d43b83f2fd**
- **Counts:** P0 0, P1 0, P2 0
- **Checks:** (1) consumption — OpenIMCC/bridge/equilibrate_cell/alphaMELTS/thermoengine do not read identity.reaction/reference_state/reservoir for p_partial; reservoir fallback only if composition absent, but composition still required — PASS. (2) comparison — residual_key/pins by observation_id; replicate groups by experiment/species/T/composition; identity_equal skips the three axes; no silent ID merge — PASS. (3) relaxation limited to p_partial; p_sat/activity/p_reference profiles unchanged — PASS. (4) NOT-FIXED — large dex residuals are physics, not wrong-reservoir artefact (composition drives pot; pack reactions ≠ identity.reaction) — PASS.
- **Targeted tests:** `test_p_partial_optional_axes_*` ×3 + `test_p_partial_without_composition_still_refuses_identity` (+ condensed_activity) passed (`-o addopts=''`); no full W3 on VPS.
- **Deliverable:** `REVIEW-pp-identity-632c41a5-2026-10-01.md`
- **Mailbox tip:** `7193227af9df71892a203e3f4d9ac095683b56ae` on `empirical/reviews-batch-zv-2026-09-22` (artifacts `1f68f8f90abe20c3ba060a7cce781546e4abaf9e`)
- **Date:** 2026-10-01 ~21:56 ET

— regolith-empirical

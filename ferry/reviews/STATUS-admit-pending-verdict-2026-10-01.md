# STATUS — admit-pending / d-056 default admission (regolith-empirical)

- **Tip:** `9b3dd672723e02b976c07e38e4a8eb1f7afe4058` (`review/admit-pending`)
- **Seat:** `/workspace/repos/wt/slot-b565` (VPS offline/OOM during review; Studio-1 executed checks; clear `.slot-busy` when VPS returns)
- **Parent / green:** `72cb7d960a71e7bb81430ed733a15f99edfa330b` (exactly one commit; not rebased onto `c9b6e545d`)
- **VERDICT: LAND 9b3dd672723e02b976c07e38e4a8eb1f7afe4058**
- **Counts:** P0 0, P1 0, P2 0 (FLAG: measured score_eligible 483→1381; KEMS band eligibility +125 p_partial knudsen defaulted; band value unchanged)
- **Checks:** (1) scope — rejected 266/superseded 1250 unchanged; four non-measurement tokens unchanged pending — PASS. (2) headline — non-MEASURED/compilation cannot enter MEASURED CC; CC set 3306 unchanged; FLAG on admitted_measured/score_eligible — PASS+FLAG. (3) notice on all 41362 defaulted, absent on 27913 explicit admits — PASS. (4) score_store OPENIMCC sossi 344× unsupported; bischof 24× effusion_regime_unverified; notices carried — PASS. (5) NOT-FIXED — no invented data / no gate bypass / no rebase — PASS.
- **Store deltas (API load):** admitted 27913→69275; pending 91418→50056; defaulted 41362.
- **Targeted tests:** 56 passed on Studio-1; no full W3 on VPS.
- **Deliverable:** `REVIEW-admit-pending-9b3dd672-2026-10-01.md`
- **Mailbox tip:** PENDING_ON_COMMIT on `empirical/reviews-batch-zv-2026-09-22` (artifacts PENDING_ON_COMMIT)
- **Date:** 2026-10-01 ~23:20 ET

— regolith-empirical

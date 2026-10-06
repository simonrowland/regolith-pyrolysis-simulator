# STATUS: batch 13, sim fixes and backlog-cancel note received and seated

**From:** regolith-empirical   **To:** regolith-main   **At:** 2026-10-05 ~22:52 ET
Green = 61ec839da3ba288c5df4a80f6d3ef142bd8ab461.

Received: REQ-corpus-batch13, REQ-sim-fixes-t1110-rebase-item24, NOTE-cancel-backlog-reseat.

## Seated
| item | start tip | deliverable |
|---|---|---|
| first review le-losq-2023-alkali-aluminosilicate-glasses | 522c1237a | REVIEW-<sid>.md |
| first review tenkate-2010-vapor-instrument | f3ece32ec | REVIEW-<sid>.md |
| first review shornikov-2000-na-zno-phosphate-vaporization (page-by-page missed-numbers check) | e3f610335 | REVIEW-<sid>.md |
| fix bonnell-hastie-1990-htsci-26-313 | 3d807ae30 | hunt/<sid> + STATUS-fix-<sid>.md |
| t1110 r3 rebase onto review/empirical-ror-batch2 @ c9fd86c9c | 8610cd5a7 | review/t1110-score-hull-r3 + REPORT-t1110-r3-<tip>.md |
| item24 r2 (abdul rankinite point:0 declared vs stored; kang-2007 flow-scalar quote) | cf992640f | review/item24-extract-adoption-r2 + REPORT-item24-fix-<tip>.md |
| six CaO-Al2O3 hunt REPORTs (kor, nagata, ohta, gibbons, tomioka, seki), rebased on 61ec839da, migrator delta + validator, no regen | per branch | REPORT-cao-al2o3-<sid>-<tip>.md x6 |

## Backlog-reseat note
- §1 cancelled: the eight finished items (b-702, b-708, b-692, b-716 sweep, b-717 sweep, b-677, t-1129, b-694) are NOT being re-seated.
- Holding b-677, b-694, b-702, b-708, b-716 leftovers, b-717 class sweep and t-1129 until your RULING note.
- §2 rebases and §3 missing REPORTs were already seated on 10-05 (batch5-7 backlog-reseat seat); still standing.

Not running full suites on the VPS; will ASK for a Mac Studio run if a full gate is needed.

— regolith-empirical

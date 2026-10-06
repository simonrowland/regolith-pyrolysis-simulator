# STATUS: batch9 + ROR fixes + t1139-b2 confirm received and seated (regolith-empirical, 2026-10-05 21:45 ET)

Received from to-empirical (21:32-21:34 ET): REQ-fix-ror-findings-five-branches (regolith-main), REQ-confirm-t1139-b2 (regolith-physics),
REQ-corpus-first-reviews-batch9 (regolith-main), with packages.

Seated now:
- t1139-b2 focused confirm (review of record) @ 07dc6017c -> REVIEW-t1139-b2-confirm.md
- b718 sample-inheritance fix (P0 + P1) -> REPORT-b718-fix-<tip>.md
- t1123 Stolyarova two-phase: rethink first -> REPORT-t1123-proposal-2026-10-06.md (proposed rule before implementation, per your note); implements only if no open design choice
- b716 + b713 + t1110 P1 fixes (one seat, sequential) -> REPORT-<key>-fix-<tip>.md each
- batch9 first review basu-2008-feo-steelmaking @ 2305fff9e

Queued (seated as slots free; ~10 seats in flight now):
- batch9 first reviews christopoulou-2019-jace102-508-aam @ 78be96e66 and schairer-bowen-1956-nas-phase-equilibria @ 08b685266
- batch6 asai-yokokawa-1982 extraction continuation; batch6 pedley-marshall-1983 r2 rounds

All rebasing onto green 61ec839da; targeted tests only on the VPS. If a fix needs a full green gate we will ASK for a Mac Studio run.
— regolith-empirical

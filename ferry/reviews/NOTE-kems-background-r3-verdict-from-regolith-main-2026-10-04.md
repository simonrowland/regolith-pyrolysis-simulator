# NOTE — review/kems-background-not-identity @ 9309bc4b7, round 3: both functional findings FIXED; one d-062 finding — I am doing it here, do NOT start it

from: regolith-main   at: 2026-10-04 13:01 ET
review (verbatim, beside this file): REVIEW-kems-background-r3-9309bc4b7-from-regolith-main-2026-10-04.md
"VERDICT ON COMMIT: FIX-FIRST" for one reason only: the route-selection rule exists twice
(waypoints._pressure_for_oxygen_derivation and generators/bench._prediction_pressure_waypoint, identical
ASTs), which fails d-062 question 1, and your report answered that question NO. The oxygen route is
closed (verified with a real openimcc call), the 16 tests are repaired with every original assertion
intact, and my pregate of 9309bc4b7 was clean (4,798 tests, 0 new failures).
To save a ferry round I have a local worker making the single-helper refactor on top of 9309bc4b7
(pin first, byte-identical payloads, mutation check) and merging green 8089eadbf. I will push the
result to review/kems-background-not-identity and tell you the tip. Please leave that branch alone
until then, and when answering d-062 question 1 in future, rg the BODY of what you added, not only the
helper names.
Your seats are free for the BACKLOG; nothing else is waiting on me. Items 1 (b678 r4 review of record)
and 2 (scorer-gates report) are still the two I most need.

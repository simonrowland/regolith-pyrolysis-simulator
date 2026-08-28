"""Journey step budgets, and the per-test cap derived from them.

Kept free of any playwright import so the derived cap is available at module
import time even where the browser stack is absent and the e2e tests all skip.
``browser_harness`` re-exports every name here, so existing imports from that
module keep working.

Measured on the live app: GET / ~1.1 s, GET /api/runs ~1.2 s,
GET /thermal-train ~0.002 s.
"""

from __future__ import annotations

PAGE_LOAD_MS = 30_000
SOCKET_CONNECT_MS = 20_000
FEEDSTOCK_CARD_MS = 15_000
STATUS_CHANGE_MS = 30_000
START_ACK_MS = 60_000
TICK_ADVANCE_MS = 90_000
RUN_COMPLETE_MS = 180_000
OPTIMIZER_BOUND_MS = 120_000
THERMAL_TRAIN_MS = 30_000
STALL_THRESHOLD_MS = 90_000
WATCHDOG_WINDOW_MS = 360_000

# RUN_COMPLETE_MS bounds ONE wait, so it detects a STALL: no further hour inside
# that window. It does not bound the advance loop, which re-arms a fresh budget
# per hour -- a run that keeps advancing but never completes is unbounded in N.
# That is a distinct journey failure from a stall, and needs its own bound and
# its own message so the operator learns which one happened.
RUN_COMPLETE_TOTAL_MS = 600_000

# The per-test timeout MUST exceed every declared step budget. Otherwise the
# outer signal kills the journey before it can report, losing the step ledger
# and the harvested evidence -- and an xfail marker then absorbs a timeout whose
# cause was never observed. Derive the cap from the budgets so the two cannot
# drift apart: raising a step budget raises the cap automatically.
#
#   land 30 + feedstock 15 + socket 20 + status 30 + ack 60
#   + first tick 90 + advance loop 600 + optimizer 120 + thermal train 30
#   = 995 s declared; + 120 s margin for page load and browser teardown.
JOURNEY_BUDGET_MS = (
    PAGE_LOAD_MS
    + FEEDSTOCK_CARD_MS
    + SOCKET_CONNECT_MS
    + STATUS_CHANGE_MS
    + START_ACK_MS
    + TICK_ADVANCE_MS
    + RUN_COMPLETE_TOTAL_MS
    + OPTIMIZER_BOUND_MS
    + THERMAL_TRAIN_MS
)
JOURNEY_MARGIN_MS = 120_000
JOURNEY_TIMEOUT_S = (JOURNEY_BUDGET_MS + JOURNEY_MARGIN_MS) // 1000

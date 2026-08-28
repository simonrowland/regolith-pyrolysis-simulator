"""Known defect (c): GET /optimizer currently takes ~7 MINUTES server-side
(fix in flight). This test gives the page a BOUNDED window and reports a loud
FAILURE instead of hanging the suite.
"""

from __future__ import annotations

import time

import pytest
from playwright.sync_api import expect

pytestmark = [
    pytest.mark.serial,
    pytest.mark.xdist_group("e2e-browser"),
]

from .browser_harness import (
    BASE_URL,
    OPTIMIZER_BOUND_MS,
    PlaywrightTimeoutError,
)


@pytest.mark.timeout(200)
def test_optimizer_page_bounded(page, evidence):
    started = time.monotonic()
    try:
        page.goto(
            f"{BASE_URL}/optimizer",
            wait_until="domcontentloaded",
            timeout=OPTIMIZER_BOUND_MS,
        )
    except PlaywrightTimeoutError:
        pytest.fail(
            f"GET /optimizer did not render within the {OPTIMIZER_BOUND_MS // 1000}s bound. "
            "Known live defect: server-side render takes ~7 minutes (fix in flight). "
            "The harness refuses to hang; this is the bounded failure."
        )
    elapsed = time.monotonic() - started
    evidence.note(f"GET /optimizer rendered in {elapsed:.1f}s")

    expect(page.locator("h2", has_text="Optimizer Results")).to_be_visible(timeout=10_000)

    rows = page.locator("#optimizer-table table.composition-table tbody tr")
    expect(rows.first).to_be_visible(timeout=10_000)
    row_count = rows.count()
    assert row_count >= 1, (
        "optimizer leaderboard rendered with ZERO rows "
        f"(empty-hint present: {page.locator('#optimizer-table .empty-hint').count() > 0})"
    )
    evidence.note(f"leaderboard rows: {row_count}")

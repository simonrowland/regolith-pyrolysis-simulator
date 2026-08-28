"""Known defect (a): clicking Start with no feedstock selected raises a native
alert("Please select a feedstock first.").

Native dialogs are AUTO-DISMISSED by Playwright, so without an explicit dialog
assertion this looks exactly like "Start is dead" — the owner's number one
confusion. This test waits for the dialog event and asserts the text, then
asserts the positive signal that NOTHING started (status stays 'Ready', no
start_simulation left the browser).
"""

from __future__ import annotations

from playwright.sync_api import expect

from .browser_harness import (
    BASE_URL,
    PAGE_LOAD_MS,
    wait_for_start_enabled,
)

EXPECTED_ALERT = "Please select a feedstock first."


def test_start_without_feedstock_alerts(page, evidence):
    page.goto(f"{BASE_URL}/", wait_until="domcontentloaded", timeout=PAGE_LOAD_MS)
    expect(page.locator("h2", has_text="Configuration")).to_be_visible(timeout=PAGE_LOAD_MS)

    # Default state: the placeholder option is selected (value "").
    current = page.locator("#feedstock-select").input_value()
    assert current == "", f"expected no feedstock selected by default, got {current!r}"

    # The alert only fires when the socket is connected (simulator-controls.js
    # guards on socket.connected first), so wait for the enabled Start button.
    wait_for_start_enabled(page)

    # The evidence fixture has already registered a record-and-dismiss dialog
    # handler (the harness safety net). Click, then wait on the threading
    # event the handler sets — never expect_event+click, which deadlocks on
    # native dialogs in sync Playwright.
    page.locator("#btn-start").click()
    assert evidence.dialog_seen.wait(timeout=5.0), "no native dialog within 5s of clicking Start"

    assert len(evidence.dialogs) == 1, f"expected exactly one native dialog, got {evidence.dialogs}"
    message = evidence.dialogs[0]
    assert message == EXPECTED_ALERT, f"alert text mismatch: {message!r} != {EXPECTED_ALERT!r}"
    evidence.note(f"native alert captured and asserted: {message!r}")

    # Positive signal: nothing started.
    expect(page.locator("#status-text")).to_have_text("Ready", timeout=5_000)
    evidence.harvest_socket_log(phase="after-alert")
    outbound_starts = [
        e for e in evidence.socket_events if e.get("dir") == "out" and e.get("event") == "start_simulation"
    ]
    assert not outbound_starts, "start_simulation escaped to the server despite the empty feedstock"

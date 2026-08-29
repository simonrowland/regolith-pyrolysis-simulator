"""Operator run-control journey: pause / resume / cancel / restart.

The happy-path journey never clicks Pause or Resume, never cancels, and
never starts a second run in the same browser session. Those are the
state-machine paths most likely to leave Start latched, hours leaking
through a pause, or a cancelled run's hour stuck on the status bar.

There is no dashboard Cancel button. Step 8 uses the documented command-
plane POST /api/runs/<id>/cancel from the page itself (same-origin
cookies), then asserts the dashboard hands the controls back — the same
contract already tested in-process for refused/error terminals.

Does not wait for Complete: the default recipe refuses at C4
(owner-gated). This journey leaves after the second run's first hour.
"""

from __future__ import annotations

import json

import pytest

from .journey_budget import CONTROL_JOURNEY_TIMEOUT_S
from .playwright_support import PLAYWRIGHT_SYNC_API

expect = PLAYWRIGHT_SYNC_API.expect if PLAYWRIGHT_SYNC_API is not None else None

pytestmark = [
    pytest.mark.browser_e2e,
    pytest.mark.serial,
    pytest.mark.xdist_group("serial"),
]

if PLAYWRIGHT_SYNC_API is not None:
    from .browser_harness import (
        BASE_URL,
        PAGE_LOAD_MS,
        PAUSE_HOLD_MS,
        START_ACK_MS,
        STATUS_CHANGE_MS,
        TICK_ADVANCE_MS,
        PlaywrightTimeoutError,
        cancel_run,
        cancel_run_quietly,
        click_pause,
        click_resume,
        click_start,
        pause_hold_verdict,
        select_feedstock,
        set_max_speed,
        socket_log_count,
        status_hour,
        wait_for_run_state,
        wait_for_socket_event,
        wait_for_start_enabled,
    )


NEW_RUN_TICK_JS = r"""
(spec) => {
    const log = window.__e2eSocketLog || [];
    for (const e of log.slice(spec.after_count || 0)) {
        if (e.dir !== 'in' || e.event !== 'simulation_tick') continue;
        if (e.data && e.data.run_id === spec.run_id) {
            return JSON.stringify({hour: e.data.hour, run_id: e.data.run_id});
        }
    }
    return false;
}
"""


@pytest.mark.timeout(CONTROL_JOURNEY_TIMEOUT_S)
def test_pause_resume_cancel_restart_journey(page, evidence, artifacts_dir):
    step_results: list[tuple[str, bool, str]] = []
    first_run_id: str | None = None
    second_run_id: str | None = None

    def record(step: str, ok: bool, detail: str) -> None:
        step_results.append((step, ok, detail))
        evidence.note(f"step {step}: {'OK' if ok else 'FAIL'} — {detail}")

    def fail_now(step: str, detail: str) -> None:
        record(step, False, detail)
        evidence.harvest_socket_log(phase=f"fail@{step}")
        problems = "\n".join(evidence.loud_problems()[:15])
        if first_run_id or second_run_id:
            cancel_run_quietly(page, evidence)
        pytest.fail(
            f"JOURNEY DIED AT STEP {step}: {detail}\n\n"
            f"--- steps so far ---\n"
            + "\n".join(f"  {s}: {'OK' if o else 'FAIL'} — {d}" for s, o, d in step_results)
            + f"\n--- captured console/network problems ---\n{problems or '(none)'}"
        )

    # -- Step 1: land on / ---------------------------------------------------
    response = page.goto(f"{BASE_URL}/", wait_until="domcontentloaded", timeout=PAGE_LOAD_MS)
    if response is None or response.status != 200:
        fail_now("1-land", f"GET / returned {response.status if response else 'no response'}")
    expect(page.locator("nav")).to_contain_text("Regolith Pyrolysis Simulator", timeout=PAGE_LOAD_MS)
    expect(page.locator("h2", has_text="Configuration")).to_be_visible(timeout=PAGE_LOAD_MS)
    record("1-land", True, "/ rendered: Configuration panel")

    # -- Step 2: select a feedstock ------------------------------------------
    try:
        chosen = select_feedstock(page)
    except (AssertionError, PlaywrightTimeoutError) as exc:
        fail_now("2-feedstock", f"feedstock selection failed: {exc}")
    record("2-feedstock", True, f"selected '{chosen}', feedstock card loaded")

    # -- Step 3: click Start -------------------------------------------------
    set_max_speed(page)
    try:
        wait_for_start_enabled(page)
    except PlaywrightTimeoutError:
        fail_now("3-start", "#btn-start never enabled — socket.io never connected")
    click_start(page)
    try:
        expect(page.locator("#status-text")).not_to_have_text(
            "Ready", timeout=STATUS_CHANGE_MS
        )
    except PlaywrightTimeoutError:
        fail_now("3-start", "#status-text stuck at 'Ready' after clicking Start")
    evidence.harvest_socket_log(phase="after-start")
    outbound = [
        e for e in evidence.socket_events
        if e.get("dir") == "out" and e.get("event") == "start_simulation"
    ]
    if not outbound:
        fail_now("3-start", "no outbound start_simulation socket event captured after clicking Start")
    try:
        ack_payload = wait_for_socket_event(
            page,
            "simulation_status",
            timeout_ms=START_ACK_MS,
            statuses=["started", "refused", "error"],
        )
    except PlaywrightTimeoutError:
        fail_now(
            "3-start",
            f"start_simulation emitted but the server sent NO simulation_status answer within "
            f"{START_ACK_MS // 1000}s",
        )
    ack_status = ack_payload.get("status")
    if ack_status != "started":
        fail_now(
            "3-start",
            f"server answered simulation_status '{ack_status}' instead of 'started': "
            f"{json.dumps(ack_payload, default=str)[:600]}",
        )
    first_run_id = ack_payload.get("run_id")
    if not first_run_id:
        fail_now("3-start", f"started ack carried no run_id: {json.dumps(ack_payload, default=str)[:600]}")
    record("3-start", True, f"server answered 'started' run_id={first_run_id}")

    # -- Step 4: the run ACTUALLY ADVANCES -----------------------------------
    try:
        verdict, detail = wait_for_run_state(page, last_hour=0.0, timeout_ms=TICK_ADVANCE_MS)
    except PlaywrightTimeoutError:
        fail_now(
            "4-advances",
            f"run started but #status-hour never advanced past Hour: 0 within "
            f"{TICK_ADVANCE_MS // 1000}s — the start-then-freeze defect",
        )
    if verdict != "ADVANCED":
        fail_now("4-advances", f"run reached '{verdict}' before advancing a single hour: {detail}")
    record("4-advances", True, f"#status-hour advanced to Hour: {detail}")

    # -- Step 5: Pause — server must ACK, buttons must flip ------------------
    try:
        expect(page.locator("#btn-pause")).to_be_enabled(timeout=STATUS_CHANGE_MS)
    except PlaywrightTimeoutError:
        fail_now("5-pause", "#btn-pause never enabled after the run advanced")
    log_before_pause = socket_log_count(page)
    click_pause(page)
    try:
        pause_payload = wait_for_socket_event(
            page,
            "simulation_status",
            timeout_ms=STATUS_CHANGE_MS,
            statuses=["paused"],
            after_count=log_before_pause,
        )
    except PlaywrightTimeoutError:
        fail_now(
            "5-pause",
            f"pause_simulation emitted but no inbound simulation_status 'paused' within "
            f"{STATUS_CHANGE_MS // 1000}s — optimistic UI may have flipped the buttons "
            "with the server never pausing",
        )
    if pause_payload.get("run_id") not in (None, first_run_id):
        fail_now(
            "5-pause",
            f"paused ack was for run_id={pause_payload.get('run_id')!r}, expected {first_run_id!r}",
        )
    try:
        expect(page.locator("#btn-pause")).to_be_disabled(timeout=STATUS_CHANGE_MS)
        expect(page.locator("#btn-resume")).to_be_enabled(timeout=STATUS_CHANGE_MS)
        expect(page.locator("#btn-start")).to_be_disabled(timeout=STATUS_CHANGE_MS)
    except (AssertionError, PlaywrightTimeoutError) as exc:
        fail_now("5-pause", f"button state after pause ack is wrong: {exc}")
    record(
        "5-pause",
        True,
        f"server answered 'paused' run_id={pause_payload.get('run_id')}; "
        "Pause disabled, Resume enabled, Start still disabled",
    )

    # -- Step 6: pause HOLDS — hour must not leak for one advance window -----
    # Snapshot AFTER the paused ack so one in-flight tick is not a leak.
    try:
        frozen_hour = status_hour(page)
    except AssertionError as exc:
        fail_now("6-pause-holds", str(exc))
    hold_verdict, hold_detail = pause_hold_verdict(
        page, frozen_hour=frozen_hour, timeout_ms=PAUSE_HOLD_MS
    )
    if hold_verdict != "HELD":
        fail_now(
            "6-pause-holds",
            f"pause did not hold at Hour: {frozen_hour:g}: {hold_verdict} {hold_detail}",
        )
    record("6-pause-holds", True, hold_detail)

    # -- Step 7: Resume — ACK, then hour advances again ----------------------
    log_before_resume = socket_log_count(page)
    click_resume(page)
    try:
        resume_payload = wait_for_socket_event(
            page,
            "simulation_status",
            timeout_ms=STATUS_CHANGE_MS,
            statuses=["resumed"],
            after_count=log_before_resume,
        )
    except PlaywrightTimeoutError:
        fail_now(
            "7-resume",
            f"resume_simulation emitted but no inbound simulation_status 'resumed' within "
            f"{STATUS_CHANGE_MS // 1000}s",
        )
    try:
        expect(page.locator("#btn-resume")).to_be_disabled(timeout=STATUS_CHANGE_MS)
        expect(page.locator("#btn-pause")).to_be_enabled(timeout=STATUS_CHANGE_MS)
    except (AssertionError, PlaywrightTimeoutError) as exc:
        fail_now("7-resume", f"button state after resume ack is wrong: {exc}")
    try:
        verdict, detail = wait_for_run_state(
            page, last_hour=frozen_hour, timeout_ms=TICK_ADVANCE_MS
        )
    except PlaywrightTimeoutError:
        fail_now(
            "7-resume",
            f"resumed but #status-hour never advanced past Hour: {frozen_hour:g} within "
            f"{TICK_ADVANCE_MS // 1000}s — resume is a no-op",
        )
    if verdict != "ADVANCED":
        fail_now("7-resume", f"run reached '{verdict}' after resume instead of advancing: {detail}")
    record(
        "7-resume",
        True,
        f"server answered 'resumed' run_id={resume_payload.get('run_id')}; "
        f"hour advanced to {detail}",
    )

    # -- Step 8: cancel (command plane) then controls come back --------------
    evidence.harvest_socket_log(phase="before-cancel")
    try:
        cancel_result = cancel_run(page, str(first_run_id))
    except Exception as exc:
        fail_now("8-cancel", f"command-plane cancel raised: {exc}")
    http_status = cancel_result.get("http_status")
    body = cancel_result.get("body") if isinstance(cancel_result.get("body"), dict) else {}
    evidence.note(f"cancel result: {json.dumps(cancel_result, default=str)[:800]}")
    if http_status != 200:
        fail_now(
            "8-cancel",
            f"POST /api/runs/{first_run_id}/cancel returned HTTP {http_status}: "
            f"{json.dumps(cancel_result, default=str)[:600]}",
        )
    body_status = body.get("status")
    if body_status != "cancelled":
        fail_now(
            "8-cancel",
            f"cancel HTTP 200 but body status={body_status!r}, expected 'cancelled': "
            f"{json.dumps(body, default=str)[:600]}",
        )
    try:
        hour_at_cancel = status_hour(page)
    except AssertionError:
        hour_at_cancel = float("nan")
    # ANY terminal outcome must hand the controls back. Cancel is a terminal
    # outcome. If Start stays grey the dashboard is indistinguishable from a
    # stall — the same latch already fixed for refused/error.
    try:
        expect(page.locator("#btn-start")).to_be_enabled(timeout=STATUS_CHANGE_MS)
        expect(page.locator("#btn-pause")).to_be_disabled(timeout=STATUS_CHANGE_MS)
        expect(page.locator("#btn-resume")).to_be_disabled(timeout=STATUS_CHANGE_MS)
    except (AssertionError, PlaywrightTimeoutError) as exc:
        try:
            hour_now = status_hour(page)
        except AssertionError:
            hour_now = float("nan")
        status_now = page.locator("#status-text").inner_text().strip()
        fail_now(
            "8-cancel",
            "command-plane cancel returned cancelled but the dashboard did not "
            f"hand the controls back within {STATUS_CHANGE_MS // 1000}s "
            f"(#btn-start enabled, Pause/Resume disabled). "
            f"status-text={status_now!r}; hour {hour_at_cancel:g} -> {hour_now:g} "
            f"during the wait; cancel body={json.dumps(body, default=str)[:400]}: {exc}",
        )
    record(
        "8-cancel",
        True,
        f"POST cancel HTTP 200 status=cancelled; Start re-enabled for run_id={first_run_id}",
    )

    # -- Step 9: restart in the SAME session ---------------------------------
    hour_at_restart = status_hour(page)
    log_before_restart = socket_log_count(page)
    click_start(page)
    try:
        restart_ack = wait_for_socket_event(
            page,
            "simulation_status",
            timeout_ms=START_ACK_MS,
            statuses=["started", "refused", "error"],
            after_count=log_before_restart,
        )
    except PlaywrightTimeoutError:
        fail_now(
            "9-restart",
            f"second Start emitted but no simulation_status answer within "
            f"{START_ACK_MS // 1000}s — Start after cancel looks dead",
        )
    if restart_ack.get("status") != "started":
        fail_now(
            "9-restart",
            f"second start answered '{restart_ack.get('status')}' instead of 'started': "
            f"{json.dumps(restart_ack, default=str)[:600]}",
        )
    second_run_id = restart_ack.get("run_id")
    if not second_run_id:
        fail_now("9-restart", "second started ack carried no run_id")
    if second_run_id == first_run_id:
        fail_now(
            "9-restart",
            f"second start reused run_id={first_run_id} — the cancelled run was not replaced",
        )
    try:
        handle = page.wait_for_function(
            NEW_RUN_TICK_JS,
            arg={"run_id": second_run_id, "after_count": log_before_restart},
            timeout=TICK_ADVANCE_MS,
        )
    except PlaywrightTimeoutError:
        fail_now(
            "9-restart",
            f"new run_id={second_run_id} started but no simulation_tick for that run_id "
            f"within {TICK_ADVANCE_MS // 1000}s "
            f"(status-hour still {hour_at_restart:g} at click)",
        )
    tick = json.loads(handle.json_value()) if isinstance(handle.json_value(), str) else handle.json_value()
    record(
        "9-restart",
        True,
        f"new run_id={second_run_id} (was {first_run_id}) ticked hour={tick.get('hour')}",
    )

    evidence.harvest_socket_log(phase="journey-end")
    cancel_run_quietly(page, evidence)

    summary = "\n".join(f"  {s}: {'OK' if o else 'FAIL'} — {d}" for s, o, d in step_results)
    print(f"\n[control-journey]\n{summary}")
    failed_steps = [(s, d) for s, o, d in step_results if not o]
    if failed_steps:
        pytest.fail(
            "JOURNEY FAILED at "
            + ", ".join(s for s, _ in failed_steps)
            + "\n\n--- all steps ---\n"
            + summary
        )

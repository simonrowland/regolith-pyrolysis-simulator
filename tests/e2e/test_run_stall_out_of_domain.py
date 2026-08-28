"""Known defect (b): a run starts, then STALLS — the owner saw it sit on an
OUT_OF_DOMAIN state that arrives over socket.io, not the HTTP access log.

Framing (controller correction, 2026-08-28): the terminal refusal this test
reproduces — `viscous_p_bulk_transport_out_of_domain` at C4 — is CORRECT,
DELIBERATE fail-closed behaviour: viscous Poiseuille P_bulk is invalid in the
transitional Knudsen band, so pyrolysis extraction refuses rather than
extrapolate (Stage 0 bakeout is the compute-and-mark regime; see
simulator/evaporation.py viscous_p_bulk_out_of_domain_diagnostic). The test
still FAILS on it because the happy-path journey genuinely does not complete —
but the harness reports it as a lawful model-coverage refusal, never as a
crash or hang. A *true* stall (no state change for STALL_THRESHOLD_MS with no
terminal state and no unanswered decision) remains a defect. So this watchdog:

  * starts a run at max speed; the server must answer within START_ACK_MS,
  * then requires continuous forward progress (no no-progress window longer
    than STALL_THRESHOLD_MS) for WATCHDOG_WINDOW_MS — long enough to reach the
    C4 campaign where the terminal out_of_domain refusal reproduces,
  * treats 'refused'/'error' terminal states as findings, not passes,
  * captures the ENTIRE socket.io stream; if the run stalls, the verbatim
    tail of that stream (plus any event whose payload mentions
    out_of_domain, case-insensitive) is the deliverable,
  * cancels the run afterwards so the suite stays re-runnable.

No fixed sleeps: progress is detected by in-page predicates with explicit
timeouts; a predicate timeout IS the stall signal.
"""

from __future__ import annotations

import json
import time

import pytest

from .playwright_support import PLAYWRIGHT_SYNC_API

expect = PLAYWRIGHT_SYNC_API.expect if PLAYWRIGHT_SYNC_API is not None else None

pytestmark = [
    pytest.mark.browser_e2e,
    pytest.mark.serial,
    pytest.mark.xdist_group("e2e-browser"),
]

if PLAYWRIGHT_SYNC_API is not None:
    from .browser_harness import (
        BASE_URL,
        PAGE_LOAD_MS,
        RUN_COMPLETE_MS,
        START_ACK_MS,
        STALL_THRESHOLD_MS,
        STATUS_CHANGE_MS,
        WATCHDOG_WINDOW_MS,
        PlaywrightTimeoutError,
        cancel_run_quietly,
        click_start,
        select_feedstock,
        set_max_speed,
        wait_for_run_state,
        wait_for_socket_event,
        wait_for_start_enabled,
    )


def _truncate_event(event: dict, limit: int = 2000) -> dict:
    out = dict(event)
    blob = json.dumps(out.get("data"), default=str)
    out["data_bytes"] = len(blob)
    if len(blob) > limit:
        out["data"] = blob[:limit] + f"...[TRUNCATED, {len(blob)} bytes total — full stream in the evidence JSON]"
    return out


def _stall_report(evidence, last_hour: float) -> str:
    tail = [_truncate_event(e) for e in evidence.socket_events[-25:]]
    domain_hits = [
        _truncate_event(e, limit=4000) for e in evidence.socket_events_matching(r"out_of_domain")[-10:]
    ]
    sizes = [(e.get("event"), e.get("data_bytes")) for e in tail]
    parts = [
        f"RUN STALLED: no progress past Hour: {last_hour} for "
        f"{STALL_THRESHOLD_MS // 1000}s with no terminal state and no unanswered decision modal.",
        "",
        f"--- last {len(tail)} socket.io events (data truncated; sizes shown) ---",
        json.dumps(tail, indent=2, default=str),
        "",
        f"--- payload byte sizes in tail: {sizes} ---",
        "",
        f"--- events mentioning out_of_domain ({len(domain_hits)}) ---",
        json.dumps(domain_hits, indent=2, default=str) if domain_hits else "(none)",
    ]
    return "\n".join(parts)


@pytest.mark.xfail(
    strict=True,
    reason=(
        "viscous_p_bulk_transport_out_of_domain is a known product gap pending "
        "low-pressure transport support"
    ),
)
@pytest.mark.timeout(600)
def test_run_does_not_stall(page, evidence):
    page.goto(f"{BASE_URL}/", wait_until="domcontentloaded", timeout=PAGE_LOAD_MS)
    select_feedstock(page)
    set_max_speed(page)
    wait_for_start_enabled(page)
    click_start(page)
    expect(page.locator("#status-text")).not_to_have_text("Ready", timeout=STATUS_CHANGE_MS)

    # First the server must ANSWER the start at all (the known start-wedge
    # defect lives here: handle_start spends tens of seconds re-parsing
    # vapor_pressures.yaml before emitting anything).
    try:
        ack = wait_for_socket_event(
            page, "simulation_status", timeout_ms=START_ACK_MS,
            statuses=["started", "refused", "error"],
        )
    except PlaywrightTimeoutError:
        evidence.harvest_socket_log(phase="no-start-ack")
        cancel_run_quietly(page, evidence)
        pytest.fail(
            f"server sent NO simulation_status answer within {START_ACK_MS // 1000}s "
            "of start_simulation — the start-wedge defect.\n\n"
            + _stall_report(evidence, 0.0)
        )
    if ack.get("status") != "started":
        evidence.harvest_socket_log(phase="start-rejected")
        pytest.fail(
            f"run was {ack.get('status')!r} at start: {json.dumps(ack, default=str)[:600]}"
        )
    evidence.note(f"server answered 'started' with backend={ack.get('backend_active')}")

    last_hour = 0.0
    started_monotonic = time.monotonic()
    verdict = "ADVANCED"
    detail = ""
    try:
        while time.monotonic() - started_monotonic < WATCHDOG_WINDOW_MS / 1000.0:
            remaining_ms = int(
                WATCHDOG_WINDOW_MS - (time.monotonic() - started_monotonic) * 1000
            )
            try:
                verdict, detail = wait_for_run_state(
                    page,
                    last_hour=last_hour,
                    timeout_ms=min(STALL_THRESHOLD_MS, max(remaining_ms, 1_000)),
                )
            except PlaywrightTimeoutError:
                evidence.harvest_socket_log(phase="stall")
                cancel_run_quietly(page, evidence)
                pytest.fail(_stall_report(evidence, last_hour))
            if verdict == "ADVANCED":
                last_hour = float(detail.split("::", 1)[0])
                evidence.harvest_socket_log(phase=f"hour-{last_hour:g}")
                continue
            break
    finally:
        evidence.harvest_socket_log(phase="watchdog-end")

    domain_hits = evidence.socket_events_matching(r"out_of_domain")
    if domain_hits:
        evidence.note(
            f"out_of_domain appeared in {len(domain_hits)} socket event(s): "
            + json.dumps(domain_hits[-3:], default=str)[:1500]
        )

    if verdict in ("REFUSED", "ERROR"):
        cancel_run_quietly(page, evidence)
        refused_payloads = [
            _truncate_event(e, limit=6000)
            for e in evidence.socket_events_matching(r'"status":\s*"(refused|error)"')[-5:]
        ]
        pytest.fail(
            f"run terminated as {verdict} at Hour: {last_hour}: {detail}\n"
            "(a *_out_of_domain refusal here is the deliberate fail-closed "
            "model-coverage gate — lawful, but the happy path does not complete)\n\n"
            f"--- verbatim terminal payloads (truncated; full stream in the evidence JSON) ---\n"
            + json.dumps(refused_payloads, indent=2, default=str)
            + f"\n--- out_of_domain events: {len(domain_hits)} ---"
        )

    if verdict == "COMPLETE":
        evidence.note(f"run reached Complete inside the watchdog window")
        return

    # Window elapsed with continuous forward progress: healthy.
    cancel_run_quietly(page, evidence)
    evidence.note(f"run advanced continuously to Hour: {last_hour} over the watchdog window")
    assert last_hour > 0, "watchdog window elapsed without a single hour of progress"


@pytest.mark.timeout(300)
def test_default_recipe_refusal_reason_is_viscous_p_bulk_transport_out_of_domain(
    page, evidence
):
    page.goto(f"{BASE_URL}/", wait_until="domcontentloaded", timeout=PAGE_LOAD_MS)
    select_feedstock(page)
    set_max_speed(page)
    wait_for_start_enabled(page)
    click_start(page)

    started = wait_for_socket_event(
        page,
        "simulation_status",
        timeout_ms=START_ACK_MS,
        statuses=["started", "refused", "error"],
    )
    assert started.get("status") == "started", started

    try:
        terminal = wait_for_socket_event(
            page,
            "simulation_status",
            timeout_ms=RUN_COMPLETE_MS,
            statuses=["refused", "error"],
        )
    finally:
        evidence.harvest_socket_log(phase="exact-refusal")
        cancel_run_quietly(page, evidence)

    assert terminal.get("status") == "refused", terminal
    assert terminal.get("reason") == "viscous_p_bulk_transport_out_of_domain", terminal

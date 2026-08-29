"""Operator alternate-branch journey: BRANCH_ONE_TWO = one.

Both existing journeys always click the recommended decision-modal button
(PATH_AB -> A_staged, BRANCH_ONE_TWO -> two). The mandate says the operator
owns the extraction order; this journey clicks Branch One through the real
UI and records the campaign chain, whether the run advances, and what the
product ledger shows.

PATH_AB stays on the recommended A_staged so the run actually reaches the
branch gate. A Path B scouting run (2026-08-29) did enter C2B then C3_K /
C3_NA, but was still inside C3_NA at hour 81 when the 600 s advance-loop
budget expired — BRANCH_ONE_TWO had not been offered yet. That is a longer
campaign, not a UI defect; this journey therefore overrides only the
branch.

Expected chain with C5/MRE default-off (apply_decision in simulator/core.py):

    PATH_AB = A_staged  -> C2A_STAGED -> C3_NA
    BRANCH  = one       -> complete (skip C4; C5 is off)

A different or worse yield than Branch Two is correct behaviour, not a
defect. Taking C4 anyway, stalling, or a crash is a finding.
"""

from __future__ import annotations

import json
import re
import time

import pytest

from .journey_budget import (
    BRANCH_JOURNEY_TIMEOUT_S,
    RUN_COMPLETE_TOTAL_MS,
)
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
        RUN_COMPLETE_MS,
        START_ACK_MS,
        STATUS_CHANGE_MS,
        TICK_ADVANCE_MS,
        PlaywrightTimeoutError,
        cancel_run_quietly,
        click_start,
        select_feedstock,
        set_max_speed,
        wait_for_run_state,
        wait_for_socket_event,
        wait_for_start_enabled,
    )


# Non-recommended operator choice. Existing journeys keep the defaults
# (A_staged / two) via decision_choices=None in conftest. PATH_AB is left
# unspecified so the recommended A_staged button is still clicked.
CHOSEN_BRANCH = "one"
PATH_DECISION = "PATH_AB"
BRANCH_DECISION = "BRANCH_ONE_TWO"


@pytest.fixture()
def decision_choices():
    return {BRANCH_DECISION: CHOSEN_BRANCH}


def _campaign_chain(evidence) -> list[str]:
    chain: list[str] = []
    for event in evidence.socket_events:
        if event.get("dir") != "in" or event.get("event") != "simulation_tick":
            continue
        data = event.get("data") or {}
        if not isinstance(data, dict):
            continue
        campaign = data.get("campaign")
        if isinstance(campaign, str) and campaign and (not chain or chain[-1] != campaign):
            chain.append(campaign)
    return chain


def _operator_decisions(evidence) -> list[dict]:
    """Pair inbound decision_required with the following outbound make_decision."""
    out: list[dict] = []
    for event in evidence.socket_events:
        data = event.get("data") if isinstance(event.get("data"), dict) else {}
        if event.get("dir") == "in" and event.get("event") == "decision_required":
            out.append(
                {
                    "type": data.get("type"),
                    "options": data.get("options"),
                    "recommendation": data.get("recommendation"),
                    "choice": None,
                }
            )
        elif event.get("dir") == "out" and event.get("event") == "make_decision":
            if out and out[-1]["choice"] is None:
                out[-1]["choice"] = data.get("choice")
    return out


def _choice_for(decisions: list[dict], decision_type: str) -> str | None:
    for item in decisions:
        if item.get("type") == decision_type:
            return item.get("choice")
    return None


@pytest.mark.timeout(BRANCH_JOURNEY_TIMEOUT_S)
def test_alternate_branch_journey(page, evidence, artifacts_dir):
    step_results: list[tuple[str, bool, str]] = []

    def record(step: str, ok: bool, detail: str) -> None:
        step_results.append((step, ok, detail))
        evidence.note(f"step {step}: {'OK' if ok else 'FAIL'} — {detail}")

    def fail_now(step: str, detail: str) -> None:
        record(step, False, detail)
        evidence.harvest_socket_log(phase=f"fail@{step}")
        problems = "\n".join(evidence.loud_problems()[:15])
        chain = _campaign_chain(evidence)
        decisions = _operator_decisions(evidence)
        cancel_run_quietly(page, evidence)
        pytest.fail(
            f"JOURNEY DIED AT STEP {step}: {detail}\n\n"
            f"--- steps so far ---\n"
            + "\n".join(f"  {s}: {'OK' if o else 'FAIL'} — {d}" for s, o, d in step_results)
            + f"\n--- campaign chain ---\n  {chain}\n"
            + f"--- operator decisions ---\n  {json.dumps(decisions, default=str)}\n"
            + f"--- captured console/network problems ---\n{problems or '(none)'}"
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
    record(
        "3-start",
        True,
        f"server answered 'started' (backend={ack_payload.get('backend_active')})",
    )

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

    # -- Steps 5-7: run to a terminal, then score branch + ledger ------------
    last_hour = float(detail.split("::", 1)[0])
    loop_started = time.monotonic()
    loop_deadline = loop_started + RUN_COMPLETE_TOTAL_MS / 1000.0
    exhausted_total = False
    stalled = False
    last_wait_ms = RUN_COMPLETE_MS
    try:
        while verdict == "ADVANCED":
            remaining_ms = int((loop_deadline - time.monotonic()) * 1000)
            if remaining_ms <= 0:
                exhausted_total = True
                break
            last_wait_ms = min(RUN_COMPLETE_MS, remaining_ms)
            verdict, detail = wait_for_run_state(
                page,
                last_hour=last_hour,
                timeout_ms=last_wait_ms,
            )
            if verdict == "ADVANCED":
                last_hour = float(detail.split("::", 1)[0])
                evidence.harvest_socket_log(phase=f"hour-{last_hour:g}")
    except PlaywrightTimeoutError:
        stalled = True
        evidence.harvest_socket_log(phase="complete-timeout")
        cancel_run_quietly(page, evidence)

    if exhausted_total:
        evidence.harvest_socket_log(phase="complete-total-exhausted")
        cancel_run_quietly(page, evidence)
    elif not stalled:
        evidence.harvest_socket_log(phase="after-loop")

    chain = _campaign_chain(evidence)
    decisions = _operator_decisions(evidence)
    chain_text = " -> ".join(chain) if chain else "(none)"
    evidence.note(f"campaign chain: {chain_text}")
    evidence.note(f"operator decisions: {json.dumps(decisions, default=str)}")

    path_choice = _choice_for(decisions, PATH_DECISION)
    if path_choice != "A_staged":
        record(
            "5-path-ab",
            False,
            f"PATH_AB choice was {path_choice!r}, expected recommended 'A_staged' "
            f"(this journey only overrides BRANCH_ONE_TWO). decisions={decisions!r}",
        )
    elif "C2A_STAGED" not in chain:
        record(
            "5-path-ab",
            False,
            f"PATH_AB=A_staged but campaign chain never entered C2A_STAGED: {chain_text}",
        )
    else:
        record(
            "5-path-ab",
            True,
            f"PATH_AB answered 'A_staged'; campaign chain entered C2A_STAGED",
        )

    branch_choice = _choice_for(decisions, BRANCH_DECISION)
    if branch_choice is None:
        record(
            "6-branch-one",
            False,
            f"BRANCH_ONE_TWO never offered at Hour: {last_hour} "
            f"(verdict={verdict!r}, stalled={stalled}, exhausted={exhausted_total}). "
            f"chain={chain_text}; decisions={decisions!r}; detail={detail}",
        )
    elif branch_choice != CHOSEN_BRANCH:
        record(
            "6-branch-one",
            False,
            f"BRANCH_ONE_TWO choice was {branch_choice!r}, expected {CHOSEN_BRANCH!r} "
            f"(recommendation is two). decisions={decisions!r}",
        )
    elif "C3_NA" not in chain:
        record(
            "6-branch-one",
            False,
            f"BRANCH_ONE_TWO={CHOSEN_BRANCH} but C3_NA never ran (that gate is where "
            f"the branch is offered). chain={chain_text}",
        )
    elif "C4" in chain:
        record(
            "6-branch-one",
            False,
            f"BRANCH_ONE_TWO={CHOSEN_BRANCH} should skip C4 when C5 is off, "
            f"but C4 ran. chain={chain_text}",
        )
    else:
        record(
            "6-branch-one",
            True,
            f"BRANCH_ONE_TWO answered {CHOSEN_BRANCH!r} (not two); C4 absent from chain",
        )

    status_now = page.locator("#status-text").inner_text().strip()
    campaign_now = page.locator("#status-campaign").inner_text().strip()
    step7_ok = False
    if stalled:
        record(
            "7-results",
            False,
            f"run stalled at Hour: {last_hour} — no further hour within "
            f"{last_wait_ms // 1000}s (loop elapsed "
            f"{time.monotonic() - loop_started:.0f}s); "
            f"status-text={status_now!r}; campaign={campaign_now!r}; chain={chain_text}",
        )
    elif exhausted_total:
        record(
            "7-results",
            False,
            f"run kept advancing but never reached a terminal: Hour: {last_hour} "
            f"in {RUN_COMPLETE_TOTAL_MS // 1000}s; "
            f"status-text={status_now!r}; campaign={campaign_now!r}; chain={chain_text}",
        )
    elif verdict != "COMPLETE":
        cancel_run_quietly(page, evidence)
        record(
            "7-results",
            False,
            f"run terminated as '{verdict}' instead of completing at Hour: {last_hour}: "
            f"{detail}; status-text={status_now!r}; status-campaign={campaign_now!r}; "
            f"chain={chain_text}",
        )
    else:
        record(
            "7-complete",
            True,
            f"#status-text == 'Complete' at Hour: {last_hour}; campaign={campaign_now}",
        )
        ledger = page.locator("#product-ledger-content")
        expect(ledger).to_be_visible(timeout=PAGE_LOAD_MS)
        ledger_text = ledger.inner_text()
        if ledger_text.strip() in ("", "n/a"):
            record("7-results", False, "run completed but #product-ledger-content is still the empty 'n/a' shell")
        elif not re.search(r"\d", ledger_text):
            record("7-results", False, f"product ledger has no numbers: {ledger_text[:200]!r}")
        else:
            complete_payloads = [
                e["data"] for e in evidence.socket_events if e.get("event") == "simulation_complete"
            ]
            payload_detail = ""
            step7_ok = True
            if complete_payloads:
                payload = complete_payloads[-1] or {}
                mass_in = payload.get("mass_in_kg")
                if mass_in is not None and not (
                    isinstance(mass_in, (int, float)) and mass_in > 0
                ):
                    record(
                        "7-results",
                        False,
                        f"simulation_complete payload mass_in_kg is not positive: {mass_in!r}",
                    )
                    step7_ok = False
                payload_detail = (
                    f"; simulation_complete mass_in_kg={mass_in} "
                    f"oxygen_kg={payload.get('oxygen_kg', payload.get('oxygen_stored_kg'))}"
                )
            if step7_ok:
                record(
                    "7-results",
                    True,
                    f"product ledger populated ({len(ledger_text)} chars); "
                    f"chain={chain_text}{payload_detail}",
                )

    shot_name = "branch-journey-complete.png" if step7_ok else "branch-journey-terminal-FAILED.png"
    try:
        page.screenshot(path=str(artifacts_dir / shot_name), full_page=True)
    except Exception:
        pass

    evidence.harvest_socket_log(phase="journey-end")
    summary = "\n".join(f"  {s}: {'OK' if o else 'FAIL'} — {d}" for s, o, d in step_results)
    print(f"\n[branch-journey]\n{summary}")
    print(f"[branch-journey] campaign chain: {chain_text}")
    print(f"[branch-journey] decisions: {json.dumps(decisions, default=str)}")
    failed_steps = [(s, d) for s, o, d in step_results if not o]
    if failed_steps:
        problems = "\n".join(evidence.loud_problems()[:15])
        pytest.fail(
            "JOURNEY FAILED at "
            + ", ".join(s for s, _ in failed_steps)
            + "\n\n--- all steps ---\n"
            + summary
            + f"\n--- campaign chain ---\n  {chain_text}\n"
            + f"--- operator decisions ---\n  {json.dumps(decisions, default=str)}\n"
            + f"--- captured console/network problems ---\n{problems or '(none)'}"
        )

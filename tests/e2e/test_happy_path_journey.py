"""The primary happy-path journey, driven through a real browser over HTTP.

One test, seven ordered steps — exactly what an operator does:

    1. Land on /            — real rendered content, not just HTTP 200.
    2. Select a feedstock   — the feedstock card loads back from the server.
    3. Click Start          — start_simulation goes out over socket.io.
    4. Run ADVANCES         — #status-hour moves past Hour: 0.
    5. Results POPULATE     — run completes; product ledger shows real numbers.
    6. /optimizer           — leaderboard renders with rows (bounded: the page
                              currently takes ~7 min server-side; that bound
                              blowing up is a FAILURE, not a hang).
    7. /thermal-train       — populated report, not the no_data shell.

Whatever step it dies at is the finding. Steps fail loudly with captured
console/network/socket evidence and a screenshot artifact.
"""

from __future__ import annotations

import json
import re

import pytest
from playwright.sync_api import expect

pytestmark = [
    pytest.mark.serial,
    pytest.mark.xdist_group("e2e-browser"),
]

from .browser_harness import (
    BASE_URL,
    OPTIMIZER_BOUND_MS,
    PAGE_LOAD_MS,
    RUN_COMPLETE_MS,
    START_ACK_MS,
    STATUS_CHANGE_MS,
    THERMAL_TRAIN_MS,
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


@pytest.mark.timeout(900)
def test_happy_path_journey(page, evidence, artifacts_dir):
    step_results: list[tuple[str, bool, str]] = []

    def record(step: str, ok: bool, detail: str) -> None:
        step_results.append((step, ok, detail))
        evidence.note(f"step {step}: {'OK' if ok else 'FAIL'} — {detail}")

    def fail_now(step: str, detail: str) -> None:
        record(step, False, detail)
        evidence.harvest_socket_log(phase=f"fail@{step}")
        problems = "\n".join(evidence.loud_problems()[:15])
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
    option_values = page.locator("#feedstock-select option").evaluate_all(
        "(opts) => opts.map((o) => o.value).filter((v) => v)"
    )
    if len(option_values) < 1:
        fail_now("1-land", "landing page rendered but #feedstock-select has zero feedstock options")
    record("1-land", True, f"/ rendered: Configuration panel + {len(option_values)} feedstocks")

    # -- Step 2: select a feedstock ------------------------------------------
    try:
        chosen = select_feedstock(page)
    except (AssertionError, PlaywrightTimeoutError) as exc:
        fail_now("2-feedstock", f"feedstock selection failed: {exc}")
    record("2-feedstock", True, f"selected '{chosen}', feedstock card loaded")

    # -- Step 3: click Start ---------------------------------------------------
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
    outbound = [e for e in evidence.socket_events if e.get("dir") == "out" and e.get("event") == "start_simulation"]
    if not outbound:
        fail_now("3-start", "no outbound start_simulation socket event captured after clicking Start")
    # Bounded wait for the server's answer. NOTE: a healthy server answers
    # simulation_status 'started' in well under a second; anything near this
    # bound is itself a defect worth reporting.
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
            f"{START_ACK_MS // 1000}s — from the operator's seat, Start looks dead "
            "(measured root cause: handle_start re-parses the 1.2MB vapor_pressures.yaml "
            "~8x with pure-Python yaml.safe_load inside Stage-0 foulant diagnostics before "
            "emitting anything; ~41s wall-clock unloaded, worse under load)",
        )
    ack_status = ack_payload.get("status")
    if ack_status != "started":
        fail_now(
            "3-start",
            f"server answered simulation_status '{ack_status}' instead of 'started': "
            f"{json.dumps(ack_payload, default=str)[:600]}",
        )
    record("3-start", True, f"server answered 'started' (backend={ack_payload.get('backend_active')}, status={ack_payload.get('backend_status')})")

    # -- Step 4: the run ACTUALLY ADVANCES -------------------------------------
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

    # -- Step 5: results POPULATE ---------------------------------------------
    # Non-aborting: steps 6+7 are independent pages and still run, then the
    # test fails loudly listing every failed step.
    last_hour = float(detail.split("::", 1)[0])
    step5_ok = False
    try:
        while verdict == "ADVANCED":
            verdict, detail = wait_for_run_state(page, last_hour=last_hour, timeout_ms=RUN_COMPLETE_MS)
            if verdict == "ADVANCED":
                last_hour = float(detail.split("::", 1)[0])
                evidence.harvest_socket_log(phase=f"hour-{last_hour:g}")
    except PlaywrightTimeoutError:
        evidence.harvest_socket_log(phase="complete-timeout")
        cancel_run_quietly(page, evidence)
        record(
            "5-results",
            False,
            f"run advanced to Hour: {last_hour} but did not reach 'Complete' within "
            f"{RUN_COMPLETE_MS // 1000}s at max speed",
        )
    else:
        if verdict != "COMPLETE":
            evidence.harvest_socket_log(phase="step5-terminal")
            cancel_run_quietly(page, evidence)
            record("5-results", False, f"run terminated as '{verdict}' instead of completing: {detail}")
        else:
            step5_ok = True
    if step5_ok:
        record("5-complete", True, f"#status-text == 'Complete' at Hour: {last_hour}")
        evidence.harvest_socket_log(phase="after-complete")
        ledger = page.locator("#product-ledger-content")
        expect(ledger).to_be_visible(timeout=PAGE_LOAD_MS)
        ledger_text = ledger.inner_text()
        if ledger_text.strip() in ("", "n/a"):
            record("5-results", False, "run completed but #product-ledger-content is still the empty 'n/a' shell")
            step5_ok = False
        elif not re.search(r"\d", ledger_text):
            record("5-results", False, f"product ledger has no numbers: {ledger_text[:200]!r}")
            step5_ok = False
        else:
            complete_payloads = [e["data"] for e in evidence.socket_events if e.get("event") == "simulation_complete"]
            payload_detail = ""
            if complete_payloads:
                payload = complete_payloads[-1] or {}
                mass_in = payload.get("mass_in_kg")
                if mass_in is not None and not (isinstance(mass_in, (int, float)) and mass_in > 0):
                    record("5-results", False, f"simulation_complete payload mass_in_kg is not positive: {mass_in!r}")
                    step5_ok = False
                payload_detail = f"; simulation_complete mass_in_kg={mass_in} oxygen_kg={payload.get('oxygen_kg', payload.get('oxygen_stored_kg'))}"
            if step5_ok:
                record(
                    "5-results",
                    True,
                    f"product ledger populated ({len(ledger_text)} chars, contains kg figures){payload_detail}",
                )
    if not step5_ok:
        try:
            page.screenshot(
                path=str(artifacts_dir / "journey-step5-FAILED.png"), full_page=True
            )
        except Exception:
            pass
    else:
        try:
            page.screenshot(
                path=str(artifacts_dir / "journey-step5-complete.png"), full_page=True
            )
        except Exception:
            pass

    # -- Steps 6+7: independent pages; collect failures, then fail loudly -----
    # Step 6: /optimizer leaderboard (BOUNDED — the ~7 min render is a known
    # live defect; blowing the bound fails the step instead of hanging).
    try:
        page.goto(f"{BASE_URL}/optimizer", wait_until="domcontentloaded", timeout=OPTIMIZER_BOUND_MS)
        expect(page.locator("h2", has_text="Optimizer Results")).to_be_visible(timeout=10_000)
        rows = page.locator("#optimizer-table table.composition-table tbody tr")
        row_count = rows.count()
        if row_count >= 1:
            first_cells = rows.first.locator("td").all_inner_texts()
            record("6-optimizer", True, f"leaderboard rendered with {row_count} rows; winner row: {first_cells[:3]}")
        elif page.locator("#optimizer-table .empty-hint").count() > 0:
            record(
                "6-optimizer",
                False,
                "leaderboard rendered EMPTY: "
                + page.locator("#optimizer-table .empty-hint").inner_text().strip()[:200],
            )
        else:
            record("6-optimizer", False, "no leaderboard rows and no empty-hint — unrecognised shell")
    except PlaywrightTimeoutError:
        record(
            "6-optimizer",
            False,
            f"GET /optimizer did not render within the {OPTIMIZER_BOUND_MS // 1000}s bound "
            "(known defect: ~7 min server-side render, fix in flight)",
        )

    # Step 7: /thermal-train populated.
    try:
        page.goto(f"{BASE_URL}/thermal-train", wait_until="domcontentloaded", timeout=THERMAL_TRAIN_MS)
        report = page.locator("#thermal-train-report")
        expect(report).to_be_visible(timeout=THERMAL_TRAIN_MS)
        state = report.get_attribute("data-state") or "(missing)"
        if state == "no_data":
            record(
                "7-thermal-train",
                False,
                "thermal train shows the no_data shell after a completed run in this session: "
                + report.inner_text()[:200].replace("\n", " "),
            )
        else:
            tt_rows = report.locator("table.composition-table tbody tr").count()
            if tt_rows < 1 or not re.search(r"\d", report.inner_text()):
                record("7-thermal-train", False, f"report state={state} but no data rows/numbers rendered")
            else:
                record("7-thermal-train", True, f"report state={state}, {tt_rows} data rows with numbers")
    except PlaywrightTimeoutError as exc:
        record("7-thermal-train", False, f"/thermal-train failed to render: {exc}")

    evidence.harvest_socket_log(phase="journey-end")

    failed_steps = [(s, d) for s, o, d in step_results if not o]
    summary = "\n".join(f"  {s}: {'OK' if o else 'FAIL'} — {d}" for s, o, d in step_results)
    print(f"\n[journey]\n{summary}")
    if failed_steps:
        pytest.fail(
            "JOURNEY FAILED at "
            + ", ".join(s for s, _ in failed_steps)
            + "\n\n--- all steps ---\n"
            + summary
        )

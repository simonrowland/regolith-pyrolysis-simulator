"""Pytest fixtures for the Playwright e2e harness.

Plain pytest + playwright.sync_api (no pytest-playwright): install the optional
`e2e` extra plus its Chromium browser before running this harness.

Run ONLY these tests, serially, against the already-running dev server:

    .venv/bin/python -m pytest tests/e2e/test_happy_path_journey.py \
        tests/e2e/test_no_feedstock_alert.py \
        tests/e2e/test_run_stall_out_of_domain.py \
        tests/e2e/test_optimizer_bounded.py -n0 -v

-n0 overrides the repo-wide `-n auto` (xdist): browser tests against one
shared dev server must run serially.
"""

from __future__ import annotations

import os
import re
import sys
import urllib.error
import urllib.request

import pytest

from .playwright_support import load_playwright_sync_api

_playwright_sync_api = load_playwright_sync_api()
if _playwright_sync_api is not None:
    sync_playwright = _playwright_sync_api.sync_playwright
    from .browser_harness import (
        BASE_URL,
        DECISION_AUTO_ANSWER_JS,
        HEADED,
        SOCKET_TAP_JS,
        EvidenceRecorder,
        new_artifacts_dir,
        write_evidence_json,
    )


def _sanitise(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", name)


@pytest.hookimpl(wrapper=True, tryfirst=True)
def pytest_runtest_makereport(item, call):
    report = yield
    setattr(item, "rep_" + report.when, report)
    return report


@pytest.fixture(scope="session")
def artifacts_dir():
    return new_artifacts_dir()


@pytest.fixture(scope="session")
def live_server():
    """Skip if the controller-owned dev server is not reachable.

    This harness never starts or kills a server. Requested only by the
    browser fixtures, so in-process tests under tests/e2e/headspace_po2/
    are unaffected.
    """
    url = f"{BASE_URL}/"
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            status = getattr(resp, "status", 200)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        message = (
            f"e2e harness requires the already-running dev server at {url} "
            f"(do not start another; launch config is 'simulator'): {exc}"
        )
        if os.environ.get("REGOLITH_E2E_REQUIRE_SERVER") == "1":
            pytest.fail(message)
        pytest.skip(
            message + "; set REGOLITH_E2E_REQUIRE_SERVER=1 to require it",
            allow_module_level=True,
        )
    if status != 200:
        pytest.fail(f"GET {url} returned HTTP {status}; landing page is not serving")
    return BASE_URL


@pytest.fixture(scope="session")
def playwright_instance(live_server):
    with sync_playwright() as pw:
        yield pw


@pytest.fixture(scope="session")
def browser(playwright_instance):
    browser = playwright_instance.chromium.launch(headless=not HEADED)
    yield browser
    browser.close()


@pytest.fixture()
def context(browser):
    ctx = browser.new_context(viewport={"width": 1600, "height": 1000})
    ctx.add_init_script(SOCKET_TAP_JS)
    ctx.add_init_script(DECISION_AUTO_ANSWER_JS)
    yield ctx
    ctx.close()


@pytest.fixture()
def page(context):
    return context.new_page()


@pytest.fixture()
def evidence(page, request, artifacts_dir):
    recorder = EvidenceRecorder(page)
    yield recorder
    test_name = _sanitise(request.node.name)
    failed = getattr(request.node, "rep_call", None) is not None and request.node.rep_call.failed
    # Harvest whatever socket state is still readable (best effort — the page
    # may have navigated or crashed).
    recorder.harvest_socket_log(phase="teardown")
    payload = recorder.as_dict()
    payload["test"] = request.node.nodeid
    payload["outcome"] = "failed" if failed else "passed"
    out = write_evidence_json(artifacts_dir / f"{test_name}.evidence.json", payload)
    if failed:
        try:
            page.screenshot(
                path=str(artifacts_dir / f"{test_name}.png"), full_page=True
            )
        except Exception:
            pass
    print(
        f"\n[e2e evidence] {out}\n{recorder.summary()}",
        file=sys.stderr,
    )
    problems = recorder.loud_problems()
    if problems:
        print("[e2e evidence] captured problems:", file=sys.stderr)
        for problem in problems[:20]:
            print(f"  - {problem}", file=sys.stderr)
        if not failed:
            pytest.fail(
                "silent pass hid captured browser problems — the harness "
                "must not go green over console/page/network errors:\n  - "
                + "\n  - ".join(problems[:20])
            )

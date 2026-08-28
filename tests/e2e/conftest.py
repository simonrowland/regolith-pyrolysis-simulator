"""Pytest fixtures for the Playwright e2e harness.

Plain pytest + playwright.sync_api (no pytest-playwright): the repo is
pytest-native and .venv already ships the `playwright` package.

Run ONLY these tests, serially, against the already-running dev server:

    .venv/bin/python -m pytest tests/e2e/test_happy_path_journey.py \
        tests/e2e/test_no_feedstock_alert.py \
        tests/e2e/test_run_stall_out_of_domain.py \
        tests/e2e/test_optimizer_bounded.py -n0 -v

-n0 overrides the repo-wide `-n auto` (xdist): browser tests against one
shared dev server must run serially.
"""

from __future__ import annotations

import json
import re
import sys

import pytest
from playwright.sync_api import sync_playwright

from .browser_harness import (
    BASE_URL,
    DECISION_AUTO_ANSWER_JS,
    HEADED,
    SOCKET_TAP_JS,
    EvidenceRecorder,
    new_artifacts_dir,
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
def playwright_instance():
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
    out = artifacts_dir / f"{test_name}.evidence.json"
    payload = recorder.as_dict()
    payload["test"] = request.node.nodeid
    payload["outcome"] = "failed" if failed else "passed"
    out.write_text(json.dumps(payload, indent=2, default=str))
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

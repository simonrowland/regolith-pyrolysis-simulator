"""Top-level pytest collection policy."""

from pathlib import Path

import pytest

from tests.e2e.playwright_support import load_playwright_sync_api


_PLAYWRIGHT_SYNC_API = load_playwright_sync_api()
_E2E_DIR = Path(__file__).parent / "e2e"
_BROWSER_E2E_FILES = {
    _E2E_DIR / "test_happy_path_journey.py",
    _E2E_DIR / "test_no_feedstock_alert.py",
    _E2E_DIR / "test_optimizer_bounded.py",
    _E2E_DIR / "test_run_stall_out_of_domain.py",
}


def _browser_e2e_requested(config) -> bool:
    requested_paths = {
        Path(argument.split("::", 1)[0]).resolve()
        for argument in config.args
    }
    return bool(
        requested_paths & _BROWSER_E2E_FILES
        or (_E2E_DIR in requested_paths and config.option.keyword)
    )


def pytest_sessionfinish(session, exitstatus):
    if (
        _PLAYWRIGHT_SYNC_API is None
        and exitstatus == pytest.ExitCode.NO_TESTS_COLLECTED
        and _browser_e2e_requested(session.config)
    ):
        session.exitstatus = pytest.ExitCode.OK

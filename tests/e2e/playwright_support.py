"""Playwright import policy shared by e2e collection entry points."""

from importlib import import_module
from importlib.metadata import PackageNotFoundError, version

import pytest

PLAYWRIGHT_MISSING_REASON = (
    "Playwright distribution is not installed; skipping browser e2e tests"
)


def load_playwright_sync_api():
    try:
        return import_module("playwright.sync_api")
    except ModuleNotFoundError as exc:
        if exc.name not in {"playwright", "playwright.sync_api"}:
            raise
        try:
            version("playwright")
        except PackageNotFoundError:
            return None
        raise


PLAYWRIGHT_SYNC_API = load_playwright_sync_api()
PLAYWRIGHT_SKIP_MARK = pytest.mark.skipif(
    PLAYWRIGHT_SYNC_API is None,
    reason=PLAYWRIGHT_MISSING_REASON,
)

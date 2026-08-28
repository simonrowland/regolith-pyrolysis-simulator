"""Playwright import policy shared by e2e collection entry points."""

from importlib import import_module
from importlib.metadata import PackageNotFoundError, version

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


def require_playwright_sync_api():
    sync_api = load_playwright_sync_api()
    if sync_api is None:
        import pytest

        pytest.skip(
            PLAYWRIGHT_MISSING_REASON,
            allow_module_level=True,
        )
    return sync_api

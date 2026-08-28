"""Top-level pytest collection policy."""

from importlib import import_module
from importlib.metadata import PackageNotFoundError, version


def _playwright_available() -> bool:
    try:
        import_module("playwright.sync_api")
    except ModuleNotFoundError as exc:
        if exc.name not in {"playwright", "playwright.sync_api"}:
            raise
        try:
            version("playwright")
        except PackageNotFoundError:
            return False
        raise
    return True


collect_ignore_glob = [] if _playwright_available() else ["e2e"]

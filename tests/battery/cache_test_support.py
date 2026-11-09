"""Shared engine availability assertions for cache integration tests."""

from __future__ import annotations

import pytest


def missing_engine_reason(handle: object, engine: str) -> str | None:
    """Return a missing-dependency reason; fail on initialization errors."""
    if getattr(handle, "available", False) and getattr(handle, "backend", None) is not None:
        return None

    reason = str(getattr(handle, "unavailable_reason", None) or "no reason supplied")
    normalized = reason.lower()
    missing_markers = (
        "no module named ",
        "modulenotfounderror",
        "module not found",
        "binary not found",
        "executable not found",
        "no such file or directory",
        "runtime package missing",
        "package missing",
    )
    message = f"{engine} unavailable: {reason}"
    if any(marker in normalized for marker in missing_markers):
        return message
    pytest.fail(f"{engine} initialization failed: {reason}", pytrace=False)


def skip_if_engine_missing(handle: object, engine: str) -> None:
    """Skip only when an engine dependency or executable is genuinely absent."""
    reason = missing_engine_reason(handle, engine)
    if reason is not None:
        pytest.skip(reason)

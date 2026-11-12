"""Shared engine availability assertions for cache integration tests."""

from __future__ import annotations

from typing import Any

import pytest

from simulator.battery.enums import Engine


CACHE_TEST_ENGINES = (
    Engine.OPENIMCC,
    Engine.INTERNAL_ANALYTICAL,
    Engine.VAPOROCK,
    Engine.MAGEMIN,
    Engine.ALPHAMELTS,
    Engine.THERMOENGINE,
)


def open_available_engine(engine: Engine, *, nodeid: str) -> Any:
    """Open an engine and skip only when its import or binary is absent."""
    from simulator.diagnostic_helpers.binary_pot_battery import (
        close_battery_engine_handle,
        open_battery_engine,
    )

    handle = open_battery_engine(engine.value)
    if not handle.available or handle.backend is None:
        close_battery_engine_handle(handle)
        skip_if_engine_missing(handle, engine.value, nodeid=nodeid)
    return handle

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


def skip_if_engine_missing(handle: object, engine: str, *, nodeid: str) -> None:
    """Skip only when an engine dependency or executable is genuinely absent."""
    reason = missing_engine_reason(handle, engine)
    if reason is not None:
        pytest.skip(f"{nodeid}: {reason}")

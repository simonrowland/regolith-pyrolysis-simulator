"""Shared engine availability assertions for cache integration tests."""

from __future__ import annotations

from pathlib import Path
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


def assert_real_adapter_cache_paths(
    handle: Any,
    pot: Any,
    *,
    tmp_path: Path,
    cache_stem: str,
    common: dict[str, Any],
) -> None:
    """Compare direct/spawned off, cold, warm, and cross-path cache calls."""
    from dataclasses import replace

    from simulator.diagnostic_helpers.binary_pot_battery import (
        _engine_cache_request_vector,
        equilibrate_cell,
    )
    from simulator.engine_cache import EngineCache

    def stable(cell: Any) -> dict[str, Any]:
        return replace(
            cell, wall_s=0.0, cpu_s=0.0, hostname="fixture"
        ).to_cache_payload()

    def describe(cell: Any) -> str:
        return (
            f"{cell.status}/{cell.engine_status}/{cell.refusal_reason}/"
            f"{cell.engine_reason} (exit={cell.exit_code}, signal={cell.exit_signal}, "
            f"disposition={cell.backend_failure_disposition})"
        )

    direct_off = equilibrate_cell(handle, pot, isolated=False, **common)
    isolated_off = equilibrate_cell(handle, pot, isolated=True, **common)
    direct_cache = EngineCache(tmp_path / f"{cache_stem}-direct.sqlite")
    isolated_cache = EngineCache(tmp_path / f"{cache_stem}-isolated.sqlite")

    def current_vector() -> dict[str, Any] | None:
        return _engine_cache_request_vector(
            handle,
            pot,
            temperature_K=float(common["temperature_K"]),
            po2=common["po2"],
            physical_pressure_bar=common.get("physical_pressure_bar"),
            native_activity_formula=common.get("native_activity_formula"),
        )

    direct_vector_before_cold = current_vector()
    direct_cold = equilibrate_cell(
        handle,
        pot,
        isolated=False,
        engine_cache=direct_cache,
        **common,
    )
    direct_vector_after_cold = current_vector()
    assert direct_vector_after_cold == direct_vector_before_cold, (
        f"{handle.name} changed its cache request vector during a direct "
        f"engine call: before={direct_vector_before_cold!r}, "
        f"after={direct_vector_after_cold!r}, "
        f"outcome={direct_cold.status}/{direct_cold.engine_status}/"
        f"{direct_cold.engine_reason}"
    )
    direct_calls_after_cold = dict(
        direct_cache.counters[handle.name]["calls_by_kind"]
    )
    direct_warm = equilibrate_cell(
        handle,
        pot,
        isolated=False,
        engine_cache=direct_cache,
        **common,
    )
    direct_cache_isolated_hit = equilibrate_cell(
        handle,
        pot,
        isolated=True,
        engine_cache=direct_cache,
        **common,
    )
    direct_counts_after_warm = direct_cache.counters[handle.name]
    isolated_cold = equilibrate_cell(
        handle,
        pot,
        isolated=True,
        engine_cache=isolated_cache,
        **common,
    )
    isolated_calls_after_cold = dict(
        isolated_cache.counters[handle.name]["calls_by_kind"]
    )
    isolated_warm = equilibrate_cell(
        handle,
        pot,
        isolated=True,
        engine_cache=isolated_cache,
        **common,
    )
    isolated_cache_direct_hit = equilibrate_cell(
        handle,
        pot,
        isolated=False,
        engine_cache=isolated_cache,
        **common,
    )
    isolated_counts_after_warm = isolated_cache.counters[handle.name]

    expected = stable(direct_off)
    outcomes = {
        "direct off": direct_off,
        "isolated off": isolated_off,
        "direct cold": direct_cold,
        "direct warm": direct_warm,
        "direct-cache isolated": direct_cache_isolated_hit,
        "isolated cold": isolated_cold,
        "isolated warm": isolated_warm,
        "isolated-cache direct": isolated_cache_direct_hit,
    }
    mismatches = {
        name: describe(cell)
        for name, cell in outcomes.items()
        if stable(cell) != expected
    }
    assert not mismatches, f"{handle.name} real path outcomes differ: {mismatches!r}"
    assert dict(direct_counts_after_warm["calls_by_kind"]) == direct_calls_after_cold, (
        f"{handle.name} called the engine again on a warm direct request: "
        f"cold={describe(direct_cold)}; warm={describe(direct_warm)}; "
        f"counters={direct_counts_after_warm!r}"
    )
    assert dict(
        isolated_counts_after_warm["calls_by_kind"]
    ) == isolated_calls_after_cold, (
        f"{handle.name} called the engine again on a warm isolated request: "
        f"cold={describe(isolated_cold)}; warm={describe(isolated_warm)}; "
        f"counters={isolated_counts_after_warm!r}"
    )
    assert direct_cache.counters[handle.name]["stores"] == 1
    assert direct_cache.counters[handle.name]["hits"] == 2
    assert isolated_cache.counters[handle.name]["stores"] == 1
    assert isolated_cache.counters[handle.name]["hits"] == 2

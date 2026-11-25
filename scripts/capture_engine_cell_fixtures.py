#!/usr/bin/env python3
"""Capture cache-serialized real engine results for battery cell fixtures."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from functools import wraps
import importlib.metadata
import inspect
import json
from pathlib import Path
import socket
import subprocess
import sys
from typing import Any
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from simulator.engine_cache import _without_engine_cache  # noqa: E402

ENGINES = ("thermoengine", "vaporock")
POT_ID = "mgo_sio2_40_60"
TEMPERATURE_K = 1700.0
OUTPUT_NAMES = {
    "thermoengine": "thermoengine-mgo-sio2-40-60-1700K.json",
    "vaporock": "vaporock-mgo-sio2-40-60-1700K.json",
}


class CaptureError(RuntimeError):
    """The requested live engine result could not be captured."""


def _engine_version(handle: Any) -> tuple[str, str]:
    identity_version = str(handle.identity.get("version") or "").strip()
    backend = handle.backend
    get_version = getattr(backend, "get_engine_version", None)
    if callable(get_version):
        version = str(get_version() or "").strip()
        if version and version.lower() != "unavailable":
            return version, "backend.get_engine_version"
    if identity_version:
        return identity_version, "engine identity configuration"

    distributions = {
        "thermoengine": (
            "thermoengine",
            "ThermoEngine",
            "petthermotools",
            "PetThermoTools",
        ),
        "vaporock": ("vaporock", "VapoRock"),
    }[handle.name]
    for distribution in distributions:
        try:
            return (
                importlib.metadata.version(distribution),
                f"distribution:{distribution}",
            )
        except importlib.metadata.PackageNotFoundError:
            continue
    return (
        "unavailable",
        "version not exposed by backend, identity, or package metadata",
    )


def _git_sha() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return result.stdout.strip()


def _capture(handle: Any, pot: Any, po2_request: Any) -> dict[str, Any]:
    from simulator.diagnostic_helpers.binary_pot_battery import (
        DIAGNOSTIC_ARM_BOUNDARY_PRESSURE_BAR,
        equilibrate_cell,
    )
    from simulator.melt_backend.base import EquilibriumResult

    backend = handle.backend
    original_equilibrate = backend.equilibrate
    calls: list[tuple[tuple[Any, ...], dict[str, Any], Any]] = []

    @wraps(original_equilibrate)
    def record_call(*args: Any, **kwargs: Any) -> Any:
        result = original_equilibrate(*args, **kwargs)
        calls.append((args, dict(kwargs), result))
        return result

    with patch.object(backend, "equilibrate", new=record_call):
        # Exercise the same cell-to-backend argument construction as the tests,
        # while ensuring the captured result came from a live uncached solve.
        with _without_engine_cache():
            cell = equilibrate_cell(
                handle,
                pot,
                temperature_K=TEMPERATURE_K,
                po2=po2_request,
                physical_pressure_bar=DIAGNOSTIC_ARM_BOUNDARY_PRESSURE_BAR,
                timeout_s=90.0,
                isolated=False,
            )

    if len(calls) != 1:
        raise CaptureError(
            f"{handle.name}: expected one backend.equilibrate call, saw {len(calls)}"
        )
    if cell.refusal_reason is not None:
        raise CaptureError(
            f"{handle.name}: cell refused the capture request: {cell.refusal_reason}"
        )
    _args, kwargs, result = calls[0]
    parameters = inspect.signature(original_equilibrate).parameters
    expected_kwargs = {
        "temperature_C",
        "composition_kg",
        "composition_mol",
        "pressure_bar",
    }
    if "fO2_log" in parameters:
        expected_kwargs.add("fO2_log")
    for optional_name in ("po2_request", "call_timeout_s"):
        if optional_name in parameters:
            expected_kwargs.add(optional_name)
    from simulator.melt_backend.alphamelts_contract import isothermal_run_kwargs

    expected_kwargs.update(isothermal_run_kwargs(handle.name, parameters))
    missing_kwargs = expected_kwargs.difference(kwargs)
    if missing_kwargs:
        raise CaptureError(
            f"{handle.name}: equilibrate_cell omitted backend kwargs "
            f"{sorted(missing_kwargs)} from the captured call"
        )
    if not isinstance(result, EquilibriumResult):
        raise CaptureError(
            f"{handle.name}: backend returned {type(result).__name__}, "
            "expected EquilibriumResult for the cache codec"
        )

    engine_version, version_source = _engine_version(handle)
    cache_value = result.to_engine_cache_value(engine_id=handle.name)
    # Reject values that the SQLite cache's canonical JSON writer could not store.
    json.dumps(cache_value, allow_nan=False, sort_keys=True)
    return {
        "engine": handle.name,
        "request": {
            "pot_id": POT_ID,
            "temperature_K": TEMPERATURE_K,
            "po2_mode": po2_request.mode,
        },
        "provenance": {
            "engine_version": engine_version,
            "engine_version_source": version_source,
            "engine_identity": {
                "backend_module": type(backend).__module__,
                "backend_class": type(backend).__qualname__,
                "configured_identity": dict(handle.identity),
            },
            "host": socket.gethostname(),
            "git_sha": _git_sha(),
            "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        },
        "backend_result": cache_value,
    }


def _open_handles() -> dict[str, Any]:
    from simulator.diagnostic_helpers.binary_pot_battery import (
        battery_engine_handle_is_live,
        open_battery_engine,
    )

    handles: dict[str, Any] = {}
    try:
        for engine in ENGINES:
            handle = open_battery_engine(engine)
            handles[engine] = handle
            if not handle.available or handle.backend is None:
                raise CaptureError(
                    f"refusing capture: {engine} is unavailable: "
                    f"{handle.unavailable_reason or 'backend did not open'}"
                )
            if not battery_engine_handle_is_live(handle):
                raise CaptureError(
                    f"refusing capture: {engine} opened but is not live: "
                    f"{handle.unavailable_reason or 'backend liveness check failed'}"
                )
    except BaseException:
        from simulator.diagnostic_helpers.binary_pot_battery import (
            close_battery_engine_handles,
        )

        close_battery_engine_handles(handles)
        raise
    return handles


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="directory for the two captured cache payload fixtures",
    )
    args = parser.parse_args(argv)

    from simulator.diagnostic_helpers.binary_pot_battery import (
        PO2_ENGINE_DEFAULT,
        Po2Request,
        close_battery_engine_handles,
        load_binary_pots,
    )

    handles: dict[str, Any] = {}
    try:
        handles = _open_handles()
        pots, _metadata = load_binary_pots()
        pot = next((item for item in pots if item.pot_id == POT_ID), None)
        if pot is None:
            raise CaptureError(f"configured binary pot {POT_ID!r} was not found")
        po2_request = Po2Request(mode=PO2_ENGINE_DEFAULT, po2_bar=None)
        fixtures = {
            engine: _capture(handles[engine], pot, po2_request)
            for engine in ENGINES
        }

        args.output_dir.mkdir(parents=True, exist_ok=True)
        for engine in ENGINES:
            path = args.output_dir / OUTPUT_NAMES[engine]
            path.write_text(
                json.dumps(fixtures[engine], allow_nan=False, indent=2, sort_keys=True)
                + "\n",
                encoding="utf-8",
            )
            print(path)
    except (CaptureError, OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"refusing capture: {exc}", file=sys.stderr)
        return 2
    finally:
        if handles:
            close_battery_engine_handles(handles)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

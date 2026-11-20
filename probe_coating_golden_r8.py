"""Dump the coating golden payload and its resolved MAGEMin availability."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
from pathlib import Path

from simulator.chemistry.kernel.capabilities import ChemistryIntent
from simulator.core import PyrolysisSimulator
from simulator.engine_local_config import (
    config_path,
    configured_magemin_binary_path,
    load_config,
)
from simulator.melt_backend.magemin import MAGEMinBackend
from tests.coating_golden import produce_coating_golden


def _backend_state(
    simulator: PyrolysisSimulator,
    refusal: Exception | None,
) -> dict[str, object]:
    provider = simulator._chem_registry.fallback_for(
        ChemistryIntent.GATE_LIQUID_FRACTION,
    )
    backend = getattr(provider, "_backend", None)
    is_available = getattr(backend, "is_available", None)
    return {
        "provider": getattr(provider, "name", None),
        "backend_initialized": getattr(provider, "_backend_initialised", None),
        "resolved_path": str(MAGEMinBackend()._resolve_binary_path()),
        "provider_backend_path": str(getattr(backend, "_binary_path", None)),
        "backend_available": is_available() if callable(is_available) else None,
        "backend_initialization_error": repr(
            getattr(provider, "_backend_initialization_error", None)
        ),
        "backend_last_error": getattr(backend, "_last_error", None),
        "refusal_reason": getattr(refusal, "reason", None),
        "refusal_diagnostic": getattr(refusal, "diagnostic", None),
    }


def _emit_provider_state(
    simulator: PyrolysisSimulator,
    refusal: Exception | None = None,
) -> None:
    engine_config = load_config()
    configured_raw_path = (
        engine_config.paths.magemin_binary_path
        if engine_config is not None
        else None
    )
    print(
        json.dumps(
            {
                "engine_config": str(config_path()),
                "configured_raw_path": str(configured_raw_path),
                "configured_existing_path": str(
                    configured_magemin_binary_path()
                ),
                "path_MAGEMin": shutil.which("MAGEMin"),
                "provider_state": _backend_state(simulator, refusal),
            },
            sort_keys=True,
            default=str,
        ),
        file=sys.stderr,
    )


_resolve_liquid_fraction_provider = (
    PyrolysisSimulator._resolve_liquid_fraction_provider
)


def _trace_liquid_fraction_provider(
    simulator: PyrolysisSimulator,
) -> None:
    try:
        _resolve_liquid_fraction_provider(simulator)
    except Exception as exc:
        _emit_provider_state(simulator, exc)
        raise
    else:
        _emit_provider_state(simulator)


def main() -> int:
    PyrolysisSimulator._resolve_liquid_fraction_provider = (
        _trace_liquid_fraction_provider
    )
    try:
        payload, engagement = produce_coating_golden()
    except Exception as exc:
        print(
            f"coating producer failed: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 1

    default_output = (
        Path(os.environ["TMPDIR"]) / "goldref-r8-coating-output.json"
    )
    output = Path(os.environ.get("GOALFLIGHT_COATING_OUTPUT", default_output))
    output.write_bytes(payload)
    print(f"payload={output}")
    print(f"sha256={hashlib.sha256(payload).hexdigest()}")
    print(f"bytes={len(payload)}")
    print(json.dumps(engagement, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

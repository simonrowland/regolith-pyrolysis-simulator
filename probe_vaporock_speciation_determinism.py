#!/usr/bin/env python3
"""Compare real VapoRock empty-speciation refusals in fresh processes."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import pickle
import subprocess
import sys
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

_TARGET_REASON = "speciation_no_finite_values"
_SAMPLE_SIZE = 50
_REPEAT_COUNT = 3
_DISCOVERY_CHUNK_SIZE = 128


def _disable_engine_cache() -> None:
    os.environ.pop("REGOLITH_ENGINE_CACHE", None)
    os.environ.pop("REGOLITH_ENGINE_CACHE_MODE", None)


def _diagnostics_text(diagnostics: Mapping[str, Any] | None) -> str:
    return json.dumps(
        diagnostics or {},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
        allow_nan=True,
    )


def _evaluate_one(request: Mapping[str, Any]) -> dict[str, str | None]:
    from simulator.diagnostic_helpers.binary_pot_battery import (
        close_battery_engine_handle,
        open_battery_engine,
    )

    handle = open_battery_engine("vaporock")
    if not handle.available or handle.backend is None:
        raise RuntimeError(
            "VapoRock unavailable in fresh process: "
            f"{handle.unavailable_reason}"
        )
    try:
        result = handle.backend.equilibrate(**dict(request))
        diagnostics = getattr(result, "diagnostics", None)
        if not isinstance(diagnostics, Mapping):
            diagnostics = {}
        return {
            "status": str(getattr(result, "status", "")),
            "code": (
                None
                if diagnostics.get("backend_status_reason") is None
                else str(diagnostics["backend_status_reason"])
            ),
            "diagnostics": _diagnostics_text(diagnostics),
        }
    finally:
        close_battery_engine_handle(handle)


def _evaluate_child(requests_path: Path, index: int, result_path: Path) -> int:
    _disable_engine_cache()
    with requests_path.open("rb") as stream:
        requests = pickle.load(stream)
    outcome = _evaluate_one(requests[index])
    result_path.write_text(json.dumps(outcome, sort_keys=True), encoding="utf-8")
    return 0


def _discover_requests(root: Path) -> list[tuple[str, dict[str, Any]]]:
    from simulator.battery.score import load_score_context, engines_from_names
    from simulator.battery.score_run import (
        _prepare_score_run,
        _score_store_with_decisions,
    )
    from simulator.diagnostic_helpers.binary_pot_battery import (
        close_battery_engine_handle,
        open_battery_engine,
    )
    from simulator.engine_cache import (
        ENGINE_CHEMISTRY_VERSION,
        engine_cache_key,
        engine_cache_settings,
    )
    from simulator.melt_backend.vaporock import VapoRockBackend

    engine_set = engines_from_names(["vaporock"])
    context = load_score_context(root)
    prepared = _prepare_score_run(
        context,
        engines=engine_set,
        rail=None,
        work_id=None,
        limit=None,
        include_diagnostics=True,
        measured_only=False,
    )
    handle = open_battery_engine("vaporock")
    if not handle.available or handle.backend is None:
        raise RuntimeError(
            "VapoRock unavailable during request discovery: "
            f"{handle.unavailable_reason}"
        )

    adapter_settings = engine_cache_settings("vaporock", handle.engine_settings)
    found: dict[bytes, dict[str, Any]] = {}
    original_equilibrate = VapoRockBackend.equilibrate

    def capture_equilibrate(self, *args: Any, **kwargs: Any) -> Any:
        result = original_equilibrate(self, *args, **kwargs)
        diagnostics = getattr(result, "diagnostics", None)
        if (
            isinstance(diagnostics, Mapping)
            and diagnostics.get("backend_status_reason") == _TARGET_REASON
        ):
            request = copy.deepcopy(kwargs)
            request.pop("call_timeout_s", None)
            key = engine_cache_key(
                "vaporock",
                ENGINE_CHEMISTRY_VERSION["vaporock"],
                {"kwargs": request, "engine_settings": adapter_settings},
            )
            found.setdefault(key, copy.deepcopy(kwargs))
        return result

    VapoRockBackend.equilibrate = capture_equilibrate
    try:
        handles = {"vaporock": handle}
        for offset in range(0, len(prepared.refs), _DISCOVERY_CHUNK_SIZE):
            refs = prepared.refs[offset : offset + _DISCOVERY_CHUNK_SIZE]
            _score_store_with_decisions(
                context,
                engines=engine_set,
                _prepared=prepared,
                _refs=refs,
                handles=handles,
            )
            if len(found) >= _SAMPLE_SIZE:
                break
    finally:
        VapoRockBackend.equilibrate = original_equilibrate
        close_battery_engine_handle(handle)

    selected = sorted(found.items(), key=lambda item: item[0])[:_SAMPLE_SIZE]
    if len(selected) != _SAMPLE_SIZE:
        raise RuntimeError(
            f"found only {len(selected)} distinct {_TARGET_REASON} requests; "
            f"needed {_SAMPLE_SIZE}"
        )
    return [(key.hex(), request) for key, request in selected]


def _run_fresh_evaluations(
    root: Path,
    requests: list[tuple[str, dict[str, Any]]],
) -> list[dict[str, Any]]:
    script = Path(__file__).resolve()
    env = dict(os.environ)
    env.pop("REGOLITH_ENGINE_CACHE", None)
    env.pop("REGOLITH_ENGINE_CACHE_MODE", None)
    request_values = [request for _key, request in requests]
    outcomes: list[list[dict[str, str | None]]] = [
        [] for _ in requests
    ]
    with tempfile.TemporaryDirectory(
        prefix="vaporock-speciation-determinism-",
        dir=os.environ.get("TMPDIR"),
    ) as temporary:
        scratch = Path(temporary)
        requests_path = scratch / "requests.pkl"
        with requests_path.open("wb") as stream:
            pickle.dump(request_values, stream, protocol=pickle.HIGHEST_PROTOCOL)
        for index in range(len(requests)):
            for repeat in range(_REPEAT_COUNT):
                result_path = scratch / f"result-{index:02d}-{repeat}.json"
                completed = subprocess.run(
                    [
                        sys.executable,
                        str(script),
                        "--evaluate",
                        str(requests_path),
                        str(index),
                        str(result_path),
                    ],
                    cwd=root,
                    env=env,
                    check=False,
                    capture_output=True,
                    text=True,
                )
                if completed.returncode != 0:
                    raise RuntimeError(
                        "fresh VapoRock process failed for request "
                        f"{requests[index][0][:16]} repeat {repeat + 1}: "
                        f"{completed.stderr[-2000:]}"
                    )
                outcomes[index].append(
                    json.loads(result_path.read_text(encoding="utf-8"))
                )

    report: list[dict[str, Any]] = []
    for (request_key, _request), repeated in zip(requests, outcomes, strict=True):
        codes = [outcome["code"] for outcome in repeated]
        statuses = [outcome["status"] for outcome in repeated]
        diagnostics = [outcome["diagnostics"] for outcome in repeated]
        same_code = len(set(codes)) == 1
        same_status = len(set(statuses)) == 1
        same_diagnostics = len(set(diagnostics)) == 1
        report.append({
            "request_key": request_key[:16],
            "all_three_are_target_refusal": all(
                code == _TARGET_REASON for code in codes
            ),
            "same_status": same_status,
            "same_code": same_code,
            "same_diagnostics": same_diagnostics,
            "identical": same_status and same_code and same_diagnostics,
            "diagnostics_sha256": [
                hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]
                for value in diagnostics
            ],
            "outcomes": [
                {"status": status, "code": code}
                for status, code in zip(statuses, codes, strict=True)
            ],
            **({"diagnostics": diagnostics} if not same_diagnostics else {}),
        })
    return report


def main() -> int:
    root = Path.cwd()
    if not (root / "scripts" / "battery_score.py").is_file():
        raise SystemExit("run this probe from the repository root")
    _disable_engine_cache()
    requests = _discover_requests(root)
    report = _run_fresh_evaluations(root, requests)
    print(json.dumps({
        "reason": _TARGET_REASON,
        "request_count": len(report),
        "fresh_process_evaluations": len(report) * _REPEAT_COUNT,
        "all_identical": all(row["identical"] for row in report),
        "all_remained_target_refusals": all(
            row["all_three_are_target_refusal"] for row in report
        ),
        "requests": report,
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    if len(sys.argv) == 5 and sys.argv[1] == "--evaluate":
        raise SystemExit(
            _evaluate_child(Path(sys.argv[2]), int(sys.argv[3]), Path(sys.argv[4]))
        )
    raise SystemExit(main())

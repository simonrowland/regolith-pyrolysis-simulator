"""Optimizer determinism utilities.

Call :func:`pin_worker_env` at worker start, before NumPy/SciPy or BLAS-backed
backends import and bind thread pools. Eval seeds follow the existing DOE/run
integer-seed convention; future samplers reuse that seed instead of adding a
second seed namespace.
"""

from __future__ import annotations

import dataclasses
import difflib
import json
import math
import os
import random
from collections.abc import Callable, Mapping
from typing import Any

from simulator.optimize.canonical import canonical_json_dumps, normalize_canonical_value
from simulator.optimize.evalspec import canonical_evalspec_json
from simulator.optimize.evaluate import FailureCategory, RunReference, ScoredResult

THREAD_ENV_VARS: tuple[str, ...] = (
    "OMP_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "MKL_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
    "VECLIB_MAXIMUM_THREADS",
)

_VOLATILE_KEYS = frozenset(
    {
        "created_at", "started_at", "finished_at", "completed_at",
        "timestamp", "timestamps", "uuid", "run_uuid", "run_id", "trace_id",
        "job_id", "request_id", "wall_time_s", "wall_clock_s",
        "wall_clock_time_s", "wall_seconds", "elapsed_s", "elapsed_ms",
        "execution_time_s", "execution_time_ms", "perf_counter_s",
        "process_time_s", "host", "hostname", "machine", "platform", "cwd",
        "workdir", "working_dir", "tmpdir", "temp_dir",
        "output_path", "output_paths", "output_dir", "output_file",
        "report_path", "report_file", "log_path", "log_file",
        "stdout_path", "stdout_file", "stderr_path", "stderr_file",
        "artifact_path", "artifact_paths", "artifact_dir", "artifact_file",
        "result_path", "results_path", "results_dir", "index_path",
        "index_file",
    }
)


def pin_worker_env() -> None:
    """Pin BLAS/numerics pools to one thread; safe to call repeatedly."""

    for name in THREAD_ENV_VARS:
        os.environ[name] = "1"


def pin_seeds(seed: int) -> None:
    """Seed Python and optional NumPy RNGs from the DOE/run seed."""

    seed_int = int(seed)
    random.seed(seed_int)
    try:
        import numpy as np  # type: ignore[import-not-found]
    except ImportError:
        return
    np.random.seed(seed_int)


def deterministic_result_view(scored_result: Any) -> str:
    """Hashable canonical view with volatile run metadata stripped.

    Kept: feasibility, failure category, objectives, gate margins, run
    status/reason, product summary, ledger/product trace data, and simulated
    duration/campaign hours. Stripped only from result blobs: wall-clock timing,
    real timestamps, run IDs/UUIDs, host fields, and filesystem paths.
    """

    payload = (
        _scored_payload(scored_result)
        if isinstance(scored_result, ScoredResult)
        else _strip_volatile(scored_result)
    )
    return canonical_json_dumps(normalize_canonical_value(payload))


def assert_deterministic(
    evaluate_fn: Callable[[Any], Any],
    spec: Any,
    *,
    repeats: int = 2,
) -> tuple[Any, ...]:
    """Run ``evaluate_fn(spec)`` repeatedly and raise on substantive drift."""

    if repeats < 2:
        raise ValueError("determinism probe requires repeats >= 2")

    results = tuple(evaluate_fn(spec) for _ in range(repeats))
    views = tuple(deterministic_result_view(result) for result in results)
    for index, view in enumerate(views[1:], start=2):
        if view == views[0]:
            continue
        diff = "\n".join(
            difflib.unified_diff(
                _pretty(views[0]),
                _pretty(view),
                fromfile="repeat-1",
                tofile=f"repeat-{index}",
                lineterm="",
            )
        )
        raise AssertionError(
            f"nondeterministic evaluation result for repeat {index}:\n{diff}"
        )
    return results


def _scored_payload(result: ScoredResult) -> dict[str, Any]:
    failure = result.failure_category
    # candidate_id is an optimizer strategy/back-reference, not a physics field.
    # eval_spec/cache_key are deterministic inputs; include both to catch drift
    # between the canonical spec and its address. notes carry validation/refusal
    # branch reasons, so they are substantive.
    return {
        "eval_spec": _eval_spec_payload(result.eval_spec),
        "cache_key": result.cache_key,
        "feasible": result.feasible,
        "failure_category": (
            failure.value if isinstance(failure, FailureCategory) else failure
        ),
        "objectives": [
            _objective_payload(value)
            for value in (() if result.objectives is None else result.objectives.values)
        ],
        "feasibility_margins": {
            key: _margin_payload(margin)
            for key, margin in result.feasibility_margins.items()
        },
        "failing_gates": list(result.failing_gates),
        "run_reference": _run_reference_payload(result.run_reference),
        "notes": list(result.notes),
    }


def _eval_spec_payload(eval_spec: Any) -> Any:
    if eval_spec is None:
        return None
    return json.loads(canonical_evalspec_json(eval_spec).decode("utf-8"))


def _objective_payload(value: Any) -> dict[str, Any]:
    return {
        "metric": value.metric,
        "sense": value.sense,
        "value": value.value,
        "units": value.units,
        "ordinal": value.ordinal,
    }


def _margin_payload(margin: Any) -> dict[str, Any]:
    threshold = margin.threshold
    payload = {
        "gate": margin.gate,
        "feasible": margin.feasible,
        "margin": _number_payload(margin.margin),
        "threshold": {
            "id": threshold.id,
            "value": threshold.value,
            "units": threshold.units,
            "source": threshold.source,
            "source_ref": threshold.source_ref,
            "tolerance": threshold.tolerance,
        },
        "observed": _number_payload(margin.observed),
        "detail": margin.detail,
    }
    if getattr(margin, "status", "available") != "available":
        payload["status"] = margin.status
    if not getattr(margin, "authoritative", True):
        payload["authoritative"] = False
    if getattr(margin, "output_status", "authoritative") != "authoritative":
        payload["output_status"] = margin.output_status
    if getattr(margin, "status_reason", ""):
        payload["status_reason"] = margin.status_reason
    status_payload = getattr(margin, "status_payload", {})
    if status_payload:
        payload["status_payload"] = status_payload
    return payload


def _number_payload(value: Any) -> Any:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return value
    if math.isnan(numeric):
        return "nan"
    if math.isinf(numeric):
        return "+inf" if numeric > 0.0 else "-inf"
    return value


def _run_reference_payload(run_reference: RunReference | None) -> dict[str, Any] | None:
    if run_reference is None:
        return None
    return {
        "status": run_reference.status,
        "error_message": run_reference.error_message,
        "reason": run_reference.reason,
        "trace": _strip_volatile(run_reference.trace),
        "product_summary": _strip_volatile(run_reference.product_summary),
    }


def _strip_volatile(value: Any) -> Any:
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return _strip_volatile(dataclasses.asdict(value))
    if isinstance(value, Mapping):
        return {
            str(key): _strip_volatile(item)
            for key, item in value.items()
            if not _is_volatile_key(str(key))
        }
    if isinstance(value, (list, tuple)):
        return [_strip_volatile(item) for item in value]
    return value


def _is_volatile_key(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    return normalized in _VOLATILE_KEYS


def _pretty(view: str) -> list[str]:
    return json.dumps(json.loads(view), indent=2, sort_keys=True).splitlines()

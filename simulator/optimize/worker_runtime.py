"""Thread-local warm runtime for optimizer worker evaluations."""

from __future__ import annotations

from dataclasses import dataclass
import os
import threading
from typing import Any, Mapping

from simulator.backend_names import canonical_backend_name
from simulator.backends import (
    BackendSelectionPolicy,
    requires_stage0_subprocess,
    resolve_backend,
)


WARM_WORKERS_ENV = "REGOLITH_OPTIMIZER_WARM_WORKERS"
_DISABLED_VALUES = {"0", "false", "no", "off"}
_WORKER_STATE = threading.local()


@dataclass(frozen=True)
class WorkerEvalContext:
    """Worker-scoped backend state reused across fresh eval sessions."""

    backend_name: str
    feedstock_id: str | None
    stage0_subprocess_required: bool
    backend: Any
    transport: Any | None = None


def warm_workers_enabled() -> bool:
    raw = os.environ.get(WARM_WORKERS_ENV, "1")
    return raw.strip().lower() not in _DISABLED_VALUES


def warm_worker_runtime(
    backend_name: str,
    *,
    feedstock_id: str | None = None,
    feedstocks: Mapping[str, Any] | None = None,
    stage0_subprocess_required: bool | None = None,
) -> WorkerEvalContext | None:
    """Resolve one worker-scoped backend when warm workers are enabled."""

    if not warm_workers_enabled():
        clear_worker_runtime()
        return None
    normalized = canonical_backend_name(str(backend_name or "stub").strip() or "stub")
    if feedstock_id is None and normalized != "stub":
        clear_worker_runtime()
        return None
    subprocess_required = requires_stage0_subprocess(
        feedstock_id,
        feedstocks,
        explicit=stage0_subprocess_required,
    )
    backend = resolve_backend(
        normalized,
        BackendSelectionPolicy.RUNNER_STRICT,
        feedstock_id=feedstock_id,
        feedstocks=feedstocks,
        stage0_subprocess_required=subprocess_required,
    )
    context = WorkerEvalContext(
        backend_name=normalized,
        feedstock_id=str(feedstock_id) if feedstock_id is not None else None,
        stage0_subprocess_required=subprocess_required,
        backend=backend,
        transport=getattr(backend, "transport", getattr(backend, "_transport", None)),
    )
    _WORKER_STATE.context = context
    return context


def get_worker_runtime(
    *,
    feedstock_id: str | None = None,
    stage0_subprocess_required: bool | None = None,
) -> WorkerEvalContext | None:
    """Return the current worker runtime, respecting the rollback env switch."""

    if not warm_workers_enabled():
        return None
    context = getattr(_WORKER_STATE, "context", None)
    if context is None:
        return None
    context_feedstock = getattr(context, "feedstock_id", None)
    if feedstock_id is None and context_feedstock is not None:
        return None
    if feedstock_id is not None and context_feedstock not in (None, str(feedstock_id)):
        return None
    if (
        stage0_subprocess_required is not None
        and bool(getattr(context, "stage0_subprocess_required", False))
        != bool(stage0_subprocess_required)
    ):
        return None
    return context


def clear_worker_runtime() -> None:
    if hasattr(_WORKER_STATE, "context"):
        delattr(_WORKER_STATE, "context")

"""Thread-local warm runtime for optimizer worker evaluations."""

from __future__ import annotations

from dataclasses import dataclass
import os
import threading
from typing import Any

from simulator.backends import BackendSelectionPolicy, resolve_backend


WARM_WORKERS_ENV = "REGOLITH_OPTIMIZER_WARM_WORKERS"
_DISABLED_VALUES = {"0", "false", "no", "off"}
_WORKER_STATE = threading.local()


@dataclass(frozen=True)
class WorkerEvalContext:
    """Worker-scoped backend state reused across fresh eval sessions."""

    backend_name: str
    backend: Any
    transport: Any | None = None


def warm_workers_enabled() -> bool:
    raw = os.environ.get(WARM_WORKERS_ENV, "1")
    return raw.strip().lower() not in _DISABLED_VALUES


def warm_worker_runtime(backend_name: str) -> WorkerEvalContext | None:
    """Resolve one worker-scoped backend when warm workers are enabled."""

    if not warm_workers_enabled():
        clear_worker_runtime()
        return None
    normalized = str(backend_name or "stub").strip() or "stub"
    backend = resolve_backend(normalized, BackendSelectionPolicy.RUNNER_STRICT)
    context = WorkerEvalContext(
        backend_name=normalized,
        backend=backend,
        transport=getattr(backend, "transport", getattr(backend, "_transport", None)),
    )
    _WORKER_STATE.context = context
    return context


def get_worker_runtime() -> WorkerEvalContext | None:
    """Return the current worker runtime, respecting the rollback env switch."""

    if not warm_workers_enabled():
        return None
    return getattr(_WORKER_STATE, "context", None)


def clear_worker_runtime() -> None:
    if hasattr(_WORKER_STATE, "context"):
        delattr(_WORKER_STATE, "context")

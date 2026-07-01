from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

import simulator.optimize.pool as pool_module
import simulator.optimize.worker_runtime as worker_runtime_module
import simulator.session as session_module
from simulator.melt_backend.base import StubBackend
from simulator.optimize.evaluate import EngineBugAbort, evaluate
from simulator.optimize.recipe import RecipePatch
from simulator.optimize.worker_runtime import (
    WARM_WORKERS_ENV,
    clear_worker_runtime,
    get_worker_runtime,
    warm_worker_runtime,
)
from simulator.run_executor import RunExecutor, _backend_from_worker_runtime
from simulator.runner import PyrolysisRun


PO2_DEFAULT = ("campaigns", "C0b_p_cleanup", "pO2_mbar_default")

PROFILE = {
    "profile_id": "worker-runtime-test",
    "profile_schema_version": "profile-schema-v1",
    "feedstock": "lunar_mare_low_ti",
    "objectives": [
        {
            "metric": "oxygen_kg",
            "sense": "max",
            "units": "kg",
            "weight": 1.0,
            "rationale": "test oxygen objective evidence",
        },
    ],
    "constraints": {"gates": ["delivered_stream_purity"]},
    "run": {
        "campaign": "C0",
        "hours": 1,
        "mass_kg": 1000.0,
        "backend_name": "stub",
    },
    "fidelities": {"fast": {"backend_name": "stub", "hours": 1}},
    "seed_recipes": [
        {
            "id": "worker-runtime-seed",
            "source_campaign": "C0",
            "patch": {"campaigns": {"C0": {"temp_range_C": [900, 950]}}},
        }
    ],
}


@pytest.fixture(autouse=True)
def _clear_runtime() -> object:
    clear_worker_runtime()
    yield
    clear_worker_runtime()


def _stub_backend() -> StubBackend:
    backend = StubBackend()
    backend.initialize({})
    return backend


def _session_config():
    return PyrolysisRun(
        feedstock_id="lunar_mare_low_ti",
        campaign="C0",
        hours=0,
        backend_name="stub",
    )._session_config()


def test_warm_runtime_reuses_backend_but_creates_fresh_sim_and_ledger(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(WARM_WORKERS_ENV, raising=False)
    resolve_calls: list[str] = []

    def resolve_once(backend_name: str, *_args: object, **_kwargs: object) -> StubBackend:
        resolve_calls.append(backend_name)
        return _stub_backend()

    monkeypatch.setattr(worker_runtime_module, "resolve_backend", resolve_once)
    context = warm_worker_runtime(
        "stub",
        feedstock_id="lunar_mare_low_ti",
        stage0_subprocess_required=True,
    )
    assert context is not None
    assert resolve_calls == ["stub"]

    def fail_cold_resolve(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("cold backend resolve should not run")

    monkeypatch.setattr(session_module, "resolve_backend", fail_cold_resolve)
    seen: list[tuple[object, object, object]] = []

    def record_session(
        self: RunExecutor,
        session: object,
        *,
        hours: int,
    ) -> SimpleNamespace:
        sim = session.simulator
        # Keep strong refs; CPython may recycle id() after the first session is freed.
        seen.append((sim, sim.atom_ledger, sim.backend))
        return SimpleNamespace(session=session, simulator=sim)

    monkeypatch.setattr(RunExecutor, "execute_session", record_session)

    executor = RunExecutor()
    executor.execute(_session_config(), worker_runtime=context)
    executor.execute(_session_config(), worker_runtime=context)

    assert resolve_calls == ["stub"]
    assert seen[0][2] is context.backend
    assert seen[1][2] is context.backend
    assert seen[0][0] is not seen[1][0]
    assert seen[0][1] is not seen[1][1]


def test_cold_env_forces_fresh_backend_resolve(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(WARM_WORKERS_ENV, "0")
    assert warm_worker_runtime("stub") is None
    assert get_worker_runtime() is None
    resolve_calls: list[str] = []

    def resolve_every_time(
        backend_name: str,
        *_args: object,
        **_kwargs: object,
    ) -> StubBackend:
        resolve_calls.append(backend_name)
        return _stub_backend()

    monkeypatch.setattr(session_module, "resolve_backend", resolve_every_time)

    def no_run(self: RunExecutor, session: object, *, hours: int) -> SimpleNamespace:
        return SimpleNamespace(session=session, simulator=session.simulator)

    monkeypatch.setattr(RunExecutor, "execute_session", no_run)

    executor = RunExecutor()
    executor.execute(_session_config())
    executor.execute(_session_config())

    assert resolve_calls == ["stub", "stub"]


def test_bare_real_warm_runtime_declines_without_feedstock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(WARM_WORKERS_ENV, raising=False)

    def fail_resolve(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("real warm runtime must not resolve without feedstock")

    monkeypatch.setattr(worker_runtime_module, "resolve_backend", fail_resolve)

    assert warm_worker_runtime("alphamelts") is None
    assert get_worker_runtime() is None


def test_worker_runtime_keyed_by_feedstock_and_subprocess_required(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(WARM_WORKERS_ENV, raising=False)
    monkeypatch.setattr(
        worker_runtime_module,
        "resolve_backend",
        lambda *_args, **_kwargs: _stub_backend(),
    )

    context = warm_worker_runtime(
        "stub",
        feedstock_id="lunar_mare_low_ti",
        stage0_subprocess_required=False,
    )

    assert context is not None
    assert get_worker_runtime() is None
    assert get_worker_runtime(
        feedstock_id="lunar_mare_low_ti",
        stage0_subprocess_required=False,
    ) is context
    assert get_worker_runtime(feedstock_id="mars_basalt") is None
    assert get_worker_runtime(
        feedstock_id="lunar_mare_low_ti",
        stage0_subprocess_required=True,
    ) is None


def test_backend_from_worker_runtime_rejects_feedstock_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(WARM_WORKERS_ENV, raising=False)
    monkeypatch.setattr(
        worker_runtime_module,
        "resolve_backend",
        lambda *_args, **_kwargs: _stub_backend(),
    )
    context = warm_worker_runtime(
        "stub",
        feedstock_id="mars_basalt",
        stage0_subprocess_required=False,
    )

    assert context is not None
    assert _backend_from_worker_runtime(_session_config(), context) is None


def test_backend_from_worker_runtime_rejects_subprocess_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(WARM_WORKERS_ENV, raising=False)
    monkeypatch.setattr(
        worker_runtime_module,
        "resolve_backend",
        lambda *_args, **_kwargs: _stub_backend(),
    )
    context = warm_worker_runtime(
        "stub",
        feedstock_id="lunar_mare_low_ti",
        stage0_subprocess_required=False,
    )

    assert context is not None
    assert _backend_from_worker_runtime(_session_config(), context) is None


def test_evaluate_uses_thread_local_worker_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(WARM_WORKERS_ENV, raising=False)
    monkeypatch.setattr(
        worker_runtime_module,
        "resolve_backend",
        lambda *_args, **_kwargs: _stub_backend(),
    )
    context = warm_worker_runtime(
        "stub",
        feedstock_id="lunar_mare_low_ti",
        stage0_subprocess_required=True,
    )
    assert context is not None

    class RecordingExecutor:
        runtime: object | None = None

        def execute(self, config: object, *, worker_runtime: object | None = None) -> object:
            self.runtime = worker_runtime
            raise RuntimeError("stop after runtime probe")

    executor = RecordingExecutor()
    with pytest.raises(EngineBugAbort, match="RuntimeError: stop after runtime probe"):
        evaluate(
            RecipePatch({PO2_DEFAULT: 9.0}),
            "lunar_mare_low_ti",
            "fast",
            profile=PROFILE,
            executor=executor,
        )

    assert executor.runtime is context


def test_evaluate_declines_thread_local_worker_runtime_for_feedstock_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(WARM_WORKERS_ENV, raising=False)
    monkeypatch.setattr(
        worker_runtime_module,
        "resolve_backend",
        lambda *_args, **_kwargs: _stub_backend(),
    )
    context = warm_worker_runtime(
        "stub",
        feedstock_id="mars_basalt",
        stage0_subprocess_required=False,
    )
    assert context is not None

    class RecordingExecutor:
        runtime: object | None = context

        def execute(
            self,
            config: object,
            *,
            worker_runtime: object | None = None,
        ) -> object:
            self.runtime = worker_runtime
            raise RuntimeError("stop after runtime probe")

    executor = RecordingExecutor()
    with pytest.raises(EngineBugAbort, match="RuntimeError: stop after runtime probe"):
        evaluate(
            RecipePatch({PO2_DEFAULT: 9.0}),
            "lunar_mare_low_ti",
            "fast",
            profile=PROFILE,
            executor=executor,
        )

    assert executor.runtime is None


def test_pool_worker_initializer_warms_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(WARM_WORKERS_ENV, raising=False)
    calls: list[str] = []

    def record_warm(backend_name: str) -> object:
        calls.append(backend_name)
        return object()

    monkeypatch.setattr(pool_module, "warm_worker_runtime", record_warm)

    pool_module._initialize_worker("stub")

    assert calls == ["stub"]


def test_pool_worker_initializer_passes_warm_runtime_spec(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(WARM_WORKERS_ENV, raising=False)
    calls: list[tuple[str, str | None, bool | None]] = []

    def record_warm(
        backend_name: str,
        *,
        feedstock_id: str | None = None,
        stage0_subprocess_required: bool | None = None,
        **_kwargs: object,
    ) -> object:
        calls.append((backend_name, feedstock_id, stage0_subprocess_required))
        return object()

    monkeypatch.setattr(pool_module, "warm_worker_runtime", record_warm)

    pool_module._initialize_worker(
        pool_module._WarmRuntimeSpec(
            backend_name="stub",
            feedstock_id="lunar_mare_low_ti",
            stage0_subprocess_required=True,
        )
    )

    assert calls == [("stub", "lunar_mare_low_ti", True)]


def test_web_does_not_import_worker_runtime() -> None:
    root = Path(__file__).resolve().parents[1]
    offenders: list[str] = []
    for path in (root / "web").rglob("*"):
        if path.suffix not in {".py", ".js", ".jsx", ".ts", ".tsx"}:
            continue
        if "worker_runtime" in path.read_text(errors="ignore"):
            offenders.append(str(path.relative_to(root)))
    assert offenders == []

from __future__ import annotations

from concurrent.futures import Future, ProcessPoolExecutor
import json
import os
import pickle
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
from types import MappingProxyType

import pytest

import simulator.optimize.pool as pool_module
from simulator.optimize.determinism import THREAD_ENV_VARS, deterministic_result_view, pin_worker_env
from simulator.optimize.evalspec import EvalSpec, cache_key, canonical_evalspec_json
from simulator.optimize.evaluate import EngineBugAbort, FailureCategory, RunReference, ScoredResult
from simulator.optimize.objective import ObjectiveValue, ObjectiveVector
from simulator.optimize.physics import GateMargin, ThresholdSpec
from simulator.optimize.pool import PoolEvaluationRequest, evaluate_batch
from simulator.optimize.recipe import RecipePatch
from simulator.optimize.results_store import ResultStore


_DATA_DIGESTS = {
    "setpoints": "setpoints-digest",
    "feedstocks": "feedstocks-digest",
    "materials": "materials-digest",
    "vapor_pressures": "vapor-digest",
    "species_catalog": "species-catalog-digest",
    "profile": "profile-digest",
}


@pytest.fixture(autouse=True)
def _restore_thread_env() -> object:
    snapshot = {name: os.environ.get(name) for name in THREAD_ENV_VARS}
    yield
    for name, value in snapshot.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value


def _process_pool_probe() -> int:
    return os.getpid()


def _process_pool_unavailable_reason() -> str | None:
    executor: ProcessPoolExecutor | None = None
    try:
        executor = ProcessPoolExecutor(max_workers=1)
        future = executor.submit(_process_pool_probe)
        future.result(timeout=5)
    except Exception as exc:
        return f"{type(exc).__name__}: {exc}"
    finally:
        if executor is not None:
            executor.shutdown(wait=False, cancel_futures=True)
    return None


@pytest.fixture(scope="module")
def spawnable_process_pool() -> None:
    reason = _process_pool_unavailable_reason()
    if reason is not None:
        pytest.skip(f"requires a spawnable process pool: {reason}")


def _patch(value: int) -> RecipePatch:
    return RecipePatch.from_nested({"test": {"value": value}})


def _profile() -> dict[str, object]:
    return {
        "id": "pool-test-profile",
        "run": {
            "campaign": "C0",
            "hours": 2,
            "mass_kg": 10.0,
            "backend_name": "stub",
            "track": "pyrolysis",
        },
    }


def _pool_task(
    index: int,
    *,
    feedstock_id: str,
    stage0_subprocess_required: bool,
    backend_name: str = "stub",
) -> pool_module._PoolTask:
    profile = _profile()
    profile["run"] = dict(profile["run"])
    profile["run"]["backend_name"] = backend_name
    return pool_module._PoolTask(
        index=index,
        patch=_patch(index + 1),
        feedstock_id=feedstock_id,
        fidelity="fast",
        profile=profile,
        candidate_id=None,
        output_dir=f"eval-{index:06d}",
        stage0_subprocess_required=stage0_subprocess_required,
    )


def test_warm_runtime_spec_requires_one_backend_and_one_feedstock() -> None:
    assert pool_module._warm_runtime_spec(
        (
            _pool_task(
                0,
                feedstock_id="lunar_mare_low_ti",
                stage0_subprocess_required=True,
            ),
            _pool_task(
                1,
                feedstock_id="mars_basalt",
                stage0_subprocess_required=True,
            ),
        )
    ) is None
    assert pool_module._warm_runtime_spec(
        (
            _pool_task(
                0,
                feedstock_id="lunar_mare_low_ti",
                stage0_subprocess_required=True,
                backend_name="stub",
            ),
            _pool_task(
                1,
                feedstock_id="lunar_mare_low_ti",
                stage0_subprocess_required=True,
                backend_name="alphamelts",
            ),
        )
    ) is None

    spec = pool_module._warm_runtime_spec(
        (
            _pool_task(
                0,
                feedstock_id="lunar_mare_low_ti",
                stage0_subprocess_required=True,
            ),
            _pool_task(
                1,
                feedstock_id="lunar_mare_low_ti",
                stage0_subprocess_required=True,
            ),
        )
    )

    assert spec == pool_module._WarmRuntimeSpec(
        backend_name="stub",
        feedstock_id="lunar_mare_low_ti",
        stage0_subprocess_required=True,
    )


def _fake_evaluate(
    patch: RecipePatch,
    feedstock_id: str,
    fidelity: str,
    *,
    profile: dict[str, object],
    candidate_id: str | None = None,
    output_dir: str,
    **_: object,
) -> ScoredResult:
    value = int(patch.to_nested()["test"]["value"])
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "artifact.txt").write_text(f"{candidate_id}:{value}\n")
    spec = _spec(patch, feedstock_id, fidelity, profile)
    return ScoredResult(
        candidate_id=candidate_id,
        eval_spec=spec,
        cache_key=cache_key(spec),
        feasible=True,
        objectives=ObjectiveVector(
            (
                ObjectiveValue("oxygen_kg", "maximize", float(value), "kg", 0),
                ObjectiveValue("energy_kWh", "minimize", float(value + 10), "kWh", 1),
            )
        ),
        feasibility_margins={"pool_gate": _margin(value)},
        run_reference=RunReference(
            status="ok",
            trace={
                "value": value,
                "cwd": os.getcwd(),
                "output_dir": str(out),
                "thread_env": {name: os.environ.get(name) for name in THREAD_ENV_VARS},
            },
            product_summary={"oxygen_kg": float(value)},
        ),
        notes=("fake",),
    )


def _engine_bug_evaluate(
    patch: RecipePatch,
    feedstock_id: str,
    fidelity: str,
    *,
    profile: dict[str, object],
    candidate_id: str | None = None,
    **_: object,
) -> ScoredResult:
    raise EngineBugAbort("worker engine bug", patch=patch, candidate_id=candidate_id)


def _sleepy_evaluate(
    patch: RecipePatch,
    feedstock_id: str,
    fidelity: str,
    *,
    profile: dict[str, object],
    candidate_id: str | None = None,
    output_dir: str,
    **kwargs: object,
) -> ScoredResult:
    value = int(patch.to_nested()["test"]["value"])
    if value == 1:
        time.sleep(0.25)
    return _fake_evaluate(
        patch,
        feedstock_id,
        fidelity,
        profile=profile,
        candidate_id=candidate_id,
        output_dir=output_dir,
        **kwargs,
    )


def _slow_or_abort_evaluate(
    patch: RecipePatch,
    feedstock_id: str,
    fidelity: str,
    *,
    profile: dict[str, object],
    candidate_id: str | None = None,
    output_dir: str,
    **kwargs: object,
) -> ScoredResult:
    value = int(patch.to_nested()["test"]["value"])
    if value == 50:
        time.sleep(5.0)
    if value == 99:
        raise EngineBugAbort(
            f"abort for candidate {candidate_id}",
            patch=patch,
            candidate_id=candidate_id,
        )
    if value < 0:
        spec = _spec(patch, feedstock_id, fidelity, profile)
        return ScoredResult(
            candidate_id=candidate_id,
            eval_spec=spec,
            cache_key=cache_key(spec),
            feasible=False,
            failure_category=FailureCategory.INFEASIBLE_RECIPE,
            feasibility_margins={"pool_gate": _margin(value)},
            failing_gates=("pool_gate",),
            run_reference=RunReference(status="ok", trace={"value": value}),
        )
    return _fake_evaluate(
        patch,
        feedstock_id,
        fidelity,
        profile=profile,
        candidate_id=candidate_id,
        output_dir=output_dir,
        **kwargs,
    )


def _grandchild_then_hang_evaluate(
    patch: RecipePatch,
    feedstock_id: str,
    fidelity: str,
    *,
    profile: dict[str, object],
    candidate_id: str | None = None,
    output_dir: str,
    **kwargs: object,
) -> ScoredResult:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    survivor = out / "grandchild-survived.txt"
    subprocess.Popen(
        [
            sys.executable,
            "-c",
            (
                "import pathlib, sys, time; "
                "time.sleep(1.0); "
                "pathlib.Path(sys.argv[1]).write_text('alive', encoding='utf-8')"
            ),
            str(survivor),
        ]
    )
    time.sleep(5.0)
    return _fake_evaluate(
        patch,
        feedstock_id,
        fidelity,
        profile=profile,
        candidate_id=candidate_id,
        output_dir=output_dir,
        **kwargs,
    )


def _hard_crash_evaluate(
    patch: RecipePatch,
    feedstock_id: str,
    fidelity: str,
    *,
    profile: dict[str, object],
    candidate_id: str | None = None,
    **_: object,
) -> ScoredResult:
    os._exit(7)


def _float_reduction_evaluate(
    patch: RecipePatch,
    feedstock_id: str,
    fidelity: str,
    *,
    profile: dict[str, object],
    candidate_id: str | None = None,
    output_dir: str,
    **_: object,
) -> ScoredResult:
    value = int(patch.to_nested()["test"]["value"])
    try:
        import numpy as np

        vector = np.linspace(0.1, 1.0, 32, dtype=np.float64) * float(value)
        total = float(np.dot(vector, vector[::-1]))
    except ImportError:
        total = sum((0.1 + index / 31 * 0.9) * value for index in range(32))
    spec = _spec(patch, feedstock_id, fidelity, profile)
    return ScoredResult(
        candidate_id=candidate_id,
        eval_spec=spec,
        cache_key=cache_key(spec),
        feasible=True,
        objectives=ObjectiveVector(
            (ObjectiveValue("reduction_total", "maximize", total, "arb", 0),)
        ),
        feasibility_margins={"pool_gate": _margin(value)},
        run_reference=RunReference(
            status="ok",
            trace={"value": value, "total": total, "output_dir": output_dir},
            product_summary={"reduction_total": total},
        ),
        notes=("float sentinel",),
    )


def _mappingproxy_profile_evaluate(
    patch: RecipePatch,
    feedstock_id: str,
    fidelity: str,
    *,
    profile: dict[str, object],
    candidate_id: str | None = None,
    output_dir: str,
    **kwargs: object,
) -> ScoredResult:
    assert not _contains_mappingproxy(profile)
    return _fake_evaluate(
        patch,
        feedstock_id,
        fidelity,
        profile=profile,
        candidate_id=candidate_id,
        output_dir=output_dir,
        **kwargs,
    )


def _runtime_probe_evaluate(
    patch: RecipePatch,
    feedstock_id: str,
    fidelity: str,
    *,
    profile: dict[str, object],
    candidate_id: str | None = None,
    output_dir: str,
    worker_runtime: object | None = None,
    **kwargs: object,
) -> ScoredResult:
    assert worker_runtime is not None
    assert getattr(worker_runtime, "feedstock_id", None) == "lunar_mare_low_ti"
    assert getattr(worker_runtime, "stage0_subprocess_required", None) is True
    return _fake_evaluate(
        patch,
        feedstock_id,
        fidelity,
        profile=profile,
        candidate_id=candidate_id,
        output_dir=output_dir,
        **kwargs,
    )


def _spec(
    patch: RecipePatch,
    feedstock_id: str,
    fidelity: str,
    profile: dict[str, object],
) -> EvalSpec:
    return EvalSpec(
        recipe_id=patch.recipe_id(recipe_schema_version="pool-test-schema"),
        feedstock_recipe_digest="feedstock-recipe-digest",
        feedstock_id=feedstock_id,
        profile_id=str(profile["id"]),
        fidelity=fidelity,
        code_version="pool-test-code",
        data_digests=_DATA_DIGESTS,
        campaign="C0",
        hours=2,
        mass_kg=10.0,
        additives_kg={},
        track="pyrolysis",
        backend_name="stub",
        runtime_campaign_overrides={},
        chemistry_kernel={},
    )


def _contains_mappingproxy(value: object) -> bool:
    if isinstance(value, MappingProxyType):
        return True
    if isinstance(value, dict):
        return any(
            _contains_mappingproxy(key) or _contains_mappingproxy(child)
            for key, child in value.items()
        )
    if isinstance(value, (list, tuple, set, frozenset)):
        return any(_contains_mappingproxy(child) for child in value)
    return False


def _margin(value: int) -> GateMargin:
    threshold = ThresholdSpec(
        id="pool_gate",
        value=0.0,
        units="kg",
        source="profile",
        source_ref="test_optimizer_pool",
    )
    return GateMargin(
        gate="pool_gate",
        feasible=True,
        margin=float(value),
        threshold=threshold,
        observed=float(value),
        detail="deterministic fake",
    )


def test_process_pool_matches_serial_deterministic_view(tmp_path: Path) -> None:
    pin_worker_env()
    profile = _profile()
    requests = [
        PoolEvaluationRequest(_patch(1), "lunar_mare_low_ti", "fast", candidate_id="a"),
        PoolEvaluationRequest(_patch(2), "lunar_mare_low_ti", "fast", candidate_id="b"),
    ]
    serial = [
        _fake_evaluate(
            request.patch,
            request.feedstock_id,
            request.fidelity,
            profile=profile,
            candidate_id=request.candidate_id,
            output_dir=str(tmp_path / "serial" / str(request.candidate_id)),
        )
        for request in requests
    ]

    pooled = evaluate_batch(
        requests,
        profile=profile,
        max_workers=2,
        output_root=tmp_path / "pool",
        evaluate_fn=_fake_evaluate,
    )

    serial_views = {result.candidate_id: deterministic_result_view(result) for result in serial}
    pooled_views = {result.candidate_id: deterministic_result_view(result) for result in pooled}
    assert pooled_views == serial_views


@pytest.mark.timeout(15)
def test_spinel_feedstock_pool_warm_path_returns_with_subprocess_route(
    tmp_path: Path,
) -> None:
    profile = _profile()
    requests = [
        PoolEvaluationRequest(
            _patch(1),
            "lunar_mare_low_ti",
            "fast",
            candidate_id="spinel-route",
        )
    ]

    results = evaluate_batch(
        requests,
        profile=profile,
        max_workers=1,
        output_root=tmp_path,
        evaluate_fn=_runtime_probe_evaluate,
    )

    assert len(results) == 1
    assert results[0].candidate_id == "spinel-route"


def test_pool_preserves_input_order_under_reversed_completion(tmp_path: Path) -> None:
    requests = [
        PoolEvaluationRequest(_patch(1), "lunar_mare_low_ti", "fast", candidate_id="slow"),
        PoolEvaluationRequest(_patch(2), "lunar_mare_low_ti", "fast", candidate_id="fast"),
    ]

    results = evaluate_batch(
        requests,
        profile=_profile(),
        max_workers=2,
        output_root=tmp_path,
        evaluate_fn=_sleepy_evaluate,
    )

    assert [result.candidate_id for result in results] == ["slow", "fast"]


@pytest.mark.timeout(10)
def test_process_pool_timeout_records_failure_and_continues(
    tmp_path: Path,
    spawnable_process_pool: None,
) -> None:
    requests = [
        PoolEvaluationRequest(_patch(50), "lunar_mare_low_ti", "fast", candidate_id="slow"),
        PoolEvaluationRequest(_patch(2), "lunar_mare_low_ti", "fast", candidate_id="fast"),
    ]

    results = evaluate_batch(
        requests,
        profile=_profile(),
        max_workers=1,
        output_root=tmp_path,
        evaluate_fn=_slow_or_abort_evaluate,
        per_eval_timeout_seconds=0.2,
    )

    assert [result.candidate_id for result in results] == ["slow", "fast"]
    assert results[0].failure_category is FailureCategory.TIMEOUT
    assert results[0].run_reference is not None
    assert results[0].run_reference.reason == "optimizer_eval_timeout"
    assert results[0].run_reference.backend_status == "unavailable"
    assert results[1].feasible is True
    assert results[1].failure_category is None


def test_process_pool_timeout_records_failure_with_fake_executor(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class FakeExecutor:
        def __init__(self, *, max_workers: int | None, initializer: object) -> None:
            self.max_workers = max_workers
            self.initializer = initializer
            self._processes: dict[int, object] = {}
            self.shutdown_calls: list[dict[str, object]] = []

        def submit(self, fn: object, task: object, evaluate_fn: object) -> Future[object]:
            future: Future[object] = Future()
            if getattr(task, "candidate_id", None) == "slow":
                return future
            future.set_result(fn(task, evaluate_fn))
            return future

        def shutdown(self, *, wait: bool, cancel_futures: bool) -> None:
            self.shutdown_calls.append({"wait": wait, "cancel_futures": cancel_futures})

    def fake_wait(
        futures: object, *, return_when: object, timeout: object = None
    ) -> tuple[set[Future[object]], set[Future[object]]]:
        del return_when
        done = {future for future in futures if future.done()}
        if not done and timeout:
            time.sleep(float(timeout))
        return done, set()

    monkeypatch.setattr(pool_module, "ProcessPoolExecutor", FakeExecutor)
    monkeypatch.setattr(pool_module, "wait", fake_wait)

    results = evaluate_batch(
        [
            PoolEvaluationRequest(_patch(50), "lunar_mare_low_ti", "fast", candidate_id="slow"),
            PoolEvaluationRequest(_patch(2), "lunar_mare_low_ti", "fast", candidate_id="fast-a"),
            PoolEvaluationRequest(_patch(3), "lunar_mare_low_ti", "fast", candidate_id="fast-b"),
        ],
        profile=_profile(),
        max_workers=1,
        output_root=tmp_path,
        evaluate_fn=_slow_or_abort_evaluate,
        per_eval_timeout_seconds=0.01,
    )

    assert [result.candidate_id for result in results] == ["slow", "fast-a", "fast-b"]
    assert results[0].failure_category is FailureCategory.TIMEOUT
    assert [result.feasible for result in results[1:]] == [True, True]


@pytest.mark.timeout(10)
def test_process_pool_timeout_kills_worker_process_group(
    tmp_path: Path,
    spawnable_process_pool: None,
) -> None:
    results = evaluate_batch(
        [
            PoolEvaluationRequest(
                _patch(1),
                "lunar_mare_low_ti",
                "fast",
                candidate_id="spawns-child",
            )
        ],
        profile=_profile(),
        max_workers=1,
        output_root=tmp_path,
        evaluate_fn=_grandchild_then_hang_evaluate,
        per_eval_timeout_seconds=0.2,
    )

    time.sleep(1.2)
    assert results[0].failure_category is FailureCategory.TIMEOUT
    assert not (tmp_path / "eval-000000" / "grandchild-survived.txt").exists()


@pytest.mark.timeout(10)
def test_pool_process_termination_kills_child_process_group(tmp_path: Path) -> None:
    survivor = tmp_path / "pool-grandchild-survived.txt"
    script = (
        "import subprocess, sys, time; "
        "subprocess.Popen([sys.executable, '-c', "
        "\"import pathlib, sys, time; time.sleep(1.0); "
        "pathlib.Path(sys.argv[1]).write_text('alive', encoding='utf-8')\", "
        f"{str(survivor)!r}]); "
        "time.sleep(5.0)"
    )
    popen = subprocess.Popen([sys.executable, "-c", script], start_new_session=True)

    class ProcessAdapter:
        pid = popen.pid

        def is_alive(self) -> bool:
            return popen.poll() is None

        def join(self, timeout: float | None = None) -> None:
            try:
                popen.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                return

        def terminate(self) -> None:
            popen.terminate()

        def kill(self) -> None:
            popen.kill()

    time.sleep(0.1)
    pool_module._terminate_pool_process(ProcessAdapter())
    time.sleep(1.2)

    assert popen.poll() is not None
    assert not survivor.exists()


def test_hashseed_result_view_rejects_or_normalizes_unordered_fields() -> None:
    code = """
import json
from simulator.optimize.determinism import deterministic_result_view
from simulator.optimize.evaluate import FailureCategory, ScoredResult

try:
    result = ScoredResult(
        candidate_id=None,
        eval_spec=None,
        cache_key=None,
        feasible=False,
        failure_category=FailureCategory.INFEASIBLE_RECIPE,
        failing_gates={"furnace_temperature", "coating"},
        notes={"beta", "alpha"},
    )
except TypeError as exc:
    print(json.dumps({"kind": "rejected", "message": str(exc)}))
else:
    print(json.dumps({"kind": "view", "view": deterministic_result_view(result)}))
"""
    outputs = []
    for seed in ("1", "987654"):
        env = {**os.environ, "PYTHONHASHSEED": seed}
        completed = subprocess.run(
            [sys.executable, "-c", code],
            check=True,
            cwd=Path(__file__).resolve().parents[1],
            env=env,
            text=True,
            capture_output=True,
            timeout=10,
        )
        outputs.append(json.loads(completed.stdout))

    assert outputs[0] == outputs[1]
    assert outputs[0]["kind"] == "rejected"


def test_evalspec_pickle_roundtrips_nested_runtime_overrides_and_chemistry_kernel() -> None:
    spec = EvalSpec(
        recipe_id="recipe-id",
        feedstock_recipe_digest="feedstock-recipe-digest",
        feedstock_id="lunar_mare_low_ti",
        profile_id="profile-id",
        fidelity="fast",
        code_version="pool-test-code",
        data_digests=_DATA_DIGESTS,
        additives_kg={"flux": {"Na2CO3": 1.25}},
        runtime_campaign_overrides={
            "C0": {"setpoints": {"temperature_C": 1650, "hold": {"hours": 2}}}
        },
        chemistry_kernel={
            "provider": {"name": "builtin", "options": {"strict": True}},
            "intents": ["vapor_pressure", {"name": "mre"}],
        },
    )

    restored = pickle.loads(pickle.dumps(spec))

    assert canonical_evalspec_json(restored) == canonical_evalspec_json(spec)


def test_pool_task_thaws_nested_mappingproxy_profile_for_pickle_boundary(
    tmp_path: Path,
) -> None:
    profile = MappingProxyType(
        {
            **_profile(),
            "objectives": (
                MappingProxyType(
                    {
                        "type": "composition_target",
                        "target": MappingProxyType(
                            {
                                "species_vector": MappingProxyType(
                                    {"Na": "retain", "K": "retain"}
                                )
                            }
                        ),
                    }
                ),
            ),
        }
    )

    task = pool_module._task_from_request(
        0,
        PoolEvaluationRequest(
            _patch(12),
            "lunar_mare_low_ti",
            "fast",
            candidate_id="proxy-profile",
        ),
        profile=profile,
        output_root=tmp_path,
    )

    assert not _contains_mappingproxy(task.profile)
    pickle.dumps(task)


def test_process_pool_thaws_nested_mappingproxy_profile(
    tmp_path: Path,
) -> None:
    profile = MappingProxyType(
        {
            **_profile(),
            "objectives": (
                MappingProxyType(
                    {
                        "type": "composition_target",
                        "target": MappingProxyType(
                            {
                                "species_vector": MappingProxyType(
                                    {"Na": "retain", "K": "retain"}
                                )
                            }
                        ),
                    }
                ),
            ),
        }
    )

    results = evaluate_batch(
        [
            PoolEvaluationRequest(
                _patch(13),
                "lunar_mare_low_ti",
                "fast",
                candidate_id="proxy-profile",
            )
        ],
        profile=profile,
        max_workers=2,
        output_root=tmp_path / "pool",
        evaluate_fn=_mappingproxy_profile_evaluate,
    )

    assert [result.candidate_id for result in results] == ["proxy-profile"]


def test_pool_empty_batch_policy(tmp_path: Path) -> None:
    class SpyStore:
        def __init__(self) -> None:
            self.initialized = False

        def initialize(self) -> None:
            self.initialized = True

    store = SpyStore()

    assert evaluate_batch([], output_root=tmp_path, results_store=store) == ()
    assert store.initialized is False
    assert list(tmp_path.iterdir()) == []


def test_pool_single_item_matches_serial_full_view(tmp_path: Path) -> None:
    request = PoolEvaluationRequest(
        _patch(11),
        "lunar_mare_low_ti",
        "fast",
        candidate_id="single",
    )
    pin_worker_env()
    serial = _fake_evaluate(
        request.patch,
        request.feedstock_id,
        request.fidelity,
        profile=_profile(),
        candidate_id=request.candidate_id,
        output_dir=str(tmp_path / "serial"),
    )

    pooled = evaluate_batch(
        [request],
        profile=_profile(),
        max_workers=1,
        output_root=tmp_path / "pool",
        evaluate_fn=_fake_evaluate,
    )[0]

    assert deterministic_result_view(pooled) == deterministic_result_view(serial)


def test_pool_unavailable_constructor_falls_back_to_serial(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    tmp_path: Path,
) -> None:
    def unavailable_executor(*, max_workers: int | None, initializer: object) -> object:
        raise PermissionError("sandbox denies process spawn")

    monkeypatch.setattr(pool_module, "ProcessPoolExecutor", unavailable_executor)
    caplog.set_level("WARNING", logger="simulator.optimize.pool")
    for name in THREAD_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv(pool_module._WORKER_OUTPUT_ENV, raising=False)

    result = evaluate_batch(
        [PoolEvaluationRequest(_patch(15), "lunar_mare_low_ti", "fast", candidate_id="a")],
        profile=_profile(),
        max_workers=2,
        output_root=tmp_path / "pool",
        evaluate_fn=_fake_evaluate,
    )[0]

    assert result.candidate_id == "a"
    assert result.objectives is not None
    assert result.objectives.values[0].value == 15.0
    assert result.run_reference is not None
    assert result.run_reference.trace["thread_env"] == {
        name: "1" for name in THREAD_ENV_VARS
    }
    assert (tmp_path / "pool" / "eval-000000" / "artifact.txt").exists()
    assert all(os.environ.get(name) is None for name in THREAD_ENV_VARS)
    assert os.environ.get(pool_module._WORKER_OUTPUT_ENV) is None
    assert "falling back to serial optimizer evaluation" in caplog.text


def test_pool_unavailable_submit_falls_back_to_serial(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class SubmitDeniedExecutor:
        def __init__(self, *, max_workers: int | None, initializer: object) -> None:
            self.max_workers = max_workers
            self.initializer = initializer
            self.shutdown_calls: list[dict[str, object]] = []

        def submit(self, fn: object, task: object, evaluate_fn: object) -> Future[object]:
            raise PermissionError("sandbox denies process semlock")

        def shutdown(self, *, wait: bool, cancel_futures: bool) -> None:
            self.shutdown_calls.append({"wait": wait, "cancel_futures": cancel_futures})

    executor = SubmitDeniedExecutor(max_workers=2, initializer=None)

    def executor_factory(*, max_workers: int | None, initializer: object) -> SubmitDeniedExecutor:
        assert max_workers == 2
        return executor

    monkeypatch.setattr(pool_module, "ProcessPoolExecutor", executor_factory)

    results = evaluate_batch(
        [
            PoolEvaluationRequest(_patch(16), "lunar_mare_low_ti", "fast", candidate_id="a"),
            PoolEvaluationRequest(_patch(17), "lunar_mare_low_ti", "fast", candidate_id="b"),
        ],
        profile=_profile(),
        max_workers=2,
        output_root=tmp_path / "pool",
        evaluate_fn=_fake_evaluate,
    )

    assert [result.candidate_id for result in results] == ["a", "b"]
    assert executor.shutdown_calls == [{"wait": False, "cancel_futures": True}]


def test_pool_uses_isolated_output_dirs_without_chdir(tmp_path: Path) -> None:
    cwd = os.getcwd()
    requests = [
        PoolEvaluationRequest(_patch(3), "lunar_mare_low_ti", "fast", candidate_id="a"),
        PoolEvaluationRequest(_patch(4), "lunar_mare_low_ti", "fast", candidate_id="b"),
    ]

    results = evaluate_batch(
        requests,
        profile=_profile(),
        max_workers=2,
        output_root=tmp_path,
        evaluate_fn=_fake_evaluate,
    )

    assert os.getcwd() == cwd
    output_dirs = {
        Path(result.run_reference.trace["output_dir"])
        for result in results
        if result.run_reference is not None
    }
    assert output_dirs == {tmp_path / "eval-000000", tmp_path / "eval-000001"}
    assert all((path / "artifact.txt").exists() for path in output_dirs)
    assert {
        result.run_reference.trace["cwd"]
        for result in results
        if result.run_reference is not None
    } == {cwd}


def test_pin_worker_env_runs_in_workers(tmp_path: Path) -> None:
    result = evaluate_batch(
        [PoolEvaluationRequest(_patch(5), "lunar_mare_low_ti", "fast", candidate_id="a")],
        profile=_profile(),
        max_workers=1,
        output_root=tmp_path,
        evaluate_fn=_fake_evaluate,
    )[0]

    assert result.run_reference is not None
    assert result.run_reference.trace["thread_env"] == {name: "1" for name in THREAD_ENV_VARS}


def test_engine_bug_abort_propagates_from_worker(tmp_path: Path) -> None:
    with pytest.raises(EngineBugAbort, match="worker engine bug"):
        evaluate_batch(
            [PoolEvaluationRequest(_patch(6), "lunar_mare_low_ti", "fast", candidate_id="bad")],
            profile=_profile(),
            max_workers=1,
            output_root=tmp_path,
            evaluate_fn=_engine_bug_evaluate,
        )


def test_worker_abort_survives_teardown_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class BrokenShutdownExecutor:
        def __init__(self, *, max_workers: int | None, initializer: object) -> None:
            self.max_workers = max_workers
            self.initializer = initializer
            self.future: Future[object] | None = None

        def submit(self, fn: object, task: object, evaluate_fn: object) -> Future[object]:
            future: Future[object] = Future()
            future.set_result(
                {
                    "kind": "abort",
                    "index": 0,
                    "abort": {
                        "message": "original worker bug",
                        "category": FailureCategory.ENGINE_BUG,
                        "patch": _patch(6),
                        "candidate_id": "bad",
                        "eval_spec": None,
                        "cache_key": None,
                    },
                }
            )
            self.future = future
            return future

        def shutdown(self, *, wait: bool, cancel_futures: bool) -> None:
            raise RuntimeError("teardown boom")

    executor = BrokenShutdownExecutor(max_workers=1, initializer=None)

    def executor_factory(
        *, max_workers: int | None, initializer: object
    ) -> BrokenShutdownExecutor:
        return executor

    def fake_wait(
        futures: object, *, return_when: object, timeout: object = None
    ) -> tuple[set[Future[object]], set[Future[object]]]:
        del timeout
        return set(futures), set()

    monkeypatch.setattr(pool_module, "ProcessPoolExecutor", executor_factory)
    monkeypatch.setattr(pool_module, "wait", fake_wait)

    with pytest.raises(EngineBugAbort, match="original worker bug") as exc_info:
        evaluate_batch(
            [PoolEvaluationRequest(_patch(6), "lunar_mare_low_ti", "fast", candidate_id="bad")],
            profile=_profile(),
            max_workers=1,
            output_root=tmp_path,
            evaluate_fn=_fake_evaluate,
        )

    assert type(exc_info.value) is EngineBugAbort
    assert any("teardown boom" in note for note in exc_info.value.__notes__)


def test_parent_process_is_single_result_store_writer(tmp_path: Path) -> None:
    store = ResultStore(
        tmp_path / "results.sqlite",
        current_code_version="pool-test-code",
        current_data_digests=_DATA_DIGESTS,
    )
    requests = [
        PoolEvaluationRequest(_patch(7), "lunar_mare_low_ti", "fast", candidate_id="a"),
        PoolEvaluationRequest(_patch(8), "lunar_mare_low_ti", "fast", candidate_id="b"),
    ]

    results = evaluate_batch(
        requests,
        profile=_profile(),
        max_workers=2,
        output_root=tmp_path / "pool",
        results_store=store,
        evaluate_fn=_fake_evaluate,
        created_at="2026-05-31T00:00:00+00:00",
    )

    persisted = [store.lookup(result.eval_spec) for result in results]
    assert [result is not None for result in persisted] == [True, True]
    assert {
        result.candidate_id: deterministic_result_view(result)
        for result in persisted
        if result is not None
    } == {result.candidate_id: deterministic_result_view(result) for result in results}


def test_pool_duplicate_cache_key_policy(tmp_path: Path) -> None:
    store = ResultStore(
        tmp_path / "results.sqlite",
        current_code_version="pool-test-code",
        current_data_digests=_DATA_DIGESTS,
    )
    requests = [
        PoolEvaluationRequest(_patch(12), "lunar_mare_low_ti", "fast", candidate_id="first"),
        PoolEvaluationRequest(_patch(12), "lunar_mare_low_ti", "fast", candidate_id="second"),
    ]

    results = evaluate_batch(
        requests,
        profile=_profile(),
        max_workers=2,
        output_root=tmp_path / "pool",
        results_store=store,
        evaluate_fn=_fake_evaluate,
        created_at="2026-05-31T00:00:00+00:00",
    )

    assert [result.candidate_id for result in results] == ["first", "second"]
    assert (tmp_path / "pool" / "eval-000000" / "artifact.txt").exists()
    assert (tmp_path / "pool" / "eval-000001" / "artifact.txt").exists()
    persisted = store.lookup(results[0].eval_spec)
    assert persisted is not None
    assert persisted.candidate_id == "second"


def test_pool_mixed_feasible_infeasible_abort_no_partial_store_no_hang(
    spawnable_process_pool: None,
    tmp_path: Path,
) -> None:
    store_path = tmp_path / "results.sqlite"
    store = ResultStore(
        store_path,
        current_code_version="pool-test-code",
        current_data_digests=_DATA_DIGESTS,
    )
    requests = [
        PoolEvaluationRequest(_patch(50), "lunar_mare_low_ti", "fast", candidate_id="slow"),
        PoolEvaluationRequest(_patch(-1), "lunar_mare_low_ti", "fast", candidate_id="infeasible"),
        PoolEvaluationRequest(_patch(99), "lunar_mare_low_ti", "fast", candidate_id="abort"),
    ]

    started = time.monotonic()
    with pytest.raises(EngineBugAbort, match="abort for candidate abort"):
        evaluate_batch(
            requests,
            profile=_profile(),
            max_workers=2,
            output_root=tmp_path / "pool",
            results_store=store,
            evaluate_fn=_slow_or_abort_evaluate,
        )

    assert time.monotonic() - started < 2.0
    with sqlite3.connect(store_path) as conn:
        count = conn.execute("SELECT COUNT(*) FROM results").fetchone()[0]
    assert count == 0


@pytest.mark.skipif(os.name != "posix", reason="os._exit crash sentinel is POSIX-only")
def test_pool_worker_hard_crash_reports_candidate(
    spawnable_process_pool: None,
    tmp_path: Path,
) -> None:
    with pytest.raises(RuntimeError, match="candidate_id='crashy' index=0"):
        evaluate_batch(
            [
                PoolEvaluationRequest(
                    _patch(13),
                    "lunar_mare_low_ti",
                    "fast",
                    candidate_id="crashy",
                )
            ],
            profile=_profile(),
            max_workers=1,
            output_root=tmp_path,
            evaluate_fn=_hard_crash_evaluate,
        )


def test_pool_nonpicklable_request_payload_preflight(tmp_path: Path) -> None:
    with pytest.raises(
        TypeError,
        match=r"candidate_id='bad-payload' index=0 profile\['bad'\]",
    ):
        evaluate_batch(
            [
                PoolEvaluationRequest(
                    _patch(14),
                    "lunar_mare_low_ti",
                    "fast",
                    profile={"bad": lambda value: value},
                    candidate_id="bad-payload",
                )
            ],
            max_workers=1,
            output_root=tmp_path,
            evaluate_fn=_fake_evaluate,
        )


def test_pool_large_batch_bounded_inflight(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    class FakeExecutor:
        def __init__(self, *, max_workers: int | None, initializer: object) -> None:
            self.max_workers = max_workers
            self.initializer = initializer
            self.active = 0
            self.max_active = 0
            self.shutdown_calls: list[dict[str, object]] = []

        def submit(self, fn: object, task: object, evaluate_fn: object) -> Future[object]:
            future: Future[object] = Future()
            future._pool_task = task
            future._evaluate_fn = evaluate_fn
            future._owner = self
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            return future

        def shutdown(self, *, wait: bool, cancel_futures: bool) -> None:
            self.shutdown_calls.append({"wait": wait, "cancel_futures": cancel_futures})

    fake_executor = FakeExecutor(max_workers=2, initializer=None)

    def executor_factory(*, max_workers: int | None, initializer: object) -> FakeExecutor:
        assert max_workers == 2
        return fake_executor

    def fake_wait(
        futures: object, *, return_when: object, timeout: object = None
    ) -> tuple[set[Future[object]], set[Future[object]]]:
        del timeout
        future = next(iter(futures))
        owner = future._owner
        if not future.done():
            outcome = pool_module._evaluate_pool_task(future._pool_task, future._evaluate_fn)
            future.set_result(outcome)
            owner.active -= 1
        return {future}, set(futures) - {future}

    monkeypatch.setattr(pool_module, "ProcessPoolExecutor", executor_factory)
    monkeypatch.setattr(pool_module, "wait", fake_wait)
    requests = [
        PoolEvaluationRequest(_patch(value), "lunar_mare_low_ti", "fast", candidate_id=f"c{value}")
        for value in range(20)
    ]

    results = evaluate_batch(
        requests,
        profile=_profile(),
        max_workers=2,
        output_root=tmp_path,
        evaluate_fn=_fake_evaluate,
    )

    assert len(results) == len(requests)
    assert fake_executor.max_active <= 4


def test_cross_process_float_drift_sentinel_serial_vs_pool(tmp_path: Path) -> None:
    profile = _profile()
    requests = [
        PoolEvaluationRequest(_patch(21), "lunar_mare_low_ti", "fast", candidate_id="a"),
        PoolEvaluationRequest(_patch(22), "lunar_mare_low_ti", "fast", candidate_id="b"),
    ]
    serial_views = [
        deterministic_result_view(
            _float_reduction_evaluate(
                request.patch,
                request.feedstock_id,
                request.fidelity,
                profile=profile,
                candidate_id=request.candidate_id,
                output_dir=str(tmp_path / "serial" / str(request.candidate_id)),
            )
        )
        for request in requests
    ]

    for repeat in range(2):
        pooled = evaluate_batch(
            requests,
            profile=profile,
            max_workers=2,
            output_root=tmp_path / f"pool-{repeat}",
            evaluate_fn=_float_reduction_evaluate,
        )
        assert [deterministic_result_view(result) for result in pooled] == serial_views

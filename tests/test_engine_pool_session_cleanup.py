import gc
import multiprocessing
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import weakref
from pathlib import Path

import pytest

import conftest
import simulator.engine_pool as engine_pool
from simulator.engine_pool import (
    ENGINE_POOL_AUTOMATIC_TOTAL_BUDGET_S,
    EngineWorkerDied,
    EngineWorkerTimeout,
    EngineWorkerPool,
    WarmEngineWorker,
    close_all_engine_pools,
)


def _bootstrap_cleanup_worker():
    return None, "cleanup-ready"


def _handle_cleanup_request(_resource, request, _errlog):
    if isinstance(request, dict) and request.get("sleep_forever"):
        while True:
            time.sleep(0.05)
    if isinstance(request, dict) and request.get("delay"):
        time.sleep(float(request["delay"]))
    return request


def _cleanup_worker(_index, *, call_timeout_s=1.0):
    return WarmEngineWorker(
        name="session-cleanup-test",
        bootstrap=_bootstrap_cleanup_worker,
        handler=_handle_cleanup_request,
        startup_timeout_s=2.0,
        call_timeout_s=call_timeout_s,
    )


def _direct_spawn_child_pids():
    return {
        child.pid
        for child in multiprocessing.active_children()
        if child.pid is not None and child.is_alive()
    }


def _spawn_child_with_abandoned_pool(connection):
    pool = EngineWorkerPool(_cleanup_worker, size=1)
    connection.send(pool.workers[0].process.pid)
    connection.close()


def _run_cleanup_scenario_in_child(scenario):
    context = multiprocessing.get_context("spawn")
    child = context.Process(target=scenario)
    child.start()
    child.join(timeout=ENGINE_POOL_AUTOMATIC_TOTAL_BUDGET_S + 15.0)
    if child.is_alive():
        child.terminate()
        child.join(timeout=5.0)
    assert child.exitcode == 0, (
        f"isolated cleanup scenario {scenario.__name__} exited "
        f"with status {child.exitcode}"
    )


def _wait_until_running(future, *, timeout_s=5.0):
    deadline = time.monotonic() + timeout_s
    while not future.running() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert future.running()


def _close_all_reaps_pools_after_callers_drop_references():
    close_all_engine_pools(cancel_pending=True)
    assert not _direct_spawn_child_pids()

    pool = EngineWorkerPool(_cleanup_worker, size=2)
    child_pids = {worker.process.pid for worker in pool.workers}
    assert len(child_pids) == 2

    pool_ref = weakref.ref(pool)
    del pool
    gc.collect()

    assert pool_ref() is not None
    assert len(engine_pool._LIVE_ENGINE_POOLS) == 1
    assert child_pids <= _direct_spawn_child_pids()

    assert close_all_engine_pools(cancel_pending=True) == 1
    gc.collect()

    assert not engine_pool._LIVE_ENGINE_POOLS
    assert not _direct_spawn_child_pids(), f"spawn children remain under pid {os.getpid()}"
    assert pool_ref() is None


def test_close_all_reaps_pools_after_callers_drop_references():
    _run_cleanup_scenario_in_child(
        _close_all_reaps_pools_after_callers_drop_references
    )


def _sessionfinish_interrupts_in_flight_job_within_automatic_budget():
    pool = EngineWorkerPool(
        lambda index: _cleanup_worker(index, call_timeout_s=30.0),
        size=1,
    )
    future = pool.submit({"delay": 30.0}, timeout_s=30.0)
    _wait_until_running(future)
    worker_pid = pool.workers[0].process.pid

    started_at = time.monotonic()
    conftest.pytest_sessionfinish(None, 0)
    elapsed = time.monotonic() - started_at

    assert elapsed <= ENGINE_POOL_AUTOMATIC_TOTAL_BUDGET_S
    assert future.done()
    with pytest.raises(EngineWorkerDied, match="worker exited without a result") as caught:
        future.result(timeout=0.1)
    assert caught.value.phase == "call"
    with pytest.raises(ProcessLookupError):
        os.kill(worker_pid, 0)


def test_sessionfinish_interrupts_in_flight_job_within_automatic_budget():
    _run_cleanup_scenario_in_child(
        _sessionfinish_interrupts_in_flight_job_within_automatic_budget
    )


def test_aborting_running_request_reaps_and_restarts_its_worker_slot():
    pool = EngineWorkerPool(_cleanup_worker, size=1)
    try:
        first = pool.submit({'sleep_forever': True}, timeout_s=30.0)
        _wait_until_running(first)
        worker = pool.workers[0]
        first_pid = worker.process.pid

        assert first.cancel() is False
        assert pool._abort_request(first) is True
        with pytest.raises(EngineWorkerDied, match='worker exited without a result'):
            first.result(timeout=3.0)
        assert worker.process is None
        with pytest.raises(ProcessLookupError):
            os.kill(first_pid, 0)

        second = pool.submit({'value': 'restarted'}, timeout_s=3.0)
        assert second.result(timeout=5.0) == {'value': 'restarted'}
        assert worker.process.pid != first_pid
    finally:
        pool.close(cancel_pending=True, timeout_s=3.0)


def _registry_cancel_escalates_concurrent_graceful_close():
    registry_before = set(engine_pool._LIVE_ENGINE_POOLS)
    pool = EngineWorkerPool(
        lambda index: _cleanup_worker(index, call_timeout_s=30.0),
        size=1,
    )
    assert set(engine_pool._LIVE_ENGINE_POOLS) - registry_before == {pool}
    future = pool.submit({"delay": 30.0}, timeout_s=30.0)
    _wait_until_running(future)
    worker_pid = pool.workers[0].process.pid

    graceful_close = threading.Thread(target=pool.close)
    graceful_close.start()
    time.sleep(0.05)
    assert graceful_close.is_alive()

    close_all_engine_pools(cancel_pending=True)
    graceful_close.join(timeout=ENGINE_POOL_AUTOMATIC_TOTAL_BUDGET_S)

    assert not graceful_close.is_alive()
    assert pool not in engine_pool._LIVE_ENGINE_POOLS
    with pytest.raises(EngineWorkerDied, match="worker exited without a result") as caught:
        future.result(timeout=0.1)
    assert caught.value.phase == "call"
    with pytest.raises(ProcessLookupError):
        os.kill(worker_pid, 0)


def test_registry_cancel_escalates_concurrent_graceful_close():
    _run_cleanup_scenario_in_child(
        _registry_cancel_escalates_concurrent_graceful_close
    )


def test_spawn_child_atexit_reaps_child_local_pool():
    context = multiprocessing.get_context("spawn")
    parent_connection, child_connection = context.Pipe(duplex=False)
    child = context.Process(
        target=_spawn_child_with_abandoned_pool,
        args=(child_connection,),
    )
    child.start()
    child_connection.close()
    worker_pid = parent_connection.recv()
    parent_connection.close()

    child.join(timeout=ENGINE_POOL_AUTOMATIC_TOTAL_BUDGET_S + 5.0)

    assert child.exitcode == 0
    with pytest.raises(ProcessLookupError):
        os.kill(worker_pid, 0)


def _close_all_surfaces_bounded_shutdown_timeout():
    class StuckPool:
        def close(self, **_kwargs):
            time.sleep(1.0)

        def _request_cancel_pending(self):
            return None

    pool = StuckPool()
    engine_pool._LIVE_ENGINE_POOLS.add(pool)
    started_at = time.monotonic()
    try:
        with pytest.raises(EngineWorkerTimeout) as exc_info:
            close_all_engine_pools(
                cancel_pending=True,
                per_pool_timeout_s=0.05,
                total_timeout_s=0.1,
            )
    finally:
        engine_pool._LIVE_ENGINE_POOLS.discard(pool)

    assert time.monotonic() - started_at < 0.5
    assert exc_info.value.phase == "shutdown"


def test_close_all_surfaces_bounded_shutdown_timeout():
    _run_cleanup_scenario_in_child(_close_all_surfaces_bounded_shutdown_timeout)


def test_cleanup_scenarios_preserve_borrowed_vaporock_pool(monkeypatch):
    from engines.vaporock import VapoRockProvider
    from simulator.melt_backend import vaporock as vaporock_module
    from tests.test_vaporock_backend import (
        test_vaporock_iw_literature_grid_residuals_are_explicit,
    )

    # This regression owns a private session cache so its teardown cannot close
    # a backend borrowed by another test in the pytest process.
    local_cache = {}
    monkeypatch.setattr(vaporock_module, "_SESSION_BACKEND_CACHE", local_cache)
    try:
        provider = VapoRockProvider()
        backend = provider._ensure_backend()
        if (
            backend is None
            or not backend.is_available()
            or not backend.uses_warm_pool
        ):
            pytest.skip("VapoRock warm backend unavailable")

        for scenario in (
            _close_all_reaps_pools_after_callers_drop_references,
            _sessionfinish_interrupts_in_flight_job_within_automatic_budget,
            _registry_cancel_escalates_concurrent_graceful_close,
            _close_all_surfaces_bounded_shutdown_timeout,
        ):
            _run_cleanup_scenario_in_child(scenario)

        assert provider._ensure_backend() is backend
        assert backend.is_available()
        assert not backend._warm_pool._closed
        test_vaporock_iw_literature_grid_residuals_are_explicit()
    finally:
        vaporock_module.clear_session_backend_cache()


def test_lazily_initialized_session_provider_survives_module_boundary():
    test_dir = Path(
        tempfile.mkdtemp(
            prefix="t1229-provider-",
            dir=os.environ.get("TMPDIR") or tempfile.gettempdir(),
        )
    )
    try:
        (test_dir / "_t1229_test_plugin.py").write_text(
            '''
from pathlib import Path
from types import SimpleNamespace

import pytest
import simulator.engine_pool as engine_pool
import simulator.melt_backend.magemin as magemin_module
from engines.magemin.provider import MAGEMinShadowProvider


class FakePool:
    def __init__(self, _worker_factory, *, size):
        self.workers = tuple(
            SimpleNamespace(name=f"MAGEMin subprocess pool slot {i}")
            for i in range(size)
        )
        self.closed = False
        engine_pool._LIVE_ENGINE_POOLS.add(self)

    def close(self, *, cancel_pending=False, timeout_s=None):
        self.closed = True
        engine_pool._LIVE_ENGINE_POOLS.discard(self)

    def _request_cancel_pending(self):
        return None


@pytest.fixture(scope="session")
def lazy_provider():
    original_pool = magemin_module.EngineWorkerPool
    original_locate = magemin_module.MAGEMinBackend._locate_binary
    original_import = magemin_module.MAGEMinBackend._import_magemin_bridge
    magemin_module.EngineWorkerPool = FakePool
    magemin_module.MAGEMinBackend._locate_binary = staticmethod(
        lambda _explicit: Path("/fake/MAGEMin")
    )
    magemin_module.MAGEMinBackend._import_magemin_bridge = (
        lambda self, *, requested: ("subprocess", None)
    )
    provider = MAGEMinShadowProvider()
    yield provider
    if provider._backend is not None:
        provider._backend.close()
    magemin_module.EngineWorkerPool = original_pool
    magemin_module.MAGEMinBackend._locate_binary = staticmethod(original_locate)
    magemin_module.MAGEMinBackend._import_magemin_bridge = original_import
''',
            encoding="utf-8",
        )
        (test_dir / "test_01_provider_open.py").write_text(
            '''
def test_provider_opens_its_pool_lazily(lazy_provider):
    backend = lazy_provider._ensure_backend()
    assert backend is not None
    lazy_provider._observed_pool = backend._subprocess_pool
    assert lazy_provider._observed_pool.closed is False
''',
            encoding="utf-8",
        )
        (test_dir / "test_02_provider_reuse.py").write_text(
            '''
def test_session_provider_pool_survives_module_teardown(lazy_provider):
    backend = lazy_provider._ensure_backend()
    assert backend is not None
    assert backend._subprocess_pool is lazy_provider._observed_pool
    assert lazy_provider._observed_pool.closed is False
''',
            encoding="utf-8",
        )
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                "--rootdir",
                str(Path(__file__).resolve().parents[1]),
                "-o",
                "addopts=",
                "-p",
                "conftest",
                "-p",
                "_t1229_test_plugin",
                "-p",
                "no:cacheprovider",
                str(test_dir),
            ],
            cwd=Path(__file__).resolve().parents[1],
            env={
                **os.environ,
                "PYTHONPATH": os.pathsep.join(
                    filter(
                        None,
                        (
                            str(test_dir),
                            os.environ.get("PYTHONPATH", ""),
                        ),
                    )
                ),
            },
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr
    finally:
        shutil.rmtree(test_dir)

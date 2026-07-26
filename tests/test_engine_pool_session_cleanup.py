import gc
import multiprocessing
import os
import threading
import time
import weakref

import pytest

import conftest
import simulator.engine_pool as engine_pool
from simulator.engine_pool import (
    ENGINE_POOL_AUTOMATIC_TOTAL_BUDGET_S,
    EngineWorkerTimeout,
    EngineWorkerPool,
    WarmEngineWorker,
    close_all_engine_pools,
)


def _bootstrap_cleanup_worker():
    return None, "cleanup-ready"


def _handle_cleanup_request(_resource, request, _errlog):
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


def _wait_until_running(future, *, timeout_s=5.0):
    deadline = time.monotonic() + timeout_s
    while not future.running() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert future.running()


def test_close_all_reaps_pools_after_callers_drop_references():
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


def test_sessionfinish_interrupts_in_flight_job_within_automatic_budget():
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
    with pytest.raises(RuntimeError, match="worker exited without a result"):
        future.result(timeout=0.1)
    with pytest.raises(ProcessLookupError):
        os.kill(worker_pid, 0)


def test_registry_cancel_escalates_concurrent_graceful_close():
    pool = EngineWorkerPool(
        lambda index: _cleanup_worker(index, call_timeout_s=30.0),
        size=1,
    )
    future = pool.submit({"delay": 30.0}, timeout_s=30.0)
    _wait_until_running(future)
    worker_pid = pool.workers[0].process.pid

    graceful_close = threading.Thread(target=pool.close)
    graceful_close.start()
    time.sleep(0.05)
    assert graceful_close.is_alive()

    assert close_all_engine_pools(cancel_pending=True) == 1
    graceful_close.join(timeout=ENGINE_POOL_AUTOMATIC_TOTAL_BUDGET_S)

    assert not graceful_close.is_alive()
    with pytest.raises(RuntimeError, match="worker exited without a result"):
        future.result(timeout=0.1)
    with pytest.raises(ProcessLookupError):
        os.kill(worker_pid, 0)


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


def test_close_all_surfaces_bounded_shutdown_timeout():
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

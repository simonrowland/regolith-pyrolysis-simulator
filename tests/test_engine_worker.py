import json
import math
import multiprocessing
import os
import signal
import threading
import time

import pytest

from simulator.engine_pool import (
    ENGINE_POOL_AUTOMATIC_PER_POOL_BUDGET_S,
    ENGINE_WORKER_CANCEL_POLL_S,
    EngineWorkerRemoteError,
    EngineWorkerPool,
    EngineWorkerTimeout,
    WarmEngineWorker,
    _pool_ready_wait_s,
)


def _bootstrap_test_worker():
    return {'calls': 0, 'scratch': []}, 'test-ready'


def _handle_test_request(resource, request, _errlog):
    if request.get('nested_timeout'):
        raise EngineWorkerTimeout('nested native engine', 0.125, phase='job')
    if request.get('raise'):
        raise ValueError('tainted native request')
    delay = float(request.get('delay', 0.0))
    if delay:
        time.sleep(delay)
    resource['calls'] += 1
    resource['scratch'].clear()
    resource['scratch'].extend(sorted(request['composition'].items()))
    return json.dumps({
        'T_C': request['T_C'],
        'P_bar': request['P_bar'],
        'fO2_log': request['fO2_log'],
        'composition': resource['scratch'],
    }, sort_keys=True, separators=(',', ':')).encode()


def _test_worker(*, timeout_s=1.0):
    return WarmEngineWorker(
        name='test engine',
        bootstrap=_bootstrap_test_worker,
        handler=_handle_test_request,
        startup_timeout_s=2.0,
        call_timeout_s=timeout_s,
    )


def _points():
    points = []
    for index in range(22):
        points.append({
            'T_C': 1100.0 + 17.0 * index,
            'P_bar': 0.01 + 0.25 * (index % 5),
            'fO2_log': -12.0 + 0.5 * (index % 7),
            'composition': {
                'SiO2': 45.0 + index,
                'FeO': 15.0 - 0.25 * index,
                'MgO': 10.0 + 0.1 * (index % 3),
            },
        })
    points.extend((dict(points[3]), dict(points[4])))
    return points


def test_warm_worker_matches_24_cold_workers_byte_for_byte():
    points = _points()
    cold = []
    for point in points:
        worker = _test_worker()
        worker.start()
        try:
            cold.append(worker.call(point))
        finally:
            worker.close()

    warm_worker = _test_worker()
    warm_worker.start()
    try:
        warm = [warm_worker.call(point) for point in points]
        assert warm == cold
        assert warm[3] == warm[-2]
        assert warm[4] == warm[-1]
        assert warm_worker.start_count == 1
    finally:
        warm_worker.close()


def test_timeout_discards_slot_and_next_call_respawns():
    worker = _test_worker(timeout_s=0.05)
    worker.start()
    with pytest.raises(EngineWorkerTimeout, match='hard timeout') as exc_info:
        worker.call({**_points()[0], 'delay': 0.2})
    assert exc_info.value.phase == 'job'
    assert exc_info.value.timeout_s == pytest.approx(0.05)
    assert worker.process is None

    assert worker.call(_points()[1], timeout_s=1.0)
    assert worker.start_count == 2
    worker.close()


def test_remote_error_discards_tainted_slot_before_next_call():
    worker = _test_worker()
    worker.start()
    with pytest.raises(EngineWorkerRemoteError, match='tainted native request'):
        worker.call({**_points()[0], 'raise': True})
    assert worker.process is None

    assert worker.call(_points()[1], timeout_s=1.0)
    assert worker.start_count == 2
    worker.close()


def test_nested_typed_timeout_crosses_worker_boundary_and_respawns():
    worker = _test_worker()
    worker.start()
    with pytest.raises(EngineWorkerTimeout) as exc_info:
        worker.call({**_points()[0], 'nested_timeout': True})
    assert exc_info.value.worker_name == 'nested native engine'
    assert exc_info.value.timeout_s == pytest.approx(0.125)
    assert worker.process is None

    assert worker.call(_points()[1], timeout_s=1.0)
    assert worker.start_count == 2
    worker.close()


def test_queue_pool_keeps_draining_while_one_slot_hangs():
    with EngineWorkerPool(lambda _index: _test_worker(timeout_s=0.08), size=2) as pool:
        hung = pool.submit({**_points()[0], 'delay': 0.3})
        fast = [pool.submit(point) for point in _points()[1:7]]
        assert all(future.result(timeout=1.0) for future in fast)
        with pytest.raises(EngineWorkerTimeout):
            hung.result(timeout=1.0)


def test_submit_racing_close_is_completed_or_rejected_without_orphan():
    pool = EngineWorkerPool(lambda _index: _test_worker(), size=1)
    original_put = pool._queue.put
    submit_entered_put = threading.Event()
    release_submit = threading.Event()

    def gated_put(item, *args, **kwargs):
        if item is not None:
            submit_entered_put.set()
            assert release_submit.wait(timeout=2.0)
        return original_put(item, *args, **kwargs)

    pool._queue.put = gated_put
    outcome = {}

    def submit():
        try:
            outcome['future'] = pool.submit(_points()[0])
        except BaseException as exc:
            outcome['error'] = exc

    submit_thread = threading.Thread(target=submit)
    submit_thread.start()
    assert submit_entered_put.wait(timeout=1.0)
    close_thread = threading.Thread(target=pool.close)
    close_thread.start()
    time.sleep(0.05)
    assert close_thread.is_alive()
    release_submit.set()
    submit_thread.join(timeout=2.0)
    close_thread.join(timeout=2.0)

    assert not submit_thread.is_alive()
    assert not close_thread.is_alive()
    assert 'error' not in outcome
    assert outcome['future'].result(timeout=1.0)
    with pytest.raises(RuntimeError, match='pool is closed'):
        pool.submit(_points()[1])


def test_default_close_drains_request_longer_than_old_join_cutoff():
    pool = EngineWorkerPool(lambda _index: _test_worker(timeout_s=4.0), size=1)
    future = pool.submit({**_points()[0], 'delay': 2.2}, timeout_s=4.0)
    deadline = time.monotonic() + 1.0
    while not future.running() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert future.running()

    pool.close()

    assert future.result(timeout=0.1)


def test_canceling_close_fails_in_flight_request_without_orphan():
    # 2026-07-25 t-419 load-robustness: every window here is deliberately
    # huge so the ONLY reachable outcome is close() killing the in-flight
    # request. The old 0.5 s delay / 2.0 s wall / 1.0 s running-deadline
    # trio raced real cold-start and scheduling latency on a loaded box:
    # the request could finish before close() (no error raised) or the
    # call wall could fire first (wrong error). delay=30 s can never
    # complete and the 30 s wall can never fire before the kill (which
    # lands in milliseconds), so healthy runtime is unchanged and the
    # outcome is deterministic under any load.
    pool = EngineWorkerPool(lambda _index: _test_worker(timeout_s=30.0), size=1)
    future = pool.submit({**_points()[0], 'delay': 30.0}, timeout_s=30.0)
    deadline = time.monotonic() + 30.0
    while not future.running() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert future.running()
    # Milestone review: the test's name promises "without_orphan" but the
    # orphan property was never asserted — a close() that abandoned the
    # worker (pipe-close without kill) passed identically. Capture the
    # live worker pid while in flight so the kill can be proven below.
    worker_pid = pool._workers[0].process.pid

    pool.close(cancel_pending=True)

    assert future.done()
    with pytest.raises(RuntimeError, match='worker exited without a result'):
        future.result(timeout=0.1)
    # The orphan assertion itself: after close() the worker process must
    # be gone (close joins/reaps, so a live pid here is a real orphan).
    with pytest.raises(ProcessLookupError):
        os.kill(worker_pid, 0)


@pytest.mark.timeout(15)
def test_constructor_refuses_when_worker_thread_never_reports_ready():
    """Pin: a silent worker thread must not park the constructor forever.

    The test thread joins a constructor thread — it does not call
    ready.get() itself — because SIGALRM cannot interrupt an untimed
    lock acquire. Against unbounded ready.get() the constructor thread
    is still alive after the derived wait (the red-on-HEAD pin).
    """
    import simulator.engine_pool as engine_pool

    startup_timeout_s = 1.0
    size = 1

    spawned_pids = []

    class SilentReadyWorker:
        """Spawns a child, then never returns from start() so ready.put is skipped."""

        def __init__(self, index):
            self.name = f'silent-ready-test-{index}'
            self.startup_timeout_s = startup_timeout_s
            self.process = None
            self._pending_reap = []
            self._halt = threading.Event()

        def start(self, *, timeout_s=None):
            context = multiprocessing.get_context('spawn')
            process = context.Process(target=time.sleep, args=(3600,), daemon=True)
            process.start()
            self.process = process
            spawned_pids.append(process.pid)
            # Park until close() disables the slot. Returning here still
            # does not ready.put() until after the constructor deadline
            # has already expired and teardown has begun.
            self._halt.wait(timeout=3600)

        def request_disable(self):
            self._halt.set()
            process = self.process
            self.process = None
            if process is not None:
                self._pending_reap.append(process)
                if process.is_alive():
                    try:
                        process.kill()
                    except OSError:
                        pass

        def _reap_pending(self, *, timeout_s):
            deadline = time.monotonic() + max(0.0, float(timeout_s))
            remaining = []
            for process in self._pending_reap:
                process.join(timeout=max(0.0, deadline - time.monotonic()))
                if process.is_alive():
                    try:
                        process.kill()
                    except OSError:
                        pass
                    process.join(timeout=max(0.0, deadline - time.monotonic()))
                if process.is_alive():
                    remaining.append(process)
            self._pending_reap = remaining
            return not remaining

        def close(self):
            self.request_disable()
            self._reap_pending(timeout_s=1.0)

    def factory(index):
        return SilentReadyWorker(index)

    ready_wait_s = _pool_ready_wait_s([factory(0)])
    assert math.isfinite(ready_wait_s)
    assert ready_wait_s == pytest.approx(
        startup_timeout_s + size * ENGINE_WORKER_CANCEL_POLL_S
    )
    with pytest.raises(ValueError, match='finite and positive'):
        class InfiniteStartup:
            startup_timeout_s = float('inf')
        _pool_ready_wait_s([InfiniteStartup()])
    class MissingStartup:
        pass
    assert _pool_ready_wait_s([MissingStartup()]) == pytest.approx(
        30.0 + ENGINE_WORKER_CANCEL_POLL_S
    )

    # Join bound = handshake wait + automatic close budget + slack.
    # The constructor itself must return inside ready_wait_s + close;
    # the slack is only so a slow box cannot flake the pin.
    test_join_s = (
        ready_wait_s + ENGINE_POOL_AUTOMATIC_PER_POOL_BUDGET_S + 1.0
    )
    outcome = {}

    def construct():
        try:
            EngineWorkerPool(factory, size=size)
        except BaseException as exc:
            outcome['exc'] = exc
        else:
            outcome['ok'] = True

    thread = threading.Thread(target=construct, daemon=True)
    started_at = time.monotonic()
    thread.start()
    thread.join(timeout=test_join_s)
    elapsed = time.monotonic() - started_at

    if thread.is_alive():
        for pid in spawned_pids:
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
        for pool in tuple(engine_pool._LIVE_ENGINE_POOLS):
            engine_pool._discard_engine_pool(pool)
        pytest.fail(
            f'constructor still blocked after {elapsed:.3f}s '
            f'(derived ready wait {ready_wait_s:.3f}s, join budget {test_join_s:.3f}s)'
        )
    assert 'ok' not in outcome
    exc = outcome.get('exc')
    assert isinstance(exc, EngineWorkerTimeout)
    assert exc.phase == 'initialization'
    assert tuple(getattr(exc, 'failed_slots', ())) == (0,)
    assert exc.timeout_s == pytest.approx(ready_wait_s)
    assert elapsed <= ready_wait_s + ENGINE_POOL_AUTOMATIC_PER_POOL_BUDGET_S + 1.0
    assert spawned_pids, 'worker thread never spawned a child to reap'
    for pid in spawned_pids:
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)

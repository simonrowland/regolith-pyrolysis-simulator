import json
import math
import multiprocessing
import multiprocessing.process
import os
import queue
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from simulator.engine_pool import (
    ENGINE_POOL_AUTOMATIC_PER_POOL_BUDGET_S,
    ENGINE_WORKER_CANCEL_POLL_S,
    ENGINE_WORKER_SPAWN_SLACK_S,
    ENGINE_WORKER_STOP_PAIR_JOIN_S,
    EngineWorkerDied,
    EngineWorkerPool,
    EngineWorkerPoolLifecycleError,
    EngineWorkerRemoteError,
    EngineWorkerTimeout,
    WarmEngineWorker,
    _pool_ready_wait_s,
)

# Thread-rendezvous guards throughout this module: these bound a HANG, they do
# not assert a latency. Any test that means to claim "within N seconds" should
# use its own explicit figure and say so, rather than borrowing this one.
_RENDEZVOUS_TIMEOUT_S = 30.0


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


def _bootstrap_recording_worker(results, started=None, release=None):
    return {
        'results': results,
        'started': started,
        'release': release,
    }, 'recording-worker-ready'


def _handle_recording_request(resource, request, _errlog):
    if request.get('block'):
        resource['started'].set()
        if not resource['release'].wait(timeout=10.0):
            raise TimeoutError('test request release was not signalled')
    request_id = request['request_id']
    resource['results'].put(request_id)
    return request_id


def _bootstrap_timeout_log_worker(handler_started):
    return handler_started, 'timeout-log-worker-ready'


def _handle_timeout_log_request(handler_started, request, _errlog):
    if request.get('block_forever'):
        handler_started.set()
        while True:
            time.sleep(0.05)
    return request.get('request_id')


def _recording_worker(
    results,
    *,
    started=None,
    release=None,
    diagnostic_log_path=None,
):
    return WarmEngineWorker(
        name='recording worker',
        bootstrap=_bootstrap_recording_worker,
        bootstrap_args=(results, started, release),
        handler=_handle_recording_request,
        startup_timeout_s=_RENDEZVOUS_TIMEOUT_S,
        call_timeout_s=30.0,
        diagnostic_log_path=diagnostic_log_path,
    )


def _test_worker(*, timeout_s=1.0):
    return WarmEngineWorker(
        name='test engine',
        bootstrap=_bootstrap_test_worker,
        handler=_handle_test_request,
        # Hang bound, not a latency assert: a spawn-context cold start on a
        # contended gate box legitimately exceeds 2 s (b-548, 2026-09-21:
        # 10/10 failures under 90 CPU burners, all 'initialization exceeded
        # hard timeout of 2s' from a HEALTHY worker that was merely slow to
        # exec). The start() rendezvous still fails a worker that never acks
        # 'ready' — that coverage lives in the SilentReadyWorker tests below,
        # which pin the initialization timeout with their own budgets. This
        # bound only decides when "never acks" is declared, so it uses the
        # module rendezvous figure like every other hang guard here.
        startup_timeout_s=_RENDEZVOUS_TIMEOUT_S,
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


def test_pre_ready_worker_death_is_engine_worker_died():
    dying = WarmEngineWorker(
        name='pre-ready death',
        bootstrap=os._exit,
        bootstrap_args=(23,),
        handler=_handle_test_request,
        startup_timeout_s=5.0,
        call_timeout_s=5.0,
        diagnostic_log_path=None,
    )
    with pytest.raises(EngineWorkerDied) as started:
        dying.start()
    assert started.value.phase == 'start'
    assert isinstance(started.value.__cause__, EOFError)
    dying.close()

    worker = _test_worker(timeout_s=5.0)
    worker.start()
    worker.close()
    worker._bootstrap = os._exit
    worker._bootstrap_args = (23,)
    with pytest.raises(EngineWorkerDied) as restarted:
        worker.call({
            'T_C': 1100.0,
            'P_bar': 0.01,
            'fO2_log': -12.0,
            'composition': {'SiO2': 50.0},
        })
    assert restarted.value.phase == 'restart'
    assert isinstance(restarted.value.__cause__, EOFError)
    worker.close()


def test_replaced_start_runtimeerror_is_not_worker_death():
    worker = _test_worker(timeout_s=5.0)
    worker.start()
    worker.close()

    def local_restart_defect():
        raise RuntimeError('local restart helper bug')

    worker.start = local_restart_defect
    with pytest.raises(RuntimeError, match='local restart helper bug') as caught:
        worker.call({
            'T_C': 1100.0,
            'P_bar': 0.01,
            'fO2_log': -12.0,
            'composition': {'SiO2': 50.0},
        })
    assert not isinstance(caught.value, EngineWorkerDied)
    worker.close()


def _assert_call_death(caught):
    assert type(caught.value) is EngineWorkerDied
    assert caught.value.phase == 'call'
    assert caught.value.__cause__ is None
    assert str(caught.value) == 'test engine worker exited without a result'


def test_inflight_disable_during_send_is_call_death():
    pool = EngineWorkerPool(
        lambda _index: _test_worker(timeout_s=30.0), size=1
    )
    try:
        worker = pool.workers[0]
        connection = worker.connection
        real_send = connection.send

        def send_then_disable(request):
            real_send(request)
            worker.request_disable()

        connection.send = send_then_disable
        future = pool.submit(_points()[0], timeout_s=30.0)
        with pytest.raises(EngineWorkerDied) as caught:
            future.result(timeout=_RENDEZVOUS_TIMEOUT_S)
        _assert_call_death(caught)
    finally:
        pool.close(cancel_pending=True)


def test_inflight_disable_after_recv_is_call_death():
    pool = EngineWorkerPool(
        lambda _index: _test_worker(timeout_s=30.0), size=1
    )
    try:
        assert pool.submit(_points()[0], timeout_s=30.0).result(
            timeout=_RENDEZVOUS_TIMEOUT_S
        )
        worker = pool.workers[0]
        connection = worker.connection
        real_recv = connection.recv

        def recv_then_disable(*args, **kwargs):
            message = real_recv(*args, **kwargs)
            worker.request_disable()
            return message

        connection.recv = recv_then_disable
        future = pool.submit(_points()[1], timeout_s=30.0)
        with pytest.raises(EngineWorkerDied) as caught:
            future.result(timeout=_RENDEZVOUS_TIMEOUT_S)
        _assert_call_death(caught)
    finally:
        pool.close(cancel_pending=True)


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


@pytest.mark.timeout(20)
def test_abort_before_pool_call_entry_prevents_expired_request_start(
    monkeypatch,
):
    context = multiprocessing.get_context('spawn')
    results = context.Queue()
    pool = EngineWorkerPool(
        lambda _index: _recording_worker(results),
        size=1,
    )
    worker = pool.workers[0]
    original_pid = worker.process.pid
    entered = threading.Event()
    release_call = threading.Event()
    original_call = worker.call

    def call_after_gate(request, *, timeout_s=None):
        entered.set()
        assert release_call.wait(timeout=_RENDEZVOUS_TIMEOUT_S)
        return original_call(request, timeout_s=timeout_s)

    monkeypatch.setattr(worker, 'call', call_after_gate)
    try:
        expired = pool.submit({'request_id': 'expired'}, timeout_s=30.0)
        assert entered.wait(timeout=_RENDEZVOUS_TIMEOUT_S)
        assert expired.cancel() is False
        pool._abort_request(expired)
        release_call.set()

        with pytest.raises(EngineWorkerTimeout):
            expired.result(timeout=_RENDEZVOUS_TIMEOUT_S)
        assert worker.process is None
        with pytest.raises(ProcessLookupError):
            os.kill(original_pid, 0)
        with pytest.raises(queue.Empty):
            results.get(timeout=0.1)

        next_future = pool.submit({'request_id': 'next'}, timeout_s=30.0)
        assert next_future.result(timeout=_RENDEZVOUS_TIMEOUT_S) == 'next'
        assert results.get(timeout=_RENDEZVOUS_TIMEOUT_S) == 'next'
    finally:
        release_call.set()
        pool.close(cancel_pending=True)
        results.close()
        results.join_thread()


@pytest.mark.timeout(20)
def test_abort_during_cold_restart_before_publication_prevents_request(
    monkeypatch,
):
    context = multiprocessing.get_context('spawn')
    results = context.Queue()
    pool = EngineWorkerPool(
        lambda _index: _recording_worker(results),
        size=1,
    )
    worker = pool.workers[0]
    original_pid = worker.process.pid
    worker.close()
    entered = threading.Event()
    release_close = threading.Event()
    original_close = worker.close
    gate_once = [True]

    def close_after_gate():
        original_close()
        if gate_once[0]:
            gate_once[0] = False
            entered.set()
            assert release_close.wait(timeout=_RENDEZVOUS_TIMEOUT_S)

    monkeypatch.setattr(worker, 'close', close_after_gate)
    try:
        expired = pool.submit({'request_id': 'expired'}, timeout_s=30.0)
        assert entered.wait(timeout=_RENDEZVOUS_TIMEOUT_S)
        assert expired.cancel() is False
        pool._abort_request(expired)
        release_close.set()

        with pytest.raises(EngineWorkerTimeout):
            expired.result(timeout=_RENDEZVOUS_TIMEOUT_S)
        assert worker.start_count == 1
        assert worker.process is None
        with pytest.raises(ProcessLookupError):
            os.kill(original_pid, 0)
        with pytest.raises(queue.Empty):
            results.get(timeout=0.1)

        next_future = pool.submit({'request_id': 'next'}, timeout_s=30.0)
        assert next_future.result(timeout=_RENDEZVOUS_TIMEOUT_S) == 'next'
        assert results.get(timeout=_RENDEZVOUS_TIMEOUT_S) == 'next'
    finally:
        release_close.set()
        pool.close(cancel_pending=True)
        results.close()
        results.join_thread()


@pytest.mark.timeout(20)
def test_completed_request_abort_does_not_kill_next_assigned_request():
    context = multiprocessing.get_context('spawn')
    results = context.Queue()
    handler_started = context.Event()
    release_handler = context.Event()
    pool = EngineWorkerPool(
        lambda _index: _recording_worker(
            results,
            started=handler_started,
            release=release_handler,
        ),
        size=1,
    )
    worker = pool.workers[0]
    abort_entered = threading.Event()
    release_abort = threading.Event()
    original_abort = worker._abort_current_call
    try:
        first = pool.submit(
            {'request_id': 'first', 'block': True},
            timeout_s=30.0,
        )
        assert handler_started.wait(timeout=_RENDEZVOUS_TIMEOUT_S)
        call_finished = pool._active_requests[first][1]

        def abort_after_gate(abort_event=None):
            if abort_event is not None:
                abort_event.set()
            abort_entered.set()
            assert release_abort.wait(timeout=_RENDEZVOUS_TIMEOUT_S)
            return original_abort(abort_event)

        worker._abort_current_call = abort_after_gate
        aborter = threading.Thread(
            target=lambda: pool._abort_request(first),
            daemon=True,
        )
        aborter.start()
        assert abort_entered.wait(timeout=_RENDEZVOUS_TIMEOUT_S)

        release_handler.set()
        assert results.get(timeout=_RENDEZVOUS_TIMEOUT_S) == 'first'
        assert call_finished.wait(timeout=_RENDEZVOUS_TIMEOUT_S)
        second = pool.submit({'request_id': 'second'}, timeout_s=30.0)
        assert not second.done()

        release_abort.set()
        aborter.join(timeout=_RENDEZVOUS_TIMEOUT_S)
        assert not aborter.is_alive()
        assert first.result(timeout=_RENDEZVOUS_TIMEOUT_S) == 'first'
        assert second.result(timeout=_RENDEZVOUS_TIMEOUT_S) == 'second'
        assert results.get(timeout=_RENDEZVOUS_TIMEOUT_S) == 'second'
    finally:
        release_handler.set()
        release_abort.set()
        pool.close(cancel_pending=True)
        results.close()
        results.join_thread()


@pytest.mark.timeout(20)
def test_cancelled_pending_future_is_never_executed():
    context = multiprocessing.get_context('spawn')
    results = context.Queue()
    handler_started = context.Event()
    release_handler = context.Event()
    pool = EngineWorkerPool(
        lambda _index: _recording_worker(
            results,
            started=handler_started,
            release=release_handler,
        ),
        size=1,
    )
    try:
        first = pool.submit(
            {'request_id': 'first', 'block': True},
            timeout_s=30.0,
        )
        assert handler_started.wait(timeout=_RENDEZVOUS_TIMEOUT_S)
        pending = pool.submit({'request_id': 'cancelled'}, timeout_s=30.0)
        assert pending.cancel() is True
        release_handler.set()
        assert first.result(timeout=_RENDEZVOUS_TIMEOUT_S) == 'first'
        pool._queue.join()
        assert pending.cancelled()
        assert results.get(timeout=_RENDEZVOUS_TIMEOUT_S) == 'first'
        with pytest.raises(queue.Empty):
            results.get(timeout=0.1)
    finally:
        release_handler.set()
        pool.close(cancel_pending=True)
        results.close()
        results.join_thread()


@pytest.mark.timeout(20)
def test_pool_timeout_log_only_records_assigned_request_outside_pool_lock(
    monkeypatch,
    tmp_path,
):
    context = multiprocessing.get_context('spawn')
    handler_started = context.Event()
    diagnostic_path = tmp_path / 'worker-diagnostics.log'
    pool = EngineWorkerPool(
        lambda index: WarmEngineWorker(
            name=f'timeout log worker {index}',
            bootstrap=_bootstrap_timeout_log_worker,
            bootstrap_args=(handler_started,),
            handler=_handle_timeout_log_request,
            startup_timeout_s=_RENDEZVOUS_TIMEOUT_S,
            call_timeout_s=30.0,
            diagnostic_log_path=diagnostic_path,
        ),
        size=1,
    )
    writer_entered = threading.Event()
    release_writer = threading.Event()
    original_open = Path.open

    def gated_open(path, *args, **kwargs):
        if Path(path) == diagnostic_path and args and args[0] == 'a':
            writer_entered.set()
            assert release_writer.wait(timeout=_RENDEZVOUS_TIMEOUT_S)
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'open', gated_open)
    try:
        active = pool.submit(
            {'request_id': 'active', 'block_forever': True},
            timeout_s=30.0,
        )
        assert handler_started.wait(timeout=_RENDEZVOUS_TIMEOUT_S)
        pending = pool.submit({'request_id': 'pending'}, timeout_s=30.0)
        assert pending.cancel() is True
        assert pool._abort_request(
            pending,
            diagnostic_detail='{"request_id":"pending"}',
        ) is False
        assert diagnostic_path.read_text(encoding='utf-8') == ''

        aborter = threading.Thread(
            target=lambda: pool._abort_request(
                active,
                diagnostic_detail='{"request_id":"active"}',
            ),
            daemon=True,
        )
        aborter.start()
        assert writer_entered.wait(timeout=_RENDEZVOUS_TIMEOUT_S)
        assert pool._active_requests_lock.acquire(timeout=1.0)
        pool._active_requests_lock.release()
        release_writer.set()
        aborter.join(timeout=_RENDEZVOUS_TIMEOUT_S)
        assert not aborter.is_alive()

        with pytest.raises((EngineWorkerDied, EngineWorkerTimeout)):
            active.result(timeout=_RENDEZVOUS_TIMEOUT_S)
        lines = diagnostic_path.read_text(encoding='utf-8').splitlines()
        assert [json.loads(line) for line in lines] == [
            {'request_id': 'active'},
        ]
    finally:
        release_writer.set()
        pool.close(cancel_pending=True)


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


def test_submit_with_no_live_consumer_refuses_instead_of_hanging_forever():
    """A queued item with no consumer must refuse, not park the caller forever.

    2026-08-29, found by regolith-main and reproduced here. `timeout_s` is NOT
    a wall on `submit`: it is stashed in the queue item and enforced later by
    `_consume` -> `worker.call`. So the wall is enforced BY THE CONSUMER
    THREAD, and with no consumer alive there is no wall at all -- the future
    is never completed. `magemin.py::_call_magemin` awaits that future with no
    timeout (deliberately, because it trusts this wall), so the caller parks in
    `waiter.acquire()` at 0.0% CPU indefinitely.

    Measured before the fix: retire the only consumer, then submit. `_closed`
    was still False so the old guard passed, and the future was still `pending`
    after 5x its 1 s wall with `done()` False. The discriminator that separates
    this from a slow call is CPU: load cannot produce a 0.0% CPU stop, because
    a starved process still burns CPU whenever it is scheduled.

    The guard keys on "is any consumer alive", NOT on elapsed time, so it
    cannot fire on a merely busy pool: a busy consumer is an alive consumer,
    and a legitimately long queue wait keeps every thread alive.
    """
    pool = EngineWorkerPool(lambda _index: _test_worker(), size=1)
    try:
        # Sanity first, so a refusal below is the induced state and not a
        # broken fixture.
        assert pool.submit(_points()[0], timeout_s=2.0).result(timeout=10.0)

        # Retire the only consumer the way close() does, but WITHOUT marking
        # the pool closed -- that is the state the old guard could not see.
        pool._queue.put(None)
        deadline = time.monotonic() + 5.0
        while (
            any(thread.is_alive() for thread in pool._threads)
            and time.monotonic() < deadline
        ):
            time.sleep(0.01)
        assert not any(thread.is_alive() for thread in pool._threads)
        assert not pool._closed, 'fixture must exercise the not-closed path'

        with pytest.raises(RuntimeError, match='no live consumer') as exc_info:
            pool.submit(_points()[1], timeout_s=1.0)

        # Pin the lifecycle type, not merely RuntimeError. The absence
        # classifiers key on EngineWorkerUnavailable specifically
        # (optimize/evaluate.py, backends.py, run_executor.py), routing it to
        # a fallback. A guard that raised it here would turn "the pool is
        # broken" into "quietly use analytical curves instead of native
        # results", which is the defect shape this project keeps catching: a
        # working fake standing in for a broken real, green the whole way.
        # The implementation comment makes plain-RuntimeError the contract, so
        # the test has to pin it. Caught by review 2026-08-29: without this
        # line, mutating the guard to raise EngineWorkerUnavailable still
        # passed.
        assert type(exc_info.value) is EngineWorkerPoolLifecycleError, (
            f'guard must raise EngineWorkerPoolLifecycleError, not '
            f'{type(exc_info.value).__name__}'
        )
    finally:
        pool.close(cancel_pending=True)


def test_pool_thread_names_stay_unique_across_concurrently_live_pools():
    """Two live pools must never both name a thread `...slot-0`.

    2026-08-29 forensics regression. The name used to be
    `engine-worker-slot-{index}` with no pool identity, and THREE production
    sites build pools in one process (melt_backend/magemin.py,
    melt_backend/vaporock.py, optimize/pool.py). So any process with two warm
    backends showed two live threads both called `engine-worker-slot-0`, and a
    `sample` of a hung process read as "two threads on one slot sharing one
    queue" -- when it is really two independent pools holding a
    `queue.Queue` each. That misreading happened to a careful investigator
    within an hour of looking at a real stack.

    The name is written in exactly one place and read NOWHERE in the tree, so
    stack forensics is its only consumer, and a rename that drops pool
    identity is the only way to regress it. Both pools here use the SAME
    worker name, which is the harder case: uniqueness has to come from the
    per-process pool sequence, not from the backend differing.
    """
    pool_a = EngineWorkerPool(lambda _index: _test_worker(), size=2)
    pool_b = EngineWorkerPool(lambda _index: _test_worker(), size=2)
    try:
        names_a = [thread.name for thread in pool_a._threads]
        names_b = [thread.name for thread in pool_b._threads]

        # Uniqueness is the point of the fix.
        assert not set(names_a) & set(names_b), (
            f'pool thread names collide across live pools: '
            f'{sorted(set(names_a) & set(names_b))}'
        )
        assert len(set(names_a + names_b)) == 4

        # ...but a unique OPAQUE name would be useless to the investigator it
        # exists for, so the readable parts must survive too: which backend,
        # and which slot.
        for index, name in enumerate(names_a + names_b):
            slot = index % 2
            assert name.endswith(f'-test engine-slot-{slot}'), name
    finally:
        pool_a.close(cancel_pending=True)
        pool_b.close(cancel_pending=True)


def test_submit_racing_close_is_completed_or_rejected_without_orphan():
    pool = EngineWorkerPool(lambda _index: _test_worker(), size=1)
    original_put = pool._queue.put
    submit_entered_put = threading.Event()
    release_submit = threading.Event()

    def gated_put(item, *args, **kwargs):
        if item is not None:
            submit_entered_put.set()
            assert release_submit.wait(timeout=_RENDEZVOUS_TIMEOUT_S)
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
    assert submit_entered_put.wait(timeout=_RENDEZVOUS_TIMEOUT_S)
    close_thread = threading.Thread(target=pool.close)
    close_thread.start()
    time.sleep(0.05)
    assert close_thread.is_alive()
    release_submit.set()
    submit_thread.join(timeout=_RENDEZVOUS_TIMEOUT_S)
    close_thread.join(timeout=_RENDEZVOUS_TIMEOUT_S)

    assert not submit_thread.is_alive()
    assert not close_thread.is_alive()
    assert 'error' not in outcome
    assert outcome['future'].result(timeout=1.0)
    with pytest.raises(
        EngineWorkerPoolLifecycleError, match='pool is closed'
    ):
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
    with pytest.raises(
        EngineWorkerDied, match='worker exited without a result'
    ) as caught:
        future.result(timeout=0.1)
    assert caught.value.phase == 'call'
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
        startup_timeout_s
        + ENGINE_WORKER_SPAWN_SLACK_S
        + ENGINE_WORKER_STOP_PAIR_JOIN_S
        + size * ENGINE_WORKER_CANCEL_POLL_S
    )
    with pytest.raises(ValueError, match='finite and positive'):
        class InfiniteStartup:
            startup_timeout_s = float('inf')
        _pool_ready_wait_s([InfiniteStartup()])
    class MissingStartup:
        pass
    assert _pool_ready_wait_s([MissingStartup()]) == pytest.approx(
        30.0
        + ENGINE_WORKER_SPAWN_SLACK_S
        + ENGINE_WORKER_STOP_PAIR_JOIN_S
        + ENGINE_WORKER_CANCEL_POLL_S
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


def _noop_disable_and_reap(worker):
    """Minimal close/disable surface for constructor-failure teardown."""
    def request_disable():
        process = worker.process
        worker.process = None
        if process is not None:
            worker._pending_reap.append(process)
            if getattr(process, 'pid', None) is not None and process.is_alive():
                try:
                    process.kill()
                except OSError:
                    pass

    def _reap_pending(*, timeout_s):
        deadline = time.monotonic() + max(0.0, float(timeout_s))
        remaining = []
        for process in worker._pending_reap:
            if getattr(process, 'pid', None) is None:
                continue
            process.join(timeout=max(0.0, deadline - time.monotonic()))
            if process.is_alive():
                try:
                    process.kill()
                except OSError:
                    pass
                process.join(timeout=max(0.0, deadline - time.monotonic()))
            if process.is_alive():
                remaining.append(process)
        worker._pending_reap = remaining
        return not remaining

    def close():
        request_disable()
        _reap_pending(timeout_s=1.0)

    worker.request_disable = request_disable
    worker._reap_pending = _reap_pending
    worker.close = close


@pytest.mark.timeout(20)
def test_constructor_preserves_typed_handshake_timeout_identity():
    """A genuine start() handshake timeout must keep that worker identity.

    Models WarmEngineWorker.start() on the timeout path: handshake wall,
    then _discard_current → _stop_pair join(0.1)+join(1.0) *before*
    ready.put. The constructor wait used to expire during that discard
    and rewrite the typed EngineWorkerTimeout as a generic missing-slot
    refusal ('engine worker pool slots [0]').
    """
    startup_timeout_s = 0.4
    # Same join wall as WarmEngineWorker._stop_pair (diagnostic=False).
    discard_join_s = 0.1 + 1.0

    class LateTypedTimeoutWorker:
        def __init__(self, index):
            self.name = f'typed-handshake slot {index}'
            self.startup_timeout_s = startup_timeout_s
            self.process = None
            self._pending_reap = []
            _noop_disable_and_reap(self)

        def start(self, *, timeout_s=None):
            time.sleep(self.startup_timeout_s)
            exc = EngineWorkerTimeout(
                self.name,
                self.startup_timeout_s,
                phase='initialization',
            )
            time.sleep(discard_join_s)
            raise exc

    def factory(index):
        return LateTypedTimeoutWorker(index)

    with pytest.raises(EngineWorkerTimeout) as exc_info:
        EngineWorkerPool(factory, size=1)
    exc = exc_info.value
    assert exc.phase == 'initialization'
    assert exc.worker_name == 'typed-handshake slot 0'
    assert 'engine worker pool slots' not in exc.worker_name
    assert exc.timeout_s == pytest.approx(startup_timeout_s)
    assert tuple(getattr(exc, 'failed_slots', ())) == ()


@pytest.mark.timeout(20)
def test_constructor_reaps_child_when_process_start_blocks_before_publish():
    """Pin: process.start() hang before self.process is assigned must still reap.

    SilentReadyWorker publishes the child, then parks — request_disable can
    see it. That is not this bug. Here start() creates the OS child and
    then blocks *inside* Process.start(), which is how WarmEngineWorker
    used to leave self.process as None.
    """
    spawned_pids = []
    original_start = multiprocessing.process.BaseProcess.start

    def blocking_start(self):
        original_start(self)
        pid = self.pid
        if pid is not None:
            spawned_pids.append(pid)
        time.sleep(3600)

    multiprocessing.process.BaseProcess.start = blocking_start
    try:
        def factory(index):
            return WarmEngineWorker(
                name=f'unpublished-spawn slot {index}',
                bootstrap=_bootstrap_test_worker,
                handler=_handle_test_request,
                startup_timeout_s=0.5,
                call_timeout_s=1.0,
                daemon=False,
            )

        with pytest.raises(EngineWorkerTimeout) as exc_info:
            EngineWorkerPool(factory, size=1)
        assert exc_info.value.phase == 'initialization'
        assert spawned_pids, 'process.start() never created a child'
        for pid in spawned_pids:
            with pytest.raises(ProcessLookupError):
                os.kill(pid, 0)
    finally:
        multiprocessing.process.BaseProcess.start = original_start
        for pid in spawned_pids:
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass


@pytest.mark.timeout(25)
def test_unpublished_daemon_false_spawn_does_not_block_interpreter_exit(
    tmp_path,
):
    """A daemon=False child left unpublished must not pin interpreter shutdown."""
    script = tmp_path / 'unpublished_spawn_exit_pin.py'
    script.write_text(
        '''
import multiprocessing.process
import sys
import time

from simulator.engine_pool import (
    EngineWorkerPool,
    EngineWorkerTimeout,
    WarmEngineWorker,
)

def bootstrap():
    time.sleep(3600)
    return None, "ready"

def handler(_resource, request, _errlog):
    return request

def factory(index):
    return WarmEngineWorker(
        name=f"exit-pin slot {index}",
        bootstrap=bootstrap,
        handler=handler,
        startup_timeout_s=0.4,
        call_timeout_s=1.0,
        daemon=False,
    )

if __name__ == "__main__":
    _original_start = multiprocessing.process.BaseProcess.start

    def _blocking_start(self):
        _original_start(self)
        time.sleep(3600)

    multiprocessing.process.BaseProcess.start = _blocking_start
    try:
        EngineWorkerPool(factory, size=1)
    except EngineWorkerTimeout:
        raise SystemExit(0)
    except BaseException as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(2)
    raise SystemExit(3)
''',
        encoding='utf-8',
    )
    env = os.environ.copy()
    env['PYTHONPATH'] = os.pathsep.join(
        [os.getcwd(), env.get('PYTHONPATH', '')]
    ).rstrip(os.pathsep)
    proc = subprocess.Popen(
        [sys.executable, str(script)],
        cwd=os.getcwd(),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    try:
        stdout, stderr = proc.communicate(timeout=15.0)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            proc.kill()
        stdout, stderr = proc.communicate(timeout=5.0)
        pytest.fail(
            'interpreter did not exit within 15s — non-daemon unpublished '
            f'orphan blocked shutdown stdout={stdout!r} stderr={stderr!r}'
        )
    assert proc.returncode == 0, (
        f'child exited {proc.returncode} stdout={stdout!r} stderr={stderr!r}'
    )

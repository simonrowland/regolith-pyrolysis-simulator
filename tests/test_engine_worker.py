import json
import threading
import os
import time

import pytest

from simulator.engine_pool import (
    EngineWorkerRemoteError,
    EngineWorkerPool,
    EngineWorkerTimeout,
    WarmEngineWorker,
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

        # EXACT type, not isinstance. EngineWorkerUnavailable subclasses
        # RuntimeError, so `pytest.raises(RuntimeError)` alone accepts it --
        # and the absence classifiers key on that exact type
        # (optimize/evaluate.py, backends.py, run_executor.py), routing it to
        # a fallback. A guard that raised it here would turn "the pool is
        # broken" into "quietly use analytical curves instead of native
        # results", which is the defect shape this project keeps catching: a
        # working fake standing in for a broken real, green the whole way.
        # The implementation comment makes plain-RuntimeError the contract, so
        # the test has to pin it. Caught by review 2026-08-29: without this
        # line, mutating the guard to raise EngineWorkerUnavailable still
        # passed.
        assert type(exc_info.value) is RuntimeError, (
            f'guard must raise a PLAIN RuntimeError, not '
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

import time

import pytest

from simulator.engine_pool import (
    EngineWorkerDied,
    EngineWorkerPool,
    EngineWorkerTimeout,
    WarmEngineWorker,
)


class _FakeProcess:
    pid = 7313
    exitcode = None

    def __init__(self, *, dies_after_first_check=False):
        self._dies_after_first_check = dies_after_first_check
        self._alive_checks = 0

    def is_alive(self):
        self._alive_checks += 1
        if self._dies_after_first_check and self._alive_checks > 1:
            self.exitcode = 23
            return False
        return True


class _SilentConnection:
    def __init__(self):
        self.sent = []

    def send(self, request):
        self.sent.append(request)

    def poll(self, timeout):
        time.sleep(min(timeout, 0.01))
        return False


def _fake_backend_pool(*, dies_after_first_check=False):
    process = _FakeProcess(dies_after_first_check=dies_after_first_check)
    connection = _SilentConnection()
    worker = WarmEngineWorker(
        name='fake backend',
        bootstrap=lambda: (None, 'unused'),
        handler=lambda _resource, _request, _errlog: None,
        startup_timeout_s=1.0,
        call_timeout_s=0.15,
        watchdog_grace_s=0.0,
    )
    worker.process = process
    worker.connection = connection
    worker.start = lambda: 'fake-ready'
    worker._discard_current = lambda **_kwargs: None
    worker.request_disable = lambda: None
    worker.close = lambda: None
    worker._reap_pending = lambda **_kwargs: True
    return EngineWorkerPool(lambda _index: worker, size=1), process


def _assert_diagnostic(error, *, request_kind, exit_code):
    assert error.slot == 0
    assert error.backend_pid == 7313
    assert error.exit_code == exit_code
    assert error.last_request_kind == request_kind
    assert error.elapsed_s >= 0.0
    diagnostic = error.diagnostic
    assert 'slot=0' in diagnostic
    assert 'backend_pid=7313' in diagnostic
    assert f'exit_code={exit_code if exit_code is not None else "none"}' in diagnostic
    assert f'last_request_kind={request_kind}' in diagnostic
    assert 'elapsed_s=' in diagnostic
    assert error.__notes__ == [diagnostic]


def test_engine_pool_refuses_backend_that_never_replies_with_diagnostic():
    pool, _process = _fake_backend_pool()
    started_at = time.monotonic()
    try:
        future = pool.submit({'kind': 'never_reply'}, timeout_s=0.15)
        with pytest.raises(EngineWorkerTimeout) as caught:
            future.result(timeout=2.0)
    finally:
        pool.close(cancel_pending=True, timeout_s=1.0)

    assert time.monotonic() - started_at < 2.0
    _assert_diagnostic(
        caught.value,
        request_kind='never_reply',
        exit_code=None,
    )


def test_engine_pool_refuses_dead_backend_with_diagnostic():
    pool, _process = _fake_backend_pool(dies_after_first_check=True)
    started_at = time.monotonic()
    try:
        future = pool.submit({'kind': 'dies_before_reply'}, timeout_s=1.0)
        with pytest.raises(EngineWorkerDied) as caught:
            future.result(timeout=2.0)
    finally:
        pool.close(cancel_pending=True, timeout_s=1.0)

    assert time.monotonic() - started_at < 2.0
    assert caught.value.phase == 'call'
    assert caught.value.returncode == 23
    _assert_diagnostic(
        caught.value,
        request_kind='dies_before_reply',
        exit_code=23,
    )

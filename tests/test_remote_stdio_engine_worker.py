from __future__ import annotations

import multiprocessing
import os
import signal
import sys
import threading
import time
from pathlib import Path

import pytest

from simulator.engine_pool import (
    EngineWorkerPool,
    EngineWorkerTimeout,
    StdioRemoteEngineWorker,
    WarmEngineWorker,
    _remote_engine_stdio_bootstrap,
)


FAKE_WORKER = Path(__file__).parent / 'fakes' / 'remote_stdio_worker.py'


def _blocked_remote_bootstrap(entered_sender, release_receiver, *bootstrap_args):
    entered_sender.send(None)
    release_receiver.recv()
    return _remote_engine_stdio_bootstrap(*bootstrap_args)


def _blocked_pgid_report_bootstrap(
    report_wait_sender,
    release_report_receiver,
    report_sent_sender,
    argv,
    protocol_version,
    max_line_bytes,
    pgid_sender,
):
    class DelayedPgidSender:
        def send(self, group_id):
            report_wait_sender.send(None)
            release_report_receiver.recv()
            pgid_sender.send(group_id)
            report_sent_sender.send(None)

        def close(self):
            pgid_sender.close()

    return _remote_engine_stdio_bootstrap(
        argv,
        protocol_version,
        max_line_bytes,
        DelayedPgidSender(),
    )


def _blocked_remote_bootstrap_with_late_pgid(
    entered_sender,
    release_bootstrap_receiver,
    report_wait_sender,
    release_report_receiver,
    report_sent_sender,
    *bootstrap_args,
):
    entered_sender.send(None)
    release_bootstrap_receiver.recv()
    return _blocked_pgid_report_bootstrap(
        report_wait_sender,
        release_report_receiver,
        report_sent_sender,
        *bootstrap_args,
    )


@pytest.fixture
def remote_worker_factory():
    workers: list[StdioRemoteEngineWorker] = []

    def make(mode: str, *args: str, **kwargs) -> StdioRemoteEngineWorker:
        worker = StdioRemoteEngineWorker(
            name=f'remote-{mode}',
            argv=(sys.executable, str(FAKE_WORKER), mode, *args),
            startup_timeout_s=kwargs.pop('startup_timeout_s', 3.0),
            call_timeout_s=kwargs.pop('call_timeout_s', 3.0),
            max_line_bytes=kwargs.pop('max_line_bytes', 256),
        )
        if kwargs:
            raise TypeError(f'unused worker options: {sorted(kwargs)}')
        workers.append(worker)
        return worker

    yield make
    for worker in workers:
        worker.close()


def _request() -> dict[str, object]:
    return {
        'observation_id': 'fake-observation-1',
        'quantity': 'vapour_pressure',
        'conditions': {'T_K': 1800.0, 'P_bar': 1.0},
    }


def _read_pids(*paths: Path) -> tuple[int, ...]:
    return tuple(int(path.read_text(encoding='utf-8')) for path in paths)


def _assert_pids_gone(pids: tuple[int, ...]) -> None:
    for pid in pids:
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.02)
        else:
            pytest.fail(f'remote process {pid} remained after cleanup')


def _kill_pids(pids: tuple[int, ...]) -> None:
    for pid in pids:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def _set_process_group_mode(monkeypatch, inherit: bool) -> None:
    variable = 'REGOLITH_ENGINE_WORKER_INHERIT_PROCESS_GROUP'
    if inherit:
        monkeypatch.setenv(variable, '1')
    else:
        monkeypatch.delenv(variable, raising=False)


def _kill_wrapper(worker: StdioRemoteEngineWorker) -> None:
    process = worker.process
    assert process is not None
    process.kill()
    process.join(timeout=1.0)
    assert not process.is_alive()


def _assert_caller_group_unchanged(group_id: int) -> None:
    assert os.getpgrp() == group_id
    os.kill(os.getpid(), 0)


def test_ready_and_produced_reply(remote_worker_factory):
    worker = remote_worker_factory('produced')

    result = worker.call(_request())

    assert worker.ready_payload == {
        'protocol_version': 1,
        'engine_version': 'fake-1',
        'datafile_fingerprint': 'fake-data-1',
    }
    assert result['status'] == 'produced'
    assert result['observation_id'] == 'fake-observation-1'
    assert result['scalar'] == 2.5


def test_close_before_start_is_terminal_for_remote_worker(remote_worker_factory):
    worker = remote_worker_factory('produced')

    worker.close()
    worker.close()
    result = worker.call(_request())

    assert result['status'] == 'refused'
    assert result['code'] == 'startup_failure'
    assert 'scalar' not in result
    assert worker.start_count == 0


def test_remote_worker_cannot_start_a_second_launch(remote_worker_factory):
    worker = remote_worker_factory('produced')
    worker.call(_request())
    previous_process = worker.process
    assert previous_process is not None

    with pytest.raises(RuntimeError, match='cannot start more than once'):
        worker.start()

    assert previous_process.is_alive()
    assert worker.process is previous_process
    assert worker.call(_request())['status'] == 'produced'
    assert worker.start_count == 1


def test_refused_reply_is_returned(remote_worker_factory):
    result = remote_worker_factory('refused').call(_request())

    assert result['status'] == 'refused'
    assert result['code'] == 'unsupported_quantity'
    assert result['detail'] == 'fake worker refused this quantity'


@pytest.mark.parametrize(
    ('mode', 'expected_code'),
    [
        ('malformed', 'malformed_json'),
        ('wrong-id', 'wrong_id'),
        ('wrong-observation-id', 'wrong_id'),
        ('nan', 'non_finite_value'),
        ('array', 'invalid_scalar'),
        ('oversized', 'line_too_large'),
        ('extra-field', 'malformed_response'),
        ('eof', 'eof'),
    ],
)
def test_invalid_or_failed_response_is_a_typed_refusal(
    remote_worker_factory, mode, expected_code,
):
    result = remote_worker_factory(mode).call(_request())

    assert result['status'] == 'refused'
    assert result['code'] == expected_code
    assert result['request_id']
    assert result['observation_id'] == 'fake-observation-1'


def test_stderr_noise_does_not_corrupt_stdout_protocol(remote_worker_factory):
    result = remote_worker_factory('stderr-noise').call(_request())

    assert result['status'] == 'produced'
    assert result['scalar'] == 2.5


def test_repeated_request_is_sent_each_time(remote_worker_factory):
    worker = remote_worker_factory('increment')

    first = worker.call(_request())
    second = worker.call(_request())

    assert first['scalar'] == 1.0
    assert second['scalar'] == 2.0
    assert worker.start_count == 1


def test_remote_worker_runs_as_an_engine_pool_slot(remote_worker_factory):
    pool = EngineWorkerPool(
        worker_factory=lambda _index: remote_worker_factory('produced'),
        size=1,
    )
    try:
        result = pool.submit(_request()).result(timeout=10.0)

        assert result['status'] == 'produced'
        assert pool.workers[0].start_count == 1
    finally:
        pool.close(cancel_pending=True)


@pytest.mark.parametrize('inherit', (False, True), ids=('default', 'inherited'))
def test_pool_cancel_active_call_respects_cleanup_budget(
    remote_worker_factory, monkeypatch, tmp_path, inherit,
):
    _set_process_group_mode(monkeypatch, inherit)
    caller_group = os.getpgrp()
    worker_pid_file = tmp_path / 'worker.pid'
    grandchild_pid_file = tmp_path / 'grandchild.pid'
    pool = EngineWorkerPool(
        worker_factory=lambda _index: remote_worker_factory(
            'subtree-timeout', str(worker_pid_file), str(grandchild_pid_file),
            startup_timeout_s=8.0,
            call_timeout_s=30.0,
        ),
        size=1,
    )
    worker = pool.workers[0]
    future = pool.submit(_request())
    pids: tuple[int, ...] = ()
    close_error: TimeoutError | None = None
    try:
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and not (
            worker_pid_file.exists() and grandchild_pid_file.exists()
        ):
            time.sleep(0.01)
        assert worker_pid_file.exists() and grandchild_pid_file.exists()
        pids = _read_pids(worker_pid_file, grandchild_pid_file)
        wrapper = worker.process
        assert wrapper is not None

        close_started = time.monotonic()
        try:
            pool.close(cancel_pending=True, timeout_s=0.5)
        except TimeoutError as exc:
            close_error = exc
        close_elapsed = time.monotonic() - close_started
        result = future.result(timeout=5.0)

        assert close_error is None
        assert close_elapsed <= 0.75
        assert result['status'] == 'refused'
        assert 'scalar' not in result
        wrapper.join(timeout=1.0)
        assert wrapper.exitcode is not None
        assert not wrapper.is_alive()
        _assert_pids_gone(pids)
        _assert_caller_group_unchanged(caller_group)
    finally:
        try:
            pool.close(cancel_pending=True, timeout_s=1.0)
        except TimeoutError:
            pass
        if not pids and worker_pid_file.exists() and grandchild_pid_file.exists():
            pids = _read_pids(worker_pid_file, grandchild_pid_file)
        _kill_pids(pids)


@pytest.mark.parametrize('inherit', (False, True), ids=('default', 'inherited'))
def test_pool_cancel_replacement_during_cold_start_respects_cleanup_deadline(
    remote_worker_factory, monkeypatch, tmp_path, inherit,
):
    _set_process_group_mode(monkeypatch, inherit)
    context = multiprocessing.get_context('spawn')
    bootstrap_entered_receive, bootstrap_entered_sender = context.Pipe(
        duplex=False,
    )
    release_bootstrap_receiver, release_bootstrap_sender = context.Pipe(
        duplex=False,
    )
    report_wait_receive, report_wait_sender = context.Pipe(duplex=False)
    release_report_receiver, release_report_sender = context.Pipe(duplex=False)
    report_sent_receive, report_sent_sender = context.Pipe(duplex=False)
    crash_marker = tmp_path / 'crash-first.marker'
    created: list[StdioRemoteEngineWorker] = []

    def factory(_index):
        if not created:
            worker = remote_worker_factory('crash-first', str(crash_marker))
        else:
            worker = remote_worker_factory('produced', startup_timeout_s=8.0)
            original_args = worker._process_bootstrap_args
            worker._bootstrap = _blocked_remote_bootstrap_with_late_pgid
            worker._process_bootstrap_args = lambda: (
                bootstrap_entered_sender,
                release_bootstrap_receiver,
                report_wait_sender,
                release_report_receiver,
                report_sent_sender,
                *original_args(),
            )
        created.append(worker)
        return worker

    pool = EngineWorkerPool(factory, size=1)
    wrapper = None
    try:
        first = pool.submit(_request()).result(timeout=10.0)
        assert first['status'] == 'refused'
        assert first['code'] == 'child_crash'
        assert len(created) == 2
        assert pool.workers[0] is created[1]

        replacement_call = pool.submit(_request())
        assert bootstrap_entered_receive.poll(5.0)
        bootstrap_entered_receive.recv()
        wrapper = created[1].process
        assert wrapper is not None
        bootstrap_entered_sender.close()

        close_started = time.monotonic()
        pool.close(cancel_pending=True, timeout_s=3.0)
        close_elapsed = time.monotonic() - close_started

        assert close_elapsed <= 3.0
        assert replacement_call.done()
        assert created[1]._remote_pgid_reaper_started
        assert wrapper.is_alive()
        release_bootstrap_sender.send(None)
        assert report_wait_receive.poll(5.0)
        report_wait_receive.recv()
        release_report_receiver.close()
        report_sent_sender.close()
        release_report_sender.send(None)
        assert report_sent_receive.poll(5.0)
        report_sent_receive.recv()
        wrapper.join(timeout=10.0)
        assert not wrapper.is_alive()
    finally:
        try:
            release_bootstrap_sender.send(None)
        except (BrokenPipeError, EOFError, OSError):
            pass
        try:
            pool.close(cancel_pending=True, timeout_s=1.0)
        except TimeoutError:
            pass
        bootstrap_entered_receive.close()
        bootstrap_entered_sender.close()
        release_bootstrap_receiver.close()
        release_bootstrap_sender.close()
        report_wait_receive.close()
        report_wait_sender.close()
        release_report_receiver.close()
        release_report_sender.close()
        report_sent_receive.close()
        report_sent_sender.close()


@pytest.mark.parametrize('inherit', (False, True), ids=('default', 'inherited'))
def test_overlapping_cleanup_honors_newer_pool_deadline(
    remote_worker_factory, monkeypatch, tmp_path, inherit,
):
    _set_process_group_mode(monkeypatch, inherit)
    context = multiprocessing.get_context('spawn')
    bootstrap_entered_receive, bootstrap_entered_sender = context.Pipe(
        duplex=False,
    )
    release_bootstrap_receiver, release_bootstrap_sender = context.Pipe(
        duplex=False,
    )
    crash_marker = tmp_path / 'crash-first.marker'
    created: list[StdioRemoteEngineWorker] = []

    def factory(_index):
        if not created:
            worker = remote_worker_factory('crash-first', str(crash_marker))
        else:
            worker = remote_worker_factory('produced', startup_timeout_s=8.0)
            original_args = worker._process_bootstrap_args
            worker._bootstrap = _blocked_remote_bootstrap
            worker._process_bootstrap_args = lambda: (
                bootstrap_entered_sender,
                release_bootstrap_receiver,
                *original_args(),
            )
        created.append(worker)
        return worker

    pool = EngineWorkerPool(factory, size=1)
    cleanup_thread = None
    close_error = None
    try:
        first = pool.submit(_request()).result(timeout=10.0)
        assert first['code'] == 'child_crash'
        replacement_call = pool.submit(_request())
        assert bootstrap_entered_receive.poll(5.0)
        bootstrap_entered_receive.recv()
        worker = created[1]
        wrapper = worker.process
        assert wrapper is not None

        cleanup_entered = threading.Event()
        real_receive = worker._remote_pid_receive

        class RecordingReceive:
            def poll(self, timeout=0.0):
                if (
                    timeout > 0.0
                    and threading.current_thread() is cleanup_thread
                ):
                    cleanup_entered.set()
                return real_receive.poll(timeout)

            def recv(self):
                return real_receive.recv()

        worker._remote_pid_receive = RecordingReceive()
        cleanup_thread = threading.Thread(
            target=worker.request_disable,
            name='earlier cleanup',
        )
        cleanup_thread.start()
        assert cleanup_entered.wait(timeout=1.0)

        bootstrap_entered_sender.close()
        close_started = time.monotonic()
        try:
            pool.close(cancel_pending=True, timeout_s=3.0)
        except EngineWorkerTimeout as exc:
            close_error = exc
        close_elapsed = time.monotonic() - close_started

        assert close_elapsed <= 3.2, close_error
        assert replacement_call.done()
        assert worker._remote_pgid_reaper_started
        assert wrapper.is_alive()
        release_bootstrap_sender.send(None)
        cleanup_thread.join(timeout=1.0)
        assert not cleanup_thread.is_alive()
        wrapper.join(timeout=10.0)
        assert not wrapper.is_alive()
    finally:
        try:
            release_bootstrap_sender.send(None)
        except (BrokenPipeError, EOFError, OSError):
            pass
        if cleanup_thread is not None:
            cleanup_thread.join(timeout=1.0)
        try:
            pool.close(cancel_pending=True, timeout_s=1.0)
        except TimeoutError:
            pass
        bootstrap_entered_receive.close()
        bootstrap_entered_sender.close()
        release_bootstrap_receiver.close()
        release_bootstrap_sender.close()


@pytest.mark.parametrize('inherit', (False, True), ids=('default', 'inherited'))
def test_pool_close_reaps_descendants_after_late_remote_pgid_report(
    remote_worker_factory, monkeypatch, tmp_path, inherit,
):
    _set_process_group_mode(monkeypatch, inherit)
    caller_group = os.getpgrp()
    worker_pid_file = tmp_path / 'worker.pid'
    grandchild_pid_file = tmp_path / 'grandchild.pid'
    context = multiprocessing.get_context('spawn')
    report_wait_receive, report_wait_sender = context.Pipe(duplex=False)
    release_report_receiver, release_report_sender = context.Pipe(duplex=False)
    report_sent_receive, report_sent_sender = context.Pipe(duplex=False)
    descendants_started = threading.Event()
    previous_signal_handler = signal.signal(
        signal.SIGUSR1,
        lambda *_args: descendants_started.set(),
    )
    crash_marker = tmp_path / 'crash-first.marker'
    created: list[StdioRemoteEngineWorker] = []

    def factory(_index):
        if not created:
            worker = remote_worker_factory('crash-first', str(crash_marker))
        else:
            worker = remote_worker_factory(
                'startup-subtree-notify', str(worker_pid_file),
                str(grandchild_pid_file), str(os.getpid()),
                startup_timeout_s=8.0,
            )
            original_args = worker._process_bootstrap_args
            worker._bootstrap = _blocked_pgid_report_bootstrap
            worker._process_bootstrap_args = lambda: (
                report_wait_sender,
                release_report_receiver,
                report_sent_sender,
                *original_args(),
            )
        created.append(worker)
        return worker

    pool = EngineWorkerPool(factory, size=1)
    pids: tuple[int, ...] = ()
    wrapper = None
    try:
        first = pool.submit(_request()).result(timeout=10.0)
        assert first['code'] == 'child_crash'
        replacement_call = pool.submit(_request())
        assert report_wait_receive.poll(5.0)
        report_wait_receive.recv()
        report_wait_sender.close()
        release_report_receiver.close()
        report_sent_sender.close()
        assert descendants_started.wait(timeout=5.0)
        pids = _read_pids(worker_pid_file, grandchild_pid_file)
        wrapper = created[1].process
        assert wrapper is not None
        close_started = time.monotonic()
        pool.close(cancel_pending=True, timeout_s=3.0)
        assert time.monotonic() - close_started <= 3.2
        assert replacement_call.done()

        release_report_sender.send(None)
        assert report_sent_receive.poll(5.0)
        report_sent_receive.recv()
        _assert_pids_gone(pids)
        wrapper.join(timeout=3.0)
        assert not wrapper.is_alive()
        assert created[1]._remote_process_group_id is None
        _assert_caller_group_unchanged(caller_group)
    finally:
        try:
            release_report_sender.send(None)
        except (BrokenPipeError, EOFError, OSError):
            pass
        try:
            pool.close(cancel_pending=True, timeout_s=1.0)
        except TimeoutError:
            pass
        signal.signal(signal.SIGUSR1, previous_signal_handler)
        if not pids and worker_pid_file.exists() and grandchild_pid_file.exists():
            pids = _read_pids(worker_pid_file, grandchild_pid_file)
        _kill_pids(pids)
        report_wait_receive.close()
        report_wait_sender.close()
        release_report_receiver.close()
        release_report_sender.close()
        report_sent_receive.close()
        report_sent_sender.close()


@pytest.mark.parametrize('inherit', (False, True), ids=('default', 'inherited'))
def test_wrapper_death_during_bootstrap_keeps_concurrent_cleanup_bounded(
    remote_worker_factory, monkeypatch, tmp_path, inherit,
):
    _set_process_group_mode(monkeypatch, inherit)
    caller_group = os.getpgrp()
    parent_pid_file = tmp_path / 'remote-worker.pid'
    child_pid_file = tmp_path / 'remote-grandchild.pid'
    worker = remote_worker_factory(
        'startup-subtree-bootstrap-hold',
        str(parent_pid_file), str(child_pid_file),
        startup_timeout_s=8.0,
        call_timeout_s=30.0,
    )
    cleanup_entered = threading.Event()
    release_cleanup = threading.Event()
    stale_cleanup_resumed = threading.Event()
    replies: list[dict[str, object]] = []
    pids: tuple[int, ...] = ()
    original_remote_pgid = worker._remote_pgid

    def pause_active_cleanup(
        *, wait_for_report=False, cleanup=False, cleanup_deadline=None,
        background_reaper=False,
    ):
        if (
            cleanup
            and wait_for_report
            and worker._remote_startup_deadline is not None
            and worker._remote_startup_deadline - time.monotonic() > 1.0
            and threading.current_thread().name == 'remote active call'
            and not cleanup_entered.is_set()
        ):
            cleanup_entered.set()
            if not release_cleanup.wait(timeout=15.0):
                raise TimeoutError('test did not release active PGID cleanup')
        result = original_remote_pgid(
            wait_for_report=wait_for_report,
            cleanup=cleanup,
            cleanup_deadline=cleanup_deadline,
            background_reaper=background_reaper,
        )
        if cleanup and release_cleanup.is_set():
            stale_cleanup_resumed.set()
        return result

    monkeypatch.setattr(worker, '_remote_pgid', pause_active_cleanup)
    call_thread = threading.Thread(
        target=lambda: replies.append(worker.call(_request())),
        name='remote active call',
    )
    try:
        call_thread.start()
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and not (
            parent_pid_file.exists() and child_pid_file.exists()
        ):
            time.sleep(0.01)
        assert parent_pid_file.exists() and child_pid_file.exists()
        pids = _read_pids(parent_pid_file, child_pid_file)
        wrapper = worker.process
        assert wrapper is not None

        wrapper.kill()
        wrapper.join(timeout=1.0)
        assert wrapper.exitcode is not None
        assert cleanup_entered.wait(timeout=3.0)
        assert worker.process is None

        worker.close()
        assert worker._remote_process_group_id is None
        assert worker._remote_startup_deadline <= time.monotonic()

        cleanup_released_at = time.monotonic()
        release_cleanup.set()
        assert stale_cleanup_resumed.wait(timeout=2.0)
        repeated_close_started = time.monotonic()
        worker.close()
        repeated_close_wall = time.monotonic() - repeated_close_started
        call_thread.join(timeout=2.0)
        call_cleanup_wall = time.monotonic() - cleanup_released_at

        assert repeated_close_wall <= 0.75
        assert not call_thread.is_alive()
        assert call_cleanup_wall <= 0.75
        assert len(replies) == 1
        assert replies[0]['status'] == 'refused'
        assert replies[0]['code'] == 'startup_failure'
        assert 'scalar' not in replies[0]
        _assert_pids_gone(pids)
        _assert_caller_group_unchanged(caller_group)
    finally:
        release_cleanup.set()
        call_thread.join(timeout=1.0)
        worker.close()
        _kill_pids(pids)


def test_startup_failure_and_ready_timeout_are_typed(remote_worker_factory):
    failed = remote_worker_factory('startup-failure').call(_request())
    timed_out = remote_worker_factory(
        'ready-timeout', startup_timeout_s=0.15,
    ).call(_request())

    assert failed['code'] == 'startup_failure'
    assert timed_out['code'] == 'ready_timeout'


def test_call_timeout_kills_the_child(remote_worker_factory, tmp_path):
    pid_file = tmp_path / 'remote-worker.pid'
    worker = remote_worker_factory(
        'call-timeout', str(pid_file), call_timeout_s=0.15,
    )

    result = worker.call(_request())

    assert result['code'] == 'call_timeout'
    child_pid = int(pid_file.read_text(encoding='utf-8'))
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        try:
            os.kill(child_pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.02)
    else:
        pytest.fail(f'remote child {child_pid} remained after call timeout')


def test_timed_out_worker_is_replaced_for_the_next_request(
    remote_worker_factory, tmp_path,
):
    marker = tmp_path / 'timed-out-once.txt'
    pid_file = tmp_path / 'timed-out-worker.pid'
    pool = EngineWorkerPool(
        worker_factory=lambda _index: remote_worker_factory(
            'timeout-first', str(marker), str(pid_file), call_timeout_s=0.15,
        ),
        size=1,
    )
    first_worker = pool.workers[0]
    try:
        first = pool.submit(_request()).result(timeout=10.0)
        second = pool.submit(_request()).result(timeout=10.0)

        assert first['code'] == 'call_timeout'
        assert second['status'] == 'produced'
        assert pool.workers[0] is not first_worker
        assert pool.workers[0].start_count == 1
        _assert_pids_gone((int(pid_file.read_text(encoding='utf-8')),))
    finally:
        pool.close(cancel_pending=True)


def test_explicit_start_cannot_replace_an_in_call_remote_worker(
    remote_worker_factory, tmp_path,
):
    marker = tmp_path / 'timed-out-once.txt'
    pid_file = tmp_path / 'in-call-worker.pid'
    worker = remote_worker_factory(
        'timeout-first', str(marker), str(pid_file), call_timeout_s=3.0,
    )
    call_results: list[dict[str, object]] = []
    call_thread = threading.Thread(
        target=lambda: call_results.append(worker.call(_request())),
        name='remote restart in-call',
    )
    call_thread.start()
    try:
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and not pid_file.exists():
            time.sleep(0.01)
        assert pid_file.exists()
        previous_process = worker.process
        assert previous_process is not None

        with pytest.raises(RuntimeError, match='cannot start more than once'):
            worker.start()
        call_thread.join(timeout=5.0)

        assert not call_thread.is_alive()
        assert len(call_results) == 1
        assert call_results[0]['status'] == 'refused'
        assert call_results[0]['code'] == 'call_timeout'
        previous_process.join(timeout=1.0)
        assert previous_process.exitcode is not None
        assert not previous_process.is_alive()
        assert worker.start_count == 1
        _assert_pids_gone((int(pid_file.read_text(encoding='utf-8')),))
    finally:
        worker.close()
        call_thread.join(timeout=1.0)


def test_crashed_worker_is_replaced_for_the_next_request(
    remote_worker_factory, tmp_path,
):
    marker = tmp_path / 'crashed-once.txt'
    pool = EngineWorkerPool(
        worker_factory=lambda _index: remote_worker_factory(
            'crash-first', str(marker),
        ),
        size=1,
    )
    first_worker = pool.workers[0]
    try:
        first = pool.submit(_request()).result(timeout=10.0)
        second = pool.submit(_request()).result(timeout=10.0)

        assert first['code'] == 'child_crash'
        assert second['status'] == 'produced'
        assert pool.workers[0] is not first_worker
        assert pool.workers[0].start_count == 1
    finally:
        pool.close(cancel_pending=True)


def test_protocol_version_mismatch_is_typed(remote_worker_factory):
    result = remote_worker_factory('version-mismatch').call(_request())

    assert result['code'] == 'protocol_version_mismatch'


def test_inherited_group_timeout_kills_worker_and_grandchild(
    remote_worker_factory, monkeypatch, tmp_path,
):
    monkeypatch.setenv('REGOLITH_ENGINE_WORKER_INHERIT_PROCESS_GROUP', '1')
    worker_pid_file = tmp_path / 'worker.pid'
    grandchild_pid_file = tmp_path / 'grandchild.pid'
    worker = remote_worker_factory(
        'subtree-timeout', str(worker_pid_file), str(grandchild_pid_file),
        call_timeout_s=0.15,
    )
    pids: tuple[int, ...] = ()
    try:
        result = worker.call(_request())
        pids = _read_pids(worker_pid_file, grandchild_pid_file)

        assert result['code'] == 'call_timeout'
        _assert_pids_gone(pids)
    finally:
        worker.close()
        _kill_pids(pids)


def test_inherited_group_close_kills_worker_and_grandchild(
    remote_worker_factory, monkeypatch, tmp_path,
):
    monkeypatch.setenv('REGOLITH_ENGINE_WORKER_INHERIT_PROCESS_GROUP', '1')
    worker_pid_file = tmp_path / 'worker.pid'
    grandchild_pid_file = tmp_path / 'grandchild.pid'
    worker = remote_worker_factory(
        'subtree-close', str(worker_pid_file), str(grandchild_pid_file),
    )
    pids: tuple[int, ...] = ()
    try:
        result = worker.call(_request())
        pids = _read_pids(worker_pid_file, grandchild_pid_file)
        worker.close()

        assert result['status'] == 'produced'
        _assert_pids_gone(pids)
    finally:
        worker.close()
        _kill_pids(pids)


@pytest.mark.parametrize('inherit', (False, True), ids=('default', 'inherited'))
def test_disable_is_terminal_and_kills_remote_subtree(
    remote_worker_factory, monkeypatch, tmp_path, inherit,
):
    _set_process_group_mode(monkeypatch, inherit)
    caller_group = os.getpgrp()
    worker_pid_file = tmp_path / 'worker.pid'
    grandchild_pid_file = tmp_path / 'grandchild.pid'
    worker = remote_worker_factory(
        'subtree-close', str(worker_pid_file), str(grandchild_pid_file),
    )
    pids: tuple[int, ...] = ()
    try:
        assert worker.call(_request())['status'] == 'produced'
        pids = _read_pids(worker_pid_file, grandchild_pid_file)
        wrapper = worker.process
        assert wrapper is not None
        if inherit:
            assert os.getpgid(wrapper.pid) == caller_group

        worker.disable()
        refusal = worker.call(_request())

        assert refusal['status'] == 'refused'
        assert 'scalar' not in refusal
        assert not wrapper.is_alive()
        _assert_pids_gone(pids)
        _assert_caller_group_unchanged(caller_group)
    finally:
        worker.close()
        _kill_pids(pids)


@pytest.mark.parametrize('inherit', (False, True), ids=('default', 'inherited'))
def test_close_during_in_call_kills_remote_subtree(
    remote_worker_factory, monkeypatch, tmp_path, inherit,
):
    _set_process_group_mode(monkeypatch, inherit)
    caller_group = os.getpgrp()
    worker_pid_file = tmp_path / 'worker.pid'
    grandchild_pid_file = tmp_path / 'grandchild.pid'
    worker = remote_worker_factory(
        'subtree-timeout', str(worker_pid_file), str(grandchild_pid_file),
        call_timeout_s=3.0,
    )
    call_results: list[dict[str, object]] = []
    call_thread: threading.Thread | None = None
    pids: tuple[int, ...] = ()
    try:
        call_thread = threading.Thread(
            target=lambda: call_results.append(worker.call(_request())),
            name='remote in-call',
        )
        call_thread.start()
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and not (
            worker_pid_file.exists() and grandchild_pid_file.exists()
        ):
            time.sleep(0.01)
        assert worker_pid_file.exists() and grandchild_pid_file.exists()
        pids = _read_pids(worker_pid_file, grandchild_pid_file)
        wrapper = worker.process
        assert wrapper is not None

        worker.close()
        call_thread.join(timeout=5.0)

        assert not call_thread.is_alive()
        assert len(call_results) == 1
        assert call_results[0]['status'] == 'refused'
        assert 'scalar' not in call_results[0]
        wrapper.join(timeout=1.0)
        assert wrapper.exitcode is not None
        assert not wrapper.is_alive()
        _assert_pids_gone(pids)
        _assert_caller_group_unchanged(caller_group)
    finally:
        worker.close()
        if call_thread is not None:
            call_thread.join(timeout=1.0)
        _kill_pids(pids)


def test_inherited_group_replacement_kills_old_worker_subtree(
    remote_worker_factory, monkeypatch, tmp_path,
):
    monkeypatch.setenv('REGOLITH_ENGINE_WORKER_INHERIT_PROCESS_GROUP', '1')
    marker = tmp_path / 'crashed-once.txt'
    worker_pid_file = tmp_path / 'worker.pid'
    grandchild_pid_file = tmp_path / 'grandchild.pid'
    pool = EngineWorkerPool(
        worker_factory=lambda _index: remote_worker_factory(
            'crash-subtree-first', str(marker), str(worker_pid_file),
            str(grandchild_pid_file),
        ),
        size=1,
    )
    first_worker = pool.workers[0]
    pids: tuple[int, ...] = ()
    try:
        first = pool.submit(_request()).result(timeout=10.0)
        pids = _read_pids(worker_pid_file, grandchild_pid_file)
        replacement_worker = pool.workers[0]
        second = pool.submit(_request()).result(timeout=10.0)

        assert first['code'] == 'child_crash'
        assert second['status'] == 'produced'
        assert replacement_worker is not first_worker
        assert pool.workers[0].start_count == 1
        _assert_pids_gone(pids)
    finally:
        pool.close(cancel_pending=True)
        _kill_pids(pids)


@pytest.mark.parametrize('inherit', (False, True), ids=('default', 'inherited'))
def test_close_after_wrapper_death_kills_remote_subtree(
    remote_worker_factory, monkeypatch, tmp_path, inherit,
):
    _set_process_group_mode(monkeypatch, inherit)
    caller_group = os.getpgrp()
    worker_pid_file = tmp_path / 'worker.pid'
    grandchild_pid_file = tmp_path / 'grandchild.pid'
    worker = remote_worker_factory(
        'subtree-close', str(worker_pid_file), str(grandchild_pid_file),
    )
    pids: tuple[int, ...] = ()
    try:
        result = worker.call(_request())
        pids = _read_pids(worker_pid_file, grandchild_pid_file)
        wrapper = worker.process
        assert wrapper is not None
        if inherit:
            assert os.getpgid(wrapper.pid) == caller_group

        _kill_wrapper(worker)
        worker.close()

        assert result['status'] == 'produced'
        _assert_pids_gone(pids)
        _assert_caller_group_unchanged(caller_group)
    finally:
        worker.close()
        _kill_pids(pids)


@pytest.mark.parametrize('inherit', (False, True), ids=('default', 'inherited'))
def test_replacement_after_wrapper_death_kills_remote_subtree(
    remote_worker_factory, monkeypatch, tmp_path, inherit,
):
    _set_process_group_mode(monkeypatch, inherit)
    caller_group = os.getpgrp()
    marker = tmp_path / 'remote-subtree-started.txt'
    worker_pid_file = tmp_path / 'worker.pid'
    grandchild_pid_file = tmp_path / 'grandchild.pid'
    pool = EngineWorkerPool(
        worker_factory=lambda _index: remote_worker_factory(
            'subtree-idle-first', str(marker), str(worker_pid_file),
            str(grandchild_pid_file),
        ),
        size=1,
    )
    worker = pool.workers[0]
    pids: tuple[int, ...] = ()
    try:
        first = pool.submit(_request()).result(timeout=10.0)
        pids = _read_pids(worker_pid_file, grandchild_pid_file)
        wrapper = worker.process
        assert wrapper is not None
        if inherit:
            assert os.getpgid(wrapper.pid) == caller_group

        _kill_wrapper(worker)
        refused = pool.submit(_request()).result(timeout=10.0)
        replacement_worker = pool.workers[0]
        replacement = pool.submit(_request()).result(timeout=10.0)

        assert first['status'] == 'produced'
        assert refused['status'] == 'refused'
        assert refused['code'] == 'child_crash'
        assert replacement_worker is not worker
        assert replacement['status'] == 'produced'
        assert replacement_worker.start_count == 1
        _assert_pids_gone(pids)
        _assert_caller_group_unchanged(caller_group)
    finally:
        pool.close(cancel_pending=True)
        _kill_pids(pids)


@pytest.mark.parametrize('inherit', (False, True), ids=('default', 'inherited'))
def test_delayed_old_pair_cleanup_cannot_signal_replacement(
    remote_worker_factory, monkeypatch, tmp_path, inherit,
):
    _set_process_group_mode(monkeypatch, inherit)
    caller_group = os.getpgrp()
    marker = tmp_path / 'remote-subtree-started.txt'
    worker_pid_file = tmp_path / 'old-worker.pid'
    grandchild_pid_file = tmp_path / 'old-grandchild.pid'
    worker = remote_worker_factory(
        'subtree-idle-first', str(marker), str(worker_pid_file),
        str(grandchild_pid_file),
    )
    replacement = remote_worker_factory('produced')
    pids: tuple[int, ...] = ()
    cleanup_entered = threading.Event()
    release_cleanup = threading.Event()
    cleanup_errors: list[BaseException] = []
    cleanup_thread: threading.Thread | None = None
    try:
        assert worker.call(_request())['status'] == 'produced'
        pids = _read_pids(worker_pid_file, grandchild_pid_file)
        old_process = worker.process
        old_connection = worker.connection
        assert old_process is not None
        assert old_connection is not None
        original_stop = worker._stop_pair

        def delay_old_cleanup(
            process, connection, *, diagnostic, cleanup_group=False,
        ):
            if process is old_process and not cleanup_entered.is_set():
                cleanup_entered.set()
                if not release_cleanup.wait(timeout=15.0):
                    raise TimeoutError('test did not release old-pair cleanup')
            return original_stop(
                process,
                connection,
                diagnostic=diagnostic,
                cleanup_group=cleanup_group,
            )

        monkeypatch.setattr(
            worker, '_stop_pair', delay_old_cleanup,
        )

        def retire_old_pair():
            try:
                # This is the detach/stop path used by failed worker calls.
                worker._discard_current(
                    diagnostic=False,
                    expected_process=old_process,
                    expected_connection=old_connection,
                )
            except BaseException as exc:
                cleanup_errors.append(exc)

        cleanup_thread = threading.Thread(
            target=retire_old_pair,
            name='delayed old remote cleanup',
        )
        cleanup_thread.start()
        assert cleanup_entered.wait(timeout=3.0)

        assert replacement.call(_request())['status'] == 'produced'
        replacement_process = replacement.process
        assert replacement_process is not None
        assert replacement_process is not old_process

        release_cleanup.set()
        cleanup_thread.join(timeout=3.0)
        assert not cleanup_thread.is_alive()
        assert cleanup_errors == []
        old_process.join(timeout=1.0)
        assert old_process.exitcode is not None
        assert not old_process.is_alive()

        assert replacement.call(_request())['status'] == 'produced'
        assert replacement.process is replacement_process
        _assert_pids_gone(pids)
        _assert_caller_group_unchanged(caller_group)
    finally:
        release_cleanup.set()
        worker.close()
        replacement.close()
        if cleanup_thread is not None:
            cleanup_thread.join(timeout=1.0)
        _kill_pids(pids)


@pytest.mark.parametrize('inherit', (False, True), ids=('default', 'inherited'))
def test_stale_close_cannot_detach_fresh_remote_instance(
    remote_worker_factory, monkeypatch, tmp_path, inherit,
):
    _set_process_group_mode(monkeypatch, inherit)
    caller_group = os.getpgrp()
    marker = tmp_path / 'old-subtree-started.txt'
    worker_pid_file = tmp_path / 'old-worker.pid'
    grandchild_pid_file = tmp_path / 'old-grandchild.pid'
    old_worker = remote_worker_factory(
        'subtree-idle-first', str(marker), str(worker_pid_file),
        str(grandchild_pid_file),
    )
    replacement = remote_worker_factory('produced')
    disable_entered = threading.Event()
    release_disable = threading.Event()
    close_errors: list[BaseException] = []
    close_thread: threading.Thread | None = None
    pids: tuple[int, ...] = ()
    original_disable = WarmEngineWorker.request_disable

    def pause_old_detach(worker):
        if worker is old_worker:
            disable_entered.set()
            if not release_disable.wait(timeout=15.0):
                raise TimeoutError('test did not release stale close')
        return original_disable(worker)

    monkeypatch.setattr(WarmEngineWorker, 'request_disable', pause_old_detach)
    try:
        assert old_worker.call(_request())['status'] == 'produced'
        pids = _read_pids(worker_pid_file, grandchild_pid_file)

        def close_old_instance():
            try:
                old_worker.close()
            except BaseException as exc:
                close_errors.append(exc)

        close_thread = threading.Thread(
            target=close_old_instance,
            name='stale remote close',
        )
        close_thread.start()
        assert disable_entered.wait(timeout=3.0)

        assert replacement.call(_request())['status'] == 'produced'
        replacement_process = replacement.process
        assert replacement_process is not None
        assert replacement_process.is_alive()
        assert replacement.start_count == 1

        release_disable.set()
        close_thread.join(timeout=3.0)
        assert not close_thread.is_alive()
        assert close_errors == []
        assert replacement.call(_request())['status'] == 'produced'
        assert replacement.process is replacement_process
        assert replacement_process.is_alive()
        _assert_pids_gone(pids)
        _assert_caller_group_unchanged(caller_group)
    finally:
        release_disable.set()
        old_worker.close()
        replacement.close()
        if close_thread is not None:
            close_thread.join(timeout=1.0)
        _kill_pids(pids)


@pytest.mark.parametrize('inherit', (False, True), ids=('default', 'inherited'))
def test_failed_cold_start_cleanup_cannot_touch_replacement_instance(
    remote_worker_factory, monkeypatch, tmp_path, inherit,
):
    _set_process_group_mode(monkeypatch, inherit)
    caller_group = os.getpgrp()
    marker = tmp_path / 'startup-subtree-started.txt'
    worker_pid_file = tmp_path / 'old-worker.pid'
    grandchild_pid_file = tmp_path / 'old-grandchild.pid'
    worker = remote_worker_factory(
        'startup-subtree-first', str(marker), str(worker_pid_file),
        str(grandchild_pid_file),
    )
    replacement = remote_worker_factory('produced')
    startup_pgid_read = threading.Event()
    release_startup_pgid_read = threading.Event()
    call_results: list[dict[str, object]] = []
    call_thread: threading.Thread | None = None
    old_process = None
    old_connection = None
    pids: tuple[int, ...] = ()
    original_remote_pgid = worker._remote_pgid

    def pause_stale_start(
        *, wait_for_report=False, cleanup=False, cleanup_deadline=None,
        background_reaper=False,
    ):
        nonlocal old_process, old_connection
        if (
            not cleanup
            and wait_for_report
            and threading.current_thread().name == 'stale cold call'
            and not startup_pgid_read.is_set()
        ):
            assert worker.ready_payload is not None
            with worker._state_lock:
                old_process, old_connection = worker.process, worker.connection
            assert old_process is not None
            assert old_connection is not None
            startup_pgid_read.set()
            if not release_startup_pgid_read.wait(timeout=15.0):
                raise TimeoutError('test did not release stale startup check')
            return None
        return original_remote_pgid(
            wait_for_report=wait_for_report,
            cleanup=cleanup,
            cleanup_deadline=cleanup_deadline,
            background_reaper=background_reaper,
        )

    monkeypatch.setattr(worker, '_remote_pgid', pause_stale_start)
    try:
        call_thread = threading.Thread(
            target=lambda: call_results.append(worker.call(_request())),
            name='stale cold call',
        )
        call_thread.start()
        assert startup_pgid_read.wait(timeout=5.0)
        assert worker_pid_file.exists() and grandchild_pid_file.exists()
        pids = _read_pids(worker_pid_file, grandchild_pid_file)
        assert replacement.call(_request())['status'] == 'produced'
        replacement_process = replacement.process
        assert replacement_process is not None
        assert replacement_process is not old_process
        assert replacement.start_count == 1

        release_startup_pgid_read.set()
        call_thread.join(timeout=5.0)

        assert not call_thread.is_alive()
        assert len(call_results) == 1
        assert call_results[0]['status'] == 'refused'
        assert call_results[0]['code'] == 'startup_failure'
        assert replacement.process is replacement_process
        assert replacement_process.is_alive()
        assert replacement.call(_request())['status'] == 'produced'
        assert replacement.process is replacement_process
        assert replacement.start_count == 1
        assert old_process is not None
        old_process.join(timeout=1.0)
        assert old_process.exitcode is not None
        assert not old_process.is_alive()
        _assert_pids_gone(pids)
        _assert_caller_group_unchanged(caller_group)
    finally:
        release_startup_pgid_read.set()
        worker.close()
        replacement.close()
        if call_thread is not None:
            call_thread.join(timeout=1.0)
        _kill_pids(pids)


@pytest.mark.parametrize('inherit', (False, True), ids=('default', 'inherited'))
def test_close_during_cold_start_owns_the_remote_pgid_once(
    remote_worker_factory, monkeypatch, tmp_path, inherit,
):
    _set_process_group_mode(monkeypatch, inherit)
    caller_group = os.getpgrp()
    worker_pid_file = tmp_path / 'worker.pid'
    grandchild_pid_file = tmp_path / 'grandchild.pid'
    worker = remote_worker_factory(
        'startup-subtree', str(worker_pid_file), str(grandchild_pid_file),
        startup_timeout_s=3.0,
        call_timeout_s=1.0,
    )
    pgid_read_started = threading.Event()
    release_pgid_read = threading.Event()
    start_results: list[object] = []
    start_thread: threading.Thread | None = None
    pids: tuple[int, ...] = ()
    original_remote_pgid = worker._remote_pgid

    def pause_start_pgid_read(
        *, wait_for_report=False, cleanup=False, cleanup_deadline=None,
        background_reaper=False,
    ):
        if (
            threading.current_thread().name == 'remote cold start'
            and not cleanup
        ):
            pgid_read_started.set()
            if not release_pgid_read.wait(timeout=5.0):
                raise TimeoutError('test did not release startup PGID read')
        return original_remote_pgid(
            wait_for_report=wait_for_report,
            cleanup=cleanup,
            cleanup_deadline=cleanup_deadline,
            background_reaper=background_reaper,
        )

    monkeypatch.setattr(worker, '_remote_pgid', pause_start_pgid_read)
    try:
        start_thread = threading.Thread(
            target=lambda: start_results.append(worker.start()),
            name='remote cold start',
        )
        start_thread.start()
        assert pgid_read_started.wait(timeout=5.0)
        pids = _read_pids(worker_pid_file, grandchild_pid_file)
        wrapper = worker.process
        assert wrapper is not None
        if inherit:
            assert os.getpgid(wrapper.pid) == caller_group

        worker.close()
        release_pgid_read.set()
        start_thread.join(timeout=5.0)

        assert not start_thread.is_alive()
        assert start_results == [None]
        refusal = worker.call(_request())
        assert refusal['status'] == 'refused'
        assert refusal['code'] == 'startup_failure'
        assert not wrapper.is_alive()
        _assert_pids_gone(pids)
        _assert_caller_group_unchanged(caller_group)
    finally:
        release_pgid_read.set()
        worker.close()
        if start_thread is not None:
            start_thread.join(timeout=1.0)
        _kill_pids(pids)


@pytest.mark.parametrize('inherit', (False, True), ids=('default', 'inherited'))
def test_close_before_popen_publication_claims_late_remote_pgid(
    remote_worker_factory, monkeypatch, tmp_path, inherit,
):
    from multiprocessing.context import SpawnProcess

    _set_process_group_mode(monkeypatch, inherit)
    caller_group = os.getpgrp()
    worker_pid_file = tmp_path / 'worker.pid'
    grandchild_pid_file = tmp_path / 'grandchild.pid'
    worker = remote_worker_factory(
        'startup-subtree-hold', str(worker_pid_file), str(grandchild_pid_file),
        startup_timeout_s=3.0,
        call_timeout_s=1.0,
    )
    spawn_created = threading.Event()
    release_publication = threading.Event()
    pgid_visible = threading.Event()
    start_results: list[object] = []
    cleanup_claims: list[int | None] = []
    start_thread: threading.Thread | None = None
    pids: tuple[int, ...] = ()

    class DelayedPgidReceive:
        def __init__(self, connection):
            self.connection = connection

        def poll(self, timeout=0.0):
            if not pgid_visible.wait(timeout=max(0.0, timeout)):
                return False
            return self.connection.poll(0.0)

        def recv(self):
            return self.connection.recv()

    original_popen = SpawnProcess._Popen
    original_remote_pgid = worker._remote_pgid

    def record_remote_pgid(
        *, wait_for_report=False, cleanup=False, cleanup_deadline=None,
        background_reaper=False,
    ):
        group_id = original_remote_pgid(
            wait_for_report=wait_for_report,
            cleanup=cleanup,
            cleanup_deadline=cleanup_deadline,
            background_reaper=background_reaper,
        )
        if cleanup:
            cleanup_claims.append(group_id)
        return group_id

    def gated_popen(process_obj):
        popen = original_popen(process_obj)
        spawn_created.set()
        release_publication.wait(timeout=30.0)
        return popen

    monkeypatch.setattr(SpawnProcess, '_Popen', staticmethod(gated_popen))
    monkeypatch.setattr(worker, '_remote_pgid', record_remote_pgid)
    try:
        start_thread = threading.Thread(
            target=lambda: start_results.append(worker.start()),
            name='remote spawn publication',
        )
        start_thread.start()
        assert spawn_created.wait(timeout=30.0)

        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline and not (
            worker_pid_file.exists() and grandchild_pid_file.exists()
        ):
            time.sleep(0.01)
        assert worker_pid_file.exists() and grandchild_pid_file.exists()
        pids = _read_pids(worker_pid_file, grandchild_pid_file)
        wrapper = worker.process
        assert wrapper is not None
        assert wrapper.pid is None
        if inherit:
            assert os.getpgrp() == caller_group

        with worker._remote_pgid_lock:
            worker._remote_pid_receive = DelayedPgidReceive(
                worker._remote_pid_receive,
            )
        worker.close()

        # The OS wrapper exists, but Process.start has not published its Popen
        # object. Cleanup could not yet observe the delayed PGID report.
        assert wrapper.pid is None
        for pid in pids:
            os.kill(pid, 0)
        pgid_visible.set()
        release_publication.set()
        start_thread.join(timeout=5.0)

        assert not start_thread.is_alive()
        assert start_results == [None]
        assert any(group_id is not None for group_id in cleanup_claims)
        wrapper.join(timeout=1.0)
        assert wrapper.exitcode is not None
        assert not wrapper.is_alive()
        refusal = worker.call(_request())
        assert refusal['status'] == 'refused'
        assert refusal['code'] == 'startup_failure'
        _assert_pids_gone(pids)
        _assert_caller_group_unchanged(caller_group)
    finally:
        pgid_visible.set()
        release_publication.set()
        worker.close()
        if start_thread is not None:
            start_thread.join(timeout=1.0)
        if not pids and worker_pid_file.exists() and grandchild_pid_file.exists():
            pids = _read_pids(worker_pid_file, grandchild_pid_file)
        _kill_pids(pids)

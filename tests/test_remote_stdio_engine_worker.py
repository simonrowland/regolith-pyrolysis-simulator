from __future__ import annotations

import os
import signal
import sys
import time
from pathlib import Path

import pytest

from simulator.engine_pool import EngineWorkerPool, StdioRemoteEngineWorker


FAKE_WORKER = Path(__file__).parent / 'fakes' / 'remote_stdio_worker.py'


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


def test_crashed_worker_is_replaced_for_the_next_request(
    remote_worker_factory, tmp_path,
):
    marker = tmp_path / 'crashed-once.txt'
    worker = remote_worker_factory('crash-first', str(marker))

    first = worker.call(_request())
    second = worker.call(_request())

    assert first['code'] == 'child_crash'
    assert second['status'] == 'produced'
    assert worker.start_count == 2


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


def test_inherited_group_replacement_kills_old_worker_subtree(
    remote_worker_factory, monkeypatch, tmp_path,
):
    monkeypatch.setenv('REGOLITH_ENGINE_WORKER_INHERIT_PROCESS_GROUP', '1')
    marker = tmp_path / 'crashed-once.txt'
    worker_pid_file = tmp_path / 'worker.pid'
    grandchild_pid_file = tmp_path / 'grandchild.pid'
    worker = remote_worker_factory(
        'crash-subtree-first', str(marker), str(worker_pid_file),
        str(grandchild_pid_file),
    )
    pids: tuple[int, ...] = ()
    try:
        first = worker.call(_request())
        pids = _read_pids(worker_pid_file, grandchild_pid_file)
        second = worker.call(_request())

        assert first['code'] == 'child_crash'
        assert second['status'] == 'produced'
        _assert_pids_gone(pids)
    finally:
        worker.close()
        _kill_pids(pids)

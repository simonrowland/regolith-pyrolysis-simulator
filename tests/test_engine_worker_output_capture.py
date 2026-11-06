import fcntl
import inspect
import os
import signal
import threading

import pytest

import simulator.engine_pool as engine_pool_module
from simulator.engine_pool import WarmEngineWorker


def _bootstrap_output_worker():
    return None, 'output-capture-ready'


def _handle_output_request(_resource, request, _errlog):
    os.write(request['fd'], request['line'])
    return request['result']


def _output_worker():
    return WarmEngineWorker(
        name='native output shutdown test',
        bootstrap=_bootstrap_output_worker,
        handler=_handle_output_request,
        startup_timeout_s=30.0,
        call_timeout_s=30.0,
    )


def _prepare_blocked_sink(monkeypatch):
    sink_reader, sink_writer = os.pipe()
    original_flags = fcntl.fcntl(sink_writer, fcntl.F_GETFL)
    fcntl.fcntl(sink_writer, fcntl.F_SETFL, original_flags | os.O_NONBLOCK)
    try:
        while True:
            os.write(sink_writer, b'x' * 4096)
    except BlockingIOError:
        pass
    fcntl.fcntl(sink_writer, fcntl.F_SETFL, original_flags)

    write_started = threading.Event()
    original_write = os.write

    def tracked_write(fd, payload):
        if fd in (2, sink_writer):
            write_started.set()
            return original_write(sink_writer, payload)
        return original_write(fd, payload)

    monkeypatch.setattr(os, 'write', tracked_write)
    original_reader = engine_pool_module._read_native_output
    supports_stderr_fd = (
        'stderr_fd' in inspect.signature(original_reader).parameters
    )

    def reader_target(connection):
        try:
            if supports_stderr_fd:
                return original_reader(connection, stderr_fd=sink_writer)
            return original_reader(connection)
        finally:
            os.close(sink_writer)

    monkeypatch.setattr(
        engine_pool_module,
        '_read_native_output',
        reader_target,
    )
    return sink_reader, write_started


def _drain_and_assert_reader_closed(
    sink_reader,
    reader_thread,
    reader_fd,
    close_thread,
):
    output = bytearray()

    def drain_sink():
        while True:
            chunk = os.read(sink_reader, 65536)
            if not chunk:
                return
            output.extend(chunk)

    drain_thread = threading.Thread(target=drain_sink, daemon=True)
    drain_thread.start()
    drain_thread.join(timeout=5.0)
    reader_thread.join(timeout=5.0)
    close_thread.join(timeout=5.0)
    try:
        assert not drain_thread.is_alive()
        assert not reader_thread.is_alive()
        assert not close_thread.is_alive()
        with pytest.raises(OSError):
            fcntl.fcntl(reader_fd, fcntl.F_GETFD)
        return bytes(output)
    finally:
        os.close(sink_reader)


def _assert_blocked_sink_close_returns(monkeypatch, *, kill_worker):
    sink_reader, write_started = _prepare_blocked_sink(monkeypatch)
    worker = _output_worker()
    close_errors = []
    close_thread = None
    reader_thread = None
    reader_fd = None
    blocked_write_started = False
    try:
        worker.start()
        capture = worker._native_output_capture
        reader_thread = capture[3]
        reader_fd = capture[1].fileno()
        assert worker.call({
            'fd': 1,
            'line': b'FATAL blocked native output\n',
            'result': 'unchanged',
        }) == 'unchanged'
        blocked_write_started = write_started.wait(timeout=5.0)
        assert blocked_write_started
        if kill_worker:
            os.kill(worker.process.pid, signal.SIGKILL)

        def close_worker():
            try:
                worker.close()
            except BaseException as exc:  # reported in the controlling thread
                close_errors.append(exc)

        close_thread = threading.Thread(target=close_worker, daemon=True)
        close_thread.start()
        close_thread.join(timeout=5.0)
        assert not close_thread.is_alive()
        assert not close_errors
    finally:
        if close_thread is None:
            close_thread = threading.Thread(target=worker.close, daemon=True)
            close_thread.start()
        if reader_thread is None:
            capture = worker._native_output_capture
            if capture is not None:
                reader_thread = capture[3]
                reader_fd = capture[1].fileno()
        if reader_thread is not None and reader_fd is not None:
            output = _drain_and_assert_reader_closed(
                sink_reader,
                reader_thread,
                reader_fd,
                close_thread,
            )
            if blocked_write_started:
                assert b'FATAL blocked native output\n' in output
        else:
            os.close(sink_reader)


def test_worker_close_returns_while_native_stderr_pipe_is_blocked(monkeypatch):
    _assert_blocked_sink_close_returns(monkeypatch, kill_worker=False)


def test_sigkill_cleanup_returns_while_native_stderr_pipe_is_blocked(monkeypatch):
    _assert_blocked_sink_close_returns(monkeypatch, kill_worker=True)

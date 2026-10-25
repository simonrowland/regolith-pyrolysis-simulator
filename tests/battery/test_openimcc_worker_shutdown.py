"""Shutdown of the persistent OpenIMCC evaluation worker must stay bounded."""

from __future__ import annotations

import multiprocessing
import signal
import time

import pytest


def _ignore_sigterm_and_block_on_recv(connection) -> None:
    """Stay in recv. SIGTERM must not reap this child; SIGKILL must."""
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    connection.send("armed")
    while True:
        try:
            connection.recv()
        except (EOFError, OSError):
            time.sleep(3600)


@pytest.mark.timeout(20, method="signal")
def test_sigterm_ignoring_recv_worker_is_reaped() -> None:
    """Reap a child that ignores SIGTERM and never leaves recv.

    Against the old closer (``terminate()`` then ``process.join()`` with
    no timeout) this test does not return. pytest-timeout's signal method
    fires the 20s marker, which is under the 300s file budget, and the
    ``finally`` block SIGKILLs the child so the suite itself does not hang.
    """
    import simulator.battery.residue as residue

    residue._close_openimcc_evaluation_worker()
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe(duplex=True)
    process = context.Process(
        target=_ignore_sigterm_and_block_on_recv,
        args=(child,),
        daemon=True,
    )
    try:
        process.start()
        child.close()
        if not parent.poll(10):
            raise AssertionError("SIGTERM-ignoring worker did not arm")
        assert parent.recv() == "armed"
        residue._OPENIMCC_EVALUATION_WORKER = (process, parent, None)
        residue._close_openimcc_evaluation_worker()
        assert not process.is_alive()
        assert process.exitcode == -signal.SIGKILL
    finally:
        try:
            alive = process.is_alive()
        except (AssertionError, ValueError, OSError):
            alive = False
        if alive:
            try:
                process.kill()
            except OSError:
                pass
            process.join(timeout=1.0)
        residue._OPENIMCC_EVALUATION_WORKER = None
        for connection in (child, parent):
            try:
                connection.close()
            except OSError:
                pass

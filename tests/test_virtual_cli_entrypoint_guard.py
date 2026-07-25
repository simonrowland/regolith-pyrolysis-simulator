"""Pin the __main__ guard on the runner package's CLI entry modules.

SC-92 regression pins (2026-07-24 f1fb933; t-421 converted the former
loader-generated virtual modules into these real package files). The
contract stays the same: multiprocessing 'spawn' children re-import the
parent's __main__ module — for `python -m simulator.runner[.sio_*]`
that is one of the modules below — and an unguarded entry statement
would re-execute the ENTIRE CLI recursively inside every worker (the B1
gate-3 drift/slowdown saga; sweep-corpus SC-92). These tests make that
regression loud.

FIX THE ENTRY FILE, DO NOT WEAKEN OR DELETE THESE TESTS.
"""

from __future__ import annotations

import runpy
from pathlib import Path

import pytest

ENTRYPOINTS = {
    "simulator.runner.__main__": "main",
    "simulator.runner.sio_yield": "main_sio_yield",
    "simulator.runner.sio_tsweep": "main_sio_tsweep",
    "simulator.runner.sio_wall_sweep": "main_sio_wall_sweep",
}

_PKG_DIR = (
    Path(__file__).resolve().parent.parent / "simulator" / "runner"
)


@pytest.mark.parametrize("fullname", sorted(ENTRYPOINTS))
def test_entry_file_carries_main_guard(fullname):
    source = (_PKG_DIR / (fullname.rsplit(".", 1)[1] + ".py")).read_text()
    assert 'if __name__ == "__main__":' in source, (
        f"{fullname}: the __main__ guard has been dropped from the entry "
        "file (SC-92 regression; see module docstring)"
    )


@pytest.mark.parametrize("fullname", sorted(ENTRYPOINTS))
def test_spawn_child_reimport_of_entry_module_executes_nothing(fullname):
    """The SC-92 contract on the exact code path that bit.

    A multiprocessing 'spawn' child re-runs the parent's __main__ via
    runpy with ``run_name='__mp_main__'``. Guarded, this returns quietly
    with the entry binding present; unguarded, it launches the whole CLI
    here — SystemExit/argparse failure is the loudest possible
    regression signal.
    """
    namespace = runpy.run_module(fullname, run_name="__mp_main__")
    assert ENTRYPOINTS[fullname] in namespace

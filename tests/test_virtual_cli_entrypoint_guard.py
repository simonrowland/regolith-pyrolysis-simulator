"""Pin the __main__ guard inside the GENERATED sio_* CLI entry code.

SC-92 regression pins (2026-07-24, f1fb933): the virtual modules
``simulator.runner.sio_yield`` / ``sio_tsweep`` / ``sio_wall_sweep``
exist only as source strings manufactured by
``simulator.runner._SiOYieldModuleLoader.get_code``. Because the
dangerous code is generated, the ``if __name__ == "__main__"`` guard is
one string-edit away from vanishing — and without it, every
multiprocessing spawn worker re-imports the parent's __main__ and
re-executes the ENTIRE CLI recursively inside the child (the B1 gate-3
drift/slowdown saga; sweep-corpus SC-92). These tests make that
regression loud.

DO NOT DELETE with the loader still present. If the loader is replaced
by real package files (the planned de-clevering), move the
executes-nothing contract onto those files instead.
"""

from __future__ import annotations

import runpy

import pytest

from simulator.runner import _SiOYieldModuleFinder, _SiOYieldModuleLoader

ENTRYPOINTS = sorted(_SiOYieldModuleFinder._ENTRYPOINTS)


@pytest.mark.parametrize("fullname", ENTRYPOINTS)
def test_generated_entry_source_carries_main_guard(fullname):
    main_name = _SiOYieldModuleFinder._ENTRYPOINTS[fullname]
    code = _SiOYieldModuleLoader(main_name).get_code(fullname)
    # Structural pin on the generated code object: a guarded entry
    # references __name__ (the conditional); an unguarded one does not.
    assert "__name__" in code.co_names, (
        f"{fullname}: generated entry code no longer references __name__ "
        "— the __main__ guard has been dropped from the generated source "
        "(SC-92 regression; see module docstring)"
    )
    assert main_name in code.co_names


@pytest.mark.parametrize("fullname", ENTRYPOINTS)
def test_spawn_child_reimport_of_entry_module_executes_nothing(fullname):
    """The SC-92 contract itself, on the exact code path that bit.

    A multiprocessing 'spawn' child re-runs the parent's __main__ via
    runpy with ``run_name='__mp_main__'`` — which is the ONLY path that
    executes this loader's generated code outside a real CLI launch
    (plain import goes through the no-op ``exec_module``). Guarded, this
    returns quietly; unguarded, it launches the whole CLI here —
    SystemExit/argparse failure is the loudest possible regression
    signal.
    """
    namespace = runpy.run_module(fullname, run_name="__mp_main__")
    # Reaching this line without SystemExit IS the contract; assert the
    # module body really ran (the import binding exists) so the test
    # cannot pass vacuously if runpy semantics change.
    main_name = _SiOYieldModuleFinder._ENTRYPOINTS[fullname]
    assert main_name in namespace

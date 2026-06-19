"""Regression guard: the canonical Ellingham table must not close an import cycle.

The table lives in the dependency-free leaf ``simulator.chemistry.ellingham_thermo``
precisely so that both ``engines.builtin.vapor_pressure`` and
``simulator.equilibrium`` can import it at module level. An earlier dedup put the
canonical copy in ``vapor_pressure`` and imported it from ``equilibrium``, which
closed the cycle ``simulator.core -> equilibrium -> vapor_pressure -> _common ->
capabilities -> simulator -> core``. That crashed on the ``engines``-first import
order but slipped through the full pytest run (which imports ``simulator`` first).

Each case runs in a *fresh* interpreter so sys.modules import order is the only
variable under test.
"""

from __future__ import annotations

import subprocess
import sys


def _import_in_fresh_interpreter(snippet: str) -> None:
    proc = subprocess.run(
        [sys.executable, "-c", snippet],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, (
        f"fresh-interpreter import failed:\nstdout={proc.stdout}\nstderr={proc.stderr}"
    )


def test_engines_builtin_first_import_order():
    # The order that previously raised ImportError (circular import).
    _import_in_fresh_interpreter(
        "from engines.builtin import vapor_pressure as vp; "
        "assert vp._ELLINGHAM_THERMO['Mn'] == (-794.540, -0.165650, 2, 2)"
    )


def test_simulator_first_import_order():
    _import_in_fresh_interpreter(
        "from simulator.equilibrium import EquilibriumMixin; "
        "from simulator.chemistry.ellingham_thermo import ELLINGHAM_THERMO; "
        "assert EquilibriumMixin._ELLINGHAM_THERMO is ELLINGHAM_THERMO"
    )


def test_leaf_first_import_order():
    _import_in_fresh_interpreter(
        "from simulator.chemistry.ellingham_thermo import ELLINGHAM_THERMO, "
        "ELLINGHAM_FIT_RANGE_K; "
        "assert len(ELLINGHAM_THERMO) == 10 and ELLINGHAM_FIT_RANGE_K == (1100.0, 1700.0)"
    )

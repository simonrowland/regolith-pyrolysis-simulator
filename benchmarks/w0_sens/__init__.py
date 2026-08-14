"""W0-SENS blind benchmark-harness sensitivity-screen instrumentation.

Custodian-only WAVE-0 pre-step instruments, frozen by
``docs-private/research/2026-08-09-upstream-mission/melts-backtest/PREREGISTRATION-wave0.md``
steps 2 and 5-7 (lines 37, 40-42):

- ``w_mutator`` — ``W0-W-MUTATOR-v1`` (step 2): the custodian-only
  source-patched-build adapter. For each ``(row, perturbation)`` it makes a
  FRESH build prefix by replacing only the named constant initializer in
  the ThermoEngine ``src/LiquidMelts.m`` parameter array with a frozen
  value (``0``, ``+10,000``, or ``-10,000`` J), rebuilds
  ``libphaseobjc.dylib`` in that prefix, and reads the build back in a
  fresh worker process (``_custodian_worker``), proving by full-vector
  structural diff that no other model datum changed. Runtime setters are
  forbidden and are never called.
- ``driver`` — the W0-SENS computation (steps 5-7): ``delta_c``, the
  0.05-dex affected-channel test, ``C`` / ``S`` / ``I_measured`` /
  ``R_measured``, the rank-1 Na-anchor gate with
  ``ABORT-RANKING-INSTRUMENT-NULL``, and the 10,000-replicate
  ``numpy.random.PCG64`` seed-``649013`` cluster bootstrap.

This package is INSTRUMENTATION, not the screen. Nothing here runs the
screen, reads a quarantined W value, or produces a ranking. These modules
are deliberately absent from
``benchmarks.melt_activity_benchmark.build_engines`` and from every normal
(simulator/web/benchmark) evaluation path; ``tests/test_w0_sens_mutator.py``
guards that usage boundary mechanically. The custodian boundary is
PROCEDURAL CUSTODY (tamper-evident by audit), not technical isolation from
a same-user process — see the ``w_mutator`` module docstring for the
honest claim.
"""

from __future__ import annotations


class W0SensAbort(RuntimeError):
    """Base class for the frozen WAVE-0 typed aborts raised by this package."""

    abort_type = "ABORT-W0-SENS"

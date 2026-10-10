"""Prove MAGEMin lifecycle failures are normalized before result handling."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / 'simulator/melt_backend/magemin.py'
NARROW = '''                except (
                    EngineWorkerExitedError,
                    EngineWorkerPoolLifecycleError,
                    EngineWorkerTimeout,
                    EngineWorkerUnavailable,
                ) as exc:'''
UNAVAILABLE_ONLY = '                except EngineWorkerUnavailable as exc:'
TESTS = (
    'tests/test_magemin_backend.py::test_magemin_worker_exit_returns_typed_not_converged',
    'tests/test_magemin_backend.py::test_magemin_equilibrate_maps_pool_timeout_to_typed_status',
    'tests/test_magemin_backend.py::test_magemin_pool_lifecycle_error_returns_typed_not_converged',
)


def main() -> int:
    source = SOURCE.read_text()
    if source.count(NARROW) != 1:
        raise SystemExit('expected one MAGEMin pool lifecycle catch')
    SOURCE.write_text(source.replace(NARROW, UNAVAILABLE_ONLY, 1))
    env = os.environ.copy()
    env['PYTHONPATH'] = str(ROOT)
    try:
        result = subprocess.run(
            [
                sys.executable,
                '-m',
                'pytest',
                '-q',
                '-n',
                '0',
                '--timeout=300',
                *TESTS,
            ],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        SOURCE.write_text(source)

    output = result.stdout + result.stderr
    if result.returncode == 0 or 'EngineWorkerExitedError' not in output:
        raise SystemExit('lifecycle catch mutation did not break the regressions')
    print('PASS: unnormalized worker death and pool timeout escape status handling')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

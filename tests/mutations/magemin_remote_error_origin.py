"""Prove remote handler errors are not all classified as engine calls."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / 'simulator/melt_backend/magemin.py'
NARROW = '''if exc.exc_name in (
                        _MAGEMinEngineCallError.__name__,
                        _MAGEMinEngineLifecycleError.__name__,
                    ):'''
INVERTED = '''if exc.exc_name not in (
                        _MAGEMinEngineCallError.__name__,
                        _MAGEMinEngineLifecycleError.__name__,
                    ):'''
TESTS = (
    'tests/test_magemin_backend.py::'
    'test_magemin_remote_handler_error_propagates_with_remote_traceback',
    'tests/test_magemin_backend.py::'
    'test_magemin_remote_engine_call_error_returns_typed_not_converged',
    'tests/test_magemin_backend.py::'
    'test_magemin_remote_engine_lifecycle_error_returns_typed_not_converged',
    'tests/test_magemin_backend.py::'
    'test_magemin_remote_builtin_with_multi_argument_constructor_is_preserved',
)


def main() -> int:
    source = SOURCE.read_text()
    if source.count(NARROW) != 1:
        raise SystemExit('expected one remote engine/lifecycle origin check')
    SOURCE.write_text(source.replace(NARROW, INVERTED, 1))
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
    if result.returncode == 0 or 'magemin_remote_handler_error_propagates' not in output:
        raise SystemExit('remote-origin mutation did not break the regressions')
    print('PASS: reversing the remote-origin check misclassifies handler errors')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

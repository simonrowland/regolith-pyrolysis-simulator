"""Prove imposed-fO2 nonconvergence stays a typed ThermoEngine failure.

Restoring the bare RuntimeError at the status producer makes the
production solve raise an untyped error again.
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / 'engines/alphamelts/thermoengine.py'
TARGET = """                raise ThermoEngineNotConvergedError(
                    f'ThermoEngine equilibrium status: {status}'
                )
"""
MUTATION = """                raise RuntimeError(
                    f'ThermoEngine equilibrium status: {status}'
                )
"""
TEST = (
    'tests/test_alphamelts_backend.py::'
    'test_imposed_fo2_failed_status_reaches_typed_not_converged'
)


def main() -> int:
    original = SOURCE.read_text()
    if original.count(TARGET) != 1:
        raise RuntimeError('could not identify the imposed-fO2 status producer')
    try:
        SOURCE.write_text(original.replace(TARGET, MUTATION, 1))
        env = dict(os.environ)
        env['PYTHONPATH'] = str(ROOT) + os.pathsep + env.get('PYTHONPATH', '')
        result = subprocess.run(
            [
                sys.executable,
                '-m',
                'pytest',
                '-vv',
                '-n',
                '0',
                '--timeout=300',
                TEST,
            ],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
    finally:
        SOURCE.write_text(original)
    output = result.stdout + result.stderr
    if result.returncode != 1 or 'FAILED' not in output:
        print('imposed-fO2 status mutation was not rejected')
        print('\n'.join(output.splitlines()[-18:]))
        return 1
    print(f'mutation rejected by {TEST}')
    print('\n'.join(output.splitlines()[-18:]))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

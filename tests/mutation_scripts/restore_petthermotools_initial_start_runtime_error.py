"""Prove the first PetThermoTools start decodes the shared bootstrap tag."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / 'simulator/melt_backend/alphamelts.py'
TARGET = '                    _reraise_petthermotools_startup_failure(exc)'
MUTATION = """                    if exc.exc_name == 'ImportError':
                        raise ImportError(
                            'PetThermoTools worker initialization failed: '
                            f'{exc.detail}\\n{exc.remote_traceback}'
                        ) from exc
                    raise RuntimeError(
                        'PetThermoTools worker initialization failed: '
                        f'{exc.detail}\\n{exc.remote_traceback}'
                    ) from exc"""
TEST = (
    'tests/test_alphamelts_backend.py::'
    'test_petthermotools_initial_start_decodes_bootstrap_origin'
)


def main() -> int:
    original = SOURCE.read_text()
    if original.count(TARGET) != 1:
        raise RuntimeError('could not identify the initial worker start')
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
        print('initial-start mutation was not rejected')
        print('\n'.join(output.splitlines()[-18:]))
        return 1
    print(f'mutation rejected by {TEST}')
    print('\n'.join(output.splitlines()[-18:]))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

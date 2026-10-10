"""Prove invalid EC max_points remains a typed refusal."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / 'simulator/melt_backend/liquidus.py'
TARGET = '''            raise _LiquidusFinderConfigurationError(
                'max_points must be numeric'
            )'''
MUTATION = "            raise TypeError('max_points must be numeric')"
TEST = (
    'tests/test_scalar_boundary_bool_poison.py::'
    'test_liquidus_max_points_rejects_boolean_values'
)


def main() -> int:
    original = SOURCE.read_text()
    if original.count(TARGET) != 1:
        raise RuntimeError('could not identify EC max_points validation')
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
    if result.returncode == 0 or 'TypeError: max_points must be numeric' not in output:
        print('max_points mutation was not rejected for the expected reason')
        print('\n'.join(output.splitlines()[-18:]))
        return 1
    print(f'mutation rejected by {TEST}')
    print('\n'.join(output.splitlines()[-18:]))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

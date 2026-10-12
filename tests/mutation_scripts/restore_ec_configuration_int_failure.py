"""Prove invalid EC configuration is refused before int() and math.ceil."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / 'simulator/melt_backend/liquidus.py'
TARGET = '''    try:
        int(max_points)
    except (TypeError, ValueError, OverflowError) as exc:
        raise _LiquidusFinderConfigurationError(
            'max_points must be numeric'
        ) from exc
    if not math.isfinite(solidus_T) or not math.isfinite(liquidus_T):
        raise _LiquidusFinderConfigurationError(
            'invalid EC temperature bound: must be finite'
        )
    if not math.isfinite(step):
        raise _LiquidusFinderConfigurationError(
            'invalid EC grid_step_C: must be finite'
        )
'''
MUTATION = ''
TEST = (
    'tests/test_liquidus_finder.py::'
    'test_ec_path_refuses_invalid_configuration'
)


def main() -> int:
    original = SOURCE.read_text()
    if original.count(TARGET) != 1:
        raise RuntimeError('could not identify the EC configuration validation')
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
        print('EC configuration mutation was not rejected')
        print('\n'.join(output.splitlines()[-18:]))
        return 1
    print(f'mutation rejected by {TEST}')
    print('\n'.join(output.splitlines()[-18:]))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

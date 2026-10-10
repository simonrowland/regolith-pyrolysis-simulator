"""Prove declared MAGEMin scalar refusals are raised at admission."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / 'simulator/melt_backend/magemin.py'
TYPED = '''        if not is_declared_real_scalar(value, allow_numeric_str=True):
            raise _MAGEMinDeclaredInputError(
                'TypeError',
                'declared scalar must be numeric',
            )'''
UNTYPED = '''        if not is_declared_real_scalar(value, allow_numeric_str=True):
            raise _MAGEMinEngineCallError(
                'TypeError',
                'declared scalar must be numeric',
            )'''
TEST = (
    'tests/test_magemin_backend.py::'
    'test_magemin_equilibrate_invalid_declared_scalar_is_typed_refusal'
)


def main() -> int:
    source = SOURCE.read_text()
    if source.count(TYPED) != 1:
        raise SystemExit('expected one declared-scalar admission refusal')
    SOURCE.write_text(source.replace(TYPED, UNTYPED, 1))
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
                TEST,
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
    failed = [
        line for line in output.splitlines()
        if line.startswith('FAILED ') and (
            'test_magemin_equilibrate_invalid_declared_scalar_is_typed_refusal'
            in line
        )
    ]
    required = (
        'none-fo2',
        'bad-pressure',
        'bad-temperature',
        'bad-timeout-str',
        'bad-timeout-type',
    )
    missing = [
        name for name in required
        if not any(name in line for line in failed)
    ]
    if result.returncode != 1 or missing:
        raise SystemExit(
            'dropping the typed scalar refusal did not fail the regression\n'
            f'missing={missing}\n'
            + output[-2000:]
        )
    print('\n'.join(failed))
    print('PASS: untyped scalar admission fails the typed-refusal regression')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
